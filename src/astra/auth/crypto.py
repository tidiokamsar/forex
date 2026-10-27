"""Password hashing, opaque tokens and TOTP, delegated to maintained libraries.

Passwords use Argon2id from ``argon2-cffi`` with its RFC 9106 low-memory profile (memory
64 MiB, 3 iterations, parallelism 4), above the OWASP Password Storage Cheat Sheet minimum
(19 MiB, 2 iterations, parallelism 1). The encoded hash carries its parameters, so a stronger
profile later only triggers a rehash at the next successful login (``needs_rehash``).

Session tokens and recovery codes are high-entropy random values from ``secrets``; only their
SHA-256 digest is stored. TOTP (RFC 6238, 30 s, 6 digits, SHA-1 as authenticator apps expect)
comes from ``pyotp``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import pyotp
from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
# Verified against unknown usernames so a login for a missing account costs the same work.
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(32))

TOKEN_BYTES = 32
RECOVERY_CODE_COUNT = 10


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(encoded: str | None, password: str) -> bool:
    """True only for a matching password; an unknown account still performs one verification."""

    try:
        return _HASHER.verify(encoded or _DUMMY_HASH, password) and encoded is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(encoded: str) -> bool:
    return _HASHER.check_needs_rehash(encoded)


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def keyed_digest(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str, issuer: str = "ASTRA") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def totp_timestep(secret: str, code: str, at: float) -> int | None:
    """The 30-second step a valid code belongs to (current, previous or next), else None."""

    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    for offset in (0, -1, 1):
        moment = at + offset * totp.interval
        if hmac.compare_digest(totp.at(moment), code):
            return int(moment // totp.interval)
    return None


def new_recovery_codes() -> list[str]:
    # 10 codes of 4x5 base32 characters (100 bits each), shown once and stored as SHA-256 digests.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(20))
        codes.append("-".join(raw[i:i + 5] for i in range(0, 20, 5)))
    return codes


def normalize_recovery_code(code: str) -> str:
    return code.strip().upper().replace(" ", "").replace("-", "")
