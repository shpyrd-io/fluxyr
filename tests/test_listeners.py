import json
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from fluxyr.listeners import MAX_FAILURES, ListenerProcess
from fluxyr.models import (
    IncomingReceipt,
    Job,
    ListenerConfig,
    ListenerVersion,
    ReactiveConfig,
    Session,
)

pytestmark = pytest.mark.integration

IDLE = """def run(ctx):
    ctx.ready()
    while not ctx.wait(0.05):
        pass
"""


def setup(make_app, monkeypatch):
    app, e, _ = make_app()
    # These tests exercise real listener subprocesses without creating a new venv each time.
    monkeypatch.setattr(e.runner, "environment", lambda *a, **kw: Path(sys.executable))
    r = e.routines.put(
        {"trigger": "worker", "name": "Listener", "prompt": "Handle incoming message"}
    )
    return app, e, r["id"]


def count(e, model):
    with e.db.transaction() as s:
        return s.scalar(select(func.count()).select_from(model))


def wait_for(fn, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Condition did not become true")


def test_worker_contract_and_http(make_app, monkeypatch):
    app, e, rid = setup(make_app, monkeypatch)
    c = app.test_client()
    assert e.routines.list()[0]["reactive"]["webhook_path"] is None
    with e.db.transaction() as s:
        token = s.get(ReactiveConfig, rid).token
    assert c.post("/api/webhooks/" + token, json={}).status_code == 404
    assert c.post(f"/api/routines/{rid}/run").status_code == 400
    assert (
        c.post(
            f"/api/routines/{rid}/worker/versions", json={"source": "x=1"}
        ).status_code
        == 400
    )
    version = c.post(f"/api/routines/{rid}/worker/versions", json={"source": IDLE}).json
    assert (
        c.patch(
            f"/api/routines/{rid}/worker",
            json={"version_id": version["id"], "mode": "active"},
        ).status_code
        == 400
    )
    result = c.post(
        f"/api/routines/{rid}/worker/versions/{version['id']}/test", json={"seconds": 1}
    ).json
    assert result["status"] == "succeeded", result
    assert (
        c.patch(
            f"/api/routines/{rid}/worker",
            json={"version_id": version["id"], "mode": "active"},
        ).status_code
        == 200
    )
    assert e.listeners.thread is None  # HTTP alone never launches background listeners.
    assert count(e, Job) == 0
    assert count(e, Session) == 1
    assert c.get(f"/api/routines/{rid}/worker").json["versions"][0]["tested_at"]


def test_emit_checkpoint_dedup_and_replay(make_app, monkeypatch):
    _, e, rid = setup(make_app, monkeypatch)
    source = """def run(ctx):
    ctx.ready()
    ctx.emit(session_key="alice", event_id="message-1", payload={"text": "hello"}, checkpoint={"cursor": 1})
    while not ctx.wait(0.05):
        pass
"""
    v = e.listeners.create_version(rid, source)
    assert e.listeners.test(rid, v["id"], 1)["status"] == "succeeded"
    assert e.listeners.inspect(rid)["checkpoint"] is None
    assert count(e, Job) == 0
    receipt = e.reactive.receipts(rid)["items"][0]
    e.listeners.configure(rid, {"version_id": v["id"], "mode": "active"})
    e.reactive.replay(rid, receipt["id"])
    assert e.reactive.process_next()
    h = ListenerProcess(e.listeners, v, 1)
    msg = {
        "op": "emit",
        "session_key": "alice",
        "event_id": "message-1",
        "payload": {"text": "hello"},
        "checkpoint": {"cursor": 1},
    }
    assert h.rpc(msg)["duplicate"]
    assert e.listeners.inspect(rid)["checkpoint"] == {"cursor": 1}
    h.rpc({**msg, "event_id": "message-2", "checkpoint": {"cursor": 2}})
    assert e.reactive.process_next()
    assert count(e, Job) == 2
    assert count(e, Session) == 2  # one main session + Alice
    with e.db.transaction() as s:
        jobs = list(s.scalars(select(Job)))
        assert jobs[0].session_id == jobs[1].session_id
        assert jobs[0].input["routine_execution"] is True
    with pytest.raises(ValueError, match="stable event_id"):
        h.rpc({**msg, "event_id": None})


def test_raw_collection_then_normalizer(make_app, monkeypatch):
    _, e, rid = setup(make_app, monkeypatch)
    v = e.listeners.create_version(rid, IDLE)
    h = ListenerProcess(e.listeners, v, 0)
    r = h.rpc(
        {
            "op": "receive",
            "event_id": "raw-1",
            "envelope": {"json": {"sender": "alice"}},
        }
    )
    assert r["status"] == "collected"
    assert e.listeners.test(rid, v["id"], 1)["status"] == "succeeded"
    e.listeners.configure(rid, {"version_id": v["id"], "mode": "active"})
    with pytest.raises(ValueError, match="normalizer"):
        h.rpc(
            {
                "op": "receive",
                "event_id": "raw-2",
                "envelope": {"json": {"sender": "alice"}},
            }
        )
    with pytest.raises(ValueError, match="normalizer"):
        e.reactive.replay(rid, r["receipt_id"])
    normal = e.reactive.create_version(
        rid,
        'from fluxyr import params, output\noutput({"events":[{"session_key":params["json"]["sender"],"payload": params["json"]}]})',
    )
    assert e.reactive.test(rid, r["receipt_id"], normal["id"])["status"] == "succeeded"
    e.reactive.configure(rid, {"normalizer_id": normal["id"]})
    h.rpc(
        {
            "op": "receive",
            "event_id": "raw-2",
            "envelope": {"json": {"sender": "alice"}},
        }
    )
    assert e.reactive.process_next()
    assert count(e, Job) == 1


def test_real_socket_listener_collects_separate_sessions(make_app, monkeypatch):
    import socketserver

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for number, sender in enumerate(["alice", "bob", "alice"]):
                self.wfile.write(
                    (json.dumps({"id": str(number), "sender": sender}) + "\n").encode()
                )
                self.wfile.flush()
            self.rfile.read(1)  # Keep connection alive until the client stops.

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, e, rid = setup(make_app, monkeypatch)
        source = f"""import socket, json

def run(ctx):
    with socket.create_connection(("127.0.0.1", {server.server_address[1]}), timeout=2) as sock:
        ctx.ready()
        stream = sock.makefile("rb")
        for _ in range(3):
            item = json.loads(stream.readline())
            ctx.emit(session_key=item["sender"], event_id=item["id"], payload=item)
        while not ctx.wait(0.05):
            pass
"""
        v = e.listeners.create_version(rid, source)
        result = e.listeners.test(rid, v["id"], 1)
        assert result["status"] == "succeeded", result
        assert result["events"] == 3
        assert count(e, Job) == 0
        e.listeners.configure(rid, {"version_id": v["id"], "mode": "active"})
        for r in e.reactive.receipts(rid)["items"]:
            e.reactive.replay(rid, r["id"])
        while e.reactive.process_next():
            pass
        assert count(e, Job) == 3 and count(e, Session) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_shutdown_restart_and_failure_budget(make_app, monkeypatch):
    _, e, rid = setup(make_app, monkeypatch)
    v = e.listeners.create_version(rid, IDLE)
    e.listeners.configure(rid, {"version_id": v["id"], "mode": "collecting"})
    e.listeners.tick()
    h = e.listeners.handles[rid]
    wait_for(lambda: h.ready)
    with pytest.raises(ValueError, match="Stop"):
        e.listeners.test(rid, v["id"], 1)
    e.listeners.configure(rid, {"mode": "disabled"})
    e.listeners.tick()
    h.thread.join(4)
    assert not h.thread.is_alive()
    e.listeners.tick()
    bad = e.listeners.create_version(
        rid, "def run(ctx):\n    raise RuntimeError('broken listener')"
    )
    e.listeners.configure(rid, {"version_id": bad["id"], "mode": "collecting"})
    for i in range(MAX_FAILURES):
        e.listeners.tick()
        h = e.listeners.handles[rid]
        h.thread.join(5)
        assert not h.thread.is_alive()
        assert e.listeners.inspect(rid)["failures"] == i + 1
        e.listeners.tick()  # Removes the finished handle; respects backoff.
        with e.db.transaction() as s:
            s.get(ListenerConfig, rid).retry_at = 0
    e.listeners.tick()
    assert rid not in e.listeners.handles
    assert e.listeners.inspect(rid)["status"] == "failed"
    e.listeners.configure(rid, {"version_id": v["id"], "restart": True})
    e.listeners.tick()
    wait_for(lambda: e.listeners.handles[rid].ready)
    e.listeners.stop()
    assert not e.listeners.handles[rid].thread.is_alive()


def test_vault_redaction_and_private_transport(make_app, monkeypatch):
    _, e, rid = setup(make_app, monkeypatch)
    monkeypatch.setattr(
        e.vault, "resolve", lambda name: {"password": "secret-987654321"}
    )
    source = """import sys, time

def run(ctx):
    value = ctx.secret("Mail")
    sys.stdout.write(value["password"][:5]); sys.stdout.flush()
    time.sleep(0.05)
    print(value["password"][5:])
    ctx.ready()
    while not ctx.wait(0.05):
        pass
"""
    v = e.listeners.create_version(rid, source, secrets=["Mail"])
    result = e.listeners.test(rid, v["id"], 1)
    assert result["status"] == "succeeded", result
    assert "secret-987654321" not in json.dumps(e.listeners.inspect(rid))
    assert "[secret]" in result["logs"]
    assert not list((e.settings.runtime / "listeners").iterdir())
    h = ListenerProcess(e.listeners, v, 0)
    with pytest.raises(ValueError, match="declared"):
        h.rpc({"op": "secret", "name": "other"})


def test_no_ready_or_early_exit_fails_test(make_app, monkeypatch):
    _, e, rid = setup(make_app, monkeypatch)
    for source in ["def run(ctx):\n    return", "def run(ctx):\n    ctx.wait(10)"]:
        v = e.listeners.create_version(rid, source)
        result = e.listeners.test(rid, v["id"], 1)
        assert result["status"] == "failed", result
        assert e.listeners.version(rid, v["id"])["tested_at"] is None


def test_queue_backpressure_and_checkpoint_rollback(make_app, monkeypatch):
    from werkzeug.exceptions import TooManyRequests

    from fluxyr import reactive

    _, e, rid = setup(make_app, monkeypatch)
    v = e.listeners.create_version(rid, IDLE)
    with e.db.transaction() as s:
        s.get(ListenerVersion, v["id"]).tested_at = time.time()
    e.listeners.configure(rid, {"version_id": v["id"], "mode": "active"})
    monkeypatch.setattr(reactive, "MAX_RECEIPTS", 1)
    h = ListenerProcess(e.listeners, v, 1)
    msg = {
        "op": "emit",
        "session_key": "alice",
        "event_id": "1",
        "payload": {},
        "checkpoint": {"cursor": 1},
    }
    h.rpc(msg)
    with pytest.raises(TooManyRequests):
        h.rpc({**msg, "event_id": "2", "checkpoint": {"cursor": 2}})
    assert e.listeners.inspect(rid)["checkpoint"] == {"cursor": 1}
    assert count(e, IncomingReceipt) == 1
    assert e.reactive.process_next()
    with pytest.raises(TooManyRequests):
        h.rpc({**msg, "event_id": "2"})


def test_cross_manager_fence_and_idle_query_budget(make_app, monkeypatch):
    from sqlalchemy import event

    from fluxyr.listeners import Listeners

    _, e, rid = setup(make_app, monkeypatch)
    version = e.listeners.create_version(rid, IDLE)
    e.listeners.configure(rid, {"version_id": version["id"], "mode": "collecting"})
    e.listeners.start()
    try:
        wait_for(lambda: rid in e.listeners.handles and e.listeners.handles[rid].ready)
        other = Listeners(e)
        result = other.test(rid, version["id"], 1)
        assert result["status"] == "failed", result
        assert (
            result["id"] is None
        )  # Could not acquire ownership, did not create a run.
        queries = []

        def capture(*args):
            queries.append(args[2])

        event.listen(e.db.engine, "before_cursor_execute", capture)
        try:
            time.sleep(2.2)
        finally:
            event.remove(e.db.engine, "before_cursor_execute", capture)
        assert len(queries) <= 8, queries  # No per-fragment/per-loop database poll.
    finally:
        e.listeners.stop()


def test_forced_stop_and_fast_traceback(make_app, monkeypatch):
    _, e, rid = setup(make_app, monkeypatch)
    v = e.listeners.create_version(
        rid, "def run(ctx):\n    raise RuntimeError('diagnostic detail')"
    )
    result = e.listeners.test(rid, v["id"], 1)
    assert "diagnostic detail" in result["logs"]
    # Deliberately ignores graceful shutdown, as some blocking SDK iterators do.
    source = "import time\ndef run(ctx):\n    ctx.ready()\n    while True:\n        time.sleep(60)"
    v = e.listeners.create_version(rid, source)
    started = time.monotonic()
    result = e.listeners.test(rid, v["id"], 1)
    assert result["status"] == "succeeded", result
    assert time.monotonic() - started < 5


@pytest.mark.skipif(sys.platform != "linux", reason="Landlock is Linux-only")
def test_listener_uses_real_cached_environment_and_landlock(make_app):
    _, e, _ = make_app(execution_mode="landlock")
    r = e.routines.put(
        {"trigger": "worker", "name": "Protected", "prompt": "Handle event"}
    )
    protected = e.settings.root / "do-not-write"
    source = f"""from pathlib import Path

def run(ctx):
    try:
        Path({str(protected)!r}).write_text("bad")
    except PermissionError:
        print("write blocked")
    else:
        raise RuntimeError("Protection failed")
    ctx.ready()
    while not ctx.wait(0.1):
        pass
"""
    v = e.listeners.create_version(r["id"], source)
    first = e.listeners.test(r["id"], v["id"], 1)
    assert first["status"] == "succeeded", first
    assert "write blocked" in first["logs"]
    assert not protected.exists()
    second = e.listeners.test(r["id"], v["id"], 1)
    assert second["status"] == "succeeded", second
    assert "Preparing Python" not in second["logs"]


def test_disable_during_dispatch_preserves_direct_event_for_replay(
    make_app, monkeypatch
):
    _, e, rid = setup(make_app, monkeypatch)
    v = e.listeners.create_version(rid, IDLE)
    with e.db.transaction() as s:
        s.get(ListenerVersion, v["id"]).tested_at = time.time()
    e.listeners.configure(rid, {"version_id": v["id"], "mode": "active"})
    h = ListenerProcess(e.listeners, v, 1)
    ack = h.rpc(
        {"op": "emit", "session_key": "alice", "event_id": "race", "payload": {}}
    )
    e.listeners.configure(rid, {"mode": "disabled"})
    with e.db.transaction() as s:
        receipt = s.get(IncomingReceipt, ack["receipt_id"])
        e.reactive._dispatch(s, receipt, receipt.result["normalization"])
        assert receipt.status == "collected" and receipt.result["worker_event"] is True
    e.listeners.configure(rid, {"mode": "active"})
    e.reactive.replay(rid, ack["receipt_id"])
    assert e.reactive.process_next()
    assert count(e, Job) == 1
    with e.db.transaction() as s:
        job = s.scalar(select(Job))
        assert job.input["listener_version_id"] == v["id"]
