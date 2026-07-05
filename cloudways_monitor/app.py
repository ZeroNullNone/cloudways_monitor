from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from cloudways_monitor.cloudways import CloudwaysClient
from cloudways_monitor.collector import TelemetryCollector
from cloudways_monitor.doctor import CloudwaysReadinessClient, Doctor
from cloudways_monitor.settings import Settings, SettingsError
from cloudways_monitor.storage import AlertEvent, AlertState, Database, Storage


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

    @app.get("/api/collector/health")
    def collector_health() -> dict[str, object]:
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
    def alerts(status: str | None = None) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        return {
            "alerts": [
                _alert_state_to_dict(alert)
                for alert in resolved_storage.list_alert_states(status=status)
            ]
        }

    @app.get("/api/alerts/events")
    def alert_events(limit: int = 100) -> dict[str, object]:
        resolved_storage = _resolve_storage(storage, settings)
        return {
            "events": [
                _alert_event_to_dict(event)
                for event in resolved_storage.list_alert_events(limit=limit)
            ]
        }

    @app.get("/api/doctor")
    def doctor() -> dict[str, object]:
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
    _mount_static_ui(app, resolved_static_dir)

    return app


def _resolve_storage(
    storage: Storage | None,
    settings: Settings | None,
) -> Storage:
    if storage is not None:
        return storage
    try:
        resolved_settings = settings or Settings.from_env()
    except SettingsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

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


def _mount_static_ui(app: FastAPI, static_dir: Path) -> None:
    index_path = static_dir / "index.html"
    if not index_path.exists():
        return

    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def frontend_index() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/{full_path:path}")
    def frontend_fallback(full_path: str) -> FileResponse:
        if full_path == "health" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)

        requested_path = (static_dir / full_path).resolve()
        static_root = static_dir.resolve()
        if requested_path.is_relative_to(static_root) and requested_path.is_file():
            return FileResponse(requested_path)

        return FileResponse(index_path)
