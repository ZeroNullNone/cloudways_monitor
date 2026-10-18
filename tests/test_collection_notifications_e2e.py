"""Failure scenarios, written before implementation:

Transient diagnostics must not notify; persistent issues must identify server,
application, metric and error; recovery/reopening and process restart must not
bypass cooldown; authentication must notify immediately; long messages must
remain deliverable; delivery failure must retain the outbox for retry.
Uses the real collector, Cloudways adapter, SQLite and Telegram HTTP capture.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from tests.test_redesign_e2e import make_system


class Clock:
    def __init__(self):
        self.value = datetime.now(UTC)

    def now(self):
        return self.value


def test_collection_details_and_quiet_reopening(tmp_path):
    _, collector, source, storage, upstream, telegram = make_system(tmp_path)
    clock = Clock()
    collector._clock = clock
    source._clock = lambda: clock.value.timestamp()
    # Keep resource threshold alerts outside this collection-health scenario.
    collector._alerts._settings = replace(collector._settings, alert_rules=())

    def run():
        clock.value += timedelta(seconds=60)
        upstream.timestamp = int(clock.value.timestamp())
        collector.run_once()

    upstream.app_error = True
    for _ in range(9):
        run()
    assert telegram == [], "Nonurgent collection diagnostics notified before 10 cycles"
    run()
    assert len(telegram) == 1
    message = telegram[0]["text"]
    assert "Collection needs attention" in message
    assert "Server 1" in message and "App 1" in message
    assert "Disk [error]" in message and "Cloudways HTTP 403" in message
    assert len(message) <= 4096

    # Simulate an entirely healthy round, without waiting for upstream caches.
    source.get_server_metrics = lambda _: {}
    source.get_application_metrics = lambda *_: {}
    run()
    assert "Recovered: collection" in telegram[-1]["text"]
    count = len(telegram)
    source.get_server_metrics = lambda _: {
        "diagnostics": [
            dict(metric="Capacity", status="warning", message="Unavailable")
        ]
    }
    for _ in range(10):
        run()
    assert len(telegram) == count, "Recovery bypassed the collection cooldown"
    # Fresh evaluator reads persisted cooldown, as after process restart.
    from cloudways_monitor.alerts import AlertEvaluator

    collector._alerts = AlertEvaluator(
        settings=collector._settings,
        storage=storage,
        notifier=collector._alerts._notifier,
    )
    clock.value += timedelta(hours=6)
    run()
    assert len(telegram) == count + 1
    assert "Capacity [warning]" in telegram[-1]["text"]
    assert "Server 2" in telegram[-1]["text"]
    artifact = Path(".tmp/e2e/collection-notifications.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(telegram, indent=2), encoding="utf-8")


def test_collection_message_fits_telegram_with_many_errors(tmp_path):
    _, collector, source, _, _, telegram = make_system(tmp_path)
    source.get_server_metrics = lambda _: {
        "diagnostics": [
            dict(metric="Capacity", status="warning", message="x" * 6000)
            for _ in range(30)
        ]
    }
    for _ in range(10):
        collector.run_once()
    assert len(telegram) == 1
    assert len(telegram[0]["text"]) <= 4096
    assert "omitted" in telegram[0]["text"]
    assert "https://monitor.example.com" in telegram[0]["text"]
