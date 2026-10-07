import json

import pytest
from sqlalchemy import select

from conftest import execute_next
from fluxyr.engine import Engine
from fluxyr.models import Effect, ToolVersion

SOURCE = """
from fluxyr import request_choice, request_input, request_confirmation, output, data_dir
import uuid

def main():
    token = uuid.uuid4().hex
    choice = request_choice("Escolha um cartão", [
        {"label":"Azul", "value":token, "preview_type":"html", "content":"<h1>Azul: " + token + "</h1>"},
        {"label":"Verde", "value":"green", "preview_type":"text", "content":"Cartão verde"}
    ], key="card", context={"token":token})
    if choice["decision"] == "rejected":
        output({"declined":True}); return
    title = request_input("Qual é o título?", key="title")
    if title["decision"] == "rejected":
        output({"declined":True}); return
    confirmed = request_confirmation("Salvar cartão?", key="save")
    if confirmed["decision"] == "rejected":
        output({"declined":True}); return
    path = data_dir / "result.json"
    if path.exists():
        raise RuntimeError("Final side effect ran twice")
    result = {"choice":choice, "title":title["user_input"]}
    path.write_text(__import__("json").dumps(result))
    output(result)
main()
"""


def action(e, source=SOURCE, approval=False):
    skill = e.skills.build("human_flow", "Human flow", "One resumable action")
    version = e.skills.create(
        skill["id"],
        "action_human_flow",
        "Interactive flow",
        source,
        {"type": "object", "properties": {}},
        requires_approval=approval,
    )
    with e.db.transaction() as s:
        s.get(ToolVersion, version["id"]).state = "tested"
    e.skills.activate(version["id"])
    return version


def pending(job):
    return next(
        p
        for p in job["brain"]["pending_tools"]
        if p.get("_status") == "parked" and not p.get("_decision")
    )


def decide(e, job, decision="complete", result=None):
    p = pending(job)
    return e.store.decide(
        job["id"],
        p.get("_request_id") or p["call_id"],
        {"decision": decision, "result": result},
    )


def test_same_action_three_rounds_restart_original_choice_and_preflight(make_app):
    app, e, adapter = make_app()
    action(e, approval=True)
    adapter.replies = [[("action_human_flow", {})], "Done"]
    e.store.enqueue("Choose, provide title and save")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    assert pending(job)["_result"]["__pua__"]["payload"]["preflight"]
    assert not (e.settings.data / "result.json").exists()
    decide(e, job, "approve")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    first = pending(job)
    request = first["_result"]["__human__"]["request"]
    assert request["variant"] == "choices"
    assert request["choices"][0]["url"].startswith("/preview/")
    html = app.test_client().get(request["choices"][0]["url"])
    assert html.status_code == 200
    assert request["context"]["token"] in html.text
    with pytest.raises(ValueError, match="Select one"):
        decide(e, job, result={"selected_index": 99})
    with pytest.raises(ValueError, match="Select one"):
        decide(e, job, result={"selected_index": True})
    response = {"selected_index": 0}
    decide(e, job, result=response)
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    second = pending(job)
    assert second["call_id"] == first["call_id"]
    assert second["_request_id"] != first["_request_id"]
    assert second["_result"]["__human__"]["request"]["variant"] == "continue"
    stale = e.store.decide(
        job["id"], first["_request_id"], {"decision": "complete", "result": response}
    )
    assert stale["duplicate"] and stale["status"] == "waiting"
    with pytest.raises(ValueError, match="non-empty"):
        decide(e, job, result={"answer": "  "})
    assert len(adapter.calls) == 1
    assert not (e.settings.data / "result.json").exists()
    # The durable request and earlier responses survive a fresh Engine instance.
    e = Engine(e.settings, adapter_factory=lambda: adapter)
    try:
        decide(e, job, result={"answer": "Título com acentuação"})
        job = execute_next(e)
        assert job["status"] == "waiting", job["error"]
        assert (
            pending(job)["_result"]["__human__"]["request"]["variant"]
            == "confirm-reject"
        )
        with pytest.raises(ValueError, match="approve or reject"):
            decide(e, job, result={"answer": "yes"})
        decide(e, job, "approve")
        job = execute_next(e)
        assert job["status"] == "succeeded", job["error"]
        result = json.loads((e.settings.data / "result.json").read_text())
        assert result["choice"]["value"] == request["context"]["token"]
        assert result["choice"]["context"] == request["context"]
        assert result["title"] == "Título com acentuação"
        assert len(adapter.calls) == 2  # Only after all Python interaction rounds.
        with e.db.transaction() as s:
            effect = s.scalar(select(Effect).where(Effect.job_id == job["id"]))
            assert effect.status == "done" and effect.result["success"]
    finally:
        e.db.engine.dispose()


