from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping, Protocol, Sequence, cast

from cloudways_monitor.cloudways import CloudwaysApiError
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import MetricSnapshot, ResourceType, Storage


CollectorStatus = Literal["never_run", "ok", "degraded"]


class Clock(Protocol):
    def now(self) -> datetime: ...


class TelemetrySource(Protocol):
    def list_servers(self) -> Sequence[Mapping[str, Any]]: ...

    def list_applications(self) -> Sequence[Mapping[str, Any]]: ...

    def get_server_metrics(self, server_id: str) -> Mapping[str, Any]: ...

    def get_application_metrics(
        self,
        application_id: str,
        server_id: str | None,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CollectorHealth:
    status: CollectorStatus
    last_run_at: datetime | None
    last_success_at: datetime | None
    servers_discovered: int
    applications_discovered: int
    snapshots_stored: int
    snapshots_expired: int
    stale: bool
    last_error_code: str | None
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "last_run_at": _iso(self.last_run_at),
            "last_success_at": _iso(self.last_success_at),
            "servers_discovered": self.servers_discovered,
            "applications_discovered": self.applications_discovered,
            "snapshots_stored": self.snapshots_stored,
            "snapshots_expired": self.snapshots_expired,
            "stale": self.stale,
            "last_error_code": self.last_error_code,
            "last_error": self.last_error,
        }


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class TelemetryCollector:
    def __init__(
        self,
        *,
        settings: Settings,
        storage: Storage,
        telemetry_source: TelemetrySource,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._telemetry_source = telemetry_source
        self._clock = clock or SystemClock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._health = CollectorHealth(
            status="never_run",
            last_run_at=None,
            last_success_at=None,
            servers_discovered=0,
            applications_discovered=0,
            snapshots_stored=0,
            snapshots_expired=0,
            stale=True,
            last_error_code=None,
            last_error=None,
        )

    @property
    def health(self) -> CollectorHealth:
        return self._current_health(self._clock.now())

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, run_immediately: bool = True) -> None:
        if self.is_running:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(run_immediately,),
            name="cloudways-telemetry-collector",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        thread = self._thread
        if thread is None:
            return

        self._stop_event.set()
        if thread is not threading.current_thread():
            thread.join(timeout=timeout_seconds)
        if not thread.is_alive():
            self._thread = None

    def run_once(self) -> CollectorHealth:
        captured_at = self._clock.now()
        try:
            servers = self._allowed_servers(self._telemetry_source.list_servers())
            applications = self._allowed_applications(
                self._telemetry_source.list_applications()
            )
            snapshots_stored = 0

            for server in servers:
                resource_id = self._upsert_resource(server, captured_at)
                metrics = self._telemetry_source.get_server_metrics(
                    _provider_id(server)
                )
                self._storage.insert_metric_snapshot(
                    _metric_snapshot(
                        resource_id=resource_id,
                        resource_type="server",
                        metrics=metrics,
                        captured_at=captured_at,
                    )
                )
                snapshots_stored += 1

            for application in applications:
                resource_id = self._upsert_resource(application, captured_at)
                metrics = self._telemetry_source.get_application_metrics(
                    _provider_id(application),
                    _parent_provider_id(application),
                )
                self._storage.insert_metric_snapshot(
                    _metric_snapshot(
                        resource_id=resource_id,
                        resource_type="application",
                        metrics=metrics,
                        captured_at=captured_at,
                    )
                )
                snapshots_stored += 1

            snapshots_expired = self._storage.expire_metric_snapshots(
                older_than=captured_at - timedelta(days=self._settings.retention_days)
            )
            self._health = CollectorHealth(
                status="ok",
                last_run_at=captured_at,
                last_success_at=captured_at,
                servers_discovered=len(servers),
                applications_discovered=len(applications),
                snapshots_stored=snapshots_stored,
                snapshots_expired=snapshots_expired,
                stale=False,
                last_error_code=None,
                last_error=None,
            )
            return self._health
        except CloudwaysApiError as exc:
            self._health = CollectorHealth(
                status="degraded",
                last_run_at=captured_at,
                last_success_at=self._health.last_success_at,
                servers_discovered=self._health.servers_discovered,
                applications_discovered=self._health.applications_discovered,
                snapshots_stored=0,
                snapshots_expired=0,
                stale=self._is_stale(captured_at),
                last_error_code=exc.code,
                last_error=str(exc),
            )
            return self._health

    def _current_health(self, now: datetime) -> CollectorHealth:
        if self._health.status == "never_run":
            return self._health

        stale = self._is_stale(now)
        status = self._health.status
        if stale and status == "ok":
            status = "degraded"
        if stale == self._health.stale and status == self._health.status:
            return self._health
        return replace(self._health, status=status, stale=stale)

    def _run_loop(self, run_immediately: bool) -> None:
        if run_immediately and not self._stop_event.is_set():
            self.run_once()

        while not self._stop_event.wait(self._settings.poll_interval_seconds):
            self.run_once()

    def _allowed_servers(
        self,
        servers: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        allowed_ids = set(self._settings.monitored_server_ids)
        if not allowed_ids:
            return list(servers)
        return [server for server in servers if _provider_id(server) in allowed_ids]

    def _allowed_applications(
        self,
        applications: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        allowed_app_ids = set(self._settings.monitored_app_ids)
        if allowed_app_ids:
            return [
                application
                for application in applications
                if _provider_id(application) in allowed_app_ids
            ]

        allowed_server_ids = set(self._settings.monitored_server_ids)
        if not allowed_server_ids:
            return list(applications)
        return [
            application
            for application in applications
            if _parent_provider_id(application) in allowed_server_ids
        ]

    def _upsert_resource(
        self,
        resource: Mapping[str, Any],
        discovered_at: datetime,
    ) -> int:
        return self._storage.upsert_resource(
            provider_id=_provider_id(resource),
            resource_type=_resource_type(resource),
            name=_resource_name(resource),
            parent_provider_id=_parent_provider_id(resource),
            raw=_raw(resource),
            discovered_at=discovered_at,
        )

    def _is_stale(self, now: datetime) -> bool:
        if self._health.last_success_at is None:
            return True
        return (
            now - self._health.last_success_at
            > timedelta(seconds=self._settings.stale_after_seconds)
        )


def _metric_snapshot(
    *,
    resource_id: int,
    resource_type: ResourceType,
    metrics: Mapping[str, Any],
    captured_at: datetime,
) -> MetricSnapshot:
    ram_used_mb = _float_metric(metrics, "ram_used_mb", "memory_used_mb")
    ram_total_mb = _float_metric(metrics, "ram_total_mb", "memory_total_mb")
    disk_used_gb = _float_metric(metrics, "disk_used_gb", "storage_used_gb")
    disk_total_gb = _float_metric(metrics, "disk_total_gb", "storage_total_gb")
    ram_percent = _float_metric(metrics, "ram_percent", "memory_percent")
    if ram_percent is None:
        ram_percent = _percentage(ram_used_mb, ram_total_mb)
    disk_percent = _float_metric(metrics, "disk_percent", "storage_percent")
    if disk_percent is None:
        disk_percent = _percentage(disk_used_gb, disk_total_gb)
    return MetricSnapshot(
        resource_id=resource_id,
        resource_type=resource_type,
        captured_at=captured_at,
        cpu_percent=_float_metric(metrics, "cpu_percent", "cpu"),
        ram_used_mb=ram_used_mb,
        ram_total_mb=ram_total_mb,
        ram_percent=ram_percent,
        disk_used_gb=disk_used_gb,
        disk_total_gb=disk_total_gb,
        disk_percent=disk_percent,
        bandwidth_bytes=_int_metric(metrics, "bandwidth_bytes", "bandwidth"),
        traffic_requests=_int_metric(metrics, "traffic_requests", "requests"),
        php_metric=_dict_metric(metrics, "php_metric"),
        mysql_metric=_dict_metric(metrics, "mysql_metric"),
        raw_payload=dict(metrics),
        collection_status="ok",
        error_code=None,
    )


def _provider_id(resource: Mapping[str, Any]) -> str:
    value = resource.get("provider_id")
    if value is None or value == "":
        raise ValueError("discovered resource is missing provider_id")
    return str(value)


def _resource_type(resource: Mapping[str, Any]) -> ResourceType:
    value = resource.get("resource_type")
    if value not in ("server", "application"):
        raise ValueError("discovered resource has an invalid resource_type")
    return cast(ResourceType, value)


def _resource_name(resource: Mapping[str, Any]) -> str:
    value = resource.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _provider_id(resource)


def _parent_provider_id(resource: Mapping[str, Any]) -> str | None:
    value = resource.get("parent_provider_id")
    if value is None or value == "":
        return None
    return str(value)


def _raw(resource: Mapping[str, Any]) -> dict[str, Any]:
    value = resource.get("raw")
    if isinstance(value, dict):
        return value
    return {}


def _float_metric(metrics: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = metrics.get(key)
        if value is not None:
            return _float(value)
    return None


def _int_metric(metrics: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = metrics.get(key)
        if value is not None:
            parsed = _float(value)
            if parsed is not None:
                return int(parsed)
    return None


def _dict_metric(metrics: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = metrics.get(key)
    if isinstance(value, dict):
        return value
    return {}


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().removesuffix("%")
        if stripped:
            try:
                return float(stripped)
            except ValueError:
                return None
    return None


def _percentage(used: float | None, total: float | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return round((used / total) * 100, 2)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
