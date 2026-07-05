import json

import httpx

from cloudways_monitor.alerts import AlertNotification, TelegramNotifier
from cloudways_monitor.settings import Settings
from tests.helpers import valid_env


def test_telegram_notifier_sends_alert_message(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    settings = Settings.from_env(
        valid_env(SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"))
    )
    notifier = TelegramNotifier(
        settings=settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    notification = AlertNotification(
        resource_id=1,
        resource_type="server",
        resource_provider_id="123",
        resource_name="production",
        rule_key="cpu_percent",
        severity="critical",
        value=98.0,
        threshold=95.0,
        breach_count=3,
        dashboard_url="https://monitor.example.com/resources/server/123",
    )

    notifier.send_alert(notification)

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == (
        "https://api.telegram.org/bottelegram-token/sendMessage"
    )
    assert json.loads(requests[0].content) == {
        "chat_id": "telegram-chat-id",
        "text": (
            "production critical cpu_percent 98.0 >= 95.0 for 3 polls "
            "https://monitor.example.com/resources/server/123"
        ),
    }


def test_telegram_notifier_is_noop_when_disabled(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("disabled Telegram notifier should not send requests")

    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            TELEGRAM_ENABLED="false",
            TELEGRAM_BOT_TOKEN="",
            TELEGRAM_CHAT_ID="",
        )
    )
    notifier = TelegramNotifier(
        settings=settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    notification = AlertNotification(
        resource_id=1,
        resource_type="server",
        resource_provider_id="123",
        resource_name="production",
        rule_key="cpu_percent",
        severity="critical",
        value=98.0,
        threshold=95.0,
        breach_count=3,
        dashboard_url="https://monitor.example.com/resources/server/123",
    )

    notifier.send_alert(notification)
