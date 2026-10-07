"""Pi-style file/shell contracts with shared cwd, effects and execution controls."""

import codecs
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from ..runtime.process import run_process
from ..runtime.python_runner import PythonRunner, lock_for

MAX_BYTES = 50 * 1024
MAX_LINES = 2000


class WorkspaceTools:
    def __init__(self, settings):
        self.settings = settings
        self.cwd = settings.data.resolve()

    def path(self, path):
        return (self.cwd / Path(path).expanduser()).resolve()

    def context(self, job_id):
        work = self.settings.workspace / job_id / "tools"
        work.mkdir(parents=True, exist_ok=True)
        env = PythonRunner._environment(work, Path(sys.executable))
        if os.getenv("HOME"):
            env["HOME"] = os.environ["HOME"]
        env["FLUXYR_DATA"] = str(self.cwd)
        env["FLUXYR_WORKSPACE"] = str(work)
        return work, env

    def file(self, operation, args, job_id, stop=lambda: False):
        work, env = self.context(job_id)
        worker = Path(__file__).parents[1] / "runtime" / "file_worker.py"
        command = PythonRunner(self.settings, None)._command(
            [sys.executable, "-I", "-u", str(worker)], [self.cwd, work]
        )
        data = bytearray()

        def collect(chunk):
            if len(data) + len(chunk) > 6 * 1024 * 1024:
                raise ValueError("File tool response exceeded 6 MiB")
            data.extend(chunk)

        def execute():
            # File-backed stdin cannot deadlock on large write arguments.
            with tempfile.TemporaryFile(dir=work) as request:
                request.write(
                    json.dumps({"operation": operation, "args": args}).encode()
                )
                request.seek(0)
                status = run_process(
                    command,
                    cwd=self.cwd,
                    env=env,
                    timeout=self.settings.tool_timeout,
                    stop=stop,
                    on_chunk=collect,
                    input_file=request,
                )
            if status.get("error") or status["exit_code"] != 0:
                return {
                    "error": status.get("error")
                    or data.decode(errors="replace")[-4000:],
                    **status,
                }
            return json.loads(data)

        if operation in ("write", "edit"):
            with lock_for(str(self.path(args["path"]))):
                return execute()
        return execute()

    def bash(
        self, command, job_id, timeout=None, emit=lambda _: None, stop=lambda: False
    ):
        timeout = self.settings.tool_timeout if timeout is None else timeout
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= self.settings.tool_timeout
        ):
            raise ValueError(
                f"timeout must be greater than zero and at most {self.settings.tool_timeout} seconds"
            )
        shell = shutil.which("bash")
        if not shell:
            raise ValueError("bash is not installed in this deployment")
        work, env = self.context(job_id)
        spool = work / (str(uuid.uuid4()) + ".log")
        tail = bytearray()
        total = 0
        last_emit = 0
        pending = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        exceeded = False

        def flush():
            nonlocal pending, last_emit
            if pending:
                emit(pending)
                pending = ""
                last_emit = time.monotonic()

        with spool.open("wb") as output:

            def collect(chunk):
                nonlocal total, pending, exceeded
                total += len(chunk)
                if total > self.settings.bash_max_output_bytes:
                    exceeded = True
                    raise ValueError("Bash output exceeded the configured disk quota")
                output.write(chunk)
                tail.extend(chunk)
                if len(tail) > MAX_BYTES * 2:
                    del tail[: -MAX_BYTES * 2]
                pending = (pending + decoder.decode(chunk))[-8192:]
                if time.monotonic() - last_emit >= 0.1:
                    flush()

            try:
                status = run_process(
                    PythonRunner(self.settings, None)._command(
                        [shell, "--noprofile", "--norc", "-c", command],
                        [self.cwd, work],
                    ),
                    cwd=self.cwd,
                    env=env,
                    timeout=timeout,
                    stop=stop,
                    on_chunk=collect,
                )
            except ValueError:
                if not exceeded:
                    raise
                status = {
                    "error": "Bash output exceeded the configured disk quota",
                    "exit_code": 137,
                }
            finally:
                pending += decoder.decode(b"", final=True)
                flush()
        # Keep the end, skipping a partial first UTF-8 character/line after truncation.
        text = bytes(tail).decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        text = "".join(lines[-MAX_LINES:])
        encoded = text.encode()
        if len(encoded) > MAX_BYTES:
            text = encoded[-MAX_BYTES:].decode("utf-8", errors="ignore")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        truncated = total > len(text.encode())
        content = text or "(no output)"
        if truncated:
            content += f"\n\n[Output truncated. Full output: {spool}]"
        else:
            spool.unlink()
        if status["exit_code"] != 0 and "error" not in status:
            status["error"] = f"Command exited with code {status['exit_code']}"
        return {
            "content": content,
            "truncated": truncated,
            **({"full_output_path": str(spool)} if truncated else {}),
            **status,
        }
