"""Cancellable process groups with byte streaming and bounded I/O buffers."""

import os
import selectors
import signal
import subprocess
import time


def run_process(command, *, cwd, env, timeout, stop, on_chunk, input_file=None):
    if stop():
        return {
            "exit_code": 130,
            "wall_time_seconds": 0,
            "error": "Command aborted before dispatch",
        }
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=input_file or subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    started = time.monotonic()
    paused_seconds = 0.0
    error = None
    terminated = False

    def send(sig):
        nonlocal terminated
        if terminated:
            return
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGKILL:
            # Darwin may report EPERM for an already-killed orphan zombie group.
            # Terminate once, then only drain/reap; never signal that group again.
            terminated = True

    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                if stop():
                    error = "Command aborted"
                    send(signal.SIGKILL)
                    break
                if time.monotonic() - started - paused_seconds > timeout:
                    error = f"Command timed out after {timeout:g} seconds"
                    send(signal.SIGKILL)
                    break
                control = getattr(stop, "control", lambda: None)
                if control() == "pause" and process.poll() is None:
                    paused_at = time.monotonic()
                    send(signal.SIGSTOP)
                    notify = getattr(stop, "paused", lambda _: None)
                    notify(True)
                    try:
                        while control() == "pause" and not stop():
                            time.sleep(0.1)
                    finally:
                        send(signal.SIGCONT)
                        notify(False)
                        paused_seconds += time.monotonic() - paused_at
                    continue
                ready = selector.select(0.1)
                for key, _ in ready:
                    chunk = os.read(key.fd, 65536)
                    if chunk:
                        on_chunk(chunk)
                    else:
                        selector.unregister(key.fileobj)
                # Do not wait forever on inherited pipes from background jobs.
                # Commands are scoped to one call, including their descendants.
                if process.poll() is not None:
                    send(signal.SIGKILL)
                    if not ready:
                        break
        if process.poll() is None:
            process.wait(timeout=1)
    finally:
        send(signal.SIGKILL)
        process.wait()
        process.stdout.close()
    code = process.returncode
    return {
        "exit_code": 128 - code if code < 0 else code,
        "wall_time_seconds": round(time.monotonic() - started - paused_seconds, 2),
        **({"error": error} if error else {}),
    }
