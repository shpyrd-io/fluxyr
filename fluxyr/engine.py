"""One application process, one supervisor thread, bounded execution workers."""

import copy
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text

from .activity import ActivityEmitter
from .builds import Builds
from .core.brain import SyntheticBrain
from .core.utils.enums import BrainState
from .database import MAIN_SESSION, Database
from .files import Files
from .interactions.human import action_wait, continuation_for, repark, request_for
from .interactions.tool_outputs import build_tool_outputs
from .memory_queue import MemoryQueue, layers, restore_memories
from .models import Event, Job, Message, Session
from .prompts import load_prompt
from .providers import make_adapter, response_text
from .reactive import ReactiveRoutines
from .routines import Routines, action_outcome
from .runtime.effects import Effects
from .runtime.human_protocol import response_for
from .runtime.interruptible import InterruptibleAdapter, ThreadGroup
from .runtime.python_runner import PythonRunner
from .runtime.worker_monitor import WorkerMonitor
from .skills import Skills
from .store import Store
from .streaming import StreamRecorder
from .tools.registry import Registry
from .tools.workspace import WorkspaceTools
from .vault import Vault

log = logging.getLogger(__name__)


class Engine:
    heartbeat_interval = 10

    def __init__(self, settings, adapter_factory=None):
        settings.prepare()
        self.settings = settings
        from .file_skills import load_file_skills

        file_skills = load_file_skills(settings)
        self.db = Database(settings)
        self.db.initialize()
        self.store = Store(self.db)
        self.vault = Vault(self.db)
        from .browser import Browsers

        self.browsers = Browsers(self)
        self.files = Files(settings.data)
        self.workspace_tools = WorkspaceTools(settings)
        self.runner = PythonRunner(settings, self.vault)
        self.skills = Skills(self.db, self.runner, file_skills)
        self.native_tools = {}
        self.app = None
        self.store.file_skills = self.skills.file_skills
        self.reactive = ReactiveRoutines(self)
        self.routines = Routines(self.db, self.store, self.reactive)
        self.effects = Effects(self.db)
        self.builds = Builds(self)
        self.memory_queue = MemoryQueue(self)
        self.adapter_factory = adapter_factory
        from .core import brain_tool_batch

        brain_tool_batch.MAX_CONCURRENT_WORKERS = settings.tool_workers
        self.owner = str(uuid.uuid4())
        self.stopping = threading.Event()
        self.leader = None
        self._sqlite_leader = None
        self.thread = None
        self._start_lock = threading.Lock()
        self.pool = None
        self.futures = set()
        self.provider_slots = threading.BoundedSemaphore(settings.workers)
        self.provider_tasks = ThreadGroup()
        self.monitor = WorkerMonitor(self)
        self._control_lock = threading.Lock()
        self._control_cache = {}
        self.store.on_control = self._invalidate_control_cache

    def adapter(self):
        return (
            self.adapter_factory()
            if self.adapter_factory
            else make_adapter(self.db.model_config(), self.vault)
        )

    def start(self):
        with self._start_lock:
            self._start()

    def _start(self):
        if self.thread:
            return
        self._acquire_fence()
        # Purge once per application lifetime, not on each recovery.
        from .runtime.workspace_cleanup import purge_workspace

        purge_workspace(self.settings, self.db)
        self._launch_generation()
        self.monitor.start()

    def _acquire_fence(self):
        if self.db.engine.dialect.name == "postgresql":
            self.leader = self.db.engine.connect()
            self.leader.execute(text("SET statement_timeout = 5000"))
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
        elif self.db.sqlite and self.db.engine.url.database not in (
            None,
            "",
            ":memory:",
        ):
            import fcntl
            import os
            from pathlib import Path

            database = Path(self.db.engine.url.database).resolve()
            lock = os.fdopen(
                os.open(str(database) + ".worker.lock", os.O_CREAT | os.O_RDWR, 0o600),
                "a+b",
            )
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                raise RuntimeError(
                    "Another Fluxyr worker already owns this SQLite database"
                ) from None
            self._sqlite_leader = lock

    def _launch_generation(self):
        self.pool = ThreadPoolExecutor(
            max_workers=self.settings.workers, thread_name_prefix="agent"
        )
        self.reactive.future = None
        self.reactive.start()
        self.memory_queue.start()
        self.thread = threading.Thread(
            target=self._supervise, name="agent-supervisor", daemon=True
        )
        self.thread.start()

    def _release_fence(self):
        if self.leader:
            try:
                if not self.leader.invalidated:
                    self.leader.execute(
                        text(
                            "SELECT pg_advisory_unlock(hashtext(current_schema()), 70399)"
                        )
                    )
                    self.leader.commit()
            except Exception:
                log.warning("Lost database connection while releasing worker fence")
                self.leader.invalidate()
            finally:
                self.leader.close()
                self.leader = None
        if self._sqlite_leader:
            self._sqlite_leader.close()
            self._sqlite_leader = None

    def _drain_generation(self):
        self.stopping.set()
        if self.thread:
            self.thread.join()
        self.reactive.stop()
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
        if self.memory_queue.thread:
            self.memory_queue.thread.join()
        self.provider_tasks.join()
        self._release_fence()

    def _restart_generation(self):
        # Every previous future, subprocess owner and detached provider call has
        # finished before this method can run. Never reuse an ownership token.
        old_owner = self.owner
        try:
            self._acquire_fence()
            self.store.recover(owner=old_owner)
            self.owner = str(uuid.uuid4())
            self._control_cache.clear()
            self.futures.clear()
            if self.monitor.shutdown.is_set():
                raise RuntimeError("Application is shutting down")
            self.stopping.clear()
            self._launch_generation()
        except Exception:
            self.stopping.set()
            # A partial launch may already have background tasks; drain before
            # another attempt, retaining the fence until they have finished.
            self._drain_generation()
            raise

    def stop(self):
        self.monitor.shutdown.set()
        self.stopping.set()
        if self.monitor.thread:
            self.monitor.thread.join()
        if self.monitor.draining:
            self.monitor.draining.join()
        else:
            self._drain_generation()
        self.browsers.close()
        self.monitor.state = "stopped"

    def _supervise(self):
        heartbeat = 0
        scheduled = 0
        idle_delay = 0.35
        while not self.stopping.is_set():
            try:
                dispatched = False
                if time.monotonic() - heartbeat > self.heartbeat_interval:
                    if self.leader:
                        self.leader.execute(text("SELECT 1"))
                        self.leader.commit()
                    self.store.heartbeat(self.owner)
                    self.store.recover()
                    heartbeat = time.monotonic()
                if time.monotonic() - scheduled >= 1:
                    self.routines.tick()
                    self.reactive.tick()
                    self.builds.resolve_dependencies()
                    scheduled = time.monotonic()
                for future in list(self.futures):
                    if future.done():
                        self.futures.remove(future)
                        # An uncaught task exception must not leave a running
                        # job with a lease that is renewed forever.
                        future.result()
                while (
                    len(self.futures) < self.settings.workers
                    and not self.stopping.is_set()
                ):
                    job = self.store.claim(self.owner)
                    if not job:
                        break
                    self.futures.add(self.pool.submit(self.execute, job))
                    dispatched = True
                self.monitor.healthy()
                idle_delay = (
                    0.35 if dispatched or self.futures else min(2, idle_delay * 1.5)
                )
            except Exception as exc:
                log.exception("Supervisor iteration failed")
                self.monitor.failed(exc)
                return
            self.stopping.wait(idle_delay)

    def _invalidate_control_cache(self):
        with self._control_lock:
            self._control_cache.clear()

    def _control(self, job_id):
        if self.stopping.is_set():
            return "pause"
        # Shared across stream callbacks and subprocess polling for this job.
        # A bounded 250 ms delay still keeps pause/cancel responsive.
        with self._control_lock:
            now = time.monotonic()
            cached = self._control_cache.get(job_id)
            if cached and now - cached[0] < 0.25:
                return cached[1]
            with self.db.transaction() as s:
                row = s.execute(
                    select(Job.control, Job.owner).where(Job.id == job_id)
                ).first()
            value = row.control if row and row.owner == self.owner else "cancel"
            self._control_cache[job_id] = (time.monotonic(), value)
            return value

    def execute(self, job):
        try:
            if self.app is not None:
                with self.app.app_context():
                    return self._execute(job)
            return self._execute(job)
        finally:
            with self._control_lock:
                self._control_cache.pop(job["id"], None)

    def _execute(self, job):
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
                pending = (job.get("brain") or {}).get("pending_tools", [])
                decision = pending[0].get("_decision", {}) if pending else {}
                if decision.get("decision") == "reject" and not (
                    pending[0].get("_result") or {}
                ).get("__human__"):
                    self._settle(job, None, "cancelled", None, "Test declined.")
                    return
                result = self.skills.test(
                    **job["input"]["tool_test"],
                    emit=lambda text: emit("progress", {"text": text}),
                    stop=registry.stop,
                    answer=decision.get("result"),
                    continuation=continuation_for(pending[0])
                    if pending and decision
                    else None,
                )
                if result.get("waiting"):
                    payload = action_wait(result, self.files)
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
                + "\n\n"
                + load_prompt("vault")
                + "\n\nInstalled skills:\n"
                + json.dumps(job["snapshot"]["skills"], ensure_ascii=False)
            )
            brain = SyntheticBrain(
                InterruptibleAdapter(
                    bind_usage(self.adapter(), emit),
                    lambda: self._control(jid),
                    self.provider_slots,
                    emit=emit,
                    tasks=self.provider_tasks,
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
                from .inspection import compact_inspection_history

                compact_inspection_history(brain._short_term.get_all())
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
                    if decision.get("decision") == "reject" and not (
                        entry.get("_result") or {}
                    ).get("__human__"):
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
                            answer=None
                            if (entry.get("_result") or {})
                            .get("__pua__", {})
                            .get("payload", {})
                            .get("preflight")
                            else decision.get("result"),
                            continuation=continuation_for(entry),
                        )
                    elif entry["name"] in ("manage_vault_credential", "browser_request_input", "browser_register_passkey"):
                        # Validated server-side by the private Vault form endpoint.
                        result = {**decision["result"], "status": "completed"}
                        mode = "continue"
                    elif entry["name"] == "test_action":
                        result, mode = registry.test_action(
                            entry.get("arguments", {}),
                            entry["call_id"],
                            (entry.get("coord"), entry.get("occurrence_hint", 0)),
                            answer=decision.get("result"),
                            continuation=continuation_for(entry),
                        )
                    else:
                        mode = "continue"
                        if entry["name"] == "ask_human":
                            reply = response_for(request_for(entry), decision)
                            result = {
                                **reply,
                                "answer": reply.get("user_input", reply.get("label")),
                                "status": "completed",
                            }
                        else:
                            result = {
                                "answer": decision.get("result")
                                or decision.get("reason")
                                or "approved",
                                "status": "completed",
                            }
                    emit(
                        "tool_end",
                        {
                            "tool_name": entry["name"],
                            "tool_call_id": entry["call_id"],
                            "mode": mode,
                            "result": result,
                        },
                    )
                    if mode == "wait":
                        repark(entry, result)
                        brain._pending_tools = pending
                        checkpoint(brain.save())
                        continue
                    entry["_reexec"] = {"result": result}
                    brain._pending_tools = pending
                    checkpoint(brain.save())
                if any(
                    t.get("_status") == "parked"
                    and not t.get("_decision")
                    and t.get("_reexec") is None
                    for t in pending
                ):
                    state = brain.save()
                    state["pending_tools"] = pending
                    state["current_state"] = "WAITING"
                    state["_gate_fire_attempts"] = 0
                    state.pop("_fire_owed", None)
                    self._settle(job, None, "waiting", None, "", state_override=state)
                    return
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
            emit.flush_fragments()
            self._settle(
                job,
                brain,
                outcome_status,
                outcome,
                response_text(response),
                usage.__dict__,
            )
        except Exception as exc:
            emit.flush_fragments()
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
        if self.stopping.is_set() and self.monitor.state in ("recovering", "failed"):
            status, outcome, text = "interrupted", None, ""
            error = "Worker stopped. Review recorded effects before retrying."
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
            if status == "cancelled":
                self.store.retain_cancelled_context(session, row)
            elif status in ("failed", "interrupted") and error:
                # Keep acquired history while closing unresolved tool calls and
                # clearing ERROR/pending state before the next user message.
                self.store.retain_stopped_context(session, row)
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
            # The original failed state stays on the job for inspection; only
            # the session copy is sanitized for a fresh turn.
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
