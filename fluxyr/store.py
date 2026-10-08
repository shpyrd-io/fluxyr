"""Durable sessions, event log and per-session job queue."""

import copy
import time

from sqlalchemy import exists, func, or_, select, update

from .cancelled_context import stopped_context
from .database import MAIN_SESSION, row_dict
from .models import Decision, Event, Job, Message, Session, Skill, Tool, ToolVersion
from .persistence import event_payload, restore_tool_identity

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class Store:
    def __init__(self, db):
        self.db = db
        self.file_skills = []
        self.on_control = lambda: None

    def emit(self, session_id, job_id, kind, payload):
        with self.db.transaction() as s:
            event = Event(
                session_id=session_id,
                job_id=job_id,
                type=kind,
                payload=event_payload(kind, payload),
            )
            s.add(event)
            s.flush()
            return event.id

    def clear_context(self, session_id):
        # Lock the same session row used by claim: no worker can start mid-reset.
        with self.db.transaction() as s:
            session = s.get(Session, session_id, with_for_update=True)
            if not session:
                raise ValueError("Session not found")
            if s.scalar(
                select(Job.id)
                .where(Job.session_id == session_id, Job.status.not_in(TERMINAL))
                .limit(1)
            ):
                raise ValueError(
                    "Pause is not enough: cancel or finish outstanding executions before clearing context"
                )
            archive = Session(
                title="Archived · " + session.title, kind="archive", brain=session.brain
            )
            s.add(archive)
            s.flush()
            for model in (Job, Message, Event):
                s.execute(
                    update(model)
                    .where(model.session_id == session_id)
                    .values(session_id=archive.id)
                )
            session.brain = None
            session.memories = None
            session.memory_epoch += 1
            session.status = "idle"
            return {"cleared": True, "archive_session_id": archive.id}

    def event_cursor(self):
        with self.db.transaction() as s:
            return s.scalar(select(func.max(Event.id))) or 0

    def events(self, session_id, after=0, *, activity_after=None):
        with self.db.transaction() as s:
            query = select(Event).where(Event.id > after)
            if session_id:
                query = query.where(Event.session_id == session_id)
            if activity_after is not None:
                query = query.where(
                    or_(Event.type != "activity", Event.id > activity_after)
                )
            return [
                {**row_dict(x), "payload": restore_tool_identity(x.type, x.payload)}
                for x in s.scalars(query.order_by(Event.id).limit(300))
            ]

    def snapshot(self, s):
        tools = []
        for tool in s.scalars(
            select(Tool)
            .join(Skill)
            .where(
                Tool.active_version.is_not(None),
                Skill.enabled.is_(True),
                Skill.deleted_at.is_(None),
            )
        ):
            version = s.get(ToolVersion, tool.active_version)
            if version:
                tools.append({**row_dict(tool), "version": row_dict(version)})
        file_names = {x["name"] for x in self.file_skills}
        if file_names and s.scalar(
            select(Skill.id).where(
                Skill.name.in_(file_names), Skill.deleted_at.is_(None)
            )
        ):
            raise ValueError(
                "A file skill conflicts with a database skill name; rename one explicitly"
            )
        return {
            "tools": tools,
            "skills": [
                *copy.deepcopy(self.file_skills),
                *[
                    row_dict(x)
                    for x in s.scalars(
                        select(Skill).where(
                            Skill.enabled.is_(True), Skill.deleted_at.is_(None)
                        )
                    )
                ],
            ],
        }

    def enqueue(
        self,
        prompt,
        session_id=MAIN_SESSION,
        routine_id=None,
        inputs=None,
        schedule_key=None,
        s=None,
    ):
        if s is None:
            with self.db.transaction() as tx:
                return self.enqueue(
                    prompt, session_id, routine_id, inputs, schedule_key, tx
                )
        if session_id is None:
            session = Session(title=prompt[:90], kind="execution")
            s.add(session)
            s.flush()
            session_id = session.id
        session = s.get(Session, session_id, with_for_update=True)
        if not session:
            raise ValueError("Session not found")
        job = Job(
            session_id=session_id,
            routine_id=routine_id,
            prompt=prompt,
            input=inputs or {},
        )
        job.schedule_key = schedule_key
        s.add(job)
        s.flush()
        s.add(
            Message(session_id=session_id, job_id=job.id, role="user", content=prompt)
        )
        s.add(
            Event(
                session_id=session_id,
                job_id=job.id,
                type="queued",
                payload={"prompt": prompt},
            )
        )
        parent_id = job.input.get("parent_job_id")
        if parent_id and not job.input.get("builder"):
            parent = s.get(Job, parent_id)
            if parent:
                s.add(
                    Event(
                        session_id=parent.session_id,
                        job_id=parent.id,
                        type="execution_started",
                        payload={
                            "job_id": job.id,
                            "session_id": session_id,
                            "prompt": prompt,
                            "status": "queued",
                        },
                    )
                )
        return row_dict(job)

    def claim(self, owner):
        now = time.time()
        with self.db.transaction() as s:
            # Session lock, not a global queue lock: different sessions can run concurrently.
            session = s.scalar(
                select(Session)
                .where(
                    Session.status == "idle",
                    exists(
                        select(Job.id).where(
                            Job.session_id == Session.id, Job.status == "queued"
                        )
                    ),
                )
                .order_by(Session.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not session:
                return None
            job = s.scalar(
                select(Job)
                .where(Job.session_id == session.id, Job.status == "queued")
                .order_by(Job.created_at)
                .with_for_update()
                .limit(1)
            )
            job.status = session.status = "running"
            job.owner = owner
            job.lease_until = now + self.db.settings.lease_seconds
            job.started_at = job.started_at or now
            if job.snapshot is None:
                job.snapshot = self.snapshot(s)
            if job.brain is None and session.brain:
                job.brain = copy.deepcopy(session.brain)
            s.flush()
            return row_dict(job)

    def heartbeat(self, owner):
        with self.db.transaction() as s:
            s.execute(
                update(Job)
                .where(Job.owner == owner, Job.status.in_(["running", "paused"]))
                .values(lease_until=time.time() + self.db.settings.lease_seconds)
            )

    def recover(self, *, owner=None):
        with self.db.transaction() as s:
            jobs = s.scalars(
                select(Job)
                .where(
                    Job.status.in_(["running", "paused"]),
                    Job.owner.is_not(None),
                    (Job.owner == owner) if owner else (Job.lease_until < time.time()),
                )
                .with_for_update(skip_locked=True)
            )
            for j in jobs:
                j.status = "interrupted"
                j.error = "Worker stopped. Review recorded effects before retrying."
                j.finished_at = time.time()
                j.owner = None
                session = s.get(Session, j.session_id, with_for_update=True)
                session.status = "idle"
                self.retain_stopped_context(session, j)
                s.add(
                    Event(
                        session_id=j.session_id,
                        job_id=j.id,
                        type="interrupted",
                        payload={"error": j.error},
                    )
                )

    def control(self, job_id, action):
        with self.db.transaction() as s:
            job = s.get(Job, job_id, with_for_update=True)
            if not job:
                raise ValueError("Job not found")
            session = s.get(Session, job.session_id, with_for_update=True)
            if action == "resume":
                if job.status != "paused":
                    raise ValueError("Only paused jobs can resume")
                waiting_build = any(
                    t.get("_status") == "parked"
                    and (t.get("_result") or {}).get("__await_job__")
                    for t in (job.brain or {}).get("pending_tools", [])
                )
                job.status = (
                    "running"
                    if job.owner
                    else "building"
                    if waiting_build
                    else "queued"
                )
                job.control = None
            elif action in ("pause", "cancel"):
                if job.status in TERMINAL:
                    raise ValueError("Job already finished")
                if action == "pause" and job.status == "waiting":
                    raise ValueError(
                        "This job is already suspended for a human response"
                    )
                job.control = action
                if not job.owner:
                    job.status = "paused" if action == "pause" else "cancelled"
                    if action == "cancel":
                        job.finished_at = time.time()
                        self.retain_cancelled_context(session, job)
            else:
                raise ValueError("Unknown control action")
            if action in ("pause", "cancel", "resume"):
                for child_id in job.input.get("build_dependencies", {}).values():
                    child = s.get(Job, child_id, with_for_update=True)
                    if child and child.status not in TERMINAL:
                        if action == "resume":
                            if child.status != "paused":
                                continue
                            child.control = None
                            child.status = "running" if child.owner else "queued"
                        else:
                            if action == "pause" and child.status == "waiting":
                                continue
                            child.control = action
                            if not child.owner:
                                child.status = (
                                    "paused" if action == "pause" else "cancelled"
                                )
                                if action == "cancel":
                                    child.finished_at = time.time()
                        child_session = s.get(
                            Session, child.session_id, with_for_update=True
                        )
                        if child.status == "cancelled":
                            self.retain_cancelled_context(child_session, child)
                        self.sync_session(s, child_session)
            s.flush()
            self.sync_session(s, session)
            result = row_dict(job)
        # Invalidate only after commit, outside the SQLite transaction lock.
        self.on_control()
        return result

    def retain_cancelled_context(self, session, job):
        self.retain_stopped_context(session, job)

    def retain_stopped_context(self, session, job):
        # An unstarted queued job only has inherited context, never new history.
        if job.input.get("started"):
            state = stopped_context(job.brain, job.status)
            if state:
                session.brain = state

    def sync_session(self, s, session):
        s.flush()
        active = s.scalar(
            select(Job)
            .where(
                Job.session_id == session.id,
                Job.status.in_(["running", "waiting", "paused", "building"]),
            )
            .order_by(Job.created_at)
            .limit(1)
        )
        session.status = active.status if active else "idle"

    def decide(self, job_id, call_id, value):
        from .interactions.human import request_for
        from .runtime.human_protocol import response_for

        with self.db.transaction() as s:
            job = s.get(Job, job_id, with_for_update=True)
            if not job:
                raise ValueError("Job not found")
            old = s.scalar(
                select(Decision).where(
                    Decision.job_id == job_id, Decision.call_id == call_id
                )
            )
            if old:
                if old.value != value:
                    raise ValueError("This request already has a different decision")
                return {"duplicate": True, "status": job.status}
            if job.status != "waiting":
                raise ValueError("Job is not waiting for a response")
            entry = next(
                (
                    p
                    for p in (job.brain or {}).get("pending_tools", [])
                    if (p.get("_request_id") or p.get("call_id")) == call_id
                ),
                None,
            )
            if not entry:
                raise ValueError(
                    "Human request is no longer current; reload the execution"
                )
            if entry.get("name") in ("manage_vault_credential", "browser_request_input", "browser_register_passkey"):
                if value.get("decision") != "reject":
                    raise ValueError(
                        "Use the embedded private form to complete this request"
                    )
                # Never allow free-text/secret input into this specialized decision.
                value = {"decision": "reject"}
            else:
                response_for(request_for(entry), value)
            return self._record_decision(s, job, entry, call_id, value)

    def _record_decision(self, s, job, entry, call_id, value):
        """Persist a validated decision using the caller's transaction and row lock."""
        from .interactions.decision_gate import apply_decision

        outcome = apply_decision(
            job.brain,
            entry["call_id"],
            value.get("decision"),
            value.get("reason"),
            value.get("result"),
            now=str(time.time()),
            abandoned=False,
        )
        if outcome.error:
            raise ValueError(outcome.error)
        job.brain = outcome.new_brain_state
        s.add(Decision(job_id=job.id, call_id=call_id, value=value))
        if outcome.resuming:
            job.status = "queued"
            s.get(Session, job.session_id).status = "idle"
        s.add(
            Event(
                session_id=job.session_id,
                job_id=job.id,
                type="decision",
                payload={
                    "call_id": entry["call_id"],
                    "request_id": call_id,
                    **value,
                },
            )
        )
        return {"status": job.status, "remaining": outcome.pending_remaining}
