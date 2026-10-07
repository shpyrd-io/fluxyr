"""Provider-reported usage, persisted once per request; no price estimates."""

import math
import uuid

from sqlalchemy import select

from .models import Event, Job


def bind_usage(adapter, emit, context=None):
    if not hasattr(adapter, "_fluxyr_original_usage_callback"):
        adapter._fluxyr_original_usage_callback = getattr(
            adapter, "_usage_callback", None
        )
    previous = adapter._fluxyr_original_usage_callback

    def record(raw):
        cost = raw.get("upstream_cost_micros")
        try:
            cost = float(cost) / 1_000_000 if cost is not None else None
            if cost is not None and (not math.isfinite(cost) or cost < 0):
                cost = None
        except (ValueError, TypeError):
            cost = None
        payload = {k: raw.get(k) for k in ("provider", "model", "request_id")}
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            payload[key] = max(0, int(raw.get(key) or 0))
        payload.update(cost_usd=cost, available=True, **(context() if context else {}))
        emit("usage", payload)
        if previous:
            previous(raw)

    adapter._usage_callback = record
    return adapter


def usage_report(db):
    with db.transaction() as s:
        # Older TechDoc requests recorded their scope but not the owning tool call.
        # Recover only unambiguous associations; never assign a shared parent cost.
        candidates = {}
        for jid, parent, tool in s.execute(
            select(
                Event.job_id,
                Event.payload["model_call_id"].as_string(),
                Event.payload["tool_call_id"].as_string(),
            ).where(
                Event.type == "tool_begin",
                Event.payload["tool_name"].as_string() == "tech_doc",
            )
        ):
            if parent and tool:
                candidates.setdefault((jid, parent), set()).add(tool)
        legacy_tools = {}
        for jid, scope, parent in s.execute(
            select(
                Event.job_id,
                Event.payload["activity_scope"].as_string(),
                Event.payload["model_call_id"].as_string(),
            ).where(
                Event.type == "substream_start",
                Event.payload["tool_name"].as_string() == "tech_doc",
            )
        ):
            matches = candidates.get((jid, parent), set())
            if scope and len(matches) == 1:
                legacy_tools[(jid, scope)] = next(iter(matches))
        events = s.execute(
            select(Event.id, Event.job_id, Event.payload, Event.created_at)
            .where(Event.type == "usage")
            .order_by(Event.id)
            .execution_options(yield_per=256)
        )
        parents = {
            jid: parent or builder_parent
            for jid, parent, builder_parent in s.execute(
                select(
                    Job.id,
                    Job.input["parent_job_id"].as_string(),
                    Job.input["builder"]["parent_job_id"].as_string(),
                ).execution_options(yield_per=256)
            )
        }

        def empty():
            return {
                "tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "cost_usd": 0,
                "requests": 0,
                "missing_cost": 0,
                "missing_tokens": 0,
            }

        total, by_job, by_call, by_tool, seen = empty(), {}, {}, {}, set()
        since = None
        for event in events:
            if since is None:
                since = event.created_at
            p = event.payload
            call = p.get("model_call_id") or str(event.id)
            key = (p.get("provider"), p.get("request_id") or call)
            if key in seen:
                continue
            seen.add(key)
            targets = [total, by_call.setdefault(call, empty())]
            tool = p.get("tool_call_id")
            if not tool and p.get("source") == "techdoc":
                tool = legacy_tools.get((event.job_id, p.get("activity_scope")))
            if tool:
                targets.append(
                    by_tool.setdefault(event.job_id, {}).setdefault(tool, empty())
                )
            jid, visited = event.job_id, set()
            while jid and jid not in visited:
                visited.add(jid)
                targets.append(by_job.setdefault(jid, empty()))
                jid = parents.get(jid)
            inp = sum(
                p.get(k) or 0
                for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
            )
            out = p.get("output_tokens") or 0
            for dest in targets:
                dest["requests"] += 1
                dest["input_tokens"] += inp
                dest["output_tokens"] += out
                dest["cached_tokens"] += p.get("cache_read_tokens") or 0
                dest["tokens"] += inp + out
                dest["cost_usd"] += p.get("cost_usd") or 0
                dest["missing_cost"] += p.get("cost_usd") is None
                dest["missing_tokens"] += not p.get("available")
        return {
            "total": total,
            "by_job": by_job,
            "by_call": by_call,
            "by_tool": by_tool,
            "since": since,
        }


def standalone_context(source):
    return {"source": source, "model_call_id": str(uuid.uuid4())}
