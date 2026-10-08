import hashlib
import hmac
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import execute_next
from sqlalchemy import func, select

from fluxyr.models import (
    IncomingReceipt,
    Job,
    Session,
)
from fluxyr.reactive import MAX_BYTES, validate_output

SOURCE = """from fluxyr import params, output
items = params["json"]
if isinstance(items, dict):
    items = [items]
events = []
for item in items:
    if item["type"] == "presence":
        continue
    sender = item["sender"] if item["type"] == "message" else item["reply"]["sender"]
    if not sender:
        raise ValueError("Missing sender")
    events.append({"session_key": sender, "event_id": item["id"], "payload": item})
output({"events": events, "ignored_reason": "Presence update"} if not events else {"events": events})
"""


def setup(app, e):
    c = app.test_client()
    r = c.post(
        "/api/routines",
        json={
            "name": "Inbox",
            "prompt": "Respond to the message",
            "trigger": "reactive",
        },
    ).json
    rid, path = r["id"], r["reactive"]["webhook_path"]
    return c, rid, path


def count(e, model):
    with e.db.transaction() as s:
        return s.scalar(select(func.count()).select_from(model))


def activate(c, e, rid, path):
    first = c.post(
        path, json={"type": "message", "sender": "ana", "id": "sample"}
    ).json["receipt_id"]
    v = e.reactive.create_version(rid, SOURCE)
    assert e.reactive.test(rid, first, v["id"])["status"] == "succeeded"
    e.reactive.configure(rid, {"mode": "active", "normalizer_id": v["id"]})
    return first, v


def test_collecting_auth_formats_and_no_sessions(make_app):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    assert c.post(path, json={"hello": "á"}).json["status"] == "collected"
    text = c.post(path, data="Olá!", content_type="text/plain").json
    form = c.post(
        path + "?tag=a&tag=b",
        data="sender=ana&tag=x&tag=y",
        content_type="application/x-www-form-urlencoded",
    ).json
    assert e.reactive.receipt(rid, text["receipt_id"])["envelope"]["text"] == "Olá!"
    envelope = e.reactive.receipt(rid, form["receipt_id"])["envelope"]
    assert envelope["form"] == {"sender": ["ana"], "tag": ["x", "y"]}
    assert envelope["query"] == {"tag": ["a", "b"]}
    assert count(e, Session) == 1 and count(e, Job) == 0
    assert c.post(path, data="{bad", content_type="application/json").status_code == 400
    assert c.post(path, data="x" * (MAX_BYTES + 1)).status_code == 413
    assert c.post("/api/webhooks/no-such-token", json={}).status_code == 404
    secret = "private-signing-secret"
    assert (
        c.patch(
            f"/api/routines/{rid}/reactive", json={"signing_secret": secret}
        ).status_code
        == 200
    )
    assert secret not in c.get("/api/routines").text
    assert c.post(path, json={}).status_code == 401
    body = b'{"type":"message"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert (
        c.post(
            path,
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": signature},
        ).status_code
        == 202
    )
    assert (
        c.post(
            path,
            data=body + b" ",
            content_type="application/json",
            headers={"X-Webhook-Signature": signature},
        ).status_code
        == 401
    )
    e.reactive.configure(rid, {"mode": "disabled"})
    assert (
        c.post(path, data=body, headers={"X-Webhook-Signature": signature}).status_code
        == 409
    )


