"""Process-local wakeups; the database remains the source of event contents."""

import threading
from contextlib import contextmanager


class EventWakeup(threading.Event):
    def __init__(self):
        super().__init__()
        self.revision = 0


class EventSignal:
    # Also recovers writes from a separate process without busy-polling the DB.
    fallback_seconds = 10

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = {}

    @contextmanager
    def subscribe(self, session_id):
        ready = EventWakeup()
        with self._lock:
            self._subscribers.setdefault(session_id, set()).add(ready)
        try:
            yield ready
        finally:
            with self._lock:
                listeners = self._subscribers[session_id]
                listeners.remove(ready)
                if not listeners:
                    del self._subscribers[session_id]

    def publish(self, session_ids):
        with self._lock:
            for session_id in {*session_ids, None}:
                for ready in self._subscribers.get(session_id, ()):
                    ready.revision += 1
                    ready.set()
