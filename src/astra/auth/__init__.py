"""Web authentication for the ASTRA dashboard: accounts, server-side sessions, MFA and web roles.

Web roles (READ_ONLY, OPERATOR, ADMIN) only govern what the dashboard shows and which account
administration a user may perform. They never map to the execution roles of ``astra.governance``:
a web session, even an ADMIN one, cannot submit orders, release the kill switch, change risk
limits or the mode. This package imports nothing from the execution, risk or broker code.

Third-party dependencies (``argon2-cffi`` for Argon2id, ``pyotp`` for TOTP) are pinned with
hashes in ``requirements/web.txt``; the trading code does not import them.
"""

from .mfa_crypto import MfaCipher, MfaKeyError
from .store import (
    AuthError,
    AuthPolicy,
    AuthStore,
    AuthUnavailable,
    LoginResult,
    SessionRecord,
    UserRecord,
    WebRole,
)

__all__ = [
    "AuthError",
    "AuthPolicy",
    "AuthStore",
    "AuthUnavailable",
    "LoginResult",
    "MfaCipher",
    "MfaKeyError",
    "SessionRecord",
    "UserRecord",
    "WebRole",
]
