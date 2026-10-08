import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import execute_next
from jsonschema import ValidationError

from fluxyr.database import row_dict
from fluxyr.models import Job, Session, VaultItem
from fluxyr.runtime.contracts import vault_parameters

SCHEMA = {
    "type": "object",
    "properties": {
        "credential": {"type": "string", "x-vault": True},
    },
    "required": ["credential"],
    "additionalProperties": False,
}


def create(e, source=None, schema=None, secrets=None):
    skill = e.skills.build("dynamic_auth", "Dynamic credentials", "Select a Vault item")
    return e.skills.create(
        skill["id"],
        "action_dynamic_auth",
        "Use the selected credential",
        source
        or 'from fluxyr import secret, output\noutput({"configured": bool(secret("credential")["value"])})',
        schema or SCHEMA,
        secrets=secrets,
    )


def test_same_version_accepts_new_and_renamed_items_resolves_only_selected(
    make_app, monkeypatch
):
    _, e, _ = make_app()
    # No existing Vault item is needed to build the parameter contract.
    v = create(e)
    first = e.vault.put("Staging", "text", {"value": "staging-private-sentinel"})
    second = e.vault.put("Production", "text", {"value": "production-private-sentinel"})
    selected = []
    resolve = e.vault.resolve_id

    def track(item_id):
        selected.append(item_id)
        return resolve(item_id)

    monkeypatch.setattr(e.vault, "resolve_id", track)
    for item in (first, second):
        assert e.skills.test(v["id"], {"credential": item["id"]})["passed"]
    with e.db.transaction() as s:
        s.get(VaultItem, first["id"]).name = "Renamed staging"
    assert e.skills.test(v["id"], {"credential": first["id"]})["passed"]
    assert selected == [first["id"], second["id"], first["id"]]
    e.skills.activate(v["id"])
    assert e.skills.version(v["id"])["secrets"] == []


def test_dynamic_secret_stays_out_of_events_brain_and_test_results(make_app):
    _, e, adapter = make_app()
    value = "dynamic-secret-never-in-context-24891"
    item = e.vault.put("Selected", "text", {"value": value})
    v = create(
        e,
        """from fluxyr import secret, params, output, log
credential = secret("credential")
log(credential["value"])
output({"selected_id": params["credential"], "accidental_value": credential["value"]})
""",
    )
    test = e.skills.test(v["id"], {"credential": item["id"]})
    assert test["passed"]
    assert test["output"] == {"selected_id": item["id"], "accidental_value": "[secret]"}
    e.skills.activate(v["id"])
    adapter.replies = [[("action_dynamic_auth", {"credential": item["id"]})], "Done"]
    e.store.enqueue("Use the selected credential")
    job = execute_next(e)
    with e.db.transaction() as s:
        persisted = [
            row_dict(s.get(Job, job["id"])),
            row_dict(s.get(Session, job["session_id"])),
        ]
    public = json.dumps(
        [
            test,
            e.skills.version(v["id"]),
            e.store.events(job["session_id"]),
            adapter.calls,
            persisted,
        ]
    )
    assert value not in public
    assert item["id"] in public
    assert job["status"] == "succeeded"


@pytest.mark.parametrize("choice", ["missing-id", "Display name is not an ID"])
def test_unknown_selector_fails_before_python_without_exposing_contents(
    make_app, choice
):
    _, e, _ = make_app()
    e.vault.put("Display name is not an ID", "text", {"value": "private"})
    v = create(e, 'from fluxyr import data_dir\n(data_dir / "executed").touch()')
    result = e.skills.test(v["id"], {"credential": choice})
    assert not result["passed"]
    assert "Could not resolve Vault parameter" in result["error"]
    assert not (e.settings.data / "executed").exists()


def test_required_optional_and_plain_string_parameters(make_app):
    _, e, _ = make_app()
    v = create(e)
    with pytest.raises(ValidationError):
        e.skills.test(v["id"], {})
    item = e.vault.put("Selected", "text", {"value": "private"})
    schema = {
        "type": "object",
        "properties": {
            "credential": {"type": "string", "x-vault": True},
            "unmarked": {"type": "string"},
        },
    }
    result = e.runner.run(
        {
            "id": "optional",
            "parameters": schema,
            "source": """
from fluxyr import secret, output
name = "unmarked"
try:
    secret(name)
except KeyError:
    output({"unmarked_blocked": True})
""",
        },
        {"unmarked": item["id"]},
        "optional",
    )
    assert result["output"] == {"unmarked_blocked": True}


@pytest.mark.parametrize(
    "schema,secrets",
    [
        (
            {
                "type": "object",
                "properties": {"credential": {"type": "object", "x-vault": True}},
            },
            [],
        ),
        (
            {
                "type": "object",
                "properties": {"credential": {"type": "string", "x-vault": "true"}},
            },
            [],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "credential": {"type": "string", "x-vault": True, "default": "id"}
                },
            },
            [],
        ),
        ({"type": "object", "properties": {"nested": SCHEMA}}, []),
        (SCHEMA, ["credential"]),
    ],
)
def test_invalid_or_ambiguous_vault_schema_rejected(schema, secrets):
    with pytest.raises(ValueError):
        vault_parameters(schema, secrets)


