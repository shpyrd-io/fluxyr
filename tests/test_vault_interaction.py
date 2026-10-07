"""Private credential input must never become a tool argument/result or chat event."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import execute_next
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from fluxyr.database import row_dict
from fluxyr.models import (
    Decision,
    Effect,
    Event,
    Job,
    Message,
    Session,
    VaultItem,
)

TOOL = "manage_vault_credential"
SECRET = "private-credential-sentinel-7d881b"


def request_card(make_app, args=None):
    app, engine, adapter = make_app(
        [
            [
                (
                    TOOL,
                    args
                    or {
                        "action": "create",
                        "vault_item_type": "text",
                        "suggested_name": "weather",
                    },
                )
            ],
            "Saved",
        ]
    )
    engine.store.enqueue("Configure a credential")
    job = execute_next(engine)
    assert job["status"] == "waiting", job["error"]
    entry = job["brain"]["pending_tools"][0]
    assert entry["_result"]["__pua__"]["payload"]["kind"] == "vault_credential"
    return app, engine, adapter, job, entry


def endpoint(job, entry):
    return f"/api/jobs/{job['id']}/vault/{entry.get('_request_id') or entry['call_id']}"


def assert_private(engine, adapter, *values):
    with engine.db.transaction() as db:
        stored = [
            row_dict(row)
            for table in (Job, Message, Session, Event, Decision, Effect, VaultItem)
            for row in db.scalars(select(table))
        ]
    public = json.dumps([stored, adapter.calls], default=str)
    for value in values:
        assert value not in public


def test_create_resume_only_returns_reference_and_retries_do_not_rewrite(make_app):
    app, e, adapter, job, entry = request_card(make_app)
    body = {"name": "weather", "kind": "text", "content": {"value": SECRET}}
    response = app.test_client().post(endpoint(job, entry), json=body)
    assert response.status_code == 200, response.json
    assert SECRET not in response.text
    assert response.json["status"] == "queued"
    item_id = response.json["item"]["vault_item_id"]
    duplicate = app.test_client().post(
        endpoint(job, entry), json={**body, "content": {"value": "overwrite-attempt"}}
    )
    assert duplicate.json["duplicate"]
    assert e.vault.resolve("weather")["value"] == SECRET
    final = execute_next(e)
    assert final["status"] == "succeeded", final["error"]
    tool_results = [m for m in adapter.calls[-1] if m["role"] == "tool"]
    assert json.loads(tool_results[-1]["content"]) == {
        "action": "create",
        "vault_item_id": item_id,
        "vault_item_name": "weather",
        "status": "completed",
    }
    assert_private(e, adapter, SECRET, "overwrite-attempt")
    action = e.runner.run(
        {
            "id": "uses-vault",
            "source": 'from fluxyr import secret, output\noutput({"configured": bool(secret("weather")["value"])})',
            "secrets": ["weather"],
        },
        {},
        "runtime-vault-test",
    )
    assert action["success"] and action["output"] == {"configured": True}


def test_edit_reuses_saved_type_and_keeps_omitted_fields(make_app):
    app, e, adapter = make_app()
    item = e.vault.put(
        "account", "key_password", {"key": "private-user", "password": SECRET}
    )
    adapter.replies = [
        [(TOOL, {"action": "edit", "vault_item_id": item["id"]})],
        "Updated",
    ]
    e.store.enqueue("Update password")
    job = execute_next(e)
    entry = job["brain"]["pending_tools"][0]
    payload = entry["_result"]["__pua__"]["payload"]
    assert (
        payload["vault_item_type"] == "key_password"
        and payload["suggested_name"] == "account"
    )
    response = app.test_client().post(
        endpoint(job, entry),
        json={
            "name": "account",
            "kind": "key_password",
            "content": {"password": "replacement-private"},
        },
    )
    assert response.status_code == 200, response.json
    assert e.vault.resolve("account") == {
        "key": "private-user",
        "password": "replacement-private",
    }
    assert execute_next(e)["status"] == "succeeded"
    assert_private(e, adapter, SECRET, "private-user", "replacement-private")


def test_generic_decisions_cannot_complete_vault_or_persist_arbitrary_secret(make_app):
    app, e, adapter, job, entry = request_card(make_app)
    route = f"/api/jobs/{job['id']}/decisions/{entry['call_id']}"
    for decision in ("complete", "approve"):
        response = app.test_client().post(
            route, json={"decision": decision, "result": {"value": SECRET}}
        )
        assert response.status_code == 400 and SECRET not in response.text
    response = app.test_client().post(
        route,
        json={"decision": "reject", "result": {"password": SECRET}, "reason": SECRET},
    )
    assert response.status_code == 200, response.json
    assert execute_next(e)["status"] == "succeeded"
    assert e.vault.list() == []
    assert_private(e, adapter, SECRET)


def test_stale_wrong_request_and_wrong_type_do_not_save(make_app):
    app, e, adapter, job, entry = request_card(make_app)
    route = endpoint(job, entry)
    body = {"name": "weather", "kind": "text", "content": {"value": SECRET}}
    assert app.test_client().post(route + "-wrong", json=body).status_code == 400
    assert (
        app.test_client().post(route, json={**body, "kind": "access_token"}).status_code
        == 400
    )
    assert (
        app.test_client().post(route, json={**body, "content": {}}).status_code == 400
    )
    e.store.control(job["id"], "cancel")
    assert app.test_client().post(route, json=body).status_code == 400
    assert not e.vault.list()
    assert_private(e, adapter, SECRET)


def test_save_and_decision_rollback_together(make_app, monkeypatch):
    app, e, _, job, entry = request_card(make_app)

    def fail(*args):
        raise ValueError("Decision unavailable")

    monkeypatch.setattr(e.store, "_record_decision", fail)
    response = app.test_client().post(
        endpoint(job, entry),
        json={"name": "weather", "kind": "text", "content": {"value": SECRET}},
    )
    assert response.status_code == 400 and not e.vault.list()
    with e.db.transaction() as db:
        assert db.get(Job, job["id"]).status == "waiting"
        assert not list(db.scalars(select(Decision)))


def test_two_requests_only_resume_after_both_decisions_and_survive_restart(make_app):
    _app, e, _ = make_app(
        [
            [
                (
                    TOOL,
                    {
                        "action": "create",
                        "vault_item_type": "text",
                        "suggested_name": name,
                    },
                )
                for name in ("one", "two")
            ]
        ]
    )
    e.store.enqueue("Configure both")
    job = execute_next(e)
    first, second = job["brain"]["pending_tools"]
    app2, e2, adapter = make_app(["Both configured"])
    response = app2.test_client().post(
        endpoint(job, first),
        json={"name": "one", "kind": "text", "content": {"value": SECRET}},
    )
    assert response.json["status"] == "waiting" and response.json["remaining"] == 1
    response = app2.test_client().post(
        endpoint(job, second),
        json={"name": "two", "kind": "text", "content": {"value": SECRET + "-two"}},
    )
    assert response.json["status"] == "queued"
    assert execute_next(e2)["status"] == "succeeded"
    assert_private(e2, adapter, SECRET)


@pytest.mark.parametrize("kind", ["certificate_pem", "certificate_pfx"])
def test_certificates_use_private_form_and_invalid_content_never_leaks(make_app, kind):
    app, e, adapter, job, entry = request_card(
        make_app,
        {"action": "create", "vault_item_type": kind, "suggested_name": "client-cert"},
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.invalid")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    if kind == "certificate_pem":
        content = {
            "certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "private_key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        }
        secret = content["private_key"]
    else:
        secret = base64.b64encode(
            pkcs12.serialize_key_and_certificates(
                b"test",
                key,
                cert,
                None,
                serialization.BestAvailableEncryption(SECRET.encode()),
            )
        ).decode()
        content = {"pfx_base64": secret, "passphrase": SECRET}
    invalid = {k: SECRET for k in content}
    result = app.test_client().post(
        endpoint(job, entry),
        json={"name": "client-cert", "kind": kind, "content": invalid},
    )
    assert result.status_code == 400 and SECRET not in result.text
    result = app.test_client().post(
        endpoint(job, entry),
        json={"name": "client-cert", "kind": kind, "content": content},
    )
    assert result.status_code == 200, result.json
    assert execute_next(e)["status"] == "succeeded"
    assert_private(e, adapter, SECRET, secret)


def test_create_request_cannot_overwrite_existing_item(make_app):
    app, e, adapter, job, entry = request_card(make_app)
    e.vault.put("weather", "text", {"value": "keep-original"})
    response = app.test_client().post(
        endpoint(job, entry),
        json={"name": "weather", "kind": "text", "content": {"value": SECRET}},
    )
    assert response.status_code == 400
    assert e.vault.resolve("weather")["value"] == "keep-original"
    assert_private(e, adapter, SECRET)


@pytest.mark.parametrize("grant", ["authorization_code", "client_credentials"])
def test_public_oauth_prefill_is_reviewed_before_private_save(make_app, grant):
    config = {
        "token_url": "https://api.example.com/token",
        "scope": "weather.read",
        "token_auth_method": "client_secret_basic",
    }
    if grant == "authorization_code":
        config["authorization_url"] = "https://api.example.com/authorize"
    app, e, adapter, job, entry = request_card(
        make_app,
        {
            "action": "create",
            "vault_item_type": "oauth2",
            "suggested_name": "weather",
            "oauth_grant_type": grant,
            "oauth_config": config,
        },
    )
    assert e.vault.list() == []
    assert entry["_result"]["__pua__"]["payload"]["oauth_config"] == {
        **config,
        "grant_type": grant,
    }
    body = {
        "name": "weather",
        "kind": "oauth2",
        "content": {
            **config,
            "grant_type": grant,
            "scope": "weather.read alerts.read",
            "client_id": SECRET + "-id",
            "client_secret": SECRET,
        },
    }
    response = app.test_client().post(endpoint(job, entry), json=body)
    assert response.status_code == 200, response.json
    assert e.vault.get_optional("weather")["scope"] == "weather.read alerts.read"
    assert execute_next(e)["status"] == "succeeded"
    assert_private(e, adapter, SECRET)


def test_oauth_edit_prefill_preserves_saved_credential_until_submit(make_app):
    app, e, adapter = make_app()
    item = e.vault.put(
        "weather",
        "oauth2",
        {
            "grant_type": "client_credentials",
            "client_id": SECRET + "-id",
            "client_secret": SECRET,
            "token_url": "https://api.example.com/old-token",
        },
    )
    adapter.replies = [
        [
            (
                TOOL,
                {
                    "action": "edit",
                    "vault_item_id": item["id"],
                    "oauth_config": {"token_url": "https://api.example.com/new-token"},
                },
            )
        ],
        "Updated",
    ]
    e.store.enqueue("Update OAuth endpoint")
    job = execute_next(e)
    entry = job["brain"]["pending_tools"][0]
    config = entry["_result"]["__pua__"]["payload"]["oauth_config"]
    assert config["grant_type"] == "client_credentials"
    assert config["token_url"] == "https://api.example.com/new-token"
    assert e.vault.get_optional("weather")["token_url"].endswith("old-token")
    response = app.test_client().post(
        endpoint(job, entry),
        json={
            "name": "weather",
            "kind": "oauth2",
            "content": config,
        },
    )
    assert response.status_code == 200, response.json
    saved = e.vault.get_optional("weather")
    assert saved["token_url"].endswith("new-token")
    assert saved["client_secret"] == SECRET
    assert execute_next(e)["status"] == "succeeded"
    assert_private(e, adapter, SECRET)
