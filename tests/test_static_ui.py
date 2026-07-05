from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from tests.helpers import login, valid_env


def test_serves_built_frontend_without_swallowing_api_routes(tmp_path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        '<!doctype html><div id="root">Cloudways Monitor</div>',
        encoding="utf-8",
    )
    settings = Settings.from_env(
        valid_env(SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"))
    )
    client = TestClient(
        create_app(settings=settings, static_dir=static_dir),
        base_url="https://testserver",
    )

    root = client.get("/", follow_redirects=False)
    login_page = client.get("/login")
    browser_route = client.get("/servers/server-1", follow_redirects=False)
    health = client.get("/health")
    missing_api = client.get("/api/not-found")
    login(client)
    authenticated_browser_route = client.get("/servers/server-1")

    assert root.status_code == 307
    assert root.headers["location"] == "/login"
    assert login_page.status_code == 200
    assert "Cloudways Monitor" in login_page.text
    assert browser_route.status_code == 307
    assert browser_route.headers["location"] == "/login"
    assert authenticated_browser_route.status_code == 200
    assert "Cloudways Monitor" in authenticated_browser_route.text
    assert health.json() == {
        "status": "ok",
        "service": "cloudways-monitor",
    }
    assert missing_api.status_code == 404
