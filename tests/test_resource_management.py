from conftest import execute_next

from fluxyr_agent.models import Job


def test_vault_edits_keep_omitted_secrets_and_identity(make_app):
    app, e, _ = make_app()
    c = app.test_client()
    created = c.post(
        "/api/vault",
        json={
            "name": "credential",
            "kind": "key_password",
            "content": {"key": "original-key", "password": "private-password"},
        },
    ).json
    result = c.patch(
        "/api/vault/" + created["id"],
        json={"name": "renamed", "content": {"key": "new-key"}},
    )
    assert result.status_code == 200
    assert result.json["id"] == created["id"]
    assert e.vault.resolve("renamed") == {
        "key": "new-key",
        "password": "private-password",
    }
    assert "private-password" not in c.get("/api/vault").text
    assert "private-password" not in result.text
    assert c.patch("/api/vault/" + created["id"], json={"name": ""}).status_code == 400
    assert e.vault.resolve("renamed")["password"] == "private-password"
    assert c.delete("/api/vault/" + created["id"]).status_code == 200
    assert c.get("/api/vault").json == []


def test_routine_delete_stops_schedule_and_preserves_execution(make_app):
    app, e, _ = make_app()
    c = app.test_client()
    routine = c.post(
        "/api/routines",
        json={
            "name": "Daily",
            "prompt": "Read weather",
            "cron": "0 8 * * *",
            "timezone": "UTC",
            "enabled": True,
        },
    ).json
    job = e.routines.run(routine["id"])
    assert c.delete("/api/routines/" + routine["id"]).status_code == 200
    assert c.get("/api/routines").json == []
    with e.db.transaction() as s:
        row = s.get(Job, job["id"])
        assert row and row.routine_id is None and row.input["routine_execution"]
    assert execute_next(e)["status"] == "succeeded"


def test_tool_batch_metadata_records_actual_dispatch_mode(make_app):
    _, e, _ = make_app([[("list_files", {"path": "."}), ("vault_list", {})], "Done"])
    e.store.enqueue("Inspect")
    job = execute_next(e)
    events = e.store.events(job["session_id"])
    start = next(ev["payload"] for ev in events if ev["type"] == "tool_batch_start")
    assert start["parallel"] is True
    assert len(start["calls"]) == 2
    calls = [ev["payload"] for ev in events if ev["type"] in ("tool_begin", "tool_end")]
    assert len(calls) == 4
    assert all(
        p["batch_id"] == start["batch_id"] and p["batch_size"] == 2 for p in calls
    )
    assert any(ev["type"] == "tool_batch_end" for ev in events)
