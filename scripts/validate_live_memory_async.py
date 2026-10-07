"""Real MiniMax extraction/recall probe in an isolated PostgreSQL schema."""

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.engine import make_url
from fluxyr_agent.config import Settings
from fluxyr_agent.engine import Engine
from fluxyr_agent.models import Job, Session, MemoryTask, Event, Message


def wait_for(fn, seconds=180):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = fn()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError("Timed out waiting for probe")


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=True)
    base = Settings()
    run = uuid.uuid4().hex[:12]
    schema = "async_memory_" + run
    with create_engine(base.database_url).begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(base.database_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    settings = Settings(
        database_url=url.render_as_string(hide_password=False),
        root=root / ".runtime/live-validation" / run,
        workers=1,
    )
    e = Engine(settings)
    report = {"run_id": run, "mock_model": False, "model": e.db.model_config()["model"]}
    print(json.dumps(report), flush=True)
    try:
        # Synthetic persona in an isolated schema; no real user data is changed.
        job = e.store.enqueue(
            'Minha cidade de trabalho é Recife. Nesta resposta, diga apenas "Entendido" e não use ferramentas.',
            None,
        )
        e.start()

        def finished():
            with e.db.transaction() as s:
                row = s.get(Job, job["id"])
                if row.status in ("failed", "waiting", "paused"):
                    raise AssertionError(row.error or row.status)
                if row.status != "succeeded":
                    return None
                last = s.scalar(
                    select(func.max(Event.created_at)).where(
                        Event.job_id == row.id, Event.type == "stream_close"
                    )
                )
                task = s.scalar(select(MemoryTask).where(MemoryTask.job_id == row.id))
                return {
                    "seconds_after_stream": round(row.finished_at - last, 3),
                    "memory_status_when_chat_finished": task.status,
                }

        report.update(wait_for(finished))
        print(json.dumps(report), flush=True)
        assert report["seconds_after_stream"] < 2, report

        def extracted():
            with e.db.transaction() as s:
                task = s.scalar(
                    select(MemoryTask).where(MemoryTask.job_id == job["id"])
                )
                if task.status == "failed":
                    raise AssertionError(task.error)
                if task.status == "done":
                    report["extraction"] = s.scalar(
                        select(Event.payload).where(
                            Event.job_id == job["id"], Event.type == "memory_extracted"
                        )
                    )
                return task.status == "done"

        wait_for(extracted)
        with e.db.transaction() as s:
            session = s.get(Session, job["session_id"])
            assert (
                "recife"
                in json.dumps(
                    session.memories["semantic"], ensure_ascii=False
                ).casefold()
            ), session.memories
            state = dict(session.brain)
            state["short_term"] = {
                **state["short_term"],
                "items": [
                    m for m in state["short_term"]["items"] if m.get("role") == "system"
                ],
            }
            state["episodic"] = {**state["episodic"], "items": []}
            state["implicit"] = {**state["implicit"], "patterns": {}}
            session.brain = state
            session.memories = {
                k: state[k] for k in ("semantic", "episodic", "implicit")
            }
        e.stop()
        e.db.engine.dispose()
        e = Engine(settings)
        recall = e.store.enqueue(
            "Qual é minha cidade de trabalho? Responda só o nome da cidade, conforme sua memória.",
            job["session_id"],
        )
        e.start()

        def recalled():
            with e.db.transaction() as s:
                row = s.get(Job, recall["id"])
                if row.status == "failed":
                    raise AssertionError(row.error)
                if row.status != "succeeded":
                    return None
                return s.scalar(
                    select(Message.content).where(
                        Message.job_id == row.id, Message.role == "assistant"
                    )
                )

        answer = wait_for(recalled)
        assert "recife" in answer.casefold(), answer
        report.update(
            success=True, automatic_semantic_extraction=True, restored_recall=answer
        )
    except Exception as exc:
        report.update(success=False, error=str(exc))
    finally:
        e.stop()
        e.db.engine.dispose()
        (settings.root / "async-memory-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
