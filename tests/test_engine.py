import json
import time
from concurrent.futures import ThreadPoolExecutor

from conftest import execute_next
from sqlalchemy import select

from fluxyr_agent.database import MAIN_SESSION
from fluxyr_agent.models import Job, Session


def test_chat_events_and_memory(make_app):
    app, e, adapter = make_app(
        [
            [
                (
                    "save_in_memory",
                    {"memory_type": "semantic", "key": "city", "content": "NYC"},
                )
            ],
            "Saved",
        ]
    )
    c = app.test_client()
    response = c.post(
        f"/api/sessions/{MAIN_SESSION}/messages", json={"content": "Remember NYC"}
    )
    assert response.status_code == 202
    job = execute_next(e)
    assert job["status"] == "succeeded", job["error"]
    assert "NYC" in json.dumps(job["brain"]["semantic"])
    detail = c.get("/api/sessions/" + MAIN_SESSION).json
    assert detail["messages"][-1]["content"] == "Saved"
    events = e.store.events(MAIN_SESSION)
    assert {"queued", "started", "delta", "tool_end", "succeeded"} <= {
        x["type"] for x in events
    }
    assert all(
        x["id"] > events[-2]["id"]
        for x in e.store.events(MAIN_SESSION, events[-2]["id"])
    )


def test_two_human_requests_durable_and_duplicate(make_app):
    app, e, adapter = make_app(
        [
            [
                ("ask_human", {"question": "City?"}),
                ("ask_human", {"question": "Units?"}),
            ],
            "Received both",
        ]
    )
    e.store.enqueue("Ask me")
    first = execute_next(e)
    assert first["status"] == "waiting", first["error"]
    pending = first["brain"]["pending_tools"]
    assert len([p for p in pending if p["_status"] == "parked"]) == 2
    c = app.test_client()
    val = {"decision": "complete", "result": {"answer": "NY"}}
    r = c.post(f"/api/jobs/{first['id']}/decisions/{pending[0]['call_id']}", json=val)
    assert r.status_code == 200, r.json
    assert r.json["remaining"] == 1
    assert c.post(
        f"/api/jobs/{first['id']}/decisions/{pending[0]['call_id']}", json=val
    ).json["duplicate"]
    assert e.store.claim(e.owner) is None
    c.post(
        f"/api/jobs/{first['id']}/decisions/{pending[1]['call_id']}",
        json={"decision": "complete", "result": {"answer": "Celsius"}},
    )
    final = execute_next(e)
    assert final["status"] == "succeeded", final["error"]
    assert "Celsius" in json.dumps(final["brain"])


def test_human_choices_from_real_model_payload_suspend_and_resume(make_app):
    question = "Por que você quer reconstruir a habilidade?"
    choices = [
        {"Eu queria dizer outra coisa (especificar)": ""},
        {"Quero mudar o catálogo ou a especificação antes de reconstruir": ""},
        {"Reconstruir do mesmo jeito, gerando novas versões candidatas": ""},
        {"Foi um engano, deixa como está": ""},
    ]
    app, e, adapter = make_app(
        [
            [("ask_human", {"question": question, "choices": choices})],
            "Received answer",
        ]
    )
    e.store.enqueue("Rebuild?")
    first = execute_next(e)
    assert first["status"] == "waiting"
    client = app.test_client()
    pending = client.get(f"/api/jobs/{first['id']}").json["pending"][0]
    assert pending["_result"]["__pua__"]["payload"]["choices"] == [
        next(iter(c)) for c in choices
    ]
    assert len(adapter.calls) == 1  # No second model step before an answer.
    assert e.store.claim(e.owner) is None
    answer = "Foi um engano, deixa como está"
    response = client.post(
        f"/api/jobs/{first['id']}/decisions/{pending['call_id']}",
        json={"decision": "complete", "result": {"answer": answer}},
    )
    assert response.status_code == 200
    final = execute_next(e)
    assert final["status"] == "succeeded"
    assert answer in json.dumps(final["brain"], ensure_ascii=False)


