from fastapi.testclient import TestClient

AUTH_PASSWORD = "correct-password"
AUTH_PASSWORD_HASH = (
    "pbkdf2_sha256$100000$testsalt$UYD0wkzOSvbNmj2QVVu1KD-huSLvgW5ND4OieCuF9a0"
)


def valid_env(**overrides: str) -> dict[str, str]:
    values = {
        "APP_ENV": "production",
        "APP_HOST": "0.0.0.0",
        "APP_PORT": "8000",
        "DASHBOARD_BASE_URL": "https://monitor.example.com",
        "SQLITE_PATH": "/data/cloudways-monitor.sqlite3",
        "POLL_INTERVAL_SECONDS": "60",
        "STALE_AFTER_SECONDS": "180",
        "RETENTION_DAYS": "30",
        "CLOUDWAYS_EMAIL": "owner@example.com",
        "CLOUDWAYS_API_KEY": "cloudways-key",
        "CLOUDWAYS_API_BASE_URL": "https://api.cloudways.com/api/v1",
        "MONITORED_SERVER_IDS": "123,456",
        "MONITORED_APP_IDS": "",
        "DASHBOARD_USERNAME": "admin",
        "DASHBOARD_PASSWORD_HASH": AUTH_PASSWORD_HASH,
        "SESSION_SECRET": "a-session-secret-with-enough-entropy",
        "SESSION_COOKIE_SECURE": "true",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "telegram-chat-id",
        "TELEGRAM_ENABLED": "true",
        "CPU_WARNING_PERCENT": "80",
        "CPU_CRITICAL_PERCENT": "95",
        "RAM_WARNING_PERCENT": "80",
        "RAM_CRITICAL_PERCENT": "95",
        "DISK_WARNING_PERCENT": "80",
        "DISK_CRITICAL_PERCENT": "90",
        "ALERT_CONSECUTIVE_POLLS": "3",
        "ALERT_COOLDOWN_SECONDS": "1800",
    }
    values.update(overrides)
    return values


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": AUTH_PASSWORD},
    )
    assert response.status_code == 200
