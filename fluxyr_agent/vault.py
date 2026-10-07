"""Instance vault with durable encryption key, OAuth refresh and client certificates."""

import base64
import hashlib
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import urlencode

import requests
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from sqlalchemy import select

from .encryption import decrypt_vault_item_content, encrypt_vault_item_content
from .models import OAuthState, VaultItem

TYPES = (
    "text",
    "key_password",
    "oauth2",
    "access_token",
    "certificate_pem",
    "certificate_pfx",
)


class Vault:
    def __init__(self, db):
        self.db = db
        configured = os.getenv("VAULT_ENCRYPTION_KEY")
        path = db.settings.runtime / "vault.key"
        if configured:
            self.key = configured.encode()
        else:
            # Publish a fully written key atomically. Concurrent starts never
            # observe an empty/partial file and never overwrite a winning key.
            fd, temporary = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
            self.key = path.read_bytes()
        Fernet(self.key)  # fail on a malformed key before accepting writes

    def encrypt(self, content):
        return encrypt_vault_item_content(content, self.key)

    def decrypt(self, content):
        return decrypt_vault_item_content(content, self.key)

    def list(self):
        with self.db.transaction() as s:
            return [
                {"id": v.id, "name": v.name, "type": v.type, "created_at": v.created_at}
                for v in s.scalars(select(VaultItem).order_by(VaultItem.name))
            ]

    def validate(self, name, kind, content):
        if kind not in TYPES or not name.strip() or not isinstance(content, dict):
            raise ValueError("Invalid vault item")
        if kind == "certificate_pem":
            x509.load_pem_x509_certificate(content["certificate"].encode())
            serialization.load_pem_private_key(
                content["private_key"].encode(),
                password=(content.get("passphrase") or "").encode() or None,
            )
        elif kind == "certificate_pfx":
            pkcs12.load_key_and_certificates(
                base64.b64decode(content["pfx_base64"]),
                (content.get("passphrase") or "").encode() or None,
            )

    def put(self, name, kind, content):
        self.validate(name, kind, content)
        with self.db.transaction() as s:
            item = s.scalar(
                select(VaultItem).where(VaultItem.name == name).with_for_update()
            )
            if not item:
                item = VaultItem(name=name)
                s.add(item)
            item.type = kind
            item.content = self.encrypt(content)
            s.flush()
            return {"id": item.id, "name": name, "type": kind}

    def update(self, item_id, name, content=None):
        with self.db.transaction() as s:
            item = s.get(VaultItem, item_id, with_for_update=True)
            if not item:
                raise ValueError("Vault item not found")
            if content is not None and not isinstance(content, dict):
                raise ValueError("Content must be an object")
            values = {**self.decrypt(item.content), **(content or {})}
            self.validate(name, item.type, values)
            item.name, item.content = name.strip(), self.encrypt(values)
            s.flush()
            return {"id": item.id, "name": item.name, "type": item.type}

    def get_optional(self, name):
        with self.db.transaction() as s:
            item = s.scalar(select(VaultItem).where(VaultItem.name == name))
            return self.decrypt(item.content) if item else None

    def resolve(self, name):
        with self.db.transaction() as s:
            item = s.scalar(
                select(VaultItem).where(VaultItem.name == name).with_for_update()
            )
            if not item:
                raise ValueError(f"Vault item not found: {name}")
            content = self.decrypt(item.content)
            if item.type == "oauth2" and (
                not content.get("access_token")
                or content.get("expires_at", 0) <= time.time() + 60
            ):
                grant = (
                    "refresh_token"
                    if content.get("refresh_token")
                    else content.get("grant_type", "authorization_code")
                )
                if grant == "authorization_code":
                    raise ValueError(f"Connect OAuth for {name} in Vault first")
                data = {"grant_type": grant}
                if grant == "refresh_token":
                    data["refresh_token"] = content["refresh_token"]
                if content.get("scope"):
                    data["scope"] = content["scope"]
                content = self._token(content, data)
                item.content = self.encrypt(content)
            return content

    def _token(self, content, data):
        auth = None
        if (
            content.get("token_auth_method", "client_secret_post")
            == "client_secret_basic"
        ):
            auth = (content["client_id"], content.get("client_secret", ""))
        else:
            data = {**data, "client_id": content["client_id"]}
            if content.get("client_secret"):
                data["client_secret"] = content["client_secret"]
        with self.certificate(content.get("certificate_name")) as cert:
            response = requests.post(
                content["token_url"], data=data, auth=auth, cert=cert, timeout=30
            )
            if not response.ok:
                raise ValueError(
                    f"OAuth token endpoint returned HTTP {response.status_code}"
                )
            token = response.json()
        if not token.get("access_token"):
            raise ValueError("OAuth response is missing access_token")
        return {
            **content,
            **token,
            "expires_at": time.time() + int(token.get("expires_in", 3600)),
        }

    def start_oauth(self, name, redirect_uri):
        with self.db.transaction() as s:
            item = s.scalar(select(VaultItem).where(VaultItem.name == name))
            if not item or item.type != "oauth2":
                raise ValueError("OAuth item not found")
            content = self.decrypt(item.content)
            verifier = secrets.token_urlsafe(48)
            state = secrets.token_urlsafe(32)
            s.add(
                OAuthState(
                    id=state,
                    vault_id=item.id,
                    expires_at=time.time() + 600,
                    payload=self.encrypt(
                        {"verifier": verifier, "redirect_uri": redirect_uri}
                    ),
                )
            )
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            params = {
                "response_type": "code",
                "client_id": content["client_id"],
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            if content.get("scope"):
                params["scope"] = content["scope"]
            return (
                content["authorization_url"]
                + ("&" if "?" in content["authorization_url"] else "?")
                + urlencode(params)
            )

    def finish_oauth(self, state, code):
        with self.db.transaction() as s:
            row = s.get(OAuthState, state, with_for_update=True)
            if not row or row.expires_at < time.time():
                raise ValueError("OAuth state invalid or expired")
            payload = self.decrypt(row.payload)
            item = s.get(VaultItem, row.vault_id, with_for_update=True)
            content = self._token(
                self.decrypt(item.content),
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": payload["verifier"],
                    "redirect_uri": payload["redirect_uri"],
                },
            )
            item.content = self.encrypt(content)
            s.delete(row)
            return item.name

    @contextmanager
    def certificate(self, name):
        if not name:
            yield None
            return
        content = self.get_optional(name)
        if not content:
            raise ValueError("Certificate not found")
        if content.get("pfx_base64"):
            key, cert, chain = pkcs12.load_key_and_certificates(
                base64.b64decode(content["pfx_base64"]),
                (content.get("passphrase") or "").encode() or None,
            )
            pem = cert.public_bytes(serialization.Encoding.PEM) + b"".join(
                c.public_bytes(serialization.Encoding.PEM) for c in chain or []
            )
        else:
            pem = content["certificate"].encode()
            key = serialization.load_pem_private_key(
                content["private_key"].encode(),
                (content.get("passphrase") or "").encode() or None,
            )
        with tempfile.TemporaryDirectory(dir=self.db.settings.runtime) as directory:
            from pathlib import Path

            certpath, keypath = (
                Path(directory) / "cert.pem",
                Path(directory) / "key.pem",
            )
            certpath.write_bytes(pem)
            keypath.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            certpath.chmod(0o600)
            keypath.chmod(0o600)
            yield (str(certpath), str(keypath))
