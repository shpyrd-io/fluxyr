"""Real CLI/reloader process, HTTP server and single PostgreSQL supervisor."""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration



def test_dev_reload_on_skill_add_remove_and_environment(database_url, tmp_path):
    if not database_url.startswith("postgresql"):
        pytest.skip("Requires disposable PostgreSQL")
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "skills").mkdir()
    (root / "app.py").write_text("""from fluxyr import Fluxyr
import os
app = Fluxyr(__name__)
@app.get('/api/example/pid')
def pid(): return {'pid':os.getpid()}
""")
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = root / ".env"
    config.write_text(
        f"DATABASE_URL={database_url}\nPORT={port}\nFLUXYR_SKILLS_DIR=skills\nFLUXYR_PROVIDER=openrouter\nFLUXYR_MODEL=test-model\nOPENROUTER_API_KEY=test-no-requests\nFLUXYR_MAX_TOKENS=2000\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("FLUXYR_")
        and k not in ("DATABASE_URL", "PORT", "WERKZEUG_RUN_MAIN")
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    log = (root / "server.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "fluxyr", "--app", "app:app", "--reload"],
        cwd=root,
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"

    def read(path):
        with urllib.request.urlopen(base + path, timeout=1) as response:
            return json.load(response)

    def until(check):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail((root / "server.log").read_text())
            try:
                result = check()
                if result:
                    return result
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        pytest.fail("Reload timed out: " + (root / "server.log").read_text())

    try:
        until(lambda: read("/api/health")["worker"])
        original = read("/api/example/pid")["pid"]
        assert original != proc.pid  # the monitor parent never owns the worker
        (root / "skills" / "new.md").write_text(
            "Use the database when asked for real facts."
        )
        until(
            lambda: (
                read("/api/example/pid")["pid"] != original
                and len(read("/api/skills")) == 1
            )
        )
        assert read("/api/health")["worker"]
        (root / "skills" / "new.md").unlink()
        until(lambda: len(read("/api/skills")) == 0)
        config.write_text(
            config.read_text().replace(
                "FLUXYR_MAX_TOKENS=2000", "FLUXYR_MAX_TOKENS=3000"
            )
        )
        until(lambda: read("/api/settings")["max_tokens"] == 3000)
        assert read("/api/health")["worker"]
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        log.close()
