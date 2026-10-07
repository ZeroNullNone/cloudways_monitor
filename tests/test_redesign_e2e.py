"""End-to-end contracts written before the redesign implementation.

Failures: OAuth called; missing/secret token leaked; pagination truncated;
deleted resources retained; failed app blocks siblings; pending/failed task
treated as completed; null filled with zero or reused outside its window; zero discarded;
graph selection cached under wrong duration; unknown schema/units guessed;
same sample opens an alert; missing sample resolves it; recovery not notified;
rate limits retried immediately; unauthenticated graph triggers upstream calls.
"""

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cloudways_monitor.alerts import AlertEvaluator, TelegramNotifier
from cloudways_monitor.app import create_app
from cloudways_monitor.cloudways import CloudwaysClient
from cloudways_monitor.collector import TelemetryCollector
from cloudways_monitor.settings import Settings, SettingsError
from cloudways_monitor.storage import Database, Storage
from tests.helpers import AUTH_PASSWORD_HASH, login

TARGETS = [
    "Idle CPU",
    "Free Disk",
    "Reads per second",
    "Writes per second",
    "Free memory",
    "Incoming network traffic",
    "Outgoing network traffic",
    "APC Fill Ratio",
    "APC Hit rate",
    "Monthly Bandwidth",
    "Memcached Fill Ratio",
    "Memcached Hit Rate",
    "Varnish Hit Rate",
    "Varnish Nuked",
    "Auto-healing Restarts",
]
DURATIONS = ["1 Hour", "12 Hours", "1 Day", "7 Days", "1 Month", "6 Months"]


class Upstream:
    def __init__(self):
        self.calls = []
        self.latest = 50.0
        self.timestamp = int(datetime.now(UTC).timestamp())
        self.pending = False
        self.failed = False
        self.app_error = False
        self.no_units = False
        self.removed = False
        self.status_code = 200
        self.graph_format = "graphite"

    def handle(self, request):
        assert request.headers["Authorization"] == "Bearer test-access-token"
        path = request.url.path.removeprefix("/api/v2")
        params = dict(request.url.params)
        self.calls.append((path, params))
        assert "/oauth/" not in path and path != "/server"
        if self.status_code != 200:
            return httpx.Response(
                self.status_code,
                json={"message": "denied"},
                headers={"Retry-After": "60"},
            )
        if path == "/monitor_targets":
            payload = {"targets": {"do": TARGETS, "amazon": ["Other"]}}
        elif path == "/monitor_durations":
            payload = {"durations": DURATIONS}
        elif path in ("/servers", "/apps/flexible"):
            page = int(params["page"])
            is_server = path == "/servers"
            items = (
                []
                if self.removed and page == 2
                else [
                    {
                        "id": str(page),
                        "label": f"{'Server' if is_server else 'App'} {page}",
                        "cloud": "do",
                        "region": "sgp1",
                        "server_id": str(page),
                        "app_fqdn": f"app{page}.example.com",
                        "sys_user": f"app{page}",
                        "master_password": "NEVER-PERSIST",
                    }
                ]
            )
            payload = {
                "data": {
                    "servers" if is_server else "apps": items,
                    "pagination": {"current_page": page, "last_page": 2},
                }
            }
        elif path in ("/servers/1", "/servers/2"):
            payload = dict(
                id=int(path.rsplit("/", 1)[1]),
                cloud="do",
                instance_type="2GB",
                volume_size=50,
                storage=None,
            )
        elif path == "/server/monitor/detail":
            assert params["timezone"] == "Asia/Singapore"
            assert params["output_format"] == "json"
            payload = {
                "status": True,
                "task_id": params["server_id"]
                + ":"
                + params["target"]
                + ":"
                + params["duration"],
            }
        elif path.startswith("/operation/"):
            target = path.split(":")[1]
            if self.pending:
                payload = {"operation": {"is_completed": "0"}}
            elif self.failed:
                payload = {
                    "operation": {"is_completed": "-1", "message": "task failed"}
                }
            elif target.startswith("analysis_"):
                key = target.removeprefix("analysis_")
                tables = {
                    "top_urls": {
                        "header": ["URL", "Count"],
                        "body": [["/checkout", 45], ["/", 0]],
                    },
                    "slow_pages": {
                        "header": ["URL", "Occurrences", "Max Time", "Avg. Duration"],
                        "body": [],
                    },
                    "slow_queries": {
                        "header": ["Query", "Execution Time", "Occurrences"],
                        "body": [["SELECT demo", 6.5, 2]],
                    },
                }
                payload = {
                    "operation": {
                        "is_completed": "1",
                        "parameters": json.dumps({key: tables[key]}),
                    }
                }
            elif target == "traffic":
                payload = {
                    "operation": {
                        "is_completed": "1",
                        "result": {"total_requests": 0, "complete": True},
                    }
                }
            else:
                unit = (
                    "%"
                    if target == "Idle CPU" or "Ratio" in target or "Rate" in target
                    else "MB"
                )
                graph = {
                    "target": target,
                    "datapoints": [
                        [70, self.timestamp - 120],
                        [None, self.timestamp - 60],
                        [self.latest, self.timestamp],
                    ],
                }
                if not self.no_units:
                    graph["unit"] = unit
                result = [graph]
                if self.graph_format == "invalid":
                    result = {"numbers": [1, 2, 3]}
                payload = {
                    "operation": {"is_completed": "1", "result": json.dumps(result)}
                }
        elif path == "/server/monitor/summary":
            assert params["type"] == "db"
            if self.app_error and params["server_id"] == "1":
                return httpx.Response(403, json={"message": "app forbidden"})
            payload = {
                "content": [
                    {
                        "name": f"app{params['server_id']}",
                        "datapoint": [2500, self.timestamp],
                        "type": "apps",
                    }
                ]
            }
        elif path in (
            "/app/analytics/traffic",
            "/app/analytics/php",
            "/app/analytics/mysql",
        ):
            key = (
                "traffic"
                if params["resource"] == "top_statuses"
                else "analysis_" + params["resource"]
            )
            payload = {
                "task_id": params["app_id"] + ":" + key + ":" + params["duration"],
                "status": True,
            }
        else:
            raise AssertionError(f"Unexpected upstream endpoint {path}")
        return httpx.Response(200, json=payload)


