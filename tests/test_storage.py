import sqlite3
from datetime import UTC, datetime

from cloudways_monitor.storage import Database, MetricSnapshot, Storage


def table_names(sqlite_path) -> set[str]:
    with sqlite3.connect(sqlite_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}


def test_migrate_creates_v1_tables_idempotently(tmp_path) -> None:
    sqlite_path = tmp_path / "cloudways-monitor.sqlite3"
    database = Database(sqlite_path)

    database.migrate()
    database.migrate()

    assert {
        "monitored_resources",
        "metric_snapshots",
        "collector_runs",
        "alert_states",
        "alert_events",
    }.issubset(table_names(sqlite_path))


def test_storage_upserts_resources_and_queries_metric_snapshots(tmp_path) -> None:
    sqlite_path = tmp_path / "cloudways-monitor.sqlite3"
    database = Database(sqlite_path)
    database.migrate()
    storage = Storage(database)

    resource_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production",
        parent_provider_id=None,
        raw={"id": 123, "label": "production"},
        discovered_at=datetime(2026, 7, 5, 1, 0, tzinfo=UTC),
    )
    updated_resource_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production-renamed",
        parent_provider_id=None,
        raw={"id": 123, "label": "production-renamed"},
        discovered_at=datetime(2026, 7, 5, 1, 1, tzinfo=UTC),
    )

    assert updated_resource_id == resource_id

    storage.insert_metric_snapshot(
        MetricSnapshot(
            resource_id=resource_id,
            resource_type="server",
            captured_at=datetime(2026, 7, 5, 1, 2, tzinfo=UTC),
            cpu_percent=41.5,
            ram_used_mb=1024.0,
            ram_total_mb=2048.0,
            ram_percent=50.0,
            disk_used_gb=18.0,
            disk_total_gb=40.0,
            disk_percent=45.0,
            bandwidth_bytes=123456,
            traffic_requests=789,
            php_metric={},
            mysql_metric={},
            raw_payload={"cpu": 41.5},
            collection_status="ok",
            error_code=None,
        )
    )

    snapshots = storage.list_metric_snapshots(
        resource_id=resource_id,
        start=datetime(2026, 7, 5, 1, 0, tzinfo=UTC),
        end=datetime(2026, 7, 5, 2, 0, tzinfo=UTC),
    )

    assert len(snapshots) == 1
    assert snapshots[0].cpu_percent == 41.5
    assert snapshots[0].raw_payload == {"cpu": 41.5}

def make_snapshot(resource_id: int, captured_at: datetime, cpu: float) -> MetricSnapshot:
    return MetricSnapshot(
        resource_id=resource_id,
        resource_type="server",
        captured_at=captured_at,
        cpu_percent=cpu,
        ram_used_mb=None,
        ram_total_mb=None,
        ram_percent=None,
        disk_used_gb=None,
        disk_total_gb=None,
        disk_percent=None,
        bandwidth_bytes=None,
        traffic_requests=None,
        php_metric={},
        mysql_metric={},
        raw_payload={"cpu": cpu},
        collection_status="ok",
        error_code=None,
    )


def test_storage_returns_latest_snapshot_and_expires_old_snapshots(tmp_path) -> None:
    sqlite_path = tmp_path / "cloudways-monitor.sqlite3"
    database = Database(sqlite_path)
    database.migrate()
    storage = Storage(database)
    resource_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production",
        parent_provider_id=None,
        raw={"id": 123},
        discovered_at=datetime(2026, 7, 5, 1, 0, tzinfo=UTC),
    )
    old_snapshot = make_snapshot(
        resource_id,
        datetime(2026, 6, 1, 1, 0, tzinfo=UTC),
        11.0,
    )
    current_snapshot = make_snapshot(
        resource_id,
        datetime(2026, 7, 5, 1, 0, tzinfo=UTC),
        77.0,
    )
    storage.insert_metric_snapshot(old_snapshot)
    storage.insert_metric_snapshot(current_snapshot)

    latest = storage.get_latest_metric_snapshot(resource_id)
    deleted = storage.expire_metric_snapshots(
        older_than=datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    )
    remaining = storage.list_metric_snapshots(
        resource_id=resource_id,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert latest is not None
    assert latest.cpu_percent == 77.0
    assert deleted == 1
    assert [snapshot.cpu_percent for snapshot in remaining] == [77.0]
