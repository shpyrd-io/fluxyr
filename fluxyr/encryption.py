"""Encryption utilities for sensitive data.

New vault items are encrypted with v2 envelope encryption:
  - A random 32-byte AES-256 DEK encrypts the content via AES-256-GCM.
  - The DEK is wrapped by a Fernet KEK (VAULT_ENCRYPTION_KEY env var).
  - The stored value is a base64-encoded JSON envelope.

Legacy v1 vault items (raw Fernet tokens) are still decryptable for backward
compatibility — no data migration required.
"""

import base64
import json
import logging
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_V2_VERSION = 2
_V2_AAD = b"v2"


def _get_kek() -> bytes:
    key = os.environ.get("VAULT_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Vault key must be explicitly initialized")
    return key.encode()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encrypt_vault_item_content(content: dict, key: bytes | None = None) -> str:
    """Encrypt vault item content using AES-256-GCM envelope encryption (v2).

    Generates a random DEK per vault item, encrypts the content, and wraps
    the DEK with the global KEK via Fernet.

    Args:
        content: Dictionary containing vault item data.

    Returns:
        Base64-encoded JSON envelope string (v2 format).
    """
    kek = key or _get_kek()
    fernet = Fernet(kek)

    # Generate a random 32-byte DEK and 12-byte GCM nonce
    dek = os.urandom(32)
    iv = os.urandom(12)

    # Encrypt content with AES-256-GCM (tag appended to ciphertext by AESGCM)
    aesgcm = AESGCM(dek)
    plaintext_bytes = json.dumps(content).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext_bytes, _V2_AAD)

    # Wrap the DEK with the KEK using Fernet; Fernet token is already base64-safe
    encrypted_dek = fernet.encrypt(dek).decode("utf-8")

    envelope = {
        "v": _V2_VERSION,
        "encrypted_dek": encrypted_dek,
        "iv": base64.b64encode(iv).decode("utf-8"),
        "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
    }
    return base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("utf-8")


def decrypt_vault_item_content(
    encrypted_content: str, key: bytes | None = None
) -> dict:
    """Decrypt vault item content, supporting both v2 and legacy v1 formats.

    Format detection:
      - Attempts to base64-decode and JSON-parse the input.
      - If the result has ``"v": 2``, uses envelope decryption.
      - Otherwise falls back to legacy Fernet (v1) decryption.

    Args:
        encrypted_content: Encrypted string (v2 envelope or v1 Fernet token).

    Returns:
        Decrypted vault item content as a dict.
    """
    kek = key or _get_kek()

    # Try to detect v2 envelope format
    try:
        raw = base64.b64decode(encrypted_content.encode("utf-8"))
        envelope = json.loads(raw.decode("utf-8"))
        if isinstance(envelope, dict) and envelope.get("v") == _V2_VERSION:
            return _decrypt_v2(envelope, kek)
    except (ValueError, KeyError, UnicodeDecodeError):
        pass

    # Fall back to legacy v1 Fernet decryption
    return _decrypt_v1(encrypted_content, kek)


def rewrap_vault_item_dek(
    encrypted_content: str, old_kek: bytes, new_kek: bytes
) -> str:
    """Re-wrap the DEK with a new KEK without touching the ciphertext.

    Enables zero-downtime key rotation: the ciphertext and IV are unchanged;
    only the encrypted DEK wrapper is replaced.

    Only works on v2 envelopes. Raises ValueError for v1 tokens.

    Args:
        encrypted_content: Existing v2 envelope string.
        old_kek: The current KEK (Fernet key bytes) used to unwrap the DEK.
        new_kek: The replacement KEK (Fernet key bytes) to wrap the DEK with.

    Returns:
        New base64-encoded v2 envelope with the DEK re-wrapped under new_kek.

    Raises:
        ValueError: If the input is not a v2 envelope.
    """
    try:
        raw = base64.b64decode(encrypted_content.encode("utf-8"))
        envelope = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"Failed to decode envelope: {e}") from e

    if not isinstance(envelope, dict) or envelope.get("v") != _V2_VERSION:
        raise ValueError("rewrap_vault_item_dek only supports v2 envelopes")

    old_fernet = Fernet(old_kek)
    new_fernet = Fernet(new_kek)

    dek = old_fernet.decrypt(envelope["encrypted_dek"].encode("utf-8"))
    new_encrypted_dek = new_fernet.encrypt(dek).decode("utf-8")

    new_envelope = dict(envelope)
    new_envelope["encrypted_dek"] = new_encrypted_dek

    return base64.b64encode(json.dumps(new_envelope).encode("utf-8")).decode("utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decrypt_v2(envelope: dict, kek: bytes) -> dict:
    """Decrypt a v2 envelope using the provided KEK."""
    fernet = Fernet(kek)
    dek = fernet.decrypt(envelope["encrypted_dek"].encode("utf-8"))

    iv = base64.b64decode(envelope["iv"])
    ciphertext = base64.b64decode(envelope["ciphertext"])

    aesgcm = AESGCM(dek)
    plaintext_bytes = aesgcm.decrypt(iv, ciphertext, _V2_AAD)
    return json.loads(plaintext_bytes.decode("utf-8"))


def _decrypt_v1(encrypted_content: str, kek: bytes) -> dict:
    """Decrypt a legacy v1 Fernet token."""
    cipher = Fernet(kek)
    decrypted = cipher.decrypt(encrypted_content.encode())
    return json.loads(decrypted.decode())
