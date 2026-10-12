"""Exercise bounded bootstrap, live continuation and scroll pagination in Chrome."""

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from flask import request
from werkzeug.serving import make_server

from fluxyr.database import MAIN_SESSION
from fluxyr.models import Job, Message


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("FLUXYR_TEST_BROWSER") != "1", reason="requires Chrome")
def test_history_in_browser(make_app, monkeypatch, tmp_path):
    monkeypatch.setattr("fluxyr.history.PAGE_SIZE", 120)
    app, engine, _ = make_app()
    requests = []

    @app.before_request
    def record():
        if request.path.endswith("/history") or request.path.startswith("/api/events"):
            requests.append(request.full_path)

    def turn(number):
        job = engine.store.enqueue(f"Question {number}")
        engine.store.emit(
            MAIN_SESSION, job["id"], "stream_open", {"block_id": f"b{number}"}
        )
        for text in ["Answer ", str(number), ": ", "finished."]:
            engine.store.emit(
                MAIN_SESSION,
                job["id"],
                "delta",
                {"block_id": f"b{number}", "text": text},
            )
        engine.store.emit(
            MAIN_SESSION, job["id"], "stream_close", {"block_id": f"b{number}"}
        )
        engine.store.emit(
            MAIN_SESSION,
            job["id"],
            "tool_end",
            {
                "tool_name": "read",
                "tool_call_id": f"t{number}",
                "result": {"content": "DETAIL-SENTINEL " + "x" * 10000},
            },
        )
        with engine.db.transaction() as s:
            s.get(Job, job["id"]).status = "succeeded"
            s.add(
                Message(
                    session_id=MAIN_SESSION,
                    job_id=job["id"],
                    role="assistant",
                    content=f"Answer {number}: finished.",
                )
            )
        engine.store.emit(MAIN_SESSION, job["id"], "succeeded", {})

    for number in range(60):
        turn(number)

    @app.post("/_test/turn")
    def next_turn():
        turn(60)
        return {"ok": True}

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                os.getenv("FLUXYR_BROWSER_NODE") or shutil.which("node"),
                str(Path(__file__).with_name("history_browser.mjs")),
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
        assert any("before=" in path for path in requests)
        streams = [path for path in requests if path.startswith("/api/events?")]
        assert len(streams) == 1 and "&after=0" not in streams[0]
        assert sum(path.startswith("/api/events/") for path in requests) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
