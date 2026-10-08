"""Routine capacity is enforced at claim time, including resumed human waits."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import execute_next
from sqlalchemy import select, text

from fluxyr.models import Job, Routine, Session, Version


def routine(engine, name="task", **values):
    return engine.routines.put({"name": name, "prompt": "Do the task", **values})


def test_queue_is_serial_and_does_not_block_other_routines_or_chat(make_app):
    _, e, _ = make_app()
    r = routine(e)
    jobs = [e.routines.run(r["id"]) for _ in range(3)]
    first = e.store.claim(e.owner)
    assert first["id"] == jobs[0]["id"]
    other = routine(e, "other")
    independent = e.routines.run(other["id"])
    chat = e.store.enqueue("Hello")
    assert e.store.claim(e.owner)["id"] == independent["id"]
    assert e.store.claim(e.owner)["id"] == chat["id"]
    assert e.store.claim(e.owner) is None
    e.execute(first)
    assert e.store.claim(e.owner)["id"] == jobs[1]["id"]


def test_parallel_capacity_is_atomic_and_lowering_limit_drains(make_app):
    _, e, _ = make_app()
    r = routine(e, overlap="parallel", max_concurrency=3)
    jobs = [e.routines.run(r["id"]) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [
            j for j in pool.map(lambda _: e.store.claim(e.owner), range(16)) if j
        ]
    # PostgreSQL SKIP LOCKED may defer a competing claim to the next poll.
    while (job := e.store.claim(e.owner)) is not None:
        claimed.append(job)
    assert len(claimed) == 3
    assert len({j["session_id"] for j in claimed}) == 3
    assert e.store.claim(e.owner) is None
    e.routines.put({"max_concurrency": 1}, r["id"])
    for job in claimed[:2]:
        e.execute(job)
        assert e.store.claim(e.owner) is None
    e.execute(claimed[2])
    assert e.store.claim(e.owner)["id"] in {j["id"] for j in jobs}
    assert e.store.claim(e.owner) is None


def test_human_wait_releases_slot_but_preserves_same_session_order(make_app):
    app, e, _ = make_app(
        [[("ask_human", {"question": "Continue?"})], "Done", "Resumed", "Later"]
    )
    r = routine(e, trigger="reactive", overlap="parallel", max_concurrency=1)
    with e.db.transaction() as s:
        a, b = (
            Session(title="Customer A", kind="reactive"),
            Session(title="Customer B", kind="reactive"),
        )
        s.add_all([a, b])
        s.flush()
        a_id, b_id = a.id, b.id
    first = e.store.enqueue("First A", a_id, r["id"])
    later = e.store.enqueue("Second A", a_id, r["id"])
    independent = e.store.enqueue("First B", b_id, r["id"])
    waiting = execute_next(e)
    assert waiting["id"] == first["id"] and waiting["status"] == "waiting"
    running = e.store.claim(e.owner)
    assert running["id"] == independent["id"]
    call_id = waiting["brain"]["pending_tools"][0]["call_id"]
    response = app.test_client().post(
        f"/api/jobs/{first['id']}/decisions/{call_id}",
        json={"decision": "complete", "result": {"answer": "Yes"}},
    )
    assert response.status_code == 200
    assert e.store.claim(e.owner) is None  # Resumption must acquire capacity again.
    e.execute(running)
    resumed = execute_next(e)
    assert resumed["id"] == first["id"] and resumed["status"] == "succeeded"
    assert execute_next(e)["id"] == later["id"]


def test_scheduler_parallel_queues_excess_and_skip_keeps_its_semantics(make_app):
    _, e, _ = make_app()
    r = routine(
        e, cron="* * * * *", enabled=True, overlap="parallel", max_concurrency=2
    )
    for _ in range(4):
        with e.db.transaction() as s:
            s.get(Routine, r["id"]).next_run = time.time() - 1
        e.routines.tick()
        e.routines.tick()  # Same occurrence is not duplicated.
    with e.db.transaction() as s:
        assert len(list(s.scalars(select(Job)))) == 4
    assert e.store.claim(e.owner)
    assert e.store.claim(e.owner)
    assert e.store.claim(e.owner) is None
    e.routines.put({"overlap": "skip"}, r["id"])
    with e.db.transaction() as s:
        s.get(Routine, r["id"]).next_run = time.time() - 1
    e.routines.tick()
    with e.db.transaction() as s:
        assert len(list(s.scalars(select(Job)))) == 4


@pytest.mark.parametrize("value", [0, -1, 33, True, 1.5, "3", None])
def test_invalid_limits_rejected_without_mutating_routine(make_app, value):
    app, e, _ = make_app()
    r = routine(e, overlap="parallel", max_concurrency=2)
    response = app.test_client().patch(
        f"/api/routines/{r['id']}", json={"max_concurrency": value}
    )
    assert response.status_code == 400
    assert e.routines.list()[0]["max_concurrency"] == 2


def test_schema_five_migrates_existing_routines_idempotently(make_app):
    _, e, _ = make_app()
    r = routine(e, cron="0 8 * * *", enabled=True)
    with e.db.engine.begin() as c:
        c.execute(text("ALTER TABLE routines DROP COLUMN max_concurrency"))
        c.execute(text("UPDATE schema_version SET id=5"))
    e.db.initialize()
    e.db.initialize()
    saved = e.routines.list()[0]
    assert saved["id"] == r["id"] and saved["max_concurrency"] == 1
    assert saved["next_run"] == r["next_run"] and saved["overlap"] == "queue"
    with e.db.transaction() as s:
        assert s.scalar(select(Version.id)) == 7


def test_global_workers_bound_parallel_routine(make_app):
    _, e, adapter = make_app(workers=2)
    entered, release, lock = threading.Event(), threading.Event(), threading.Lock()
    counts = {"active": 0, "peak": 0, "started": 0}

    def block(messages, tools):
        with lock:
            counts["active"] += 1
            counts["started"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
            if counts["active"] == 2:
                entered.set()
        try:
            assert release.wait(8)
            return "Done"
        finally:
            with lock:
                counts["active"] -= 1

    adapter.replies = [block] * 5
    r = routine(e, overlap="parallel", max_concurrency=5)
    for _ in range(5):
        e.routines.run(r["id"])
    e.start()
    try:
        assert entered.wait(8)
        with lock:
            assert counts["started"] == 2
        release.set()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with e.db.transaction() as s:
                if all(j.status == "succeeded" for j in s.scalars(select(Job))):
                    break
            time.sleep(0.05)
        else:
            pytest.fail("Queued routine executions did not drain")
        assert counts["peak"] == 2 and counts["started"] == 5
    finally:
        release.set()
        e.stop()


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_BROWSER") != "1",
    reason="requires local Chrome",
)
def test_parallel_form_saves_only_on_submit(make_app, monkeypatch):
    from werkzeug.serving import make_server

    monkeypatch.setenv("FLUXYR_BROWSER_ENABLED", "true")
    app, e, _ = make_app()
    r = routine(e)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    browser = e.browsers

    def call(operation, **arguments):
        result = browser.call("routine-ui", operation, arguments)
        assert not result.get("isError"), result
        return result

    try:
        call("navigate", url=origin)
        call("wait_for", condition="element", selector='button[aria-label="Routines"]')
        call("click", selector='button[aria-label="Routines"]')
        call("wait_for", condition="element", selector='button[aria-label="Edit task"]')
        call("click", selector='button[aria-label="Edit task"]')
        selector = '[aria-label="When another execution is running"]'
        call("wait_for", condition="element", selector=selector)
        call("click", selector=selector)
        call("wait_for", condition="element", selector='[role="option"]:last-child')
        call("click", selector='[role="option"]:last-child')
        number = 'input[aria-label="Maximum simultaneous executions"]'
        call("wait_for", condition="element", selector=number)
        target = browser.prepare("routine-ui", origin, selector=number)
        assert browser.fill(target, value="3")["filled"]
        saved = e.routines.list()[0]
        assert saved["overlap"] == "queue" and saved["max_concurrency"] == 1
        call("click", selector='[role="dialog"] button[type="submit"]')
        call(
            "wait_for",
            condition="js",
            expression="!document.querySelector('[role=dialog]')",
        )
        saved = e.routines.list()[0]
        assert saved["id"] == r["id"] and saved["overlap"] == "parallel"
        assert saved["max_concurrency"] == 3
        call("click", selector='button[aria-label="Edit task"]')
        call("wait_for", condition="element", selector=number)
        call(
            "wait_for",
            condition="js",
            expression='document.querySelector(\'input[aria-label="Maximum simultaneous executions"]\').value === "3"',
        )
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
