"""Instance vault with durable encryption key, OAuth refresh and client certificates."""

import base64
import hashlib
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

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
    "totp",
    "passkey",
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

    def list(self, *, include_configuration=False):
        with self.db.transaction() as s:
            return [
                {
                    "id": v.id,
                    "name": v.name,
                    "type": v.type,
                    "created_at": v.created_at,
                    **({"passkey_config": {k: self.decrypt(v.content).get(k) for k in ("origin", "state")}} if v.type == "passkey" else {}),
                    **(
                        {
                            "oauth_config": self.oauth_configuration(v, s),
                            "oauth_status": self.oauth_status(v, s),
                        }
                        if include_configuration and v.type == "oauth2"
                        else {}
                    ),
                }
                for v in s.scalars(select(VaultItem).order_by(VaultItem.name))
            ]

    def oauth_configuration(self, item, session):
        """Only selectors may leave the vault; never return IDs/secrets/tokens from content."""
        content = self.decrypt(item.content)
        certificate_id = content.get("certificate_id") or ""
        if not certificate_id and content.get("certificate_name"):
            certificate = session.scalar(
                select(VaultItem).where(VaultItem.name == content["certificate_name"])
            )
            certificate_id = certificate.id if certificate else ""
        return {
            "grant_type": content.get("grant_type", "authorization_code"),
            "token_auth_method": content.get("token_auth_method", "client_secret_post"),
            "certificate_id": certificate_id,
        }

    def oauth_status(self, item, session):
        content = self.decrypt(item.content)
        config = self.oauth_configuration(item, session)
        certificate = (
            session.get(VaultItem, config["certificate_id"])
            if config["certificate_id"]
            else None
        )
        return {
            "token_endpoint_host": urlsplit(content.get("token_url", "")).hostname,
            "certificate_name": certificate.name if certificate else None,
            "authorization_required": config["grant_type"] == "authorization_code"
            and not (content.get("access_token") or content.get("refresh_token")),
        }

    def check(self, name):
        """Resolve credentials/token once, returning evidence but never their values."""
        metadata = next(
            (v for v in self.list(include_configuration=True) if v["name"] == name),
            None,
        )
        if not metadata:
            return {
                "ready": False,
                "credential": name,
                "phase": "credential_resolution",
                "error": "Vault item not found",
                "executed": False,
            }
        try:
            if metadata["type"] == "passkey":
                with self.db.transaction() as db:
                    item = db.get(VaultItem, metadata["id"])
                    ready = bool(self.decrypt(item.content).get("credential"))
                return {"ready": ready, "vault_item_id": metadata["id"], "type": "passkey", "note": "Use browser_use_passkey; private key export is unavailable."}
            self.resolve(name)
        except Exception as exc:  # noqa: BLE001 - private diagnostics must not expose raw HTTP errors
            return {
                "ready": False,
                "credential": name,
                "vault_item_id": metadata["id"],
                "phase": "credential_resolution",
                "error_type": type(exc).__name__,
                "error": "Credential resolution failed. Check the private Vault configuration, token endpoint and certificate; no action was executed.",
                "oauth_status": metadata.get("oauth_status"),
                "executed": False,
                "remediation": "Edit this item with manage_vault_credential, then check it again. Do not rebuild an action for this failure.",
            }
        return {
            "ready": True,
            "credential": name,
            "vault_item_id": metadata["id"],
            "type": metadata["type"],
            "note": "Credential resolved successfully; this does not test resource API permissions or the action.",
        }

    def _oauth_content(self, session, content):
        values = dict(content)
        certificate_id = values.get("certificate_id")
        if certificate_id:
            certificate = session.get(VaultItem, certificate_id)
        elif values.get("certificate_name"):
            certificate = session.scalar(
                select(VaultItem).where(VaultItem.name == values["certificate_name"])
            )
        else:
            return values
        if not certificate or certificate.type not in (
            "certificate_pem",
            "certificate_pfx",
        ):
            raise ValueError("Choose an existing PEM or PFX certificate from Vault")
        values["certificate_id"] = certificate.id
        values.pop("certificate_name", None)
        return values

    def validate(self, name, kind, content):
        if kind == "passkey":
            raise ValueError("Passkeys are managed by the browser enrollment flow")
        if (
            kind not in TYPES
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(content, dict)
        ):
            raise ValueError("Invalid vault item")
        if kind == "totp":
            from .otp import configuration

            normalized = configuration(content)
            content.clear()
            content.update(normalized)
        required = {
            "totp": ("secret",),
            "text": ("value",),
            "key_password": ("key", "password"),
            "access_token": ("access_token",),
            "oauth2": ("client_id", "token_url"),
            "certificate_pem": ("certificate", "private_key"),
            "certificate_pfx": ("pfx_base64",),
        }
        if any(
            not isinstance(content.get(key), str) or not content[key].strip()
            for key in required[kind]
        ):
            raise ValueError("Required credential fields are missing")
        if kind == "oauth2":
            grant = content.get("grant_type", "authorization_code")
            if grant not in ("authorization_code", "client_credentials"):
                raise ValueError("Unsupported OAuth grant type")
            if content.get("token_auth_method", "client_secret_post") not in (
                "client_secret_post",
                "client_secret_basic",
            ):
                raise ValueError("Unsupported token authentication method")
            if grant == "client_credentials":
                if (
                    not isinstance(content.get("client_secret"), str)
                    or not content["client_secret"].strip()
                ):
                    raise ValueError("Client credentials requires a client secret")
            elif not any(
                content.get(key)
                for key in ("authorization_url", "refresh_token", "access_token")
            ):
                raise ValueError("Authorization code requires an authorization URL")
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
        with self.db.transaction() as session:
            return self._put(session, name, kind, content)

    def _put(self, session, name, kind, content, *, create_only=False):
        if kind == "passkey":
            raise ValueError("Register passkeys through browser_register_passkey, not a manual Vault form")
        self.validate(name, kind, content)
        if kind == "oauth2":
            content = self._oauth_content(session, content)
            if content.get("grant_type") == "client_credentials":
                content.pop("authorization_url", None)
                content.pop("refresh_token", None)
        name = name.strip()
        item = session.scalar(
            select(VaultItem).where(VaultItem.name == name).with_for_update()
        )
        if item and item.type == "passkey":
            raise ValueError("A saved passkey cannot be overwritten with another credential")
        if item and create_only:
            raise ValueError(
                "A Vault item with this name already exists; choose another name or edit it"
            )
        if not item:
            item = VaultItem(name=name)
            session.add(item)
        item.type, item.content = kind, self.encrypt(content)
        session.flush()
        return {"id": item.id, "name": item.name, "type": item.type}

    def update(self, item_id, name, content=None):
        with self.db.transaction() as session:
            return self._update(session, item_id, name, content)

    def _update(self, session, item_id, name, content=None):
        item = session.get(VaultItem, item_id, with_for_update=True)
        if not item:
            raise ValueError("Vault item not found")
        if item.type == "passkey":
            if content or not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
                raise ValueError("Passkeys allow renaming only; enroll a new credential in the browser to replace one")
            item.name = name.strip()
            session.flush()
            return {"id": item.id, "name": item.name, "type": item.type}
        if content is not None and not isinstance(content, dict):
            raise ValueError("Content must be an object")
        old = self.decrypt(item.content)
        if item.type == "oauth2":
            # Read selector defaults without requiring the old certificate to still
            # exist: editing must allow replacing/removing a broken reference.
            old = {**old, **self.oauth_configuration(item, session)}
        values = {**old, **(content or {})}
        if item.type == "oauth2":
            if "certificate_id" in (content or {}):
                values.pop("certificate_name", None)
            if any(old.get(key) != value for key, value in (content or {}).items()):
                # Credentials/configuration changed: never reuse a token issued for the old identity.
                for key in (
                    "access_token",
                    "refresh_token",
                    "expires_at",
                    "token_type",
                ):
                    values.pop(key, None)
            values = self._oauth_content(session, values)
            if values.get("grant_type") == "client_credentials":
                values.pop("authorization_url", None)
        self.validate(name, item.type, values)
        item.name, item.content = name.strip(), self.encrypt(values)
        session.flush()
        return {"id": item.id, "name": item.name, "type": item.type}

    def get_optional(self, name):
        with self.db.transaction() as s:
            item = s.scalar(select(VaultItem).where(VaultItem.name == name))
            if item and item.type == "passkey":
                raise ValueError("Passkey private keys are available only to the browser authenticator")
            return self.decrypt(item.content) if item else None

    def resolve(self, name):
        if self.db.sqlite:
            return self._resolve_sqlite(name)
        with self.db.transaction() as s:
            item = s.scalar(
                select(VaultItem).where(VaultItem.name == name).with_for_update()
            )
            if not item:
                raise ValueError(f"Vault item not found: {name}")
            if item.type == "passkey":
                raise ValueError("Use browser_use_passkey for this credential")
            content = self.decrypt(item.content)
            if item.type == "oauth2" and (
                not content.get("access_token")
                or content.get("expires_at", 0) <= time.time() + 60
            ):
                grant = content.get("grant_type", "authorization_code")
                if grant != "client_credentials" and content.get("refresh_token"):
                    grant = "refresh_token"
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

    def _resolve_sqlite(self, name):
        from .runtime.python_runner import lock_for

        # SQLite has one writer. Keep network token renewal outside its write
        # transaction so the UI, cancellation and other jobs remain responsive.
        with lock_for(("vault-oauth", str(self.db.engine.url), name)):
            with self.db.transaction() as s:
                item = s.scalar(select(VaultItem).where(VaultItem.name == name))
                if not item:
                    raise ValueError(f"Vault item not found: {name}")
                if item.type == "passkey":
                    raise ValueError("Use browser_use_passkey for this credential")
                original = item.content
                item_id = item.id
                content = self.decrypt(original)
                if item.type != "oauth2" or (
                    content.get("access_token")
                    and content.get("expires_at", 0) > time.time() + 60
                ):
                    return content
            grant = content.get("grant_type", "authorization_code")
            if grant != "client_credentials" and content.get("refresh_token"):
                grant = "refresh_token"
            if grant == "authorization_code":
                raise ValueError(f"Connect OAuth for {name} in Vault first")
            data = {"grant_type": grant}
            if grant == "refresh_token":
                data["refresh_token"] = content["refresh_token"]
            if content.get("scope"):
                data["scope"] = content["scope"]
            refreshed = self._token(content, data)
            with self.db.transaction() as s:
                item = s.get(VaultItem, item_id)
                if item is None or item.content != original:
                    raise ValueError(
                        "Credential changed during token renewal; retry the action"
                    )
                item.content = self.encrypt(refreshed)
            return refreshed

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
        with self.certificate(
            content.get("certificate_name"), item_id=content.get("certificate_id")
        ) as cert:
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
            if content.get("grant_type") == "client_credentials":
                raise ValueError(
                    "Client credentials does not use browser authorization"
                )
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
    def certificate(self, name=None, *, item_id=None):
        if not name and not item_id:
            yield None
            return
        with self.db.transaction() as s:
            item = (
                s.get(VaultItem, item_id)
                if item_id
                else s.scalar(select(VaultItem).where(VaultItem.name == name))
            )
            if not item or item.type not in ("certificate_pem", "certificate_pfx"):
                raise ValueError(
                    "Referenced mTLS certificate is missing or not a certificate"
                )
            content = self.decrypt(item.content)
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
