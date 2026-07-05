from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
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
from cloudways_monitor.storage import AlertEvent, AlertState, Database, Storage


class LoginRequest(BaseModel):
    username: str
    password: str


def create_app(
    settings: Settings | None = None,
    static_dir: str | Path | None = None,
    cloudways_client: CloudwaysReadinessClient | None = None,
    telemetry_collector: TelemetryCollector | None = None,
    storage: Storage | None = None,
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