def make_system(tmp_path):
    settings = Settings.from_env(
        {
            "DASHBOARD_BASE_URL": "https://monitor.example.com",
            "SQLITE_PATH": str(tmp_path / "e2e.sqlite3"),
            "CLOUDWAYS_ACCESS_TOKEN": "test-access-token",
            "DASHBOARD_USERNAME": "admin",
            "DASHBOARD_PASSWORD_HASH": AUTH_PASSWORD_HASH,
            "SESSION_SECRET": "a-session-secret-with-enough-entropy",
            "SESSION_COOKIE_SECURE": "false",
            "TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "test-bot",
            "TELEGRAM_CHAT_ID": "test-chat",
            "ALERT_CONSECUTIVE_POLLS": "3",
        }
    )
    db = Database(settings.sqlite_path)
    db.migrate()
    storage = Storage(db)
    upstream = Upstream()
    source = CloudwaysClient(
        settings,
        httpx.Client(
            base_url=settings.cloudways_api_base_url,
            transport=httpx.MockTransport(upstream.handle),
        ),
    )
    telegram = []

    def send(request):
        telegram.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier(
        settings=settings, http_client=httpx.Client(transport=httpx.MockTransport(send))
    )
    evaluator = AlertEvaluator(settings=settings, storage=storage, notifier=notifier)
    collector = TelemetryCollector(
        settings=settings,
        storage=storage,
        telemetry_source=source,
        alert_evaluator=evaluator,
    )
    client = TestClient(
        create_app(
            settings=settings,
            storage=storage,
            cloudways_client=source,
            telemetry_collector=collector,
        ),
        base_url="http://testserver",
    )
    return client, collector, source, storage, upstream, telegram


def graph(client, source, resource_id, target="Idle CPU", duration="1 Hour"):
    source.invalidate_graph_cache()
    return client.get(
        f"/api/servers/{resource_id}/graph",
        params={"target": target, "duration": duration},
    )


