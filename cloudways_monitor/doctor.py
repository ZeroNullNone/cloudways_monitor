from __future__ import annotations

import sqlite3
from pathlib import Path

from cloudways_monitor.settings import Settings


class Doctor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run(self) -> dict[str, object]:
        config = {
            "status": "ok",
            "settings": self._settings.public_summary(),
        }
        sqlite = self._check_sqlite()
        status = "ok" if sqlite["status"] == "ok" else "degraded"

        return {
            "status": status,
            "checks": {
                "config": config,
                "sqlite": sqlite,
            },
        }

    def _check_sqlite(self) -> dict[str, object]:
        sqlite_path = Path(self._settings.sqlite_path)
        try:
            if sqlite_path.parent:
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(sqlite_path) as connection:
                connection.execute("PRAGMA user_version")
        except sqlite3.Error as exc:
            return {
                "status": "error",
                "path": str(sqlite_path),
                "writable": False,
                "error": exc.__class__.__name__,
            }
        except OSError as exc:
            return {
                "status": "error",
                "path": str(sqlite_path),
                "writable": False,
                "error": exc.__class__.__name__,
            }

        return {
            "status": "ok",
            "path": str(sqlite_path),
            "writable": True,
        }
