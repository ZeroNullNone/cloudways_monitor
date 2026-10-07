"""Failure cases written before capacity and application-analysis implementation.

Wrong disk selected when block storage exists; plan prefixes lose RAM; missing
capacity guessed; secrets persisted; failed capacity blocks existing monitoring;
unauthenticated analysis starts tasks; collapse still polls; pending starts duplicate
tasks; cached window/category crosses another selection; partial errors presented
as a full error rate; empty table confused with null; malformed counts accepted;
rate-limited diagnosis blocks overview; failed task looks like an empty result.
"""

import json

import httpx
import pytest

from tests.helpers import login
from tests.test_redesign_e2e import make_system


def fixture(tmp_path):
    client, collector, source, storage, upstream, telegram = make_system(tmp_path)
    original = upstream.handle
    state = {
        "pending": False,
        "failed": False,
        "limited": False,
        "null": False,
        "invalid": False,
        "capacity_error": False,
    }

    def handle(request):
        path = request.url.path.removeprefix("/api/v2")
        params = dict(request.url.params)
        if path in ("/servers/1", "/servers/2"):
            upstream.calls.append((path, params))
            if state["capacity_error"]:
                return httpx.Response(403, json={"message": "forbidden"})
            return httpx.Response(
                200,
                json={
                    "id": int(path.rsplit("/", 1)[1]),
                    "cloud": "do",
                    "instance_type": "intel-2GB" if path.endswith("/1") else "4GB",
                    "volume_size": 50 if path.endswith("/1") else 80,
                    "storage": 100 if path.endswith("/1") else None,
                    "master_password": "NEVER-PERSIST",
                    **state.get("capacity_fields", {}),
                },
            )
        if path.startswith("/app/analytics/") and not (
            path.endswith("/traffic")
            and params.get("resource") == "top_statuses"
            and params.get("duration") == "1h"
        ):
            upstream.calls.append((path, params))
            if state["limited"]:
                return httpx.Response(429, json={}, headers={"Retry-After": "60"})
            return httpx.Response(
                200,
                json={
                    "task_id": "analysis:"
                    + params["resource"]
                    + ":"
                    + params["duration"]
                },
            )
        if path.startswith("/operation/analysis:"):
            upstream.calls.append((path, params))
            if state["pending"] or state["failed"]:
                return httpx.Response(
                    200,
                    json={
                        "operation": {"is_completed": "-1" if state["failed"] else "0"}
                    },
                )
            resource = path.split(":")[1]
            tables = {
                "top_statuses": {
                    "header": ["Status", "Count"],
                    "body": [["200", 90], ["404", 7], ["500", 3]],
                },
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
            table = tables[resource]
            table.update(from_=1)
            if state["null"]:
                table["body"] = None
            if state["invalid"]:
                table["body"] = [["/checkout", -1]]
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "parameters": json.dumps({resource: table}),
                    }
                },
            )
        if (
            path.startswith("/operation/")
            and ":traffic:" in path
            and state.get("collector_table")
        ):
            upstream.calls.append((path, params))
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "is_completed": "1",
                        "parameters": json.dumps(
                            {
                                "top_statuses": {
                                    "header": ["Status", "Count"],
                                    "body": [["200", 90], ["404", 7], ["500", 3]],
                                }
                            }
                        ),
                    }
                },
            )
        return original(request)

    source._http_client = httpx.Client(transport=httpx.MockTransport(handle))
    collector.run_once()
    app = next(r for r in storage.list_resources() if r.resource_type == "application")
    return client, collector, source, storage, upstream, state, app


def analysis(client, app, category="urls", duration="1h"):
    return client.get(
        f"/api/applications/{app.id}/analytics",
        params={"category": category, "duration": duration},
    )


def test_capacity_matches_do_block_storage_and_plan_ram(tmp_path):
    client, collector, source, storage, upstream, _, _ = fixture(tmp_path)
    login(client)
    servers = client.get("/api/overview").json()["servers"]
    assert servers[0]["latest"]["capacity"]["memory_gb"] == 2
    assert servers[0]["latest"]["capacity"]["disk_gb"] == 100
    assert servers[0]["latest"]["capacity"]["disk_source"] == "storage"
    assert servers[1]["latest"]["capacity"]["disk_gb"] == 80
    collector.run_once()
    assert sum(p == "/servers/1" for p, _ in upstream.calls) == 1
    assert "NEVER-PERSIST" not in json.dumps(
        [
            s.raw_payload
            for r in storage.list_resources()
            if (s := storage.get_latest_metric_snapshot(r.id))
        ]
    )


