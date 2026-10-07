from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cloudways_monitor.cloudways import CloudwaysApiError
from cloudways_monitor.alerts import TelegramNotificationError
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import MetricSnapshot, Storage
from cloudways_monitor.monitor import latest_complete_point


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class CollectorHealth:
    status: str = "never_run"
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    servers_discovered: int = 0
    applications_discovered: int = 0
    snapshots_stored: int = 0
    snapshots_expired: int = 0
    stale: bool = True
    last_error_code: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in vars(self).items()
        }


class TelemetryCollector:
    def __init__(
        self,
        *,
        settings: Settings,
        storage: Storage,
        telemetry_source: Any,
        clock: Clock | None = None,
        alert_evaluator: Any = None,
    ):
        self._settings, self._storage, self._source = (
            settings,
            storage,
            telemetry_source,
        )
        self._clock, self._alerts = clock or SystemClock(), alert_evaluator
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        self._health = CollectorHealth()
        self._offset = 0

    @property
    def health(self) -> CollectorHealth:
        stale = (
            self._health.last_success_at is None
            or (self._clock.now() - self._health.last_success_at).total_seconds()
            > self._settings.stale_after_seconds
        )
        return replace(
            self._health,
            stale=stale,
            status="degraded"
            if stale and self._health.status == "ok"
            else self._health.status,
        )

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, run_immediately: bool = True):
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(run_immediately,),
            name="cloudways-collector",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 25):
        self._stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout_seconds)

    def _run_loop(self, immediately: bool):
        if immediately:
            self.run_once()
        while not self._stop_event.wait(self._settings.poll_interval_seconds):
            self.run_once()

    def run_once(self) -> CollectorHealth:
        with self._run_lock:
            started = self._clock.now()
            try:
                servers = self._source.list_servers()
                apps = self._source.list_applications()
                if self._settings.monitored_server_ids:
                    servers = [
                        s
                        for s in servers
                        if s["provider_id"] in self._settings.monitored_server_ids
                    ]
                server_ids = {s["provider_id"] for s in servers}
                apps = [
                    a
                    for a in apps
                    if a["parent_provider_id"] in server_ids
                    and (
                        not self._settings.monitored_app_ids
                        or a["provider_id"] in self._settings.monitored_app_ids
                    )
                ]
            except Exception as exc:
                self._health = replace(
                    self._health,
                    status="degraded",
                    last_run_at=started,
                    snapshots_stored=0,
                    last_error_code=getattr(exc, "code", "discovery_error"),
                    last_error=_safe_error(exc),
                )
                if self._alerts:
                    self._alerts.interrupt_pending_samples()
                    self._alerts.evaluate_health(
                        _safe_error(exc),
                        started,
                        authentication_failed=getattr(exc, "code", "")
                        == "authentication_failed",
                    )
                    try:
                        self._alerts.flush_notifications()
                    except Exception:
                        self._health = replace(
                            self._health,
                            last_error="Collection failed; Telegram delivery queued for retry",
                        )
                return self.health
            self._storage.prune_resources(
                {(r["provider_id"], r["resource_type"]) for r in servers + apps}
            )
            errors = []
            stored = 0
            resources = servers + apps
            resource_ids = {
                (r["resource_type"], r["provider_id"]): self._storage.upsert_resource(
                    **r, discovered_at=started
                )
                for r in resources
            }
            if resources:
                self._offset %= len(resources)
                resources = resources[self._offset :] + resources[: self._offset]
                self._offset += 1
            for resource in resources:
                if self._stop_event.is_set():
                    break
                now = self._clock.now()
                rid = resource_ids[(resource["resource_type"], resource["provider_id"])]
                try:
                    if resource["resource_type"] == "server":
                        metrics = self._source.get_server_metrics(
                            resource["provider_id"]
                        )
                    else:
                        metrics = self._source.get_application_metrics(
                            resource["provider_id"], resource["parent_provider_id"]
                        )
                except Exception as exc:
                    metrics = dict(
                        diagnostics=[
                            dict(
                                metric="Collection",
                                status="error",
                                message=_safe_error(exc),
                            )
                        ]
                    )
                diagnostics = metrics.get("diagnostics", [])
                if diagnostics:
                    errors.extend(diagnostics)
                snapshot = MetricSnapshot(
                    resource_id=rid,
                    resource_type=resource["resource_type"],
                    captured_at=now,
                    cpu_percent=None,
                    ram_used_mb=None,
                    ram_total_mb=None,
                    ram_percent=None,
                    disk_used_gb=metrics.get("disk_used_gb"),
                    disk_total_gb=None,
                    disk_percent=None,
                    bandwidth_bytes=metrics.get("bandwidth_bytes"),
                    traffic_requests=metrics.get("traffic_requests"),
                    php_metric={},
                    mysql_metric={},
                    raw_payload=_snapshot_payload(metrics),
                    collection_status="degraded" if diagnostics else "ok",
                    error_code="partial_collection" if diagnostics else None,
                )
                self._storage.insert_metric_snapshot(snapshot)
                stored += 1
                for graph in metrics.get("graphs", {}).values():
                    self._storage.save_graph(rid, graph)
                if self._alerts:
                    try:
                        self._alerts.evaluate_snapshot(snapshot)
                    except Exception as exc:
                        errors.append(
                            dict(
                                metric="Telegram",
                                status="error",
                                message=_safe_error(exc),
                            )
                        )
            if self._alerts:
                try:
                    self._alerts.evaluate_health(
                        f"{len(errors)} metric collections need attention"
                        if errors
                        else None,
                        self._clock.now(),
                    )
                    self._alerts.flush_notifications()
                except Exception as exc:
                    errors.append(
                        dict(
                            metric="Telegram", status="error", message=_safe_error(exc)
                        )
                    )
            expired = self._storage.expire_metric_snapshots(
                older_than=self._clock.now()
                - timedelta(days=self._settings.retention_days)
            )
            self._health = CollectorHealth(
                status="degraded" if errors else "ok",
                last_run_at=started,
                last_success_at=self._clock.now()
                if stored
                else self._health.last_success_at,
                servers_discovered=len(servers),
                applications_discovered=len(apps),
                snapshots_stored=stored,
                snapshots_expired=expired,
                stale=not bool(stored),
                last_error_code="partial_collection" if errors else None,
                last_error="; ".join(
                    dict.fromkeys(f"{e['metric']}: {e['message']}" for e in errors[:3])
                )
                if errors
                else None,
            )
            return self.health


def _safe_error(exc: Exception) -> str:
    return (
        str(exc)
        if isinstance(exc, (CloudwaysApiError, ValueError, TelegramNotificationError))
        else f"Collection failed: {exc.__class__.__name__}"
    )


def _snapshot_payload(metrics: dict) -> dict:
    """Store each fetch's latest source samples; full windows live in monitor_graphs."""
    payload = {k: v for k, v in metrics.items() if k != "graphs"}
    if "graphs" in metrics:
        payload["graphs"] = {
            target: {
                **{k: v for k, v in graph.items() if k not in ("raw", "series")},
                "series": [
                    {
                        **series,
                        "points": [latest_complete_point(series["points"])]
                        if latest_complete_point(series["points"])
                        else series["points"][-1:],
                    }
                    for series in graph.get("series", [])
                ],
            }
            for target, graph in metrics["graphs"].items()
        }
    return payload
