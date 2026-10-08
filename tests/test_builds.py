import json
import time

import pytest
from conftest import execute_next
from sqlalchemy import select, text

from fluxyr.models import Effect, Job, Tool, ToolVersion, Version
from fluxyr.tools.registry import Registry


def setup_skill(e):
    return e.skills.build(
        "Arithmetic",
        "Local arithmetic",
        "Double a number",
        spec="## Double\nInput value integer; output its double as integer. No credentials.",
    )


def builder_steps(skill):
    return [
        [
            (
                "submit_plan",
                {
                    "skill_id": skill["id"],
                    "actions": [
                        {"name": "action_double", "description": "Double an integer"}
                    ],
                },
            )
        ],
        [
            (
                "create_action",
                {
                    "skill_id": skill["id"],
                    "name": "action_double",
                    "description": "Double an integer",
                    "source": "from fluxyr import params, output\noutput(params['value'] * 2)",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                    },
                },
            )
        ],
        "Candidate generated; testing belongs to the workbench.",
    ]


def test_build_progress_tracks_durable_plan_actions_and_lifecycle(make_app):
    app, e, _ = make_app()
    client = app.test_client()
    skill = setup_skill(e)
    child = e.builds.enqueue(skill["id"])
    jid, sid = child["id"], child["session_id"]

    def progress():
        response = client.get(f"/api/jobs/{jid}/build")
        assert response.status_code == 200
        assert "source" not in response.json
        return response.json

    assert progress()["phase"] == "queued"
    with e.db.transaction() as s:
        s.get(Job, jid).status = "running"
    assert progress()["phase"] == "planning"
    e.store.emit(
        sid, jid, "tool_begin", {"tool_call_id": "research", "tool_name": "tech_doc"}
    )
    assert progress()["research_tools"] == ["tech_doc"]
    e.store.emit(sid, jid, "tool_end", {"tool_call_id": "research"})
    e.builds.plan(
        jid,
        skill["id"],
        [
            {"name": "double", "description": "Double an integer"},
            {"name": "triple", "description": "Triple an integer"},
        ],
    )
    assert progress()["phase"] == "generating"
    e.store.emit(
        sid,
        jid,
        "tool_begin",
        {
            "tool_call_id": "create",
            "tool_name": "create_action",
            "args": {"name": "double", "source": "PRIVATE_SOURCE"},
        },
    )
    p = progress()
    assert p["phase"] == "creating"
    assert p["actions"][0]["status"] == "creating"
    assert "PRIVATE_SOURCE" not in json.dumps(p)
    args = builder_steps(skill)[1][0][1]
    e.skills.create(**args, build_job_id=jid)
    # A retry creates a new version, not another completed action.
    e.skills.create(**args, build_job_id=jid)
    e.store.emit(sid, jid, "tool_end", {"tool_call_id": "create"})
    p = progress()
    assert (p["created"], p["total"], p["phase"]) == (1, 2, "generating")
    assert [a["status"] for a in p["actions"]] == ["created", "planned"]
    # No in-memory subscriptions required: a new client reads the same progress.
    assert app.test_client().get(f"/api/jobs/{jid}/build").json == p
    for status in ["paused", "waiting", "cancelled", "failed", "interrupted"]:
        with e.db.transaction() as s:
            row = s.get(Job, jid)
            row.status, row.error = (
                status,
                "provider failed" if status == "failed" else None,
            )
        p = progress()
        assert p["phase"] == status
        assert p["created"] == 1
        if status == "failed":
            assert p["error"] == "provider failed"


def test_build_progress_from_worker_lifecycle_and_completion(make_app):
    app, e, adapter = make_app()
    skill = setup_skill(e)
    child = e.builds.enqueue(skill["id"])
    adapter.replies = builder_steps(skill)
    result = execute_next(e)
    assert result["status"] == "succeeded"
    p = app.test_client().get(f"/api/jobs/{child['id']}/build").json
    assert (p["status"], p["phase"], p["created"], p["total"]) == (
        "succeeded",
        "succeeded",
        1,
        1,
    )
    assert p["actions"][0]["name"] == "action_double"
    ordinary = e.store.enqueue("Normal chat")
    assert app.test_client().get(f"/api/jobs/{ordinary['id']}/build").status_code == 400


def test_single_worker_build_releases_parent_and_resumes_with_artifacts(make_app):
    _, e, adapter = make_app(workers=1)
    skill = setup_skill(e)

    def inspect(messages, tools):
        result = json.loads(
            next(m["content"] for m in reversed(messages) if m["role"] == "tool")
        )
        assert result["build"]["tools"][0]["function_name"] == "action_double"
        assert "create_action" not in {t["name"] for t in tools}
        return [
            (
                "update_action_description",
                {
                    "action_id": result["build"]["tools"][0]["action_id"],
                    "description": "Doubles integer values; returns one integer",
                },
            )
        ]

    adapter.replies = [
        [("build_skill", {"skill_id": skill["id"]})],
        *builder_steps(skill),
        inspect,
        "Build returned; description refined.",
    ]
    parent = e.store.enqueue("Build this skill. Private workbench context marker.")
    e.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with e.db.transaction() as s:
            state = s.get(Job, parent["id"]).status
        if state in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    e.stop()
    assert state == "succeeded"
    with e.db.transaction() as s:
        children = list(s.scalars(select(Job).where(Job.id != parent["id"])))
        assert len(children) == 1 and children[0].status == "succeeded"
        assert "Private workbench context marker" not in json.dumps(children[0].brain)
        assert children[0].session_id != parent["session_id"]
        tool = s.scalar(select(Tool))
        assert tool.active_version is None
        assert tool.description == "Doubles integer values; returns one integer"
        assert (
            s.scalar(
                select(Effect).where(
                    Effect.job_id == parent["id"], Effect.tool_name == "build_skill"
                )
            ).status
            == "done"
        )