def test_token_catalog_pagination_and_grouped_overview(tmp_path):
    client, collector, source, storage, upstream, _ = make_system(tmp_path)
    assert client.get("/api/monitor/catalog").status_code == 401
    assert upstream.calls == []
    collector.run_once()
    login(client)
    catalog = client.get("/api/monitor/catalog").json()
    assert catalog["targets"] == TARGETS
    assert catalog["durations"] == DURATIONS
    assert catalog["timezone"] == "UTC+8"
    overview = client.get("/api/overview").json()
    assert len(overview["servers"]) == 2
    assert all(len(s["applications"]) == 1 for s in overview["servers"])
    assert "test-access-token" not in json.dumps(overview)
    assert "NEVER-PERSIST" not in json.dumps([r.raw for r in storage.list_resources()])
    assert client.get("/api/events").status_code == 404
    assert client.get("/api/resources/1/series").status_code == 404
    settings = client.get("/api/doctor").json()["checks"]["config"]["settings"]
    assert settings["cloudways_access_token_configured"] is True
    assert "test-access-token" not in json.dumps(settings)
    assert catalog["alert_rules"][0]["target"] == "Idle CPU"
    Path(".tmp/e2e").mkdir(parents=True, exist_ok=True)
    Path(".tmp/e2e/overview.json").write_text(
        json.dumps(overview, indent=2), encoding="utf-8"
    )


def test_graph_duration_null_zero_time_and_summary(tmp_path):
    client, collector, source, storage, upstream, _ = make_system(tmp_path)
    collector.run_once()
    login(client)
    server = next(r for r in storage.list_resources() if r.resource_type == "server")
    upstream.latest = None
    result = graph(client, source, server.id).json()
    assert result["status"] == "ok"
    assert result["series"][0]["points"][-1]["value"] is None
    assert result["series"][0]["summary"]["latest"] == 70
    assert result["series"][0]["summary"]["avg"] == 70
    upstream.latest = 0
    result = graph(client, source, server.id, duration="6 Months").json()
    assert result["duration"] == "6 Months"
    assert result["series"][0]["summary"]["latest"] == 0
    assert result["series"][0]["points"][-1]["timestamp"].endswith("+00:00")
    assert graph(client, source, server.id, target="Other").status_code == 422
    assert graph(client, source, server.id, duration="6 Hours").status_code == 422


@pytest.mark.parametrize(
    "flag,status",
    [("pending", "pending"), ("failed", "error"), ("no_units", "ok")],
)
def test_task_and_unit_statuses(tmp_path, flag, status):
    client, collector, source, storage, upstream, _ = make_system(tmp_path)
    collector.run_once()
    login(client)
    setattr(upstream, flag, True)
    assert graph(client, source, 1).json()["status"] == status


def test_invalid_graph_is_visible_error(tmp_path):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    collector.run_once()
    login(client)
    upstream.graph_format = "invalid"
    response = graph(client, source, 1).json()
    assert response["status"] == "error"
    assert "schema" in response["error"].lower()


def test_app_failure_does_not_block_other_resources_or_zero(tmp_path):
    client, collector, _, _, upstream, _ = make_system(tmp_path)
    upstream.app_error = True
    health = collector.run_once()
    assert health.status == "degraded"
    assert health.snapshots_stored == 4
    login(client)
    servers = client.get("/api/overview").json()["servers"]
    app1, app2 = servers[0]["applications"][0], servers[1]["applications"][0]
    assert app1["latest"]["diagnostics"]
    assert app2["latest"]["disk_used_gb"] == 2.5
    assert "bandwidth_bytes" not in app2["latest"]
    assert app2["latest"]["traffic_requests"] == 0
    assert "cpu_percent" not in app2["latest"]


