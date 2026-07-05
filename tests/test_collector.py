from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from cloudways_monitor.alerts import AlertEvaluator, AlertNotification
from cloudways_monitor.app import create_app
from cloudways_monitor.collector import TelemetryCollector
from cloudways_monitor.cloudways import CloudwaysApiError
from cloudways_monitor.settings import Settings
from cloudways_monitor.storage import Database, MetricSnapshot, Storage
from tests.helpers import login, valid_env


class MutableClock:
    def __init__(self, current_time: datetime) -> None:
        self.current_time = current_time

    def now(self) -> datetime:
        return self.current_time


class FakeTelemetrySource:
    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {
                "provider_id": "123",
                "resource_type": "server",
                "name": "production",
                "parent_provider_id": None,
                "raw": {"id": 123, "label": "production"},
            },
            {
                "provider_id": "999",
                "resource_type": "server",
                "name": "ignored",
                "parent_provider_id": None,
                "raw": {"id": 999, "label": "ignored"},
            },
        ]

    def list_applications(self) -> list[dict[str, Any]]:
        return [
            {
                "provider_id": "987",
                "resource_type": "application",
                "name": "storefront",
                "parent_provider_id": "123",
                "raw": {"id": 987, "server_id": 123, "app_label": "storefront"},
            },
            {
                "provider_id": "654",
                "resource_type": "application",
                "name": "ignored-app",
                "parent_provider_id": "999",
                "raw": {"id": 654, "server_id": 999, "app_label": "ignored-app"},
            },
        ]

    def get_server_metrics(self, server_id: str) -> dict[str, Any]:
        assert server_id == "123"
        return {
            "cpu_percent": 42.5,
            "ram_used_mb": 1024,
            "ram_total_mb": 2048,
            "disk_used_gb": 30,
            "disk_total_gb": 100,
            "bandwidth_bytes": 5000,
            "php_metric": {"workers": 3},
            "mysql_metric": {"threads_connected": 2},
        }

    def get_application_metrics(
        self,
        application_id: str,
        server_id: str | None,
    ) -> dict[str, Any]:
        assert application_id == "987"
        assert server_id == "123"
        return {
            "disk_used_gb": 2,
            "disk_percent": 0,
            "traffic_requests": 120,
            "bandwidth_bytes": 8000,
            "php_metric": {"slow_requests": 1},
        }


class HighCpuTelemetrySource(FakeTelemetrySource):
    def get_server_metrics(self, server_id: str) -> dict[str, Any]:
        assert server_id == "123"
        return {
            "cpu_percent": 96.0,
            "ram_used_mb": 1024,
            "ram_total_mb": 2048,
            "disk_used_gb": 30,
            "disk_total_gb": 100,
        }


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[AlertNotification] = []

    def send_alert(self, notification: AlertNotification) -> None:
        self.sent.append(notification)


class FailingTelemetrySource(FakeTelemetrySource):
    def __init__(self) -> None:
        self.fail_discovery = False

    def list_servers(self) -> list[dict[str, Any]]:
        if self.fail_discovery:
            raise CloudwaysApiError(
                "Cloudways API rate limit exceeded",
                status_code=429,
                code="rate_limited",
                detail={"message": "too many requests"},
            )
        return super().list_servers()


def test_collector_run_once_discovers_allowed_resources_and_stores_snapshots(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="123",
            MONITORED_APP_IDS="987",
        )
    )
    storage = make_storage(settings)
    old_resource_id = storage.upsert_resource(
        provider_id="old-server",
        resource_type="server",
        name="old-server",
        parent_provider_id=None,
        raw={"id": "old-server"},
        discovered_at=now - timedelta(days=60),
    )
    storage.insert_metric_snapshot(
        MetricSnapshot(
            resource_id=old_resource_id,
            resource_type="server",
            captured_at=now - timedelta(days=45),
            cpu_percent=91.0,
            ram_used_mb=None,
            ram_total_mb=None,
            ram_percent=None,
            disk_used_gb=None,
            disk_total_gb=None,
            disk_percent=None,
            bandwidth_bytes=None,
            traffic_requests=None,
            php_metric={},
            mysql_metric={},
            raw_payload={"cpu_percent": 91.0},
            collection_status="ok",
            error_code=None,
        )
    )
    collector = TelemetryCollector(
        settings=settings,
        storage=storage,
        telemetry_source=FakeTelemetrySource(),
        clock=MutableClock(now),
    )

    health = collector.run_once()

    resources = {
        resource.provider_id: resource for resource in storage.list_resources()
    }
    assert set(resources) == {"123", "987", "old-server"}
    assert health.status == "ok"
    assert health.servers_discovered == 1
    assert health.applications_discovered == 1
    assert health.snapshots_stored == 2
    assert health.snapshots_expired == 1

    server_snapshot = storage.get_latest_metric_snapshot(resources["123"].id)
    app_snapshot = storage.get_latest_metric_snapshot(resources["987"].id)
    old_snapshot = storage.get_latest_metric_snapshot(old_resource_id)

    assert server_snapshot is not None
    assert server_snapshot.captured_at == now
    assert server_snapshot.cpu_percent == 42.5
    assert server_snapshot.ram_percent == 50.0
    assert server_snapshot.disk_percent == 30.0
    assert server_snapshot.bandwidth_bytes == 5000
    assert server_snapshot.php_metric == {"workers": 3}
    assert server_snapshot.mysql_metric == {"threads_connected": 2}
    assert app_snapshot is not None
    assert app_snapshot.cpu_percent is None
    assert app_snapshot.disk_used_gb == 2.0
    assert app_snapshot.disk_percent == 0.0
    assert app_snapshot.traffic_requests == 120
    assert app_snapshot.bandwidth_bytes == 8000
    assert app_snapshot.php_metric == {"slow_requests": 1}
    assert old_snapshot is None