def test_capacity_failure_keeps_existing_metrics(tmp_path):
    client, collector, source, _, _, state, _ = fixture(tmp_path)
    state["capacity_error"] = True
    source._clock = lambda: 1e10
    collector.run_once()
    login(client)
    server = client.get("/api/overview").json()["servers"][0]
    assert "capacity" not in server["latest"] or not server["latest"]["capacity"]
    assert len(server["latest"]["metrics"]) == 3
    assert any(d["metric"] == "Capacity" for d in server["latest"]["diagnostics"])


def test_analytics_requires_login_and_is_on_demand(tmp_path):
    client, _, _, _, upstream, _, app = fixture(tmp_path)
    calls = list(upstream.calls)
    assert analysis(client, app).status_code == 401
    assert upstream.calls == calls
    assert not any(
        p.endswith("/php") or p.endswith("/mysql") or q.get("resource") == "top_urls"
        for p, q in calls
    )
    login(client)
    assert analysis(client, app).json()["rows"] == [["/checkout", 45], ["/", 0]]
    assert (
        client.get(
            "/api/applications/99999/analytics",
            params={"category": "urls", "duration": "1h"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/applications/1/analytics",
            params={"category": "urls", "duration": "1h"},
        ).status_code
        == 404
    )
    assert analysis(client, app, category="secret").status_code == 422
    assert analysis(client, app, duration="7 Days").status_code == 422


def test_pending_analysis_reuses_task_and_preserves_selected_window(tmp_path):
    client, _, source, _, upstream, state, app = fixture(tmp_path)
    login(client)
    source._clock = lambda: 1e10
    state["pending"] = True
    assert analysis(client, app).json()["status"] == "pending"
    source._clock = lambda: 1e10 + 4
    assert analysis(client, app).json()["status"] == "pending"
    assert (
        sum(
            p.endswith("/traffic") and q.get("resource") == "top_urls"
            for p, q in upstream.calls
        )
        == 1
    )
    state["pending"] = False
    source._clock = lambda: 1e10 + 8
    result = analysis(client, app).json()
    assert result["status"] == "ok" and result["duration"] == "1h"
    assert result["partial"] is True
    calls = len(upstream.calls)
    assert analysis(client, app).json()["rows"] == result["rows"]
    assert len(upstream.calls) == calls
    assert analysis(client, app, duration="1d").json()["duration"] == "1d"


@pytest.mark.parametrize(
    "category,summary",
    [
        (
            "statuses",
            {"returned_requests": 100, "client_errors": 7, "server_errors": 3},
        ),
        ("php", {"returned_slow_pages": 0}),
        ("mysql", {"returned_slow_queries": 1}),
    ],
)
def test_analysis_summaries_are_honest_returned_counts(tmp_path, category, summary):
    client, _, _, _, _, _, app = fixture(tmp_path)
    login(client)
    result = analysis(client, app, category=category, duration="1d").json()
    assert result["status"] == "ok"
    assert result["summary"] == summary
    assert result["partial"] is True
    assert "error_rate" not in result["summary"]


@pytest.mark.parametrize("flag", ["null", "invalid", "failed", "limited"])
def test_missing_invalid_failed_and_limited_analytics_not_fabricated(tmp_path, flag):
    client, _, _, _, _, state, app = fixture(tmp_path)
    login(client)
    state[flag] = True
    result = analysis(client, app).json()
    assert result["status"] in ("warning", "error")
    assert result["rows"] == [] and result["summary"] == {}
    assert result.get("error")
    assert client.get("/api/overview").status_code == 200


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"instance_type": None, "volume_size": None, "storage": None}, {}),
        (
            {"instance_type": "unknown-plan", "storage": None},
            {"disk_gb": 50, "disk_source": "volume_size"},
        ),
    ],
)
def test_unknown_capacity_is_not_guessed(tmp_path, fields, expected):
    client, collector, source, _, _, state, _ = fixture(tmp_path)
    state["capacity_fields"] = fields
    source._clock = lambda: 1e10
    collector.run_once()
    login(client)
    capacity = (
        client.get("/api/overview").json()["servers"][0]["latest"].get("capacity", {})
    )
    assert {k: v for k, v in capacity.items() if k != "fetched_at"} == expected


def test_status_analysis_reuses_collected_table_without_upstream_requests(tmp_path):
    client, collector, source, _, upstream, state, app = fixture(tmp_path)
    state["collector_table"] = True
    source._clock = lambda: 1e10
    collector.run_once()
    login(client)
    calls = len(upstream.calls)
    result = analysis(client, app, category="statuses").json()
    assert result["status"] == "ok" and result["summary"]["server_errors"] == 3
    assert len(upstream.calls) == calls


def test_rate_limited_analysis_exposes_provider_retry_delay(tmp_path):
    client, _, _, _, _, state, app = fixture(tmp_path)
    login(client)
    state["limited"] = True
    result = analysis(client, app).json()
    assert result["error_code"] == "rate_limited"
    assert 59 <= result["retry_after_seconds"] <= 60
