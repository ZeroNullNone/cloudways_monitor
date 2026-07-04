import httpx
import pytest

from cloudways_monitor.cloudways import CloudwaysApiError, CloudwaysClient
from cloudways_monitor.settings import Settings
from tests.helpers import valid_env


def make_settings() -> Settings:
    return Settings.from_env(valid_env())


def test_authenticate_gets_and_caches_oauth_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/oauth/access_token"
        return httpx.Response(200, json={"access_token": "token-123"})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.cloudways.com/api/v1",
    )
    client = CloudwaysClient(make_settings(), http_client=http_client)

    first_token = client.authenticate()
    second_token = client.authenticate()

    assert first_token == "token-123"
    assert second_token == "token-123"
    assert len(requests) == 1


def test_list_servers_and_applications_return_normalized_resources() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/oauth/access_token":
            return httpx.Response(200, json={"access_token": "token-123"})
        assert request.headers["Authorization"] == "Bearer token-123"
        if request.url.path == "/api/v1/server":
            return httpx.Response(
                200,
                json={
                    "servers": [
                        {
                            "id": 123,
                            "label": "production",
                            "provider": "do",
                            "region": "Singapore",
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/app":
            return httpx.Response(
                200,
                json={
                    "apps": [
                        {
                            "id": 987,
                            "server_id": 123,
                            "label": "storefront",
                            "application": "wordpress",
                            "app_version": "6.5",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.cloudways.com/api/v1",
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
            "raw": {
                "id": 123,
                "label": "production",
                "provider": "do",
                "region": "Singapore",
            },
        }
    ]
    assert applications == [
        {
            "provider_id": "987",
            "resource_type": "application",
            "name": "storefront",
            "parent_provider_id": "123",
            "raw": {
                "id": 987,
                "server_id": 123,
                "label": "storefront",
                "application": "wordpress",
                "app_version": "6.5",
            },
        }
    ]


def test_cloudways_http_errors_are_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/oauth/access_token":
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
        base_url="https://api.cloudways.com/api/v1",
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
