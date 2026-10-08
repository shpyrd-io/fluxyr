"""Recover an embedded worker only after its previous generation has drained."""

import logging
import threading
import time

from sqlalchemy.exc import OperationalError

log = logging.getLogger(__name__)


class WorkerMonitor:
    retry_delays = (1, 5, 15)
    drain_timeout = 30
    stall_timeout = 30

    def __init__(self, engine):
        self.engine = engine
        self.shutdown = threading.Event()
        self.thread = None
        self.state = "stopped"
        self.failures = 0
        self.last_cycle = 0
        self.next_attempt = 0
        self.draining = None
        self.drain_started = 0
        self.database_unavailable = False
        self.last_tick = time.monotonic()

    def start(self):
        if self.state == "stopped":
            self.state = "starting"
        self.last_cycle = time.monotonic()
        self.thread = threading.Thread(
            target=self._loop, name="worker-monitor", daemon=True
        )
        self.thread.start()

    def healthy(self):
        # Called only after a fenced heartbeat AND a complete dispatch cycle.
        if (
            self.engine.stopping.is_set()
            or self.shutdown.is_set()
            or self.state == "failed"
        ):
            return
        self.last_cycle = time.monotonic()
        self.state = "running"
        self.failures = 0
        self.database_unavailable = False

    def failed(self, exc):
        if self.state == "failed":
            self.engine.stopping.set()
            return
        self.database_unavailable = isinstance(exc, OperationalError)
        self.state = "recovering"
        self.engine.stopping.set()

    def health(self):
        alive = bool(self.engine.thread and self.engine.thread.is_alive())
        ready = self.state == "running" and alive and not self.engine.stopping.is_set()
        monitor_alive = (
            self.thread is None or self.thread.is_alive() or self.shutdown.is_set()
        )
        responsive = (
            self.thread is None
            or time.monotonic() - self.last_tick < self.stall_timeout
        )
        return {
            "worker": ready and monitor_alive and responsive,
            "worker_state": self.state if responsive else "failed",
            "recovery_failures": self.failures,
            "live": self.state != "failed" and monitor_alive and responsive,
        }

    def _drain(self):
        try:
            self.engine._drain_generation()
        except BaseException:
            log.exception("Could not safely drain the previous worker generation")
            self.state = "failed"

    def _loop(self):
        while not self.shutdown.wait(0.25):
            try:
                self.last_tick = time.monotonic()
                self.tick()
            except Exception:
                log.exception("Worker recovery monitor failed")
                self.state = "failed"
                self.engine.stopping.set()

    def tick(self):
        if self.state == "failed" or self.shutdown.is_set():
            return
        e, now = self.engine, time.monotonic()
        if self.draining is None:
            if (
                e.thread
                and e.thread.is_alive()
                and not e.stopping.is_set()
                and now - self.last_cycle < self.stall_timeout
            ):
                return
            self.state = "recovering"
            e.stopping.set()
            self.drain_started = now
            self.draining = threading.Thread(
                target=self._drain, name="worker-drain", daemon=True
            )
            self.draining.start()
            self.next_attempt = now + self.retry_delays[min(self.failures, 2)]
            return
        if self.draining.is_alive():
            if now - self.drain_started >= self.drain_timeout:
                # Native Python tools cannot safely be killed as threads. Never
                # start a second generation over an uncooperative old one.
                self.state = "failed"
            return
        if now < self.next_attempt:
            return
        if not self.database_unavailable:
            if self.failures >= len(self.retry_delays):
                self.state = "failed"
                return
            self.failures += 1
        try:
            e._restart_generation()
        except OperationalError:
            # Restarting the container cannot repair an unavailable database.
            self.database_unavailable = True
            self.next_attempt = now + 30
        except Exception:
            log.exception("Worker recovery attempt failed")
            self.database_unavailable = False
            if self.failures >= len(self.retry_delays):
                self.state = "failed"
            else:
                self.next_attempt = now + self.retry_delays[min(self.failures, 2)]
        else:
            self.draining = None
            self.last_cycle = time.monotonic()
            # The new supervisor, not successful thread creation, marks health.
            if self.state != "running":
                self.state = "starting"
