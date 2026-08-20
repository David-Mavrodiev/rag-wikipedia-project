"""Symmetric encryption for secrets that must be stored and later replayed.

Only used for OIDC client secrets: unlike a password, the app has to send the
original value to the provider, so it cannot be hashed.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from app.core.config import settings


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        # Wrong key, or a row written under a key that has since been replaced.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored credential cannot be decrypted",
        ) from exc


def _fernet() -> Fernet:
    key = settings.secret_encryption_key
    if not key:
        # Fail closed: storing IdP secrets in the clear is not an acceptable
        # fallback just because a key is missing.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SECRET_ENCRYPTION_KEY is not configured",
        )
    try:
        return _build(key)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SECRET_ENCRYPTION_KEY is not a valid Fernet key",
        ) from exc


# Keyed on the key itself rather than cached outright, so a configuration change
# (including the one every test makes) takes effect immediately.
@lru_cache(maxsize=4)
def _build(key: str) -> Fernet:
    return Fernet(key.encode("utf-8"))
