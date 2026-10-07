"""Fixture-only browser acceptance server; never loads .env or calls real services.

Build frontend, then run: python -m scripts.e2e_preview
Login: admin / correct-password. Stop with Ctrl+C. All measurements are synthetic.
"""

from pathlib import Path
import json
import tempfile

import httpx
import uvicorn

from cloudways_monitor.app import create_app
from tests.test_redesign_e2e import make_system


def build_preview():
    Path(".tmp").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="cw-e2e-", dir=Path(".tmp")))
    _, collector, source, storage, upstream, _ = make_system(folder)
    original = upstream.handle

    def handle(request):
        path = request.url.path.removeprefix("/api/v2")
        params = dict(request.url.params)
        response = original(request)
        payload = response.json()
        if path == "/apps/flexible":
            item = dict(payload["data"]["apps"][0])
            item.update(
                id=str(int(item["id"]) + 2),
                label=f"App {int(item['id']) + 2}",
                app_fqdn=f"app{int(item['id']) + 2}.example.com",
                sys_user=f"app{int(item['id']) + 2}",
            )
            payload["data"]["apps"].append(item)
        elif path in ("/servers/1", "/servers/2"):
            payload.update(
                volume_size=50 if path.endswith("/1") else 80,
                storage=100 if path.endswith("/1") else None,
            )
        elif path == "/server/monitor/summary":
            sid = int(params["server_id"])
            payload = {
                "content": [
                    {
                        "name": f"app{aid}",
                        "type": "apps",
                        "datapoint": [
                            {1: 12400, 2: 3800, 3: 2100, 4: 0}[aid],
                            upstream.timestamp,
                        ],
                    }
                    for aid in (sid, sid + 2)
                ]
            }
        elif path.startswith("/operation/") and ":traffic:" in path:
            app_id = path.removeprefix("/operation/").split(":")[0]
            payload = dict(
                operation=dict(
                    is_completed="1",
                    parameters=json.dumps(
                        dict(
                            top_statuses=dict(
                                header=["Status", "Count"],
                                body=[
                                    [
                                        "200",
                                        {"1": 321, "2": 78, "3": 507, "4": 0}[app_id],
                                    ]
                                ],
                            )
                        )
                    ),
                )
            )
        elif (
            path.startswith("/operation/")
            and ":traffic:" not in path
            and ":analysis_" not in path
        ):
            sid, target, duration = path.removeprefix("/operation/").split(":")
            span = {
                "1 Hour": 3600,
                "12 Hours": 43200,
                "1 Day": 86400,
                "7 Days": 604800,
                "1 Month": 2592000,
                "6 Months": 15552000,
            }[duration]
            values = [80, 74, 69, 73, 57, 56, None, None, 70, 85, 76, 63, 71]
            unit = "%"
            if target == "Free memory":
                values = [v * 18 if v is not None else None for v in values]
                unit = "MB"
            elif target == "Free Disk":
                values = [v * 300 if v is not None else None for v in values]
                unit = "MB"
            elif target in ("Reads per second", "Writes per second"):
                unit = "ops/s"
            elif target in ("Incoming network traffic", "Outgoing network traffic"):
                unit = "B/s"
            elif target == "Monthly Bandwidth":
                unit = "MB"
            elif target in ("Varnish Nuked", "Auto-healing Restarts"):
                unit = "events"
            if sid == "1" and target in ("Idle CPU", "Free memory"):
                values[-1] = None
            payload = dict(
                operation=dict(
                    is_completed="1",
                    result=json.dumps(
                        [
                            dict(
                                target=target,
                                unit=unit,
                                datapoints=[
                                    [v, upstream.timestamp - span + span * i / 12]
                                    for i, v in enumerate(values)
                                ],
                            )
                        ]
                    ),
                )
            )
        return httpx.Response(response.status_code, json=payload)

    source._http_client = httpx.Client(transport=httpx.MockTransport(handle))
    collector.run_once()
    return create_app(
        settings=collector._settings,
        storage=storage,
        cloudways_client=source,
        telemetry_collector=collector,
        static_dir="frontend/dist",
    )


if __name__ == "__main__":
    uvicorn.run(build_preview(), host="127.0.0.1", port=8084)
