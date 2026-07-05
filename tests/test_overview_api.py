from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, MetricSnapshot, Storage
from tests.helpers import login, valid_env


def test_overview_api_prioritizes_alerts_and_groups_applications(tmp_path) -> None:
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
            cpu_percent=91.0,
            ram_percent=72.0,
            disk_percent=61.0,
            bandwidth_bytes=2048,
        )
    )
    storage.insert_metric_snapshot(
        make_snapshot(
            resource_id=app_id,
            resource_type="application",
            captured_at=now - timedelta(minutes=4),
            disk_percent=0.0,
            traffic_requests=120,
            bandwidth_bytes=4096,
        )
    )
    alert = storage.save_alert_state(
        resource_id=server_id,
        rule_key="cpu_percent",
        status="active",
        severity="warning",
        consecutive_breaches=3,
        opened_at=now - timedelta(minutes=2),
        resolved_at=None,
        last_notification_at=now - timedelta(minutes=2),
    )
    client = TestClient(
        create_app(settings=settings, storage=storage, clock=lambda: now),
        base_url="https://testserver",
    )
    login(client)

    response = client.get("/api/overview")

    assert response.status_code == 200
    assert response.json() == {
        "attention": {
            "status": "needs_attention",
            "active_alert_count": 1,
            "stale_resource_count": 1,
        },
        "collector": {
            "status": "never_run",
            "last_run_at": None,
            "last_success_at": None,
            "servers_discovered": 0,
            "applications_discovered": 0,
            "snapshots_stored": 0,
            "snapshots_expired": 0,
            "stale": True,
            "last_error_code": None,
            "last_error": None,
        },
        "active_alerts": [
            {
                "id": alert.id,
                "resource_id": server_id,
                "resource_name": "production",
                "resource_type": "server",
                "rule_key": "cpu_percent",
                "severity": "warning",
                "status": "active",
                "consecutive_breaches": 3,
                "opened_at": (now - timedelta(minutes=2)).isoformat(),
                "last_notification_at": (now - timedelta(minutes=2)).isoformat(),
            }
        ],
        "servers": [
            {
                "id": server_id,
                "provider_id": "123",
                "resource_type": "server",
                "name": "production",
                "parent_provider_id": None,
                "latest": {
                    "captured_at": (now - timedelta(seconds=30)).isoformat(),
                    "stale": False,
                    "cpu_percent": 91.0,
                    "ram_percent": 72.0,
                    "disk_percent": 61.0,
                    "bandwidth_bytes": 2048,
                    "traffic_requests": None,
                },
                "alerts": [
                    {
                        "id": alert.id,
                        "rule_key": "cpu_percent",
                        "severity": "warning",
                        "status": "active",
                    }
                ],
                "applications": [
                    {
                        "id": app_id,
                        "provider_id": "987",
                        "resource_type": "application",
                        "name": "storefront",
                        "parent_provider_id": "123",
                        "latest": {
                            "captured_at": (now - timedelta(minutes=4)).isoformat(),
                            "stale": True,
                            "cpu_percent": None,
                            "ram_percent": None,
                            "disk_percent": 0.0,
                            "bandwidth_bytes": 4096,
                            "traffic_requests": 120,
                        },
                        "alerts": [],
                    }
                ],
            }
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
        resource_type=resource_type,  # type: ignore[arg-type]
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
