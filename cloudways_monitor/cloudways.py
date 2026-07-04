from __future__ import annotations

from typing import Any, Literal, TypedDict

import httpx

from cloudways_monitor.settings import Settings


class DiscoveredResource(TypedDict):
    provider_id: str
    resource_type: Literal["server", "application"]
    name: str
    parent_provider_id: str | None
    raw: dict[str, Any]


class CloudwaysApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        code: str,
        detail: object,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.detail = detail


class CloudwaysClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or httpx.Client(
            base_url=settings.cloudways_api_base_url,
            timeout=30.0,
        )
        self._access_token: str | None = None

    def authenticate(self) -> str:
        if self._access_token is not None:
            return self._access_token

        response = self._request(
            "POST",
            "/oauth/access_token",
            data={
                "email": self._settings.cloudways_email,
                "api_key": self._settings.cloudways_api_key,
            },
            authenticated=False,
        )
        payload = response.json()
        self._access_token = str(payload["access_token"])
        return self._access_token

    def list_servers(self) -> list[DiscoveredResource]:
        payload = self._get("/server")
        servers = _payload_items(payload, "servers")
        return [_server_resource(server) for server in servers]

    def list_applications(self) -> list[DiscoveredResource]:
        payload = self._get("/app")
        applications = _payload_items(payload, "apps")
        return [_application_resource(application) for application in applications]

    def _get(self, path: str) -> dict[str, Any]:
        response = self._request("GET", path, authenticated=True)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Cloudways response payload must be a JSON object")
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if authenticated:
            token = self.authenticate()
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self._http_client.request(
                method,
                path,
                data=data,
                headers=headers,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise _api_error_from_response(exc.response) from exc
        except httpx.RequestError as exc:
            raise CloudwaysApiError(
                "Cloudways API request failed",
                status_code=None,
                code="network_error",
                detail={"error": exc.__class__.__name__},
            ) from exc


def _api_error_from_response(response: httpx.Response) -> CloudwaysApiError:
    detail = _response_detail(response)
    return CloudwaysApiError(
        f"Cloudways API returned HTTP {response.status_code}",
        status_code=response.status_code,
        code=_error_code(response.status_code),
        detail=detail,
    )


def _response_detail(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return {"message": response.text}


def _error_code(status_code: int) -> str:
    if status_code == 400:
        return "missing_access_token"
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "authorization_error"
    if status_code == 422:
        return "validation_error"
    if status_code == 500:
        return "server_error"
    return "http_error"


def _payload_items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw_items = payload.get(key, [])
    if not isinstance(raw_items, list):
        raise ValueError(f"Cloudways response field {key!r} must be a list")
    return [item for item in raw_items if isinstance(item, dict)]


def _server_resource(server: dict[str, Any]) -> DiscoveredResource:
    provider_id = _required_provider_id(server)
    return {
        "provider_id": provider_id,
        "resource_type": "server",
        "name": _resource_name(server, fallback=provider_id),
        "parent_provider_id": None,
        "raw": server,
    }


def _application_resource(application: dict[str, Any]) -> DiscoveredResource:
    provider_id = _required_provider_id(application)
    return {
        "provider_id": provider_id,
        "resource_type": "application",
        "name": _resource_name(application, fallback=provider_id),
        "parent_provider_id": _optional_provider_id(application.get("server_id")),
        "raw": application,
    }


def _required_provider_id(payload: dict[str, Any]) -> str:
    provider_id = _optional_provider_id(payload.get("id"))
    if provider_id is None:
        raise ValueError("Cloudways resource is missing an id")
    return provider_id


def _optional_provider_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _resource_name(payload: dict[str, Any], fallback: str) -> str:
    for key in ("label", "name", "app_label"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback
