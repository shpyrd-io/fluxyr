"""Bounded Python execution with optional Linux Landlock write confinement."""

import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from packaging.requirements import Requirement

from .landlock import launch_command

_locks_guard = threading.Lock()
_locks = {}


@contextmanager
def lock_for(key):
    with _locks_guard:
        entry = _locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _locks_guard:
            entry[1] -= 1
            if not entry[1]:
                del _locks[key]


class PythonRunner:
    def __init__(self, settings, vault):
        self.settings = settings
        self.vault = vault

    @staticmethod
    def _install(command, timeout, stop, *, env=None, cwd=None):
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=env,
            cwd=cwd,
            close_fds=True,
        )
        start = time.monotonic()
        try:
            while True:
                if stop():
                    raise RuntimeError("Dependency setup cancelled")
                if time.monotonic() - start > timeout:
                    raise RuntimeError("Dependency setup timed out")
                try:
                    _stdout, stderr = process.communicate(timeout=0.1)
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
        directory = self.settings.cache / "envs" / digest
        with lock_for(digest):
            if stop():
                raise RuntimeError("Dependency setup cancelled")
            python = directory / "bin" / "python"
            if not (directory / ".ready").exists():
                directory.mkdir(parents=True, exist_ok=True)
                emit(
                    "Installing Python dependencies…"
                    if dependencies
                    else "Preparing Python environment…"
                )
                with tempfile.TemporaryDirectory(
                    prefix=".setup-", dir=directory
                ) as scratch:
                    setup_env = self._environment(scratch, python)

                    def install(command, timeout):
                        self._install(
                            self._command(command, [directory]),
                            timeout,
                            stop,
                            env=setup_env,
                            cwd=scratch,
                        )

                    command = [sys.executable, "-m", "venv"]
                    if not dependencies:
                        # Stdlib-only actions need isolation, not a pip bootstrap.
                        command.append("--without-pip")
                    install([*command, str(directory)], 90)
                    if dependencies:
                        install(
                            [
                                str(python),
                                "-m",
                                "pip",
                                "install",
                                "--disable-pip-version-check",
                                "--no-cache-dir",
                                *dependencies,
                            ],
                            300,
                        )
                (directory / ".ready").touch()
            return python

    def _command(self, command, writable):
        if self.settings.execution_mode == "landlock":
            return launch_command(command, writable)
        if self.settings.execution_mode != "local":
            raise ValueError("FLUXYR_EXECUTION_MODE must be local or landlock")
        return command

    @staticmethod
    def _environment(work, python):
        # Skills inherit deployment configuration, including database/provider env.
        # Place caches and temporary files inside their writable invocation folder.
        env = os.environ.copy()
        env.update(
            {
                "PATH": str(python.parent)
                + os.pathsep
                + env.get("PATH", "/usr/bin:/bin"),
                "HOME": str(work),
                "VIRTUAL_ENV": str(python.parent.parent),
                "TMPDIR": str(work),
                "TMP": str(work),
                "TEMP": str(work),
                "XDG_CACHE_HOME": str(Path(work) / ".cache"),
                "PYTHONUNBUFFERED": "1",
            }
        )
        return env

    def run(
        self,
        version,
        params,
        job_id,
        emit=lambda *_: None,
        stop=lambda: False,
        answer=None,
        continuation=None,
    ):
        logs = []
        stage = {"phase": "contract_validation", "executed": False}
        identity = {
            "version_id": version["id"],
            "source_sha256": hashlib.sha256(version["source"].encode()).hexdigest(),
        }

        def capture(line):
            logs.append(str(line))
            if len(logs) > 100:
                del logs[0]
            emit(line)

        try:
            result = self._run(
                version, params, job_id, capture, stop, answer, continuation, stage
            )
            if result.get("waiting"):
                return {"done": False, "success": None, **result, **identity}
            return {
                "done": True,
                "success": "error" not in result,
                **stage,
                **result,
                **identity,
            }
        except Exception as exc:  # noqa: BLE001 - action failures become protocol results
            return {
                "done": True,
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "logs": logs,
                "side_effects_possible": stage["executed"],
                **stage,
                **identity,
            }

    def _run(
        self,
        version,
        params,
        job_id,
        emit=lambda *_: None,
        stop=lambda: False,
        answer=None,
        continuation=None,
        stage=None,
    ):
        from .contracts import output_failed, validate_secret_references

        stage = stage if stage is not None else {}
        validate_secret_references(version["source"], version.get("secrets"))
        stage.update(phase="dependency_installation")
        python = self.environment(
            version.get("dependencies", []), emit, version["id"], stop
        )
        # Different invocation directories prevent parallel tools overwriting one another.
        import uuid

        work = self.settings.workspace / job_id / str(uuid.uuid4())
        work.mkdir(parents=True)
        (work / "action.py").write_text(version["source"])
        shutil.copyfile(Path(__file__).with_name("helper.py"), work / "fluxyr.py")
        shutil.copyfile(
            Path(__file__).with_name("human_protocol.py"), work / "fluxyr_human.py"
        )
        secret_names = version.get("secrets", [])
        resolved = {}
        for name in secret_names:
            stage.update(phase="credential_resolution", credential=name, executed=False)
            try:
                resolved[name] = self.vault.resolve(name)
            except Exception as exc:  # noqa: BLE001 - report private setup failures without exposing credential contents
                # The script never started. Keep private URLs, tokens and HTTP bodies
                # out of the model-visible exception; the editable item is identified.
                stage["remediation"] = (
                    "Inspect vault_list configuration and edit this credential with manage_vault_credential. Do not rebuild the action for a credential setup or token endpoint failure."
                )
                raise ValueError(
                    f"Could not resolve Vault credential {name!r} ({type(exc).__name__}). Check its token endpoint, OAuth flow and certificate configuration in Vault. Python was not executed."
                ) from None
        stage.clear()
        stage.update(phase="process_start", executed=False)
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

        env = self._environment(work, python)
        env["FLUXYR_DATA"] = str(self.settings.data)
        process = subprocess.Popen(
            self._command(
                [str(python), "-u", str(work / "action.py")],
                [self.settings.data, work],
            ),
            cwd=work,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            close_fds=True,
        )

        stage.update(phase="python_execution", executed=True)

        def feed_input():
            try:
                process.stdin.write(
                    json.dumps(
                        {
                            "params": params,
                            "secrets": resolved,
                            "approval_response": answer,
                            "continuation": continuation,
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
            if output_failed(result):
                return {
                    "done": True,
                    "success": False,
                    "output": result,
                    "error": str(
                        result.get("error") or "Action returned success=false"
                    ),
                    "error_type": "ActionReportedFailure",
                    "exit_code": 0,
                    "side_effects_possible": True,
                    "logs": logs[-100:],
                }
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
