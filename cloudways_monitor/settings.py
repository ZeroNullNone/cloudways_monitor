from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class SettingsError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_host: str
    app_port: int
    dashboard_base_url: str
    sqlite_path: str
    poll_interval_seconds: int
    stale_after_seconds: int
    retention_days: int
    cloudways_email: str
    cloudways_api_key: str
    cloudways_api_base_url: str
    cloudways_task_polling_enabled: bool
    cloudways_task_poll_attempts: int
    cloudways_task_poll_interval_seconds: float
    cloudways_monitor_graph_duration: str
    cloudways_monitor_graph_timezone: str
    cloudways_app_traffic_polling_enabled: bool
    cloudways_app_traffic_duration: str
    monitored_server_ids: tuple[str, ...]
    monitored_app_ids: tuple[str, ...]
    dashboard_username: str
    dashboard_password_hash: str
    session_secret: str
    session_cookie_secure: bool
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_enabled: bool
    cpu_warning_percent: int
    cpu_critical_percent: int
    ram_warning_percent: int
    ram_critical_percent: int
    disk_warning_percent: int
    disk_critical_percent: int
    alert_consecutive_polls: int
    alert_cooldown_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env

        settings = cls(
            app_env=_optional(source, "APP_ENV", "production"),
            app_host=_optional(source, "APP_HOST", "0.0.0.0"),
            app_port=_positive_int(source, "APP_PORT", 8083),
            dashboard_base_url=_required(source, "DASHBOARD_BASE_URL"),
            sqlite_path=_required(source, "SQLITE_PATH"),
            poll_interval_seconds=_positive_int(source, "POLL_INTERVAL_SECONDS", 60),
            stale_after_seconds=_positive_int(source, "STALE_AFTER_SECONDS", 600),
            retention_days=_positive_int(source, "RETENTION_DAYS", 30),
            cloudways_email=_required(source, "CLOUDWAYS_EMAIL"),
            cloudways_api_key=_required(source, "CLOUDWAYS_API_KEY"),
            cloudways_api_base_url=_optional(
                source,
                "CLOUDWAYS_API_BASE_URL",
                "https://api.cloudways.com/api/v2",
            ),
            cloudways_task_polling_enabled=_bool(
                source,
                "CLOUDWAYS_TASK_POLLING_ENABLED",
                True,
            ),
            cloudways_task_poll_attempts=_positive_int(
                source,
                "CLOUDWAYS_TASK_POLL_ATTEMPTS",
                1,
            ),
            cloudways_task_poll_interval_seconds=_non_negative_float(
                source,
                "CLOUDWAYS_TASK_POLL_INTERVAL_SECONDS",
                0.0,
            ),
            cloudways_monitor_graph_duration=_optional(
                source,
                "CLOUDWAYS_MONITOR_GRAPH_DURATION",
                "1 Hour",
            ),
            cloudways_monitor_graph_timezone=_optional(
                source,
                "CLOUDWAYS_MONITOR_GRAPH_TIMEZONE",
                "UTC",
            ),
            cloudways_app_traffic_polling_enabled=_bool(
                source,
                "CLOUDWAYS_APP_TRAFFIC_POLLING_ENABLED",
                True,
            ),
            cloudways_app_traffic_duration=_optional(
                source,
                "CLOUDWAYS_APP_TRAFFIC_DURATION",
                "1h",
            ),
            monitored_server_ids=_csv(source, "MONITORED_SERVER_IDS"),
            monitored_app_ids=_csv(source, "MONITORED_APP_IDS"),
            dashboard_username=_required(source, "DASHBOARD_USERNAME"),
            dashboard_password_hash=_required(source, "DASHBOARD_PASSWORD_HASH"),
            session_secret=_required(source, "SESSION_SECRET"),
            session_cookie_secure=_bool(source, "SESSION_COOKIE_SECURE", True),
            telegram_bot_token=_optional(source, "TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=_optional(source, "TELEGRAM_CHAT_ID", ""),
            telegram_enabled=_bool(source, "TELEGRAM_ENABLED", True),
            cpu_warning_percent=_percent(source, "CPU_WARNING_PERCENT", 80),
            cpu_critical_percent=_percent(source, "CPU_CRITICAL_PERCENT", 95),
            ram_warning_percent=_percent(source, "RAM_WARNING_PERCENT", 80),
            ram_critical_percent=_percent(source, "RAM_CRITICAL_PERCENT", 95),
            disk_warning_percent=_percent(source, "DISK_WARNING_PERCENT", 80),
            disk_critical_percent=_percent(source, "DISK_CRITICAL_PERCENT", 90),
            alert_consecutive_polls=_positive_int(
                source,
                "ALERT_CONSECUTIVE_POLLS",
                3,
            ),
            alert_cooldown_seconds=_positive_int(
                source,
                "ALERT_COOLDOWN_SECONDS",
                1800,
            ),
        )
        settings._validate()
        return settings

    @property
    def effective_stale_after_seconds(self) -> int:
        if not self.cloudways_task_polling_enabled:
            return self.stale_after_seconds
        return max(self.stale_after_seconds, self.poll_interval_seconds * 10)
    def public_summary(self) -> dict[str, object]:
        return {
            "app_env": self.app_env,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "dashboard_base_url": self.dashboard_base_url,
            "sqlite_path": self.sqlite_path,
            "poll_interval_seconds": self.poll_interval_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "effective_stale_after_seconds": self.effective_stale_after_seconds,
            "retention_days": self.retention_days,
            "cloudways_email": self.cloudways_email,
            "cloudways_api_base_url": self.cloudways_api_base_url,
            "cloudways_api_key_configured": bool(self.cloudways_api_key),
            "cloudways_task_polling_enabled": self.cloudways_task_polling_enabled,
            "cloudways_task_poll_attempts": self.cloudways_task_poll_attempts,
            "cloudways_task_poll_interval_seconds": (
                self.cloudways_task_poll_interval_seconds
            ),
            "cloudways_monitor_graph_duration": (
                self.cloudways_monitor_graph_duration
            ),
            "cloudways_monitor_graph_timezone": (
                self.cloudways_monitor_graph_timezone
            ),
            "cloudways_app_traffic_polling_enabled": (
                self.cloudways_app_traffic_polling_enabled
            ),
            "cloudways_app_traffic_duration": self.cloudways_app_traffic_duration,
            "monitored_server_ids": self.monitored_server_ids,
            "monitored_app_ids": self.monitored_app_ids,
            "dashboard_username": self.dashboard_username,
            "password_hash_configured": bool(self.dashboard_password_hash),
            "session_secret_configured": bool(self.session_secret),
            "session_cookie_secure": self.session_cookie_secure,
            "telegram_enabled": self.telegram_enabled,
            "telegram_bot_token_configured": bool(self.telegram_bot_token),
            "telegram_chat_id_configured": bool(self.telegram_chat_id),
            "cpu_warning_percent": self.cpu_warning_percent,
            "cpu_critical_percent": self.cpu_critical_percent,
            "ram_warning_percent": self.ram_warning_percent,
            "ram_critical_percent": self.ram_critical_percent,
            "disk_warning_percent": self.disk_warning_percent,
            "disk_critical_percent": self.disk_critical_percent,
            "alert_consecutive_polls": self.alert_consecutive_polls,
            "alert_cooldown_seconds": self.alert_cooldown_seconds,
        }

    def _validate(self) -> None:
        _require_http_url("DASHBOARD_BASE_URL", self.dashboard_base_url)
        _require_http_url("CLOUDWAYS_API_BASE_URL", self.cloudways_api_base_url)
        _require_min_length("SESSION_SECRET", self.session_secret, 16)
        _require_value(
            "CLOUDWAYS_MONITOR_GRAPH_DURATION",
            self.cloudways_monitor_graph_duration,
        )
        _require_value(
            "CLOUDWAYS_MONITOR_GRAPH_TIMEZONE",
            self.cloudways_monitor_graph_timezone,
        )
        _require_value(
            "CLOUDWAYS_APP_TRAFFIC_DURATION",
            self.cloudways_app_traffic_duration,
        )
        _require_ordered_thresholds(
            "CPU",
            self.cpu_warning_percent,
            self.cpu_critical_percent,
        )
        _require_ordered_thresholds(
            "RAM",
            self.ram_warning_percent,
            self.ram_critical_percent,
        )
        _require_ordered_thresholds(
            "DISK",
            self.disk_warning_percent,
            self.disk_critical_percent,
        )
        if self.stale_after_seconds < self.poll_interval_seconds:
            raise SettingsError(
                "STALE_AFTER_SECONDS must be greater than or equal to "
                "POLL_INTERVAL_SECONDS"
            )
        if self.telegram_enabled:
            _require_value("TELEGRAM_BOT_TOKEN", self.telegram_bot_token)
            _require_value("TELEGRAM_CHAT_ID", self.telegram_chat_id)


def _optional(source: Mapping[str, str], key: str, default: str) -> str:
    value = source.get(key, default)
    return value.strip()


def _required(source: Mapping[str, str], key: str) -> str:
    value = _optional(source, key, "")
    _require_value(key, value)
    return value


def _require_value(key: str, value: str) -> None:
    if not value:
        raise SettingsError(f"{key} is required")


def _positive_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw_value = _optional(source, key, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer") from exc
    if value <= 0:
        raise SettingsError(f"{key} must be greater than zero")
    return value


def _non_negative_float(source: Mapping[str, str], key: str, default: float) -> float:
    raw_value = _optional(source, key, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number") from exc
    if value < 0:
        raise SettingsError(f"{key} must be greater than or equal to zero")
    return value


def _percent(source: Mapping[str, str], key: str, default: int) -> int:
    value = _positive_int(source, key, default)
    if value > 100:
        raise SettingsError(f"{key} must be between 1 and 100")
    return value


def _bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw_value = _optional(source, key, str(default)).lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{key} must be a boolean")


def _csv(source: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw_value = _optional(source, key, "")
    if not raw_value:
        return ()
    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


def _require_http_url(key: str, value: str) -> None:
    if not value.startswith(("http://", "https://")):
        raise SettingsError(f"{key} must start with http:// or https://")


def _require_min_length(key: str, value: str, minimum: int) -> None:
    if len(value) < minimum:
        raise SettingsError(f"{key} must be at least {minimum} characters")


def _require_ordered_thresholds(name: str, warning: int, critical: int) -> None:
    if warning >= critical:
        raise SettingsError(
            f"{name}_WARNING_PERCENT must be less than {name}_CRITICAL_PERCENT"
        )
