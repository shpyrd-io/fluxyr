"""Copied into a listener's virtualenv workspace; no Fluxyr import required."""

import asyncio
import inspect
import json
import os
import runpy
import signal
import socket
import sys
import threading

MAX_FRAME = 300_000


class Context:
    def __init__(self, stream, initial):
        self._stream = stream
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.routine_id = initial["routine_id"]
        self.test = initial["test"]
        self.checkpoint = initial.get("checkpoint")

    @property
    def stopping(self):
        return self._stop.is_set()

    def wait(self, seconds):
        """Interruptible wait. Returns True when stopping."""
        if seconds < 0.01:
            raise ValueError("Wait at least 0.01 seconds; do not busy-poll")
        return self._stop.wait(seconds)

    def _call(self, operation, **values):
        data = (
            json.dumps({"op": operation, **values}, allow_nan=False) + "\n"
        ).encode()
        if len(data) > MAX_FRAME:
            raise ValueError("Listener message exceeds 300 KB")
        with self._lock:
            self._stream.write(data)
            self._stream.flush()
            raw = self._stream.readline(MAX_FRAME + 1)
            if not raw or len(raw) > MAX_FRAME:
                raise RuntimeError("Listener host disconnected")
            reply = json.loads(raw)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply.get("value")

    def ready(self):
        """Call after establishing the connection; required by the smoke test."""
        return self._call("ready")

    def emit(self, *, session_key, event_id, payload, checkpoint=None):
        """Acknowledge only after durable storage; does not wait for the agent."""
        result = self._call(
            "emit",
            session_key=session_key,
            event_id=event_id,
            payload=payload,
            checkpoint=checkpoint,
        )
        if checkpoint is not None:
            self.checkpoint = checkpoint
        return result

    def receive(self, envelope, *, event_id, checkpoint=None):
        """Collect a raw envelope or pass it through the selected normalizer."""
        result = self._call(
            "receive", envelope=envelope, event_id=event_id, checkpoint=checkpoint
        )
        if checkpoint is not None:
            self.checkpoint = checkpoint
        return result

    def save_checkpoint(self, value):
        result = self._call("checkpoint", value=value)
        self.checkpoint = value
        return result

    def secret(self, name):
        return self._call("secret", name=name)


if __name__ == "__main__":
    channel = socket.socket(fileno=int(sys.argv[1]))
    stream = channel.makefile("rwb")
    ctx = Context(stream, json.loads(stream.readline(MAX_FRAME)))
    parent_pid = os.getppid()

    def watch_parent():
        while not ctx._stop.wait(1):
            if os.getppid() != parent_pid:
                os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: ctx._stop.set())
    signal.signal(signal.SIGINT, lambda *_: ctx._stop.set())
    # Die when the supervisor disappears on Linux (including container OOM).
    if sys.platform == "linux":
        import ctypes

        parent = os.getppid()
        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() != parent:
            sys.exit(1)
    namespace = runpy.run_path("listener.py", run_name="fluxyr_listener")
    result = namespace["run"](ctx)
    if inspect.isawaitable(result):
        asyncio.run(result)
