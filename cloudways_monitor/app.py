from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from cloudways_monitor.alerts import AlertEvaluator, TelegramNotifier
from cloudways_monitor.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_session_token,
    verify_password,
    verify_session_token,
)
from cloudways_monitor.cloudways import CloudwaysApiError, CloudwaysClient
from cloudways_monitor.collector import CollectorHealth, TelemetryCollector
from cloudways_monitor.doctor import Doctor
from cloudways_monitor.monitor import (
    value_in_unit,
    latest_complete_point,
    sample_is_stale,
)
from cloudways_monitor.settings import Settings, SettingsError
from cloudways_monitor.storage import Database, Storage


class LoginRequest(BaseModel):
    username: str
    password: str


@dataclass
class RuntimeState:
    settings: Settings | None
    storage: Storage | None
    telemetry_collector: TelemetryCollector | None
    cloudways_client: CloudwaysClient | None
    notifier: TelegramNotifier | None = None
    startup_error: str | None = None


def create_app(
    settings=None,
    static_dir=None,
    cloudways_client=None,
    telemetry_collector=None,
    storage=None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    runtime = RuntimeState(settings, storage, telemetry_collector, cloudways_client)
    auto_start = all(
        x is None for x in (settings, storage, telemetry_collector, cloudways_client)
    )

    @asynccontextmanager
    async def lifespan(app):
        if auto_start:
            try:
                runtime.settings = Settings.from_env()
                runtime.storage = _make_storage(runtime.settings)
                runtime.cloudways_client = CloudwaysClient(runtime.settings)
                runtime.notifier = TelegramNotifier(settings=runtime.settings)
                evaluator = AlertEvaluator(
                    settings=runtime.settings,
                    storage=runtime.storage,
                    notifier=runtime.notifier,
                )
                runtime.telemetry_collector = TelemetryCollector(
                    settings=runtime.settings,
                    storage=runtime.storage,
                    telemetry_source=runtime.cloudways_client,
                    alert_evaluator=evaluator,
                )
                runtime.telemetry_collector.start()
            except (SettingsError, OSError) as exc:
                runtime.startup_error = str(exc)
        try:
            yield
        finally:
            if auto_start:
                if runtime.telemetry_collector:
                    runtime.telemetry_collector.stop()
                if runtime.cloudways_client:
                    runtime.cloudways_client.close()
                if runtime.notifier:
                    runtime.notifier.close()

    app = FastAPI(title="Cloudways Monitor", lifespan=lifespan)

    def config():
        if runtime.settings is None:
            try:
                runtime.settings = Settings.from_env()
            except SettingsError as exc:
                raise HTTPException(503, str(exc)) from exc
        return runtime.settings

    def store():
        if runtime.storage is None:
            runtime.storage = _make_storage(config())
        return runtime.storage

    def source():
        if runtime.cloudways_client is None:
            raise HTTPException(
                503, runtime.startup_error or "Cloudways collector is not initialized"
            )
        return runtime.cloudways_client

    def current_alert_states(status=None):
        targets = {rule.target for rule in config().alert_rules}
        return [
            state
            for state in store().list_alert_states(status=status)
            if state.rule_key in targets
        ]

    def user(request):
        token = request.cookies.get(SESSION_COOKIE_NAME)
        return (
            verify_session_token(token=token, secret=config().session_secret)
            if token
            else None
        )

    def protected(request: Request):
        if user(request) is None:
            raise HTTPException(401, "Authentication required")

    @app.get("/health")
    def health():
        return dict(status="ok", service="cloudways-monitor")

    @app.post("/api/auth/login")
    def login(payload: LoginRequest, response: Response):
        cfg = config()
        if payload.username != cfg.dashboard_username or not verify_password(
            password=payload.password, password_hash=cfg.dashboard_password_hash
        ):
            raise HTTPException(401, "Invalid username or password")
        response.set_cookie(
            SESSION_COOKIE_NAME,
            create_session_token(
                username=cfg.dashboard_username, secret=cfg.session_secret
            ),
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            secure=cfg.session_cookie_secure,
            samesite="lax",
        )
        return dict(authenticated=True, username=cfg.dashboard_username)

    @app.post("/api/auth/logout")
    def logout(response: Response):
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            httponly=True,
            secure=config().session_cookie_secure,
            samesite="lax",
        )
        return dict(authenticated=False, username=None)

    @app.get("/api/auth/me")
    def me(request: Request):
        current = user(request)
        return dict(
            authenticated=bool(current), username=current.username if current else None
        )

    @app.get("/api/collector/health", dependencies=[Depends(protected)])
    def collector_health():
        return (
            runtime.telemetry_collector.health.as_dict()
            if runtime.telemetry_collector
            else CollectorHealth().as_dict()
        )

    @app.get("/api/monitor/catalog", dependencies=[Depends(protected)])
    def monitor_catalog():
        try:
            return source().monitor_catalog()
        except CloudwaysApiError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/servers/{resource_id}/graph", dependencies=[Depends(protected)])
    def graph(
        resource_id: int,
        target: str = Query(max_length=120),
        duration: str = Query(max_length=40),
    ):
        resource = store().get_resource(resource_id)
        if resource is None or resource.resource_type != "server":
            raise HTTPException(404, "Server not found")
        try:
            result = source().get_graph(resource.provider_id, target, duration)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except CloudwaysApiError as exc:
            raise HTTPException(503, str(exc)) from exc
        # Each requested window stores its full source series independently.
        store().save_graph(resource.id, result)
        return {k: v for k, v in result.items() if k != "raw"}

    @app.get(
        "/api/applications/{resource_id}/analytics", dependencies=[Depends(protected)]
    )
    def application_analytics(
        resource_id: int,
        category: str = Query(max_length=20),
        duration: str = Query(max_length=10),
    ):
        resource = store().get_resource(resource_id)
        if (
            resource is None
            or resource.resource_type != "application"
            or not resource.parent_provider_id
        ):
            raise HTTPException(404, "Application not found")
        try:
            return source().get_application_analysis(
                resource.provider_id, resource.parent_provider_id, category, duration
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/overview", dependencies=[Depends(protected)])
    def overview():
        now = clock() if clock else datetime.now(UTC)
        resources = store().list_resources()
        states = current_alert_states()
        alerts = [a for a in states if a.status == "active"]
        pressure_alerts = [
            a
            for a in states
            if a.status == "active"
            or (a.status == "pending" and a.consecutive_breaches > 0)
        ]
        grouped = {}
        servers = []
        for resource in resources:
            snapshot = store().get_latest_metric_snapshot(resource.id)
            latest = dict(
                fetched_at=snapshot.captured_at.isoformat() if snapshot else None,
                stale=snapshot is None
                or (now - snapshot.captured_at).total_seconds()
                > config().stale_after_seconds,
                diagnostics=snapshot.raw_payload.get("diagnostics", [])
                if snapshot
                else [],
                metrics=[],
            )
            if snapshot:
                raw = snapshot.raw_payload
                if resource.resource_type == "server":
                    if raw.get("capacity"):
                        latest["capacity"] = raw["capacity"]
                    for target, graph_payload in raw.get("graphs", {}).items():
                        series = graph_payload.get("series", [])
                        if (
                            graph_payload.get("status") in ("ok", "warning")
                            and len(series) == 1
                        ):
                            item = series[0]
                            points = item["points"]
                            point = latest_complete_point(points)
                            if point:
                                latest["metrics"].append(
                                    dict(
                                        target=target,
                                        value=point["value"],
                                        unit=item["unit"],
                                        sample_at=point["timestamp"],
                                        comparable_value=value_in_unit(
                                            point["value"],
                                            item["unit"],
                                            "%" if target == "Idle CPU" else "MB",
                                        ),
                                        stale=sample_is_stale(
                                            item, now, config().stale_after_seconds
                                        ),
                                    )
                                )
                else:
                    for field in (
                        "disk_used_gb",
                        "traffic_requests",
                        "bandwidth_status",
                        "traffic_complete",
                        "traffic_window",
                        "traffic_status",
                        "traffic_fetched_at",
                        "disk_used_gb_sample_at",
                    ):
                        if raw.get(field) is not None:
                            latest[field] = raw[field]
            summary = dict(
                id=resource.id,
                provider_id=resource.provider_id,
                resource_type=resource.resource_type,
                name=resource.name,
                parent_provider_id=resource.parent_provider_id,
                metadata=resource.raw,
                latest=latest,
                alerts=[
                    dict(
                        id=a.id,
                        rule_key=a.rule_key,
                        severity=a.severity,
                        status=a.status,
                    )
                    for a in pressure_alerts
                    if a.resource_id == resource.id
                ],
            )
            if resource.resource_type == "server":
                summary["applications"] = []
                servers.append(summary)
            else:
                grouped.setdefault(resource.parent_provider_id, []).append(summary)
        for server in servers:
            server["applications"] = grouped.pop(server["provider_id"], [])
        stale_count = sum(s["latest"]["stale"] for s in servers) + sum(
            a["latest"]["stale"] for s in servers for a in s["applications"]
        )
        return dict(
            servers=servers,
            unassociated_applications=[a for apps in grouped.values() for a in apps],
            collector=collector_health(),
            attention=dict(
                status="needs_attention" if alerts or stale_count else "ok",
                active_alert_count=len(alerts),
                stale_resource_count=stale_count,
            ),
            telegram_enabled=config().telegram_enabled,
            timezone="UTC+8",
            refresh_seconds=config().poll_interval_seconds,
            active_alerts=[
                dict(
                    id=a.id,
                    resource_id=a.resource_id,
                    rule_key=a.rule_key,
                    severity=a.severity,
                    opened_at=_iso(a.opened_at),
                )
                for a in alerts
            ],
        )

    @app.get(
        "/api/resources/{resource_id}/diagnostics", dependencies=[Depends(protected)]
    )
    def diagnostics(resource_id: int):
        resource = store().get_resource(resource_id)
        if resource is None:
            raise HTTPException(404, "Resource not found")
        snapshot = store().get_latest_metric_snapshot(resource_id)
        graphs = {}
        if snapshot:
            for target, graph_payload in snapshot.raw_payload.get("graphs", {}).items():
                graphs[target] = store().get_graph(
                    resource_id, target, graph_payload["duration"]
                )
        return dict(
            resource_id=resource_id,
            fetched_at=snapshot.captured_at.isoformat() if snapshot else None,
            payload=snapshot.raw_payload if snapshot else None,
            graphs=graphs,
        )

    @app.get("/api/alerts", dependencies=[Depends(protected)])
    def alerts(status: str | None = None):
        return dict(
            alerts=[
                {
                    k: _iso(v) if isinstance(v, datetime) else v
                    for k, v in vars(a).items()
                }
                for a in current_alert_states(status=status)
            ]
        )

    @app.get("/api/alerts/events", dependencies=[Depends(protected)])
    def events(limit: int = Query(100, ge=1, le=500)):
        return dict(
            events=[
                {
                    k: _iso(v) if isinstance(v, datetime) else v
                    for k, v in vars(a).items()
                }
                for a in store().list_alert_events(limit=limit)
            ]
        )

    @app.get("/api/doctor", dependencies=[Depends(protected)])
    def doctor():
        return Doctor(config(), runtime.cloudways_client).run()

    static = Path(static_dir) if static_dir else Path("frontend/dist")
    index = static / "index.html"
    if index.exists():
        if (static / "assets").exists():
            app.mount(
                "/assets", StaticFiles(directory=static / "assets"), name="assets"
            )

        @app.get("/")
        def frontend(request: Request):
            return FileResponse(index) if user(request) else RedirectResponse("/login")

        @app.get("/login")
        def login_page():
            return FileResponse(index)

        @app.get("/favicon.svg")
        def favicon():
            return FileResponse(static / "favicon.svg")

    return app


def _make_storage(settings):
    database = Database(settings.sqlite_path)
    database.migrate()
    return Storage(database)


def _iso(value):
    return value.isoformat() if value else None
