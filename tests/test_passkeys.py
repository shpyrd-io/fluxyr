import base64
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import execute_next
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from test_vault_interaction import assert_private

from fluxyr.models import VaultItem


def credential():
    key = ec.generate_private_key(ec.SECP256R1())
    return {
        "credentialId": base64.b64encode(b"credential-identity").decode(),
        "isResidentCredential": True,
        "rpId": "example.com",
        "privateKey": base64.b64encode(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ).decode(),
        "userHandle": base64.b64encode(b"account-identifier").decode(),
        "signCount": 0,
    }


def enrollment(make_app, monkeypatch):
    monkeypatch.setenv("FLUXYR_BROWSER_ENABLED", "true")
    app, e, adapter = make_app()
    adapter.replies = [
        [
            (
                "browser_register_passkey",
                {
                    "origin": "https://example.com",
                    "selector": "#register",
                    "suggested_name": "Example passkey",
                },
            )
        ],
        "Saved",
    ]
    monkeypatch.setattr(
        e.browsers.passkeys,
        "prepare",
        lambda sid, **kw: {
            "session_id": sid,
            "generation": "one",
            "target_id": "1",
            "origin": "https://example.com",
            "expires_at": time.time() + 600,
        },
    )
    key = credential()
    calls = []

    class Driver:
        def call(self, op, **kw):
            calls.append(op)
            kw["persist"](key)
            return {"saved": True, "asserted": False}

    monkeypatch.setattr(e.browsers, "_get", lambda *a, **kw: (Driver(), None, "one"))
    e.store.enqueue("Register a passkey")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    entry = job["brain"]["pending_tools"][0]
    url = f"/api/jobs/{job['id']}/private-input/{entry.get('_request_id') or entry['call_id']}"
    return app, e, adapter, job, entry, url, key, calls


def test_enrollment_requires_private_confirmation_and_persists_no_key_in_context(
    make_app, monkeypatch
):
    app, e, adapter, job, entry, url, key, calls = enrollment(make_app, monkeypatch)
    client = app.test_client()
    assert not calls
    assert (
        client.post(
            f"/api/jobs/{job['id']}/decisions/{entry['call_id']}",
            json={"decision": "complete"},
        ).status_code
        == 400
    )
    approval = client.get("/api/approvals").json[0]
    assert approval["kind"] == "browser_passkey" and approval[
        "submission_url"
    ].endswith("/private-input")
    response = client.post(url, json={"name": "Example account passkey"})
    assert response.status_code == 200, response.json
    assert calls == ["passkey_register"]
    assert client.post(url, json={"name": "Do not register twice"}).json["duplicate"]
    assert len(calls) == 1
    assert execute_next(e)["status"] == "succeeded"
    item = e.vault.list()[0]
    assert item["type"] == "passkey" and item["passkey_config"]["state"] == "saved"
    assert e.vault.check(item["name"])["ready"]
    for resolve in (e.vault.resolve, e.vault.get_optional):
        with pytest.raises(ValueError):
            resolve(item["name"])
    with pytest.raises(ValueError):
        e.vault.put(item["name"], "text", {"value": "overwrite"})
    with pytest.raises(ValueError):
        e.vault.update(item["id"], item["name"], {"credential": {}})
    assert (
        e.vault.update(item["id"], "Renamed passkey", {})["name"] == "Renamed passkey"
    )
    assert_private(e, adapter, key["privateKey"])


def test_passkey_approval_auth_cancellation_and_wrong_origin(make_app, monkeypatch):
    monkeypatch.setenv("FLUXYR_APPROVALS_API_KEY", "test-passkey-key")
    app, e, _, job, _, url, _, calls = enrollment(make_app, monkeypatch)
    assert app.test_client().post(url, json={"name": "Example"}).status_code == 401
    e.store.control(job["id"], "cancel")
    assert (
        app.test_client()
        .post(
            url,
            json={"name": "Example"},
            headers={"Authorization": "Bearer test-passkey-key"},
        )
        .status_code
        == 400
    )
    assert not calls


