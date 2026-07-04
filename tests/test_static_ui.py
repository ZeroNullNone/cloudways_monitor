from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from tests.helpers import valid_env


def test_serves_built_frontend_without_swallowing_api_routes(tmp_path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<!doctype html><div id=\"root\">Cloudways Monitor</div>",
        encoding="utf-8",
    )
    settings = Settings.from_env(
        valid_env(SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"))
    )
    client = TestClient(create_app(settings=settings, static_dir=static_dir))

    root = client.get("/")
    browser_route = client.get("/servers/server-1")
    health = client.get("/health")
    missing_api = client.get("/api/not-found")

    assert root.status_code == 200
    assert "Cloudways Monitor" in root.text
    assert browser_route.status_code == 200
    assert "Cloudways Monitor" in browser_route.text
    assert health.json() == {
        "status": "ok",
        "service": "cloudways-monitor",
    }
    assert missing_api.status_code == 404
