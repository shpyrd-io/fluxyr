"""Opt-in end-to-end test: FLUXYR_TEST_BROWSER=1 pytest -m integration ..."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fluxyr.otp import generate


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_BROWSER") != "1",
    reason="requires Node, public-browser and Chrome",
)
def test_real_headless_private_fill_snapshot_expiry_and_totp(make_app, monkeypatch):
    monkeypatch.setenv("FLUXYR_BROWSER_ENABLED", "true")
    received = []
    posted = threading.Event()

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b'<html><body><form method="POST"><label>Password<input id="password" name="password" type="password"></label><label>Code<input id="code" name="code"></label><button>Sign in</button></form></body></html>'
            )

        def do_POST(self):
            received.append(
                self.rfile.read(int(self.headers["Content-Length"])).decode()
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Signed in")
            posted.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    _, e, _ = make_app()
    browser = e.browsers
    try:
        result = browser.call("one", "navigate", {"url": origin})
        assert not result["isError"], result
        page = browser.call("one", "view_page")
        assert "Password" in json.dumps(page)
        target = browser.prepare("one", origin, selector="#password")
        assert browser.fill(target, value="private-chrome-sentinel")["filled"]
        check = browser.call(
            "one",
            "evaluate",
            {"expression": 'document.querySelector("#password").value'},
        )
        assert "private-chrome-sentinel" not in json.dumps(check)
        assert "[private]" in json.dumps(check)
        image = browser.call("one", "capture_image", {})
        assert any(block["type"] == "image_file" for block in image["content"]), image
        assert browser._get("one")[0].call("status")["transport"] == "pipe"
        seed = "JBSWY3DPEHPK3PXP"
        item = e.vault.put("OTP", "totp", {"secret": seed})
        target = browser.prepare("one", origin, selector="#code")
        assert browser.fill(target, vault_item_id=item["id"], submit=True)["submitted"]
        assert posted.wait(3)
        assert received and "password=private-chrome-sentinel" in received[0]
        assert "code=" + generate({"secret": seed})[0] in received[0]
        # The old field handle cannot be replayed into a different document.
        with pytest.raises(ValueError):
            browser.fill(target, value="must-not-fill")
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_BROWSER") != "1",
    reason="requires Node, public-browser and Chrome",
)
def test_private_input_card_in_real_ui(make_app, monkeypatch):
    from test_browser_private import request_private
    from test_vault_interaction import assert_private
    from werkzeug.serving import make_server

    from fluxyr.browser import Browsers

    app, e, adapter, _, _, _, calls = request_private(make_app, monkeypatch)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    ui = Browsers(e)
    try:
        assert not ui.call("ui", "navigate", {"url": origin})["isError"]
        card = '[aria-label="Private browser input"]'
        ready = ui.call("ui", "wait_for", {"condition": "element", "selector": card})
        assert not ready["isError"], ready
        # Use the actual browser's private channel to type the form as a human would.
        field = ui.prepare("ui", origin, selector=card + ' input[type="password"]')
        ui.fill(field, value="human-ui-private-sentinel")
        screenshot = ui.call("ui", "capture_image")
        assert any(b["type"] == "image_file" for b in screenshot["content"])
        if os.getenv("FLUXYR_TEST_SCREENSHOT_DIR"):
            import shutil
            from pathlib import Path

            block = next(b for b in screenshot["content"] if b["type"] == "image_file")
            shutil.copy2(
                e.files.path(block["path"]),
                Path(os.environ["FLUXYR_TEST_SCREENSHOT_DIR"])
                / "fluxyr-private-input-ui.png",
            )
        clicked = ui.call("ui", "click", {"selector": card + ' button[type="submit"]'})
        assert not clicked["isError"], clicked
        ready = ui.call(
            "ui", "wait_for", {"condition": "text", "text": "Response saved"}
        )
        assert not ready["isError"], ready
        assert len(calls) == 1 and calls[0]["value"] == "human-ui-private-sentinel"
        assert_private(e, adapter, "human-ui-private-sentinel")
    finally:
        ui.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
