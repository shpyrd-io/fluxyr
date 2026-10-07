"""Keep polled metadata endpoints from materializing execution contexts."""

from sqlalchemy import event

from fluxyr.models import Job
from fluxyr.database import MAIN_SESSION
from fluxyr.usage import usage_report


def test_public_job_lists_preserve_pending_without_loading_context(make_app):
    app, engine, _ = make_app()
    job = engine.store.enqueue("Await human")
    pending = [{"tool_name": "ask_human", "call_id": "human-1", "question": "Proceed?"}]
    with engine.db.transaction() as s:
        row = s.get(Job, job["id"])
        row.status = "waiting"
        row.brain = {"pending_tools": pending, "large": "x" * 100000}
        row.snapshot = {"large": "y" * 100000}
    sql = []

    def capture(conn, cursor, statement, *_):
        sql.append(statement)

    event.listen(engine.db.engine, "before_cursor_execute", capture)
    try:
        for path in ["/api/jobs", f"/api/sessions/{MAIN_SESSION}"]:
            response = app.test_client().get(path)
            assert response.status_code == 200
            rows = response.json if path == "/api/jobs" else response.json["jobs"]
            result = next(r for r in rows if r["id"] == job["id"])
            assert result["pending"] == pending
            assert "brain" not in result and "snapshot" not in result
        job_queries = [q for q in sql if "FROM jobs" in q]
        assert len(job_queries) == 2  # no deferred context loads per row
        assert all("jobs.snapshot" not in q for q in job_queries)
        # SQLite projects the pending subtree with JSON_EXTRACT(jobs.brain, ...).
        # A bare selected brain column would materialize the entire context.
        import re

        assert all(
            not re.search(r"(?:SELECT|,)\s*jobs\.brain\s*(?:,|AS)", q)
            for q in job_queries
        )
    finally:
        event.remove(engine.db.engine, "before_cursor_execute", capture)


def test_usage_parent_projection_handles_builder_and_ignores_context(make_app):
    _, engine, _ = make_app()
    parent = engine.store.enqueue("Parent")
    child = engine.store.enqueue(
        "Builder", session_id=None, inputs={"builder": {"parent_job_id": parent["id"]}}
    )
    with engine.db.transaction() as s:
        s.get(Job, parent["id"]).brain = {"large": "x" * 100000}
    engine.store.emit(
        child["session_id"],
        child["id"],
        "usage",
        {
            "model_call_id": "one",
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_usd": 0.01,
            "available": True,
        },
    )
    sql = []

    def capture(conn, cursor, statement, *_):
        sql.append(statement)

    event.listen(engine.db.engine, "before_cursor_execute", capture)
    try:
        report = usage_report(engine.db)
        assert report["by_job"][parent["id"]] == report["by_job"][child["id"]]
        assert report["total"]["tokens"] == 12
        assert report["since"] is not None
        assert not any("jobs.brain" in q or "jobs.snapshot" in q for q in sql)
    finally:
        event.remove(engine.db.engine, "before_cursor_execute", capture)
