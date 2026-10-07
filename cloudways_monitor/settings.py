from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class SettingsError(ValueError):
    """Invalid runtime configuration; never includes secret values."""


@dataclass(frozen=True)
class AlertRule:
    target: str
    unit: str
    warning: float
    critical: float
    comparison: str = "<="

    def as_dict(self) -> dict[str, object]:
        return dict(
            target=self.target,
            unit=self.unit,
            warning=self.warning,
            critical=self.critical,
            comparison=self.comparison,
        )


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
    cloudways_access_token: str
    cloudways_api_base_url: str
    monitored_server_ids: tuple[str, ...]
    monitored_app_ids: tuple[str, ...]
    dashboard_username: str
    dashboard_password_hash: str
    session_secret: str
    session_cookie_secure: bool
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_enabled: bool
    alert_rules: tuple[AlertRule, ...]
    alert_consecutive_polls: int
    alert_cooldown_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        def text(key: str, default: str | None = None) -> str:
            value = source.get(key, default or "").strip()
            if default is None and not value:
                raise SettingsError(f"{key} is required")
            return value

        def number(key: str, default: int) -> int:
            try:
                value = int(text(key, str(default)))
            except ValueError as exc:
                raise SettingsError(f"{key} must be an integer") from exc
            if value <= 0:
                raise SettingsError(f"{key} must be greater than zero")
            return value

        def boolean(key: str, default: bool) -> bool:
            value = text(key, str(default)).lower()
            if value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                raise SettingsError(f"{key} must be a boolean")
            return value in {"true", "1", "yes", "on"}

        def ids(key: str) -> tuple[str, ...]:
            return tuple(x.strip() for x in text(key, "").split(",") if x.strip())

        token = text("CLOUDWAYS_ACCESS_TOKEN")
        rules = tuple(
            AlertRule(
                target,
                unit,
                number(f"{prefix}_WARNING_{suffix}", warn),
                number(f"{prefix}_CRITICAL_{suffix}", crit),
            )
            for target, unit, prefix, suffix, warn, crit in (
                ("Idle CPU", "%", "IDLE_CPU", "PERCENT", 20, 5),
                ("Free memory", "MB", "FREE_MEMORY", "MB", 256, 128),
                ("Free Disk", "MB", "FREE_DISK", "MB", 5120, 2048),
            )
        )
        for rule in rules:
            if rule.critical >= rule.warning:
                raise SettingsError(
                    f"{rule.target}: critical must be lower than warning"
                )
            if rule.unit == "%" and rule.warning > 100:
                raise SettingsError("IDLE_CPU_WARNING_PERCENT must be at most 100")
        result = cls(
            app_env=text("APP_ENV", "production"),
            app_host=text("APP_HOST", "0.0.0.0"),
            app_port=number("APP_PORT", 8083),
            dashboard_base_url=text("DASHBOARD_BASE_URL"),
            sqlite_path=text("SQLITE_PATH"),
            poll_interval_seconds=number("POLL_INTERVAL_SECONDS", 60),
            stale_after_seconds=number("STALE_AFTER_SECONDS", 600),
            retention_days=number("RETENTION_DAYS", 30),
            cloudways_access_token=token,
            cloudways_api_base_url=text(
                "CLOUDWAYS_API_BASE_URL", "https://api.cloudways.com/api/v2"
            ),
            monitored_server_ids=ids("MONITORED_SERVER_IDS"),
            monitored_app_ids=ids("MONITORED_APP_IDS"),
            dashboard_username=text("DASHBOARD_USERNAME"),
            dashboard_password_hash=text("DASHBOARD_PASSWORD_HASH"),
            session_secret=text("SESSION_SECRET"),
            session_cookie_secure=boolean("SESSION_COOKIE_SECURE", True),
            telegram_bot_token=text("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=text("TELEGRAM_CHAT_ID", ""),
            telegram_enabled=boolean("TELEGRAM_ENABLED", True),
            alert_rules=rules,
            alert_consecutive_polls=number("ALERT_CONSECUTIVE_POLLS", 3),
            alert_cooldown_seconds=number("ALERT_COOLDOWN_SECONDS", 1800),
        )
        if len(result.session_secret) < 16:
            raise SettingsError("SESSION_SECRET must be at least 16 characters")
        if result.stale_after_seconds < result.poll_interval_seconds:
            raise SettingsError(
                "STALE_AFTER_SECONDS must be at least POLL_INTERVAL_SECONDS"
            )
        for key, url in (
            ("DASHBOARD_BASE_URL", result.dashboard_base_url),
            ("CLOUDWAYS_API_BASE_URL", result.cloudways_api_base_url),
        ):
            if not url.startswith(("http://", "https://")):
                raise SettingsError(f"{key} must start with http:// or https://")
        if result.telegram_enabled and not (
            result.telegram_bot_token and result.telegram_chat_id
        ):
            raise SettingsError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when Telegram is enabled"
            )
        return result

    def public_summary(self) -> dict[str, object]:
        return dict(
            app_env=self.app_env,
            poll_interval_seconds=self.poll_interval_seconds,
            stale_after_seconds=self.stale_after_seconds,
            retention_days=self.retention_days,
            cloudways_api_base_url=self.cloudways_api_base_url,
            cloudways_access_token_configured=bool(self.cloudways_access_token),
            timezone="UTC+8",
            provider="do",
            telegram_enabled=self.telegram_enabled,
            telegram_bot_token_configured=bool(self.telegram_bot_token),
            telegram_chat_id_configured=bool(self.telegram_chat_id),
            alert_rules=[r.as_dict() for r in self.alert_rules],
            alert_consecutive_samples=self.alert_consecutive_polls,
            alert_cooldown_seconds=self.alert_cooldown_seconds,
        )
