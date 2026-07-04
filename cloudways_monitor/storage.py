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
                """
            )


class Storage:
    def __init__(self, database: Database) -> None:
        self._database = database

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


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
