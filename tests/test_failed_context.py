import copy
import json
import time

import pytest
from conftest import execute_next

from fluxyr.database import MAIN_SESSION
from fluxyr.models import Job, Session


def candidate(engine):
    skill = engine.skills.build("bank-report", "Bank report", "Read the balance")
    return engine.skills.create(
        skill["id"],
        "balance",
        "Read the balance",
        "from fluxyr import output\noutput({'balance': 1})",
        {"type": "object"},
    )


def test_provider_failure_after_test_keeps_context_for_next_message(
    make_app, monkeypatch
):
    _, engine, adapter = make_app()
    version = candidate(engine)
    failure = {
        "passed": False,
        "success": False,
        "executed": False,
        "phase": "dependency_installation",
        "error": "Dependency setup failed",
        "version_id": version["id"],
        "logs": [],
    }
    monkeypatch.setattr(engine.skills, "test", lambda **_: failure)

    def unavailable(*_):
        raise RuntimeError("Provider temporarily unavailable")

    adapter.replies = [
        [("test_action", {"version_id": version["id"], "params": {}})],
        unavailable,
    ]
    job = engine.store.enqueue("Build a Banco Inter balance integration")
    failed = execute_next(engine)
    assert failed["status"] == "failed"
    with engine.db.transaction() as s:
        assert s.get(Job, job["id"]).brain["current_state"] == "ERROR"
        state = s.get(Session, MAIN_SESSION).brain
        assert state["current_state"] == "READY"
        assert state["pending_tools"] == []
    adapter.replies = ["The balance test failed during dependency setup."]
    engine.store.enqueue("Can you continue?")
    assert execute_next(engine)["status"] == "succeeded"
    history = json.dumps(adapter.calls[-1])
    assert "Banco Inter balance integration" in history
    assert "Dependency setup failed" in history
    assert version["id"] in history


def test_worker_loss_during_test_keeps_checkpoint_without_retry(make_app, monkeypatch):
    _, engine, adapter = make_app()
    version = candidate(engine)
    calls = []

    class WorkerStopped(BaseException):
        pass

    def killed(**_):
        calls.append(1)
        raise WorkerStopped()

    monkeypatch.setattr(engine.skills, "test", killed)
    adapter.replies = [[("test_action", {"version_id": version["id"], "params": {}})]]
    job = engine.store.enqueue("Build a Banco Inter balance integration")
    with pytest.raises(WorkerStopped):
        engine.execute(engine.store.claim(engine.owner))
    with engine.db.transaction() as s:
        row = s.get(Job, job["id"])
        original = copy.deepcopy(row.brain)
        row.lease_until = time.time() - 1
    engine.store.recover()
    with engine.db.transaction() as s:
        assert s.get(Job, job["id"]).status == "interrupted"
        assert s.get(Job, job["id"]).brain == original
        state = s.get(Session, MAIN_SESSION).brain
        assert state["current_state"] == "READY" and state["pending_tools"] == []
        replies = [m for m in state["short_term"]["items"] if m["role"] == "tool"]
        result = json.loads(replies[-1]["content"])
        assert result["status"] == "interrupted"
        assert "Do not retry automatically" in result["error"]
    adapter.replies = ["The balance test was interrupted; inspect its effects first."]
    engine.store.enqueue("Are you there?")
    assert execute_next(engine)["status"] == "succeeded"
    assert "Banco Inter balance integration" in json.dumps(adapter.calls[-1])
    assert version["id"] in json.dumps(adapter.calls[-1])
    assert calls == [1]