def test_build_wait_and_human_interaction_survive_engine_restart(make_app):
    _, e, adapter = make_app()
    skill = setup_skill(e)
    adapter.replies = [[("build_skill", {"skill_id": skill["id"]})]]
    e.store.enqueue("Build")
    parent = execute_next(e)
    assert parent["status"] == "building"
    _, resumed, model = make_app(
        [
            [("ask_human", {"question": "Confirm the input contract?"})],
            *builder_steps(skill),
            "Builder artifacts received.",
        ]
    )
    child = execute_next(resumed)
    assert child["status"] == "waiting"
    resumed.builds.resolve_dependencies()
    assert resumed.store.claim(resumed.owner) is None
    resumed.store.decide(
        child["id"],
        child["brain"]["pending_tools"][0]["call_id"],
        {"decision": "complete", "result": {"answer": "integer value"}},
    )
    assert execute_next(resumed)["status"] == "succeeded"
    resumed.builds.resolve_dependencies()
    done = execute_next(resumed)
    assert done["id"] == parent["id"] and done["status"] == "succeeded"


def test_failed_builder_returns_error_instead_of_claiming_success(make_app):
    _, e, adapter = make_app()
    skill = setup_skill(e)
    adapter.replies = [
        [("build_skill", {"skill_id": skill["id"]})],
        "I did not create code.",
        "Build failed; no action available.",
    ]
    e.store.enqueue("Build")
    parent = execute_next(e)
    child = execute_next(e)
    assert child["status"] == "failed"
    e.builds.resolve_dependencies()
    done = execute_next(e)
    results = [
        m
        for m in done["brain"]["short_term"]["items"]
        if m.get("role") == "tool" and m.get("name") == "build_skill"
    ]
    assert json.loads(results[-1]["content"])["error"]
    assert done["id"] == parent["id"]


def test_cancel_parent_cancels_queued_builder_and_releases_session(make_app):
    _, e, adapter = make_app()
    skill = setup_skill(e)
    adapter.replies = [[("build_skill", {"skill_id": skill["id"]})]]
    e.store.enqueue("Build")
    parent = execute_next(e)
    e.store.control(parent["id"], "cancel")
    with e.db.transaction() as s:
        assert all(j.status == "cancelled" for j in s.scalars(select(Job)))
    e.store.enqueue("New work")
    assert e.store.claim(e.owner) is not None


def test_rebuild_is_scoped_and_preserves_existing_versions(make_app):
    _, e, _ = make_app()
    skill = setup_skill(e)
    first = e.skills.create(
        skill["id"],
        "action_double",
        "Double",
        "from fluxyr import output\noutput(2)",
        {"type": "object"},
    )
    e.skills.test(first["id"], {})
    e.skills.activate(first["id"])
    e.builds.enqueue(skill["id"], action_id=first["tool_id"])
    with pytest.raises(ValueError, match="already exists"):
        e.builds.enqueue(skill["id"])
    job = e.store.claim(e.owner)
    with pytest.raises(ValueError, match="original action name"):
        e.builds.plan(
            job["id"], skill["id"], [{"name": "Different", "description": "Different"}]
        )
    e.builds.plan(
        job["id"], skill["id"], [{"name": "action_double", "description": "Double"}]
    )
    registry = Registry(e, job, lambda *_: None, lambda: False)
    create = next(
        t["function"] for t in registry.definitions() if t["name"] == "create_action"
    )
    result, _ = create(
        skill_id=skill["id"],
        name="action_double",
        description="New double",
        source="from fluxyr import output\noutput(4)",
        parameters={"type": "object"},
    )
    assert result["id"] != first["id"]
    with e.db.transaction() as s:
        assert s.get(Tool, first["tool_id"]).active_version == first["id"]
        assert s.get(ToolVersion, first["id"]).source.endswith("output(2)")


def test_schema_one_migration_preserves_skills(make_app):
    _, e, _ = make_app()
    skill = setup_skill(e)
    with e.db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE skills DROP COLUMN spec"))
        conn.execute(text("UPDATE schema_version SET id=1"))
    e.db.initialize()
    restored = e.skills.get(skill["id"])
    assert restored["name"] == skill["name"] and restored["spec"] == ""
    with e.db.transaction() as s:
        assert s.scalar(select(Version.id)) == 5
