"""Encryption at rest for merchant credentials (Fernet: AES-128-CBC with HMAC-SHA256).

The Fernet key is derived from CREDENTIALS_ENCRYPTION_KEY, so any high-entropy secret
works (including Render's generated values). Development falls back to a key derived
from AUTH_SECRET; production requires an explicit key before credentials can be stored.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class CredentialKeyError(RuntimeError):
    pass


def _fernet() -> Fernet:
    material = settings.CREDENTIALS_ENCRYPTION_KEY
    if not material:
        if settings.is_production:
            raise CredentialKeyError("Set CREDENTIALS_ENCRYPTION_KEY before storing merchant credentials")
        material = f"recoverx-dev-credentials:{settings.AUTH_SECRET}"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialKeyError("Stored credentials cannot be decrypted; was the encryption key changed?") from exc


def mask(value: str, visible: int = 4) -> str:
    return f"{value[:visible]}…{value[-visible:]}" if len(value) > visible * 2 else "…"