def test_normalizer_dry_run_activation_and_fanout(make_app):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    v = e.reactive.create_version(rid, SOURCE)
    assert (
        c.patch(
            f"/api/routines/{rid}/reactive",
            json={"mode": "active", "normalizer_id": v["id"]},
        ).status_code
        == 400
    )
    payload = [
        {"type": "presence"},
        {"type": "message", "sender": "ana", "id": "one"},
        {"type": "reply", "reply": {"sender": "joao"}, "id": "two"},
        {"type": "message", "sender": "ana", "id": "three"},
    ]
    receipt = c.post(path, json=payload).json["receipt_id"]
    test = e.reactive.test(rid, receipt, v["id"])
    assert test["status"] == "succeeded"
    assert [x["session_key"] for x in test["result"]["events"]] == [
        "ana",
        "joao",
        "ana",
    ]
    assert count(e, Session) == 1 and count(e, Job) == 0
    e.reactive.configure(rid, {"mode": "active", "normalizer_id": v["id"]})
    assert e.reactive.process_next() is False  # Old samples are never dispatched.
    assert e.reactive.receipt(rid, receipt)["status"] == "collected"
    e.reactive.replay(rid, receipt)
    assert e.reactive.process_next()
    assert count(e, Session) == 3 and count(e, Job) == 3
    result = e.reactive.receipt(rid, receipt)
    deliveries = result["result"]["deliveries"]
    assert deliveries[0]["session_id"] == deliveries[2]["session_id"]
    assert deliveries[1]["session_id"] != deliveries[0]["session_id"]
    assert deliveries[0]["execution_status"] == "queued"
    # One job per session can be in flight; third Ana message waits.
    j1, j2 = e.store.claim(e.owner), e.store.claim(e.owner)
    assert j1["session_id"] != j2["session_id"]
    assert e.store.claim(e.owner) is None
    assert any(
        x["type"] == "webhook_received" for x in e.store.events(j1["session_id"])
    )
    with pytest.raises(ValueError, match="Only collected"):
        e.reactive.replay(rid, receipt)


def test_dedup_transport_and_provider_messages(make_app):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    activate(c, e, rid, path)
    payload = {"type": "message", "sender": "ana", "id": "message-1"}
    a = c.post(path, json=payload, headers={"Idempotency-Key": "request-1"}).json
    b = c.post(path, json=payload, headers={"Idempotency-Key": "request-1"}).json
    assert a["receipt_id"] == b["receipt_id"] and b["duplicate"]
    e.reactive.process_next()
    third = c.post(path, json=payload).json["receipt_id"]
    e.reactive.process_next()
    assert count(e, Job) == 1
    assert e.reactive.receipt(rid, third)["result"]["deliveries"][0]["duplicate"]


def test_failed_and_ignored_are_distinct_and_versions_pinned(make_app):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    _, v = activate(c, e, rid, path)
    bad = c.post(path, json={"type": "message", "id": "bad"}).json["receipt_id"]
    ignored = c.post(path, json={"type": "presence"}).json["receipt_id"]
    new = e.reactive.create_version(
        rid, 'from fluxyr import output\noutput({"events": [], "ignored_reason":"new"})'
    )
    e.reactive.test(rid, ignored, new["id"])
    e.reactive.configure(rid, {"normalizer_id": new["id"]})
    e.reactive.process_next()
    e.reactive.process_next()
    assert e.reactive.receipt(rid, bad)["status"] == "failed"
    assert "sender" in e.reactive.receipt(rid, bad)["error"]
    assert e.reactive.receipt(rid, ignored)["status"] == "ignored"
    assert e.reactive.receipt(rid, ignored)["attempts"][0]["version_id"] == v["id"]
    assert count(e, Job) == 0
    assert count(e, Session) == 1


def test_context_continues_and_waiting_human_holds_later_events(make_app):
    app, e, _ = make_app(["Remember this", [("ask_human", {"question": "Continue?"})]])
    c, rid, path = setup(app, e)
    activate(c, e, rid, path)
    for msg in ("one", "two", "three"):
        c.post(path, json={"type": "message", "sender": "ana", "id": msg})
        e.reactive.process_next()
    first = execute_next(e)
    assert first["status"] == "succeeded"
    with e.db.transaction() as s:
        assert s.get(Session, first["session_id"]).brain
    second = e.store.claim(e.owner)
    assert second["brain"] is not None
    e.execute(second)
    with e.db.transaction() as s:
        assert s.get(Job, second["id"]).status == "waiting"
    assert e.store.claim(e.owner) is None


