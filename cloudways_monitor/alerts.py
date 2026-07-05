from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import httpx

from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import (
    AlertState,
    MetricSnapshot,
    MonitoredResource,
    Storage,
)

Severity = Literal["warning", "critical"]


class AlertNotifier(Protocol):
    def send_alert(self, notification: "AlertNotification") -> None: ...


@dataclass(frozen=True)
class AlertNotification:
    resource_id: int
    resource_type: str
    resource_provider_id: str
    resource_name: str
    rule_key: str
    severity: Severity
    value: float
    threshold: float
    breach_count: int
    dashboard_url: str

    def message(self) -> str:
        return (
            f"{self.resource_name} {self.severity} {self.rule_key} "
            f"{self.value} >= {self.threshold} for {self.breach_count} polls "
            f"{self.dashboard_url}"
        )


class TelegramNotificationError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.Client | None = None,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._settings = settings
        self._http_client = http_client or httpx.Client(timeout=10.0)
        self._api_base_url = api_base_url.rstrip("/")

    def send_alert(self, notification: AlertNotification) -> None:
        if not self._settings.telegram_enabled:
            return

        response = self._http_client.post(
            f"{self._api_base_url}/bot{self._settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": self._settings.telegram_chat_id,
                "text": notification.message(),
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TelegramNotificationError(
                f"Telegram sendMessage failed with HTTP {response.status_code}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramNotificationError(
                "Telegram sendMessage returned invalid JSON"
            ) from exc
        if payload.get("ok") is not True:
            description = payload.get("description", "unknown error")
            raise TelegramNotificationError(
                f"Telegram sendMessage failed: {description}"
            )


class AlertEvaluator:
    def __init__(
        self,
        *,
        settings: Settings,
        storage: Storage,
        notifier: AlertNotifier,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._notifier = notifier

    def evaluate_snapshot(self, snapshot: MetricSnapshot) -> None:
        resource = self._storage.get_resource(snapshot.resource_id)
        if resource is None:
            raise ValueError(f"resource {snapshot.resource_id} does not exist")

        self._evaluate_rule(
            resource=resource,
            snapshot=snapshot,
            rule_key="cpu_percent",
            value=snapshot.cpu_percent,
            warning_threshold=float(self._settings.cpu_warning_percent),
            critical_threshold=float(self._settings.cpu_critical_percent),
        )
        self._evaluate_rule(
            resource=resource,
            snapshot=snapshot,
            rule_key="ram_percent",
            value=snapshot.ram_percent,
            warning_threshold=float(self._settings.ram_warning_percent),
            critical_threshold=float(self._settings.ram_critical_percent),
        )
        self._evaluate_rule(
            resource=resource,
            snapshot=snapshot,
            rule_key="disk_percent",
            value=snapshot.disk_percent,
            warning_threshold=float(self._settings.disk_warning_percent),
            critical_threshold=float(self._settings.disk_critical_percent),
        )

    def _evaluate_rule(
        self,
        *,
        resource: MonitoredResource,
        snapshot: MetricSnapshot,
        rule_key: str,
        value: float | None,
        warning_threshold: float,
        critical_threshold: float,
    ) -> None:
        if value is None:
            return

        previous = self._storage.get_alert_state(
            resource_id=resource.id,
            rule_key=rule_key,
        )
        breach = _breach(
            value=value,
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold,
        )
        if breach is None:
            self._resolve_rule_if_needed(
                resource=resource,
                snapshot=snapshot,
                rule_key=rule_key,
                value=value,
                previous=previous,
            )
            return

        severity, threshold = breach
        consecutive_breaches = 1
        if previous is not None and previous.status in {"pending", "active"}:
            consecutive_breaches = previous.consecutive_breaches + 1

        should_open = (
            consecutive_breaches >= self._settings.alert_consecutive_polls
            and (previous is None or previous.status != "active")
        )
        opened_at = previous.opened_at if previous is not None else None
        last_notification_at = (
            previous.last_notification_at if previous is not None else None
        )
        status = "pending"
        if should_open:
            status = "active"
            opened_at = snapshot.captured_at
            notification = AlertNotification(
                resource_id=resource.id,
                resource_type=resource.resource_type,
                resource_provider_id=resource.provider_id,
                resource_name=resource.name,
                rule_key=rule_key,
                severity=severity,
                value=value,
                threshold=threshold,
                breach_count=consecutive_breaches,
                dashboard_url=_dashboard_url(self._settings, resource),
            )
            self._notifier.send_alert(notification)
            last_notification_at = snapshot.captured_at
            self._storage.insert_alert_event(
                resource_id=resource.id,
                rule_key=rule_key,
                event_type="opened",
                severity=severity,
                message=notification.message(),
                created_at=snapshot.captured_at,
            )
        elif previous is not None and previous.status == "active":
            status = "active"
            severity_changed = previous.severity != severity
            cooldown_elapsed = _cooldown_elapsed(
                last_notification_at=previous.last_notification_at,
                captured_at=snapshot.captured_at,
                cooldown_seconds=self._settings.alert_cooldown_seconds,
            )
            if severity_changed or cooldown_elapsed:
                notification = AlertNotification(
                    resource_id=resource.id,
                    resource_type=resource.resource_type,
                    resource_provider_id=resource.provider_id,
                    resource_name=resource.name,
                    rule_key=rule_key,
                    severity=severity,
                    value=value,
                    threshold=threshold,
                    breach_count=consecutive_breaches,
                    dashboard_url=_dashboard_url(self._settings, resource),
                )
                self._notifier.send_alert(notification)
                last_notification_at = snapshot.captured_at
                self._storage.insert_alert_event(
                    resource_id=resource.id,
                    rule_key=rule_key,
                    event_type="severity_changed" if severity_changed else "notified",
                    severity=severity,
                    message=notification.message(),
                    created_at=snapshot.captured_at,
                )

        self._storage.save_alert_state(
            resource_id=resource.id,
            rule_key=rule_key,
            status=status,
            severity=severity,
            consecutive_breaches=consecutive_breaches,
            opened_at=opened_at,
            resolved_at=None,
            last_notification_at=last_notification_at,
        )

    def _resolve_rule_if_needed(
        self,
        *,
        resource: MonitoredResource,
        snapshot: MetricSnapshot,
        rule_key: str,
        value: float,
        previous: AlertState | None,
    ) -> None:
        if previous is None or previous.status not in {"pending", "active"}:
            return

        if previous.status == "active":
            self._storage.insert_alert_event(
                resource_id=resource.id,
                rule_key=rule_key,
                event_type="resolved",
                severity=previous.severity,
                message=(
                    f"{resource.name} resolved {rule_key} at {value} "
                    f"{_dashboard_url(self._settings, resource)}"
                ),
                created_at=snapshot.captured_at,
            )

        self._storage.save_alert_state(
            resource_id=resource.id,
            rule_key=rule_key,
            status="resolved",
            severity=None,
            consecutive_breaches=0,
            opened_at=None,
            resolved_at=snapshot.captured_at,
            last_notification_at=previous.last_notification_at,
        )


def _breach(
    *,
    value: float,
    warning_threshold: float,
    critical_threshold: float,
) -> tuple[Severity, float] | None:
    if value >= critical_threshold:
        return "critical", critical_threshold
    if value >= warning_threshold:
        return "warning", warning_threshold
    return None


def _cooldown_elapsed(
    *,
    last_notification_at: datetime | None,
    captured_at: datetime,
    cooldown_seconds: int,
) -> bool:
    if last_notification_at is None:
        return True
    return (captured_at - last_notification_at).total_seconds() >= cooldown_seconds


def _dashboard_url(settings: Settings, resource: MonitoredResource) -> str:
    base_url = settings.dashboard_base_url.rstrip("/")
    return f"{base_url}/resources/{resource.resource_type}/{resource.provider_id}"
