import json

from sqlalchemy import event as sql_event

from fluxyr.database import MAIN_SESSION
from fluxyr.history import compact_events
from fluxyr.models import Event, Session


def test_paged_history_consolidates_text_and_loads_tool_details_on_demand(make_app):
    app, engine, _ = make_app()
    job = engine.store.enqueue("Question")
    with engine.db.transaction() as s:
        s.get(Session, MAIN_SESSION).brain = {"large": "private-state" * 10000}
        s.add(
            Event(
                session_id=MAIN_SESSION,
                job_id=job["id"],
                type="stream_open",
                payload={"block_id": "answer"},
            )
        )
        for i in range(2500):
            s.add(
                Event(
                    session_id=MAIN_SESSION,
                    job_id=job["id"],
                    type="delta",
                    payload={"block_id": "answer", "text": f"{i}ç "},
                )
            )
    tool_id = engine.store.emit(
        MAIN_SESSION,
        job["id"],
        "tool_end",
        {
            "tool_name": "read",
            "tool_call_id": "read-1",
            "result": {
                "content": "x" * 15000,
                "success": False,
                "error": "Broken",
                "version_id": "v1",
            },
        },
    )
    engine.store.emit(MAIN_SESSION, job["id"], "stream_close", {"block_id": "answer"})
    engine.store.emit(MAIN_SESSION, job["id"], "succeeded", {})
    sql = []
    sql_event.listen(
        engine.db.engine,
        "before_cursor_execute",
        lambda c, cur, statement, *a: sql.append(statement),
    )
    client = app.test_client()
    pages = []
    path = f"/api/sessions/{MAIN_SESSION}/history"
    while True:
        response = client.get(path)
        assert response.status_code == 200
        assert len(response.data) < 16000
        page = response.json
        pages.append(page)
        if page["before"] is None:
            break
        path = f"/api/sessions/{MAIN_SESSION}/history?before={page['before']}"
    assert len(pages) == 3
    events = [ev for page in reversed(pages) for ev in page["events"]]
    assert "".join(
        ev["payload"]["text"] for ev in events if ev["type"] == "delta"
    ) == "".join(f"{i}ç " for i in range(2500))
    assert len({ev["id"] for ev in events}) == len(events)
    tool = next(ev for ev in events if ev["id"] == tool_id)
    assert "content" not in tool["payload"]["result"]
    assert tool["payload"]["result"]["error"] == "Broken"
    assert (
        client.get("/api" + tool["details_url"]).json["payload"]["result"]["content"]
        == "x" * 15000
    )
    assert not any("sessions.brain" in query for query in sql)
    assert any(m["content"] == "Question" for page in pages for m in page["messages"])


def test_snapshot_cursor_and_incremental_catchup_do_not_replay_history(make_app):
    app, engine, _ = make_app()
    job = engine.store.enqueue("Question")
    first = engine.store.emit(
        MAIN_SESSION, job["id"], "delta", {"block_id": "b", "text": "Before"}
    )
    client = app.test_client()
    initial = client.get(f"/api/sessions/{MAIN_SESSION}/history").json
    assert initial["event_cursor"] == first
    later = engine.store.emit(
        MAIN_SESSION, job["id"], "delta", {"block_id": "b", "text": "After"}
    )
    response = client.get(
        f"/api/events?session_id={MAIN_SESSION}&view=chat&after={first}", buffered=False
    )
    try:
        wire = next(iter(response.response)).decode()
        assert f"id: {later}" in wire
        assert "Before" not in wire
        assert "After" in wire
    finally:
        response.close()
    update = client.get(f"/api/sessions/{MAIN_SESSION}/history?after={first}").json
    assert update["range_start"] == first
    assert [e["id"] for e in update["events"]] == [later]
    empty = client.get(f"/api/sessions/{MAIN_SESSION}/history?after={later}").json
    assert empty["events"] == []


def test_compaction_preserves_parallel_calls_error_and_human_wait():
    records = [
        {
            "id": 1,
            "job_id": "j",
            "type": "tool_batch_start",
            "payload": {
                "parallel": True,
                "calls": [{"tool_call_id": "a"}, {"tool_call_id": "b"}],
            },
        },
        {
            "id": 2,
            "job_id": "j",
            "type": "tool_end",
            "payload": {
                "tool_call_id": "a",
                "mode": "wait",
                "result": {"__human__": True, "large": "x" * 10000},
            },
        },
        {
            "id": 3,
            "job_id": "j",
            "type": "tool_end",
            "payload": {
                "tool_call_id": "b",
                "result": {"isError": True, "content": [{"text": "error"}]},
            },
        },
        {"id": 4, "job_id": "j", "type": "tool_batch_end", "payload": {}},
    ]
    result = compact_events(records)
    assert [e["id"] for e in result] == [1, 2, 3, 4]
    assert result[1]["payload"]["result"]["__human__"] is True
    assert result[2]["payload"]["result"]["error"]
    assert len(json.dumps(result)) < 1000
