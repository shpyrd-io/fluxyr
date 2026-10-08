"""Opt-in Chrome coverage for theme persistence, OS changes and responsive UI."""

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from conftest import execute_next
from werkzeug.serving import make_server


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_BROWSER") != "1", reason="requires public-browser and Chrome"
)
def test_theme_in_browser(make_app, tmp_path):
    app, engine, _ = make_app(replies=["Ready to help. **Your agent is online.**"])
    engine.store.enqueue("Hello, Fluxyr!")
    execute_next(engine)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                os.getenv("FLUXYR_BROWSER_NODE") or shutil.which("node"),
                str(Path(__file__).with_name("theme_browser.mjs")),
                f"http://127.0.0.1:{server.server_port}",
                str(
                    engine.browsers.runtime
                    / "node_modules/public-browser/build/lib/session-core.js"
                ),
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if destination := os.getenv("FLUXYR_TEST_SCREENSHOT_DIR"):
            Path(destination).mkdir(parents=True, exist_ok=True)
            for screenshot in tmp_path.glob("theme-*.png"):
                shutil.copy2(screenshot, Path(destination) / screenshot.name)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
