from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast


ResourceType = Literal["server", "application"]


@dataclass(frozen=True)
class MetricSnapshot:
    resource_id: int
    resource_type: ResourceType
    captured_at: datetime
    cpu_percent: float | None
    ram_used_mb: float | None
    ram_total_mb: float | None
    ram_percent: float | None
    disk_used_gb: float | None
    disk_total_gb: float | None
    disk_percent: float | None
    bandwidth_bytes: int | None
    traffic_requests: int | None
    php_metric: dict[str, Any]
    mysql_metric: dict[str, Any]
    raw_payload: dict[str, Any]
    collection_status: str
    error_code: str | None


@dataclass(frozen=True)
class MonitoredResource:
    id: int
    provider_id: str
    resource_type: ResourceType
    name: str
    parent_provider_id: str | None
    raw: dict[str, Any]
    discovered_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AlertState:
    id: int
    resource_id: int
    rule_key: str
    status: str
    severity: str | None
    consecutive_breaches: int
    opened_at: datetime | None
    resolved_at: datetime | None
    last_notification_at: datetime | None


@dataclass(frozen=True)
class AlertEvent:
    id: int
    resource_id: int
    rule_key: str
    event_type: str
    severity: str | None
    message: str
    created_at: datetime


class Database:
    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)

    def connect(self) -> sqlite3.Connection:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitored_resources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL CHECK (
                        resource_type IN ('server', 'application')
                    ),
                    name TEXT NOT NULL,
                    parent_provider_id TEXT,
                    raw_json TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (provider_id, resource_type)
                );

                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_id INTEGER NOT NULL REFERENCES monitored_resources(id)
                        ON DELETE CASCADE,
                    resource_type TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    cpu_percent REAL,
                    ram_used_mb REAL,
                    ram_total_mb REAL,
                    ram_percent REAL,
                    disk_used_gb REAL,
                    disk_total_gb REAL,
                    disk_percent REAL,
                    bandwidth_bytes INTEGER,
                    traffic_requests INTEGER,
                    php_metric_json TEXT,
                    mysql_metric_json TEXT,
                    raw_payload_json TEXT NOT NULL,
                    collection_status TEXT NOT NULL,
                    error_code TEXT
                );

                CREATE TABLE IF NOT EXISTS collector_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    detail_json TEXT
                );

                CREATE TABLE IF NOT EXISTS alert_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_id INTEGER NOT NULL REFERENCES monitored_resources(id)
                        ON DELETE CASCADE,
                    rule_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT,
                    consecutive_breaches INTEGER NOT NULL DEFAULT 0,
                    opened_at TEXT,
                    resolved_at TEXT,
                    last_notification_at TEXT,
                    UNIQUE (resource_id, rule_key)
                );

                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_id INTEGER NOT NULL REFERENCES monitored_resources(id)
                        ON DELETE CASCADE,
                    rule_key TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_resource_time
                    ON metric_snapshots(resource_id, captured_at);

                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_captured_at
                    ON metric_snapshots(captured_at);

                CREATE TABLE IF NOT EXISTS monitor_graphs (
                    resource_id INTEGER NOT NULL REFERENCES monitored_resources(id) ON DELETE CASCADE,
                    target TEXT NOT NULL,
                    duration TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(resource_id, target, duration)
                );
                CREATE TABLE IF NOT EXISTS alert_samples (
                    resource_id INTEGER NOT NULL REFERENCES monitored_resources(id) ON DELETE CASCADE,
                    target TEXT NOT NULL,
                    sample_at TEXT NOT NULL,
                    PRIMARY KEY(resource_id, target)
                );
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER UNIQUE REFERENCES alert_events(id) ON DELETE CASCADE,
                    message TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS collector_alert_state (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    payload_json TEXT NOT NULL
                );
                """
            )


class Storage:
    def __init__(self, database: Database) -> None:
        self._database = database

    def prune_resources(self, identities: set[tuple[str, str]]) -> None:
        """Called only after a complete successful paginated discovery."""
        with self._database.connect() as connection:
            for row in connection.execute(
                "SELECT id, provider_id, resource_type FROM monitored_resources"
            ).fetchall():
                if (row["provider_id"], row["resource_type"]) not in identities:
                    connection.execute(
                        "DELETE FROM monitored_resources WHERE id = ?", (row["id"],)
                    )

    def save_graph(self, resource_id: int, payload: dict[str, Any]) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO monitor_graphs VALUES (?, ?, ?, ?, ?) ON CONFLICT(resource_id,target,duration) DO UPDATE SET fetched_at=excluded.fetched_at,payload_json=excluded.payload_json",
                (
                    resource_id,
                    payload["target"],
                    payload["duration"],
                    payload["fetched_at"],
                    _json(payload),
                ),
            )

    def get_graph(
        self, resource_id: int, target: str, duration: str
    ) -> dict[str, Any] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM monitor_graphs WHERE resource_id=? AND target=? AND duration=?",
                (resource_id, target, duration),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def claim_alert_sample(self, resource_id: int, target: str, sample_at: str) -> bool:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT sample_at FROM alert_samples WHERE resource_id=? AND target=?",
                (resource_id, target),
            ).fetchone()
            if row and row[0] >= sample_at:
                return False
            connection.execute(
                "INSERT INTO alert_samples VALUES(?,?,?) ON CONFLICT(resource_id,target) DO UPDATE SET sample_at=excluded.sample_at",
                (resource_id, target, sample_at),
            )
        return True

    def queue_notification(self, event_id: int | None, message: str) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO notification_outbox(event_id,message) VALUES(?,?)",
                (event_id, message),
            )

    def pending_notifications(self) -> list[tuple[int, str]]:
        with self._database.connect() as connection:
            return [
                (r[0], r[1])
                for r in connection.execute(
                    "SELECT id,message FROM notification_outbox WHERE delivered=0 ORDER BY id"
                ).fetchall()
            ]

    def mark_notification_delivered(self, event_id: int) -> None:
        with self._database.connect() as connection:
            event = connection.execute(
                "SELECT e.resource_id,e.rule_key FROM notification_outbox o JOIN alert_events e ON e.id=o.event_id WHERE o.id=?",
                (event_id,),
            ).fetchone()
            connection.execute(
                "UPDATE notification_outbox SET delivered=1 WHERE id=?",
                (event_id,),
            )
            if event:
                from datetime import UTC

                connection.execute(
                    "UPDATE alert_states SET last_notification_at=? WHERE resource_id=? AND rule_key=?",
                    (datetime.now(UTC).isoformat(), event[0], event[1]),
                )

    def has_pending_notification(self, resource_id: int, target: str) -> bool:
        with self._database.connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM notification_outbox o JOIN alert_events e ON e.id=o.event_id WHERE o.delivered=0 AND e.resource_id=? AND e.rule_key=? LIMIT 1",
                    (resource_id, target),
                ).fetchone()
                is not None
            )

    def get_collector_alert(self) -> dict[str, Any]:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collector_alert_state WHERE id=1"
            ).fetchone()
        return (
            json.loads(row[0])
            if row
            else dict(failures=0, active=False, last_notification_at=None)
        )

    def save_collector_alert(self, state: dict[str, Any]) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO collector_alert_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json",
                (_json(state),),
            )

    def upsert_resource(
        self,
        *,
        provider_id: str,
        resource_type: ResourceType,
        name: str,
        parent_provider_id: str | None,
        raw: dict[str, Any],
        discovered_at: datetime,
    ) -> int:
        timestamp = _format_datetime(discovered_at)
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO monitored_resources (
                    provider_id,
                    resource_type,
                    name,
                    parent_provider_id,
                    raw_json,
                    discovered_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id, resource_type) DO UPDATE SET
                    name = excluded.name,
                    parent_provider_id = excluded.parent_provider_id,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    provider_id,
                    resource_type,
                    name,
                    parent_provider_id,
                    _json(raw),
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT id FROM monitored_resources
                WHERE provider_id = ? AND resource_type = ?
                """,
                (provider_id, resource_type),
            ).fetchone()
        return int(row["id"])

    def list_resources(self) -> list[MonitoredResource]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM monitored_resources
                ORDER BY resource_type ASC, provider_id ASC
                """
            ).fetchall()
        return [_resource_from_row(row) for row in rows]

    def get_resource(self, resource_id: int) -> MonitoredResource | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM monitored_resources
                WHERE id = ?
                """,
                (resource_id,),
            ).fetchone()
        if row is None:
            return None
        return _resource_from_row(row)

    def insert_metric_snapshot(self, snapshot: MetricSnapshot) -> int:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO metric_snapshots (
                    resource_id,
                    resource_type,
                    captured_at,
                    cpu_percent,
                    ram_used_mb,
                    ram_total_mb,
                    ram_percent,
                    disk_used_gb,
                    disk_total_gb,
                    disk_percent,
                    bandwidth_bytes,
                    traffic_requests,
                    php_metric_json,
                    mysql_metric_json,
                    raw_payload_json,
                    collection_status,
                    error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.resource_id,
                    snapshot.resource_type,
                    _format_datetime(snapshot.captured_at),
                    snapshot.cpu_percent,
                    snapshot.ram_used_mb,
                    snapshot.ram_total_mb,
                    snapshot.ram_percent,
                    snapshot.disk_used_gb,
                    snapshot.disk_total_gb,
                    snapshot.disk_percent,
                    snapshot.bandwidth_bytes,
                    snapshot.traffic_requests,
                    _json(snapshot.php_metric),
                    _json(snapshot.mysql_metric),
                    _json(snapshot.raw_payload),
                    snapshot.collection_status,
                    snapshot.error_code,
                ),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("metric snapshot insert did not return an id")
        return cursor.lastrowid

    def list_metric_snapshots(
        self,
        *,
        resource_id: int,
        start: datetime,
        end: datetime,
    ) -> list[MetricSnapshot]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM metric_snapshots
                WHERE resource_id = ?
                    AND captured_at >= ?
                    AND captured_at <= ?
                ORDER BY captured_at ASC
                """,
                (resource_id, _format_datetime(start), _format_datetime(end)),
            ).fetchall()
        return [_snapshot_from_row(row) for row in rows]

    def get_latest_metric_snapshot(self, resource_id: int) -> MetricSnapshot | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM metric_snapshots
                WHERE resource_id = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (resource_id,),
            ).fetchone()
        if row is None:
            return None
        return _snapshot_from_row(row)

    def expire_metric_snapshots(self, *, older_than: datetime) -> int:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM metric_snapshots
                WHERE captured_at < ?
                """,
                (_format_datetime(older_than),),
            )
        return cursor.rowcount

    def get_alert_state(self, *, resource_id: int, rule_key: str) -> AlertState | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM alert_states
                WHERE resource_id = ? AND rule_key = ?
                """,
                (resource_id, rule_key),
            ).fetchone()
        if row is None:
            return None
        return _alert_state_from_row(row)

    def save_alert_state(
        self,
        *,
        resource_id: int,
        rule_key: str,
        status: str,
        severity: str | None,
        consecutive_breaches: int,
        opened_at: datetime | None,
        resolved_at: datetime | None,
        last_notification_at: datetime | None,
    ) -> AlertState:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO alert_states (
                    resource_id,
                    rule_key,
                    status,
                    severity,
                    consecutive_breaches,
                    opened_at,
                    resolved_at,
                    last_notification_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_id, rule_key) DO UPDATE SET
                    status = excluded.status,
                    severity = excluded.severity,
                    consecutive_breaches = excluded.consecutive_breaches,
                    opened_at = excluded.opened_at,
                    resolved_at = excluded.resolved_at,
                    last_notification_at = excluded.last_notification_at
                """,
                (
                    resource_id,
                    rule_key,
                    status,
                    severity,
                    consecutive_breaches,
                    _format_optional_datetime(opened_at),
                    _format_optional_datetime(resolved_at),
                    _format_optional_datetime(last_notification_at),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM alert_states
                WHERE resource_id = ? AND rule_key = ?
                """,
                (resource_id, rule_key),
            ).fetchone()
        return _alert_state_from_row(row)

    def list_alert_states(self, *, status: str | None = None) -> list[AlertState]:
        query = "SELECT * FROM alert_states"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY resource_id ASC, rule_key ASC"
        with self._database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_alert_state_from_row(row) for row in rows]

    def insert_alert_event(
        self,
        *,
        resource_id: int,
        rule_key: str,
        event_type: str,
        severity: str | None,
        message: str,
        created_at: datetime,
    ) -> AlertEvent:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO alert_events (
                    resource_id,
                    rule_key,
                    event_type,
                    severity,
                    message,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    resource_id,
                    rule_key,
                    event_type,
                    severity,
                    message,
                    _format_datetime(created_at),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM alert_events
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return _alert_event_from_row(row)

    def list_alert_events(self, *, limit: int = 100) -> list[AlertEvent]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM alert_events
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_alert_event_from_row(row) for row in rows]


def _resource_from_row(row: sqlite3.Row) -> MonitoredResource:
    return MonitoredResource(
        id=int(row["id"]),
        provider_id=str(row["provider_id"]),
        resource_type=cast(ResourceType, row["resource_type"]),
        name=str(row["name"]),
        parent_provider_id=row["parent_provider_id"],
        raw=json.loads(row["raw_json"]),
        discovered_at=datetime.fromisoformat(row["discovered_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _alert_state_from_row(row: sqlite3.Row) -> AlertState:
    return AlertState(
        id=int(row["id"]),
        resource_id=int(row["resource_id"]),
        rule_key=str(row["rule_key"]),
        status=str(row["status"]),
        severity=row["severity"],
        consecutive_breaches=int(row["consecutive_breaches"]),
        opened_at=_parse_optional_datetime(row["opened_at"]),
        resolved_at=_parse_optional_datetime(row["resolved_at"]),
        last_notification_at=_parse_optional_datetime(row["last_notification_at"]),
    )


def _alert_event_from_row(row: sqlite3.Row) -> AlertEvent:
    return AlertEvent(
        id=int(row["id"]),
        resource_id=int(row["resource_id"]),
        rule_key=str(row["rule_key"]),
        event_type=str(row["event_type"]),
        severity=row["severity"],
        message=str(row["message"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _snapshot_from_row(row: sqlite3.Row) -> MetricSnapshot:
    return MetricSnapshot(
        resource_id=int(row["resource_id"]),
        resource_type=row["resource_type"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
        cpu_percent=row["cpu_percent"],
        ram_used_mb=row["ram_used_mb"],
        ram_total_mb=row["ram_total_mb"],
        ram_percent=row["ram_percent"],
        disk_used_gb=row["disk_used_gb"],
        disk_total_gb=row["disk_total_gb"],
        disk_percent=row["disk_percent"],
        bandwidth_bytes=row["bandwidth_bytes"],
        traffic_requests=row["traffic_requests"],
        php_metric=json.loads(row["php_metric_json"] or "{}"),
        mysql_metric=json.loads(row["mysql_metric_json"] or "{}"),
        raw_payload=json.loads(row["raw_payload_json"]),
        collection_status=row["collection_status"],
        error_code=row["error_code"],
    )


def _format_datetime(value: datetime) -> str:
    return value.isoformat()


def _format_optional_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _format_datetime(value)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
