import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from conftest import execute_next, ScriptedAdapter
from fluxyr_agent.database import MAIN_SESSION
from fluxyr_agent.models import Job, Session, Message, ToolVersion
from fluxyr_agent.core.brain import SyntheticBrain
from fluxyr_agent.core.brain_tool_executor import execute_tool
from fluxyr_agent.tools.registry import Registry


@pytest.mark.parametrize(
    "action,status", [("pause", "paused"), ("cancel", "cancelled")]
)
def test_control_interrupts_blocked_provider_and_discards_late_tools(
    make_app, action, status
):
    _, e, adapter = make_app()
    entered, release = threading.Event(), threading.Event()
    original = adapter.execute_step

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(5)
        return {"role": "assistant", "content": []}, [
            {
                "name": "create_skill",
                "arguments": {
                    "name": "late",
                    "description": "",
                    "instruction": "",
                    "spec": "",
                },
                "call_id": "late",
            }
        ]

    adapter.execute_step = blocked
    job = e.store.enqueue("Wait for model")
    claimed = e.store.claim(e.owner)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(e.execute, claimed)
        assert entered.wait(2)
        e.store.control(job["id"], action)
        future.result(timeout=2)
        with e.db.transaction() as s:
            row = s.get(Job, job["id"])
            assert row.status == status
            assert row.error is None
        release.set()
    assert not e.skills.list()
    if action == "pause":
        adapter.execute_step = original
        adapter.replies = ["Resumed"]
        e.store.control(job["id"], "resume")
        assert execute_next(e)["status"] == "succeeded"


def test_clear_context_archives_all_memory_and_history_but_preserves_resources(
    make_app,
):
    app, e, _ = make_app(["Saved"])
    e.skills.build("kept", "Keep", "Keep")
    e.store.enqueue("A previous conversation")
    execute_next(e)
    with e.db.transaction() as s:
        session = s.get(Session, MAIN_SESSION)
        old = copy.deepcopy(session.brain)
        assert old
    result = app.test_client().post(f"/api/sessions/{MAIN_SESSION}/clear")
    assert result.status_code == 200
    with e.db.transaction() as s:
        assert s.get(Session, MAIN_SESSION).brain is None
        assert s.get(Session, result.json["archive_session_id"]).brain == old
        assert not list(
            s.scalars(select(Message).where(Message.session_id == MAIN_SESSION))
        )
    assert len(e.skills.list()) == 1
    e.store.enqueue("Fresh")
    assert e.store.claim(e.owner)["brain"] is None
    assert (
        app.test_client().post(f"/api/sessions/{MAIN_SESSION}/clear").status_code == 400
    )


def test_memory_reads_do_not_mutate_and_restored_memories_are_in_prompt():
    adapter = ScriptedAdapter([])
    brain = SyntheticBrain(adapter)
    brain._semantic.add("wallet", "R$ 127,43")
    saved = brain.save()
    restored = SyntheticBrain(adapter)
    restored.restore(saved)
    before = copy.deepcopy(restored._semantic.to_dict())
    result = execute_tool(
        {"name": "read_memory", "arguments": {"query": "wallet"}, "call_id": "read"},
        restored._all_tools(),
    )
    assert result.result["semantic"]["wallet"] == "R$ 127,43"
    assert restored._semantic.to_dict() == before
    from fluxyr_agent.core.brain_prompt import build_enhanced_prompt

    assert "wallet: R$ 127,43" in build_enhanced_prompt(
        "System", restored._memory_manager
    )
    result = execute_tool(
        {
            "name": "delete_memory",
            "arguments": {"memory_type": "semantic", "key": "wallet"},
            "call_id": "delete",
        },
        restored._all_tools(),
    )
    assert result.result["deleted"]
    assert "wallet" not in restored._semantic.get_all()


def test_disabled_and_deleted_skill_unavailable_even_to_existing_snapshot(make_app):
    app, e, _ = make_app()
    skill = e.skills.build("managed", "managed", "managed")
    version = e.skills.create(
        skill["id"],
        "managed",
        "managed",
        "from fluxyr import output\noutput(1)",
        {"type": "object", "properties": {}},
    )
    with e.db.transaction() as s:
        s.get(ToolVersion, version["id"]).state = "tested"
    e.skills.activate(version["id"])
    e.store.enqueue("existing")
    job = e.store.claim(e.owner)
    registry = Registry(e, job, lambda *_: None, lambda: False)
    old_tool = job["snapshot"]["tools"][0]
    e.skills.update(skill["id"], enabled=False)
    assert old_tool["name"] not in [t["name"] for t in registry.definitions()]
    assert registry.action(old_tool, {}, "x", None)[0]["executed"] is False
    e.skills.update(skill["id"], enabled=True)
    assert old_tool["name"] in [t["name"] for t in registry.definitions()]
    assert app.test_client().delete("/api/skills/" + skill["id"]).status_code == 200
    assert not e.skills.list()
    assert e.skills.version(version["id"])["source"]
    assert registry.action(old_tool, {}, "y", None)[0]["executed"] is False


