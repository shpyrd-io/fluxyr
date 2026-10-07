import json

from sqlalchemy import select

from fluxyr_agent.models import Event
from fluxyr_agent.persistence import bounded, event_payload, restore_tool_identity


def test_large_inspection_keeps_lifecycle_identity_in_postgres_and_replay(make_app):
    _, engine, _ = make_app()
    job = engine.store.enqueue("Inspect execution")
    identity = {
        "tool_call_id": "inspection-call",
        "tool_name": "inspect_execution",
        "index": 7,
        "batch_id": "batch",
        "parallel": True,
        "batch_size": 2,
    }
    engine.store.emit(
        job["session_id"],
        job["id"],
        "tool_begin",
        {**identity, "args": {"job_id": "inspected"}},
    )
    result = {"events": [{"payload": "😀" * 100_000}], "api_key": "must-not-persist"}
    engine.store.emit(
        job["session_id"],
        job["id"],
        "tool_end",
        {**identity, "result": result, "mode": "continue"},
    )
    events = engine.store.events(job["session_id"])
    end = next(e["payload"] for e in events if e["type"] == "tool_end")
    assert all(end[k] == v for k, v in identity.items())
    assert end["result"]["truncated"]
    serialized = json.dumps(end, ensure_ascii=False)
    assert "must-not-persist" not in serialized
    assert len(serialized.encode()) < 100_000


def test_historical_truncated_event_recovers_only_top_level_identity_without_rewriting(
    make_app,
):
    _, engine, _ = make_app()
    job = engine.store.enqueue("Inspect execution")
    old = bounded(
        {
            "event": "tool_end",
            "tool_call_id": "correct-call",
            "tool_name": "inspect_execution",
            "result": {"tool_call_id": "nested-unrelated-call", "text": "x" * 200_000},
        }
    )
    with engine.db.transaction() as s:
        s.add(
            Event(
                session_id=job["session_id"],
                job_id=job["id"],
                type="tool_end",
                payload=old,
            )
        )
    replay = next(
        e["payload"]
        for e in engine.store.events(job["session_id"])
        if e["type"] == "tool_end"
    )
    assert replay["tool_call_id"] == "correct-call"
    assert replay["tool_name"] == "inspect_execution"
    assert replay["result"]["truncated"]
    with engine.db.transaction() as s:
        assert s.scalar(select(Event).where(Event.type == "tool_end")).payload == old
    malformed = {
        "truncated": True,
        "summary": '{"result": {"tool_call_id": "unrelated"',
    }
    assert restore_tool_identity("tool_end", malformed) == malformed


def test_large_failures_and_arguments_keep_identity_and_error_status():
    payload = event_payload(
        "tool_end",
        {
            "tool_call_id": "failed-call",
            "tool_name": "inspect_execution",
            "mode": "continue",
            "result": {
                "error": "Inspection failed",
                "success": False,
                "details": "x" * 200_000,
            },
        },
    )
    assert payload["result"]["error"] == "Inspection failed"
    assert payload["result"]["success"] is False
    begin = event_payload(
        "tool_begin",
        {
            "tool_call_id": "code",
            "tool_name": "create_action",
            "args": {"source": "x" * 200_000},
        },
    )
    assert begin["tool_call_id"] == "code"
    assert begin["args"]["truncated"]
