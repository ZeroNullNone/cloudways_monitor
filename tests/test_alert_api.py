from datetime import UTC, datetime

from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, Storage
from tests.helpers import login, valid_env


def test_alert_api_exposes_current_states_and_events(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
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
    state = storage.save_alert_state(
        resource_id=resource_id,
        rule_key="cpu_percent",
        status="active",
        severity="critical",
        consecutive_breaches=3,
        opened_at=now,
        resolved_at=None,
        last_notification_at=now,
    )
    event = storage.insert_alert_event(
        resource_id=resource_id,
        rule_key="cpu_percent",
        event_type="opened",
        severity="critical",
        message="production critical cpu_percent 98.0 >= 95.0 for 3 polls",
        created_at=now,
    )
    client = TestClient(
        create_app(settings=settings, storage=storage), base_url="https://testserver"
    )
    login(client)

    alerts_response = client.get("/api/alerts")
    events_response = client.get("/api/alerts/events")

    assert alerts_response.status_code == 200
    assert alerts_response.json() == {
        "alerts": [
            {
                "id": state.id,
                "resource_id": resource_id,
                "rule_key": "cpu_percent",
                "status": "active",
                "severity": "critical",
                "consecutive_breaches": 3,
                "opened_at": now.isoformat(),
                "resolved_at": None,
                "last_notification_at": now.isoformat(),
            }
        ]
    }
    assert events_response.status_code == 200
    assert events_response.json() == {
        "events": [
            {
                "id": event.id,
                "resource_id": resource_id,
                "rule_key": "cpu_percent",
                "event_type": "opened",
                "severity": "critical",
                "message": ("production critical cpu_percent 98.0 >= 95.0 for 3 polls"),
                "created_at": now.isoformat(),
            }
        ]
    }


def test_alert_api_uses_configured_storage_when_not_injected(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
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
    storage.save_alert_state(
        resource_id=resource_id,
        rule_key="cpu_percent",
        status="active",
        severity="critical",
        consecutive_breaches=3,
        opened_at=now,
        resolved_at=None,
        last_notification_at=now,
    )
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    login(client)

    response = client.get("/api/alerts")

    assert response.status_code == 200
    assert response.json()["alerts"][0]["rule_key"] == "cpu_percent"


def make_storage(settings: Settings) -> Storage:
    database = Database(settings.sqlite_path)
    database.migrate()
    return Storage(database)
