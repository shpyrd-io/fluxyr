"""Real Python subprocesses and persistence; scripted LLM only controls dispatch."""

import pytest

from conftest import execute_next
from sqlalchemy import select

from fluxyr.models import Effect, ToolVersion

pytestmark = pytest.mark.integration


def install(e, source):
    skill = e.skills.build("parallel_probe", "Parallel probe", "Independent calls")
    v = e.skills.create(
        skill["id"],
        "action_parallel_probe",
        "Independent calls",
        source,
        {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    )
    # These are executor tests, not an assertion that the builder validated code.
    with e.db.transaction() as s:
        s.get(ToolVersion, v["id"]).state = "tested"
    e.skills.activate(v["id"])
    return v


def test_two_real_python_processes_overlap_and_keep_pinned_version(make_app):
    _, e, adapter = make_app()
    v = install(
        e,
        """from fluxyr import params, output, data_dir
import time, os
started = time.time()
(data_dir / str(params['id'])).touch()
deadline = time.monotonic() + 5
while not (data_dir / str(1 - params['id'])).exists():
    if time.monotonic() > deadline: raise RuntimeError('Sibling never started concurrently')
    time.sleep(0.01)
output({'id': params['id'], 'started': started, 'ended': time.time(), 'pid': os.getpid()})
""",
    )
    adapter.replies = [
        [("action_parallel_probe", {"id": 0}), ("action_parallel_probe", {"id": 1})],
        "Done",
    ]
    e.store.enqueue("Two independent calls")
    job = e.store.claim(e.owner)
    # A concurrent publisher must not change this already claimed job's version.
    install(e, 'from fluxyr import output\noutput("wrong version")')
    e.execute(job)
    events = e.store.events(job["session_id"])
    assert next(x for x in events if x["type"] == "tool_batch_start")["payload"][
        "parallel"
    ]
    results = [x["payload"]["result"] for x in events if x["type"] == "tool_end"]
    assert len(results) == 2 and all(r["success"] for r in results), results
    assert {r["version_id"] for r in results} == {v["id"]}
    assert len({r["source_sha256"] for r in results}) == 1
    a, b = [r["output"] for r in results]
    assert a["pid"] != b["pid"]
    assert max(a["started"], b["started"]) <= min(a["ended"], b["ended"])
    with e.db.transaction() as s:
        effects = list(s.scalars(select(Effect).where(Effect.job_id == job["id"])))
        assert len(effects) == 2 and len({x.occurrence for x in effects}) == 2


def test_completed_parallel_sibling_is_not_replayed_after_human_resume(make_app):
    _, e, adapter = make_app()
    install(
        e,
        """from fluxyr import params, request_input, output, data_dir
if params['id']:
    answer = request_input('Name?', key='name')
    output(answer['user_input'])
else:
    p = data_dir / 'once'
    if p.exists(): raise RuntimeError('Completed sibling replayed')
    p.touch()
    output('once')
""",
    )
    adapter.replies = [
        [("action_parallel_probe", {"id": 0}), ("action_parallel_probe", {"id": 1})],
        "Done",
    ]
    e.store.enqueue("One result and one human wait")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    entries = job["brain"]["pending_tools"]
    assert [p["_status"] for p in entries] == ["completed", "parked"]
    e.store.decide(
        job["id"],
        entries[1]["call_id"],
        {"decision": "complete", "result": {"answer": "Patrick"}},
    )
    finished = execute_next(e)
    assert finished["status"] == "succeeded", finished["error"]
    with e.db.transaction() as s:
        results = [
            x.result["output"]
            for x in s.scalars(select(Effect).where(Effect.job_id == job["id"]))
        ]
    assert sorted(results) == ["Patrick", "once"]


def test_single_worker_records_sequential_dispatch(make_app):
    _, e, adapter = make_app(tool_workers=1)
    install(e, 'from fluxyr import params, output\noutput(params["id"])')
    adapter.replies = [
        [("action_parallel_probe", {"id": 0}), ("action_parallel_probe", {"id": 1})],
        "Done",
    ]
    e.store.enqueue("Two calls")
    job = execute_next(e)
    assert job["status"] == "succeeded"
    event = next(
        x for x in e.store.events(job["session_id"]) if x["type"] == "tool_batch_start"
    )
    assert event["payload"]["parallel"] is False


def test_parallel_human_cards_resume_with_separate_answers(make_app):
    _, e, adapter = make_app()
    install(
        e,
        """from fluxyr import params, request_input, output
answer = request_input('Name?', key='name')
output({'id': params['id'], 'answer': answer['user_input']})
""",
    )
    adapter.replies = [
        [("action_parallel_probe", {"id": 0}), ("action_parallel_probe", {"id": 1})],
        "Done",
    ]
    e.store.enqueue("Two human workflows")
    job = execute_next(e)
    assert job["status"] == "waiting"
    entries = job["brain"]["pending_tools"]
    assert len(entries) == 2 and all(x["_status"] == "parked" for x in entries)
    for i, entry in enumerate(entries):
        e.store.decide(
            job["id"],
            entry["call_id"],
            {"decision": "complete", "result": {"answer": f"answer-{i}"}},
        )
    done = execute_next(e)
    assert done["status"] == "succeeded", done["error"]
    with e.db.transaction() as s:
        results = [
            x.result["output"]
            for x in s.scalars(select(Effect).where(Effect.job_id == job["id"]))
        ]
    assert sorted(results, key=lambda x: x["id"]) == [
        {"id": 0, "answer": "answer-0"},
        {"id": 1, "answer": "answer-1"},
    ]
