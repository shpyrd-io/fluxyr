"""Bounded local Python execution. A virtualenv isolates packages, not the host."""

import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from packaging.requirements import Requirement

_locks_guard = threading.Lock()
_locks = {}


def lock_for(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class PythonRunner:
    def __init__(self, settings, vault):
        self.settings = settings
        self.vault = vault

    @staticmethod
    def _install(command, timeout, stop):
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        start = time.monotonic()
        try:
            while True:
                if stop():
                    raise RuntimeError("Dependency setup cancelled")
                if time.monotonic() - start > timeout:
                    raise RuntimeError("Dependency setup timed out")
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    if process.returncode:
                        raise ValueError("Dependency setup failed: " + stderr[-3000:])
                    return
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()

    def environment(
        self,
        dependencies,
        emit=lambda *_: None,
        version_id="default",
        stop=lambda: False,
    ):
        for requirement in dependencies:
            parsed = Requirement(requirement)
            if parsed.url:
                raise ValueError(
                    "Dependencies must use package index requirements, not arbitrary URLs"
                )
        digest = hashlib.sha256(
            json.dumps([version_id, sorted(dependencies)]).encode()
        ).hexdigest()[:24]
        directory = self.settings.runtime / "envs" / digest
        with lock_for(digest):
            if stop():
                raise RuntimeError("Dependency setup cancelled")
            python = directory / "bin" / "python"
            if not (directory / ".ready").exists():
                directory.parent.mkdir(exist_ok=True)
                emit("Installing Python dependencies…")
                self._install([sys.executable, "-m", "venv", str(directory)], 90, stop)
                if dependencies:
                    self._install(
                        [
                            str(python),
                            "-m",
                            "pip",
                            "install",
                            "--disable-pip-version-check",
                            *dependencies,
                        ],
                        300,
                        stop,
                    )
                (directory / ".ready").touch()
            return python

    def run(
        self,
        version,
        params,
        job_id,
        emit=lambda *_: None,
        stop=lambda: False,
        answer=None,
    ):
        logs = []

        def capture(line):
            logs.append(str(line))
            if len(logs) > 100:
                del logs[0]
            emit(line)

        try:
            result = self._run(version, params, job_id, capture, stop, answer)
            if result.get("waiting"):
                return {"done": False, "success": None, **result}
            return {"done": True, "success": "error" not in result, **result}
        except Exception as exc:
            return {
                "done": True,
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "logs": logs,
                "side_effects_possible": True,
            }

    def _run(
        self,
        version,
        params,
        job_id,
        emit=lambda *_: None,
        stop=lambda: False,
        answer=None,
    ):
        python = self.environment(
            version.get("dependencies", []), emit, version["id"], stop
        )
        # Different invocation directories prevent parallel tools overwriting one another.
        import uuid

        work = self.settings.workspace / job_id / str(uuid.uuid4())
        work.mkdir(parents=True)
        (work / "action.py").write_text(version["source"])
        shutil.copyfile(Path(__file__).with_name("helper.py"), work / "fluxyr.py")
        secret_names = version.get("secrets", [])
        resolved = {name: self.vault.resolve(name) for name in secret_names}
        redactions = []

        def collect(v):
            if isinstance(v, dict):
                for item in v.values():
                    collect(item)
            elif isinstance(v, str) and len(v) >= 4:
                redactions.append(v)

        collect(resolved)

        def redact(text):
            for value in sorted(redactions, key=len, reverse=True):
                text = text.replace(value, "[secret]")
            return text

        env = {
            "PATH": str(python.parent) + ":/usr/bin:/bin",
            "HOME": str(work),
            "LANG": "en_US.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "FLUXYR_DATA": str(self.settings.data),
        }
        # TLS roots are configuration, not credentials.
        for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            if os.getenv(key):
                env[key] = os.environ[key]
        process = subprocess.Popen(
            [str(python), "-u", str(work / "action.py")],
            cwd=work,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        def feed_input():
            try:
                process.stdin.write(
                    json.dumps(
                        {
                            "params": params,
                            "secrets": resolved,
                            "approval_response": answer,
                        }
                    )
                )
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        # A script that never reads stdin must still be cancellable/time-bounded.
        feeder = threading.Thread(target=feed_input, daemon=True)
        feeder.start()
        lines = queue.Queue(maxsize=256)

        def read():
            try:
                for line in iter(lambda: process.stdout.readline(100001), ""):
                    while process.poll() is None:
                        try:
                            lines.put(line[:100000], timeout=0.1)
                            break
                        except queue.Full:
                            continue
                    else:
                        try:
                            lines.put_nowait(line[:100000])
                        except queue.Full:
                            pass
            finally:
                process.stdout.close()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        start = time.monotonic()
        result = None
        got_result = False
        wait = None
        logs = []
        size = 0
        try:
            while process.poll() is None or reader.is_alive() or not lines.empty():
                if stop() or time.monotonic() - start > self.settings.tool_timeout:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise RuntimeError(
                        "Python execution cancelled or timed out; side effects may have occurred"
                    )
                control = getattr(stop, "control", lambda: None)
                if control() == "pause" and process.poll() is None:
                    paused_at = time.monotonic()
                    os.killpg(process.pid, signal.SIGSTOP)
                    notify_pause = getattr(stop, "paused", lambda _: None)
                    notify_pause(True)
                    try:
                        while control() == "pause" and not stop():
                            time.sleep(0.1)
                    finally:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGCONT)
                        notify_pause(False)
                        start += time.monotonic() - paused_at
                    continue
                try:
                    line = lines.get(timeout=0.1).rstrip("\n")
                except queue.Empty:
                    continue
                line = redact(line)
                size += len(line)
                if size > 2_000_000:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise ValueError("Python output exceeded 2 MB")
                if line.startswith("__FLUXYR_RESULT__"):
                    if got_result:
                        raise ValueError("Action called output() more than once")
                    result = json.loads(line[len("__FLUXYR_RESULT__") :])
                    got_result = True
                elif line.startswith("__FLUXYR_WAIT__"):
                    wait = json.loads(line[len("__FLUXYR_WAIT__") :])
                else:
                    logs.append(line)
                    emit(line)
            code = process.wait()
            if code == 75 and wait:
                return {"waiting": wait, "logs": logs[-100:]}
            if code != 0:
                return {
                    "error": "\n".join(logs[-100:])
                    or f"Python exited with status {code}",
                    "exit_code": code,
                    "side_effects_possible": True,
                    "logs": logs[-100:],
                }
            if not got_result:
                return {"error": "Action must call output(value)", "logs": logs[-100:]}
            return {
                "done": True,
                "success": True,
                "output": result,
                "logs": logs[-100:],
                "exit_code": 0,
            }
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            reader.join(timeout=2)
            feeder.join(timeout=2)
