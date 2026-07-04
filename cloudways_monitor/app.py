from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from cloudways_monitor.doctor import Doctor
from cloudways_monitor.settings import Settings, SettingsError


def create_app(
    settings: Settings | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Cloudways Monitor")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "cloudways-monitor",
        }

    @app.get("/api/doctor")
    def doctor() -> dict[str, object]:
        resolved_settings = settings
        if resolved_settings is None:
            try:
                resolved_settings = Settings.from_env()
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
        return Doctor(resolved_settings).run()

    resolved_static_dir = Path("frontend/dist") if static_dir is None else Path(static_dir)
    _mount_static_ui(app, resolved_static_dir)

    return app


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
