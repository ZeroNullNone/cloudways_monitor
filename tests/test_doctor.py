from fastapi.testclient import TestClient

from cloudways_monitor.app import create_app
from cloudways_monitor.cloudways import CloudwaysApiError
from cloudways_monitor.settings import Settings
from tests.helpers import login, valid_env


def test_doctor_reports_config_and_sqlite_readiness(tmp_path) -> None:
    sqlite_path = tmp_path / "cloudways-monitor.sqlite3"
    settings = Settings.from_env(valid_env(SQLITE_PATH=str(sqlite_path)))
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    login(client)

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


class FakeCloudwaysClient:
    def __init__(self) -> None:
        self.authenticated = False
        self.discovery_checked = False

    def authenticate(self) -> str:
        self.authenticated = True
        return "token-123"

    def list_servers(self) -> list[dict[str, object]]:
        self.discovery_checked = True
        return [
            {
                "provider_id": "123",
                "resource_type": "server",
                "name": "production",
                "parent_provider_id": None,
                "raw": {},
            }
        ]

    def list_applications(self) -> list[dict[str, object]]:
        return [
            {
                "provider_id": "987",
                "resource_type": "application",
                "name": "storefront",
                "parent_provider_id": "123",
                "raw": {},
            }
        ]


class FailingCloudwaysClient:
    def authenticate(self) -> str:
        raise CloudwaysApiError(
            "Cloudways API returned HTTP 401",
            status_code=401,
            code="authentication_error",
            detail={"message": "invalid token"},
        )

    def list_servers(self) -> list[dict[str, object]]:
        raise AssertionError("list_servers should not run after auth failure")

    def list_applications(self) -> list[dict[str, object]]:
        raise AssertionError("list_applications should not run after auth failure")


def test_doctor_reports_cloudways_readiness(tmp_path) -> None:
    sqlite_path = tmp_path / "cloudways-monitor.sqlite3"
    settings = Settings.from_env(valid_env(SQLITE_PATH=str(sqlite_path)))
    cloudways_client = FakeCloudwaysClient()
    client = TestClient(
        create_app(settings=settings, cloudways_client=cloudways_client),
        base_url="https://testserver",
    )
    login(client)

    response = client.get("/api/doctor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["cloudways"] == {
        "status": "ok",
        "authenticated": True,
        "servers_discovered": 1,
        "applications_discovered": 1,
    }
    assert cloudways_client.authenticated is True
    assert cloudways_client.discovery_checked is True
    assert "cloudways-key" not in str(payload)


def test_doctor_reports_cloudways_failure_without_secrets(tmp_path) -> None:
    sqlite_path = tmp_path / "cloudways-monitor.sqlite3"
    settings = Settings.from_env(valid_env(SQLITE_PATH=str(sqlite_path)))
    client = TestClient(
        create_app(settings=settings, cloudways_client=FailingCloudwaysClient()),
        base_url="https://testserver",
    )
    login(client)

    response = client.get("/api/doctor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["checks"]["cloudways"] == {
        "status": "error",
        "authenticated": False,
        "error_code": "authentication_error",
        "status_code": 401,
    }
    assert "cloudways-key" not in str(payload)