def test_pending_and_foreign_passkeys_cannot_authenticate(make_app, monkeypatch):
    app, e, _, _, _, url, key, _ = enrollment(make_app, monkeypatch)
    app.test_client().post(url, json={"name": "Example"})
    item = e.vault.list()[0]
    with pytest.raises(ValueError, match="exact origin"):
        e.browsers.passkeys.authenticate(
            "sid", item["id"], "https://other.example.com", selector="#login"
        )
    with pytest.raises(ValueError):
        e.browsers.passkeys._persist(
            item["id"], "https://example.com", {**key, "rpId": "evil.example"}
        )
    with pytest.raises(ValueError):
        e.vault.put("Manual", "passkey", {})


def test_failed_assertion_reserves_counter_before_browser_receives_key(
    make_app, monkeypatch
):
    from fluxyr.browser import BrowserError

    app, e, adapter, _, _, url, key, _ = enrollment(make_app, monkeypatch)
    app.test_client().post(url, json={"name": "Example"})
    item = e.vault.list()[0]
    monkeypatch.setattr(
        e.browsers.passkeys,
        "prepare",
        lambda *a, **kw: {
            "generation": "one",
            "target_id": "1",
            "session_id": "s",
            "origin": "https://example.com",
        },
    )
    counts = []

    class FailedDriver:
        def call(self, op, **kwargs):
            private = kwargs["secret"]()
            counts.append(private["signCount"])
            raise BrowserError("disconnected")

    monkeypatch.setattr(
        e.browsers, "_get", lambda *a, **kw: (FailedDriver(), None, "one")
    )
    for _ in range(2):
        with pytest.raises(BrowserError):
            e.browsers.passkeys.authenticate(
                "s", item["id"], "https://example.com", selector="#login"
            )
    assert counts == [0, 1]
    with e.db.transaction() as db:
        stored = e.vault.decrypt(db.get(VaultItem, item["id"]).content)
    assert stored["credential"]["signCount"] == 2
    assert_private(e, adapter, key["privateKey"])


