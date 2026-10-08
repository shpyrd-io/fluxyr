"""Vault-managed WebAuthn enrollment and one-shot assertions over local pipes."""

import base64
import time
import uuid
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.serialization import load_der_private_key
from sqlalchemy import select

from .browser import BrowserError
from .models import VaultItem
from .runtime.python_runner import lock_for


def validate_origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (
            parsed.scheme != "https"
            and parsed.hostname not in ("localhost", "127.0.0.1", "[::1]", "::1")
        )
    ):
        raise BrowserError(
            "Passkeys require an exact HTTPS origin (HTTP localhost is allowed)"
        )
    return value.rstrip("/")


class Passkeys:
    def __init__(self, browsers):
        self.browsers = browsers
        self.engine = browsers.engine

    def prepare(self, session_id, origin, ref=None, selector=None, stop=lambda: False):
        destination = validate_origin(origin)
        if bool(ref) == bool(selector):
            raise BrowserError("Identify the exact passkey button with ref OR selector")
        driver, _, generation = self.browsers._get(session_id)
        target = driver.call(
            "passkey_prepare",
            target={"ref": ref, "selector": selector},
            origin=destination,
            stop=stop,
        )
        return {**target, "session_id": session_id, "generation": generation}

    def _item(self, item_id):
        with self.engine.db.transaction() as db:
            item = db.get(VaultItem, item_id)
            if not item or item.type != "passkey":
                raise BrowserError("Choose an existing passkey from Vault")
            return item.name, self.engine.vault.decrypt(item.content)

    def recovery_target(self, session_id, item_id, destination):
        _, content = self._item(item_id)
        _, _, generation = self.browsers._get(session_id, create=False)
        if (
            content["origin"] != validate_origin(destination)
            or content.get("generation") != generation
        ):
            raise BrowserError("Unsaved passkey is no longer in this browser")
        return {
            "session_id": session_id,
            "generation": generation,
            "origin": content["origin"],
        }

    def _persist(self, item_id, destination, credential):
        # Strictly validate the private CDP record; errors never echo key material.
        try:
            host = urlsplit(destination).hostname
            rp = credential["rpId"]
            if not rp or not (host == rp or host.endswith("." + rp)):
                raise ValueError()
            for field in ("credentialId", "privateKey", "userHandle"):
                value = credential[field]
                if not isinstance(value, str) or not 0 < len(value) <= 20000:
                    raise ValueError()
                base64.b64decode(value, validate=True)
            load_der_private_key(
                base64.b64decode(credential["privateKey"]), password=None
            )
            if not credential.get("isResidentCredential"):
                raise ValueError()
            count = credential["signCount"]
            if type(count) is not int or count < 0:
                raise ValueError()
        except Exception:  # noqa: BLE001 - normalize malformed private records without echoing them
            raise BrowserError("Invalid passkey record from browser") from None
        with self.engine.db.transaction() as db:
            item = db.get(VaultItem, item_id, with_for_update=True)
            if not item or item.type != "passkey":
                raise BrowserError("Passkey item was removed")
            content = self.engine.vault.decrypt(item.content)
            if content["origin"] != destination:
                raise BrowserError("Passkey destination changed")
            old = content.get("credential")
            if old and (
                old["credentialId"] != credential["credentialId"]
                or old["privateKey"] != credential["privateKey"]
            ):
                raise BrowserError("Passkey identity changed")
            # Keep the highest reserved count when assertions run in separate browsers.
            credential = {
                **credential,
                "signCount": max(count, (old or {}).get("signCount", 0)),
            }
            item.content = self.engine.vault.encrypt(
                {
                    **content,
                    "credential": credential,
                    "state": "saved",
                    "updated_at": time.time(),
                }
            )

    def register(self, target, name, request_key, *, item_id=None, stop=lambda: False):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
            raise BrowserError("Give this passkey a name of 1–200 characters")
        driver, _, generation = self.browsers._get(target["session_id"], create=False)
        if target["generation"] != generation:
            raise BrowserError(
                "Browser was restarted; request passkey enrollment again"
            )
        recover = bool(item_id)
        item_id = item_id or str(
            uuid.uuid5(uuid.NAMESPACE_URL, "fluxyr-passkey:" + request_key)
        )
        with lock_for(("passkey", str(self.engine.db.engine.url), item_id)):
            if not recover:
                with self.engine.db.transaction() as db:
                    item = db.get(VaultItem, item_id)
                    if item:
                        raise BrowserError(
                            "Enrollment already attempted; recover the saved record instead of registering again"
                        )
                    if db.scalar(
                        select(VaultItem.id).where(VaultItem.name == name.strip())
                    ):
                        raise BrowserError("A Vault item already uses this name")
                    db.add(
                        VaultItem(
                            id=item_id,
                            name=name.strip(),
                            type="passkey",
                            content=self.engine.vault.encrypt(
                                {
                                    "origin": target["origin"],
                                    "state": "pending",
                                    "generation": generation,
                                }
                            ),
                        )
                    )
            else:
                _, content = self._item(item_id)
                if (
                    content["origin"] != target["origin"]
                    or content.get("generation") != generation
                ):
                    raise BrowserError("Passkey recovery belongs to another browser")
            try:
                outcome = driver.call(
                    "passkey_recover" if recover else "passkey_register",
                    target_id=target.get("target_id"),
                    origin=target["origin"],
                    vault_item_id=item_id,
                    persist=lambda credential: self._persist(
                        item_id, target["origin"], credential
                    ),
                    stop=stop,
                )
            except BrowserError:
                outcome = {"saved": False, "recoverable": False}
            return {
                **outcome,
                "vault_item_id": item_id,
                "note": "Passkey encrypted in Vault. Inspect the site to confirm registration was accepted."
                if outcome.get("saved")
                else "Passkey enrollment was not confirmed saved. Inspect the site and Vault; do not repeat registration blindly. If recoverable, request enrollment recovery using this Vault item ID while the browser remains open.",
            }

    def authenticate(
        self,
        session_id,
        item_id,
        destination,
        ref=None,
        selector=None,
        stop=lambda: False,
    ):
        destination = validate_origin(destination)
        with lock_for(("passkey", str(self.engine.db.engine.url), item_id)):
            _, content = self._item(item_id)
            if content["origin"] != destination or not content.get("credential"):
                raise BrowserError("Passkey is not saved for this exact origin")
            target = self.prepare(session_id, destination, ref, selector, stop)
            driver, _, generation = self.browsers._get(session_id, create=False)
            if generation != target["generation"]:
                raise BrowserError("Browser changed")

            def resolve():
                # Reserve before signing; a crash can skip a counter, never reuse it.
                with self.engine.db.transaction() as db:
                    item = db.get(VaultItem, item_id, with_for_update=True)
                    if not item or item.type != "passkey":
                        raise BrowserError("Passkey was removed")
                    current = self.engine.vault.decrypt(item.content)
                    if current["origin"] != destination:
                        raise BrowserError("Passkey destination changed")
                    credential = dict(current["credential"])
                    if credential["signCount"] >= 2**32 - 1:
                        raise BrowserError(
                            "Passkey counter exhausted; register a replacement"
                        )
                    current["credential"] = {
                        **credential,
                        "signCount": credential["signCount"] + 1,
                    }
                    item.content = self.engine.vault.encrypt(current)
                    return credential

            result = driver.call(
                "passkey_authenticate",
                target_id=target["target_id"],
                origin=destination,
                vault_item_id=item_id,
                secret=resolve,
                persist=lambda credential: self._persist(
                    item_id, destination, credential
                ),
                stop=stop,
            )
            return {
                **result,
                "vault_item_id": item_id,
                "note": "WebAuthn assertion generated. Inspect the site to confirm login; an assertion alone does not prove the server accepted it.",
            }
