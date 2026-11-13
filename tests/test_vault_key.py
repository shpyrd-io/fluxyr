"""VAULT_ENCRYPTION_KEY: a Fernet key as before, or any long secret."""

import pytest
from cryptography.fernet import Fernet

from fluxyr.encryption import (
    decrypt_vault_item_content,
    encrypt_vault_item_content,
    kek_from_secret,
)


def test_a_fernet_key_is_taken_as_it_is():
    key = Fernet.generate_key()
    assert kek_from_secret(key.decode()) == key


def test_a_long_secret_always_gives_the_same_usable_key():
    secret = "0123456789abcdef" * 4  # a platform's generated hex
    key = kek_from_secret(secret)
    assert key == kek_from_secret(secret) and key != kek_from_secret(secret + "x")
    Fernet(key)
    token = encrypt_vault_item_content({"value": "s"}, key)
    assert decrypt_vault_item_content(token, key) == {"value": "s"}


def test_a_short_secret_is_refused():
    with pytest.raises(ValueError, match="at least 32 characters"):
        kek_from_secret("too-short")
