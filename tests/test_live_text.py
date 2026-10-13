import json
import threading
import time

from fluxyr.activity import ActivityEmitter
from fluxyr.database import MAIN_SESSION
from fluxyr.live_text import LiveText, LiveTextReader


def test_wire_text_arrives_before_commit_without_database_queries(
    make_app, monkeypatch
):
    app, engine, _ = make_app()
    job = engine.store.enqueue("Stream")
    emit = ActivityEmitter(engine.store, job, interval=10000)
    emit("stream_open", {"block_id": "a", "kind": "delta"})
    emit.fragment_time = time.monotonic()
    emit.last["main"] = time.monotonic()
    cursor = engine.store.event_cursor()
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}&after={cursor}&text=live",
        buffered=False,
    )
    frames = iter(response.response)
    assert next(frames) == b": heartbeat\n\n"
    original_events = engine.store.events

    def unexpected_query(*args, **kwargs):
        raise AssertionError("Wire fragments must not query the database")

    monkeypatch.setattr(engine.store, "events", unexpected_query)
    try:
        for index, part in enumerate(["Hi ", "🌍", "!"]):
            emit("delta", {"block_id": "a", "kind": "delta", "text": part})
            frame = next(frames)
            assert frame.startswith(b"event: fragment\n")
            assert b"\nid:" not in frame
            body = json.loads(frame.split(b"data: ")[1])
            assert body["payload"]["text"] == part
            assert body["payload"]["text_offset"] == [0, 3, 5][index]
            assert body["payload"]["text_end"] == [3, 5, 6][index]
            assert original_events(MAIN_SESSION, cursor) == []
    finally:
        response.close()
    assert not engine.db.live_text.readers
    emit("stream_close", {"block_id": "a"})
    saved = original_events(MAIN_SESSION, cursor)
    text = next(e["payload"] for e in saved if e["type"] == "delta")
    assert text["text"] == "Hi 🌍!"
    assert text["text_offset"] == 0 and text["text_end"] == 6


def test_background_routine_persists_without_browser_and_replays(make_app):
    app, engine, _ = make_app()
    routine = engine.routines.put({"name": "Background", "prompt": "Report"})
    job = engine.routines.run(routine["id"])
    sid = job["session_id"]
    assert sid != MAIN_SESSION
    emit = ActivityEmitter(engine.store, job, interval=10000)
    emit("stream_open", {"block_id": "background", "kind": "delta"})
    for _ in range(100):
        emit("delta", {"block_id": "background", "kind": "delta", "text": "Olá 🌍 "})
    emit("stream_close", {"block_id": "background"})
    assert engine.db.live_text.readers == {}
    events = engine.store.events(sid)
    deltas = [e for e in events if e["type"] == "delta"]
    assert len(deltas) == 2  # first fragment + final batch; not 100 writes
    assert "".join(e["payload"]["text"] for e in deltas) == "Olá 🌍 " * 100
    history = app.test_client().get(f"/api/sessions/{sid}/history").json
    text = next(e["payload"] for e in history["events"] if e["type"] == "delta")
    assert text["text"] == "Olá 🌍 " * 100
    assert text["text_end"] == 700
    assert not any(e["type"] == "delta" for e in engine.store.events(MAIN_SESSION))


def test_session_isolation_slow_reader_bounds_and_cleanup():
    bus = LiveText()
    ready = threading.Event()
    payload = {"block_id": "b", "text": "x" * 1000, "text_offset": 0, "text_end": 1000}
    with (
        bus.subscribe("routine", ready) as reader,
        bus.subscribe("main", threading.Event()) as main,
    ):
        for _ in range(1000):
            bus.publish("routine", "job", "delta", payload)
        assert ready.is_set()
        assert reader.size <= LiveTextReader.max_bytes
        assert len(reader.queue) < 100
        assert main.drain() == []
        assert reader.drain()
        assert reader.size == 0
        bus.publish("routine", "job", "tool_argument_delta", payload)
        assert reader.drain() == []
    assert bus.readers == {}


def test_legacy_sse_does_not_subscribe_to_speculative_text(make_app):
    app, engine, _ = make_app()
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}", buffered=False
    )
    assert engine.db.live_text.readers == {}
    response.close()
