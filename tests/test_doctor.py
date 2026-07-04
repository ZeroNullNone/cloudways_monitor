from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from tests.helpers import valid_env


def test_doctor_reports_config_and_sqlite_readiness(tmp_path) -> None:
    sqlite_path = tmp_path / "cloudways-monitor.sqlite3"
    settings = Settings.from_env(valid_env(SQLITE_PATH=str(sqlite_path)))
    client = TestClient(create_app(settings=settings))

    response = client.get("/api/doctor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["config"]["status"] == "ok"
    assert payload["checks"]["sqlite"] == {
        "status": "ok",
        "path": str(sqlite_path),
        "writable": True,
    }
    assert sqlite_path.exists()
    assert "cloudways-key" not in str(payload)
    assert "telegram-token" not in str(payload)
    assert "a-session-secret" not in str(payload)
