"""Bound provider I/O and stop waiting without dispatching a late response."""

import queue
import threading


class ThreadGroup:
    """Track detached provider work so recovery cannot overlap generations."""

    def __init__(self):
        self.condition = threading.Condition()
        self.active = 0

    def start(self, target, name):
        def run():
            try:
                target()
            finally:
                with self.condition:
                    self.active -= 1
                    self.condition.notify_all()

        with self.condition:
            self.active += 1
        try:
            threading.Thread(target=run, daemon=True, name=name).start()
        except BaseException:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()
            raise

    def join(self):
        with self.condition:
            self.condition.wait_for(lambda: self.active == 0)


class ModelInterrupted(RuntimeError):
    pass


class InterruptibleAdapter:
    def __init__(self, adapter, control, slots, emit=None, tasks=None):
        self.adapter, self.control, self.slots = adapter, control, slots
        self.emit = emit or (lambda *_: None)
        self.tasks = tasks or ThreadGroup()

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def execute_step(self, *args, **kwargs):
        return self._call("execute_step", args, kwargs)

    def execute_step_with_usage(self, *args, **kwargs):
        return self._call("execute_step_with_usage", args, kwargs)

    def _call(self, method, args, kwargs):
        stopped = threading.Event()
        closing = threading.Lock()

        def check():
            if stopped.is_set() or self.control():
                with closing:
                    if not stopped.is_set():
                        stopped.set()

                        def close_connection():
                            try:
                                client = getattr(self.adapter, "client", None)
                                if client:
                                    client.close()
                            except Exception:
                                pass

                        self.tasks.start(close_connection, "provider-close")
                raise ModelInterrupted("Model request interrupted")

        check()
        while not self.slots.acquire(timeout=0.1):
            check()
        result = queue.Queue(maxsize=1)
        callback = kwargs.get("stream_callback")
        if callback:

            def guarded(*a, **kw):
                check()
                return callback(*a, **kw)

            kwargs = {**kwargs, "stream_callback": guarded}

        def run():
            try:
                check()
                self.emit("model_start", {})
                outcome = (True, getattr(self.adapter, method)(*args, **kwargs))
            except BaseException as exc:
                outcome = (False, exc)
            finally:
                try:
                    self.emit("model_end", {})
                except BaseException as exc:
                    outcome = (False, exc)
                finally:
                    self.slots.release()
            result.put(outcome)

        try:
            self.tasks.start(run, "provider-request")
        except BaseException:
            self.slots.release()
            raise
        while True:
            check()
            try:
                ok, value = result.get(timeout=0.1)
            except queue.Empty:
                continue
            check()
            if not ok:
                raise value
            return value
