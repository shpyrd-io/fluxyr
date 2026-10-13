"""Live delivery without polling per token, including commit/wait races."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from fluxyr.database import MAIN_SESSION
from fluxyr.models import Event


def decode(frame):
    return json.loads(frame.decode().split("data: ", 1)[1])


def test_notify_only_after_commit_and_only_interested_sessions(make_app):
    _, engine, _ = make_app()
    signal = engine.db.event_signal
    with (
        signal.subscribe(MAIN_SESSION) as first,
        signal.subscribe(MAIN_SESSION) as second,
        signal.subscribe(None) as all_sessions,
        signal.subscribe("other") as unrelated,
    ):
        with engine.db.transaction() as session:
            session.add(
                Event(session_id=MAIN_SESSION, type="delta", payload={"text": "a"})
            )
            session.flush()
            assert not first.is_set()
            assert not all_sessions.is_set()
        assert first.is_set() and second.is_set() and all_sessions.is_set()
        assert not unrelated.is_set()
        # The notification is a hint; contents are already durably readable.
        assert engine.store.events(MAIN_SESSION)[0]["payload"]["text"] == "a"
    assert not signal._subscribers


def test_rollback_never_notifies_or_delivers(make_app):
    _, engine, _ = make_app()
    with engine.db.event_signal.subscribe(MAIN_SESSION) as ready:
        with (
            pytest.raises(RuntimeError, match="rollback"),
            engine.db.transaction() as session,
        ):
            session.add(Event(session_id=MAIN_SESSION, type="delta", payload={}))
            session.flush()
            raise RuntimeError("rollback")
        assert not ready.is_set()
        assert engine.store.events(MAIN_SESSION) == []


def test_nested_sqlite_emit_waits_for_outer_commit(make_app):
    _, engine, _ = make_app()
    if not engine.db.sqlite:
        pytest.skip("Only SQLite reuses the outer transaction for Store.emit")
    with engine.db.event_signal.subscribe(MAIN_SESSION) as ready:
        with engine.db.transaction():
            engine.store.emit(MAIN_SESSION, None, "delta", {"text": "committed"})
            assert not ready.is_set()
        assert ready.is_set()
        ready.clear()
        with pytest.raises(RuntimeError), engine.db.transaction():
            engine.store.emit(MAIN_SESSION, None, "delta", {"text": "rolled back"})
            assert not ready.is_set()
            raise RuntimeError()
        assert not ready.is_set()
        assert len(engine.store.events(MAIN_SESSION)) == 1


def test_queue_events_also_notify(make_app):
    _, engine, _ = make_app()
    with engine.db.event_signal.subscribe(MAIN_SESSION) as ready:
        engine.store.enqueue("Hello")
        assert ready.is_set()


def test_sse_wakes_without_polling_and_reconnects_at_cursor(make_app, monkeypatch):
    app, engine, _ = make_app()
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}&view=chat", buffered=False
    )
    frames = iter(response.response)
    assert next(frames) == b": heartbeat\n\n"
    signal = engine.db.event_signal
    ready = next(iter(signal._subscribers[MAIN_SESSION]))
    waiting = threading.Event()
    original_wait = ready.wait
    original_events = engine.store.events
    reads = []

    def wait(timeout):
        waiting.set()
        return original_wait(timeout)

    def events(*args, **kwargs):
        reads.append(1)
        return original_events(*args, **kwargs)

    monkeypatch.setattr(ready, "wait", wait)
    monkeypatch.setattr(engine.store, "events", events)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            received = pool.submit(next, frames)
            assert waiting.wait(2)
            assert reads == []  # Waiting releases the DB and performs no reads.
            event_id = engine.store.emit(MAIN_SESSION, None, "delta", {"text": "Olá"})
            result = decode(received.result(timeout=2))
            assert result["id"] == event_id
            assert result["payload"]["text"] == "Olá"
            assert len(reads) == 1
    finally:
        response.close()
    assert not signal._subscribers
    newer_id = engine.store.emit(MAIN_SESSION, None, "delta", {"text": " again"})
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}&after=0",
        headers={"Last-Event-ID": str(event_id)},
        buffered=False,
    )
    try:
        assert decode(next(iter(response.response)))["id"] == newer_id
    finally:
        response.close()
    assert not signal._subscribers


def test_commit_between_empty_query_and_wait_is_not_lost(make_app):
    app, engine, _ = make_app()
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}", buffered=False
    )
    frames = iter(response.response)
    assert next(frames) == b": heartbeat\n\n"
    # Generator is suspended at yield, after the query but before ready.wait.
    event_id = engine.store.emit(MAIN_SESSION, None, "delta", {"text": "race"})
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert decode(pool.submit(next, frames).result(timeout=2))["id"] == event_id
    finally:
        response.close()


def test_fallback_recovers_external_writes(make_app, monkeypatch):
    app, engine, _ = make_app()
    monkeypatch.setattr(engine.db.event_signal, "fallback_seconds", 0.05)
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}", buffered=False
    )
    frames = iter(response.response)
    assert next(frames) == b": heartbeat\n\n"
    # Bypass Database.transaction to simulate another process with no local hint.
    with engine.db.factory.begin() as session:
        row = Event(session_id=MAIN_SESSION, type="delta", payload={"text": "external"})
        session.add(row)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert decode(pool.submit(next, frames).result(timeout=2))["id"] == row.id
    finally:
        response.close()


def test_replay_drains_full_pages_without_waiting(make_app, monkeypatch):
    app, engine, _ = make_app()
    monkeypatch.setattr(engine.store, "event_page_size", 2)
    for index in range(5):
        engine.store.emit(MAIN_SESSION, None, "delta", {"text": str(index)})
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}", buffered=False
    )
    frames = iter(response.response)
    ready = next(iter(engine.db.event_signal._subscribers[MAIN_SESSION]))

    def unexpected_wait(_):
        pytest.fail("Must drain durable history before waiting")

    monkeypatch.setattr(ready, "wait", unexpected_wait)
    try:
        assert [decode(next(frames))["payload"]["text"] for _ in range(5)] == [
            str(i) for i in range(5)
        ]
        assert next(frames) == b": heartbeat\n\n"
    finally:
        response.close()


def test_idle_stream_does_not_query_on_unrelated_notifications(make_app, monkeypatch):
    app, engine, _ = make_app()
    response = app.test_client().get(
        f"/api/events?session_id={MAIN_SESSION}", buffered=False
    )
    frames = iter(response.response)
    assert next(frames) == b": heartbeat\n\n"
    ready = next(iter(engine.db.event_signal._subscribers[MAIN_SESSION]))

    def wait(timeout):
        assert 9 < timeout <= 10
        engine.db.event_signal.publish({"unrelated"})
        assert not ready.is_set()
        raise RuntimeError("stop idle wait")

    def unexpected_query(*args, **kwargs):
        pytest.fail("Idle wait must not query the database")

    monkeypatch.setattr(ready, "wait", wait)
    monkeypatch.setattr(engine.store, "events", unexpected_query)
    try:
        with pytest.raises(RuntimeError, match="stop idle wait"):
            next(frames)
    finally:
        response.close()
    assert not engine.db.event_signal._subscribers
