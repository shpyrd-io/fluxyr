"""PostgreSQL scheduler and explicit outcome validation."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, update

from .database import row_dict
from .models import Job, Routine, Effect
from .text import prose_unicode


def next_occurrence(expression, timezone, after=None):
    if len(expression.split()) != 5:
        raise ValueError("Cron must have five fields")
    try:
        zone = ZoneInfo(timezone)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            "Choose a valid timezone, such as UTC or America/Sao_Paulo"
        ) from exc
    return (
        croniter(
            expression,
            datetime.fromtimestamp(after if after is not None else time.time(), zone),
        )
        .get_next(datetime)
        .timestamp()
    )


def action_outcome(db, job_id):
    """Execution results come from the Python handler, never model attestation."""
    with db.transaction() as s:
        effects = list(
            s.scalars(
                select(Effect)
                .where(
                    Effect.job_id == job_id,
                    Effect.tool_name.like(r"action\_%", escape="\\"),
                )
                .order_by(Effect.created_at)
            )
        )
        actions = [
            {
                "name": x.tool_name,
                "effect_id": x.id,
                "status": x.status,
                "result": x.result,
            }
            for x in effects
        ]
    failures = [
        a["name"]
        for a in actions
        if a["status"] != "done"
        or (a["result"] or {}).get("error")
        or (a["result"] or {}).get("done") is False
        or (a["result"] or {}).get("success") is False
    ]
    success = (
        False
        if failures
        else True
        if actions and all((a["result"] or {}).get("success") is True for a in actions)
        else None
    )
    return {
        "done": True,
        "success": success,
        "actions": actions,
        "failures": failures,
        "validation": "python_handler"
        if success is not None
        else "no_action_execution",
    }


class Routines:
    def __init__(self, db, store):
        self.db = db
        self.store = store

    def list(self):
        with self.db.transaction() as s:
            return [
                public_routine(r)
                for r in s.scalars(select(Routine).order_by(Routine.created_at.desc()))
            ]

    def prepare_create(self, values):
        if values.get("enabled") and not values.get("cron"):
            raise ValueError("Enabled schedules require cron")
        if values.get("cron"):
            next_occurrence(values["cron"], values.get("timezone", "UTC"))
        return values

    def put(self, values, routine_id=None):
        allowed = {
            "name",
            "prompt",
            "cron",
            "timezone",
            "enabled",
            "overlap",
        }
        values = {
            k: prose_unicode(v) if k in ("name", "prompt") else v
            for k, v in values.items()
            if k in allowed
        }
        with self.db.transaction() as s:
            row = (
                s.get(Routine, routine_id, with_for_update=True)
                if routine_id
                else Routine()
            )
            if not row:
                raise ValueError("Routine not found")
            for key, value in values.items():
                setattr(row, key, value)
            if not row.name or not row.prompt:
                raise ValueError("Name and prompt are required")
            row.expectation = row.expectation or ""
            row.timezone = row.timezone or "UTC"
            row.overlap = row.overlap or "queue"
            if row.overlap not in ("queue", "skip"):
                raise ValueError("Overlap must be queue or skip")
            upcoming = next_occurrence(row.cron, row.timezone) if row.cron else None
            row.next_run = upcoming if row.enabled else None
            if row.enabled and not row.cron:
                raise ValueError("Enabled schedules require cron")
            s.add(row)
            s.flush()
            return public_routine(row)

    def run(self, routine_id, s=None, schedule_key=None, parent=None, call_id=None):
        if s is None:
            with self.db.transaction() as tx:
                return self.run(routine_id, tx, schedule_key, parent, call_id)
        row = s.get(Routine, routine_id)
        if not row:
            raise ValueError("Routine not found")
        return self.store.enqueue(
            prose_unicode(row.prompt),
            None,
            row.id,
            {
                "routine_execution": True,
                "parent_job_id": parent["id"] if parent else None,
                "parent_call_id": call_id,
            },
            schedule_key,
            s,
        )

    def delete(self, routine_id):
        with self.db.transaction() as s:
            row = s.get(Routine, routine_id, with_for_update=True)
            if not row:
                raise ValueError("Routine not found")
            # Keep all execution history, including in-flight jobs.
            s.execute(
                update(Job).where(Job.routine_id == routine_id).values(routine_id=None)
            )
            s.delete(row)
        return {"deleted": True, "history_preserved": True}

    def tick(self):
        with self.db.transaction() as s:
            for row in s.scalars(
                select(Routine)
                .where(Routine.enabled == True, Routine.next_run <= time.time())
                .with_for_update(skip_locked=True)
            ):
                active = s.scalar(
                    select(Job.id)
                    .where(
                        Job.routine_id == row.id,
                        Job.status.in_(["queued", "running", "paused", "waiting"]),
                    )
                    .limit(1)
                )
                if not active or row.overlap == "queue":
                    self.run(row.id, s, f"{row.id}:{row.next_run}")
                # Coalesce missed occurrences after downtime; never flood the queue.
                row.next_run = next_occurrence(row.cron, row.timezone)


def public_routine(row):
    # Retain the old columns in storage, not in prompts or the active API contract.
    return {
        k: prose_unicode(v) if k in ("name", "prompt") else v
        for k, v in row_dict(row).items()
        if k not in {"expectation", "output_schema", "checks"}
    }
