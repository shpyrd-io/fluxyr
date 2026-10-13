"""Paged read projection of the durable event log; never rewrite stored events."""

from sqlalchemy import func, select

from .database import row_dict
from .models import Event, Message, Session
from .persistence import restore_tool_identity

PAGE_SIZE = 1200
HIDDEN = {
    "activity",
    "usage",
    "model_start",
    "model_end",
    "memory_extracted",
    "memory_updated",
    "substream_start",
    "substream_end",
    "tool_stream_open",
    "tool_stream_close",
}


def compact_event(event):
    event = dict(event)
    p = dict(restore_tool_identity(event["type"], event["payload"]))
    if event["type"] in {"tool_begin", "tool_end"}:
        p.pop("args", None)
        result = p.get("result")
        if isinstance(result, dict):
            p["result"] = {
                k: result[k]
                for k in (
                    "success",
                    "passed",
                    "version_id",
                    "__human__",
                    "phase",
                    "executed",
                    "exit_code",
                    "error_type",
                    "type",
                )
                if k in result
            }
            if result.get("error") or result.get("isError"):
                p["result"]["error"] = str(
                    result.get("error") or "Browser tool failed"
                )[:500]
        else:
            p.pop("result", None)
        event["details_url"] = f"/events/{event['id']}"
    elif event["type"] == "execution_report":
        p = {
            k: v
            for k, v in p.items()
            if k in {"job_id", "session_id", "status", "prompt"}
        }
        if isinstance(p.get("prompt"), str):
            p["prompt"] = p["prompt"][:200]
    event["payload"] = p
    return event


def compact_events(events):
    result, blocks, legacy = [], {}, {}
    for event in events:
        kind, p, job = event["type"], event["payload"], event["job_id"]
        if kind in HIDDEN:
            continue
        if kind in {"delta", "reasoning"}:
            block = p.get("block_id") or legacy.setdefault(job, event["id"])
            key = (job, block, kind)
            if key in blocks:
                blocks[key]["payload"]["text"] += p.get("text", "")
                if "text_end" in p:
                    blocks[key]["payload"]["text_end"] = p["text_end"]
                continue
            event = {**event, "payload": {**p, "text": p.get("text", "")}}
            blocks[key] = event
        elif kind in {
            "tool_begin",
            "tool_end",
            "succeeded",
            "failed",
            "interrupted",
            "cancelled",
        }:
            legacy.pop(job, None)
        result.append(
            compact_event(event) if kind not in {"delta", "reasoning"} else event
        )
    return result


def history_page(db, sid, *, before=None, after=None):
    if before is not None and after is not None:
        raise ValueError("Use before or after, not both")
    if any(v is not None and v < 0 for v in (before, after)):
        raise ValueError("Invalid history cursor")
    with db.transaction() as s:
        session = (
            s.execute(
                select(Session.id, Session.title, Session.kind, Session.status).where(
                    Session.id == sid
                )
            )
            .mappings()
            .first()
        )
        if not session:
            raise ValueError("Session not found")
        cursor = (
            s.scalar(select(func.max(Event.id)).where(Event.session_id == sid)) or 0
        )
        reset = after is not None and after > cursor
        if reset:
            after = None
        query = select(Event).where(
            Event.session_id == sid, Event.id <= cursor, Event.type.not_in(HIDDEN)
        )
        upper = None
        if before is not None:
            boundary = s.scalar(
                select(Event.created_at).where(
                    Event.session_id == sid, Event.id == before
                )
            )
            if not boundary:
                raise ValueError("Invalid history cursor")
            upper = boundary
            query = query.where(Event.id < before)
        if after is not None:
            query = query.where(Event.id > after)
        # Catch-up is also bounded. A busy client drains pages before opening SSE.
        rows = list(
            s.scalars(
                query.order_by(
                    Event.id.asc() if after is not None else Event.id.desc()
                ).limit(PAGE_SIZE + 1)
            )
        )
        more = len(rows) > PAGE_SIZE
        rows = rows[:PAGE_SIZE]
        rows.sort(key=lambda x: x.id)
        lower_id = after if after is not None else (rows[0].id if more else 0)
        # Include boundary messages: Message and queued Event are separate rows in
        # the same transaction. Client merges overlapping messages by their ID.
        previous = (
            s.scalar(
                select(Event.created_at)
                .where(Event.session_id == sid, Event.id < lower_id)
                .order_by(Event.id.desc())
                .limit(1)
            )
            if lower_id
            else None
        )
        if after is not None and more:
            cursor = rows[-1].id
            upper = rows[-1].created_at
        messages = select(Message).where(Message.session_id == sid)
        if previous is not None:
            messages = messages.where(Message.created_at >= previous)
        if upper is not None:
            messages = messages.where(Message.created_at <= upper)
        return {
            "session": dict(session),
            "events": compact_events(row_dict(row) for row in rows),
            "messages": [
                row_dict(m) for m in s.scalars(messages.order_by(Message.created_at))
            ],
            "event_cursor": cursor,
            "reset": reset,
            "before": rows[0].id if more and after is None else None,
            "has_more": more if after is not None else False,
            "range_start": after
            if after is not None
            else (rows[0].id - 1 if more else 0),
            "range_end": (before - 1) if before is not None else cursor,
        }