def test_queue_one_writer_per_session(make_app):
    _, e, _ = make_app()
    if e.db.engine.dialect.name != "postgresql":
        import pytest

        pytest.skip("Concurrent claiming requires PostgreSQL row locks")
    for i in range(3):
        e.store.enqueue(str(i))
    other = e.store.enqueue("other", None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: e.store.claim(e.owner), range(4)))
    claims = [x for x in claims if x]
    assert len(claims) == 2
    assert len({x["session_id"] for x in claims}) == 2


def test_expired_lease_never_automatically_reexecutes(make_app):
    _, e, _ = make_app()
    e.store.enqueue("do effect")
    claimed = e.store.claim(e.owner)
    with e.db.transaction() as s:
        s.get(Job, claimed["id"]).lease_until = time.time() - 5
    e.store.recover()
    with e.db.transaction() as s:
        job = s.get(Job, claimed["id"])
        assert job.status == "interrupted"
        assert "Review" in job.error
    assert e.store.claim(e.owner) is None


def test_pause_resume_and_cancel(make_app):
    app, e, adapter = make_app(["Before pause", "After pause"])
    first = e.store.enqueue("hello")
    e.store.control(first["id"], "pause")
    assert e.store.claim(e.owner) is None
    e.store.control(first["id"], "resume")
    assert execute_next(e)["status"] == "succeeded"
    cancelled = e.store.enqueue("never")
    e.store.control(cancelled["id"], "cancel")
    assert e.store.claim(e.owner) is None


def test_routine_narrative_cannot_assert_success_without_execution(make_app):
    _, e, adapter = make_app(["I am done"])
    routine = e.routines.put(
        {
            "name": "weather",
            "prompt": "weather",
            "expectation": "city and temperature",
            "output_schema": {"type": "object", "required": ["city"]},
        }
    )
    job = e.routines.run(routine["id"])
    finished = execute_next(e)
    assert finished["status"] == "succeeded"  # conversation completed
    assert finished["outcome"]["done"] is True
    assert finished["outcome"]["success"] is None  # no action executed
    adapter.replies = [
        [
            (
                "finish_execution",
                {
                    "output": {"city": "NY"},
                    "evidence": "API returned NY",
                },
            )
        ],
        "Complete",
    ]
    e.routines.run(routine["id"])
    assert execute_next(e)["status"] == "succeeded"
    assert any(x["type"] == "execution_report" for x in e.store.events(MAIN_SESSION))


def test_scheduler_idempotent_and_overlap(make_app):
    _, e, _ = make_app()
    r = e.routines.put(
        {
            "name": "daily",
            "prompt": "run",
            "expectation": "done",
            "cron": "0 8 * * *",
            "enabled": True,
            "overlap": "skip",
        }
    )
    from fluxyr_agent.models import Routine

    with e.db.transaction() as s:
        s.get(Routine, r["id"]).next_run = time.time() - 1
    e.routines.tick()
    e.routines.tick()
    with e.db.transaction() as s:
        assert len(list(s.scalars(select(Job)))) == 1
    with e.db.transaction() as s:
        s.get(Routine, r["id"]).next_run = time.time() - 1
    e.routines.tick()
    with e.db.transaction() as s:
        assert len(list(s.scalars(select(Job)))) == 1


def test_effect_replay_and_uncertain_claim(make_app):
    _, e, _ = make_app()
    job = e.store.enqueue("effect")
    calls = []
    result = e.effects.run(
        job["id"], "example", 1, {"a": 1}, lambda: calls.append(1) or {"ok": True}
    )
    assert e.effects.run(job["id"], "example", 1, {"a": 1}, lambda: 1) == result
    assert len(calls) == 1
    import pytest

    def fail():
        raise RuntimeError("network lost after send")

    with pytest.raises(RuntimeError):
        e.effects.run(job["id"], "example", 2, {}, fail)
    assert "uncertain" in e.effects.run(job["id"], "example", 2, {}, dict)["error"]


def test_cancelling_queued_message_does_not_unblock_waiting_session(make_app):
    _, e, _ = make_app([[("ask_human", {"question": "Required input"})]])
    e.store.enqueue("wait")
    first = execute_next(e)
    second = e.store.enqueue("queued")
    third = e.store.enqueue("also queued")
    e.store.control(second["id"], "cancel")
    assert e.store.claim(e.owner) is None
    with e.db.transaction() as s:
        assert s.get(Session, MAIN_SESSION).status == "waiting"
