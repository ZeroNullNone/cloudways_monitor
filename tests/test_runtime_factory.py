from __future__ import annotations

from time import monotonic, sleep
from typing import Any

from fastapi.testclient import TestClient

import cloudways_monitor.app as app_module
from cloudways_monitor.settings import Settings
from tests.helpers import login, valid_env


def _wait_for_resource_counts(
    client: TestClient,
    *,
    expected_servers: int,
    expected_applications: int,
    timeout_seconds: float = 2.0,
) -> tuple[Any, Any]:
    deadline = monotonic() + timeout_seconds
    resources_response = None
    health_response = None

    while monotonic() < deadline:
        resources_response = client.get("/api/resources")
        health_response = client.get("/api/collector/health")
        if resources_response.status_code == 200 and health_response.status_code == 200:
            resources = resources_response.json()["resources"]
            server_count = len(
                [item for item in resources if item["resource_type"] == "server"]
            )
            application_count = len(
                [item for item in resources if item["resource_type"] == "application"]
            )
            if (
                server_count == expected_servers
                and application_count == expected_applications
            ):
                return resources_response, health_response
        sleep(0.01)

    if resources_response is None or health_response is None:
        raise AssertionError("resource endpoint was not queried")
    return resources_response, health_response


class RuntimeFakeCloudwaysClient:
    def authenticate(self) -> str:
        return "token-123"

    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {
                "provider_id": server_id,
                "resource_type": "server",
                "name": f"server-{server_id}",
                "parent_provider_id": None,
                "raw": {"id": server_id},
            }
            for server_id in ("687506", "706980", "794126", "1366047")
        ]

    def list_applications(self) -> list[dict[str, Any]]:
        return [
            {
                "provider_id": f"{server_id}-{index}",
                "resource_type": "application",
                "name": f"app-{server_id}-{index}",
                "parent_provider_id": server_id,
                "raw": {"id": f"{server_id}-{index}", "server_id": server_id},
            }
            for server_id, count in {
                "687506": 6,
                "706980": 5,
                "794126": 6,
                "1366047": 2,
            }.items()
            for index in range(count)
        ]

    def get_server_metrics(self, server_id: str) -> dict[str, Any]:
        return {}

    def get_application_metrics(
        self,
        application_id: str,
        server_id: str | None,
    ) -> dict[str, Any]:
        return {}


def test_production_factory_does_not_block_startup_on_collector_run_once(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="",
            MONITORED_APP_IDS="",
        )
    )
    collectors: list[Any] = []

    class StartupFakeCollector:
        def __init__(self, **kwargs: Any) -> None:
            self.started = False
            self.start_run_immediately: bool | None = None
            collectors.append(self)

        def run_once(self) -> None:
            raise AssertionError("collector.run_once must not block app startup")

        def start(self, *, run_immediately: bool = True) -> None:
            self.started = True
            self.start_run_immediately = run_immediately

        def stop(self) -> None:
            pass

    monkeypatch.setattr(
        app_module.Settings,
        "from_env",
        classmethod(lambda cls, env=None: settings),
    )
    monkeypatch.setattr(
        app_module,
        "CloudwaysClient",
        lambda resolved_settings: RuntimeFakeCloudwaysClient(),
    )
    monkeypatch.setattr(app_module, "TelemetryCollector", StartupFakeCollector)

    with TestClient(app_module.create_app(), base_url="https://testserver") as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert collectors[0].started is True
    assert collectors[0].start_run_immediately is True


def test_production_factory_starts_collector_and_syncs_discovered_resources(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="",
            MONITORED_APP_IDS="",
        )
    )
    monkeypatch.setattr(
        app_module.Settings,
        "from_env",
        classmethod(lambda cls, env=None: settings),
    )
    monkeypatch.setattr(
        app_module,
        "CloudwaysClient",
        lambda resolved_settings: RuntimeFakeCloudwaysClient(),
    )

    with TestClient(app_module.create_app(), base_url="https://testserver") as client:
        login(client)

        resources_response, health_response = _wait_for_resource_counts(
            client,
            expected_servers=4,
            expected_applications=19,
        )

    assert resources_response.status_code == 200
    resources = resources_response.json()["resources"]
    assert len([item for item in resources if item["resource_type"] == "server"]) == 4
    assert (
        len([item for item in resources if item["resource_type"] == "application"])
        == 19
    )
    assert health_response.status_code == 200
    assert health_response.json()["servers_discovered"] == 4
    assert health_response.json()["applications_discovered"] == 19
