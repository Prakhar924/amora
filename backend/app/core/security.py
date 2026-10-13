"""Password hashing, JWT access tokens, and opaque refresh-token handling.

Design notes
------------
* Access tokens are short-lived JWTs (stateless, carry `sub` + `token_version`).
* Refresh tokens are long, opaque, random strings. Only a SHA-256 hash is
  persisted (`sessions.refresh_token_hash`), so a database leak cannot be
  replayed against the API. They are rotated on every refresh, and reuse of a
  consumed token revokes the whole session family (theft detection).
* `token_version` on the user row lets "log out everywhere" / password change
  invalidate every outstanding access token without a blocklist.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings

TokenType = Literal["access", "verify_email", "reset_password"]


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    # bcrypt silently truncates at 72 bytes; pre-hash so long passphrases keep
    # their full entropy instead of being cut off.
    digest = hashlib.sha256(plain.encode("utf-8")).digest()
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(digest, salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    digest = hashlib.sha256(plain.encode("utf-8")).digest()
    try:
        return bcrypt.checkpw(digest, hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def password_problems(password: str) -> list[str]:
    """Return a list of human-readable reasons a password is unacceptable."""
    problems: list[str] = []
    if len(password) < 10:
        problems.append("must be at least 10 characters long")
    if not any(c.isalpha() for c in password):
        problems.append("must contain at least one letter")
    if not any(c.isdigit() for c in password):
        problems.append("must contain at least one number")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("is too common")
    return problems


_COMMON_PASSWORDS = {
    "password1!", "password123", "qwerty12345", "1234567890",
    "iloveyou123", "letmein123", "admin12345", "welcome123",
}


# --------------------------------------------------------------------------
# JWT access tokens
# --------------------------------------------------------------------------
def _encode(payload: dict[str, Any], ttl: timedelta, token_type: TokenType) -> str:
    now = datetime.now(UTC)
    body = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": token_type,
        "iss": settings.app_name.lower(),
    }
    return jwt.encode(body, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    user_id: uuid.UUID, token_version: int, is_admin: bool = False
) -> str:
    return _encode(
        {"sub": str(user_id), "tv": token_version, "adm": is_admin},
        timedelta(minutes=settings.access_token_ttl_minutes),
        "access",
    )


def create_email_token(user_id: uuid.UUID, purpose: TokenType, hours: int = 24) -> str:
    """Single-purpose token embedded in a verification or reset link."""
    return _encode({"sub": str(user_id)}, timedelta(hours=hours), purpose)


class TokenError(Exception):
    pass


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name.lower(),
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid") from exc
    # A reset-password token must never be accepted as an access token.
    if payload.get("typ") != expected_type:
        raise TokenError("Token is not valid for this operation")
    return payload


# --------------------------------------------------------------------------
# Refresh tokens
# --------------------------------------------------------------------------
def new_refresh_token() -> tuple[str, str]:
    """Return `(plaintext, sha256_hash)`. Only the hash is ever stored."""
    plaintext = secrets.token_urlsafe(48)
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def refresh_tokens_match(plaintext: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_refresh_token(plaintext), stored_hash)
