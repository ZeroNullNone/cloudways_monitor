from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, MetricSnapshot, ResourceType, Storage
from tests.helpers import login, valid_env


def test_raw_latest_api_returns_latest_snapshot_debug_payload(tmp_path) -> None:
    now = datetime(2026, 7, 5, 8, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"))
    )
    storage = make_storage(settings)
    resource_id = storage.upsert_resource(
        provider_id="987",
        resource_type="application",
        name="storefront",
        parent_provider_id="123",
        raw={"id": 987, "app_label": "storefront"},
        discovered_at=now,
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=resource_id,
            resource_type="application",
            captured_at=now - timedelta(minutes=10),
            raw_payload={"old": True},
            collection_status="ok",
        )
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=resource_id,
            resource_type="application",
            captured_at=now - timedelta(minutes=1),
            php_metric={"version": "8.2", "workers": 4},
            mysql_metric={"threads": 12},
            raw_payload={"id": 987, "disk": {"used_gb": 3.5}, "traffic": 120},
            collection_status="error",
            error_code="cloudways_timeout",
        )
    )
    client = TestClient(
        create_app(settings=settings, storage=storage, clock=lambda: now),
        base_url="https://testserver",
    )
    login(client)

    response = client.get(f"/api/resources/{resource_id}/raw/latest")

    assert response.status_code == 200
    assert response.json() == {
        "resource": {
            "id": resource_id,
            "provider_id": "987",
            "resource_type": "application",
            "name": "storefront",
            "parent_provider_id": "123",
        },
        "snapshot": {
            "captured_at": (now - timedelta(minutes=1)).isoformat(),
            "collection_status": "error",
            "error_code": "cloudways_timeout",
            "php_metric": {"version": "8.2", "workers": 4},
            "mysql_metric": {"threads": 12},
            "raw_payload": {
                "id": 987,
                "disk": {"used_gb": 3.5},
                "traffic": 120,
            },
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
    php_metric: dict[str, object] | None = None,
    mysql_metric: dict[str, object] | None = None,
    raw_payload: dict[str, object] | None = None,
    collection_status: str,
    error_code: str | None = None,
) -> MetricSnapshot:
    return MetricSnapshot(
        resource_id=resource_id,
        resource_type=cast(ResourceType, resource_type),
        captured_at=captured_at,
        cpu_percent=None,
        ram_used_mb=None,
        ram_total_mb=None,
        ram_percent=None,
        disk_used_gb=None,
        disk_total_gb=None,
        disk_percent=None,
        bandwidth_bytes=None,
        traffic_requests=None,
        php_metric=php_metric or {},
        mysql_metric=mysql_metric or {},
        raw_payload=raw_payload or {},
        collection_status=collection_status,
        error_code=error_code,
    )
