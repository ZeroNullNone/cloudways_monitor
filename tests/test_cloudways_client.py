import httpx
import pytest

from cloudways_monitor.cloudways import CloudwaysApiError, CloudwaysClient
from cloudways_monitor.settings import Settings
from tests.helpers import valid_env


API_BASE_URL = "https://api.cloudways.com/api/v2"


def make_settings(**overrides: str) -> Settings:
    return Settings.from_env(valid_env(**overrides))


def test_authenticate_gets_and_caches_oauth_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v2/oauth/access_token"
        assert b"grant_type=password" in request.content
        return httpx.Response(200, json={"access_token": "token-123"})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    first_token = client.authenticate()
    second_token = client.authenticate()

    assert first_token == "token-123"
    assert second_token == "token-123"
    assert len(requests) == 1


def test_list_servers_and_applications_return_normalized_resources() -> None:
    application_payload = {
        "id": 987,
        "server_id": 123,
        "label": "storefront",
        "application": "wordpress",
        "app_version": "6.5",
    }
    server_payload = {
        "id": 123,
        "label": "production",
        "provider": "do",
        "region": "Singapore",
        "apps": [application_payload],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v2/server":
            return httpx.Response(200, json={"servers": [server_payload]})
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    servers = client.list_servers()
    applications = client.list_applications()

    assert servers == [
        {
            "provider_id": "123",
            "resource_type": "server",
            "name": "production",
            "parent_provider_id": None,
            "raw": server_payload,
        }
    ]
    assert applications == [
        {
            "provider_id": "987",
            "resource_type": "application",
            "name": "storefront",
            "parent_provider_id": "123",
            "raw": application_payload,
        }
    ]


def test_discovery_redacts_sensitive_raw_fields() -> None:
    server_payload = {
        "id": 123,
        "label": "production",
        "master_password": "secret-master-password",
        "apps": [
            {
                "id": 987,
                "server_id": 123,
                "label": "storefront",
                "app_password": "secret-app-password",
                "sys_password": "secret-system-password",
                "mysql_password": "secret-mysql-password",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        if request.url.path == "/api/v2/server":
            return httpx.Response(200, json={"servers": [server_payload]})
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    servers = client.list_servers()
    applications = client.list_applications()
    serialized_resources = str({"servers": servers, "applications": applications})

    assert "secret-master-password" not in serialized_resources
    assert "secret-app-password" not in serialized_resources
    assert "secret-system-password" not in serialized_resources
    assert "secret-mysql-password" not in serialized_resources
    assert servers[0]["raw"]["master_password"] == "[redacted]"
    assert applications[0]["raw"]["mysql_password"] == "[redacted]"


def test_v2_application_discovery_uses_server_payload_apps() -> None:
    server_ids = ["100", "200", "300", "400"]
    app_counts_by_server = {
        "100": 5,
        "200": 5,
        "300": 5,
        "400": 4,
    }
    server_payload = [
        {
            "id": server_id,
            "label": f"server-{server_id}",
            "apps": [
                {
                    "id": f"{server_id}{app_index}",
                    "label": f"app-{server_id}-{app_index}",
                }
                for app_index in range(app_count)
            ],
        }
        for server_id, app_count in app_counts_by_server.items()
    ]
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v2/server":
            return httpx.Response(200, json={"status": True, "servers": server_payload})
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    servers = client.list_servers()
    applications = client.list_applications()

    assert len(servers) == 4
    assert [server["provider_id"] for server in servers] == server_ids
    assert len(applications) == 19
    assert {app["parent_provider_id"] for app in applications} == set(server_ids)
    assert "/api/v2/app" not in requested_paths


def test_get_server_metrics_fetches_v2_monitor_summaries() -> None:
    requested_summary_types: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        assert request.url.path == "/api/v2/server/monitor/summary"
        assert request.url.params["server_id"] == "687506"
        summary_type = request.url.params["type"]
        requested_summary_types.append(summary_type)
        if summary_type == "bw":
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "name": "bandwidth_monthly",
                            "datapoint": [2037.76, 1571202000],
                            "type": "bw",
                        }
                    ]
                },
            )
        if summary_type == "db":
            return httpx.Response(
                200,
                json={
                    "contents": [
                        {
                            "name": "storage_used",
                            "datapoint": [12.5, 25],
                            "type": "db",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected type {summary_type}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(
        make_settings(CLOUDWAYS_TASK_POLLING_ENABLED="false"),
        http_client=http_client,
    )

    metrics = client.get_server_metrics("687506")

    assert requested_summary_types == ["bw", "db"]
    assert metrics["bandwidth_bytes"] == 2038
    assert metrics["disk_used_gb"] == 12.5
    assert metrics["disk_total_gb"] == 25.0
    assert metrics["monitor_summary"]["bandwidth"]["content"][0]["type"] == "bw"
    assert metrics["monitor_summary"]["disk"]["contents"][0]["type"] == "db"


def test_get_application_metrics_fetches_v2_monitor_summaries() -> None:
    requested_params: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        assert request.url.path == "/api/v2/app/monitor/summary"
        requested_params.append(
            (
                request.url.params["server_id"],
                request.url.params["app_id"],
                request.url.params["type"],
            )
        )
        if request.url.params["type"] == "bw":
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "name": "app_bandwidth",
                            "datapoint": [4096, 1571202000],
                            "type": "bw",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "name": "app_storage",
                        "datapoint": [2.4, 25],
                        "type": "db",
                    }
                ]
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(
        make_settings(CLOUDWAYS_TASK_POLLING_ENABLED="false"),
        http_client=http_client,
    )

    metrics = client.get_application_metrics("2622071", "687506")

    assert requested_params == [
        ("687506", "2622071", "bw"),
        ("687506", "2622071", "db"),
    ]
    assert metrics["bandwidth_bytes"] == 4096
    assert metrics["disk_used_gb"] == 2.4
    assert metrics["disk_total_gb"] == 25.0

def test_get_application_metrics_parses_cloudways_object_summary_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        assert request.url.path == "/api/v2/app/monitor/summary"
        assert request.url.params["server_id"] == "1366047"
        assert request.url.params["app_id"] == "5032935"
        if request.url.params["type"] == "bw":
            return httpx.Response(
                200,
                json={
                    "content": {
                        "app_home": 1513700,
                        "app_mysql": 25554.21484032,
                        "total": 1539254.2148403,
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "content": [
                    {"name": "jobs", "size": 18064},
                    {"name": "map_city", "size": 4624},
                    {"name": "bookings", "size": 288},
                    {"name": "articles", "size": 288},
                    {"name": "log_booking_statuses", "size": 272},
                    {"name": "map_state", "size": 134},
                    {"name": "booking_payments", "size": 96},
                    {"name": "sessions", "size": 80},
                    {"name": "claims", "size": 80},
                    {"name": "transport_route_rates", "size": 80},
                ]
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(
        make_settings(CLOUDWAYS_TASK_POLLING_ENABLED="false"),
        http_client=http_client,
    )

    metrics = client.get_application_metrics("5032935", "1366047")

    assert metrics["bandwidth_bytes"] == 1_576_196_316
    assert metrics["disk_used_gb"] == pytest.approx(24006 / 1024 / 1024)
    assert "cpu_percent" not in metrics
    assert "ram_percent" not in metrics
    assert "traffic_requests" not in metrics

def test_get_server_metrics_polls_graph_tasks_for_cpu_ram_and_disk() -> None:
    server_payload = {
        "id": "1366047",
        "label": "Tian Tian Car",
        "cloud": "do",
        "instance_type": "1GB",
        "volume_size": "25",
    }
    requested_targets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v2/server":
            return httpx.Response(200, json={"servers": [server_payload]})
        if request.url.path == "/api/v2/server/monitor/summary":
            return httpx.Response(200, json={"content": []})
        if request.url.path == "/api/v2/server/monitor/detail":
            assert request.url.params["server_id"] == "1366047"
            assert request.url.params["duration"] == "1 Hour"
            assert request.url.params["timezone"] == "UTC"
            assert request.url.params["output_format"] == "json"
            assert request.url.params["strorge"] == "false"
            target = request.url.params["target"]
            requested_targets.append(target)
            return httpx.Response(
                200,
                json={"status": True, "task_id": f"task-{target}"},
            )
        if request.url.path == "/api/v2/operation/task-Idle CPU":
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "data": {"content": [{"datapoint": [87, 1720000000]}]},
                    }
                },
            )
        if request.url.path == "/api/v2/operation/task-Free memory":
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "data": {"content": [{"datapoint": [512, 1720000000]}]},
                    }
                },
            )
        if request.url.path == "/api/v2/operation/task-Free Disk":
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "data": {"content": [{"datapoint": [20, 1720000000]}]},
                    }
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    metrics = client.get_server_metrics("1366047")

    assert requested_targets == ["Idle CPU", "Free memory", "Free Disk"]
    assert metrics["cpu_percent"] == 13.0
    assert metrics["ram_used_mb"] == 512.0
    assert metrics["ram_total_mb"] == 1024.0
    assert metrics["ram_percent"] == 50.0
    assert metrics["disk_used_gb"] == 5.0
    assert metrics["disk_total_gb"] == 25.0
    assert metrics["disk_percent"] == 20.0
    assert set(metrics["task_results"]["server_graphs"]) == {
        "Idle CPU",
        "Free memory",
        "Free Disk",
    }


