from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, MetricSnapshot, ResourceType, Storage
from tests.helpers import login, valid_env


def test_resources_api_lists_resources_and_application_parent_context(tmp_path) -> None:
    now = datetime(2026, 7, 5, 8, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"))
    )
    storage = make_storage(settings)
    server_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production",
        parent_provider_id=None,
        raw={"id": 123, "label": "production"},
        discovered_at=now,
    )
    app_id = storage.upsert_resource(
        provider_id="987",
        resource_type="application",
        name="storefront",
        parent_provider_id="123",
        raw={"id": 987, "app_label": "storefront"},
        discovered_at=now,
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=server_id,
            resource_type="server",
            captured_at=now - timedelta(seconds=30),
            cpu_percent=47.0,
            ram_percent=52.0,
            disk_percent=66.0,
        )
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=app_id,
            resource_type="application",
            captured_at=now - timedelta(seconds=40),
            disk_percent=4.0,
            traffic_requests=220,
        )
    )
    client = TestClient(
        create_app(settings=settings, storage=storage, clock=lambda: now),
        base_url="https://testserver",
    )
    login(client)

    resources_response = client.get("/api/resources")
    detail_response = client.get(f"/api/resources/{app_id}")

    assert resources_response.status_code == 200
    assert resources_response.json() == {
        "resources": [
            {
                "id": server_id,
                "provider_id": "123",
                "resource_type": "server",
                "name": "production",
                "parent_provider_id": None,
                "latest": {
                    "captured_at": (now - timedelta(seconds=30)).isoformat(),
                    "stale": False,
                    "cpu_percent": 47.0,
                    "ram_percent": 52.0,
                    "disk_percent": 66.0,
                    "bandwidth_bytes": None,
                    "traffic_requests": None,
                },
                "alerts": [],
            },
            {
                "id": app_id,
                "provider_id": "987",
                "resource_type": "application",
                "name": "storefront",
                "parent_provider_id": "123",
                "latest": {
                    "captured_at": (now - timedelta(seconds=40)).isoformat(),
                    "stale": False,
                    "cpu_percent": None,
                    "ram_percent": None,
                    "disk_percent": 4.0,
                    "bandwidth_bytes": None,
                    "traffic_requests": 220,
                },
                "alerts": [],
            },
        ]
    }
    assert detail_response.status_code == 200
    assert detail_response.json() == {
        "resource": {
            "id": app_id,
            "provider_id": "987",
            "resource_type": "application",
            "name": "storefront",
            "parent_provider_id": "123",
            "latest": {
                "captured_at": (now - timedelta(seconds=40)).isoformat(),
                "stale": False,
                "cpu_percent": None,
                "ram_percent": None,
                "disk_percent": 4.0,
                "bandwidth_bytes": None,
                "traffic_requests": 220,
            },
            "alerts": [],
        },
        "parent_server": {
            "id": server_id,
            "provider_id": "123",
            "resource_type": "server",
            "name": "production",
            "parent_provider_id": None,
            "latest": {
                "captured_at": (now - timedelta(seconds=30)).isoformat(),
                "stale": False,
                "cpu_percent": 47.0,
                "ram_percent": 52.0,
                "disk_percent": 66.0,
                "bandwidth_bytes": None,
                "traffic_requests": None,
            },
            "alerts": [],
        },
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
