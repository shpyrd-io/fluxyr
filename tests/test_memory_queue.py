import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text
from conftest import execute_next
from fluxyr.database import MAIN_SESSION
from fluxyr.models import Job, MemoryTask, Session
from fluxyr.core.memory.semantic import SemanticMemory
from fluxyr.core.memory.extraction_context import turn_messages


def candidate(task, key="wallet", value="old"):
    state = copy.deepcopy(task["base"])
    semantic = SemanticMemory.from_dict(state["semantic"])
    semantic.add(key, value)
    state["semantic"] = semantic.to_dict()
    return state


def ready(engine, task, result):
    with engine.db.transaction() as s:
        row = s.get(MemoryTask, task["id"])
        row.status, row.result = "ready", result


def test_foreground_finishes_without_memory_call_and_builder_skips_queue(make_app):
    _, e, adapter = make_app(["Reply"])
    entered, release = threading.Event(), threading.Event()
    original = adapter.execute_step

    def execute(*args, **kwargs):
        if any(
            t.get("name") == "store_semantic_memory" for t in kwargs.get("tools", [])
        ):
            entered.set()
            release.wait(3)
        return original(*args, **kwargs)

    adapter.execute_step = execute
    e.store.enqueue("remember context")
    result = execute_next(e)
    assert result["status"] == "succeeded" and not entered.is_set()
    task = e.memory_queue.claim()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(e.memory_queue.extract, task)
        assert entered.wait(1)
        # Even with only one foreground worker, an extraction cannot own its slot.
        e.store.enqueue("next turn")
        assert execute_next(e)["status"] == "succeeded"
        release.set()
        future.result(timeout=2)
    e.memory_queue.apply_ready()
    skill = e.skills.build(
        "isolated", "isolated", "Build it", "## Action\nReturn a value"
    )
    child = e.builds.enqueue(skill["id"])
    execute_next(e)  # no candidate -> failed build, but no automatic extraction
    with e.db.transaction() as s:
        assert not s.scalar(
            select(MemoryTask.id).where(MemoryTask.job_id == child["id"])
        )


def test_explicit_save_is_durable_before_final_response_and_fences_old_analysis(
    make_app,
):
    _, e, adapter = make_app(["First"])
    e.store.enqueue("earlier context")
    execute_next(e)
    task = e.memory_queue.claim()

    def inspect(*_):
        with e.db.transaction() as s:
            assert (
                s.get(Session, MAIN_SESSION).memories["semantic"]["items"]["wallet"][
                    "content"
                ]
                == "127.43"
            )
        return "Saved"

    adapter.replies = [
        [
            (
                "save_in_memory",
                {"memory_type": "semantic", "key": "wallet", "content": "127.43"},
            )
        ],
        inspect,
    ]
    e.store.enqueue("save wallet")
    execute_next(e)
    ready(e, task, candidate(task))
    e.memory_queue.apply_ready()
    with e.db.transaction() as s:
        assert s.get(MemoryTask, task["id"]).status == "discarded"
        assert (
            s.get(Session, MAIN_SESSION).memories["semantic"]["items"]["wallet"][
                "content"
            ]
            == "127.43"
        )


def test_delete_and_context_reset_do_not_resurrect_old_memories(make_app):
    _, e, adapter = make_app(["Start"])
    e.store.enqueue("context")
    execute_next(e)
    task = e.memory_queue.claim()
    adapter.replies = [
        [("delete_memory", {"memory_type": "semantic", "key": "wallet"})],
        "Forgotten",
    ]
    e.store.enqueue("forget wallet even if not saved yet")
    execute_next(e)
    ready(e, task, candidate(task))
    e.memory_queue.apply_ready()
    with e.db.transaction() as s:
        assert (
            "wallet" not in s.get(Session, MAIN_SESSION).memories["semantic"]["items"]
        )
        assert s.get(MemoryTask, task["id"]).status == "discarded"
    e.store.enqueue("another turn")
    execute_next(e)
    task = e.memory_queue.claim()
    e.store.clear_context(MAIN_SESSION)
    ready(e, task, candidate(task))
    e.memory_queue.apply_ready()
    with e.db.transaction() as s:
        assert s.get(Session, MAIN_SESSION).memories is None
        assert s.get(MemoryTask, task["id"]).status == "discarded"


