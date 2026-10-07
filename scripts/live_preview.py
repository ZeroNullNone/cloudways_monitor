"""Local real-data preview. Load .env in-process; never print or edit it.

Run: python -m scripts.live_preview. Stop with Ctrl+C.
Uses configured dashboard credentials, a separate local database and no Telegram.
"""

from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

from cloudways_monitor.app import create_app
from cloudways_monitor.cloudways import CloudwaysClient
from cloudways_monitor.collector import TelemetryCollector
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, Storage


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=True)
    local = root / ".tmp" / "live-preview"
    local.mkdir(parents=True, exist_ok=True)
    settings = replace(
        Settings.from_env(),
        sqlite_path=str(local / "monitor.sqlite3"),
        dashboard_base_url="http://127.0.0.1:8084",
        session_cookie_secure=False,
        telegram_enabled=False,
    )
    database = Database(settings.sqlite_path)
    database.migrate()
    storage = Storage(database)
    source = CloudwaysClient(settings)
    collector = TelemetryCollector(
        settings=settings, storage=storage, telemetry_source=source
    )
    app = create_app(
        settings=settings,
        storage=storage,
        cloudways_client=source,
        telemetry_collector=collector,
        static_dir=str(root / "frontend" / "dist"),
    )
    collector.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=8084)
    finally:
        collector.stop()
        source.close()


if __name__ == "__main__":
    main()
