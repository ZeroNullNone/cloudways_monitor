from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from cloudways_monitor.cloudways import CloudwaysApiError
from cloudways_monitor.settings import Settings


class CloudwaysReadinessClient(Protocol):
    def authenticate(self) -> str: ...

    def list_servers(self) -> Sequence[object]: ...

    def list_applications(self) -> Sequence[object]: ...


class Doctor:
    def __init__(
        self,
        settings: Settings,
        cloudways_client: CloudwaysReadinessClient | None = None,
    ) -> None:
        self._settings = settings
        self._cloudways_client = cloudways_client

    def run(self) -> dict[str, object]:
        checks = {
            "config": {
                "status": "ok",
                "settings": self._settings.public_summary(),
            },
            "sqlite": self._check_sqlite(),
        }
        if self._cloudways_client is not None:
            checks["cloudways"] = self._check_cloudways()

        status = "ok"
        if any(check["status"] != "ok" for check in checks.values()):
            status = "degraded"

        return {
            "status": status,
            "checks": checks,
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

    def _check_cloudways(self) -> dict[str, object]:
        if self._cloudways_client is None:
            return {
                "status": "skipped",
                "authenticated": False,
            }
        try:
            self._cloudways_client.authenticate()
            servers = self._cloudways_client.list_servers()
            applications = self._cloudways_client.list_applications()
        except CloudwaysApiError as exc:
            return {
                "status": "error",
                "authenticated": False,
                "error_code": exc.code,
                "status_code": exc.status_code,
            }
        except Exception as exc:
            return {
                "status": "error",
                "authenticated": False,
                "error_code": exc.__class__.__name__,
                "status_code": None,
            }

        return {
            "status": "ok",
            "authenticated": True,
            "servers_discovered": len(servers),
            "applications_discovered": len(applications),
        }
