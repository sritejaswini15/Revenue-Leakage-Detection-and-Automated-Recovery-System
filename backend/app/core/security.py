"""Framework-free security primitives: password hashing and signed bearer tokens.

Token format (URL-safe base64):
    ``user_id:organization_id:role:session_id:expires_unix:hmac_sha256_signature``
The session id points at a server-side ``AuthSession`` row so tokens can be revoked.
"""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from uuid import uuid4

_PBKDF2_ITERATIONS = 120_000


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hmac_sha256_hex(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def hash_password(password: str, salt: str | None = None) -> str:
    salt_value = salt or uuid4().hex
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_value.encode(), _PBKDF2_ITERATIONS).hex()
    return f"{salt_value}${digest}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or "$" not in encoded:
        return False
    salt, expected = encoded.split("$", 1)
    actual = hash_password(password, salt).split("$", 1)[1]
    return constant_time_equals(actual, expected)


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    organization_id: str
    role: str
    session_id: str
    expires_at: int

    def payload(self) -> str:
        return f"{self.user_id}:{self.organization_id}:{self.role}:{self.session_id}:{self.expires_at}"


def encode_token(claims: TokenClaims, secret: str) -> str:
    payload = claims.payload()
    signature = hmac_sha256_hex(secret, payload.encode())
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def decode_token(token: str, secret: str) -> TokenClaims | None:
    """Return the claims if the signature is valid. Expiry is checked by the caller."""
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        user_id, organization_id, role, session_id, expires_at, signature = decoded.split(":", 5)
        claims = TokenClaims(user_id, organization_id, role, session_id, int(expires_at))
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac_sha256_hex(secret, claims.payload().encode())
    return claims if constant_time_equals(signature, expected) else None
