from sqlalchemy import event

from fluxyr.activity import ActivityEmitter
from fluxyr.database import MAIN_SESSION
from fluxyr.models import Event, Job


def test_compact_jobs_preserve_human_cards_without_source_or_results(make_app):
    app, e, _ = make_app()
    parent = e.store.enqueue("Parent")
    child = e.store.enqueue(
        "x" * 20000,
        session_id=None,
        inputs={"parent_job_id": parent["id"], "source": "y" * 100000},
    )
    with e.db.transaction() as s:
        row = s.get(Job, child["id"])
        row.status = "waiting"
        row.outcome = {"output": "z" * 100000}
        row.brain = {
            "pending_tools": [
                {
                    "name": "manage_vault_credential",
                    "call_id": "one",
                    "_status": "parked",
                    "arguments": {"source": "x" * 100000},
                    "_result": {
                        "__pua__": {
                            "title": "Credential",
                            "payload": {"token_url": "https://example.invalid/token"},
                        },
                        "internal": "z" * 100000,
                    },
                }
            ]
        }
    response = app.test_client().get("/api/jobs?summary=1")
    assert len(response.data) < 2000
    result = next(j for j in response.json if j["id"] == child["id"])
    assert result["input"] == {"parent_job_id": parent["id"]}
    assert result["pending"][0]["_result"]["__pua__"]["title"] == "Credential"
    assert "outcome" not in result and "arguments" not in result["pending"][0]


def test_usage_delta_and_unchanged_response_do_not_rebuild_report(make_app):
    app, e, _ = make_app()
    job = e.store.enqueue("Usage")

    def usage(call, tokens):
        return e.store.emit(
            MAIN_SESSION,
            job["id"],
            "usage",
            {"model_call_id": call, "input_tokens": tokens, "available": True},
        )

    first = usage("first", 10)
    client = app.test_client()
    full = client.get(f"/api/usage?session_id={MAIN_SESSION}").json
    assert full["by_call"]["first"]["tokens"] == 10
    sql = []
    event.listen(
        e.db.engine,
        "before_cursor_execute",
        lambda c, cur, statement, *a: sql.append(statement),
    )
    unchanged = client.get(f"/api/usage?session_id={MAIN_SESSION}&after={first}")
    assert unchanged.json == {"cursor": first, "unchanged": True}
    assert len(unchanged.data) < 100
    assert len([q for q in sql if q.startswith("SELECT")]) == 1
    usage("second", 20)
    delta = client.get(f"/api/usage?session_id={MAIN_SESSION}&after={first}").json
    assert set(delta["by_call"]) == {"second"}
    assert delta["total"]["tokens"] == 30


def test_stream_fragments_are_batched_without_losing_text_or_order(make_app):
    _, e, _ = make_app()
    job = e.store.enqueue("Stream")
    emit = ActivityEmitter(e.store, job, interval=60)
    emit("model_start", {})
    for block, kind in (("a", "reasoning"), ("b", "delta")):
        emit("stream_open", {"block_id": block, "kind": kind})
        for i in range(200):
            emit(kind, {"block_id": block, "kind": kind, "text": f"{i}ç"})
        emit("stream_close", {"block_id": block})
    emit("model_end", {})
    events = e.store.events(MAIN_SESSION)
    fragments = [ev for ev in events if ev["type"] in ("delta", "reasoning")]
    assert len(fragments) < 10
    for block in ("a", "b"):
        assert "".join(
            ev["payload"]["text"]
            for ev in fragments
            if ev["payload"]["block_id"] == block
        ) == "".join(f"{i}ç" for i in range(200))
        close = next(
            ev["id"]
            for ev in events
            if ev["type"] == "stream_close" and ev["payload"]["block_id"] == block
        )
        assert all(
            ev["id"] < close for ev in fragments if ev["payload"]["block_id"] == block
        )


def test_chat_projection_excludes_unused_job_payloads(make_app):
    app, e, _ = make_app()
    e.store.enqueue("x" * 10000, inputs={"source": "y" * 100000})
    response = app.test_client().get(f"/api/sessions/{MAIN_SESSION}?view=chat")
    assert "jobs" not in response.json
    assert "brain" not in response.json["session"]
    assert response.json["event_cursor"] > 0


def test_usage_history_is_paged_and_other_sessions_do_not_send_call_maps(make_app):
    app, e, _ = make_app()
    job = e.store.enqueue("Many requests")
    child = e.store.enqueue(
        "Child", session_id=None, inputs={"parent_job_id": job["id"]}
    )
    with e.db.transaction() as s:
        for i in range(450):
            s.add(
                Event(
                    session_id=MAIN_SESSION,
                    job_id=job["id"],
                    type="usage",
                    payload={
                        "model_call_id": f"call-{i}",
                        "input_tokens": 1,
                        "available": True,
                    },
                )
            )
        s.add(
            Event(
                session_id=child["session_id"],
                job_id=child["id"],
                type="usage",
                payload={
                    "model_call_id": "child-call",
                    "input_tokens": 3,
                    "available": True,
                },
            )
        )
    client = app.test_client()
    cursor, calls, totals = None, {}, {}
    pages = 0
    while True:
        response = client.get(
            f"/api/usage?session_id={MAIN_SESSION}"
            + (f"&after={cursor}" if cursor is not None else "")
        )
        page = response.json
        assert len(page["by_call"]) <= 200
        assert len(response.data) < 80000
        calls.update(page["by_call"])
        totals.update(page["by_job"])
        cursor = page["cursor"]
        pages += 1
        if not page["has_more"]:
            break
        assert pages < 5
    assert len(calls) == 450 and "child-call" not in calls
    assert totals[job["id"]]["tokens"] == 453
    assert totals[child["id"]]["tokens"] == 3
    assert pages == 3
