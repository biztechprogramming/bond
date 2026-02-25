"""Field-level encryption for settings values using Fernet symmetric encryption.

Reuses the same vault key infrastructure from vault.py so there is
a single encryption key for the entire Bond installation.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.vault import _get_or_create_key

_ENC_PREFIX = "enc:"


def _get_fernet() -> Fernet:
    return Fernet(_get_or_create_key())


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return prefixed ciphertext."""
    token = _get_fernet().encrypt(plaintext.encode())
    return _ENC_PREFIX + token.decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a prefixed ciphertext string back to plaintext.

    If the value lacks the ``enc:`` prefix it is returned as-is
    (handles legacy plaintext values).
    """
    if not ciphertext.startswith(_ENC_PREFIX):
        return ciphertext
    token = ciphertext[len(_ENC_PREFIX) :]
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ciphertext


def is_encrypted(value: str) -> bool:
    """Check whether a value carries the encryption prefix."""
    return value.startswith(_ENC_PREFIX)
