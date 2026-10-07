import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import execute_next

from fluxyr.cancelled_context import cancelled_context
from fluxyr.database import MAIN_SESSION
from fluxyr.models import Job, Session
from fluxyr.tools.registry import Registry

REFERENCE = "Banco Inter: token_url=https://cdpj.partners.bancointer.com.br/oauth/v2/token; scope=extrato.read; mTLS required."


@pytest.mark.parametrize("same_batch", [False, True])
@pytest.mark.parametrize("form", ["ask_human", "manage_vault_credential"])
def test_cancel_keeps_documentation_and_closes_pending_form(
    make_app, monkeypatch, same_batch, form
):
    monkeypatch.setattr(Registry, "techdoc", lambda *args: {"reference": REFERENCE})
    read = ("tech_doc", {"url": "https://developers.inter.co/"})
    request = (
        form,
        {"question": "Configure Inter credentials?"}
        if form == "ask_human"
        else {
            "action": "create",
            "vault_item_type": "oauth2",
            "suggested_name": "Inter",
        },
    )
    replies = [[read, request]] if same_batch else [[read], [request]]
    _, engine, adapter = make_app(replies)
    job = engine.store.enqueue("Implement Banco Inter saldo")
    waiting = execute_next(engine)
    assert waiting["status"] == "waiting"
    original = copy.deepcopy(waiting["brain"])
    engine.store.control(job["id"], "cancel")
    with engine.db.transaction() as s:
        # The job retains its original inspectable state; only session continuity changes.
        assert s.get(Job, job["id"]).brain == original
        state = s.get(Session, MAIN_SESSION).brain
        assert state["current_state"] == "READY"
        assert state["pending_tools"] == []
    adapter.replies = ["Use the Inter URL and scope already read."]
    engine.store.enqueue("Send the scope and URL again")
    assert execute_next(engine)["status"] == "succeeded"
    messages = adapter.calls[-1]
    assert "Implement Banco Inter saldo" in json.dumps(messages)
    assert sum(REFERENCE in str(m.get("content")) for m in messages) == 1
    result = next(
        m for m in messages if m.get("role") == "tool" and m.get("name") == form
    )
    assert json.loads(result["content"])["status"] == "cancelled"
    assert len(adapter.calls) == len(replies) + 1
    engine.store.clear_context(MAIN_SESSION)
    engine.store.enqueue("Fresh conversation")
    assert engine.store.claim(engine.owner)["brain"] is None


def test_cancel_during_provider_keeps_completed_results(make_app, monkeypatch):
    monkeypatch.setattr(Registry, "techdoc", lambda *args: {"reference": REFERENCE})
    _, engine, adapter = make_app(
        [[("tech_doc", {"url": "https://developers.inter.co/"})]]
    )
    entered, release = threading.Event(), threading.Event()

    def blocked(messages, tools):
        entered.set()
        release.wait(5)
        return "Late response should be discarded"

    adapter.replies.append(blocked)
    job = engine.store.enqueue("Read Banco Inter docs")
    claimed = engine.store.claim(engine.owner)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(engine.execute, claimed)
        assert entered.wait(2)
        try:
            engine.store.control(job["id"], "cancel")
            future.result(timeout=2)
        finally:
            release.set()
        for _ in range(engine.settings.workers):
            assert engine.provider_slots.acquire(timeout=2)
        for _ in range(engine.settings.workers):
            engine.provider_slots.release()
    engine.store.enqueue("Which scope was it?")
    assert execute_next(engine)["status"] == "succeeded"
    assert REFERENCE in json.dumps(adapter.calls[-1])
    assert "Late response" not in json.dumps(adapter.calls[-1])


def test_cancelling_unstarted_job_does_not_replace_session_context(make_app):
    _, engine, _ = make_app(["Remember Inter"])
    engine.store.enqueue("Banco Inter")
    execute_next(engine)
    with engine.db.transaction() as s:
        original = copy.deepcopy(s.get(Session, MAIN_SESSION).brain)
    queued = engine.store.enqueue("Never started")
    engine.store.control(queued["id"], "cancel")
    with engine.db.transaction() as s:
        assert s.get(Session, MAIN_SESSION).brain == original


def test_canonical_tool_calls_closed_without_duplicating_completed_results():
    state = {
        "provider": "openai",
        "model": "test",
        "current_state": "WAITING",
        "short_term": {
            "capacity": 200,
            "items": [
                {
                    "role": "assistant",
                    "content": "",
                    "function_calls": [
                        {"call_id": "docs", "name": "tech_doc"},
                        {"call_id": "form", "name": "manage_vault_credential"},
                    ],
                },
                {
                    "role": "tool",
                    "name": "tech_doc",
                    "tool_call_id": "docs",
                    "content": REFERENCE,
                },
            ],
        },
        "pending_tools": [
            {"name": "manage_vault_credential", "call_id": "form", "_status": "parked"}
        ],
    }
    original = copy.deepcopy(state)
    messages = cancelled_context(state)["short_term"]["items"]
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == [
        "docs",
        "form",
    ]
    assert state == original
