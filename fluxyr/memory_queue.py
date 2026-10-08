"""Durable extraction outside conversation workers, with explicit-write fencing."""

import copy
import logging
import threading
import time

from sqlalchemy import select, update

from .core.memory.episodic import EpisodicMemory
from .core.memory.extraction_context import turn_messages
from .core.memory.implicit import ImplicitMemory
from .core.memory.manager import MemoryManager
from .core.memory.semantic import SemanticMemory
from .database import row_dict
from .models import Event, MemoryTask, Session
from .providers import make_adapter
from .runtime.interruptible import InterruptibleAdapter
from .usage import bind_usage, standalone_context

LAYERS = ("semantic", "episodic", "implicit")
log = logging.getLogger(__name__)


def layers(state):
    return {k: copy.deepcopy(state[k]) for k in LAYERS if k in state}


def restore_memories(brain, state):
    brain._semantic = SemanticMemory.from_dict(state["semantic"])
    brain._episodic = EpisodicMemory.from_dict(state["episodic"])
    brain._implicit = ImplicitMemory.from_dict(state["implicit"])
    brain._memory_manager = MemoryManager(
        brain._semantic, brain._episodic, brain._implicit, brain._provider_adapter
    )


class MemoryQueue:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self.thread = None
        self.slots = threading.BoundedSemaphore(1)

    def enqueue(self, s, session, job, state):
        if job.input.get("builder") or not state.get("analysis_pending"):
            return
        if s.scalar(select(MemoryTask.id).where(MemoryTask.job_id == job.id)):
            return
        s.add(
            MemoryTask(
                session_id=session.id,
                job_id=job.id,
                epoch=session.memory_epoch,
                messages=turn_messages(job.prompt, state),
            )
        )

    def start(self):
        # Called only after the instance acquired the PostgreSQL supervisor fence.
        with self.db.transaction() as s:
            s.execute(
                update(MemoryTask)
                .where(MemoryTask.status == "running")
                .values(status="queued")
            )
        self.thread = threading.Thread(
            target=self._loop, daemon=True, name="memory-extraction"
        )
        self.thread.start()

    def _loop(self):
        while not self.engine.stopping.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Memory queue iteration failed")
            # Extraction is background work; do not poll an empty queue 3x/sec.
            self.engine.stopping.wait(5)

    def tick(self):
        self.apply_ready()
        task = self.claim()
        if task:
            self.extract(task)
            self.apply_ready()

    def claim(self):
        with self.db.transaction() as s:
            # One extraction lane; per-session FIFO also prevents a ready result
            # from being overtaken while the next foreground turn is still running.
            pending = list(
                s.scalars(
                    select(MemoryTask)
                    .where(MemoryTask.status.in_(["queued", "running", "ready"]))
                    .order_by(MemoryTask.created_at, MemoryTask.id)
                    .with_for_update(skip_locked=True)
                )
            )
            blocked = set()
            for task in pending:
                session = s.get(Session, task.session_id)
                if (
                    not session
                    or task.epoch != session.memory_epoch
                    or not session.brain
                ):
                    task.status = "discarded"
                    task.finished_at = time.time()
                    continue
                if task.session_id in blocked:
                    continue
                blocked.add(task.session_id)
                if task.status != "queued" or task.available_at > time.time():
                    continue
                task.status = "running"
                task.attempts += 1
                s.flush()
                return {
                    **row_dict(task),
                    "base": copy.deepcopy(session.memories or layers(session.brain)),
                }
        return None

    def extract(self, task):
        try:
            if self.engine.adapter_factory:
                adapter = self.engine.adapter_factory()
            else:
                config = {
                    **self.db.model_config(),
                    "max_tokens": 2048,
                    "thinking_mode": "none",
                    "thinking_budget": 0,
                    "reasoning_effort": "",
                }
                adapter = make_adapter(config, self.engine.vault)
            adapter = bind_usage(
                adapter,
                lambda kind, payload: self.engine.store.emit(
                    task["session_id"], task["job_id"], kind, payload
                ),
                lambda: standalone_context("memory"),
            )
            adapter = InterruptibleAdapter(
                adapter,
                lambda: "cancel" if self.engine.stopping.is_set() else None,
                self.slots,
                tasks=self.engine.provider_tasks,
            )
            base = task["base"]
            manager = MemoryManager(
                SemanticMemory.from_dict(base["semantic"]),
                EpisodicMemory.from_dict(base["episodic"]),
                ImplicitMemory.from_dict(base["implicit"]),
                adapter,
            )
            usage = manager.analyze_conversation(
                task["messages"], curated=True, strict=True
            )
            result = {
                "semantic": manager._semantic.to_dict(),
                "episodic": manager._episodic.to_dict(),
                "implicit": manager._implicit.to_dict(),
            }
            with self.db.transaction() as s:
                row = s.get(MemoryTask, task["id"], with_for_update=True)
                row.result = result
                row.status = "ready"
                row.error = None
                s.add(
                    Event(
                        session_id=row.session_id,
                        job_id=row.job_id,
                        type="memory_extracted",
                        payload={
                            "task_id": row.id,
                            "usage": usage.__dict__,
                            "decisions": manager.last_analysis_decisions,
                        },
                    )
                )
        except Exception as exc:
            with self.db.transaction() as s:
                row = s.get(MemoryTask, task["id"], with_for_update=True)
                row.status = (
                    "queued"
                    if self.engine.stopping.is_set() or row.attempts < 3
                    else "failed"
                )
                row.error = str(exc)
                row.available_at = time.time() + (
                    0 if self.engine.stopping.is_set() else 30 * row.attempts
                )
                if row.status == "failed":
                    row.finished_at = time.time()
        finally:
            client = getattr(locals().get("adapter"), "client", None)
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def apply_ready(self):
        with self.db.transaction() as s:
            tasks = s.scalars(
                select(MemoryTask)
                .where(MemoryTask.status == "ready")
                .order_by(MemoryTask.created_at)
                .with_for_update(skip_locked=True)
            )
            for task in tasks:
                session = s.get(Session, task.session_id, with_for_update=True)
                if (
                    not session
                    or session.memory_epoch != task.epoch
                    or not session.brain
                ):
                    task.status = "discarded"
                elif session.status != "idle":
                    continue
                else:
                    session.memories = copy.deepcopy(task.result)
                    session.brain = {**session.brain, **copy.deepcopy(task.result)}
                    task.status = "done"
                    s.add(
                        Event(
                            session_id=session.id,
                            job_id=task.job_id,
                            type="memory_updated",
                            payload={"task_id": task.id},
                        )
                    )
                task.finished_at = time.time()