@pytest.mark.parametrize("disk_mb", [606.7, 0, None])
def test_real_application_disk_identity_units_and_missing_values(tmp_path, disk_mb):
    # Failures: db tables summed as total, disk labelled bandwidth, app identity
    # mismatched, zero discarded, a null sample guessed or replaced by old data.
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle

    def live_contract(request):
        if request.url.path.endswith("/server/monitor/summary"):
            sid = request.url.params["server_id"]
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "name": "other_app",
                            "datapoint": [90000, upstream.timestamp],
                            "type": "apps",
                        },
                        {
                            "name": f"app{sid}",
                            "datapoint": [disk_mb, upstream.timestamp]
                            if disk_mb is not None
                            else None,
                            "type": "apps",
                        },
                    ]
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    login(client)
    latest = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    if disk_mb is None:
        assert "disk_used_gb" not in latest
        assert any(d["metric"] == "Disk" for d in latest["diagnostics"])
    else:
        assert latest["disk_used_gb"] == pytest.approx(disk_mb / 1000)
        assert (
            latest["disk_used_gb_sample_at"]
            == datetime.fromtimestamp(upstream.timestamp, UTC).isoformat()
        )
    assert "bandwidth_bytes" not in latest
    assert latest["bandwidth_status"] == "unavailable"
    assert not any(path == "/app/monitor/summary" for path, _ in upstream.calls)


@pytest.mark.parametrize(
    "failure", ["unknown_unit", "duplicate_identity", "negative", "wrong_identity"]
)
def test_invalid_application_disk_is_withheld_without_losing_requests(
    tmp_path, failure
):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle

    def live_contract(request):
        if request.url.path.endswith("/server/monitor/summary"):
            sid = request.url.params["server_id"]
            row = {
                "name": f"app{sid}",
                "datapoint": [2500, upstream.timestamp],
                "type": "apps",
            }
            content = [row]
            if failure == "duplicate_identity":
                content.append(dict(row))
            elif failure == "negative":
                row["datapoint"][0] = -1
            elif failure == "wrong_identity":
                row["name"] = "unrelated"
            return httpx.Response(
                200,
                json={
                    "content": content,
                    **({"unit": "unverified"} if failure == "unknown_unit" else {}),
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    login(client)
    latest = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    assert "disk_used_gb" not in latest
    assert latest["traffic_requests"] == 0
    assert any(d["metric"] == "Disk" for d in latest["diagnostics"])


def test_application_disk_cached_once_per_parent_and_null_does_not_reuse_old_total(
    tmp_path,
):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle
    disk_mb = 1200
    now = [0.0]
    source._clock = lambda: now[0]

    def live_contract(request):
        response = original(request)
        if (
            request.url.path.endswith("/apps/flexible")
            and request.url.params["page"] == "1"
        ):
            payload = response.json()
            payload["data"]["apps"].append(
                {"id": "3", "label": "App 3", "server_id": "1", "sys_user": "app3"}
            )
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/server/monitor/summary"):
            sid = request.url.params["server_id"]
            users = [f"app{sid}"] + (["app3"] if sid == "1" else [])
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "name": user,
                            "type": "apps",
                            "datapoint": [disk_mb, upstream.timestamp]
                            if disk_mb is not None
                            else None,
                        }
                        for user in users
                    ]
                },
            )
        return response

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    assert sum(path == "/server/monitor/summary" for path, _ in upstream.calls) == 2
    login(client)
    overview = client.get("/api/overview").json()
    assert len(overview["servers"][0]["applications"]) == 2
    assert all(
        app["latest"]["disk_used_gb"] == 1.2
        for app in overview["servers"][0]["applications"]
    )
    disk_mb = None
    now[0] += 61
    collector.run_once()
    overview = client.get("/api/overview").json()
    assert all(
        "disk_used_gb" not in app["latest"]
        for s in overview["servers"]
        for app in s["applications"]
    )


def test_pending_application_requests_keep_the_completed_fetch_time_then_expire(
    tmp_path,
):
    # Failure: every newly pending analytics task erases a completed count, or
    # its cached count is falsely relabelled with this cycle's new fetch time.
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    now = [0.0]
    source._clock = lambda: now[0]
    collector.run_once()
    login(client)
    latest = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    collected_at = latest["traffic_fetched_at"]
    assert latest["traffic_requests"] == 0
    upstream.pending = True
    now[0] += 301
    collector.run_once()
    latest = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    assert latest["traffic_requests"] == 0
    assert latest["traffic_status"] == "pending"
    assert latest["traffic_fetched_at"] == collected_at
    now[0] += 601
    collector.run_once()
    latest = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    assert "traffic_requests" not in latest


