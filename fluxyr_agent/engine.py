"""One application process, one supervisor thread, bounded execution workers."""

import copy
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from .builds import Builds
from .activity import ActivityEmitter
from .memory_queue import MemoryQueue, layers, restore_memories
from .core.brain import SyntheticBrain
from .core.utils.enums import BrainState
from .database import MAIN_SESSION, Database
from .files import Files
from .interactions.tool_outputs import build_tool_outputs
from .models import Event, Job, Message, Session
from .prompts import load_prompt
from .providers import make_adapter, response_text
from .routines import Routines, action_outcome
from .runtime.effects import Effects
from .runtime.interruptible import InterruptibleAdapter
from .runtime.python_runner import PythonRunner
from .skills import Skills
from .store import Store
from .streaming import StreamRecorder
from .tools.registry import Registry
from .vault import Vault

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings, adapter_factory=None):
        settings.prepare()
        self.settings = settings
        self.db = Database(settings)
        self.db.initialize()
        self.store = Store(self.db)
        self.vault = Vault(self.db)
        self.files = Files(settings.data)
        self.runner = PythonRunner(settings, self.vault)
        self.skills = Skills(self.db, self.runner)
        self.routines = Routines(self.db, self.store)
        self.effects = Effects(self.db)
        self.builds = Builds(self)
        self.memory_queue = MemoryQueue(self)
        self.adapter_factory = adapter_factory
        from .core import brain_tool_batch

        brain_tool_batch.MAX_CONCURRENT_WORKERS = settings.tool_workers
        self.owner = str(uuid.uuid4())
        self.stopping = threading.Event()
        self.leader = None
        self.thread = None
        self.pool = None
        self.futures = set()
        self.provider_slots = threading.BoundedSemaphore(settings.workers)

    def adapter(self):
        return (
            self.adapter_factory()
            if self.adapter_factory
            else make_adapter(self.db.model_config(), self.vault)
        )

    def start(self):
        if self.thread:
            return
        if self.db.engine.dialect.name == "postgresql":
            self.leader = self.db.engine.connect()
            acquired = self.leader.scalar(
                text("SELECT pg_try_advisory_lock(hashtext(current_schema()), 70399)")
            )
            self.leader.commit()
            if not acquired:
                self.leader.close()
                self.leader = None
                raise RuntimeError(
                    "Another Fluxyr Agent worker already owns this database schema"
                )
        self.pool = ThreadPoolExecutor(
            max_workers=self.settings.workers, thread_name_prefix="agent"
        )
        self.thread = threading.Thread(
            target=self._supervise, name="agent-supervisor", daemon=True
        )
        self.thread.start()
        self.memory_queue.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=5)
        if self.pool:
            self.pool.shutdown(wait=True)
        if self.memory_queue.thread:
            self.memory_queue.thread.join(timeout=5)
        if self.leader:
            self.leader.execute(
                text("SELECT pg_advisory_unlock(hashtext(current_schema()), 70399)")
            )
            self.leader.commit()
            self.leader.close()
            self.leader = None

    def _supervise(self):
        heartbeat = 0
        scheduled = 0
        while not self.stopping.is_set():
            try:
                if time.monotonic() - heartbeat > 10:
                    if self.leader:
                        self.leader.execute(text("SELECT 1"))
                        self.leader.commit()
                    self.store.heartbeat(self.owner)
                    self.store.recover()
                    heartbeat = time.monotonic()
                if time.monotonic() - scheduled >= 1:
                    self.routines.tick()
                    scheduled = time.monotonic()
                self.builds.resolve_dependencies()
                self.futures = {f for f in self.futures if not f.done()}
                while (
                    len(self.futures) < self.settings.workers
                    and not self.stopping.is_set()
                ):
                    job = self.store.claim(self.owner)
                    if not job:
                        break
                    self.futures.add(self.pool.submit(self.execute, job))
            except Exception:
                log.exception("Supervisor iteration failed")
                if self.leader and self.leader.invalidated:
                    self.stopping.set()  # lost the process fence; stop dispatch until explicit restart
            self.stopping.wait(0.35)

    def _control(self, job_id):
        if self.stopping.is_set():
            return "pause"
        with self.db.transaction() as s:
            job = s.get(Job, job_id)
            return job.control if job and job.owner == self.owner else "cancel"

    def execute(self, job):
        from .usage import bind_usage

        sid, jid = job["session_id"], job["id"]
        emit = ActivityEmitter(self.store, job)

        def stop_tool():
            return self.stopping.is_set() or self._control(jid) == "cancel"

        def pause_tool(paused):
            with self.db.transaction() as s:
                row = s.get(Job, jid, with_for_update=True)
                if row.owner != self.owner:
                    return
                row.status = "paused" if paused else "running"
                self.store.sync_session(s, s.get(Session, sid, with_for_update=True))
            emit("paused" if paused else "started", {"local_process": True})

        stop_tool.control = lambda: self._control(jid)
        stop_tool.paused = pause_tool
        registry = Registry(self, job, emit, stop_tool)
        brain = None
        try:
            emit("started", {})
            if job["input"].get("tool_test"):
                from .tools.registry import human

                pending = (job.get("brain") or {}).get("pending_tools", [])
                decision = pending[0].get("_decision", {}) if pending else {}
                if decision.get("decision") == "reject":
                    self._settle(job, None, "cancelled", None, "Test declined.")
                    return
                result = self.skills.test(
                    **job["input"]["tool_test"],
                    emit=lambda text: emit("progress", {"text": text}),
                    stop=registry.stop,
                    answer=decision.get("result"),
                )
                if result.get("waiting"):
                    payload, _ = human(result["waiting"]["question"])
                    state = {
                        "provider": "local",
                        "model": "python",
                        "current_state": "WAITING",
                        "pending_tools": [
                            {
                                "name": "test_action",
                                "call_id": str(uuid.uuid4()),
                                "arguments": job["input"]["tool_test"],
                                "_status": "parked",
                                "_result": payload,
                            }
                        ],
                    }
                    self._settle(job, None, "waiting", None, "", state_override=state)
                else:
                    control = self._control(jid)
                    status = (
                        "cancelled"
                        if control == "cancel"
                        else ("succeeded" if result["passed"] else "failed")
                    )
                    self._settle(
                        job,
                        None,
                        status,
                        {"success": result["passed"], "test": result},
                        json.dumps(result, ensure_ascii=False),
                    )
                return

            def checkpoint(state):
                with self.db.transaction() as s:
                    row = s.get(Job, jid, with_for_update=True)
                    if row.owner != self.owner or row.status != "running":
                        raise RuntimeError("Worker lease lost")
                    row.brain = copy.deepcopy(state)

            def memory_changed():
                # Commit explicit memory mutations before returning their tool result.
                state = brain.save()
                with self.db.transaction() as s:
                    row = s.get(Job, jid, with_for_update=True)
                    if row.owner != self.owner:
                        raise RuntimeError("Worker lease lost")
                    row.brain = copy.deepcopy(state)
                    session = s.get(Session, sid, with_for_update=True)
                    session.memory_epoch += 1
                    session.memories = layers(state)
                    if session.brain:
                        session.brain = {**session.brain, **layers(state)}

            prompt = (
                (
                    load_prompt("skill_build") + "\n\n" + load_prompt("tools_guide")
                    if job["input"].get("builder")
                    else load_prompt("chat") + "\n\n" + load_prompt("workbench")
                )
                + "\n\nInstalled skills:\n"
                + json.dumps(job["snapshot"]["skills"], ensure_ascii=False)
            )
            brain = SyntheticBrain(
                InterruptibleAdapter(
                    bind_usage(self.adapter(), emit),
                    lambda: self._control(jid),
                    self.provider_slots,
                    emit=emit,
                ),
                tools=registry.definitions,
                system_prompt=prompt,
                short_term_capacity=200,
                max_iterations=self.settings.max_iterations,
                stream_callback=StreamRecorder(emit),
                tool_lifecycle_callback=lambda event: emit(event["event"], event),
                quota_check=lambda _: self._control(jid),
                checkpoint=checkpoint,
                automatic_memory=False,
                memory_changed=memory_changed,
                effect_scope={"job_id": jid},
            )
            registry.brain = brain
            state = job.get("brain")
            if state:
                brain.restore(state, allow_model_change=True)
            with self.db.transaction() as s:
                persisted = s.get(Session, sid).memories
                if persisted:
                    restore_memories(brain, persisted)
            # A new job inherits conversation memory, never another job's pending calls/effect ordinals.
            continuation = bool(job.get("input", {}).get("started"))
            if not continuation:
                brain._effect_occurrences = {}
                brain._pending_tools = []
                with self.db.transaction() as s:
                    row = s.get(Job, jid)
                    row.input = {**row.input, "started": True}
            outputs = None
            if continuation and state and state.get("pending_tools"):
                pending = copy.deepcopy(state["pending_tools"])
                for entry in pending:
                    if (
                        entry.get("_status") != "parked"
                        or entry.get("_reexec") is not None
                    ):
                        continue
                    decision = entry.get("_decision") or {}
                    if not decision:
                        raise RuntimeError(
                            "Cannot resume while a human request is unresolved"
                        )
                    if decision.get("decision") == "reject":
                        continue
                    tool = next(
                        (
                            t
                            for t in registry.snapshot["tools"]
                            if t["name"] == entry["name"]
                        ),
                        None,
                    )
                    if tool:
                        result, mode = registry.action(
                            tool,
                            entry.get("arguments", {}),
                            entry["call_id"],
                            (entry.get("coord"), entry.get("occurrence_hint", 0)),
                            approved=True,
                            answer=decision.get("result"),
                        )
                        if mode == "wait":
                            raise RuntimeError(
                                "Action requested another human response after receiving one"
                            )
                    else:
                        result = {
                            "answer": decision.get("result")
                            or decision.get("reason")
                            or "approved",
                            "status": "completed",
                        }
                    entry["_reexec"] = {"result": result}
                    brain._pending_tools = pending
                    checkpoint(brain.save())
                outputs = build_tool_outputs(pending)
                brain._pending_tools = []
            response, status, _, usage = brain.step(
                None if continuation else job["prompt"], tool_outputs=outputs
            )
            control = self._control(jid)
            if control == "cancel":
                outcome_status = "cancelled"
                outcome = None
            elif control == "pause":
                outcome_status = "paused"
                outcome = None
            elif status == BrainState.WAITING:
                pending = brain.save().get("pending_tools", [])
                parked = [t for t in pending if t.get("_status") == "parked"]
                outcome_status = (
                    "building"
                    if parked
                    and all(
                        (t.get("_result") or {}).get("__await_job__") for t in parked
                    )
                    else "waiting"
                )
                outcome = None
            else:
                outcome = (
                    self.builds.outcome(job)
                    if job["input"].get("builder")
                    else action_outcome(self.db, jid)
                    if job.get("routine_id") or job["input"].get("routine_execution")
                    else {"success": brain.last_step_finish_reason == "end_turn"}
                )
                if brain.last_step_finish_reason == "max_iterations":
                    outcome = {
                        "success": False,
                        "failures": ["Maximum reasoning iterations reached"],
                    }
                outcome_status = (
                    "failed" if outcome["success"] is False else "succeeded"
                )
            self._settle(
                job,
                brain,
                outcome_status,
                outcome,
                response_text(response),
                usage.__dict__,
            )
        except Exception as exc:
            control = self._control(jid)
            if control not in ("pause", "cancel"):
                log.exception("Job %s failed", jid)
            elif brain:
                brain._current_state = BrainState.READY
            self._settle(
                job,
                brain,
                "cancelled"
                if control == "cancel"
                else "paused"
                if control == "pause"
                else "failed",
                None,
                "",
                error=None if control in ("pause", "cancel") else str(exc),
            )

    def _settle(
        self,
        job,
        brain,
        status,
        outcome,
        text,
        usage=None,
        error=None,
        state_override=None,
    ):
        state = state_override or (brain.save() if brain else job.get("brain"))
        with self.db.transaction() as s:
            row = s.get(Job, job["id"], with_for_update=True)
            if row.owner != self.owner:
                return
            row.status = status
            row.brain = state
            row.outcome = outcome
            row.error = error
            row.owner = None
            row.lease_until = None
            session = s.get(Session, row.session_id, with_for_update=True)
            self.store.sync_session(s, session)
            if (
                status in ("succeeded", "failed")
                and state
                and state.get("provider") != "local"
                and not error
            ):
                session.brain = state
                session.memories = layers(state)
                self.memory_queue.enqueue(s, session, row, state)
                state["analysis_pending"] = False
                # JSON columns need fresh assignments after changing nested state.
                row.brain = copy.deepcopy(state)
                session.brain = copy.deepcopy(state)
            # Failed conversations are retained on the job for inspection; never poison the next turn.
            if status not in ("waiting", "paused", "building"):
                row.finished_at = time.time()
            if text:
                s.add(
                    Message(
                        session_id=row.session_id,
                        job_id=row.id,
                        role="assistant",
                        content=text,
                    )
                )
            payload = {
                "status": status,
                "outcome": outcome,
                "error": error,
                "usage": usage,
            }
            if status == "waiting":
                payload["pending"] = state.get("pending_tools", [])
            s.add(
                Event(
                    session_id=row.session_id,
                    job_id=row.id,
                    type=status,
                    payload=payload,
                )
            )
            if row.session_id != MAIN_SESSION and status in (
                "succeeded",
                "failed",
                "waiting",
                "interrupted",
            ):
                s.add(
                    Event(
                        session_id=MAIN_SESSION,
                        job_id=row.id,
                        type="execution_report",
                        payload={
                            **payload,
                            "session_id": row.session_id,
                            "prompt": row.prompt,
                        },
                    )
                )
