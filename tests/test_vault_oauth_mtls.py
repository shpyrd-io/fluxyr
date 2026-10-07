"""Real HTTPS token exchanges against a local server requiring a client certificate."""

import base64
import ipaddress
import json
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import certifi
import pytest
from conftest import execute_next
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from fluxyr.models import VaultItem


@pytest.fixture
def mtls_server(tmp_path, monkeypatch):
    def identity(name, issuer=None, server=False):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        issuer_key, issuer_cert = issuer or (key, None)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer_cert.subject if issuer_cert else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(ca=issuer is None, path_length=None),
                critical=True,
            )
        )
        if issuer:
            cert = cert.add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH
                        if server
                        else ExtendedKeyUsageOID.CLIENT_AUTH
                    ]
                ),
                critical=False,
            )
        if server:
            cert = cert.add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
        return key, cert.sign(issuer_key, hashes.SHA256())

    ca = identity("Test CA")
    server_key, server_cert = identity("Test token endpoint", ca, server=True)
    client_key, client_cert = identity("Test client", ca)
    ca_file, cert_file, key_file = [
        tmp_path / n for n in ("ca.pem", "server.pem", "server.key")
    ]
    # Keep public roots too: action dependency installation and the real-model
    # opt-in still need normal HTTPS while requests trusts this local CA.
    from pathlib import Path

    ca_file.write_bytes(
        Path(certifi.where()).read_bytes()
        + b"\n"
        + ca[1].public_bytes(serialization.Encoding.PEM)
    )
    cert_file.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_file))
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            authorized = self.headers.get("Authorization", "").startswith(
                "Bearer private-token-"
            )
            calls.append(
                {
                    "path": self.path,
                    "authorized": authorized,
                    "peer": self.connection.getpeercert(),
                }
            )
            payload = json.dumps(
                {"disponivel": "1234.56"} if authorized else {"error": "unauthorized"}
            ).encode()
            self.send_response(200 if authorized else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            calls.append(
                {
                    "fields": parse_qs(
                        self.rfile.read(int(self.headers["Content-Length"])).decode()
                    ),
                    "authorization": self.headers.get("Authorization"),
                    "peer": self.connection.getpeercert(),
                }
            )
            payload = json.dumps(
                {
                    "access_token": f"private-token-{len(calls)}",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(cert_file), str(key_file))
    tls.load_verify_locations(str(ca_file))
    tls.verify_mode = ssl.CERT_REQUIRED
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"https://127.0.0.1:{server.server_port}/token",
            client_key,
            client_cert,
            calls,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("kind", ["certificate_pem", "certificate_pfx"])
@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic"])
def test_private_client_credentials_form_uses_real_mtls_and_renews(
    make_app, mtls_server, kind, method
):
    url, key, cert, calls = mtls_server
    app, e, adapter = make_app()
    if kind == "certificate_pem":
        content = {
            "certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "private_key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(b"private-passphrase"),
            ).decode(),
            "passphrase": "private-passphrase",
        }
    else:
        content = {
            "pfx_base64": base64.b64encode(
                pkcs12.serialize_key_and_certificates(
                    b"client",
                    key,
                    cert,
                    None,
                    serialization.BestAvailableEncryption(b"private-passphrase"),
                )
            ).decode(),
            "passphrase": "private-passphrase",
        }
    certificate = e.vault.put("mtls-client", kind, content)
    adapter.replies = [
        [
            (
                "manage_vault_credential",
                {
                    "action": "create",
                    "vault_item_type": "oauth2",
                    "suggested_name": "service",
                    "oauth_grant_type": "client_credentials",
                    "mtls_certificate_id": certificate["id"],
                },
            )
        ],
        "Configured",
    ]
    e.store.enqueue("Configure server-to-server OAuth with mTLS")
    job = execute_next(e)
    assert job["status"] == "waiting"
    entry = job["brain"]["pending_tools"][0]
    assert entry["_result"]["__pua__"]["payload"]["oauth_config"] == {
        "grant_type": "client_credentials",
        "certificate_id": certificate["id"],
    }
    response = app.test_client().post(
        f"/api/jobs/{job['id']}/vault/{entry.get('_request_id') or entry['call_id']}",
        json={
            "name": "service",
            "kind": "oauth2",
            "content": {
                "client_id": "private-client-id",
                "client_secret": "private-client-secret",
                "token_url": url,
                "grant_type": "client_credentials",
                "scope": "weather.read",
                "certificate_id": certificate["id"],
                "token_auth_method": method,
            },
        },
    )
    assert response.status_code == 200, response.json
    assert execute_next(e)["status"] == "succeeded"
    assert "private-client-secret" not in json.dumps(adapter.calls)
    # References use stable IDs; no browser authorization or authorization URL exists.
    e.vault.update(certificate["id"], "renamed-certificate")
    assert e.vault.resolve("service")["access_token"] == "private-token-1"
    assert e.vault.resolve("service")["access_token"] == "private-token-1"
    assert len(calls) == 1 and calls[0]["peer"]
    ready = e.vault.check("service")
    assert ready["ready"] is True
    assert len(calls) == 1  # The readiness probe reuses a valid cached token.
    public = e.vault.list(include_configuration=True)
    metadata_item = next(v for v in public if v["name"] == "service")
    assert metadata_item["oauth_status"] == {
        "token_endpoint_host": "127.0.0.1",
        "certificate_name": "renamed-certificate",
        "authorization_required": False,
    }
    encoded = json.dumps([ready, public])
    assert all(
        value not in encoded
        for value in (
            "private-client-id",
            "private-client-secret",
            "private-token",
            "private-passphrase",
        )
    )
    assert calls[0]["fields"]["grant_type"] == ["client_credentials"]
    assert calls[0]["fields"]["scope"] == ["weather.read"]
    if method == "client_secret_post":
        assert calls[0]["fields"]["client_id"] == ["private-client-id"]
        assert calls[0]["fields"]["client_secret"] == ["private-client-secret"]
        assert not calls[0]["authorization"]
    else:
        assert (
            calls[0]["authorization"]
            == "Basic "
            + base64.b64encode(b"private-client-id:private-client-secret").decode()
        )
        assert "client_secret" not in calls[0]["fields"]
    with e.db.transaction() as db:
        item = db.get(VaultItem, response.json["item"]["vault_item_id"])
        saved = e.vault.decrypt(item.content)
        item.content = e.vault.encrypt({**saved, "expires_at": 0})
    assert e.vault.resolve("service")["access_token"] == "private-token-2"
    assert calls[1]["fields"]["grant_type"] == ["client_credentials"]
    assert not list(e.settings.runtime.glob("tmp*/cert.pem"))
    with pytest.raises(ValueError, match="browser authorization"):
        e.vault.start_oauth("service", "http://localhost/callback")
    metadata = app.test_client().get("/api/vault").text
    assert "client_credentials" in metadata and certificate["id"] in metadata
    assert all(
        value not in metadata
        for value in (
            "private-client-id",
            "private-client-secret",
            "private-token",
            "private-passphrase",
        )
    )
    # A removed reference must fail explicitly, never silently fall back without mTLS.
    with e.db.transaction() as db:
        db.delete(db.get(VaultItem, certificate["id"]))
        item = db.get(VaultItem, response.json["item"]["vault_item_id"])
        item.content = e.vault.encrypt(
            {**e.vault.decrypt(item.content), "expires_at": 0}
        )
    with pytest.raises(ValueError, match="certificate"):
        e.vault.resolve("service")
    assert len(calls) == 2
    e.vault.update(
        response.json["item"]["vault_item_id"], "service", {"certificate_id": ""}
    )
    assert e.vault.get_optional("service")["certificate_id"] == ""


def test_oauth_edit_defaults_preserve_legacy_token_and_changes_invalidate(make_app):
    app, e, _ = make_app()
    item = e.vault.put(
        "legacy",
        "oauth2",
        {
            "client_id": "id",
            "token_url": "https://example.invalid/token",
            "refresh_token": "private-refresh",
            "access_token": "private-access",
        },
    )
    config = app.test_client().get("/api/vault").json[0]["oauth_config"]
    e.vault.update(item["id"], "renamed", config)
    assert e.vault.get_optional("renamed")["access_token"] == "private-access"
    e.vault.update(
        item["id"],
        "renamed",
        {
            "grant_type": "client_credentials",
            "client_secret": "secret",
            "certificate_id": "",
        },
    )
    saved = e.vault.get_optional("renamed")
    assert "access_token" not in saved and "refresh_token" not in saved
    assert saved["certificate_id"] == ""


def test_oauth_rejects_invalid_flow_missing_secret_and_non_certificate(make_app):
    _, e, _ = make_app()
    base = {
        "client_id": "id",
        "client_secret": "secret",
        "token_url": "https://example.invalid/token",
        "grant_type": "client_credentials",
    }
    for override in (
        {"grant_type": "invented"},
        {"client_secret": ""},
        {"token_auth_method": "invented"},
    ):
        with pytest.raises(ValueError):
            e.vault.put("invalid", "oauth2", {**base, **override})
    text = e.vault.put("not-a-cert", "text", {"value": "secret"})
    with pytest.raises(ValueError, match="certificate"):
        e.vault.put("invalid", "oauth2", {**base, "certificate_id": text["id"]})
