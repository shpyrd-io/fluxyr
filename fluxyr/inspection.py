"""Small, navigable model views. Full source/history remains in persistence/UI."""

import json

from sqlalchemy import select
from sqlalchemy.orm import load_only

from .database import row_dict
from .models import Event, Job, Message, Skill, Tool, ToolVersion


def compact_inspection_history(messages):
    """Bound legacy inspection dumps when resuming an existing conversation.

    Keep tool-call identities and all user/action results; full inspection output
    remains in the event log. Explicit source pages fit below this threshold.
    """
    for message in messages:
        content = message.get("content")
        if (
            message.get("role") != "tool"
            or message.get("name")
            not in {
                "inspect_execution",
                "list_executions",
                "get_skill",
                "list_skills",
                "create_action",
            }
            or not isinstance(content, str)
            or len(content) <= 32000
        ):
            continue
        try:
            value = json.loads(content)
        except ValueError:
            value = content
        message["content"] = json.dumps(
            {
                "previous_result": clip(value, 600),
                "note": "Large historical inspection shortened. Full output remains in execution events; use inspect_execution pagination or get_skill with version_id/include_source to retrieve current evidence.",
            },
            ensure_ascii=False,
        )


def clip(value, limit=1200):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "… [truncated]"
    if isinstance(value, list):
        return [clip(v, limit) for v in value[:8]] + (
            [{"omitted": len(value) - 8}] if len(value) > 8 else []
        )
    if isinstance(value, dict):
        return {
            k: clip(
                v,
                300
                if k in {"source", "spec", "instruction", "logs", "summary"}
                else limit,
            )
            for k, v in value.items()
            if k not in {"brain", "snapshot", "input"}
        }
    return value


def version_summary(version):
    value = {k: getattr(version, k) for k in ("id", "state", "created_at")}
    test = version.test_result
    if test:
        value["test_result"] = {
            k: clip(test[k])
            for k in (
                "success",
                "error",
                "error_type",
                "phase",
                "executed",
                "credential",
                "remediation",
                "source_sha256",
            )
            if k in test
        }
    return value


def skill_context(
    engine, skill_id=None, include_source=False, version_id=None, offset=0
):
    with engine.db.transaction() as db:
        query = select(Skill).where(Skill.deleted_at.is_(None)).order_by(Skill.name)
        if skill_id:
            query = query.where(Skill.id == skill_id)
        result = []
        for skill in db.scalars(query):
            value = {
                k: getattr(skill, k) for k in ("id", "name", "description", "enabled")
            }
            if skill_id:
                value.update(instruction=skill.instruction, spec=skill.spec)
            value["tools"] = []
            for tool in db.scalars(select(Tool).where(Tool.skill_id == skill.id)):
                versions = list(
                    db.scalars(
                        select(ToolVersion)
                        .options(
                            load_only(
                                ToolVersion.id,
                                ToolVersion.state,
                                ToolVersion.created_at,
                                ToolVersion.test_result,
                            )
                        )
                        .where(ToolVersion.tool_id == tool.id)
                        .order_by(ToolVersion.created_at.desc())
                    )
                )
                detail = {
                    **row_dict(tool),
                    "version_count": len(versions),
                    "versions": [version_summary(v) for v in versions[:5]],
                }
                selected = (
                    next((v for v in versions if v.id == version_id), None)
                    if version_id
                    else next(iter(versions), None)
                )
                if skill_id and selected:
                    detail.update(
                        parameters=selected.parameters,
                        secrets=selected.secrets,
                        dependencies=selected.dependencies,
                    )
                    if include_source:
                        detail.update(
                            source=selected.source[offset : offset + 24000],
                            source_version_id=selected.id,
                            source_next_offset=offset + 24000
                            if len(selected.source) > offset + 24000
                            else None,
                        )
                value["tools"].append(detail)
            result.append(value)
        files = [
            v for v in engine.skills.file_skills if not skill_id or v["id"] == skill_id
        ]
        result.extend(
            files
            if skill_id
            else [
                {k: v.get(k) for k in ("id", "name", "description", "enabled")}
                for v in files
            ]
        )
        if skill_id:
            if not result:
                raise ValueError("Skill not found; use the exact returned UUID")
            if version_id and not db.scalar(
                select(ToolVersion.id)
                .join(Tool)
                .where(ToolVersion.id == version_id, Tool.skill_id == skill_id)
            ):
                raise ValueError("Version does not belong to this skill")
            return result[0]
        return result


def job_summary_query():
    # Extract only the parent reference in SQL, never materialize job snapshots,
    # conversation state or the builder's full input just to display metadata.
    return select(
        Job.id,
        Job.session_id,
        Job.prompt,
        Job.status,
        Job.error,
        Job.created_at,
        Job.started_at,
        Job.finished_at,
        Job.input["parent_job_id"].as_string().label("parent_job_id"),
    )


def execution_list(engine, limit=20, before=None):
    with engine.db.transaction() as db:
        query = job_summary_query().order_by(Job.created_at.desc())
        if before is not None:
            query = query.where(Job.created_at < before)
        rows = list(db.execute(query.limit(limit + 1)))
        return {
            "executions": [
                {
                    "id": j.id,
                    "session_id": j.session_id,
                    "prompt": clip(j.prompt, 240),
                    "status": j.status,
                    "error": clip(j.error),
                    "created_at": j.created_at,
                    "parent_job_id": j.parent_job_id,
                }
                for j in rows[:limit]
            ],
            "next_before": rows[limit - 1].created_at if len(rows) > limit else None,
        }


def execution_details(
    engine, job_id, before_event_id=None, limit=20, include_logs=False
):
    with engine.db.transaction() as db:
        job = db.execute(job_summary_query().where(Job.id == job_id)).first()
        if not job:
            raise ValueError("Execution not found")
        kinds = [
            "tool_begin",
            "tool_end",
            "decision",
            "failed",
            "interrupted",
            "cancelled",
            "succeeded",
            "build_started",
            "execution_report",
        ]
        if include_logs:
            kinds.append("progress")
        query = (
            select(Event)
            .where(Event.job_id == job_id, Event.type.in_(kinds))
            .order_by(Event.id.desc())
        )
        if before_event_id is not None:
            query = query.where(Event.id < before_event_id)
        rows = list(db.scalars(query.limit(limit + 1)))
        messages = list(
            db.scalars(
                select(Message)
                .where(Message.job_id == job_id)
                .order_by(Message.created_at.desc())
                .limit(6)
            )
        )
        return {
            "job": {
                "id": job.id,
                "session_id": job.session_id,
                "status": job.status,
                "error": clip(job.error),
                "prompt": clip(job.prompt),
                "parent_job_id": job.parent_job_id,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            },
            "messages": [clip(row_dict(m)) for m in reversed(messages)],
            "events": [clip(row_dict(e)) for e in reversed(rows[:limit])],
            "next_before_event_id": rows[limit - 1].id if len(rows) > limit else None,
            "note": "Recent significant events only; stream fragments omitted. Use next_before_event_id for older events and get_skill(include_source=true) for code.",
        }