def test_collection_pagination_retention_restart_delete(make_app):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    ids = [c.post(path, json={"n": n}).json["receipt_id"] for n in range(4)]
    page = e.reactive.receipts(rid, limit=2)
    assert [r["id"] for r in page["items"]] == ids[::-1][:2]
    assert "envelope" not in page["items"][0]
    assert len(e.reactive.receipts(rid, before=page["next_before"])["items"]) == 2
    with e.db.transaction() as s:
        s.get(IncomingReceipt, ids[0]).created_at = time.time() - 8 * 86400
        s.get(IncomingReceipt, ids[1]).status = "processing"
        e.reactive._prune(s)
    assert count(e, IncomingReceipt) == 3
    e.reactive.start()
    e.reactive.stop()
    assert e.reactive.receipt(rid, ids[1])["status"] == "failed"
    e.reactive.delete_receipt(rid, ids[2])
    assert count(e, IncomingReceipt) == 2
    assert c.delete(f"/api/routines/{rid}").status_code == 200
    assert count(e, IncomingReceipt) == 0
    assert c.post(path, json={}).status_code == 404


def test_parallel_receive_is_idempotent(make_app):
    app, e, _ = make_app()
    _, rid, _ = setup(app, e)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: e.reactive.receive(rid, {"json": {"hello": "world"}}, "same"),
                range(8),
            )
        )
    assert len({r["receipt_id"] for r in responses}) == 1
    assert count(e, IncomingReceipt) == 1


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"events": []},
        {"events": [{"session_key": "", "payload": {}}]},
        {"events": [{"session_key": "a"}]},
        {"events": "wrong"},
        {"events": [{"session_key": "a", "payload": {}, "event_id": "x"}] * 2},
        {"events": [], "ignored_reason": 10},
    ],
)
def test_invalid_normalizer_contract(value):
    with pytest.raises(ValueError):
        validate_output(value)


def test_normalizer_crash_and_human_pause_cannot_activate(make_app):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    receipt = c.post(path, json={}).json["receipt_id"]
    for source in (
        'raise ValueError("bad format")',
        'from fluxyr import request_input\nrequest_input("Question",key="q")',
    ):
        v = e.reactive.create_version(rid, source)
        result = e.reactive.test(rid, receipt, v["id"])
        assert result["status"] == "failed"
        with pytest.raises(ValueError, match="Test this normalizer"):
            e.reactive.configure(rid, {"normalizer_id": v["id"], "mode": "active"})
    assert count(e, Job) == 0 and count(e, Session) == 1


def test_reset_idle_session_preserves_history_and_creates_new_context(make_app):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    activate(c, e, rid, path)
    c.post(path, json={"type": "message", "sender": "ana", "id": "first"})
    e.reactive.process_next()
    old = e.reactive.sessions(rid)["items"][0]
    with pytest.raises(ValueError, match="pending"):
        e.reactive.reset_session(rid, "ana")
    execute_next(e)
    e.reactive.reset_session(rid, "ana")
    assert not e.reactive.sessions(rid)["items"]
    c.post(path, json={"type": "message", "sender": "ana", "id": "second"})
    e.reactive.process_next()
    assert e.reactive.sessions(rid)["items"][0]["session_id"] != old["session_id"]
    assert count(e, Session) == 3 and count(e, Job) == 2


def test_dispatch_rollback_is_visible_and_can_retry(make_app, monkeypatch):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    activate(c, e, rid, path)
    receipt = c.post(
        path, json={"type": "message", "sender": "ana", "id": "first"}
    ).json["receipt_id"]
    enqueue = e.store.enqueue

    def fail(*args, **kwargs):
        raise RuntimeError("simulated database dispatch failure")

    monkeypatch.setattr(e.store, "enqueue", fail)
    with pytest.raises(RuntimeError):
        e.reactive.process_next()
    assert e.reactive.receipt(rid, receipt)["status"] == "failed"
    assert count(e, Session) == 1 and count(e, Job) == 0
    monkeypatch.setattr(e.store, "enqueue", enqueue)
    e.reactive.replay(rid, receipt)
    e.reactive.process_next()
    assert count(e, Job) == 1


