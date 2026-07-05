from datetime import UTC, datetime, timedelta

from cloudways_monitor.alerts import AlertEvaluator, AlertNotification
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, MetricSnapshot, Storage
from tests.helpers import valid_env


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[AlertNotification] = []

    def send_alert(self, notification: AlertNotification) -> None:
        self.sent.append(notification)


def test_alert_evaluator_opens_critical_cpu_alert_after_sustained_breach(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            ALERT_CONSECUTIVE_POLLS="3",
        )
    )
    storage = make_storage(settings)
    resource_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production",
        parent_provider_id=None,
        raw={"id": 123, "label": "production"},
        discovered_at=now,
    )
    notifier = FakeNotifier()
    evaluator = AlertEvaluator(
        settings=settings,
        storage=storage,
        notifier=notifier,
    )

    for index, cpu_percent in enumerate((96.0, 97.0, 98.0)):
        captured_at = now + timedelta(minutes=index)
        snapshot = make_snapshot(
            resource_id=resource_id,
            captured_at=captured_at,
            cpu_percent=cpu_percent,
        )
        storage.insert_metric_snapshot(snapshot)
        evaluator.evaluate_snapshot(snapshot)

    states = storage.list_alert_states()
    events = storage.list_alert_events()

    assert len(states) == 1
    assert states[0].resource_id == resource_id
    assert states[0].rule_key == "cpu_percent"
    assert states[0].status == "active"
    assert states[0].severity == "critical"
    assert states[0].consecutive_breaches == 3
    assert states[0].opened_at == now + timedelta(minutes=2)
    assert states[0].last_notification_at == now + timedelta(minutes=2)
    assert [event.event_type for event in events] == ["opened"]
    assert events[0].message == (
        "production critical cpu_percent 98.0 >= 95.0 for 3 polls "
        "https://monitor.example.com/resources/server/123"
    )
    assert notifier.sent == [
        AlertNotification(
            resource_id=resource_id,
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
    ]


def test_alert_evaluator_opens_ram_warning_and_disk_critical_alerts(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            ALERT_CONSECUTIVE_POLLS="1",
        )
    )
    storage = make_storage(settings)
    resource_id = storage.upsert_resource(
        provider_id="987",
        resource_type="application",
        name="storefront",
        parent_provider_id="123",
        raw={"id": 987, "app_label": "storefront"},
        discovered_at=now,
    )
    notifier = FakeNotifier()
    evaluator = AlertEvaluator(
        settings=settings,
        storage=storage,
        notifier=notifier,
    )
    snapshot = make_snapshot(
        resource_id=resource_id,
        captured_at=now,
        ram_percent=82.0,
        disk_percent=91.0,
    )

    storage.insert_metric_snapshot(snapshot)
    evaluator.evaluate_snapshot(snapshot)

    states = {
        state.rule_key: state for state in storage.list_alert_states(status="active")
    }

    assert set(states) == {"ram_percent", "disk_percent"}
    assert states["ram_percent"].severity == "warning"
    assert states["disk_percent"].severity == "critical"
    assert [notification.rule_key for notification in notifier.sent] == [
        "ram_percent",
        "disk_percent",
    ]
    assert notifier.sent[0].dashboard_url == (
        "https://monitor.example.com/resources/application/987"
    )


def test_alert_evaluator_resolves_and_reopens_alerts(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            ALERT_CONSECUTIVE_POLLS="2",
        )
    )
    storage = make_storage(settings)
    resource_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production",
        parent_provider_id=None,
        raw={"id": 123, "label": "production"},
        discovered_at=now,
    )
    notifier = FakeNotifier()
    evaluator = AlertEvaluator(
        settings=settings,
        storage=storage,
        notifier=notifier,
    )

    for index, cpu_percent in enumerate((83.0, 84.0, 42.0, 96.0, 97.0)):
        captured_at = now + timedelta(minutes=index)
        snapshot = make_snapshot(
            resource_id=resource_id,
            captured_at=captured_at,
            cpu_percent=cpu_percent,
        )
        storage.insert_metric_snapshot(snapshot)
        evaluator.evaluate_snapshot(snapshot)

    state = storage.get_alert_state(
        resource_id=resource_id,
        rule_key="cpu_percent",
    )
    events = storage.list_alert_events()

    assert state is not None
    assert state.status == "active"
    assert state.severity == "critical"
    assert state.consecutive_breaches == 2
    assert state.opened_at == now + timedelta(minutes=4)
    assert state.resolved_at is None
    assert [event.event_type for event in events] == [
        "opened",
        "resolved",
        "opened",
    ]
    assert len(notifier.sent) == 2
    assert notifier.sent[0].severity == "warning"
    assert notifier.sent[1].severity == "critical"


def test_alert_evaluator_suppresses_cooldown_and_notifies_on_severity_change(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            ALERT_CONSECUTIVE_POLLS="1",
            ALERT_COOLDOWN_SECONDS="1800",
        )
    )
    storage = make_storage(settings)
    resource_id = storage.upsert_resource(
        provider_id="123",
        resource_type="server",
        name="production",
        parent_provider_id=None,
        raw={"id": 123, "label": "production"},
        discovered_at=now,
    )
    notifier = FakeNotifier()
    evaluator = AlertEvaluator(
        settings=settings,
        storage=storage,
        notifier=notifier,
    )

    for offset_minutes, cpu_percent in ((0, 82.0), (5, 84.0), (10, 96.0)):
        captured_at = now + timedelta(minutes=offset_minutes)
        snapshot = make_snapshot(
            resource_id=resource_id,
            captured_at=captured_at,
            cpu_percent=cpu_percent,
        )
        storage.insert_metric_snapshot(snapshot)
        evaluator.evaluate_snapshot(snapshot)

    state = storage.get_alert_state(
        resource_id=resource_id,
        rule_key="cpu_percent",
    )
    events = storage.list_alert_events()

    assert state is not None
    assert state.status == "active"
    assert state.severity == "critical"
    assert state.last_notification_at == now + timedelta(minutes=10)
    assert [event.event_type for event in events] == [
        "opened",
        "severity_changed",
    ]
    assert [notification.severity for notification in notifier.sent] == [
        "warning",
        "critical",
    ]
    assert notifier.sent[1].threshold == 95.0


def make_storage(settings: Settings) -> Storage:
    database = Database(settings.sqlite_path)
    database.migrate()
    return Storage(database)


def make_snapshot(
    *,
    resource_id: int,
    captured_at: datetime,
    cpu_percent: float | None = None,
    ram_percent: float | None = None,
    disk_percent: float | None = None,
) -> MetricSnapshot:
    return MetricSnapshot(
        resource_id=resource_id,
        resource_type="server",
        captured_at=captured_at,
        cpu_percent=cpu_percent,
        ram_used_mb=None,
        ram_total_mb=None,
        ram_percent=ram_percent,
        disk_used_gb=None,
        disk_total_gb=None,
        disk_percent=disk_percent,
        bandwidth_bytes=None,
        traffic_requests=None,
        php_metric={},
        mysql_metric={},
        raw_payload={},
        collection_status="ok",
        error_code=None,
    )