def test_completed_application_requests_refresh_at_most_every_five_minutes(tmp_path):
    # Failure: a completed 1h analysis immediately triggers another operation
    # on every 60s cycle, exhausting the real Traffic endpoint's smaller quota.
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    now = [0.0]
    source._clock = lambda: now[0]
    collector.run_once()
    login(client)
    before = sum(path == "/app/analytics/traffic" for path, _ in upstream.calls)
    initial = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    now[0] += 60
    collector.run_once()
    latest = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    assert sum(path == "/app/analytics/traffic" for path, _ in upstream.calls) == before
    assert latest["traffic_requests"] == initial["traffic_requests"]
    assert latest["traffic_fetched_at"] == initial["traffic_fetched_at"]
    assert latest["traffic_status"] == "cached"
    now[0] += 241
    collector.run_once()
    assert (
        sum(path == "/app/analytics/traffic" for path, _ in upstream.calls)
        == before + 2
    )


def test_traffic_endpoint_rate_limit_preserves_completed_counts_and_other_metrics(
    tmp_path,
):
    # Failures: a Traffic-specific 429 pauses server graphs/disk/discovery;
    # last completed counts disappear; retries ignore the actual Retry-After.
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle
    now = [0.0]
    limited = [False]
    traffic_calls = []
    source._clock = lambda: now[0]

    def quota(request):
        if limited[0] and request.url.path.endswith("/app/analytics/traffic"):
            traffic_calls.append(now[0])
            return httpx.Response(
                429,
                json={"message": "Too Many Attempts."},
                headers={
                    "Retry-After": "88",
                    "X-RateLimit-Limit": "20",
                    "X-RateLimit-Remaining": "0",
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(quota))
    collector.run_once()
    login(client)
    initial = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    limited[0] = True
    now[0] += 301
    collector.run_once()
    overview = client.get("/api/overview").json()
    assert len(overview["servers"]) == 2
    for server in overview["servers"]:
        assert len(server["latest"]["metrics"]) == 3
        app = server["applications"][0]["latest"]
        assert app["disk_used_gb"] == 2.5
        assert app["traffic_requests"] == 0
        assert app["traffic_status"] == "rate_limited"
    assert (
        overview["servers"][0]["applications"][0]["latest"]["traffic_fetched_at"]
        == initial["traffic_fetched_at"]
    )
    assert len(traffic_calls) == 1
    now[0] += 60
    collector.run_once()
    assert len(traffic_calls) == 1
    assert (
        client.get(
            "/api/servers/1/graph",
            params={"target": "Idle CPU", "duration": "12 Hours"},
        ).json()["status"]
        == "ok"
    )
    now[0] += 29
    limited[0] = False
    collector.run_once()
    overview = client.get("/api/overview").json()
    assert all(
        s["applications"][0]["latest"]["traffic_status"] == "ok"
        for s in overview["servers"]
    )


def test_rate_limited_application_count_is_not_reused_after_expiry(tmp_path):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle
    now = [0.0]
    limited = [False]
    source._clock = lambda: now[0]

    def quota(request):
        if limited[0] and request.url.path.endswith("/app/analytics/traffic"):
            return httpx.Response(429, json={}, headers={"Retry-After": "88"})
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(quota))
    collector.run_once()
    login(client)
    limited[0] = True
    now[0] += 601
    collector.run_once()
    latest = client.get("/api/overview").json()["servers"][0]["applications"][0][
        "latest"
    ]
    assert latest["disk_used_gb"] == 2.5
    assert "traffic_requests" not in latest
    assert latest["traffic_status"] == "rate_limited"


def test_rediscovery_removes_deleted_resources(tmp_path):
    client, collector, _, storage, upstream, _ = make_system(tmp_path)
    collector.run_once()
    upstream.removed = True
    collector.run_once()
    assert len(storage.list_resources()) == 2