def test_python_handler_captures_traceback_and_model_cannot_override_failure(make_app):
    _, e, adapter = make_app()
    skill = e.skills.build("failure", "failure", "failure")
    version = e.skills.create(
        skill["id"],
        "failure",
        "failure",
        'from fluxyr import output\noutput({"looks": "successful"})\nraise ValueError("real failure after output")',
        {"type": "object", "properties": {}},
    )
    failed = e.skills.test(version["id"], {})
    assert failed["done"] and not failed["success"] and not failed["passed"]
    assert "ValueError: real failure after output" in failed["error"]
    # A formerly working version can fail against a real service/input later.
    with e.db.transaction() as s:
        s.get(ToolVersion, version["id"]).state = "tested"
    e.skills.activate(version["id"])
    routine = e.routines.put({"name": "failure", "prompt": "Run failing action"})
    adapter.replies = [
        [("action_failure", {})],
        [
            (
                "finish_execution",
                {"output": {"claimed": "success"}, "evidence": "I say it worked"},
            )
        ],
        "Done successfully!",
    ]
    e.routines.run(routine["id"])
    finished = execute_next(e)
    assert finished["status"] == "failed"
    assert finished["outcome"]["done"] is True
    assert finished["outcome"]["success"] is False
    assert "real failure" in finished["outcome"]["actions"][0]["result"]["error"]


def test_pause_local_python_preserves_process_then_resume_or_cancel(make_app):
    import time

    _, e, _ = make_app()
    skill = e.skills.build("pause_process", "pause", "pause")
    version = e.skills.create(
        skill["id"],
        "pause_process",
        "pause",
        'from fluxyr import output, data_dir\nimport time\np=data_dir / "ticks"\nfor i in range(200):\n p.write_text(str(i))\n time.sleep(.02)\noutput("done")',
        {"type": "object", "properties": {}},
    )
    # Avoid timing the one-time virtualenv setup in this process-control test.
    e.runner.environment([], version_id=version["id"])
    job = e.store.enqueue(
        "Test pause",
        None,
        inputs={"tool_test": {"version_id": version["id"], "params": {}}},
    )
    claimed = e.store.claim(e.owner)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(e.execute, claimed)
        deadline = time.monotonic() + 3
        while not (e.settings.data / "ticks").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        e.store.control(job["id"], "pause")
        while time.monotonic() < deadline:
            with e.db.transaction() as s:
                if s.get(Job, job["id"]).status == "paused":
                    break
            time.sleep(0.02)
        ticks = (e.settings.data / "ticks").read_text()
        time.sleep(0.2)
        assert (e.settings.data / "ticks").read_text() == ticks
        assert not future.done()
        e.store.control(job["id"], "resume")
        assert e.store.claim(e.owner) is None
        time.sleep(0.2)
        assert (e.settings.data / "ticks").read_text() != ticks
        e.store.control(job["id"], "cancel")
        future.result(timeout=2)
    with e.db.transaction() as s:
        assert s.get(Job, job["id"]).status == "cancelled"


def test_enable_skill_refreshes_tools_in_same_workbench_turn(make_app):
    _, e, _ = make_app()
    skill = e.skills.build('reenable', 'reenable', 'reenable')
    version = e.skills.create(skill['id'], 'reenable', 'reenable', 'from fluxyr import output\noutput(1)', {'type':'object','properties':{}})
    with e.db.transaction() as s:
        s.get(ToolVersion, version['id']).state = 'tested'
    e.skills.activate(version['id'])
    e.skills.update(skill['id'], enabled=False)
    e.store.enqueue('Enable it')
    registry = Registry(e, e.store.claim(e.owner), lambda *_: None, lambda: False)
    functions = {t['name']:t['function'] for t in registry.definitions()}
    assert 'action_reenable' not in functions
    result, _ = functions['set_skill_enabled'](skill_id=skill['id'], enabled=True)
    assert result['enabled']
    assert 'action_reenable' in {t['name'] for t in registry.definitions()}
