from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from cloudways_monitor.auth import (
    AuthenticatedUser,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_session_token,
    verify_password,
    verify_session_token,
)
from cloudways_monitor.cloudways import CloudwaysClient
from cloudways_monitor.collector import TelemetryCollector
from cloudways_monitor.doctor import CloudwaysReadinessClient, Doctor
from cloudways_monitor.settings import Settings, SettingsError
from cloudways_monitor.storage import (
    AlertEvent,
    AlertState,
    Database,
    MetricSnapshot,
    MonitoredResource,
    Storage,
)


DashboardRange = Literal["1h", "6h", "24h", "7d", "30d"]


class LoginRequest(BaseModel):
    username: str
    password: str


def create_app(
    settings: Settings | None = None,
    static_dir: str | Path | None = None,
    cloudways_client: CloudwaysReadinessClient | None = None,
    telemetry_collector: TelemetryCollector | None = None,
    storage: Storage | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    app = FastAPI(title="Cloudways Monitor")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "cloudways-monitor",
        }

    @app.post("/api/auth/login")
    def login(payload: LoginRequest, response: Response) -> dict[str, object]:
        resolved_settings = _resolve_settings(settings)
        valid_credentials = (
            payload.username == resolved_settings.dashboard_username
            and verify_password(
                password=payload.password,
                password_hash=resolved_settings.dashboard_password_hash,
            )
        )
        if not valid_credentials:
            raise HTTPException(
                status_code=401,
                detail="Invalid username or password",
            )

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=create_session_token(
                username=resolved_settings.dashboard_username,
                secret=resolved_settings.session_secret,
            ),
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            secure=resolved_settings.session_cookie_secure,
            samesite="lax",
        )
        return {
            "authenticated": True,
            "username": resolved_settings.dashboard_username,
        }

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, object]:
        resolved_settings = _resolve_settings(settings)
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            httponly=True,
            secure=resolved_settings.session_cookie_secure,
            samesite="lax",
        )
        return {"authenticated": False, "username": None}

    @app.get("/api/auth/me")
    def me(request: Request) -> dict[str, object]:
        user = _current_user(request, settings)
        if user is None:
            return {"authenticated": False, "username": None}
        return {"authenticated": True, "username": user.username}

    def require_authenticated_user(request: Request) -> None:
        if _current_user(request, settings) is None:
            raise HTTPException(status_code=401, detail="Authentication required")

    @app.get("/api/collector/health")
    def collector_health(
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        return _collector_health_to_dict(telemetry_collector)

    @app.get("/api/overview")
    def overview(
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        resolved_settings = _resolve_settings(settings)
        return _overview_to_dict(
            storage=resolved_storage,
            settings=resolved_settings,
            now=_current_time(clock),
            collector=_collector_health_to_dict(telemetry_collector),
        )
    @app.get("/api/resources")
    def resources(
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        resolved_settings = _resolve_settings(settings)
        now = _current_time(clock)
        alerts_by_resource_id = _active_alerts_by_resource_id(resolved_storage)
        return {
            "resources": [
                _resource_summary_to_dict(
                    resource=resource,
                    latest=resolved_storage.get_latest_metric_snapshot(resource.id),
                    alerts=alerts_by_resource_id.get(resource.id, []),
                    settings=resolved_settings,
                    now=now,
                )
                for resource in _sorted_resources(resolved_storage.list_resources())
            ]
        }

    @app.get("/api/resources/{resource_id}")
    def resource_detail(
        resource_id: int,
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        resolved_settings = _resolve_settings(settings)
        now = _current_time(clock)
        resource = resolved_storage.get_resource(resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="Resource not found")

        alerts_by_resource_id = _active_alerts_by_resource_id(resolved_storage)
        parent_server = _parent_server_for(
            resource=resource,
            resources=resolved_storage.list_resources(),
        )
        return {
            "resource": _resource_summary_to_dict(
                resource=resource,
                latest=resolved_storage.get_latest_metric_snapshot(resource.id),
                alerts=alerts_by_resource_id.get(resource.id, []),
                settings=resolved_settings,
                now=now,
            ),
            "parent_server": None
            if parent_server is None
            else _resource_summary_to_dict(
                resource=parent_server,
                latest=resolved_storage.get_latest_metric_snapshot(parent_server.id),
                alerts=alerts_by_resource_id.get(parent_server.id, []),
                settings=resolved_settings,
                now=now,
            ),
        }
    @app.get("/api/resources/{resource_id}/series")
    def resource_series(
        resource_id: int,
        range_key: Annotated[DashboardRange, Query(alias="range")] = "1h",
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        resource = resolved_storage.get_resource(resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="Resource not found")

        end = _current_time(clock)
        start = end - _range_delta(range_key)
        snapshots = resolved_storage.list_metric_snapshots(
            resource_id=resource_id,
            start=start,
            end=end,
        )
        return {
            "resource": _resource_identity_to_dict(resource),
            "range": {
                "key": range_key,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "points": [_series_point_to_dict(snapshot) for snapshot in snapshots],
        }
    @app.get("/api/resources/{resource_id}/raw/latest")
    def resource_raw_latest(
        resource_id: int,
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        resource = resolved_storage.get_resource(resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="Resource not found")

        snapshot = resolved_storage.get_latest_metric_snapshot(resource_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No metric snapshot found")

        return {
            "resource": _resource_identity_to_dict(resource),
            "snapshot": _raw_snapshot_to_dict(snapshot),
        }
    @app.get("/api/alerts")
    def alerts(
        status: str | None = None,
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        return {
            "alerts": [
                _alert_state_to_dict(alert)
                for alert in resolved_storage.list_alert_states(status=status)
            ]
        }

    @app.get("/api/alerts/events")
    def alert_events(
        limit: int = 100,
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        return {
            "events": [
                _alert_event_to_dict(event)
                for event in resolved_storage.list_alert_events(limit=limit)
            ]
        }

    @app.get("/api/events")
    def events(
        _authenticated: None = Depends(require_authenticated_user),
    ) -> StreamingResponse:
        return StreamingResponse(
            iter(['event: dashboard-refresh\ndata: {"type":"dashboard-refresh"}\n\n']),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    @app.get("/api/doctor")
    def doctor(
        _authenticated: None = Depends(require_authenticated_user),
    ) -> dict[str, object]:
        resolved_settings = settings
        resolved_cloudways_client = cloudways_client
        if resolved_settings is None:
            try:
                resolved_settings = Settings.from_env()
                if resolved_cloudways_client is None:
                    resolved_cloudways_client = CloudwaysClient(resolved_settings)
            except SettingsError as exc:
                return {
                    "status": "degraded",
                    "checks": {
                        "config": {
                            "status": "error",
                            "error": str(exc),
                        },
                        "sqlite": {
                            "status": "skipped",
                            "writable": False,
                        },
                    },
                }
        return Doctor(resolved_settings, resolved_cloudways_client).run()

    resolved_static_dir = (
        Path("frontend/dist") if static_dir is None else Path(static_dir)
    )
    _mount_static_ui(app, resolved_static_dir, settings)

    return app


def _current_time(clock: Callable[[], datetime] | None) -> datetime:
    if clock is not None:
        return clock()
    return datetime.now(UTC)


def _collector_health_to_dict(
    telemetry_collector: TelemetryCollector | None,
) -> dict[str, object]:
    if telemetry_collector is None:
        return {
            "status": "never_run",
            "last_run_at": None,
            "last_success_at": None,
            "servers_discovered": 0,
            "applications_discovered": 0,
            "snapshots_stored": 0,
            "snapshots_expired": 0,
            "stale": True,
            "last_error_code": None,
            "last_error": None,
        }
    return telemetry_collector.health.as_dict()


def _overview_to_dict(
    *,
    storage: Storage,
    settings: Settings,
    now: datetime,
    collector: dict[str, object],
) -> dict[str, object]:
    resources = storage.list_resources()
    resources_by_id = {resource.id: resource for resource in resources}
    latest_by_resource_id = {
        resource.id: storage.get_latest_metric_snapshot(resource.id)
        for resource in resources
    }
    active_alerts = storage.list_alert_states(status="active")
    alerts_by_resource_id = _active_alerts_by_resource_id(storage)

    application_summaries_by_parent: dict[str, list[dict[str, object]]] = {}
    for resource in resources:
        if resource.resource_type != "application":
            continue
        summary = _resource_summary_to_dict(
            resource=resource,
            latest=latest_by_resource_id[resource.id],
            alerts=alerts_by_resource_id.get(resource.id, []),
            settings=settings,
            now=now,
        )
        if resource.parent_provider_id is not None:
            application_summaries_by_parent.setdefault(
                resource.parent_provider_id,
                [],
            ).append(summary)

    servers = []
    for resource in resources:
        if resource.resource_type != "server":
            continue
        summary = _resource_summary_to_dict(
            resource=resource,
            latest=latest_by_resource_id[resource.id],
            alerts=alerts_by_resource_id.get(resource.id, []),
            settings=settings,
            now=now,
        )
        summary["applications"] = application_summaries_by_parent.get(
            resource.provider_id,
            [],
        )
        servers.append(summary)

    stale_resource_count = sum(
        1
        for snapshot in latest_by_resource_id.values()
        if _snapshot_is_stale(snapshot=snapshot, settings=settings, now=now)
    )
    active_alert_dicts = [
        _overview_alert_to_dict(alert, resources_by_id.get(alert.resource_id))
        for alert in active_alerts
    ]
    needs_attention = bool(active_alert_dicts) or stale_resource_count > 0
    return {
        "attention": {
            "status": "needs_attention" if needs_attention else "ok",
            "active_alert_count": len(active_alert_dicts),
            "stale_resource_count": stale_resource_count,
        },
        "collector": collector,
        "active_alerts": active_alert_dicts,
        "servers": servers,
    }


def _active_alerts_by_resource_id(storage: Storage) -> dict[int, list[AlertState]]:
    alerts_by_resource_id: dict[int, list[AlertState]] = {}
    for alert in storage.list_alert_states(status="active"):
        alerts_by_resource_id.setdefault(alert.resource_id, []).append(alert)
    return alerts_by_resource_id


def _sorted_resources(resources: list[MonitoredResource]) -> list[MonitoredResource]:
    return sorted(
        resources,
        key=lambda resource: (
            0 if resource.resource_type == "server" else 1,
            resource.provider_id,
        ),
    )


def _parent_server_for(
    *,
    resource: MonitoredResource,
    resources: list[MonitoredResource],
) -> MonitoredResource | None:
    if resource.resource_type != "application" or resource.parent_provider_id is None:
        return None
    for candidate in resources:
        if (
            candidate.resource_type == "server"
            and candidate.provider_id == resource.parent_provider_id
        ):
            return candidate
    return None

def _range_delta(range_key: DashboardRange) -> timedelta:
    return {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }[range_key]


def _resource_identity_to_dict(resource: MonitoredResource) -> dict[str, object]:
    return {
        "id": resource.id,
        "provider_id": resource.provider_id,
        "resource_type": resource.resource_type,
        "name": resource.name,
        "parent_provider_id": resource.parent_provider_id,
    }


def _series_point_to_dict(snapshot: MetricSnapshot) -> dict[str, object]:
    return {
        "captured_at": snapshot.captured_at.isoformat(),
        "cpu_percent": snapshot.cpu_percent,
        "ram_percent": snapshot.ram_percent,
        "disk_percent": snapshot.disk_percent,
        "bandwidth_bytes": snapshot.bandwidth_bytes,
        "traffic_requests": snapshot.traffic_requests,
        "collection_status": snapshot.collection_status,
        "error_code": snapshot.error_code,
    }

def _raw_snapshot_to_dict(snapshot: MetricSnapshot) -> dict[str, object]:
    return {
        "captured_at": snapshot.captured_at.isoformat(),
        "collection_status": snapshot.collection_status,
        "error_code": snapshot.error_code,
        "php_metric": snapshot.php_metric,
        "mysql_metric": snapshot.mysql_metric,
        "raw_payload": snapshot.raw_payload,
    }

def _resource_summary_to_dict(
    *,
    resource: MonitoredResource,
    latest: MetricSnapshot | None,
    alerts: list[AlertState],
    settings: Settings,
    now: datetime,
) -> dict[str, object]:
    return {
        "id": resource.id,
        "provider_id": resource.provider_id,
        "resource_type": resource.resource_type,
        "name": resource.name,
        "parent_provider_id": resource.parent_provider_id,
        "latest": _latest_snapshot_to_dict(
            snapshot=latest,
            settings=settings,
            now=now,
        ),
        "alerts": [_compact_alert_to_dict(alert) for alert in alerts],
    }


def _latest_snapshot_to_dict(
    *,
    snapshot: MetricSnapshot | None,
    settings: Settings,
    now: datetime,
) -> dict[str, object]:
    if snapshot is None:
        return {
            "captured_at": None,
            "stale": True,
            "cpu_percent": None,
            "ram_percent": None,
            "disk_percent": None,
            "bandwidth_bytes": None,
            "traffic_requests": None,
        }
    return {
        "captured_at": snapshot.captured_at.isoformat(),
        "stale": _snapshot_is_stale(snapshot=snapshot, settings=settings, now=now),
        "cpu_percent": snapshot.cpu_percent,
        "ram_percent": snapshot.ram_percent,
        "disk_percent": snapshot.disk_percent,
        "bandwidth_bytes": snapshot.bandwidth_bytes,
        "traffic_requests": snapshot.traffic_requests,
    }


def _snapshot_is_stale(
    *,
    snapshot: MetricSnapshot | None,
    settings: Settings,
    now: datetime,
) -> bool:
    if snapshot is None:
        return True
    return (now - snapshot.captured_at).total_seconds() > settings.stale_after_seconds


def _compact_alert_to_dict(alert: AlertState) -> dict[str, object]:
    return {
        "id": alert.id,
        "rule_key": alert.rule_key,
        "severity": alert.severity,
        "status": alert.status,
    }


def _overview_alert_to_dict(
    alert: AlertState,
    resource: MonitoredResource | None,
) -> dict[str, object]:
    return {
        "id": alert.id,
        "resource_id": alert.resource_id,
        "resource_name": resource.name if resource is not None else None,
        "resource_type": resource.resource_type if resource is not None else None,
        "rule_key": alert.rule_key,
        "severity": alert.severity,
        "status": alert.status,
        "consecutive_breaches": alert.consecutive_breaches,
        "opened_at": _iso(alert.opened_at),
        "last_notification_at": _iso(alert.last_notification_at),
    }

def _resolve_settings(settings: Settings | None) -> Settings:
    if settings is not None:
        return settings
    try:
        return Settings.from_env()
    except SettingsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _current_user(
    request: Request,
    settings: Settings | None,
) -> AuthenticatedUser | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        return None
    resolved_settings = _resolve_settings(settings)
    return verify_session_token(token=token, secret=resolved_settings.session_secret)


def _resolve_storage(
    storage: Storage | None,
    settings: Settings | None,
) -> Storage:
    if storage is not None:
        return storage
    resolved_settings = _resolve_settings(settings)
    database = Database(resolved_settings.sqlite_path)
    database.migrate()
    return Storage(database)


def _alert_state_to_dict(alert: AlertState) -> dict[str, object]:
    return {
        "id": alert.id,
        "resource_id": alert.resource_id,
        "rule_key": alert.rule_key,
        "status": alert.status,
        "severity": alert.severity,
        "consecutive_breaches": alert.consecutive_breaches,
        "opened_at": _iso(alert.opened_at),
        "resolved_at": _iso(alert.resolved_at),
        "last_notification_at": _iso(alert.last_notification_at),
    }


def _alert_event_to_dict(event: AlertEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "resource_id": event.resource_id,
        "rule_key": event.rule_key,
        "event_type": event.event_type,
        "severity": event.severity,
        "message": event.message,
        "created_at": event.created_at.isoformat(),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _mount_static_ui(
    app: FastAPI,
    static_dir: Path,
    settings: Settings | None,
) -> None:
    index_path = static_dir / "index.html"
    if not index_path.exists():
        return

    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def frontend_index(request: Request) -> Response:
        if _current_user(request, settings) is None:
            return RedirectResponse("/login")
        return FileResponse(index_path)

    @app.get("/{full_path:path}")
    def frontend_fallback(
        request: Request,
        full_path: str,
    ) -> Response:
        if full_path == "health" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        if full_path != "login" and _current_user(request, settings) is None:
            return RedirectResponse("/login")

        requested_path = (static_dir / full_path).resolve()
        static_root = static_dir.resolve()
        if requested_path.is_relative_to(static_root) and requested_path.is_file():
            return FileResponse(requested_path)

        return FileResponse(index_path)
