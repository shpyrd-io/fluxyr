"""Best-effort live text for connected readers; never a persistence queue."""

import threading
from collections import deque
from contextlib import contextmanager


class LiveTextReader:
    max_bytes = 128 * 1024

    def __init__(self, ready):
        self.ready = ready
        self.lock = threading.Lock()
        self.queue = deque()
        self.size = 0

    def append(self, fragment):
        size = len(fragment["payload"]["text"].encode("utf-8")) + 512
        with self.lock:
            # Slow readers lose only speculative text. Durable replay fills
            # gaps; absolute offsets prevent joining text across a missing part.
            while self.queue and self.size + size > self.max_bytes:
                _, removed = self.queue.popleft()
                self.size -= removed
            if size <= self.max_bytes:
                self.queue.append((fragment, size))
                self.size += size
        self.ready.set()

    def drain(self):
        with self.lock:
            result = [fragment for fragment, _ in self.queue]
            self.queue.clear()
            self.size = 0
        return result


class LiveText:
    def __init__(self):
        self.lock = threading.Lock()
        self.readers = {}

    @contextmanager
    def subscribe(self, session_id, ready, *, enabled=True):
        reader = LiveTextReader(ready)
        if enabled:
            with self.lock:
                self.readers.setdefault(session_id, set()).add(reader)
        try:
            yield reader
        finally:
            if enabled:
                with self.lock:
                    readers = self.readers[session_id]
                    readers.remove(reader)
                    if not readers:
                        del self.readers[session_id]

    def publish(self, session_id, job_id, kind, payload):
        if kind not in {"delta", "reasoning"} or not payload.get("block_id"):
            return
        # Only displayable text crosses this path, never tool arguments/secrets.
        fragment = {
            "session_id": session_id,
            "job_id": job_id,
            "type": kind,
            "payload": {
                k: payload[k] for k in ("block_id", "text", "text_offset", "text_end")
            },
        }
        with self.lock:
            for sid in {session_id, None}:
                for reader in self.readers.get(sid, ()):
                    reader.append(fragment)