def test_fifo_waits_for_foreground_and_ready_results_survive_restart(make_app):
    _, e, adapter = make_app(["First"])
    e.store.enqueue("first")
    execute_next(e)
    first = e.memory_queue.claim()
    e.store.enqueue("second")
    job = e.store.claim(e.owner)
    ready(e, first, candidate(first))
    e.memory_queue.apply_ready()
    with e.db.transaction() as s:
        assert s.get(MemoryTask, first["id"]).status == "ready"
    e.execute(job)
    assert e.memory_queue.claim() is None  # prior ready task must apply first
    from fluxyr.engine import Engine

    restarted = Engine(e.settings, lambda: adapter)
    restarted.memory_queue.apply_ready()
    second = restarted.memory_queue.claim()
    assert second["base"]["semantic"]["items"]["wallet"]["content"] == "old"
    assert second["job_id"] == job["id"]
    restarted.db.engine.dispose()


def test_extraction_input_preserves_user_and_final_answer_with_many_tools():
    state = {
        "short_term": {
            "items": [{"role": "user", "content": "Remember 127.43"}]
            + [{"role": "tool", "content": "x" * 50000} for _ in range(50)]
            + [{"role": "assistant", "content": "Saved 127.43"}]
        }
    }
    result = turn_messages("Remember 127.43", state)
    assert result[0]["content"] == "Remember 127.43"
    assert result[-1]["content"] == "Saved 127.43"
    assert sum(len(m["content"]) for m in result) <= 16000


def test_schema_three_migration_preserves_existing_brain(make_app):
    _, e, _ = make_app(["Saved"])
    e.store.enqueue("before upgrade")
    execute_next(e)
    with e.db.engine.begin() as c:
        c.execute(text("ALTER TABLE sessions DROP COLUMN memories"))
        c.execute(text("ALTER TABLE sessions DROP COLUMN memory_epoch"))
        c.execute(text("UPDATE schema_version SET id=3"))
    e.db.initialize()
    with e.db.transaction() as s:
        session = s.get(Session, MAIN_SESSION)
        assert session.brain and session.memory_epoch == 0 and session.memories is None


def test_extraction_retries_empty_decision_then_accepts_explicit_no_change(make_app):
    _, e, adapter = make_app(["Nothing new"])
    e.store.enqueue("hello")
    execute_next(e)
    task = e.memory_queue.claim()
    e.memory_queue.extract(task)  # scripted adapter's empty response is not success
    with e.db.transaction() as s:
        row = s.get(MemoryTask, task["id"])
        assert row.status == "queued" and "no structured decision" in row.error
        assert row.available_at > time.time()
        row.available_at = 0
    assert e.memory_queue.claim()["attempts"] == 2

    def no_change(**kwargs):
        assert kwargs["tool_choice"] == {"type": "any"}
        # Anthropic removes system-role messages; instructions must also be in
        # the dedicated system_prompt argument rather than silently disappearing.
        assert "Existing Semantic Memories" in kwargs["system_prompt"]
        return (
            {"content": ""},
            [{"name": "no_memory_changes", "arguments": {"reason": "Greeting only"}}],
            adapter.get_token_usage(),
        )

    adapter.execute_step_with_usage = no_change
    e.memory_queue.extract(task)
    e.memory_queue.apply_ready()
    with e.db.transaction() as s:
        row = s.get(MemoryTask, task["id"])
        assert row.status == "done" and row.error is None


def test_extraction_restart_recovers_running_task_and_bounds_retries(make_app):
    _, e, _ = make_app(["hello"])
    e.store.enqueue("hi")
    execute_next(e)
    task = e.memory_queue.claim()
    # Start the recovery path without a live loop racing the assertions.
    e.memory_queue._loop = lambda: None
    e.memory_queue.start()
    e.memory_queue.thread.join()
    recovered = e.memory_queue.claim()
    assert recovered["id"] == task["id"] and recovered["attempts"] == 2
    e.memory_queue.extract(recovered)
    with e.db.transaction() as s:
        s.get(MemoryTask, task["id"]).available_at = 0
    last = e.memory_queue.claim()
    assert last["attempts"] == 3
    e.memory_queue.extract(last)
    with e.db.transaction() as s:
        row = s.get(MemoryTask, task["id"])
        assert row.status == "failed" and row.finished_at
