import pytest

from cloudways_monitor.settings import Settings, SettingsError

from tests.helpers import valid_env


def test_settings_from_env_parses_typed_values() -> None:
    settings = Settings.from_env(valid_env())

    assert settings.app_port == 8083
    assert settings.poll_interval_seconds == 60
    assert settings.stale_after_seconds == 180
    assert settings.retention_days == 30
    assert settings.session_cookie_secure is True
    assert settings.telegram_enabled is True
    assert settings.monitored_server_ids == ("123", "456")
    assert settings.monitored_app_ids == ()
    assert settings.cpu_warning_percent == 80
    assert settings.disk_critical_percent == 90


def test_settings_extends_effective_stale_window_for_cloudways_task_polling() -> None:
    settings = Settings.from_env(
        valid_env(
            POLL_INTERVAL_SECONDS="60",
            STALE_AFTER_SECONDS="180",
            CLOUDWAYS_TASK_POLLING_ENABLED="true",
        )
    )

    assert settings.effective_stale_after_seconds == 600


def test_settings_preserves_stale_window_without_cloudways_task_polling() -> None:
    settings = Settings.from_env(
        valid_env(
            POLL_INTERVAL_SECONDS="60",
            STALE_AFTER_SECONDS="180",
            CLOUDWAYS_TASK_POLLING_ENABLED="false",
        )
    )

    assert settings.effective_stale_after_seconds == 180


def test_settings_rejects_missing_required_values() -> None:
    env = valid_env(CLOUDWAYS_API_KEY="")

    with pytest.raises(SettingsError, match="CLOUDWAYS_API_KEY"):
        Settings.from_env(env)


def test_settings_allows_missing_telegram_values_when_disabled() -> None:
    settings = Settings.from_env(
        valid_env(
            TELEGRAM_ENABLED="false",
            TELEGRAM_BOT_TOKEN="",
            TELEGRAM_CHAT_ID="",
        )
    )

    assert settings.telegram_enabled is False
    assert settings.telegram_bot_token == ""
    assert settings.telegram_chat_id == ""


def test_public_config_summary_redacts_secrets() -> None:
    settings = Settings.from_env(valid_env())

    summary = settings.public_summary()

    assert summary["cloudways_email"] == "owner@example.com"
    assert summary["cloudways_api_key_configured"] is True
    assert summary["telegram_enabled"] is True
    assert "cloudways-key" not in str(summary)
    assert "telegram-token" not in str(summary)
    assert "a-session-secret" not in str(summary)

def test_env_example_documents_valid_settings() -> None:
    env = {}
    with open(".env.example", encoding="utf-8") as example:
        for line in example:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, value = stripped.split("=", maxsplit=1)
            env[key] = value

    settings = Settings.from_env(env)

    assert settings.sqlite_path == "/data/cloudways-monitor.sqlite3"
