import base64
import json
import time

import pytest
from conftest import execute_next
from test_vault_interaction import assert_private

from fluxyr.otp import configuration, generate


@pytest.mark.parametrize(
    "algorithm,raw,expected",
    [
        ("SHA1", b"12345678901234567890", "94287082"),
        ("SHA256", b"12345678901234567890123456789012", "46119246"),
        (
            "SHA512",
            b"1234567890123456789012345678901234567890123456789012345678901234",
            "90693936",
        ),
    ],
)
def test_rfc6238_vectors(algorithm, raw, expected):
    config = {
        "secret": base64.b32encode(raw).decode(),
        "digits": 8,
        "algorithm": algorithm,
    }
    assert generate(config, 59) == (expected, 60)


def test_otpauth_and_invalid_secret_errors():
    config = configuration(
        {
            "secret": "otpauth://totp/Example%3Aalice?secret=JBSWY3DPEHPK3PXP&issuer=Example&digits=8&period=60&algorithm=SHA256"
        }
    )
    assert config["account"] == "Example:alice"
    assert config["digits"] == 8 and config["period"] == 60
    assert config["algorithm"] == "SHA256"
    for seed in (
        "private-sentinel!",
        "otpauth://hotp/test?secret=JBSWY3DPEHPK3PXP&counter=1",
    ):
        with pytest.raises(ValueError) as exc:
            configuration({"secret": seed})
        assert seed not in str(exc.value)


def test_vault_totp_is_encrypted_and_edit_preserves_settings(make_app):
    _, e, adapter = make_app()
    seed = "JBSWY3DPEHPK3PXP"
    item = e.vault.put("OTP Example", "totp", {"secret": seed, "digits": "8"})
    e.vault.update(item["id"], "OTP Example", {"issuer": "Example"})
    saved = e.vault.get_optional("OTP Example")
    assert saved["digits"] == 8 and saved["secret"] == seed
    assert e.vault.check("OTP Example")["ready"]
    assert seed not in json.dumps(e.vault.list(include_configuration=True))
    assert_private(e, adapter, seed)


def request_private(make_app, monkeypatch, *, vault=False):
    monkeypatch.setenv("FLUXYR_BROWSER_ENABLED", "true")
    args = {
        "origin": "https://example.com",
        "selector": "#code",
        "title": "Enter SMS code",
    }
    app, e, adapter = make_app()
    if vault:
        args["vault_item_id"] = e.vault.put(
            "Example password", "text", {"value": "secret-sentinel"}
        )["id"]
    adapter.replies = [[("browser_request_input", args)], "Continued"]
    monkeypatch.setattr(
        e.browsers,
        "prepare",
        lambda sid, **kwargs: {
            "target_id": "1",
            "generation": "browser-1",
            "session_id": sid,
            "origin": args["origin"],
            "expires_at": time.time() + 600,
        },
    )
    calls = []
    monkeypatch.setattr(
        e.browsers,
        "fill",
        lambda target, **kwargs: (
            calls.append(kwargs) or {"filled": True, "submitted": False}
        ),
    )
    e.store.enqueue("Sign in")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    entry = job["brain"]["pending_tools"][0]
    request_id = entry.get("_request_id") or entry["call_id"]
    endpoint = f"/api/jobs/{job['id']}/private-input/{request_id}"
    return app, e, adapter, job, entry, endpoint, calls


def test_private_input_delivers_once_and_never_reaches_context(make_app, monkeypatch):
    app, e, adapter, _job, _, endpoint, calls = request_private(make_app, monkeypatch)
    secret = "private-browser-sentinel-14999"
    response = app.test_client().post(endpoint, json={"value": secret})
    assert response.status_code == 200, response.json
    assert response.json["status"] == "queued"
    assert calls[0]["value"] == secret
    assert (
        app.test_client().post(endpoint, json={"value": "new-secret"}).json["duplicate"]
    )
    assert len(calls) == 1
    final = execute_next(e)
    assert final["status"] == "succeeded", final["error"]
    assert_private(e, adapter, secret, "new-secret")
    tool = [m for m in adapter.calls[-1] if m["role"] == "tool"][-1]
    assert json.loads(tool["content"])["filled"]


def test_private_form_not_generic_decision_and_approval_api(make_app, monkeypatch):
    app, e, adapter, job, entry, _, calls = request_private(
        make_app, monkeypatch, vault=True
    )
    client = app.test_client()
    route = f"/api/jobs/{job['id']}/decisions/{entry['call_id']}"
    secret = "private-browser-sentinel-reject"
    assert (
        client.post(
            route, json={"decision": "complete", "result": {"value": secret}}
        ).status_code
        == 400
    )
    approval = client.get("/api/approvals").json[0]
    assert approval["kind"] == "browser_private_input"
    response = client.post(approval["submission_url"], json={})
    assert response.status_code == 200, response.json
    assert calls[0]["vault_item_id"]
    assert calls[0]["value"] is None
    assert execute_next(e)["status"] == "succeeded"
    assert_private(e, adapter, secret, "secret-sentinel")