def test_rejection_resumes_declined_branch_without_effects(make_app):
    _, e, adapter = make_app()
    action(e)
    adapter.replies = [[("action_human_flow", {})], "Declined"]
    e.store.enqueue("Choose")
    job = execute_next(e)
    decide(e, job, "reject")
    done = execute_next(e)
    assert done["status"] == "succeeded", done["error"]
    assert not (e.settings.data / "result.json").exists()
    with e.db.transaction() as s:
        assert s.scalar(select(Effect)).result["output"] == {"declined": True}


def test_interactive_candidate_test_has_multiple_rounds(make_app):
    _, e, _ = make_app()
    version = action(
        e,
        'from fluxyr import request_input, output\na=request_input("First?",key="first")\nb=request_input("Second?",key="second")\noutput([a["user_input"],b["user_input"]])',
    )
    e.store.enqueue(
        "Test",
        session_id=None,
        inputs={"tool_test": {"version_id": version["id"], "params": {}}},
    )
    job = execute_next(e)
    decide(e, job, result={"answer": "one"})
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    decide(e, job, result={"answer": "two"})
    job = execute_next(e)
    assert job["status"] == "succeeded", job["error"]
    assert e.skills.version(version["id"])["test_result"]["output"] == ["one", "two"]


def test_parallel_test_invocations_keep_responses_separate(make_app):
    from concurrent.futures import ThreadPoolExecutor
    from fluxyr.models import Job
    from fluxyr.database import row_dict

    _, e, _ = make_app()
    version = action(
        e,
        'from fluxyr import request_input,output\na=request_input("First?",key="first")\nb=request_input("Second?",key="second")\noutput([a["user_input"],b["user_input"]])',
    )
    ids = [
        e.store.enqueue(
            "Test",
            session_id=None,
            inputs={"tool_test": {"version_id": version["id"], "params": {}}},
        )["id"]
        for _ in range(2)
    ]

    def execute_both():
        claimed = [e.store.claim(e.owner), e.store.claim(e.owner)]
        assert all(claimed)
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(e.execute, claimed))
        with e.db.transaction() as s:
            return [row_dict(s.get(Job, jid)) for jid in ids]

    jobs = execute_both()
    for i, j in enumerate(jobs):
        decide(e, j, result={"answer": f"first-{i}"})
    jobs = execute_both()
    assert all(j["status"] == "waiting" for j in jobs)
    for i, j in enumerate(jobs):
        decide(e, j, result={"answer": f"second-{i}"})
    jobs = execute_both()
    assert all(j["status"] == "succeeded" for j in jobs), jobs
    assert [j["outcome"]["test"]["output"] for j in jobs] == [
        ["first-0", "second-0"],
        ["first-1", "second-1"],
    ]


def test_workbench_test_action_suspends_model_until_candidate_finishes(make_app):
    _, e, adapter = make_app()
    version = action(
        e,
        'from fluxyr import request_input,output\na=request_input("First?",key="first")\nb=request_input("Second?",key="second")\noutput([a["user_input"],b["user_input"]])',
    )
    adapter.replies = [
        [("test_action", {"version_id": version["id"], "params_json": "{}"})],
        "Test completed",
    ]
    e.store.enqueue("Test this candidate")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    assert len(adapter.calls) == 1
    decide(e, job, result={"answer": "one"})
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    assert len(adapter.calls) == 1
    decide(e, job, result={"answer": "two"})
    job = execute_next(e)
    assert job["status"] == "succeeded", job["error"]
    assert e.skills.version(version["id"])["test_result"]["output"] == ["one", "two"]
    assert len(adapter.calls) == 2


def test_sixth_interaction_fails_instead_of_reusing_an_old_answer(make_app):
    _, e, _ = make_app()
    version = action(
        e,
        'from fluxyr import request_input,output\nfor i in range(6): request_input("Next?",key=f"round_{i}")\noutput("done")',
    )
    e.store.enqueue(
        "Test limit",
        session_id=None,
        inputs={"tool_test": {"version_id": version["id"], "params": {}}},
    )
    for i in range(5):
        job = execute_next(e)
        assert job["status"] == "waiting", job["error"]
        decide(e, job, result={"answer": str(i)})
    job = execute_next(e)
    assert job["status"] == "failed"
    assert "at most 5" in job["outcome"]["test"]["error"]