def test_distinct_samples_alert_null_gap_and_recovery(tmp_path):
    client, collector, source, storage, upstream, telegram = make_system(tmp_path)
    upstream.latest = 4
    tick = [0]
    source._clock = lambda: tick[0]
    for _ in range(4):
        tick[0] += 60
        source.invalidate_graph_cache()
        collector.run_once()
    assert telegram == []
    upstream.latest = None
    upstream.timestamp += 60
    tick[0] += 60
    source.invalidate_graph_cache()
    collector.run_once()
    upstream.latest = 4
    for _ in range(3):
        tick[0] += 60
        upstream.timestamp += 60
        source.invalidate_graph_cache()
        collector.run_once()
    assert telegram and all(
        "UTC+8" in m["text"] and "/#server-" in m["text"] for m in telegram
    )
    assert any("Idle CPU" in m["text"] and "<=" in m["text"] for m in telegram)
    upstream.latest = None
    upstream.timestamp += 60
    tick[0] += 60
    source.invalidate_graph_cache()
    collector.run_once()
    assert storage.list_alert_states(status="active")
    upstream.latest = 9999
    upstream.timestamp += 60
    tick[0] += 60
    source.invalidate_graph_cache()
    collector.run_once()
    assert any("Recovered" in m["text"] for m in telegram)


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
def test_errors_are_explicit_and_rate_limit_backs_off(tmp_path, status_code):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    upstream.status_code = status_code
    health = collector.run_once()
    assert health.status == "degraded"
    assert health.last_error_code
    if status_code == 429:
        count = len(upstream.calls)
        collector.run_once()
        assert len(upstream.calls) == count


def test_access_token_required_and_old_key_not_accepted():
    with pytest.raises(SettingsError, match="CLOUDWAYS_ACCESS_TOKEN"):
        Settings.from_env(
            {
                "DASHBOARD_BASE_URL": "http://localhost",
                "SQLITE_PATH": "unused.sqlite3",
                "CLOUDWAYS_API_KEY": "old",
            }
        )


def test_null_latest_hidden_but_source_history_preserved(tmp_path):
    client, collector, source, storage, upstream, _ = make_system(tmp_path)
    upstream.latest = None
    collector.run_once()
    login(client)
    overview = client.get("/api/overview").json()
    assert all(len(s["latest"]["metrics"]) == 3 for s in overview["servers"])
    assert all(
        m["value"] == 70 for s in overview["servers"] for m in s["latest"]["metrics"]
    )
    persisted = storage.get_graph(1, "Idle CPU", "1 Hour")
    assert len(persisted["series"][0]["points"]) == 3
    assert persisted["series"][0]["points"][-1]["value"] is None
    assert storage.get_latest_metric_snapshot(1).raw_payload["graphs"]["Idle CPU"][
        "series"
    ][0]["points"] == [persisted["series"][0]["points"][0]]


def test_partial_pagination_does_not_remove_discovered_resources(tmp_path):
    _, collector, source, storage, upstream, _ = make_system(tmp_path)
    collector.run_once()
    original = upstream.handle

    def fail_second_page(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(500, json={"message": "page failed"})
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(fail_second_page))
    assert collector.run_once().status == "degraded"
    assert len(storage.list_resources()) == 4


def test_graph_cache_shared_by_browser_requests_and_collector(tmp_path):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    collector.run_once()
    login(client)
    before = len(upstream.calls)
    for _ in range(4):
        result = client.get(
            "/api/servers/1/graph", params={"target": "Idle CPU", "duration": "1 Hour"}
        )
        assert result.status_code == 200
    assert len(upstream.calls) == before


def test_authentication_health_notification_and_recovery(tmp_path):
    _, collector, source, _, upstream, telegram = make_system(tmp_path)
    tick = [0]
    source._clock = lambda: tick[0]
    upstream.status_code = 401
    collector.run_once()
    assert len(telegram) == 1 and "authentication failed" in telegram[0]["text"]
    assert "test-access-token" not in json.dumps(telegram)
    upstream.status_code = 200
    tick[0] = 301
    collector.run_once()
    assert any("Recovered: collection" in m["text"] for m in telegram)


