from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from tests.helpers import login, valid_env


def test_events_api_streams_dashboard_refresh_event(tmp_path) -> None:
    settings = Settings.from_env(
        valid_env(SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"))
    )
    client = TestClient(
        create_app(settings=settings),
        base_url="https://testserver",
    )
    login(client)

    response = client.get("/api/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == (
        "event: dashboard-refresh\n"
        'data: {"type":"dashboard-refresh"}\n'
        "\n"
    )
