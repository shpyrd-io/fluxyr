"""Persist lightweight live activity and mirror it to the calling conversations."""

import threading
import time
import uuid

from .models import Job


class ActivityEmitter:
    def __init__(self, store, job, interval=0.25):
        self.store, self.job, self.interval = store, job, interval
        self.lock = threading.RLock()
        self.pending = {}
        self.last = {}
        self.ancestors = []
        self.model_call_id = None
        self.usage_seen = False
        seen = {job["id"]}
        parent = job["input"].get("parent_job_id") or job["input"].get(
            "builder", {}
        ).get("parent_job_id")
        with store.db.transaction() as s:
            while parent and parent not in seen:
                seen.add(parent)
                row = s.get(Job, parent)
                if not row:
                    break
                self.ancestors.append((row.session_id, row.id))
                parent = row.input.get("parent_job_id") or row.input.get(
                    "builder", {}
                ).get("parent_job_id")

    def __call__(self, kind, payload):
        if kind == "model_start":
            self.model_call_id = str(uuid.uuid4())
            self.usage_seen = False
        if self.model_call_id and not payload.get("model_call_id"):
            payload = {**payload, "model_call_id": self.model_call_id}
        if kind == "usage" and payload.get("activity_scope", "main") == "main":
            self.usage_seen = True
        if kind == "model_end" and not self.usage_seen and self.model_call_id:
            self.store.emit(
                self.job["session_id"],
                self.job["id"],
                "usage",
                {
                    "model_call_id": self.model_call_id,
                    "available": False,
                    "cost_usd": None,
                },
            )
        # Argument contents can include source or credentials: keep counters only.
        if kind not in ("tool_argument_delta", "substream_delta"):
            self.store.emit(self.job["session_id"], self.job["id"], kind, payload)
        scope = payload.get("activity_scope", "main")
        with self.lock:
            if kind in ("delta", "reasoning", "tool_argument_delta", "substream_delta"):
                p = self.pending.setdefault(scope, {"chunks": 0, "characters": 0})
                p["chunks"] += payload.get("chunks", 1)
                p["characters"] += payload.get(
                    "characters", len(payload.get("text", ""))
                )
                p.update(
                    phase="reasoning"
                    if kind == "reasoning"
                    else "generating_arguments"
                    if kind == "tool_argument_delta"
                    else "streaming",
                    tool=payload.get("tool_name"),
                    substream=kind == "substream_delta",
                )
                if time.monotonic() - self.last.get(scope, 0) >= self.interval:
                    self.flush(scope)
            elif kind in (
                "model_start",
                "model_end",
                "stream_close",
                "tool_stream_open",
                "tool_stream_close",
                "tool_begin",
                "tool_end",
                "substream_start",
                "substream_end",
            ):
                self.flush(scope)
                phase = {
                    "model_start": "waiting_model",
                    "model_end": "model_finished",
                    "tool_stream_open": "generating_arguments",
                    "tool_begin": "tool",
                    "tool_end": "tool_finished",
                    "substream_start": "waiting_model",
                    "substream_end": "model_finished",
                }.get(kind)
                if phase:
                    self.publish(
                        scope,
                        {
                            "phase": phase,
                            "tool": payload.get("tool_name"),
                            "substream": kind.startswith("substream"),
                            "chunks": 0,
                            "characters": 0,
                        },
                    )

    def flush(self, scope):
        payload = self.pending.pop(scope, None)
        if payload:
            self.publish(scope, payload)
        self.last[scope] = time.monotonic()

    def publish(self, scope, payload):
        destinations = [(self.job["session_id"], self.job["id"]), *self.ancestors]
        sent = set()
        for depth, (sid, jid) in enumerate(destinations):
            if sid in sent:
                continue
            sent.add(sid)
            self.store.emit(
                sid,
                jid,
                "activity",
                {
                    **payload,
                    "origin_job_id": self.job["id"],
                    "origin_session_id": self.job["session_id"],
                    "scope": scope,
                    "depth": depth + int(payload.get("substream", False)),
                    "label": self.job["input"]
                    .get("builder", {})
                    .get("skill", {})
                    .get("name")
                    or self.job["prompt"][:70],
                },
            )
