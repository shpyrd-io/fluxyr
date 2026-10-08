import json
from datetime import UTC

import pytest
from conftest import execute_next

from fluxyr.core.brain_tool_batch import execute_tool_batch_concurrent
from fluxyr.models import ToolVersion, VaultItem
from fluxyr.routines import next_occurrence


def build(
    e,
    source='from fluxyr import params, output\noutput({"answer":params["value"] * 2})',
):
    skill = e.skills.build("math", "Math", "Use arithmetic")
    return e.skills.create(
        skill["id"],
        "action_double",
        "Double a number",
        source,
        {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
        },
    )


def test_candidate_test_activation_and_version_pin(make_app):
    app, e, adapter = make_app()
    version = build(e)
    with pytest.raises(ValueError):
        e.skills.activate(version["id"])
    test = e.skills.test(version["id"], {"value": 4})
    assert test["passed"]
    assert test["output"]["answer"] == 8
    e.skills.activate(version["id"])
    e.store.enqueue("Use math")
    job = e.store.claim(e.owner)
    assert job["snapshot"]["tools"][0]["version"]["id"] == version["id"]
    v2 = build(e, "from fluxyr import output\noutput(99)")
    e.skills.test(v2["id"], {"value": 1})
    e.skills.activate(v2["id"])
    adapter.replies = [[("action_double", {"value": 7})], "14"]
    e.execute(job)
    events = e.store.events(job["session_id"])
    result = next(
        x["payload"]["result"]
        for x in events
        if x["type"] == "tool_end" and x["payload"]["tool_name"] == "action_double"
    )
    assert result["output"]["answer"] == 14


def test_python_human_resume_and_secret_redaction(make_app):
    app, e, adapter = make_app()
    e.vault.put("test_key", "text", {"value": "super-secret-value"})
    skill = e.skills.build("human", "Human", "Get answer")
    version = e.skills.create(
        skill["id"],
        "action_ask",
        "Ask",
        'from fluxyr import request_human, output, secret, log\nanswer=request_human("Continue?")\nlog(secret("test_key")["value"])\noutput(answer)',
        {"type": "object", "properties": {}},
        secrets=["test_key"],
    )
    # A candidate that needs interaction cannot pass an unattended test; test using an explicit answer.
    test = e.runner.run(version, {}, "test", answer={"answer": "test"})
    assert test["logs"] == ["[secret]"]
    with e.db.transaction() as s:
        s.get(ToolVersion, version["id"]).state = "tested"
    e.skills.activate(version["id"])
    adapter.replies = [[("action_ask", {})], "Resumed"]
    e.store.enqueue("Run")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    p = job["brain"]["pending_tools"][0]
    e.store.decide(
        job["id"], p["call_id"], {"decision": "complete", "result": {"answer": "go"}}
    )
    finished = execute_next(e)
    assert finished["status"] == "succeeded", finished["error"]
    assert "super-secret-value" not in json.dumps(e.store.events(job["session_id"]))


def test_vault_key_survives_new_engine_and_not_exposed(make_app):
    app, e, _ = make_app()
    e.vault.put("token", "access_token", {"access_token": "abcd1234"})
    from fluxyr.vault import Vault

    assert Vault(e.db).resolve("token")["access_token"] == "abcd1234"
    assert "abcd1234" not in json.dumps(app.test_client().get("/api/vault").json)
    with e.db.transaction() as s:
        from sqlalchemy import select

        assert "abcd1234" not in s.scalar(select(VaultItem)).content
    assert (e.settings.state / "vault.key").stat().st_mode & 0o777 == 0o600


def test_files_traversal_edit_conflict_and_previews(make_app, tmp_path):
    app, e, _ = make_app()
    first = e.files.write("doc.txt", "hello")
    e.files.write("doc.txt", "world", first["etag"])
    with pytest.raises(ValueError):
        e.files.write("doc.txt", "stale", first["etag"])
    with pytest.raises(ValueError):
        e.files.read("../state/vault.key")
    (e.settings.data / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        e.files.path("escape/outside")
    e.files.write("preview.html", "<h1>Hello</h1>")
    res = app.test_client().get("/preview/preview.html")
    assert res.status_code == 200
    assert "sandbox allow-scripts" in res.headers["Content-Security-Policy"]
    assert "allow-same-origin" not in res.headers["Content-Security-Policy"]


def test_cron_timezone():
    from datetime import datetime

    after = datetime(2026, 10, 7, 9, tzinfo=UTC).timestamp()
    assert (
        datetime.fromtimestamp(next_occurrence("0 8 * * *", "UTC", after), UTC).hour
        == 8
    )


def test_parallel_tools_overlap_and_keep_result_order():
    import threading

    gate = threading.Barrier(2)

    def fn(**kwargs):
        gate.wait(timeout=2)
        return {"id": kwargs["id"]}, "continue"

    tools = [{"name": "read", "function": fn, "parallel_safe": True}]
    calls = [
        {"name": "read", "arguments": {"id": i}, "call_id": str(i)} for i in range(2)
    ]
    results, waiting = execute_tool_batch_concurrent(calls, tools, None)
    assert not waiting and [r.result["id"] for r in results] == [0, 1]


def test_api_rejects_cross_origin_mutation(make_app):
    app, _, _ = make_app()
    res = app.test_client().post(
        "/api/skills", json={}, headers={"Origin": "https://unrelated.example"}
    )
    assert res.status_code == 403


def test_python_timeout_even_when_stdin_is_not_consumed(make_app):
    _, e, _ = make_app(tool_timeout=1)
    version = build(e, "import time\ntime.sleep(30)")
    result = e.runner.run(version, {"value": "x" * 200000}, "timeout-test")
    assert result["success"] is False and result["done"] is True
    assert "timed out" in result["error"]


def test_real_dependency_installation(make_app):
    import os

    if os.getenv("TEST_INSTALL_DEPENDENCIES") != "1":
        pytest.skip("Enable explicit network dependency-installation check")
    _, e, _ = make_app()
    skill = e.skills.build("dependencies", "dependencies", "test")
    v = e.skills.create(
        skill["id"],
        "action_package",
        "package test",
        'from fluxyr import output\nfrom packaging.version import Version\noutput(str(Version("1.2.3")))',
        {"type": "object", "properties": {}},
        dependencies=["packaging==26.3"],
    )
    result = e.skills.test(v["id"], {})
    assert result["passed"] and result["output"] == "1.2.3"


def test_stdlib_action_reuses_venv_without_pip_and_keeps_invocations_separate(make_app):
    _, engine, _ = make_app()
    version = {
        "id": "stdlib-cache",
        "source": "from fluxyr import output\nimport sys, importlib.util, os\noutput({'python': sys.executable, 'isolated': sys.prefix != sys.base_prefix, 'pip': importlib.util.find_spec('pip') is not None, 'cwd': os.getcwd()})",
    }
    first = engine.runner.run(version, {}, "first")
    second = engine.runner.run(version, {}, "second")
    assert first["success"] and second["success"]
    assert first["output"]["isolated"] and not first["output"]["pip"]
    assert first["output"]["python"] == second["output"]["python"]
    assert first["output"]["cwd"] != second["output"]["cwd"]
