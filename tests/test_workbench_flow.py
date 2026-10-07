"""Deterministic version of the requested build → schedule → execute workflow."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import execute_next

pytestmark = pytest.mark.integration


def test_build_weather_skill_schedule_and_execute(make_app):
    class WeatherFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                {"city": "New York", "forecast": "Clear", "fixture": True}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), WeatherFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        app, e, adapter = make_app()
        refs = {}

        def last(messages):
            return json.loads(
                next(m["content"] for m in reversed(messages) if m["role"] == "tool")
            )

        source = f"""from fluxyr import params, output, log
import json
from urllib.request import urlopen
from urllib.parse import urlencode
with urlopen("http://127.0.0.1:{server.server_port}/forecast?" + urlencode(params), timeout=5) as response:
    forecast=json.load(response)
log(json.dumps(forecast))
output(forecast)
"""

        def create(messages, tools):
            return [
                (
                    "create_action",
                    {
                        "skill_id": refs["skill"],
                        "name": "action_weather",
                        "description": "Read the local weather test API",
                        "source": source,
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                )
            ]

        def test(messages, tools):
            refs["version"] = last(messages)["build"]["tools"][0]["version_id"]
            return [
                (
                    "test_action",
                    {"version_id": refs["version"], "params": {"city": "New York"}},
                )
            ]

        def run(messages, tools):
            refs["routine"] = last(messages)["id"]
            return [("run_routine", {"routine_id": refs["routine"]})]

        def delegate(messages, tools):
            assert "create_action" not in {t["name"] for t in tools}
            refs["skill"] = last(messages)["id"]
            return [("build_skill", {"skill_id": refs["skill"]})]

        def plan(messages, tools):
            assert "create_action" in {t["name"] for t in tools}
            assert "create_skill" not in {t["name"] for t in tools}
            return [
                (
                    "submit_plan",
                    {
                        "skill_id": refs["skill"],
                        "actions": [
                            {"name": "action_weather", "description": "Fetch weather"}
                        ],
                    },
                )
            ]

        adapter.replies = [
            [
                (
                    "create_skill",
                    {
                        "name": "Weather",
                        "description": "Weather API",
                        "instruction": "Use action_weather to obtain a forecast.",
                        "spec": "## Weather\nInput city string; return forecast from the local test API.",
                    },
                )
            ],
            delegate,
            plan,
            create,
            "Candidate created; not tested yet.",
            test,
            lambda *_: [("activate_action", {"version_id": refs["version"]})],
            [
                (
                    "create_routine",
                    {
                        "name": "Morning weather",
                        "prompt": "Get weather for New York, log it and report the result.",
                        "cron": "0 8 * * *",
                        "timezone": "UTC",
                        "enabled": True,
                    },
                )
            ],
            run,
            "Built, tested, activated and scheduled. A manual run is queued.",
        ]
        e.store.enqueue(
            "Build a weather skill and schedule it every morning at 08:00 UTC; run it now too."
        )
        waiting = execute_next(e)
        assert waiting["status"] == "building", waiting["error"]
        builder = execute_next(e)
        assert builder["status"] == "succeeded", builder["error"]
        assert builder["session_id"] != waiting["session_id"]
        e.builds.resolve_dependencies()
        built = execute_next(e)
        assert built["id"] == waiting["id"]
        assert built["status"] == "succeeded", built["error"]
        assert e.skills.list()[0]["tools"][0]["active_version"] == refs["version"]
        assert e.routines.list()[0]["cron"] == "0 8 * * *"
        adapter.replies = [
            [("action_weather", {"city": "New York"})],
            lambda messages, _: [
                (
                    "finish_execution",
                    {
                        "output": last(messages)["output"],
                        "evidence": "Local test API returned the forecast; output was printed.",
                    },
                )
            ],
            "Forecast retrieved and printed.",
        ]
        executed = execute_next(e)
        assert executed["status"] == "succeeded", executed["error"]
        assert executed["session_id"] != built["session_id"]
        assert (
            executed["outcome"]["actions"][0]["result"]["output"]["forecast"] == "Clear"
        )
        assert any(
            x["type"] == "execution_report" for x in e.store.events(built["session_id"])
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