def test_dynamic_binding_survives_human_continuation(make_app):
    _, e, _ = make_app()
    item = e.vault.put("Selected", "text", {"value": "continuation-private-value"})
    v = create(
        e,
        """from fluxyr import secret, request_confirmation, output
reply = request_confirmation("Proceed?", key="confirm")
output({"configured": bool(secret("credential")["value"]), "decision": reply["decision"]})
""",
    )
    params = {"credential": item["id"]}
    result = e.skills.test(v["id"], params)
    assert "waiting" in result
    # Credentials are re-resolved privately on replay; only the selector is retained.
    continuation = {
        "responses": [
            {
                "request": {"key": "confirm", "variant": "confirm-reject"},
                "response": {"decision": "approve"},
            }
        ]
    }
    result = e.skills.test(v["id"], params, continuation=continuation)
    assert result["passed"]
    assert result["output"] == {"configured": True, "decision": "approve"}
    assert params == {"credential": item["id"]}


def test_oauth_resolves_by_id_and_keeps_refresh_in_backend(make_app, monkeypatch):
    _, e, _ = make_app()
    item = e.vault.put(
        "OAuth",
        "oauth2",
        {
            "grant_type": "client_credentials",
            "client_id": "client",
            "client_secret": "oauth-private-secret",
            "token_url": "https://example.test/token",
        },
    )
    calls = []

    def token(content, data):
        calls.append(data)
        return {
            **content,
            "access_token": "fresh-private-token",
            "expires_at": 9999999999,
        }

    monkeypatch.setattr(e.vault, "_token", token)
    v = create(
        e,
        'from fluxyr import secret, output\noutput({"configured": bool(secret("credential")["access_token"])})',
    )
    assert e.skills.test(v["id"], {"credential": item["id"]})["passed"]
    assert e.vault.resolve("OAuth")["access_token"] == "fresh-private-token"
    assert calls == [{"grant_type": "client_credentials"}]


def test_passkey_cannot_be_selected_by_action(make_app):
    _, e, _ = make_app()
    with e.db.transaction() as s:
        item = VaultItem(
            name="Passkey",
            type="passkey",
            content=e.vault.encrypt({"private_key": "must-not-leak"}),
        )
        s.add(item)
        s.flush()
        item_id = item.id
    v = create(e)
    result = e.skills.test(v["id"], {"credential": item_id})
    assert not result["passed"]
    assert "must-not-leak" not in json.dumps(result)


@pytest.mark.integration
def test_selected_token_reaches_http_service_without_rebuilding(make_app):
    _, e, _ = make_app()
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b'{"accepted":1}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        v = create(
            e,
            f"""from fluxyr import secret, output
import json, urllib.request
request = urllib.request.Request("http://127.0.0.1:{server.server_port}/metrics", data=b"{{}}",
    headers={{"Authorization": "Bearer " + secret("credential")["value"]}})
with urllib.request.urlopen(request, timeout=5) as response:
    output(json.load(response))
""",
        )
        for name in ("Staging", "New production account"):
            item = e.vault.put(name, "text", {"value": name + "-private-token"})
            result = e.skills.test(v["id"], {"credential": item["id"]})
            assert result["passed"] and result["output"] == {"accepted": 1}
        assert received == [
            "Bearer Staging-private-token",
            "Bearer New production account-private-token",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_oauth_name_and_id_selectors_share_refresh_lock(make_app, monkeypatch):
    _, e, _ = make_app()
    item = e.vault.put(
        "OAuth",
        "oauth2",
        {
            "grant_type": "client_credentials",
            "client_id": "client",
            "client_secret": "private-client-secret",
            "token_url": "https://example.test/token",
        },
    )
    started, release = threading.Event(), threading.Event()
    calls = []

    def token(content, data):
        calls.append(data)
        started.set()
        assert release.wait(5)
        return {**content, "access_token": "refreshed-token", "expires_at": 9999999999}

    monkeypatch.setattr(e.vault, "_token", token)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(e.vault.resolve, "OAuth")
        try:
            assert started.wait(5)
            second = pool.submit(e.vault.resolve_id, item["id"])
        finally:
            release.set()
        assert first.result(timeout=5)["access_token"] == "refreshed-token"
        assert second.result(timeout=5)["access_token"] == "refreshed-token"
    assert len(calls) == 1


def test_private_resolver_error_is_not_exposed(make_app, monkeypatch):
    _, e, _ = make_app()
    v = create(e)

    def failed(item_id):
        raise ValueError("HTTP response contained private-token-sentinel")

    monkeypatch.setattr(e.vault, "resolve_id", failed)
    result = e.skills.test(v["id"], {"credential": "selected-id"})
    assert not result["passed"]
    assert "private-token-sentinel" not in json.dumps(result)
    assert result["phase"] == "credential_resolution" and result["executed"] is False