def test_collector_run_once_evaluates_alerts_for_stored_snapshots(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="123",
            ALERT_CONSECUTIVE_POLLS="3",
        )
    )
    storage = make_storage(settings)
    notifier = FakeNotifier()
    evaluator = AlertEvaluator(
        settings=settings,
        storage=storage,
        notifier=notifier,
    )
    clock = MutableClock(now)
    collector = TelemetryCollector(
        settings=settings,
        storage=storage,
        telemetry_source=HighCpuTelemetrySource(),
        clock=clock,
        alert_evaluator=evaluator,
    )

    for index in range(3):
        clock.current_time = now + timedelta(minutes=index)
        collector.run_once()

    states = storage.list_alert_states(status="active")

    assert len(states) == 1
    assert states[0].rule_key == "cpu_percent"
    assert states[0].severity == "critical"
    assert states[0].consecutive_breaches == 3
    assert [notification.resource_name for notification in notifier.sent] == [
        "production"
    ]


def test_collector_failure_preserves_last_known_snapshots_and_marks_stale(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    failure_time = now + timedelta(minutes=4)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="123",
            MONITORED_APP_IDS="987",
        )
    )
    storage = make_storage(settings)
    source = FailingTelemetrySource()
    clock = MutableClock(now)
    collector = TelemetryCollector(
        settings=settings,
        storage=storage,
        telemetry_source=source,
        clock=clock,
    )

    collector.run_once()
    source.fail_discovery = True
    clock.current_time = failure_time
    health = collector.run_once()

    resources = {
        resource.provider_id: resource for resource in storage.list_resources()
    }
    server_snapshots = storage.list_metric_snapshots(
        resource_id=resources["123"].id,
        start=now - timedelta(minutes=1),
        end=failure_time + timedelta(minutes=1),
    )

    assert health.status == "degraded"
    assert health.last_run_at == failure_time
    assert health.last_success_at == now
    assert health.stale is True
    assert health.snapshots_stored == 0
    assert health.last_error_code == "rate_limited"
    assert len(server_snapshots) == 1
    assert server_snapshots[0].captured_at == now
    assert server_snapshots[0].cpu_percent == 42.5


def test_collector_health_endpoint_exposes_current_state(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="123",
            MONITORED_APP_IDS="987",
        )
    )
    storage = make_storage(settings)
    collector = TelemetryCollector(
        settings=settings,
        storage=storage,
        telemetry_source=FakeTelemetrySource(),
        clock=MutableClock(now),
    )
    collector.run_once()
    client = TestClient(
        create_app(settings=settings, telemetry_collector=collector),
        base_url="https://testserver",
    )
    login(client)

    response = client.get("/api/collector/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "last_run_at": now.isoformat(),
        "last_success_at": now.isoformat(),
        "servers_discovered": 1,
        "applications_discovered": 1,
        "snapshots_stored": 2,
        "snapshots_expired": 0,
        "stale": False,
        "last_error_code": None,
        "last_error": None,
    }


def test_collector_health_endpoint_marks_aged_data_stale(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="123",
            MONITORED_APP_IDS="987",
        )
    )
    storage = make_storage(settings)
    clock = MutableClock(now)
    collector = TelemetryCollector(
        settings=settings,
        storage=storage,
        telemetry_source=FakeTelemetrySource(),
        clock=clock,
    )
    collector.run_once()
    clock.current_time = now + timedelta(minutes=4)
    client = TestClient(
        create_app(settings=settings, telemetry_collector=collector),
        base_url="https://testserver",
    )
    login(client)

    response = client.get("/api/collector/health")

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "degraded"
    assert payload["last_success_at"] == now.isoformat()
    assert payload["stale"] is True
    assert payload["last_error_code"] is None


def test_collector_start_and_stop_control_polling_loop(tmp_path) -> None:
    now = datetime(2026, 7, 5, 1, 0, tzinfo=UTC)
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            MONITORED_SERVER_IDS="123",
            MONITORED_APP_IDS="987",
        )
    )
    storage = make_storage(settings)
    collector = TelemetryCollector(
        settings=settings,
        storage=storage,
        telemetry_source=FakeTelemetrySource(),
        clock=MutableClock(now),
    )

    collector.start(run_immediately=False)
    running_after_start = collector.is_running
    collector.stop()

    assert running_after_start is True
    assert collector.is_running is False


def make_storage(settings: Settings) -> Storage:
    database = Database(settings.sqlite_path)
    database.migrate()
    return Storage(database)
