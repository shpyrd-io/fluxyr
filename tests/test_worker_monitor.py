import threading
import time

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy import text
import pytest

from fluxyr.models import Job
from fluxyr.runtime.interruptible import InterruptibleAdapter


def until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("Condition did not become true")


def test_monitor_restarts_after_failure_without_replaying_job(make_app, monkeypatch):
    app, e, _ = make_app()
    job = e.store.enqueue("Previously claimed")
    e.store.claim(e.owner)
    owner = e.owner
    original = e.store.heartbeat
    attempts = []

    def heartbeat(token):
        attempts.append(token)
        if len(attempts) == 1:
            raise RuntimeError("supervisor failed")
        return original(token)

    monkeypatch.setattr(e.store, "heartbeat", heartbeat)
    e.monitor.retry_delays = (0.01, 0.01, 0.01)
    e.start()
    until(lambda: e.monitor.health()["worker"] and e.owner != owner)
    with e.db.transaction() as s:
        assert s.get(Job, job["id"]).status == "interrupted"
    assert app.test_client().get("/api/health/ready").status_code == 200
    assert e.monitor.failures == 0


def test_three_failed_recoveries_fail_liveness(make_app, monkeypatch):
    app, e, _ = make_app()
    owners = []

    def fail(owner):
        owners.append(owner)
        raise RuntimeError("persistent defect")

    monkeypatch.setattr(e.store, "heartbeat", fail)
    e.monitor.retry_delays = (0.01, 0.01, 0.01)
    e.start()
    until(lambda: e.monitor.state == "failed")
    assert len(set(owners)) == 4  # original + three recovery generations
    assert app.test_client().get("/api/health/live").status_code == 503
    assert app.test_client().get("/api/health").status_code == 503


def test_uncaught_task_failure_does_not_renew_orphan_forever(make_app, monkeypatch):
    _, e, _ = make_app()
    job = e.store.enqueue("Uncaught task failure")
    owner = e.owner
    calls = []

    def crash(job):
        calls.append(job["id"])
        raise RuntimeError("Failure outside normal job settlement")

    monkeypatch.setattr(e, "execute", crash)
    e.monitor.retry_delays = (0.01, 0.01, 0.01)
    e.start()
    until(lambda: e.owner != owner and e.monitor.health()["worker"])
    assert calls == [job["id"]]
    with e.db.transaction() as s:
        assert s.get(Job, job["id"]).status == "interrupted"


def test_database_outage_keeps_liveness_and_reconnects(make_app, monkeypatch):
    app, e, _ = make_app()
    original = e._acquire_fence
    database_up = threading.Event()

    def acquire():
        if not database_up.is_set():
            raise OperationalError("connect", {}, Exception("unavailable"))
        original()

    e.start()
    until(lambda: e.monitor.health()["worker"])
    monkeypatch.setattr(e, "_acquire_fence", acquire)
    e.monitor.failed(OperationalError("connect", {}, Exception("unavailable")))
    until(lambda: e.monitor.draining is not None and not e.monitor.draining.is_alive())
    # Drive retries without waiting 30 seconds; database outages do not exhaust
    # the budget reserved for broken worker generations.
    for _ in range(4):
        e.monitor.next_attempt = 0
        e.monitor.tick()
    assert app.test_client().get("/api/health/live").status_code == 200
    assert app.test_client().get("/api/health/ready").status_code == 503
    assert e.monitor.failures == 0
    database_up.set()
    e.monitor.next_attempt = 0
    until(lambda: e.monitor.health()["worker"])


def test_old_provider_must_finish_before_next_generation(make_app):
    _, e, _ = make_app()
    release = threading.Event()
    entered = threading.Event()
    e.start()
    until(lambda: e.monitor.health()["worker"])
    owner = e.owner

    def detached():
        entered.set()
        release.wait(5)

    e.provider_tasks.start(detached, "test-old-provider")
    assert entered.wait(1)
    e.monitor.retry_delays = (0.01, 0.01, 0.01)
    e.monitor.drain_timeout = 0.1
    try:
        e.monitor.failed(RuntimeError("worker failed"))
        until(lambda: e.monitor.state == "failed")
        assert e.owner == owner
        assert not e.monitor.health()["live"]
    finally:
        release.set()


def test_stream_control_is_bounded_and_does_not_read_context(make_app, monkeypatch):
    _, e, _ = make_app()
    job = e.store.enqueue("stream")
    e.store.claim(e.owner)
    statements = []
    event.listen(
        e.db.engine,
        "before_cursor_execute",
        lambda c, cur, sql, *a: statements.append(sql),
    )
    # Fixed time makes the query bound independent of CI machine speed.
    monkeypatch.setattr("fluxyr.engine.time.monotonic", lambda: 100.0)
    for _ in range(200):
        assert e._control(job["id"]) is None
    reads = [sql for sql in statements if "FROM jobs" in sql]
    assert len(reads) == 1
    assert "brain" not in reads[0] and "snapshot" not in reads[0]
    e.store.control(job["id"], "cancel")
    monkeypatch.setattr("fluxyr.engine.time.monotonic", lambda: 100.26)
    assert e._control(job["id"]) == "cancel"
    e.stopping.set()
    assert e._control(job["id"]) == "pause"  # shutdown bypasses cached values


def test_provider_group_tracks_request_after_caller_interrupts():
    from fluxyr.runtime.interruptible import ModelInterrupted, ThreadGroup
    import pytest

    release, cancel, entered = threading.Event(), threading.Event(), threading.Event()
    tasks = ThreadGroup()

    class Adapter:
        def execute_step(self):
            entered.set()
            release.wait(3)

    adapter = InterruptibleAdapter(
        Adapter(), cancel.is_set, threading.BoundedSemaphore(1), tasks=tasks
    )

    def call():
        with pytest.raises(ModelInterrupted):
            adapter.execute_step()

    thread = threading.Thread(target=call)
    thread.start()
    assert entered.wait(1)
    cancel.set()
    thread.join(1)
    try:
        assert not thread.is_alive()
        assert tasks.active >= 1
    finally:
        release.set()
        tasks.join()


def test_lost_postgres_fence_reconnects_with_new_owner(make_app):
    _, e, _ = make_app()
    if e.db.sqlite:
        pytest.skip("Requires disposable PostgreSQL")
    e.heartbeat_interval = 0.05
    e.monitor.retry_delays = (0.01, 0.01, 0.01)
    e.start()
    until(lambda: e.monitor.health()["worker"])
    owner = e.owner
    pid = e.leader.connection.driver_connection.get_backend_pid()
    with e.db.engine.begin() as conn:
        conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    until(lambda: e.owner != owner and e.monitor.health()["worker"])
    assert e.leader.connection.driver_connection.get_backend_pid() != pid
