from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from cloudways_monitor.monitor import (
    DISPLAY_TIMEZONE,
    value_in_unit,
    latest_complete_point,
    sample_is_stale,
)
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import MetricSnapshot, Storage


@dataclass(frozen=True)
class AlertNotification:
    text: str

    def message(self) -> str:
        return self.text


class TelegramNotificationError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.Client | None = None,
        api_base_url="https://api.telegram.org",
    ):
        self._settings = settings
        self._http_client = http_client or httpx.Client(timeout=10.0)
        self._api_base_url = api_base_url.rstrip("/")

    def close(self):
        self._http_client.close()

    def send_alert(self, notification: AlertNotification):
        if not self._settings.telegram_enabled:
            return
        try:
            response = self._http_client.post(
                f"{self._api_base_url}/bot{self._settings.telegram_bot_token}/sendMessage",
                json=dict(
                    chat_id=self._settings.telegram_chat_id, text=notification.message()
                ),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("ok") is not True:
                raise TelegramNotificationError("Telegram rejected the notification")
        except (httpx.HTTPError, ValueError) as exc:
            # HTTP exceptions include the bot credential in their URL; never expose them.
            raise TelegramNotificationError(
                "Telegram delivery failed; notification queued for retry"
            ) from exc


class AlertEvaluator:
    def __init__(
        self, *, settings: Settings, storage: Storage, notifier: TelegramNotifier
    ):
        self._settings, self._storage, self._notifier = settings, storage, notifier

    def evaluate_snapshot(self, snapshot: MetricSnapshot):
        if snapshot.resource_type != "server":
            return
        resource = self._storage.get_resource(snapshot.resource_id)
        if resource is None:
            return
        graphs = snapshot.raw_payload.get("graphs", {})
        for rule in self._settings.alert_rules:
            previous = self._storage.get_alert_state(
                resource_id=resource.id, rule_key=rule.target
            )
            graph = graphs.get(rule.target, {})
            series = graph.get("series", [])
            value = None
            sample_at = None
            new_sample = False
            if graph.get("status") in ("ok", "warning") and len(series) == 1:
                points = series[0].get("points", [])
                last = latest_complete_point(points)
                if last:
                    sample_at = last["timestamp"]
                    if last["value"] is not None:
                        value = value_in_unit(
                            last["value"], series[0].get("unit"), rule.unit
                        )
            if sample_at:
                if sample_is_stale(
                    series[0], snapshot.captured_at, self._settings.stale_after_seconds
                ):
                    value = None
                new_sample = self._storage.claim_alert_sample(
                    resource.id, rule.target, sample_at
                )
            if value is None:
                # Unknown must not recover an active alert; a pending run loses continuity.
                if previous and previous.status == "pending":
                    self._storage.save_alert_state(
                        resource_id=resource.id,
                        rule_key=rule.target,
                        status="pending",
                        severity=None,
                        consecutive_breaches=0,
                        opened_at=None,
                        resolved_at=None,
                        last_notification_at=previous.last_notification_at,
                    )
                continue
            if not new_sample:
                continue
            severity = (
                "critical"
                if value <= rule.critical
                else "warning"
                if value <= rule.warning
                else None
            )
            if severity is None:
                if previous and previous.status in ("active", "pending"):
                    if previous.status == "active":
                        self._emit(
                            resource.id,
                            rule.target,
                            "resolved",
                            None,
                            f"Recovered: {resource.name}\n{rule.target}: {value:g} {rule.unit}",
                            snapshot.captured_at,
                            resource.provider_id,
                        )
                    self._storage.save_alert_state(
                        resource_id=resource.id,
                        rule_key=rule.target,
                        status="resolved",
                        severity=None,
                        consecutive_breaches=0,
                        opened_at=None,
                        resolved_at=snapshot.captured_at,
                        last_notification_at=previous.last_notification_at,
                    )
                continue
            count = (
                previous.consecutive_breaches
                if previous and previous.status in ("pending", "active")
                else 0
            ) + 1
            active = previous is not None and previous.status == "active"
            should_open = not active and count >= self._settings.alert_consecutive_polls
            last_notification = previous.last_notification_at if previous else None
            renotify = active and (
                previous.severity != severity
                or (
                    self._settings.telegram_enabled
                    and not self._storage.has_pending_notification(
                        resource.id, rule.target
                    )
                    and (
                        last_notification is None
                        or (snapshot.captured_at - last_notification).total_seconds()
                        >= self._settings.alert_cooldown_seconds
                    )
                )
            )
            if should_open or renotify:
                threshold = rule.critical if severity == "critical" else rule.warning
                self._emit(
                    resource.id,
                    rule.target,
                    "opened" if should_open else "notified",
                    severity,
                    f"{severity.title()}: {resource.name}\n{rule.target}: {value:g} {rule.unit} <= {threshold:g} {rule.unit}\n{count} distinct samples\nSample: {_display_time(datetime.fromisoformat(sample_at))}",
                    snapshot.captured_at,
                    resource.provider_id,
                )
            self._storage.save_alert_state(
                resource_id=resource.id,
                rule_key=rule.target,
                status="active" if active or should_open else "pending",
                severity=severity,
                consecutive_breaches=count,
                opened_at=previous.opened_at
                if active
                else snapshot.captured_at
                if should_open
                else None,
                resolved_at=None,
                last_notification_at=last_notification,
            )

    def _emit(
        self, resource_id, target, event_type, severity, message, when, provider_id
    ):
        message += f"\nTime: {_display_time(when)}\n{self._settings.dashboard_base_url.rstrip('/')}/#server-{provider_id}"
        event = self._storage.insert_alert_event(
            resource_id=resource_id,
            rule_key=target,
            event_type=event_type,
            severity=severity,
            message=message,
            created_at=when,
        )
        if self._settings.telegram_enabled:
            self._storage.queue_notification(event.id, message)

    def flush_notifications(self):
        if not self._settings.telegram_enabled:
            return
        for event_id, message in self._storage.pending_notifications():
            self._notifier.send_alert(AlertNotification(message))
            self._storage.mark_notification_delivered(event_id)

    def evaluate_health(
        self, error: str | None, when: datetime, *, authentication_failed=False
    ):
        state = self._storage.get_collector_alert()
        if error:
            state["failures"] += 1
            last = (
                datetime.fromisoformat(state["last_notification_at"])
                if state["last_notification_at"]
                else None
            )
            due = (
                last is None
                or (when - last).total_seconds()
                >= self._settings.alert_cooldown_seconds
            )
            if (
                authentication_failed
                or state["failures"] >= self._settings.alert_consecutive_polls
            ) and (not state["active"] or due):
                message = f"Collection needs attention\n{error}\nTime: {_display_time(when)}\n{self._settings.dashboard_base_url.rstrip('/')}"
                if self._settings.telegram_enabled:
                    self._storage.queue_notification(None, message)
                state.update(active=True, last_notification_at=when.isoformat())
        else:
            if state["active"] and self._settings.telegram_enabled:
                self._storage.queue_notification(
                    None,
                    f"Recovered: collection\nTime: {_display_time(when)}\n{self._settings.dashboard_base_url.rstrip('/')}",
                )
            state.update(failures=0, active=False)
        self._storage.save_collector_alert(state)

    def interrupt_pending_samples(self):
        for alert in self._storage.list_alert_states(status="pending"):
            self._storage.save_alert_state(
                resource_id=alert.resource_id,
                rule_key=alert.rule_key,
                status="pending",
                severity=None,
                consecutive_breaches=0,
                opened_at=None,
                resolved_at=None,
                last_notification_at=alert.last_notification_at,
            )


def _display_time(value: datetime) -> str:
    return value.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S UTC+8")
