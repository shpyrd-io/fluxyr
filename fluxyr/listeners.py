"""Supervised continuous routine triggers. Only the fenced engine starts listeners."""

import ast
import fcntl
import json
import logging
import os
import selectors
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from packaging.requirements import Requirement
from sqlalchemy import delete, select, text

from .database import row_dict
from .models import (
    ListenerConfig,
    ListenerRun,
    ListenerVersion,
    NormalizerVersion,
    ReactiveConfig,
    Routine,
)
from .reactive import MAX_BYTES, validate_output

log = logging.getLogger(__name__)
MAX_LISTENERS = 8
MAX_FAILURES = 5
MAX_LOG = 16_384
SCAN_SECONDS = 5


def bounded_json(value, maximum=16_384):
    if len(json.dumps(value, allow_nan=False).encode()) > maximum:
        raise ValueError(f"JSON exceeds {maximum} bytes")
    return value


class Listeners:
    def __init__(self, engine):
        self.engine, self.db = engine, engine.db
        self.handles = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.stopping = threading.Event()

    def config(self, s, rid):
        routine = s.get(Routine, rid, with_for_update=True)
        if not routine or routine.trigger != "worker":
            raise ValueError("Worker routine not found")
        cfg = s.get(ListenerConfig, rid)
        if not cfg:
            cfg = ListenerConfig(routine_id=rid)
            s.add(cfg)
            s.flush()
        return cfg

    def inspect(self, rid):
        with self.db.transaction() as s:
            cfg = self.config(s, rid)
            versions = s.execute(
                select(
                    ListenerVersion.id,
                    ListenerVersion.created_at,
                    ListenerVersion.tested_at,
                    ListenerVersion.dependencies,
                    ListenerVersion.secrets,
                )
                .where(ListenerVersion.routine_id == rid)
                .order_by(ListenerVersion.created_at.desc())
                .limit(50)
            ).mappings()
            runs = s.scalars(
                select(ListenerRun)
                .where(ListenerRun.routine_id == rid)
                .order_by(ListenerRun.started_at.desc())
                .limit(10)
            )
            return {
                **row_dict(cfg),
                "versions": [dict(v) for v in versions],
                "runs": [row_dict(r) for r in runs],
                "limit": MAX_LISTENERS,
            }

    def version(self, rid, vid):
        with self.db.transaction() as s:
            v = s.get(ListenerVersion, vid)
            if not v or v.routine_id != rid:
                raise ValueError("Listener version not found")
            return row_dict(v)

    def create_version(self, rid, source, dependencies=None, secrets=None):
        if not isinstance(source, str) or not 1 <= len(source.encode()) <= MAX_BYTES:
            raise ValueError("Python source must contain 1–262144 bytes")
        try:
            tree = ast.parse(source)
            compile(tree, "listener.py", "exec")
        except SyntaxError as exc:
            raise ValueError(f"Invalid Python: {exc}") from None
        if not any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"
            for n in tree.body
        ):
            raise ValueError("Define run(ctx) or async def run(ctx)")
        dependencies, secrets = dependencies or [], secrets or []
        for values in (dependencies, secrets):
            if (
                not isinstance(values, list)
                or len(values) > 20
                or any(
                    not isinstance(v, str) or not v.strip() or len(v) > 255
                    for v in values
                )
            ):
                raise ValueError(
                    "Dependencies and secrets must contain at most 20 non-empty strings, up to 255 characters"
                )
        for dep in dependencies:
            if Requirement(dep).url:
                raise ValueError("Dependencies must use package index requirements")
        with self.db.transaction() as s:
            self.config(s, rid)
            version = ListenerVersion(
                routine_id=rid,
                source=source,
                dependencies=dependencies,
                secrets=secrets,
            )
            s.add(version)
            s.flush()
            return row_dict(version)

    def configure(self, rid, values):
        if not isinstance(values, dict) or set(values) - {
            "version_id",
            "mode",
            "restart",
        }:
            raise ValueError("Configure version_id, mode and/or restart")
        with self.db.transaction() as s:
            cfg = self.config(s, rid)
            _, reactive = self.engine.reactive.config(s, rid)
            vid = values.get("version_id", cfg.version_id)
            mode = values.get("mode", reactive.mode)
            if mode not in {"collecting", "active", "disabled"}:
                raise ValueError("Mode must be collecting, active or disabled")
            if vid:
                v = s.get(ListenerVersion, vid)
                if not v or v.routine_id != rid:
                    raise ValueError("Listener version not found")
                if mode == "active" and not v.tested_at:
                    raise ValueError("Test this listener version before activation")
            elif mode == "active":
                raise ValueError("Select a tested listener version")
            if mode == "active" and reactive.normalizer_id:
                normalizer = s.get(NormalizerVersion, reactive.normalizer_id)
                if not normalizer or not normalizer.tested_at:
                    raise ValueError("Test the selected normalizer before activation")
            if vid != cfg.version_id or values.get("restart") or mode != reactive.mode:
                cfg.revision += 1
                cfg.failures, cfg.retry_at = 0, 0
                cfg.status = "stopped" if mode == "disabled" else "pending"
            cfg.version_id = vid
            reactive.mode = mode
            s.get(Routine, rid).enabled = mode != "disabled"
        self.wake.set()
        return self.inspect(rid)

    def test(self, rid, vid, seconds=5):
        if type(seconds) not in (int, float) or not 1 <= seconds <= 30:
            raise ValueError(
                "Test duration must be 1–30 seconds (after dependency setup)"
            )
        version = self.version(rid, vid)
        with self.lock:
            if rid in self.handles:
                raise ValueError(
                    "Stop the listener before testing; duplicate connections are not allowed"
                )
            if len(self.handles) >= MAX_LISTENERS:
                raise ValueError("Listener capacity reached")
            h = ListenerProcess(self, version, -1, seconds)
            self.handles[rid] = h
        try:
            h.run()
            return h.result
        finally:
            with self.lock:
                self.handles.pop(rid, None)

    def start(self):
        self.stopping = threading.Event()
        # The engine already owns the database fence; prior process history is stale.
        with self.db.transaction() as s:
            for run in s.scalars(
                select(ListenerRun).where(ListenerRun.finished_at.is_(None))
            ):
                run.status, run.finished_at = "interrupted", time.time()
            for cfg in s.scalars(select(ListenerConfig)):
                cfg.status = "stopped"
        self.thread = threading.Thread(
            target=self._supervise, name="routine-listeners", daemon=True
        )
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        with self.lock:
            handles = list(self.handles.values())
        for h in handles:
            h.stop.set()
        if self.thread:
            self.thread.join()
            self.thread = None
        for h in handles:
            h.completed.wait()

    def _supervise(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Listener supervisor iteration failed")
                # Do not keep orphan connections delivering while ownership/config is unknown.
                with self.lock:
                    for h in self.handles.values():
                        h.stop.set()
            self.wake.wait(SCAN_SECONDS)
            self.wake.clear()
        with self.lock:
            handles = list(self.handles.values())
        for h in handles:
            h.stop.set()
        for h in handles:
            if h.thread:
                h.thread.join()

    def tick(self):
        # One small query per five seconds, independent of connected listeners.
        with self.db.transaction() as s:
            rows = (
                s.execute(
                    select(
                        ListenerConfig.routine_id,
                        ListenerConfig.version_id,
                        ListenerConfig.revision,
                        ListenerConfig.failures,
                        ListenerConfig.retry_at,
                    )
                    .join(Routine, Routine.id == ListenerConfig.routine_id)
                    .join(ReactiveConfig, ReactiveConfig.routine_id == Routine.id)
                    .where(
                        Routine.enabled == True,
                        ReactiveConfig.mode != "disabled",
                        ListenerConfig.version_id.is_not(None),
                    )
                )
                .mappings()
                .all()
            )
        desired = {r["routine_id"]: r for r in rows}
        with self.lock:
            finished = set()
            for rid, h in list(self.handles.items()):
                if h.test_seconds:
                    continue
                cfg = desired.get(rid)
                if not cfg or cfg["revision"] != h.revision:
                    h.stop.set()
                if h.thread and not h.thread.is_alive():
                    del self.handles[rid]
                    finished.add(rid)
            for rid, cfg in desired.items():
                if (
                    rid in self.handles
                    or rid in finished
                    or cfg["failures"] >= MAX_FAILURES
                    or cfg["retry_at"] > time.time()
                    or len(self.handles) >= MAX_LISTENERS
                ):
                    continue
                h = ListenerProcess(
                    self, self.version(rid, cfg["version_id"]), cfg["revision"]
                )
                self.handles[rid] = h
                h.thread = threading.Thread(
                    target=h.run, name="listener-" + rid[:8], daemon=True
                )
                h.thread.start()


class ListenerProcess:
    def __init__(self, manager, version, revision, test_seconds=None):
        self.manager, self.engine, self.version = manager, manager.engine, version
        self.rid, self.revision, self.test_seconds = (
            version["routine_id"],
            revision,
            test_seconds,
        )
        self.stop = threading.Event()
        self.thread = None
        self.logs, self.redactions, self.events = "", [], 0
        self.ready = False
        self.ready_at = None
        self.completed = threading.Event()
        self.log_buffer = b""
        self.dropping_log = False
        self.result = None
        self.next_rpc = 0

    def redact(self, text):
        for value in sorted(self.redactions, key=len, reverse=True):
            text = text.replace(value, "[secret]")
        return text

    def capture(self, text):
        self.logs = (self.logs + self.redact(str(text)))[-MAX_LOG:]

    def capture_bytes(self, data):
        self.log_buffer += data
        while b"\n" in self.log_buffer:
            line, self.log_buffer = self.log_buffer.split(b"\n", 1)
            if not self.dropping_log:
                self.capture(line.decode("utf-8", errors="replace") + "\n")
            self.dropping_log = False
        if len(self.log_buffer) > MAX_LOG:
            self.log_buffer = b""
            self.dropping_log = True
            self.capture("[Long log line omitted]\n")

    def stopped(self):
        return (
            self.stop.is_set()
            or self.manager.stopping.is_set()
            or self.engine.stopping.is_set()
        )

    def rpc(self, message):
        # Backpressure on the local channel, rather than spinning or growing a queue.
        self.stop.wait(max(0, self.next_rpc - time.monotonic()))
        self.next_rpc = time.monotonic() + 0.05
        op = message.get("op")
        if op == "ready":
            self.ready = True
            self.ready_at = self.ready_at or time.monotonic()
            return True
        if op == "secret":
            name = message.get("name")
            if name not in self.version["secrets"]:
                raise ValueError("Secret was not declared by this listener")
            try:
                value = self.engine.vault.resolve(name)
            except Exception:  # noqa: BLE001 - keep provider response and secrets private
                raise ValueError(
                    "Vault resolution failed; check the declared credential in Vault"
                ) from None

            def collect(v):
                if isinstance(v, dict):
                    for item in v.values():
                        collect(item)
                elif isinstance(v, str) and len(v) >= 4 and v not in self.redactions:
                    self.redactions.append(v)

            collect(value)
            return value
        checkpoint = message.get("checkpoint")
        if checkpoint is not None:
            bounded_json(checkpoint)
        if op in {"emit", "receive"}:
            event_id = message.get("event_id")
            if (
                not isinstance(event_id, str)
                or not event_id.strip()
                or not 1 <= len(event_id) <= 255
            ):
                raise ValueError("A stable event_id (1–255 characters) is required")
            normalized = None
            if op == "emit":
                normalized = validate_output(
                    {
                        "events": [
                            {
                                k: message.get(k)
                                for k in ("session_key", "event_id", "payload")
                            }
                        ]
                    }
                )
                envelope = {"json": message["payload"]}
            else:
                envelope = message.get("envelope")
            value = self.engine.reactive.receive(
                self.rid,
                envelope,
                event_id,
                normalized=normalized,
                checkpoint=checkpoint,
                collect=bool(self.test_seconds),
                listener_version_id=self.version["id"],
            )
            self.events += 1
            return value
        if op == "checkpoint":
            value = bounded_json(message.get("value"))
            if not self.test_seconds:
                with self.engine.db.transaction() as s:
                    self.manager.config(s, self.rid).checkpoint = value
            return True
        raise ValueError("Unknown listener operation")

    def run(self):
        process, parent, child, selector, work = None, None, None, None, None
        fence = None
        error = None
        run_id = None
        try:
            # Fence tests in HTTP processes against the real listener as well.
            if self.engine.db.sqlite:
                path = self.engine.settings.state / "listener-locks"
                path.mkdir(parents=True, exist_ok=True)
                fence = (path / self.rid).open("a")
                fcntl.flock(fence, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fence = self.engine.db.engine.connect()
                if not fence.scalar(
                    text(
                        "SELECT pg_try_advisory_lock(hashtext(current_schema()), hashtext(:id))"
                    ),
                    {"id": "listener:" + self.rid},
                ):
                    raise ValueError(
                        "This listener is already running in another process"
                    )
                fence.commit()
            with self.engine.db.transaction() as s:
                cfg = self.manager.config(s, self.rid)
                checkpoint = cfg.checkpoint
                record = ListenerRun(
                    routine_id=self.rid,
                    version_id=self.version["id"],
                    test=bool(self.test_seconds),
                )
                s.add(record)
                s.flush()
                run_id = record.id
                if not self.test_seconds:
                    cfg.status = "starting"
                old = list(
                    s.scalars(
                        select(ListenerRun.id)
                        .where(
                            ListenerRun.routine_id == self.rid,
                            ListenerRun.finished_at.is_not(None),
                        )
                        .order_by(ListenerRun.started_at.desc())
                        .offset(19)
                    )
                )
                if old:
                    s.execute(delete(ListenerRun).where(ListenerRun.id.in_(old)))
            python = self.engine.runner.environment(
                self.version["dependencies"],
                self.capture,
                self.version["id"],
                self.stopped,
            )
            if self.stopped():
                return
            work = self.engine.settings.runtime / "listeners" / run_id
            work.mkdir(parents=True)
            (work / "listener.py").write_text(self.version["source"])
            helper = Path(__file__).parent / "runtime" / "listener_helper.py"
            shutil.copyfile(helper, work / "host.py")
            parent, child = socket.socketpair()
            process = subprocess.Popen(
                self.engine.runner._command(
                    [str(python), "-u", str(work / "host.py"), str(child.fileno())],
                    [work],
                ),
                cwd=work,
                env=self.engine.runner._environment(work, python),
                pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            child.close()
            parent.sendall(
                (
                    json.dumps(
                        {
                            "routine_id": self.rid,
                            "test": bool(self.test_seconds),
                            "checkpoint": checkpoint,
                        }
                    )
                    + "\n"
                ).encode()
            )
            parent.settimeout(2)
            selector = selectors.DefaultSelector()
            selector.register(parent, selectors.EVENT_READ, "rpc")
            selector.register(process.stdout, selectors.EVENT_READ, "log")
            buffer = b""
            deadline = (
                time.monotonic() + self.test_seconds if self.test_seconds else None
            )
            startup_deadline = time.monotonic() + 60
            fence_ping = time.monotonic()
            flush_at, last_state, termination = 0, None, None
            while process.poll() is None:
                now = time.monotonic()
                if not self.ready and now > startup_deadline:
                    raise ValueError("Listener did not become ready within 60 seconds")
                if not self.engine.db.sqlite and now - fence_ping >= 5:
                    # A lost connection releases the advisory lock. Never reconnect
                    # it and continue the old subprocess under uncertain ownership.
                    fence.execute(text("SELECT 1"))
                    fence.commit()
                    fence_ping = now
                if self.stopped() or (deadline and now >= deadline):
                    if termination is None:
                        termination = now
                        os.killpg(process.pid, signal.SIGTERM)
                    elif now - termination >= 2:
                        os.killpg(process.pid, signal.SIGKILL)
                for key, _ in selector.select(0.25):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "log":
                        self.capture_bytes(data)
                        continue
                    buffer += data
                    if len(buffer) > 300_000:
                        raise ValueError("Listener RPC exceeds 300 KB")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        try:
                            if termination is not None:
                                raise ValueError("Listener is stopping")
                            reply = {"value": self.rpc(json.loads(line))}
                        except Exception as exc:  # noqa: BLE001 - return bounded RPC errors
                            reply = {"error": self.redact(str(exc))[:1000]}
                        encoded = (json.dumps(reply, allow_nan=False) + "\n").encode()
                        if len(encoded) > 300_000:
                            encoded = b'{"error":"Reply exceeds 300 KB"}\n'
                        parent.sendall(encoded)
                state = (self.ready, self.events, self.logs)
                if state != last_state and now - flush_at >= 5:
                    with self.engine.db.transaction() as s:
                        record = s.get(ListenerRun, run_id)
                        if not record:
                            self.stop.set()
                        else:
                            record.logs, record.events = self.logs, self.events
                            record.status = "listening" if self.ready else "starting"
                            cfg = s.get(ListenerConfig, self.rid)
                            if cfg and not self.test_seconds:
                                cfg.status = record.status
                    last_state, flush_at = state, now
            if not termination and not self.stopped():
                error = f"Listener exited unexpectedly (code {process.returncode}); run(ctx) must stay alive"
            elif self.test_seconds and not self.ready and not self.stopped():
                error = "Listener did not call ctx.ready() during the test"
        except Exception as exc:  # noqa: BLE001 - record listener failures
            error = self.redact(f"{type(exc).__name__}: {exc}")[:2000]
        finally:
            if process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                # Preserve fast startup tracebacks without waiting for descendants.
                os.set_blocking(process.stdout.fileno(), False)
                for _ in range(32):
                    try:
                        data = os.read(process.stdout.fileno(), 8192)
                    except BlockingIOError:
                        break
                    if not data:
                        break
                    self.capture_bytes(data)
                if self.log_buffer and not self.dropping_log:
                    self.capture(self.log_buffer.decode("utf-8", errors="replace"))
                process.stdout.close()
            if selector:
                selector.close()
            for sock in (parent, child):
                if sock:
                    sock.close()
            if work:
                shutil.rmtree(work, ignore_errors=True)
            if fence:
                if not self.engine.db.sqlite:
                    try:
                        fence.execute(
                            text(
                                "SELECT pg_advisory_unlock(hashtext(current_schema()), hashtext(:id))"
                            ),
                            {"id": "listener:" + self.rid},
                        )
                        fence.commit()
                    except Exception:  # noqa: BLE001 - a failed fence must never return to the pool
                        fence.invalidate()
                fence.close()
            success = bool(
                self.test_seconds and self.ready and not error and not self.stopped()
            )
            status = "succeeded" if success else "failed" if error else "stopped"
            self.result = {
                "id": run_id,
                "status": status,
                "error": error,
                "events": self.events,
                "logs": self.logs,
            }
            if run_id:
                try:
                    with self.engine.db.transaction() as s:
                        record = s.get(ListenerRun, run_id)
                        if record:
                            record.status, record.error = status, error
                            record.logs, record.events, record.finished_at = (
                                self.logs,
                                self.events,
                                time.time(),
                            )
                            if success:
                                s.get(
                                    ListenerVersion, self.version["id"]
                                ).tested_at = time.time()
                        cfg = s.get(ListenerConfig, self.rid)
                        if (
                            cfg
                            and not self.test_seconds
                            and cfg.revision == self.revision
                        ):
                            if error:
                                cfg.failures = (
                                    0
                                    if self.ready_at
                                    and time.monotonic() - self.ready_at >= 60
                                    else cfg.failures
                                ) + 1
                                cfg.retry_at = time.time() + min(300, 2**cfg.failures)
                                cfg.status = (
                                    "failed"
                                    if cfg.failures >= MAX_FAILURES
                                    else "backoff"
                                )
                            else:
                                cfg.status = "stopped"
                except Exception:
                    log.exception("Could not save listener outcome")
            self.completed.set()
            self.manager.wake.set()