def test_get_application_metrics_polls_traffic_task() -> None:
    traffic_starts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal traffic_starts
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v2/app/monitor/summary":
            return httpx.Response(200, json={"content": []})
        if request.url.path == "/api/v2/app/analytics/traffic":
            traffic_starts += 1
            assert request.url.params["server_id"] == "1366047"
            assert request.url.params["app_id"] == "5032935"
            assert request.url.params["duration"] == "1h"
            assert request.url.params["resource"] == "top_statuses"
            return httpx.Response(200, json={"status": True, "task_id": "traffic-1"})
        if request.url.path == "/api/v2/operation/traffic-1":
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "data": {
                            "contents": [
                                {"status": 200, "count": 12},
                                {"status": 500, "count": 3},
                            ]
                        },
                    }
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    metrics = client.get_application_metrics("5032935", "1366047")

    assert traffic_starts == 1
    assert metrics["traffic_requests"] == 15
    assert metrics["task_results"]["app_traffic"]["task_id"] == "traffic-1"


def test_get_server_metrics_parses_immediate_graph_content_without_task_id() -> None:
    server_payload = {
        "id": "1366047",
        "label": "Tian Tian Car",
        "cloud": "do",
        "instance_type": "1GB",
        "volume_size": "25",
    }

    def graph_content(target: str) -> str:
        if target == "Idle CPU":
            datapoints = [[79.29, 1783260000], [80.33, 1783263300]]
        elif target == "Free memory":
            datapoints = [[643457024.0, 1783260000], [599400448.0, 1783263300]]
        elif target == "Free Disk":
            datapoints = [[6.282, 1783260000], [6.281, 1783261800]]
        else:
            raise AssertionError(f"unexpected target {target}")
        return '[{"datapoints": ' + str(datapoints) + ', "target": "' + target + '"}]'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v2/server":
            return httpx.Response(200, json={"servers": [server_payload]})
        if request.url.path == "/api/v2/server/monitor/summary":
            return httpx.Response(200, json={"content": []})
        if request.url.path == "/api/v2/server/monitor/detail":
            return httpx.Response(
                200,
                json={"content": graph_content(request.url.params["target"])},
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    metrics = client.get_server_metrics("1366047")

    assert metrics["cpu_percent"] == pytest.approx(19.67)
    assert metrics["ram_used_mb"] == pytest.approx(452.3671875)
    assert metrics["ram_total_mb"] == 1024.0
    assert metrics["ram_percent"] == pytest.approx(44.176483154296875)
    assert metrics["disk_used_gb"] == pytest.approx(18.719)
    assert metrics["disk_total_gb"] == 25.0
    assert metrics["disk_percent"] == pytest.approx(74.876)


def test_get_application_metrics_parses_traffic_from_operation_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v2/app/monitor/summary":
            return httpx.Response(200, json={"content": []})
        if request.url.path == "/api/v2/app/analytics/traffic":
            return httpx.Response(200, json={"status": True, "task_id": "traffic-1"})
        if request.url.path == "/api/v2/operation/traffic-1":
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "data": [],
                        "parameters": (
                            '{"top_statuses":{"body":[["200",1],["404",2]],'
                            '"footer":[],"header":["Status","Count"]}}'
                        ),
                    },
                    "server": [
                        {
                            "apps": [
                                {
                                    "id": "5032935",
                                    "app_password": "secret-app-password",
                                    "sys_password": "secret-system-password",
                                    "mysql_password": "secret-mysql-password",
                                }
                            ],
                            "master_password": "secret-master-password",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    metrics = client.get_application_metrics("5032935", "1366047")

    assert metrics["traffic_requests"] == 3
    serialized_metrics = str(metrics)
    assert "secret-app-password" not in serialized_metrics
    assert "secret-system-password" not in serialized_metrics
    assert "secret-mysql-password" not in serialized_metrics
    assert "secret-master-password" not in serialized_metrics


def test_get_application_metrics_treats_empty_traffic_table_as_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v2/app/monitor/summary":
            return httpx.Response(200, json={"content": []})
        if request.url.path == "/api/v2/app/analytics/traffic":
            return httpx.Response(200, json={"status": True, "task_id": "traffic-empty"})
        if request.url.path == "/api/v2/operation/traffic-empty":
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "data": [],
                        "parameters": (
                            '{"top_statuses":{"body":[],"footer":[],'
                            '"header":["Status","Count"]}}'
                        ),
                    }
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    metrics = client.get_application_metrics("2341654", "706980")

    assert metrics["traffic_requests"] == 0


def test_cloudways_http_errors_are_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        return httpx.Response(
            422,
            json={
                "server_id": [
                    {
                        "code": "integer",
                        "message": "The server id must be an integer.",
                    }
                ]
            },
            request=request,
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=API_BASE_URL,
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    with pytest.raises(CloudwaysApiError) as error:
        client.list_servers()

    assert error.value.status_code == 422
    assert error.value.code == "validation_error"
    assert error.value.detail == {
        "server_id": [
            {
                "code": "integer",
                "message": "The server id must be an integer.",
            }
        ]
    }
    assert "cloudways-key" not in str(error.value)