@contextmanager
def relying_party():
    """Local WebAuthn RP that verifies real ECDSA assertions after browser restart."""
    state = {"challenge": base64.b64encode(os.urandom(32)).decode(), "count": 0}
    page = r"""<!doctype html><title>Passkey test</title>
<button id="register">Add passkey</button><button id="login">Sign in with passkey</button><p id="result">Ready</p>
<script>
const bin = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const b64 = b => btoa(String.fromCharCode(...new Uint8Array(b)));
async function ceremony(register) {
 try {
  const config = await (await fetch('/challenge')).json();
  let c;
  if (register) c = await navigator.credentials.create({publicKey:{
    challenge:bin(config.challenge), rp:{id:'localhost',name:'Fluxyr test'},
    user:{id:bin(btoa('account-one')),name:'account-one',displayName:'Test account'},
    pubKeyCredParams:[{type:'public-key',alg:-7}],
    authenticatorSelection:{residentKey:'required',userVerification:'required'},timeout:30000}});
  else c = await navigator.credentials.get({publicKey:{challenge:bin(config.challenge),rpId:'localhost',userVerification:'required',timeout:30000}});
  const r = c.response;
  const data = {id:b64(c.rawId),client:b64(r.clientDataJSON),auth:b64(register ? r.getAuthenticatorData() : r.authenticatorData)};
  if(register) data.publicKey=b64(r.getPublicKey()); else data.signature=b64(r.signature);
  const result = await fetch(register ? '/register' : '/login', {method:'POST',body:JSON.stringify(data)});
  document.querySelector('#result').textContent=result.ok ? (register ? 'Registered' : 'Authenticated') : 'Rejected';
 } catch(e) { document.querySelector('#result').textContent='Error: '+e.name; }
}
document.querySelector('#register').onclick=()=>ceremony(true);
document.querySelector('#login').onclick=()=>ceremony(false);
</script>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json" if self.path == "/challenge" else "text/html",
            )
            self.end_headers()
            self.wfile.write(
                json.dumps({"challenge": state["challenge"]}).encode()
                if self.path == "/challenge"
                else page.encode()
            )

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                client_raw = base64.b64decode(body["client"])
                client = json.loads(client_raw)
                expected = (
                    base64.urlsafe_b64encode(base64.b64decode(state["challenge"]))
                    .decode()
                    .rstrip("=")
                )
                assert (
                    client["challenge"] == expected
                    and client["origin"] == state["origin"]
                )
                auth = base64.b64decode(body["auth"])
                assert auth[:32] == hashlib.sha256(b"localhost").digest()
                assert auth[32] & 5 == 5  # user presence and verification
                count = int.from_bytes(auth[33:37], "big")
                if self.path == "/register":
                    assert client["type"] == "webauthn.create"
                    state["public_key"] = serialization.load_der_public_key(
                        base64.b64decode(body["publicKey"])
                    )
                    state["id"] = body["id"]
                    state["registrations"] = state.get("registrations", 0) + 1
                else:
                    assert (
                        client["type"] == "webauthn.get" and body["id"] == state["id"]
                    )
                    state["public_key"].verify(
                        base64.b64decode(body["signature"]),
                        auth + hashlib.sha256(client_raw).digest(),
                        ec.ECDSA(hashes.SHA256()),
                    )
                    assert count > state["count"]
                    state["authenticated"] = state.get("authenticated", 0) + 1
                state["count"] = count
                state["challenge"] = base64.b64encode(os.urandom(32)).decode()
                self.send_response(200)
            except Exception as error:  # noqa: BLE001 - fixture reports verification failure without key material
                state["error"] = type(error).__name__
                self.send_response(400)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["origin"] = f"http://localhost:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_BROWSER") != "1", reason="requires local Chrome"
)
def test_real_passkey_enrollment_confirmation_and_login_after_browser_restart(
    make_app, monkeypatch
):
    monkeypatch.setenv("FLUXYR_BROWSER_ENABLED", "true")
    app, e, adapter = make_app()
    with relying_party() as site:
        adapter.replies = [
            [
                (
                    "browser_register_passkey",
                    {
                        "origin": site["origin"],
                        "selector": "#register",
                        "suggested_name": "Local passkey",
                    },
                )
            ],
            "Saved",
        ]
        sid = "00000000-0000-0000-0000-000000000001"
        browser = e.browsers
        try:
            assert not browser.call(sid, "navigate", {"url": site["origin"]})["isError"]
            e.store.enqueue("Register a passkey")
            job = execute_next(e)
            assert job["status"] == "waiting", job["error"]
            pending = job["brain"]["pending_tools"][0]
            assert "public_key" not in site  # no enrollment before human consent
            route = f"/api/approvals/{job['id']}/{pending.get('_request_id') or pending['call_id']}/private-input"
            response = app.test_client().post(route, json={"name": "Test site passkey"})
            assert response.status_code == 200, response.json
            item = e.vault.list()[0]
            assert item["passkey_config"]["state"] == "saved", response.json
            assert not browser.call(
                sid, "wait_for", {"condition": "text", "text": "Registered"}
            )["isError"], site
            assert execute_next(e)["status"] == "succeeded"
            with e.db.transaction() as db:
                key = e.vault.decrypt(db.get(VaultItem, item["id"]).content)[
                    "credential"
                ]["privateKey"]
            for _ in range(2):
                browser.call(sid, "close")
                assert not browser.call(sid, "navigate", {"url": site["origin"]})[
                    "isError"
                ]
                outcome = browser.passkeys.authenticate(
                    sid, item["id"], site["origin"], selector="#login"
                )
                assert outcome["asserted"] and outcome["saved"], outcome
                assert not browser.call(
                    sid, "wait_for", {"condition": "text", "text": "Authenticated"}
                )["isError"], site
            assert site["authenticated"] == 2 and "error" not in site
            assert_private(e, adapter, key)
        finally:
            browser.close()


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_BROWSER") != "1", reason="requires local Chrome"
)
def test_real_passkey_save_recovery_does_not_register_again(make_app, monkeypatch):
    monkeypatch.setenv("FLUXYR_BROWSER_ENABLED", "true")
    _, e, adapter = make_app()
    sid = "recover-test"
    browser = e.browsers
    with relying_party() as site:
        try:
            assert not browser.call(sid, "navigate", {"url": site["origin"]})["isError"]
            target = browser.passkeys.prepare(sid, site["origin"], selector="#register")
            persist = browser.passkeys._persist

            def unavailable(*args):
                raise RuntimeError("simulated database outage")

            monkeypatch.setattr(browser.passkeys, "_persist", unavailable)
            result = browser.passkeys.register(
                target, "Recoverable passkey", "recover-request"
            )
            assert result["saved"] is False and result["recoverable"] is True
            assert e.vault.check("Recoverable passkey")["ready"] is False
            assert not browser.call(
                sid, "wait_for", {"condition": "text", "text": "Registered"}
            )["isError"]
            monkeypatch.setattr(browser.passkeys, "_persist", persist)
            target = browser.passkeys.recovery_target(
                sid, result["vault_item_id"], site["origin"]
            )
            recovered = browser.passkeys.register(
                target,
                "Recoverable passkey",
                "recover-confirmation",
                item_id=result["vault_item_id"],
            )
            assert recovered["saved"] and e.vault.check("Recoverable passkey")["ready"]
            assert site["registrations"] == 1
            browser.call(sid, "close")
            browser.call(sid, "navigate", {"url": site["origin"]})
            assert browser.passkeys.authenticate(
                sid, result["vault_item_id"], site["origin"], selector="#login"
            )["asserted"]
            assert not browser.call(
                sid, "wait_for", {"condition": "text", "text": "Authenticated"}
            )["isError"]
            with e.db.transaction() as db:
                key = e.vault.decrypt(
                    db.get(VaultItem, result["vault_item_id"]).content
                )["credential"]["privateKey"]
            assert_private(e, adapter, key)
        finally:
            browser.close()


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FLUXYR_TEST_BROWSER") != "1", reason="requires local Chrome"
)
def test_real_passkey_confirmation_card(make_app, monkeypatch):
    from werkzeug.serving import make_server

    monkeypatch.setenv("FLUXYR_BROWSER_ENABLED", "true")
    app, e, adapter = make_app()
    browser = e.browsers
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ui_origin = f"http://127.0.0.1:{server.server_port}"
    with relying_party() as site:
        try:
            sid = "00000000-0000-0000-0000-000000000001"
            browser.call(sid, "navigate", {"url": site["origin"]})
            adapter.replies = [
                [
                    (
                        "browser_register_passkey",
                        {
                            "origin": site["origin"],
                            "selector": "#register",
                            "suggested_name": "Example account",
                        },
                    )
                ],
                "Saved",
            ]
            e.store.enqueue("Register this passkey")
            assert execute_next(e)["status"] == "waiting"
            browser.call("ui", "navigate", {"url": ui_origin})
            card = '[aria-label="Private browser input"]'
            assert not browser.call(
                "ui", "wait_for", {"condition": "element", "selector": card}
            )["isError"]
            target = browser.prepare("ui", ui_origin, selector=card + " input")
            browser.fill(target, value="Test passkey from private card")
            assert "public_key" not in site
            if os.getenv("FLUXYR_TEST_SCREENSHOT_DIR"):
                import shutil
                from pathlib import Path

                screenshot = browser.call("ui", "capture_image")
                block = next(
                    b for b in screenshot["content"] if b["type"] == "image_file"
                )
                shutil.copy2(
                    e.files.path(block["path"]),
                    Path(os.environ["FLUXYR_TEST_SCREENSHOT_DIR"])
                    / ("fluxyr-passkey-card" + Path(block["path"]).suffix),
                )
            browser.call("ui", "click", {"selector": card + ' button[type="submit"]'})
            assert not browser.call(
                "ui",
                "wait_for",
                {
                    "condition": "js",
                    "expression": "!document.querySelector('[aria-label=\"Private browser input\"]') || document.body.innerText.includes('Response saved')",
                },
            )["isError"]
            item = e.vault.list()[0]
            assert (
                item["name"] == "Test passkey from private card"
                and item["passkey_config"]["state"] == "saved"
            )
            with e.db.transaction() as db:
                key = e.vault.decrypt(db.get(VaultItem, item["id"]).content)[
                    "credential"
                ]["privateKey"]
            assert_private(e, adapter, key)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
