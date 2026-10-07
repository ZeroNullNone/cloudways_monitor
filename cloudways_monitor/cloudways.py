from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
import threading
import time
from typing import Any

import httpx

from cloudways_monitor.monitor import (
    parse_graph,
    parse_application_disk,
    parse_traffic,
    latest_complete_point,
    sample_is_stale,
    parse_capacity,
    parse_analysis,
    unwrap,
)
from cloudways_monitor.settings import Settings
from cloudways_monitor.targets import TARGET_DEFINITIONS


class CloudwaysApiError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None, code: str, detail: object = None
    ):
        super().__init__(message)
        self.status_code, self.code, self.detail = status_code, code, detail


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (
                "[redacted]"
                if any(
                    s in k.lower()
                    for s in ("password", "secret", "token", "api_key", "private_key")
                )
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class CloudwaysClient:
    """Read-only Flexible API. One request budget shared by collection and graphs."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
        clock=time.monotonic,
    ):
        self._settings = settings
        self._http_client = http_client or httpx.Client(timeout=20.0)
        self._lock = threading.RLock()
        self._catalog_lock = threading.Lock()
        self._operation_locks: dict[tuple, threading.RLock] = {}
        self._clock = clock
        self._requests: deque[float] = deque()
        self._blocked_until = 0.0
        self._blocked_code = "rate_limited"
        self._endpoint_pauses: dict[str, float] = {}
        self._catalog: tuple[float, dict] | None = None
        self._pending: dict[tuple, tuple[str, float]] = {}
        self._graphs: dict[tuple, tuple[float, dict]] = {}
        self._application_users: dict[tuple[str, str], str] = {}
        self._disk_summaries: dict[str, tuple[float, Any]] = {}
        self._traffic_results: dict[tuple[str, str], tuple[float, dict]] = {}
        self._capacities: dict[str, tuple[float, dict]] = {}
        self._analyses: dict[tuple, tuple[float, dict]] = {}

    def close(self):
        self._http_client.close()

    def invalidate_graph_cache(self):
        with self._lock:
            self._graphs.clear()

    def _key_lock(self, key):
        with self._lock:
            return self._operation_locks.setdefault(key, threading.RLock())

    def _get(self, path: str, params: dict | None = None) -> Any:
        endpoint = "/operation/{id}" if path.startswith("/operation/") else path
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                raise CloudwaysApiError(
                    "Cloudways requests paused: "
                    + self._blocked_code.replace("_", " "),
                    status_code=401
                    if self._blocked_code == "authentication_failed"
                    else 429,
                    code=self._blocked_code,
                )
            if now < self._endpoint_pauses.get(endpoint, 0):
                raise CloudwaysApiError(
                    "Cloudways requests paused for this endpoint: rate limited",
                    status_code=429,
                    code="rate_limited",
                )
            while self._requests and now - self._requests[0] >= 60:
                self._requests.popleft()
            if len(self._requests) >= 80:
                raise CloudwaysApiError(
                    "Shared request budget exhausted; retry next minute",
                    status_code=429,
                    code="request_budget",
                )
            self._requests.append(now)
        try:
            response = self._http_client.get(
                self._settings.cloudways_api_base_url.rstrip("/") + path,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._settings.cloudways_access_token}"
                },
            )
            if response.status_code == 429:
                try:
                    delay = max(1, float(response.headers.get("Retry-After", "60")))
                except ValueError:
                    delay = 60
                with self._lock:
                    self._endpoint_pauses[endpoint] = self._clock() + delay
            elif response.status_code == 401:
                with self._lock:
                    self._blocked_until = self._clock() + 300
                    self._blocked_code = "authentication_failed"
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            code = {
                401: "authentication_failed",
                403: "permission_denied",
                429: "rate_limited",
            }.get(exc.response.status_code, "http_error")
            raise CloudwaysApiError(
                f"Cloudways HTTP {exc.response.status_code}: {code.replace('_', ' ')}",
                status_code=exc.response.status_code,
                code=code,
            ) from exc
        except httpx.RequestError as exc:
            raise CloudwaysApiError(
                "Cloudways network request failed",
                status_code=None,
                code="network_error",
            ) from exc
        except ValueError as exc:
            raise CloudwaysApiError(
                "Cloudways returned invalid JSON",
                status_code=response.status_code,
                code="invalid_json",
            ) from exc
        if isinstance(result, dict) and result.get("status") is False:
            raise CloudwaysApiError(
                "Cloudways reported an unsuccessful request",
                status_code=200,
                code="upstream_error",
            )
        return result

    def _pages(self, path: str, key: str) -> list[dict]:
        items = []
        page = 1
        while True:
            payload = self._get(path, {"page": page, "limit": 100})
            try:
                data = payload["data"]
                batch = data[key]
                last = int(data["pagination"]["last_page"])
                current = int(data["pagination"]["current_page"])
                if not isinstance(batch, list) or current != page or last < page:
                    raise ValueError("Invalid pagination")
                if not all(
                    isinstance(item, dict) and item.get("id") is not None
                    for item in batch
                ):
                    raise ValueError("Invalid resource identity")
            except (KeyError, TypeError, ValueError) as exc:
                raise CloudwaysApiError(
                    "Unrecognized resource pagination schema",
                    status_code=200,
                    code="schema_error",
                ) from exc
            items.extend(batch)
            if page >= last:
                return items
            if page >= 1000:
                raise CloudwaysApiError(
                    "Resource pagination exceeds safety limit",
                    status_code=200,
                    code="pagination_limit",
                )
            page += 1

    def list_servers(self):
        result = []
        for server in self._pages("/servers", "servers"):
            if server.get("cloud") != "do":
                raise CloudwaysApiError(
                    "This dashboard is configured for DigitalOcean servers (cloud=do)",
                    status_code=200,
                    code="provider_mismatch",
                )
            raw = {k: server.get(k) for k in ("cloud", "region", "status", "public_ip")}
            result.append(
                dict(
                    provider_id=str(server["id"]),
                    resource_type="server",
                    name=server.get("label") or str(server["id"]),
                    parent_provider_id=None,
                    raw=raw,
                )
            )
        return result

    def list_applications(self):
        result = []
        users = {}
        for app in self._pages("/apps/flexible", "apps"):
            if app.get("server_id") is None:
                raise CloudwaysApiError(
                    "Application is missing server_id",
                    status_code=200,
                    code="schema_error",
                )
            raw = {
                k: app.get(k)
                for k in ("cname", "app_fqdn", "application", "is_staging")
            }
            users[(str(app["server_id"]), str(app["id"]))] = str(
                app.get("sys_user") or ""
            )
            result.append(
                dict(
                    provider_id=str(app["id"]),
                    resource_type="application",
                    name=app.get("label") or str(app["id"]),
                    parent_provider_id=str(app["server_id"]),
                    raw=raw,
                )
            )
        self._application_users = users
        return result

    def monitor_catalog(self) -> dict:
        if self._catalog and self._clock() - self._catalog[0] < 600:
            return self._catalog[1]
        with self._catalog_lock:
            now = self._clock()
            if self._catalog and now - self._catalog[0] < 600:
                return self._catalog[1]
            targets = self._get("/monitor_targets")
            durations = self._get("/monitor_durations")
            try:
                target_list, duration_list = (
                    targets["targets"]["do"],
                    durations["durations"],
                )
                if not all(
                    isinstance(a, list)
                    and a
                    and all(isinstance(x, str) and x for x in a)
                    for a in (target_list, duration_list)
                ):
                    raise ValueError("Empty/invalid monitoring catalog")
            except (KeyError, TypeError, ValueError) as exc:
                raise CloudwaysApiError(
                    "Invalid DigitalOcean monitoring catalog",
                    status_code=200,
                    code="catalog_error",
                ) from exc
            result = dict(
                targets=list(dict.fromkeys(target_list)),
                durations=list(dict.fromkeys(duration_list)),
                provider="do",
                timezone="UTC+8",
                metric_definitions={
                    target: TARGET_DEFINITIONS[target]
                    for target in target_list
                    if target in TARGET_DEFINITIONS
                },
                alert_rules=[
                    r.as_dict()
                    for r in self._settings.alert_rules
                    if r.target in target_list
                ],
            )
            self._catalog = now, result
            return result

    def _task(self, key: tuple, path: str, params: dict) -> tuple[str, Any]:
        with self._key_lock(key):
            now = self._clock()
            pending = self._pending.get(key)
            if pending and now - pending[1] > 300:
                del self._pending[key]
                return "error", "Monitoring task timed out after 5 minutes"
            if pending is None:
                payload = self._get(path, params)
                task_id = (
                    payload.get("task_id", payload.get("operation_id"))
                    if isinstance(payload, dict)
                    else None
                )
                if task_id is None:
                    return "ok", payload
                pending = str(task_id), now
                self._pending[key] = pending
            payload = self._get("/operation/" + pending[0])
            operation = (
                payload.get("operation", payload) if isinstance(payload, dict) else {}
            )
            completed = operation.get("is_completed")
            if str(completed) == "-1":
                self._pending.pop(key, None)
                return "error", "Cloudways monitoring task failed"
            if str(completed) not in ("1", "True"):
                return "pending", None
            self._pending.pop(key, None)
            return "ok", payload

    def get_graph(self, server_id: str, target: str, duration: str) -> dict:
        with self._key_lock(("graph", server_id, target, duration)):
            catalog = self.monitor_catalog()
            if target not in catalog["targets"] or duration not in catalog["durations"]:
                raise ValueError("Select a target and duration returned by Cloudways")
            key = ("graph", server_id, target, duration)
            cached = self._graphs.get(key)
            if cached and self._clock() - cached[0] < (
                3
                if cached[1]["status"] == "pending"
                else self._settings.poll_interval_seconds
            ):
                return cached[1]
            result = dict(target=target, duration=duration, timezone="UTC+8", series=[])
            try:
                status, payload = self._task(
                    key,
                    "/server/monitor/detail",
                    dict(
                        server_id=server_id,
                        target=target,
                        duration=duration,
                        strorge="false",
                        timezone="Asia/Singapore",
                        output_format="json",
                    ),
                )
                if status == "ok":
                    result["raw"] = redact(payload)
                    result.update(parse_graph(payload, target, duration))
                    for series in result["series"]:
                        series["freshness_seconds"] = max(
                            self._settings.stale_after_seconds,
                            (series.get("cadence_seconds") or 0) * 2,
                        )
                else:
                    result.update(status=status, error=payload)
            except (
                CloudwaysApiError,
                ValueError,
                TypeError,
                KeyError,
                OverflowError,
                json.JSONDecodeError,
            ) as exc:
                result.update(
                    status="error",
                    error=str(exc),
                    error_code=getattr(exc, "code", "schema_error"),
                )
            result["fetched_at"] = datetime.now(UTC).isoformat()
            with self._lock:
                if len(self._graphs) >= 128 and key not in self._graphs:
                    oldest = min(self._graphs, key=lambda k: self._graphs[k][0])
                    self._graphs.pop(oldest)
                self._graphs[key] = self._clock(), result
            return result

    def get_server_metrics(self, server_id: str) -> dict:
        catalog = self.monitor_catalog()
        duration = (
            "1 Hour" if "1 Hour" in catalog["durations"] else catalog["durations"][0]
        )
        graphs = {}
        diagnostics = []
        for rule in self._settings.alert_rules:
            if rule.target not in catalog["targets"]:
                diagnostics.append(
                    dict(
                        metric=rule.target,
                        status="error",
                        message="Alert target is not in the DigitalOcean catalog",
                    )
                )
                continue
            graph = self.get_graph(server_id, rule.target, duration)
            graphs[rule.target] = graph
            if graph["status"] not in ("ok",):
                diagnostics.append(
                    dict(
                        metric=rule.target,
                        status=graph["status"],
                        message=graph.get("error") or "Cloudways task pending",
                    )
                )
            if graph["status"] in ("ok", "warning"):
                if any(
                    latest_complete_point(s["points"])
                    and sample_is_stale(
                        s, datetime.now(UTC), self._settings.stale_after_seconds
                    )
                    for s in graph["series"]
                ):
                    diagnostics.append(
                        dict(
                            metric=rule.target,
                            status="warning",
                            message="Latest valid source sample is stale",
                        )
                    )
        capacity = {}
        try:
            with self._key_lock(("capacity", server_id)):
                cached = self._capacities.get(server_id)
                if cached and self._clock() - cached[0] < 600:
                    capacity = cached[1]
                else:
                    payload = self._get("/servers/" + server_id)
                    if (
                        not isinstance(payload, dict)
                        or str(payload.get("id")) != server_id
                    ):
                        raise ValueError("Server capacity identity mismatch")
                    capacity = parse_capacity(payload)
                    if capacity:
                        capacity["fetched_at"] = datetime.now(UTC).isoformat()
                    self._capacities[server_id] = self._clock(), capacity
            if "memory_gb" not in capacity or "disk_gb" not in capacity:
                diagnostics.append(
                    dict(
                        metric="Capacity",
                        status="warning",
                        message="Some server capacities are unavailable",
                    )
                )
        except (CloudwaysApiError, ValueError, TypeError) as exc:
            diagnostics.append(
                dict(metric="Capacity", status="error", message=str(exc))
            )
        return dict(graphs=graphs, diagnostics=diagnostics, capacity=capacity)

    def get_application_analysis(
        self, application_id: str, server_id: str, category: str, duration: str
    ) -> dict:
        analyses = {
            "statuses": ("traffic", "top_statuses"),
            "urls": ("traffic", "top_urls"),
            "php": ("php", "slow_pages"),
            "mysql": ("mysql", "slow_queries"),
        }
        if category not in analyses or duration not in ("15m", "30m", "1h", "1d"):
            raise ValueError("Select an available application analysis and duration")
        endpoint, resource = analyses[category]
        key = ("analysis", server_id, application_id, category, duration)
        with self._key_lock(key):
            cached = self._analyses.get(key)
            if cached and self._clock() - cached[0] < (
                300 if cached[1]["status"] == "ok" else 3
            ):
                return cached[1]
            result = dict(
                category=category,
                duration=duration,
                columns=[],
                rows=[],
                summary={},
                partial=True,
            )
            try:
                task_key = (
                    ("traffic", server_id, application_id)
                    if category == "statuses" and duration == "1h"
                    else key
                )
                status, payload = self._task(
                    task_key,
                    "/app/analytics/" + endpoint,
                    dict(
                        server_id=server_id,
                        app_id=application_id,
                        resource=resource,
                        duration=duration,
                    ),
                )
                if status == "ok":
                    result.update(parse_analysis(payload, resource), status="ok")
                else:
                    result.update(status=status, error=payload)
            except (
                CloudwaysApiError,
                ValueError,
                TypeError,
                KeyError,
                OverflowError,
            ) as exc:
                result.update(
                    status="error",
                    error=str(exc),
                    error_code=getattr(exc, "code", "schema_error"),
                )
                if getattr(exc, "code", "") in ("rate_limited", "request_budget"):
                    result["retry_after_seconds"] = max(
                        1,
                        self._endpoint_pauses.get(
                            "/app/analytics/" + endpoint, self._clock() + 60
                        )
                        - self._clock(),
                    )
            result["fetched_at"] = datetime.now(UTC).isoformat()
            if result["status"] == "ok" and category == "statuses" and duration == "1h":
                self._traffic_results[(server_id, application_id)] = (
                    self._clock(),
                    dict(
                        traffic_requests=result["summary"]["returned_requests"],
                        traffic_complete=False,
                        traffic_window="Last 1 hour",
                        traffic_fetched_at=result["fetched_at"],
                    ),
                )
            self._cache_analysis(key, result)
            return result

    def _cache_analysis(self, key: tuple, result: dict):
        with self._lock:
            if len(self._analyses) >= 128 and key not in self._analyses:
                oldest = min(self._analyses, key=lambda k: self._analyses[k][0])
                self._analyses.pop(oldest)
            self._analyses[key] = self._clock(), result

    def get_application_metrics(
        self, application_id: str, server_id: str | None
    ) -> dict:
        metrics: dict[str, Any] = dict(diagnostics=[], bandwidth_status="unavailable")
        if server_id is None:
            metrics["diagnostics"].append(
                dict(
                    metric="Application",
                    status="error",
                    message="Missing parent server",
                )
            )
            return metrics
        try:
            with self._key_lock(("app-disk", server_id)):
                cached = self._disk_summaries.get(server_id)
                if (
                    not cached
                    or self._clock() - cached[0] >= self._settings.poll_interval_seconds
                ):
                    payload = self._get(
                        "/server/monitor/summary", dict(server_id=server_id, type="db")
                    )
                    self._disk_summaries[server_id] = self._clock(), payload
                else:
                    payload = cached[1]
            metrics.setdefault("raw", {})["disk"] = redact(payload)
            metrics.update(
                parse_application_disk(
                    payload,
                    self._application_users.get((server_id, application_id), ""),
                )
            )
            if "disk_used_gb" not in metrics:
                metrics["diagnostics"].append(
                    dict(
                        metric="Disk",
                        status="warning",
                        message="Latest application disk sample is null",
                    )
                )
        except (CloudwaysApiError, ValueError, KeyError, TypeError, IndexError) as exc:
            metrics["diagnostics"].append(
                dict(metric="Disk", status="error", message=str(exc))
            )
        cached = self._traffic_results.get((server_id, application_id))
        if cached and self._clock() - cached[0] < min(
            max(300, self._settings.poll_interval_seconds),
            self._settings.stale_after_seconds,
        ):
            metrics.update(cached[1])
            metrics["traffic_status"] = "cached"
            return metrics
        try:
            status, payload = self._task(
                ("traffic", server_id, application_id),
                "/app/analytics/traffic",
                dict(
                    server_id=server_id,
                    app_id=application_id,
                    resource="top_statuses",
                    duration="1h",
                ),
            )
            if status == "ok":
                metrics.setdefault("raw", {})["traffic"] = redact(payload)
                traffic = parse_traffic(payload)
                traffic["traffic_fetched_at"] = datetime.now(UTC).isoformat()
                body = unwrap(payload)
                if isinstance(body, dict) and "top_statuses" in body:
                    analysis = dict(
                        category="statuses",
                        duration="1h",
                        fetched_at=traffic["traffic_fetched_at"],
                        columns=[],
                        rows=[],
                        summary={},
                        partial=True,
                    )
                    try:
                        analysis.update(
                            parse_analysis(payload, "top_statuses"), status="ok"
                        )
                    except ValueError as exc:
                        analysis.update(status="error", error=str(exc))
                    self._cache_analysis(
                        ("analysis", server_id, application_id, "statuses", "1h"),
                        analysis,
                    )
                self._traffic_results[(server_id, application_id)] = (
                    self._clock(),
                    traffic,
                )
                metrics.update(traffic)
            else:
                if (
                    status == "pending"
                    and cached
                    and self._clock() - cached[0] <= self._settings.stale_after_seconds
                ):
                    metrics.update(cached[1])
                else:
                    metrics["diagnostics"].append(
                        dict(
                            metric="Requests",
                            status=status,
                            message=payload or "Cloudways task pending",
                        )
                    )
            metrics["traffic_status"] = status
        except (CloudwaysApiError, ValueError, KeyError, TypeError, IndexError) as exc:
            limited = getattr(exc, "code", "") == "rate_limited"
            metrics["traffic_status"] = "rate_limited" if limited else "error"
            if (
                limited
                and cached
                and self._clock() - cached[0] <= self._settings.stale_after_seconds
            ):
                metrics.update(cached[1])
            metrics["diagnostics"].append(
                dict(
                    metric="Requests",
                    status="warning" if limited else "error",
                    message=str(exc),
                )
            )
        return metrics
