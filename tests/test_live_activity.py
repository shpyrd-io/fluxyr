import json

from conftest import execute_next

from fluxyr.activity import ActivityEmitter
from fluxyr.streaming import StreamRecorder


def test_argument_activity_is_counted_without_persisting_contents(make_app):
    _, e, _ = make_app()
    job = e.store.enqueue("Create a skill")
    emit = ActivityEmitter(e.store, job, interval=10000)
    record = StreamRecorder(emit)
    emit("model_start", {})
    record(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "c", "name": "create_skill"},
        }
    )
    for _ in range(40):
        record(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": "PRIVATE_ARGUMENT",
                },
            }
        )
    record({"type": "content_block_stop", "index": 0})
    events = e.store.events(job["session_id"])
    activities = [ev["payload"] for ev in events if ev["type"] == "activity"]
    assert sum(p["chunks"] for p in activities) == 40
    assert sum(p["characters"] for p in activities) == 40 * len("PRIVATE_ARGUMENT")
    assert len(activities) < 8
    assert activities[0]["phase"] == "waiting_model"
    assert "PRIVATE_ARGUMENT" not in json.dumps(events)
    assert any(
        p["phase"] == "generating_arguments" and p["tool"] == "create_skill"
        for p in activities
    )


def test_activity_reaches_ancestors_and_nested_streams_without_text(make_app):
    _, e, _ = make_app()
    parent = e.store.enqueue("Workbench")
    child = e.store.enqueue("Builder", None, inputs={"parent_job_id": parent["id"]})
    grandchild = e.store.enqueue(
        "Nested execution", None, inputs={"parent_job_id": child["id"]}
    )
    emit = ActivityEmitter(e.store, grandchild)
    emit("substream_start", {"activity_scope": "nested", "tool_name": "tech_doc"})
    emit(
        "substream_delta",
        {"activity_scope": "nested", "tool_name": "tech_doc", "characters": 19},
    )
    emit("substream_end", {"activity_scope": "nested", "tool_name": "tech_doc"})
    for job, depth in [(parent, 3), (child, 2), (grandchild, 1)]:
        events = e.store.events(job["session_id"])
        stream = [
            x["payload"]
            for x in events
            if x["type"] == "activity" and x["payload"]["chunks"]
        ]
        assert len(stream) == 1 and stream[0]["depth"] == depth
        assert stream[0]["origin_job_id"] == grandchild["id"]
        assert stream[0]["characters"] == 19
        assert "text" not in stream[0]


def test_called_routine_records_parent_and_emits_immediate_card(make_app):
    _, e, _ = make_app()
    parent = e.store.enqueue("Run a routine")
    routine = e.routines.put({"name": "Routine", "prompt": "Inspect files"})
    child = e.routines.run(routine["id"], parent=parent, call_id="run")
    assert child["input"]["parent_job_id"] == parent["id"]
    events = e.store.events(parent["session_id"])
    assert any(
        ev["type"] == "execution_started" and ev["payload"]["job_id"] == child["id"]
        for ev in events
    )


def test_model_waiting_and_stream_activity_are_emitted_by_worker(make_app):
    _, e, _ = make_app(["Visible answer"])
    e.store.enqueue("Hello")
    job = execute_next(e)
    assert job["status"] == "succeeded"
    phases = [
        ev["payload"]["phase"]
        for ev in e.store.events(job["session_id"])
        if ev["type"] == "activity"
    ]
    assert phases[0] == "waiting_model"
    assert "streaming" in phases and "model_finished" in phases
