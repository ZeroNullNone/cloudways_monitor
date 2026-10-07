from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from cloudways_monitor.targets import TARGET_DEFINITIONS

DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


def number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric monitoring value")
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid numeric monitoring value") from exc
    if not math.isfinite(parsed):
        raise ValueError("Non-finite monitoring value")
    return parsed


def timestamp(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Unix seconds and JavaScript Unix milliseconds are explicit supported formats.
        seconds = value / 1000 if abs(value) >= 1e12 else value
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # The graph request specifies Asia/Singapore, including naive labels.
            parsed = parsed.replace(tzinfo=DISPLAY_TIMEZONE)
        return parsed.astimezone(UTC).isoformat()
    raise ValueError("A monitoring sample must have a timestamp")


def unwrap(payload: Any) -> Any:
    for _ in range(8):
        if isinstance(payload, str):
            payload = json.loads(payload)
        elif isinstance(payload, dict):
            if "operation" in payload:
                payload = payload["operation"]
            elif ("unit" in payload and "data" in payload) or any(
                k in payload
                for k in (
                    "datapoints",
                    "series",
                    "content",
                    "top_statuses",
                    "total_requests",
                    "disk_used_gb",
                    "bandwidth_bytes",
                )
            ):
                return payload
            else:
                key = next(
                    (
                        k
                        for k in ("result", "response", "parameters", "graph", "data")
                        if k in payload
                    ),
                    None,
                )
                if key is None:
                    return payload
                payload = payload[key]
        else:
            return payload
    raise ValueError("Monitoring result exceeds supported envelope depth")


def parse_graph(payload: Any, target: str, duration: str) -> dict[str, Any]:
    body = unwrap(payload)
    unit = None
    categories = None
    if body is None:
        entries = []
    elif isinstance(body, dict):
        unit = body.get("unit")
        axis = body.get("xAxis", {})
        if isinstance(axis, dict):
            categories = axis.get("categories")
        if "series" in body:
            entries = body["series"]
        elif "datapoints" in body or "data" in body:
            entries = [body]
        elif "content" in body:
            entries = body["content"]
            if isinstance(entries, str):
                entries = json.loads(entries)
            if entries is None:
                entries = []
        else:
            raise ValueError("Unrecognized graph schema; expected timestamped series")
    elif isinstance(body, list):
        entries = body
    else:
        raise ValueError("Unrecognized graph schema; expected a JSON series")
    if not isinstance(entries, list):
        raise ValueError("Graph series must be an array")
    series = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("Graph series must contain named objects")
        source_unit = entry.get("unit", unit)
        unit_source = "Cloudways response"
        if not source_unit:
            source_unit = TARGET_DEFINITIONS.get(target, {}).get("unit")
            unit_source = (
                "confirmed target definition" if source_unit else "unconfirmed"
            )
        entry_unit = (
            "MB"
            if target == "Free memory" and source_unit in ("B", "bytes")
            else source_unit
        )
        name = str(entry.get("target", entry.get("name", target)))
        points = []
        if "datapoints" in entry:
            rows = entry["datapoints"]
            orientation = "value_time"
        elif "data" in entry:
            rows = entry["data"]
            orientation = "time_value"
        elif "datapoint" in entry:
            rows = [entry["datapoint"]]
            orientation = "value_time"
        else:
            raise ValueError("Graph series has no supported samples schema")
        if not isinstance(rows, list):
            raise ValueError("Graph samples must be an array")
        for i, row in enumerate(rows):
            if isinstance(row, dict) and "value" in row and "timestamp" in row:
                t, v = row["timestamp"], row["value"]
            elif isinstance(row, dict) and "x" in row and "y" in row:
                t, v = row["x"], row["y"]
            elif isinstance(row, (list, tuple)) and len(row) == 2:
                v, t = row if orientation == "value_time" else (row[1], row[0])
            elif categories is not None and i < len(categories):
                t, v = categories[i], row
            else:
                raise ValueError("Graph point has no explicit timestamp/value schema")
            value = number(v)
            if value is not None and entry_unit != source_unit:
                value = value_in_unit(value, source_unit, entry_unit)
            points.append({"timestamp": timestamp(t), "value": value})
            if (
                entry_unit in ("%", "percent")
                and points[-1]["value"] is not None
                and not 0 <= points[-1]["value"] <= 100
            ):
                raise ValueError(
                    "Percentage sample outside 0 to 100; graph schema requires verification"
                )
        points.sort(key=lambda p: p["timestamp"])
        if len({p["timestamp"] for p in points}) != len(points):
            raise ValueError("Graph has duplicate sample timestamps")
        values = [p["value"] for p in points if p["value"] is not None]
        summary = {}
        if values:
            summary = dict(
                min=min(values),
                max=max(values),
                avg=math.fsum(v / len(values) for v in values),
                count=len(values),
            )
        complete = latest_complete_point(points)
        if complete:
            summary["latest"] = complete["value"]
            summary["sample_at"] = complete["timestamp"]
        intervals = [
            (
                datetime.fromisoformat(b["timestamp"])
                - datetime.fromisoformat(a["timestamp"])
            ).total_seconds()
            for a, b in zip(points, points[1:])
        ]
        series.append(
            dict(
                name=name,
                unit=entry_unit,
                source_unit=source_unit,
                unit_source=unit_source,
                points=points,
                summary=summary,
                cadence_seconds=min(intervals) if intervals else None,
            )
        )
    unknown_units = any(not s["unit"] for s in series)
    return dict(
        target=target,
        duration=duration,
        timezone="UTC+8",
        series=series,
        status="warning" if unknown_units else "ok",
        error="Unit not confirmed for this target; unit-dependent alerts disabled"
        if unknown_units
        else None,
    )


def latest_complete_point(points):
    return next(
        (point for point in reversed(points) if point["value"] is not None), None
    )


def sample_is_stale(series, now, minimum_seconds):
    point = latest_complete_point(series.get("points", []))
    if point is None:
        return True
    cadence = series.get("cadence_seconds") or 0
    return (now - datetime.fromisoformat(point["timestamp"])).total_seconds() > max(
        minimum_seconds, cadence * 2
    )


def value_in_unit(value: float, unit: str | None, wanted: str) -> float | None:
    if unit == wanted:
        return value
    factors = {
        "B": 1,
        "bytes": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
    }
    if unit in factors and wanted in factors:
        return value * factors[unit] / factors[wanted]
    if wanted == "%" and unit in ("percent", "%"):
        return value
    return None


def parse_application_disk(payload: Any, sys_user: str) -> dict[str, Any]:
    """Server db summary contains app disk sizes, identified by system user.

    Cloudways' application disk monitor reports MB, confirmed by its help guide
    and a live crosscheck against the matching application total (KB / 1024).
    /app/monitor/summary db contains top tables, not total application usage.
    """
    body = unwrap(payload)
    if not sys_user:
        raise ValueError("Application system user is missing; disk identity unverified")
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        raise ValueError("Unrecognized server application disk summary schema")
    entries = [
        entry
        for entry in body["content"]
        if isinstance(entry, dict)
        and entry.get("name") == sys_user
        and entry.get("type") == "apps"
    ]
    if len(entries) != 1:
        raise ValueError("No unique disk entry for this application system user")
    entry = entries[0]
    row = entry.get("datapoint")
    if row is None:
        return {}
    if not isinstance(row, list) or len(row) != 2:
        raise ValueError("Invalid application disk datapoint")
    value = number(row[0])
    if value is None:
        return {}
    if value < 0:
        raise ValueError("Application disk usage cannot be negative")
    unit = entry.get("unit", body.get("unit", "MB"))
    converted = value_in_unit(value, unit, "GB")
    if converted is None:
        raise ValueError("Unverified application disk unit; value not displayed")
    return dict(disk_used_gb=converted, disk_used_gb_sample_at=timestamp(row[1]))


def parse_traffic(payload: Any) -> dict[str, Any]:
    body = unwrap(payload)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError("Unrecognized traffic schema")
    if "total_requests" in body:
        count = number(body["total_requests"])
        if count is None:
            return {}
        if count < 0 or not count.is_integer():
            raise ValueError("Invalid request count")
        return dict(
            traffic_requests=int(count),
            traffic_complete=body.get("complete") is True,
            traffic_window="Last 1 hour",
        )
    table = body.get("top_statuses")
    if (
        isinstance(table, dict)
        and isinstance(table.get("header"), list)
        and isinstance(table.get("body"), list)
    ):
        headers = [str(h).lower() for h in table["header"]]
        col = next(
            (
                headers.index(h)
                for h in ("requests", "request count", "hits", "count")
                if h in headers
            ),
            None,
        )
        if col is None:
            raise ValueError("Traffic table has no request count column")
        values = [number(row[col]) for row in table["body"]]
        if any(v is None or v < 0 or not v.is_integer() for v in values):
            raise ValueError("Invalid request count")
        return dict(
            traffic_requests=int(sum(values)),
            traffic_complete=False,
            traffic_window="Last 1 hour",
        )
    raise ValueError("Unrecognized traffic result schema")


def parse_capacity(payload: Any) -> dict[str, Any]:
    """Configured DO capacities; block storage is the data disk when attached."""
    if not isinstance(payload, dict):
        raise ValueError("Unrecognized server capacity schema")
    capacity = {}
    plan = payload.get("instance_type")
    match = (
        re.search(r"(?:^|-)(\d+(?:\.\d+)?)GB$", plan, re.I)
        if isinstance(plan, str)
        else None
    )
    if match and float(match[1]) > 0:
        capacity["memory_gb"] = float(match[1])
    block = number(payload.get("storage"))
    if block is not None and block < 0:
        raise ValueError("Invalid block storage capacity")
    field = "storage" if block is not None and block > 0 else "volume_size"
    disk = number(payload.get(field))
    if disk is not None:
        if disk <= 0:
            raise ValueError("Invalid disk capacity")
        capacity.update(disk_gb=disk, disk_source=field)
    return capacity


def parse_analysis(payload: Any, resource: str) -> dict[str, Any]:
    """Preserve top-table scope and null cells; never manufacture a full total."""
    body = unwrap(payload)
    table = body.get(resource) if isinstance(body, dict) else None
    if not isinstance(table, dict):
        raise ValueError("Unrecognized application analysis schema")
    columns, rows = table.get("header"), table.get("body")
    if (
        not isinstance(columns, list)
        or not columns
        or not all(isinstance(c, str) and c for c in columns)
    ):
        raise ValueError("Application analysis has no valid column headers")
    if not isinstance(rows, list):
        raise ValueError("Application analysis rows are missing; no count available")
    if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
        raise ValueError("Invalid application analysis row")
    if any(
        isinstance(cell, (dict, list, bool))
        or not (cell is None or isinstance(cell, (str, int, float)))
        for row in rows
        for cell in row
    ):
        raise ValueError("Invalid application analysis cell")
    lower = [c.lower() for c in columns]
    count_column = next(
        (
            i
            for i, c in enumerate(lower)
            if c in ("count", "occurrences", "requests", "request count", "hits")
        ),
        None,
    )
    if count_column is None:
        raise ValueError("Application analysis has no count column")
    counts = [number(row[count_column]) for row in rows]
    if any(v is None or v < 0 or not v.is_integer() for v in counts):
        raise ValueError("Application analysis has a missing or invalid count")
    summary = {}
    if resource == "top_statuses":
        if "status" not in lower:
            raise ValueError("Application analysis has no HTTP status column")
        statuses = [number(row[lower.index("status")]) for row in rows]
        if any(
            v is None or not v.is_integer() or not 100 <= v <= 599 for v in statuses
        ):
            raise ValueError("Invalid HTTP status code")
        summary = dict(
            returned_requests=int(sum(counts)),
            client_errors=int(
                sum(c for s, c in zip(statuses, counts) if 400 <= s < 500)
            ),
            server_errors=int(
                sum(c for s, c in zip(statuses, counts) if 500 <= s < 600)
            ),
        )
    elif resource == "top_urls":
        summary = dict(returned_urls=len(rows))
    elif resource == "slow_pages":
        summary = dict(returned_slow_pages=len(rows))
    elif resource == "slow_queries":
        summary = dict(returned_slow_queries=len(rows))
    return dict(columns=columns, rows=rows, summary=summary, partial=True)