def test_all_null_graph_has_no_completed_summary_or_metric(tmp_path):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle

    def missing(request):
        if request.url.path.endswith("/server/monitor/detail"):
            return httpx.Response(
                200,
                json={
                    "content": json.dumps(
                        [
                            {
                                "datapoints": [
                                    [None, upstream.timestamp - 300],
                                    [None, upstream.timestamp],
                                ]
                            }
                        ]
                    )
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(missing))
    collector.run_once()
    login(client)
    assert all(
        s["latest"]["metrics"] == []
        for s in client.get("/api/overview").json()["servers"]
    )
    result = client.get(
        "/api/servers/1/graph", params={"target": "Idle CPU", "duration": "1 Hour"}
    ).json()
    assert "latest" not in result["series"][0]["summary"]


def test_completed_sample_respects_cadence_and_real_staleness(tmp_path):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle
    sample_time = upstream.timestamp - 1800

    def live_contract(request):
        if request.url.path.endswith("/server/monitor/detail"):
            return httpx.Response(
                200,
                json={
                    "content": json.dumps(
                        [{"datapoints": [[2, sample_time], [None, sample_time + 1800]]}]
                    )
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    login(client)
    disk = next(
        m
        for m in client.get("/api/overview").json()["servers"][0]["latest"]["metrics"]
        if m["target"] == "Free Disk"
    )
    assert disk["value"] == 2 and disk["stale"] is False
    sample_time -= 7200
    source.invalidate_graph_cache()
    collector.run_once()
    disk = next(
        m
        for m in client.get("/api/overview").json()["servers"][0]["latest"]["metrics"]
        if m["target"] == "Free Disk"
    )
    assert disk["stale"] is True


def test_cached_ui_is_not_blocked_by_application_network_request(tmp_path):
    # Failure: a slow application HTTP call holds the global graph/cache lock.
    from concurrent.futures import ThreadPoolExecutor, wait
    import threading

    client, collector, source, _, upstream, _ = make_system(tmp_path)
    now = [0.0]
    source._clock = lambda: now[0]
    collector.run_once()
    login(client)
    now[0] += 301
    client.get(
        "/api/servers/1/graph", params={"target": "Idle CPU", "duration": "1 Hour"}
    )
    original = upstream.handle
    entered, release = threading.Event(), threading.Event()

    def slow_application(request):
        if request.url.path.endswith("/app/analytics/traffic"):
            entered.set()
            release.wait(3)
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(slow_application))
    with ThreadPoolExecutor(max_workers=3) as pool:
        application = pool.submit(source.get_application_metrics, "1", "1")
        assert entered.wait(1)
        try:
            responses = [
                pool.submit(client.get, "/api/monitor/catalog"),
                pool.submit(
                    client.get,
                    "/api/servers/1/graph",
                    params={"target": "Idle CPU", "duration": "1 Hour"},
                ),
            ]
            done, pending = wait(responses, timeout=0.5)
            assert not pending, (
                "Cached UI requests queued behind unrelated application network call"
            )
            assert all(f.result().status_code == 200 for f in done)
        finally:
            release.set()
        application.result()


def test_discovery_is_visible_before_slow_metric_collection(tmp_path):
    _, collector, source, storage, _, _ = make_system(tmp_path)
    original = source.get_server_metrics

    def observe_discovery(server_id):
        assert len(storage.list_resources()) == 4
        return original(server_id)

    source.get_server_metrics = observe_discovery
    assert collector.run_once().snapshots_stored == 4
    assert all(
        s.collection_status == "ok"
        for r in storage.list_resources()
        if (s := storage.get_latest_metric_snapshot(r.id))
    )


def test_live_json_content_graph_is_decoded_without_inventing_units(tmp_path):
    # Live failure: content is a JSON string; its final null must stay null.
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle

    def live_contract(request):
        if request.url.path.endswith("/server/monitor/detail"):
            return httpx.Response(
                200,
                json={
                    "content": json.dumps(
                        [
                            {
                                "target": "cg.example.cpu.idle",
                                "datapoints": [
                                    [82.22, upstream.timestamp - 300],
                                    [None, upstream.timestamp],
                                ],
                                "tags": {"name": "cg.example.cpu.idle"},
                            }
                        ]
                    )
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    login(client)
    graph = client.get(
        "/api/servers/1/graph", params={"target": "Idle CPU", "duration": "1 Hour"}
    ).json()
    assert graph["status"] == "ok"
    assert graph["series"][0]["unit"] == "%"
    assert len(graph["series"][0]["points"]) == 2
    assert graph["series"][0]["points"][-1]["value"] is None
    assert graph["series"][0]["summary"]["latest"] == 82.22


@pytest.mark.parametrize(
    "target,raw_value,unit,value",
    [
        ("Free memory", 100_000_000, "MB", 100),
        ("Free Disk", 2, "GB", 2),
        ("Monthly Bandwidth", 0, "GB", 0),
        ("Incoming network traffic", 7.5, "Mbps", 7.5),
        ("Reads per second", 0, "ops/s", 0),
        ("Varnish Nuked", 0, "items", 0),
        ("Auto-healing Restarts", 1, "restarts", 1),
        ("Varnish Hit Rate", 80, "%", 80),
    ],
)
def test_confirmed_target_units_through_graph_api(
    tmp_path, target, raw_value, unit, value
):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle

    def live_contract(request):
        if request.url.path.endswith("/server/monitor/detail"):
            return httpx.Response(
                200,
                json={
                    "content": json.dumps(
                        [
                            {
                                "target": "confirmed.metric",
                                "datapoints": [
                                    [raw_value, upstream.timestamp - 300],
                                    [None, upstream.timestamp],
                                ],
                            }
                        ]
                    )
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    login(client)
    result = client.get(
        "/api/servers/1/graph", params={"target": target, "duration": "7 Days"}
    ).json()
    assert result["status"] == "ok"
    series = result["series"][0]
    assert series["unit"] == unit
    assert series["points"][0]["value"] == value
    assert series["points"][-1]["value"] is None
    assert series["summary"]["latest"] == value
    assert series["unit_source"] == "confirmed target definition"


def test_unknown_target_units_stay_unconfirmed(tmp_path):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle

    def live_contract(request):
        if request.url.path.endswith("/monitor_targets"):
            return httpx.Response(
                200, json={"targets": {"do": TARGETS + ["Future metric"]}}
            )
        if request.url.path.endswith("/server/monitor/detail"):
            return httpx.Response(
                200,
                json={
                    "content": json.dumps([{"datapoints": [[0, upstream.timestamp]]}])
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    login(client)
    result = client.get(
        "/api/servers/1/graph", params={"target": "Future metric", "duration": "7 Days"}
    ).json()
    assert result["status"] == "warning"
    assert result["series"][0]["unit"] is None
    assert result["series"][0]["summary"]["latest"] == 0


def test_live_traffic_count_header_and_parameters_are_preserved(tmp_path):
    client, collector, source, _, upstream, _ = make_system(tmp_path)
    original = upstream.handle

    def live_contract(request):
        if "/operation/" in request.url.path and ":traffic:" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": 1,
                        "data": [],
                        "parameters": {
                            "top_statuses": {
                                "header": ["Status", "Count"],
                                "body": [["200", 17], ["404", 0]],
                                "footer": [],
                            }
                        },
                    }
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(live_contract))
    collector.run_once()
    login(client)
    app = client.get("/api/overview").json()["servers"][0]["applications"][0]
    assert app["latest"]["traffic_requests"] == 17
    assert app["latest"]["traffic_complete"] is False


def test_telegram_queue_survives_restart_and_delivery_failure(tmp_path):
    _, collector, source, storage, upstream, telegram = make_system(tmp_path)
    tick = [0]
    source._clock = lambda: tick[0]
    upstream.latest = 4
    notifier = collector._alerts._notifier
    notifier._http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    for _ in range(3):
        tick[0] += 60
        upstream.timestamp += 60
        source.invalidate_graph_cache()
        collector.run_once()
    assert storage.list_alert_states(status="active")
    queued = storage.pending_notifications()
    assert queued
    restarted_storage = Storage(Database(collector._settings.sqlite_path))

    def accept(request):
        telegram.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    restarted = AlertEvaluator(
        settings=collector._settings,
        storage=restarted_storage,
        notifier=TelegramNotifier(
            settings=collector._settings,
            http_client=httpx.Client(transport=httpx.MockTransport(accept)),
        ),
    )
    restarted.flush_notifications()
    assert len(telegram) == len(queued)
    assert restarted_storage.pending_notifications() == []
    Path(".tmp/e2e/telegram-capture.json").write_text(
        json.dumps(telegram, indent=2), encoding="utf-8"
    )