def test_disabling_during_normalization_does_not_dispatch(make_app, monkeypatch):
    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    activate(c, e, rid, path)
    receipt = c.post(
        path, json={"type": "message", "sender": "ana", "id": "first"}
    ).json["receipt_id"]
    run = e.runner.run

    def disable(*args, **kwargs):
        e.reactive.configure(rid, {"mode": "disabled"})
        return run(*args, **kwargs)

    monkeypatch.setattr(e.runner, "run", disable)
    e.reactive.process_next()
    assert e.reactive.receipt(rid, receipt)["status"] == "collected"
    assert count(e, Job) == 0


def test_engine_processes_webhooks_asynchronously(make_app):
    app, e, _ = make_app(["Hello"])
    c, rid, path = setup(app, e)
    activate(c, e, rid, path)
    e.start()
    try:
        receipt = c.post(
            path, json={"type": "message", "sender": "ana", "id": "async"}
        ).json["receipt_id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            item = e.reactive.receipt(rid, receipt)
            if (
                item["status"] == "routed"
                and item["result"]["deliveries"][0]["execution_status"] == "succeeded"
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("Webhook did not produce a completed execution")
    finally:
        e.stop()


def test_schema_four_migrates_manual_and_scheduled_routines(make_app):
    from sqlalchemy import text

    _, e, _ = make_app()
    manual = e.routines.put({"name": "manual", "prompt": "Manual task"})
    scheduled = e.routines.put(
        {"name": "daily", "prompt": "Daily task", "cron": "0 8 * * *", "enabled": True}
    )
    with e.db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE routines DROP COLUMN trigger"))
        conn.execute(text("UPDATE schema_version SET id=4"))
    e.db.initialize()
    rows = {r["id"]: r for r in e.routines.list()}
    assert rows[manual["id"]]["trigger"] == "manual"
    assert rows[scheduled["id"]]["trigger"] == "scheduled"
    assert rows[scheduled["id"]]["enabled"] is True
    assert rows[scheduled["id"]]["next_run"] == scheduled["next_run"]


def test_capacity_and_proof_survive_sample_retention(make_app, monkeypatch):
    import fluxyr.reactive as module

    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    _, version = activate(c, e, rid, path)
    monkeypatch.setattr(module, "MAX_RECEIPTS", 1)
    queued = c.post(path, json={})
    assert (
        queued.status_code == 202
    )  # Finished samples rotate; pending work never does.
    assert c.post(path, json={}).status_code == 429
    e.reactive.process_next()  # Invalid payload becomes a visible failure.
    e.reactive.configure(rid, {"mode": "collecting"})
    e.reactive.delete_receipt(rid, queued.json["receipt_id"])
    e.reactive.configure(rid, {"mode": "active", "normalizer_id": version["id"]})
    assert c.post(path, json={}).status_code == 202


def test_sample_rotation_bounds_storage_and_does_not_reuse_receipt_ids(
    make_app, monkeypatch
):
    import fluxyr.reactive as module

    app, e, _ = make_app()
    c, rid, path = setup(app, e)
    monkeypatch.setattr(module, "MAX_RECEIPTS", 3)
    ids = [c.post(path, json={"index": n}).json["receipt_id"] for n in range(12)]
    assert ids == sorted(set(ids)) and len(ids) == 12
    assert count(e, IncomingReceipt) == 3
    assert [r["id"] for r in e.reactive.receipts(rid)["items"]] == ids[-3:][::-1]
    for receipt_id in ids[-3:]:
        e.reactive.delete_receipt(rid, receipt_id)
    assert c.post(path, json={}).json["receipt_id"] > ids[-1]
