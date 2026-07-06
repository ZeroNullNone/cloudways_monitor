from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, MetricSnapshot, ResourceType, Storage
from tests.helpers import login, valid_env


def test_series_api_returns_chronological_points_for_selected_range(tmp_path) -> None:
    now = datetime(2026, 7, 5, 8, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"))
    )
    storage = make_storage(settings)
    resource_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production",
        parent_provider_id=None,
        raw={"id": 123, "label": "production"},
        discovered_at=now,
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=resource_id,
            resource_type="server",
            captured_at=now - timedelta(hours=2),
            cpu_percent=99.0,
            ram_percent=88.0,
            disk_percent=77.0,
        )
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=resource_id,
            resource_type="server",
            captured_at=now - timedelta(minutes=45),
            cpu_percent=41.0,
            ram_percent=51.0,
            disk_percent=61.0,
            bandwidth_bytes=1024,
        )
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=resource_id,
            resource_type="server",
            captured_at=now - timedelta(minutes=10),
            cpu_percent=44.0,
            ram_percent=54.0,
            disk_percent=64.0,
            bandwidth_bytes=4096,
        )
    )
    client = TestClient(
        create_app(settings=settings, storage=storage, clock=lambda: now),
        base_url="https://testserver",
    )
    login(client)

    response = client.get(f"/api/resources/{resource_id}/series?range=1h")

    assert response.status_code == 200
    assert response.json() == {
        "resource": {
            "id": resource_id,
            "provider_id": "123",
            "resource_type": "server",
            "name": "production",
            "parent_provider_id": None,
        },
        "range": {
            "key": "1h",
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": now.isoformat(),
        },
        "points": [
            {
                "captured_at": (now - timedelta(minutes=45)).isoformat(),
                "cpu_percent": 41.0,
                "ram_percent": 51.0,
                "ram_used_mb": None,
                "ram_total_mb": None,
                "disk_used_gb": None,
                "disk_total_gb": None,
                "disk_percent": 61.0,
                "bandwidth_bytes": 1024,
                "traffic_requests": None,
                "collection_status": "ok",
                "error_code": None,
            },
            {
                "captured_at": (now - timedelta(minutes=10)).isoformat(),
                "cpu_percent": 44.0,
                "ram_percent": 54.0,
                "ram_used_mb": None,
                "ram_total_mb": None,
                "disk_used_gb": None,
                "disk_total_gb": None,
                "disk_percent": 64.0,
                "bandwidth_bytes": 4096,
                "traffic_requests": None,
                "collection_status": "ok",
                "error_code": None,
            },
        ],
    }


def make_storage(settings: Settings) -> Storage:
    database = Database(settings.sqlite_path)
    database.migrate()
    return Storage(database)


def make_snapshot(
    *,
    resource_id: int,
    resource_type: str,
    captured_at: datetime,
    cpu_percent: float | None = None,
    ram_percent: float | None = None,
    disk_percent: float | None = None,
    bandwidth_bytes: int | None = None,
    traffic_requests: int | None = None,
) -> MetricSnapshot:
    return MetricSnapshot(
        resource_id=resource_id,
        resource_type=cast(ResourceType, resource_type),
        captured_at=captured_at,
        cpu_percent=cpu_percent,
        ram_used_mb=None,
        ram_total_mb=None,
        ram_percent=ram_percent,
        disk_used_gb=None,
        disk_total_gb=None,
        disk_percent=disk_percent,
        bandwidth_bytes=bandwidth_bytes,
        traffic_requests=traffic_requests,
        php_metric={},
        mysql_metric={},
        raw_payload={},
        collection_status="ok",
        error_code=None,
    )