def test_cancelled_private_request_cannot_fill(make_app, monkeypatch):
    app, e, _adapter, job, _, endpoint, calls = request_private(make_app, monkeypatch)
    e.store.control(job["id"], "cancel")
    assert app.test_client().post(endpoint, json={"value": "secret"}).status_code == 400
    assert calls == []


def test_expired_browser_reports_failure_and_resumes(make_app, monkeypatch):
    app, e, adapter, _, _, endpoint, _ = request_private(make_app, monkeypatch)

    def expired(*args, **kwargs):
        raise ValueError("expired")

    monkeypatch.setattr(e.browsers, "fill", expired)
    response = app.test_client().post(
        endpoint, json={"value": "secret-expiry-sentinel"}
    )
    assert response.status_code == 200
    assert execute_next(e)["status"] == "succeeded"
    assert_private(e, adapter, "secret-expiry-sentinel")


def test_vault_code_is_generated_only_on_driver_request(make_app, monkeypatch):
    _, e, _ = make_app()
    seed = "JBSWY3DPEHPK3PXP"
    item = e.vault.put("Authenticator", "totp", {"secret": seed})

    class Fake:
        def call(self, op, **args):
            assert op == "fill" and "value" not in args
            code = args.pop("secret")()
            assert code == generate({"secret": seed})[0]
            assert code != seed
            return {"filled": True}

    monkeypatch.setattr(e.browsers, "_get", lambda *a, **kw: (Fake(), None, "one"))
    result = e.browsers.fill(
        {"session_id": "sid", "generation": "one", "target_id": "1"},
        vault_item_id=item["id"],
    )
    assert result == {"filled": True}


def test_browser_generation_change_never_resolves_vault(make_app, monkeypatch):
    _, e, _ = make_app()
    monkeypatch.setattr(e.browsers, "_get", lambda *a, **kw: (None, None, "new"))
    with pytest.raises(ValueError, match="restarted"):
        e.browsers.fill(
            {"session_id": "s", "generation": "old"}, vault_item_id="not-read"
        )


def test_private_input_alias_requires_approval_key(make_app, monkeypatch):
    monkeypatch.setenv("FLUXYR_APPROVALS_API_KEY", "browser-approval-key")
    app, _, _, job, entry, endpoint, calls = request_private(make_app, monkeypatch)
    client = app.test_client()
    public = f"/api/approvals/{job['id']}/{entry['call_id']}/private-input"
    for path in (endpoint, public):
        assert client.post(path, json={"value": "never-delivered"}).status_code == 401
    assert not calls
    response = client.post(
        public,
        json={"value": "delivered"},
        headers={"Authorization": "Bearer browser-approval-key"},
    )
    assert response.status_code == 200
    assert len(calls) == 1


def test_near_expiry_waits_without_busy_loop_and_generates_next_code(
    make_app, monkeypatch
):
    from types import SimpleNamespace

    import fluxyr.browser as module

    _, e, _ = make_app()
    item = e.vault.put("OTP", "totp", {"secret": "JBSWY3DPEHPK3PXP"})
    now, sleeps, generations = [29.0], [], []

    def sleep(seconds):
        assert 0 < seconds <= 0.1
        sleeps.append(seconds)
        now[0] += seconds

    def code(content):
        generations.append(now[0])
        return ("111111", 30) if now[0] < 30 else ("222222", 60)

    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0], sleep=sleep),
    )
    monkeypatch.setattr(module, "generate", code)

    class Fake:
        def call(self, op, **args):
            assert args["secret"]() == "222222"
            return {"filled": True}

    monkeypatch.setattr(e.browsers, "_get", lambda *a, **kw: (Fake(), None, "one"))
    assert e.browsers.fill(
        {"session_id": "s", "generation": "one", "target_id": "1"},
        vault_item_id=item["id"],
    )["filled"]
    assert len(generations) == 2 and 1 <= len(sleeps) <= 12


def test_concurrent_private_post_cannot_settle_before_delivery(make_app, monkeypatch):
    import threading

    app, e, _, _, _, endpoint, _ = request_private(make_app, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls, responses = [], []

    def fill(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"filled": True}

    monkeypatch.setattr(e.browsers, "fill", fill)

    def first():
        responses.append(
            app.test_client().post(endpoint, json={"value": "first-private"})
        )

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(3)
        response = app.test_client().post(endpoint, json={"value": "second-private"})
        assert response.status_code == 400
    finally:
        release.set()
        thread.join(timeout=5)
    assert len(calls) == 1 and responses[0].status_code == 200
