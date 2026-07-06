from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Literal, TypedDict

import httpx

from cloudways_monitor.settings import Settings


class DiscoveredResource(TypedDict):
    provider_id: str
    resource_type: Literal["server", "application"]
    name: str
    parent_provider_id: str | None
    raw: dict[str, Any]


class CloudwaysApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        code: str,
        detail: object,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.detail = detail


class CloudwaysClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or httpx.Client(
            base_url=settings.cloudways_api_base_url,
            timeout=30.0,
        )
        self._access_token: str | None = None
        self._server_cache: list[dict[str, Any]] | None = None
        self._pending_tasks: dict[tuple[str, ...], str] = {}

    def authenticate(self) -> str:
        if self._access_token is not None:
            return self._access_token

        response = self._request(
            "POST",
            "/oauth/access_token",
            data={
                "email": self._settings.cloudways_email,
                "api_key": self._settings.cloudways_api_key,
                "grant_type": "password",
            },
            authenticated=False,
        )
        payload = response.json()
        self._access_token = str(payload["access_token"])
        return self._access_token

    def list_servers(self) -> list[DiscoveredResource]:
        return [_server_resource(server) for server in self._server_items()]

    def list_applications(self) -> list[DiscoveredResource]:
        applications: list[DiscoveredResource] = []
        for server in self._server_items():
            parent_provider_id = _required_provider_id(
                server,
                "id",
                "server_id",
                "serverId",
            )
            for application in _payload_items(server, "apps", "applications"):
                applications.append(
                    _application_resource(
                        application,
                        fallback_parent_provider_id=parent_provider_id,
                    )
                )
        return applications

    def get_server_metrics(self, server_id: str) -> dict[str, Any]:
        bandwidth_summary = self._get_monitor_summary(
            "/server/monitor/summary",
            {"server_id": server_id, "type": "bw"},
        )
        disk_summary = self._get_monitor_summary(
            "/server/monitor/summary",
            {"server_id": server_id, "type": "db"},
        )
        metrics = _monitor_summary_metrics(
            bandwidth_summary=bandwidth_summary,
            disk_summary=disk_summary,
        )
        if self._settings.cloudways_task_polling_enabled:
            task_metrics, task_results = self._server_graph_metrics(server_id)
            metrics.update(task_metrics)
            if task_results:
                metrics.setdefault("task_results", {})["server_graphs"] = task_results
        return metrics

    def get_application_metrics(
        self,
        application_id: str,
        server_id: str | None,
    ) -> dict[str, Any]:
        if server_id is None:
            return {}
        bandwidth_summary = self._get_monitor_summary(
            "/app/monitor/summary",
            {"server_id": server_id, "app_id": application_id, "type": "bw"},
        )
        disk_summary = self._get_monitor_summary(
            "/app/monitor/summary",
            {"server_id": server_id, "app_id": application_id, "type": "db"},
        )
        metrics = _monitor_summary_metrics(
            bandwidth_summary=bandwidth_summary,
            disk_summary=disk_summary,
        )
        if (
            self._settings.cloudways_task_polling_enabled
            and self._settings.cloudways_app_traffic_polling_enabled
        ):
            traffic_result = self._app_traffic_task_result(
                application_id=application_id,
                server_id=server_id,
            )
            if traffic_result:
                metrics.setdefault("task_results", {})["app_traffic"] = traffic_result
                traffic_requests = _traffic_request_count(traffic_result)
                if traffic_requests is not None:
                    metrics["traffic_requests"] = traffic_requests
        return metrics

    def _server_graph_metrics(
        self,
        server_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        server = self._server_metadata(server_id)
        if server is None:
            return {}, {}

        task_results: dict[str, Any] = {}
        idle_cpu = self._server_graph_task_result(server_id, "Idle CPU")
        if idle_cpu:
            task_results["Idle CPU"] = idle_cpu
        free_memory = self._server_graph_task_result(server_id, "Free memory")
        if free_memory:
            task_results["Free memory"] = free_memory
        disk_target = _server_disk_graph_target(server)
        free_disk = self._server_graph_task_result(server_id, disk_target)
        if free_disk:
            task_results[disk_target] = free_disk

        metrics: dict[str, Any] = {}
        idle_cpu_value = _graph_latest_value(idle_cpu)
        if idle_cpu_value is not None:
            metrics["cpu_percent"] = max(0.0, min(100.0, 100.0 - idle_cpu_value))

        total_ram_mb = _server_total_memory_mb(server)
        free_memory_value = _graph_latest_value(free_memory)
        if total_ram_mb is not None and free_memory_value is not None:
            free_memory_mb = _memory_graph_value_to_mb(
                free_memory_value,
                total_mb=total_ram_mb,
            )
            ram_used_mb = max(0.0, total_ram_mb - free_memory_mb)
            metrics["ram_used_mb"] = ram_used_mb
            metrics["ram_total_mb"] = total_ram_mb
            metrics["ram_percent"] = _percentage(ram_used_mb, total_ram_mb)

        total_disk_gb = _server_total_disk_gb(server)
        free_disk_value = _graph_latest_value(free_disk)
        if total_disk_gb is not None and free_disk_value is not None:
            free_disk_gb = _disk_graph_value_to_gb(
                free_disk_value,
                total_gb=total_disk_gb,
            )
            disk_used_gb = max(0.0, total_disk_gb - free_disk_gb)
            metrics["disk_used_gb"] = disk_used_gb
            metrics["disk_total_gb"] = total_disk_gb
            metrics["disk_percent"] = _percentage(disk_used_gb, total_disk_gb)

        return metrics, task_results

    def _server_graph_task_result(self, server_id: str, target: str) -> dict[str, Any]:
        return self._task_result_for(
            ("server_graph", server_id, target),
            lambda: self._get(
                "/server/monitor/detail",
                params={
                    "server_id": server_id,
                    "target": target,
                    "duration": self._settings.cloudways_monitor_graph_duration,
                    "strorge": "false",
                    "timezone": self._settings.cloudways_monitor_graph_timezone,
                    "output_format": "json",
                },
            ),
        )

    def _app_traffic_task_result(
        self,
        *,
        application_id: str,
        server_id: str,
    ) -> dict[str, Any]:
        return self._task_result_for(
            ("app_traffic", server_id, application_id, "top_statuses"),
            lambda: self._get(
                "/app/analytics/traffic",
                params={
                    "server_id": server_id,
                    "app_id": application_id,
                    "duration": self._settings.cloudways_app_traffic_duration,
                    "resource": "top_statuses",
                },
            ),
        )

    def _task_result_for(
        self,
        task_key: tuple[str, ...],
        start_task: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        task_id = self._pending_tasks.get(task_key)
        start_payload: dict[str, Any] | None = None
        if task_id is None:
            start_payload = start_task()
            task_id = _task_id(start_payload)
            if task_id is None:
                redacted_start_payload = _redact_sensitive_fields(start_payload)
                if _task_completed(start_payload):
                    return {
                        "status": "inline_result",
                        "result": redacted_start_payload,
                    }
                return {
                    "status": "missing_task_id",
                    "start": redacted_start_payload,
                }
            self._pending_tasks[task_key] = task_id

        result_payload = self._poll_task(task_id)
        task_result: dict[str, Any] = {
            "task_id": task_id,
            "result": _redact_sensitive_fields(result_payload),
        }
        if start_payload is not None:
            task_result["start"] = _redact_sensitive_fields(start_payload)

        if _task_completed(result_payload) or _task_failed(result_payload):
            self._pending_tasks.pop(task_key, None)
        return task_result

    def _poll_task(self, task_id: str) -> dict[str, Any]:
        attempts = max(1, self._settings.cloudways_task_poll_attempts)
        last_payload: dict[str, Any] | None = None
        for attempt in range(attempts):
            try:
                last_payload = self._get(f"/operation/{task_id}")
            except CloudwaysApiError as exc:
                if exc.status_code in (400, 404, 422):
                    return {
                        "status": False,
                        "error_code": exc.code,
                        "error_detail": exc.detail,
                    }
                raise
            if _task_completed(last_payload) or _task_failed(last_payload):
                return last_payload
            if attempt < attempts - 1 and self._settings.cloudways_task_poll_interval_seconds:
                time.sleep(self._settings.cloudways_task_poll_interval_seconds)
        return last_payload or {}

    def _server_metadata(self, server_id: str) -> dict[str, Any] | None:
        for server in self._server_items():
            provider_id = _first_provider_id(
                server.get("id"),
                server.get("server_id"),
                server.get("serverId"),
            )
            if provider_id == server_id:
                return server
        return None
    def _server_items(self) -> list[dict[str, Any]]:
        if self._server_cache is None:
            payload = self._get("/server")
            self._server_cache = _payload_items(payload, "servers")
        return list(self._server_cache)

    def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self._request("GET", path, authenticated=True, params=params)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Cloudways response payload must be a JSON object")
        return payload

    def _get_monitor_summary(
        self,
        path: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        try:
            return self._get(path, params=params)
        except CloudwaysApiError as exc:
            if exc.status_code in (400, 404, 422):
                return {
                    "status": False,
                    "error_code": exc.code,
                    "error_detail": exc.detail,
                }
            raise

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if authenticated:
            token = self.authenticate()
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self._http_client.request(
                method,
                path,
                data=data,
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise _api_error_from_response(exc.response) from exc
        except httpx.RequestError as exc:
            raise CloudwaysApiError(
                "Cloudways API request failed",
                status_code=None,
                code="network_error",
                detail={"error": exc.__class__.__name__},
            ) from exc


def _monitor_summary_metrics(
    *,
    bandwidth_summary: dict[str, Any],
    disk_summary: dict[str, Any],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "monitor_summary": {
            "bandwidth": bandwidth_summary,
            "disk": disk_summary,
        }
    }

    bandwidth_bytes = _bandwidth_summary_bytes(bandwidth_summary)
    if bandwidth_bytes is not None:
        metrics["bandwidth_bytes"] = bandwidth_bytes

    disk_used_gb, disk_total_gb = _disk_summary_values(disk_summary)
    if disk_used_gb is not None:
        metrics["disk_used_gb"] = disk_used_gb
    if disk_total_gb is not None:
        metrics["disk_total_gb"] = disk_total_gb

    return metrics


def _bandwidth_summary_bytes(payload: dict[str, Any]) -> int | None:
    content = _summary_content(payload)
    if isinstance(content, dict):
        total = _as_float(content.get("total"))
        if total is not None and _looks_like_app_bandwidth_summary(content):
            return int(round(total * 1024))

    bandwidth_value = _summary_value(
        payload,
        preferred_type="bw",
        preferred_name_terms=("bandwidth", "traffic"),
    )
    if bandwidth_value is None:
        return None
    return int(round(bandwidth_value))


def _looks_like_app_bandwidth_summary(content: dict[str, Any]) -> bool:
    return any(key in content for key in ("app_home", "app_mysql"))

def _summary_value(
    payload: dict[str, Any],
    *,
    preferred_type: str | None,
    preferred_name_terms: tuple[str, ...],
) -> float | None:
    for item in _preferred_summary_items(
        _summary_items(payload),
        preferred_type=preferred_type,
        preferred_name_terms=preferred_name_terms,
    ):
        values = _datapoint_numbers(item)
        if values:
            return values[0]
        value = _first_number_from_keys(item, ("value", "used", "size"))
        if value is not None:
            return value
    return None


def _disk_summary_values(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    used_values: list[float] = []
    disk_total_gb: float | None = None
    for item in _preferred_summary_items(
        _summary_items(payload),
        preferred_type="db",
        preferred_name_terms=("disk", "storage", "database", "db"),
    ):
        values = _datapoint_numbers(item)
        disk_used_gb = _disk_item_used_gb(item, values)
        if disk_used_gb is not None:
            used_values.append(disk_used_gb)

        explicit_total = _first_number_from_keys(
            item,
            ("total_gb", "disk_total_gb", "storage_total_gb", "total"),
        )
        if explicit_total is not None:
            disk_total_gb = explicit_total
        elif disk_total_gb is None and len(values) > 1:
            disk_total_gb = _plausible_total(values[1], disk_used_gb)

    if not used_values:
        return None, disk_total_gb
    return sum(used_values), disk_total_gb


def _disk_item_used_gb(item: dict[str, Any], values: list[float]) -> float | None:
    if values:
        return values[0]

    explicit_gb = _first_number_from_keys(
        item,
        ("used_gb", "disk_used_gb", "storage_used_gb"),
    )
    if explicit_gb is not None:
        return explicit_gb

    size_kb = _as_float(item.get("size"))
    if size_kb is not None:
        return _kilobytes_to_gigabytes(size_kb)

    return _first_number_from_keys(
        item,
        ("used", "disk_used", "storage_used", "value"),
    )


def _summary_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _normalise_summary_items(_summary_content(payload))


def _summary_content(payload: dict[str, Any]) -> object:
    for container in _payload_containers(payload):
        for key in ("content", "contents"):
            if key in container:
                return container[key]
    return None


def _normalise_summary_items(raw_items: object) -> list[dict[str, Any]]:
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, dict)]
    if isinstance(raw_items, dict):
        if _summary_dict_is_metric_item(raw_items):
            return [raw_items]
        items: list[dict[str, Any]] = []
        for value in raw_items.values():
            items.extend(_normalise_summary_items(value))
        return items
    if isinstance(raw_items, str):
        stripped = raw_items.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return []
        return _normalise_summary_items(parsed)
    return []


def _summary_dict_is_metric_item(raw_items: dict[str, Any]) -> bool:
    if "datapoint" in raw_items or "value" in raw_items:
        return True
    return any(_as_float(value) is not None for value in raw_items.values())
def _preferred_summary_items(
    items: list[dict[str, Any]],
    *,
    preferred_type: str | None,
    preferred_name_terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    preferred = [
        item
        for item in items
        if _summary_item_matches(
            item,
            preferred_type=preferred_type,
            preferred_name_terms=preferred_name_terms,
        )
    ]
    if not preferred:
        return items
    return preferred + [item for item in items if item not in preferred]


def _summary_item_matches(
    item: dict[str, Any],
    *,
    preferred_type: str | None,
    preferred_name_terms: tuple[str, ...],
) -> bool:
    raw_type = item.get("type")
    item_type = str(raw_type).lower() if raw_type is not None else ""
    if preferred_type is not None and item_type == preferred_type:
        return True

    name = str(item.get("name", "")).lower()
    return any(term in name for term in preferred_name_terms)


def _datapoint_numbers(item: dict[str, Any]) -> list[float]:
    raw_datapoint = _first_present(
        item,
        ("datapoint", "data_point", "dataPoint", "values", "value"),
    )
    if isinstance(raw_datapoint, list | tuple):
        return [value for value in (_as_float(raw) for raw in raw_datapoint) if value is not None]
    if isinstance(raw_datapoint, dict):
        values: list[float] = []
        for key in ("used", "current", "value", "total", "size", "y"):
            value = _as_float(raw_datapoint.get(key))
            if value is not None:
                values.append(value)
        if values:
            return values
        return [
            value
            for value in (_as_float(raw) for raw in raw_datapoint.values())
            if value is not None
        ]
    value = _as_float(raw_datapoint)
    return [] if value is None else [value]


def _redacted_dict(value: dict[str, Any]) -> dict[str, Any]:
    redacted = _redact_sensitive_fields(value)
    if isinstance(redacted, dict):
        return redacted
    return {}

SENSITIVE_FIELD_TERMS = (
    "api_key",
    "app_password",
    "master_password",
    "mysql_password",
    "password",
    "private_key",
    "redis_password",
    "secret",
    "sys_password",
    "token",
)
REDACTED_FIELD_VALUE = "[redacted]"


def _redact_sensitive_fields(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, child in value.items():
            key_text = str(key).lower()
            if any(term in key_text for term in SENSITIVE_FIELD_TERMS):
                redacted[key] = REDACTED_FIELD_VALUE
            else:
                redacted[key] = _redact_sensitive_fields(child)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_fields(item) for item in value]
    return value

def _task_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("task_id") or payload.get("operation_id")
    if value in (None, ""):
        return None
    return str(value)


def _task_completed(payload: dict[str, Any]) -> bool:
    operation = _operation_payload(payload)
    completed = _completion_value(operation)
    if completed == 1:
        return True
    if completed is None and _has_analytics_result(payload):
        return True
    status = str(operation.get("status", payload.get("status", ""))).lower()
    return status in {"complete", "completed", "done", "success", "succeeded"}


def _task_failed(payload: dict[str, Any]) -> bool:
    operation = _operation_payload(payload)
    completed = _completion_value(operation)
    if completed == -1:
        return True
    if "error_code" in payload:
        return True
    status = str(operation.get("status", payload.get("status", ""))).lower()
    return status in {"failed", "error", "errored"}


def _operation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    operation = payload.get("operation")
    if isinstance(operation, dict):
        return operation
    return payload


def _completion_value(payload: dict[str, Any]) -> int | None:
    for key in ("is_completed", "completed"):
        value = payload.get(key)
        if value in (True, "true", "True"):
            return 1
        if value in (False, "false", "False"):
            return 0
        numeric = _as_float(value)
        if numeric is not None:
            return int(numeric)
    return None


def _has_analytics_result(payload: dict[str, Any]) -> bool:
    candidate = _operation_payload(payload)
    return any(
        key in candidate
        for key in (
            "data",
            "parameters",
            "result",
            "results",
            "response",
            "payload",
            "graph",
            "content",
            "contents",
        )
    )


def _analytics_payload(payload: object) -> object:
    parsed = _parse_json_string(payload)
    if isinstance(parsed, dict):
        operation = parsed.get("operation")
        if isinstance(operation, dict):
            parsed = operation

        for key in (
            "data",
            "parameters",
            "result",
            "results",
            "response",
            "payload",
            "graph",
            "content",
            "contents",
        ):
            if key in parsed:
                value = _analytics_payload(parsed[key])
                if _has_payload_content(value):
                    return value
        return parsed
    if isinstance(parsed, list):
        return [_analytics_payload(item) for item in parsed]
    return parsed


def _has_payload_content(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple)):
        return bool(value)
    return True


def _parse_json_string(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except ValueError:
        return value


def _graph_latest_value(task_result: object) -> float | None:
    if not isinstance(task_result, dict):
        return None
    result = task_result.get("result")
    if not isinstance(result, dict) or not _task_completed(result):
        return None
    payload = _analytics_payload(result)
    points: list[tuple[float | None, float]] = []
    _collect_graph_points(payload, points)
    if not points:
        return None
    timestamped = [point for point in points if point[0] is not None]
    if timestamped:
        return max(timestamped, key=lambda point: point[0] or 0)[1]
    return points[-1][1]


def _collect_graph_points(
    value: object,
    points: list[tuple[float | None, float]],
) -> None:
    if isinstance(value, dict):
        datapoint = _first_present(
            value,
            ("datapoint", "data_point", "dataPoint", "point"),
        )
        if datapoint is not None:
            _collect_graph_points(datapoint, points)
            return

        value_candidate = _first_number_from_keys(value, ("value", "y", "current"))
        if value_candidate is not None:
            timestamp = _first_number_from_keys(value, ("timestamp", "time", "x"))
            points.append((timestamp, value_candidate))
            return

        for child in value.values():
            _collect_graph_points(child, points)
        return

    if isinstance(value, list | tuple):
        numeric_values = [_as_float(item) for item in value]
        if len(value) == 2 and all(item is not None for item in numeric_values):
            first = numeric_values[0]
            second = numeric_values[1]
            assert first is not None and second is not None
            if _looks_like_timestamp(first):
                points.append((first, second))
            elif _looks_like_timestamp(second):
                points.append((second, first))
            else:
                points.append((None, second))
            return

        for child in value:
            _collect_graph_points(child, points)


def _looks_like_timestamp(value: float) -> bool:
    return value >= 1_000_000_000


def _traffic_request_count(task_result: dict[str, Any]) -> int | None:
    result = task_result.get("result")
    if not isinstance(result, dict) or not _task_completed(result):
        return None
    payload = _analytics_payload(result)
    count = _count_requests(payload)
    if count is None:
        return 0 if _has_empty_traffic_table(payload) else None
    return int(round(count))


def _has_empty_traffic_table(value: object) -> bool:
    if isinstance(value, dict):
        top_statuses = value.get("top_statuses")
        if isinstance(top_statuses, dict) and top_statuses.get("body") == []:
            return True
        if value.get("body") == [] and "header" in value:
            return True
        return any(_has_empty_traffic_table(child) for child in value.values())
    if isinstance(value, list | tuple):
        return any(_has_empty_traffic_table(child) for child in value)
    return False

def _count_requests(value: object) -> float | None:
    if isinstance(value, dict):
        own_value = _first_number_from_keys(
            value,
            ("requests", "request_count", "hits", "count", "total_requests"),
        )
        if own_value is not None:
            return own_value
        child_values = [_count_requests(child) for child in value.values()]
        child_numbers = [child for child in child_values if child is not None]
        if child_numbers:
            return sum(child_numbers)
        return None
    if isinstance(value, list | tuple):
        table_count = _table_request_count(value)
        if table_count is not None:
            return table_count
        child_values = [_count_requests(child) for child in value]
        child_numbers = [child for child in child_values if child is not None]
        if child_numbers:
            return sum(child_numbers)
    return None


def _table_request_count(value: list[object] | tuple[object, ...]) -> float | None:
    total = 0.0
    matched = False
    for row in value:
        if not isinstance(row, list | tuple) or len(row) < 2:
            continue
        count = _as_float(row[-1])
        if count is None:
            continue
        total += count
        matched = True
    if not matched:
        return None
    return total


def _server_total_memory_mb(server: dict[str, Any]) -> float | None:
    for key in ("instance_type", "cloud_plan_id"):
        value = server.get(key)
        if not isinstance(value, str):
            continue
        match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb)", value, flags=re.IGNORECASE)
        if match is None:
            continue
        amount = float(match.group(1))
        unit = match.group(2).lower()
        return amount * 1024 if unit == "gb" else amount
    return None


def _server_total_disk_gb(server: dict[str, Any]) -> float | None:
    candidates = [
        _as_float(server.get(key))
        for key in (
            "storage",
            "volume_size",
            "data_volume_size",
            "db_volume_size",
        )
    ]
    values = [candidate for candidate in candidates if candidate is not None and candidate > 0]
    if not values:
        return None
    return max(values)


def _server_disk_graph_target(server: dict[str, Any]) -> str:
    cloud = str(server.get("cloud", "")).lower()
    if cloud in {"amazon", "aws", "gce"}:
        return "Free Disk (Data)"
    return "Free Disk"


def _memory_graph_value_to_mb(value: float, *, total_mb: float) -> float:
    if value > total_mb * 1024:
        return value / 1024 / 1024
    if value > total_mb:
        return value / 1024
    return value


def _disk_graph_value_to_gb(value: float, *, total_gb: float) -> float:
    if value <= total_gb:
        return value
    if value <= total_gb * 1024 * 1.5:
        return value / 1024
    return value / 1024 / 1024


def _percentage(used: float | None, total: float | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return used / total * 100
def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _first_number_from_keys(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> float | None:
    for key in keys:
        value = _as_float(payload.get(key))
        if value is not None:
            return value
    return None


def _plausible_total(candidate: float, used: float | None) -> float | None:
    if candidate <= 0 or candidate >= 1_000_000:
        return None
    if used is not None and candidate < used:
        return None
    return candidate


def _kilobytes_to_gigabytes(value: float) -> float:
    return value / 1024 / 1024
def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None

def _api_error_from_response(response: httpx.Response) -> CloudwaysApiError:
    detail = _response_detail(response)
    return CloudwaysApiError(
        f"Cloudways API returned HTTP {response.status_code}",
        status_code=response.status_code,
        code=_error_code(response.status_code),
        detail=detail,
    )


def _response_detail(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return {"message": response.text}


def _error_code(status_code: int) -> str:
    if status_code == 400:
        return "missing_access_token"
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "authorization_error"
    if status_code == 422:
        return "validation_error"
    if status_code == 500:
        return "server_error"
    return "http_error"


def _payload_items(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for container in _payload_containers(payload):
        for key in keys:
            if key not in container:
                continue
            raw_items = container[key]
            if not isinstance(raw_items, list):
                raise ValueError(f"Cloudways response field {key!r} must be a list")
            return [item for item in raw_items if isinstance(item, dict)]
    return []


def _payload_containers(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    data = payload.get("data")
    if isinstance(data, dict):
        return (payload, data)
    return (payload,)


def _server_resource(server: dict[str, Any]) -> DiscoveredResource:
    provider_id = _required_provider_id(server, "id", "server_id", "serverId")
    return {
        "provider_id": provider_id,
        "resource_type": "server",
        "name": _resource_name(server, fallback=provider_id),
        "parent_provider_id": None,
        "raw": _redacted_dict(server),
    }


def _application_resource(
    application: dict[str, Any],
    *,
    fallback_parent_provider_id: str | None,
) -> DiscoveredResource:
    provider_id = _required_provider_id(application, "id", "app_id", "appId")
    parent_provider_id = _first_provider_id(
        application.get("server_id"),
        application.get("serverId"),
        _nested_provider_id(application.get("server"), "id", "server_id", "serverId"),
        fallback_parent_provider_id,
    )
    return {
        "provider_id": provider_id,
        "resource_type": "application",
        "name": _resource_name(
            application,
            fallback=provider_id,
            keys=("label", "app_label", "name"),
        ),
        "parent_provider_id": parent_provider_id,
        "raw": _redacted_dict(application),
    }


def _required_provider_id(payload: dict[str, Any], *keys: str) -> str:
    provider_id = _first_provider_id(*(payload.get(key) for key in keys))
    if provider_id is None:
        raise ValueError("Cloudways resource is missing an id")
    return provider_id


def _first_provider_id(*values: object) -> str | None:
    for value in values:
        provider_id = _optional_provider_id(value)
        if provider_id is not None:
            return provider_id
    return None


def _nested_provider_id(value: object, *keys: str) -> str | None:
    if isinstance(value, dict):
        return _first_provider_id(*(value.get(key) for key in keys))
    return _optional_provider_id(value)


def _optional_provider_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _resource_name(
    payload: dict[str, Any],
    fallback: str,
    *,
    keys: tuple[str, ...] = ("label", "name", "app_label"),
) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback
