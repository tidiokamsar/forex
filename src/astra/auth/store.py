"""Accounts, server-side sessions, login throttling and the authentication audit (SQLite).

Rules (see SECURITY.md, "Web authentication"):
- The browser holds only an opaque random token; the database keeps its SHA-256 digest.
- A session has a stage. Only ``FULL`` grants access to data; ``MFA_PENDING``, ``MFA_ENROLL``
  and ``PASSWORD_CHANGE`` only reach the page that completes them. Every stage change and every
  privilege change issues a new token and revokes the old one (no session fixation).
- A session expires after ``idle_seconds`` without activity and in any case ``absolute_seconds``
  after creation; logout, password change or reset, MFA reset and account deactivation revoke it.
- Unknown, revoked, expired or malformed tokens are refused. Any storage error raises
  ``AuthUnavailable``: the caller must answer 503, never fall back to anonymous access.
- Failed logins are throttled per account and per client address with a progressive delay that
  caps at ``throttle_max_seconds``: an attacker can slow the legitimate user down, never lock the
  account permanently.
- ADMIN and OPERATOR accounts must enrol TOTP before reaching the dashboard.
- ``auth_events`` is append-only (triggers refuse UPDATE and DELETE). No password, token, cookie,
  TOTP secret or code is ever written to it.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import crypto
from .mfa_crypto import TOTP_SECRET, MfaCipher, MfaKeyError, is_sealed, load_cipher, sealed_key_id

USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")


class WebRole(StrEnum):
    READ_ONLY = "READ_ONLY"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"

    @property
    def rank(self) -> int:
        return {WebRole.READ_ONLY: 1, WebRole.OPERATOR: 2, WebRole.ADMIN: 3}[self]

    def allows(self, required: WebRole) -> bool:
        return self.rank >= required.rank


class SessionStage(StrEnum):
    MFA_PENDING = "MFA_PENDING"
    MFA_ENROLL = "MFA_ENROLL"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    FULL = "FULL"


class AuthError(ValueError):
    """A refused operation; the message is safe to show to the user."""


class AuthUnavailable(RuntimeError):
    """The authentication store cannot be read or written; access must be refused (503)."""


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    idle_seconds: int = 15 * 60
    absolute_seconds: int = 8 * 60 * 60
    pending_seconds: int = 5 * 60  # MFA, enrolment and forced password change steps
    max_sessions_per_user: int = 5
    min_password_length: int = 12
    max_password_length: int = 256
    throttle_free_failures: int = 5
    throttle_base_seconds: int = 2
    throttle_max_seconds: int = 15 * 60
    temporary_password_seconds: int = 72 * 3600  # generated or reset passwords must be changed within 72 h
    mfa_required_roles: frozenset[WebRole] = frozenset({WebRole.ADMIN, WebRole.OPERATOR})

    def __post_init__(self) -> None:
        if not 60 <= self.idle_seconds <= self.absolute_seconds <= 24 * 3600:
            raise ValueError("require 60 <= idle_seconds <= absolute_seconds <= 24 h")
        if not 60 <= self.pending_seconds <= 900 or not 1 <= self.max_sessions_per_user <= 20:
            raise ValueError("invalid pending_seconds or max_sessions_per_user")
        if not 12 <= self.min_password_length <= self.max_password_length <= 1024:
            raise ValueError("minimum password length must be at least 12")
        if not 1 <= self.throttle_free_failures <= 20 or not 1 <= self.throttle_base_seconds <= self.throttle_max_seconds:
            raise ValueError("invalid throttle settings")
        if not 3600 <= self.temporary_password_seconds <= 7 * 86400:
            raise ValueError("temporary_password_seconds must lie between 1 h and 7 days")
        if WebRole.ADMIN not in self.mfa_required_roles:
            raise ValueError("ADMIN accounts must require MFA")


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: int
    username: str
    role: WebRole
    active: bool
    must_change_password: bool
    mfa_enabled: bool
    created_at: str

    def to_public(self) -> dict[str, Any]:
        return {"username": self.username, "role": self.role.value, "active": self.active,
                "mfa_enabled": self.mfa_enabled, "must_change_password": self.must_change_password,
                "created_at": self.created_at}


@dataclass(frozen=True, slots=True)
class SessionRecord:
    token_digest: str
    user: UserRecord
    stage: SessionStage
    created_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginResult:
    status: str  # "ok", "invalid", "throttled"
    token: str | None = None
    stage: SessionStage | None = None
    retry_after_seconds: int = 0


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _safe_label(value: str, limit: int = 64) -> str:
    # Only for audit labels of usernames as typed: printable ASCII, truncated.
    return "".join(ch if 32 < ord(ch) < 127 else "?" for ch in value)[:limit]


class AuthStore:
    """SQLite store for web accounts and sessions; a separate file from the trading database."""

    def __init__(self, path: Path | str, policy: AuthPolicy | None = None,
                 clock: Callable[[], datetime] | None = None, mfa_cipher: MfaCipher | None = None,
                 mfa_key_path: Path | str | None = None) -> None:
        self.path = Path(path)
        self.policy = policy or AuthPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        # TOTP secrets are stored only encrypted with this key ring (astra.auth.mfa_crypto). Without it,
        # every operation that needs a TOTP secret fails closed with AuthUnavailable (HTTP 503).
        self.mfa_cipher = mfa_cipher
        # With a key file, the ring is reloaded whenever the file changes (inode, size, mtime or ctime: the
        # atomic replace of a rotation changes the inode), so running workers pick up a rotation at once.
        # The first use always loads it from the file, so a ring passed in can never be stale.
        self.mfa_key_path = Path(mfa_key_path) if mfa_key_path else None
        self._mfa_key_fingerprint: tuple[int, int, int, int] | None = None

    def _key_fingerprint(self) -> tuple[int, int, int, int] | None:
        try:
            info = self.mfa_key_path.stat()
        except OSError:
            return None
        return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _cipher(self) -> MfaCipher:
        if self.mfa_key_path is not None:
            fingerprint = self._key_fingerprint()  # taken before loading: a change during the load is seen next time
            if fingerprint is None:
                raise MfaKeyError("MFA key ring not readable (ASTRA_MFA_KEY_FILE)")
            if fingerprint != self._mfa_key_fingerprint or self.mfa_cipher is None:
                self.mfa_cipher = load_cipher(self.mfa_key_path)
                self._mfa_key_fingerprint = fingerprint
        if self.mfa_cipher is None:
            raise MfaKeyError("MFA key ring not configured (ASTRA_MFA_KEY_FILE)")
        return self.mfa_cipher

    # -- TOTP secret protection ---------------------------------------------------------------

    def _seal(self, user_id: int, purpose: str, secret: str) -> str:
        return self._cipher().seal(user_id, purpose, secret)

    def _open(self, user_id: int, username: str, purpose: str, stored: str) -> str:
        return self._cipher().open(user_id, purpose, stored)

    @contextmanager
    def _mfa_guard(self, username: str, throttle_key: str | None = None) -> Iterator[None]:
        """Turn a key-ring failure into AuthUnavailable (503) after the caller's transaction rolled back.

        Use as the OUTER context of the write transaction, so the audit event is written on its own
        connection once the database is unlocked. MFA stays enabled: nothing is changed on failure.
        """

        try:
            yield
        except MfaKeyError as exc:
            with self._write() as db:
                if throttle_key:
                    # Counted like a failed code, so attempts while the key ring is broken are throttled and
                    # the audit table cannot be grown at will (review L4).
                    if self._throttle_wait(db, [throttle_key]):
                        raise AuthUnavailable("MFA secret unavailable") from exc
                    self._record_failure(db, [throttle_key])
                self._event(db, "system", "mfa.secret_unavailable", "refused", target=username, detail=str(exc)[:120])
            raise AuthUnavailable("MFA secret unavailable") from exc

    # -- schema ------------------------------------------------------------------------------

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL CHECK (role IN ('READ_ONLY', 'OPERATOR', 'ADMIN')),
                    password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    mfa_secret TEXT,
                    mfa_pending_secret TEXT,
                    mfa_enabled INTEGER NOT NULL DEFAULT 0,
                    mfa_last_step INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    password_changed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_codes (
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    code_digest TEXT NOT NULL,
                    used_at TEXT,
                    PRIMARY KEY (user_id, code_digest)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_digest TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    absolute_expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revoke_reason TEXT,
                    created_ip TEXT,
                    last_ip TEXT,
                    user_agent_digest TEXT
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id, revoked_at);
                CREATE TABLE IF NOT EXISTS login_throttle (
                    throttle_key TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL,
                    last_failure_at TEXT NOT NULL,
                    blocked_until TEXT
                );
                CREATE TABLE IF NOT EXISTS auth_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT,
                    result TEXT NOT NULL,
                    client_ip TEXT,
                    detail TEXT
                );
                CREATE TRIGGER IF NOT EXISTS auth_events_no_update BEFORE UPDATE ON auth_events
                BEGIN SELECT RAISE(ABORT, 'auth_events is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS auth_events_no_delete BEFORE DELETE ON auth_events
                BEGIN SELECT RAISE(ABORT, 'auth_events is append-only'); END;
                """
            )
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        except sqlite3.Error as exc:
            raise AuthUnavailable("authentication store unavailable") from exc
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        except sqlite3.Error as exc:
            raise AuthUnavailable("authentication store unavailable") from exc
        finally:
            db.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    # -- helpers -----------------------------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise AuthUnavailable("clock must be timezone-aware")
        return now

    def _event(self, db: sqlite3.Connection, actor: str, action: str, result: str, target: str | None = None,
               client_ip: str | None = None, detail: str | None = None) -> None:
        db.execute(
            "INSERT INTO auth_events(occurred_at, actor, action, target, result, client_ip, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_iso(self._now()), actor, action, target, result, client_ip, detail),
        )

    @staticmethod
    def _user(row: sqlite3.Row) -> UserRecord:
        return UserRecord(id=row["id"], username=row["username"], role=WebRole(row["role"]),
                          active=bool(row["active"]), must_change_password=bool(row["must_change_password"]),
                          mfa_enabled=bool(row["mfa_enabled"]), created_at=row["created_at"])

    def password_problems(self, password: str, username: str) -> list[str]:
        problems = []
        if not isinstance(password, str) or len(password) < self.policy.min_password_length:
            problems.append(f"au moins {self.policy.min_password_length} caractères")
        elif len(password) > self.policy.max_password_length:
            problems.append(f"au plus {self.policy.max_password_length} caractères")
        else:
            if username and normalize_username(username) in password.casefold():
                problems.append("ne doit pas contenir l'identifiant")
            if len(set(password)) < 5:
                problems.append("trop peu de caractères différents")
        return problems

    def _require_password(self, password: str, username: str) -> None:
        problems = self.password_problems(password, username)
        if problems:
            raise AuthError("mot de passe refusé : " + ", ".join(problems))

    # -- accounts ----------------------------------------------------------------------------

    def user_count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, actor: str, username: str, role: WebRole, password: str,
                    must_change_password: bool = True, first_admin: bool = False) -> UserRecord:
        name = normalize_username(username)
        if not USERNAME_PATTERN.fullmatch(name):
            raise AuthError("identifiant invalide (3 à 64 caractères : a-z, 0-9, point, tiret, souligné)")
        role = WebRole(role)
        self._require_password(password, name)
        encoded = crypto.hash_password(password)
        now = _iso(self._now())
        with self._write() as db:
            if first_admin:
                if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                    raise AuthError("des comptes existent déjà : utilisez la gestion des comptes")
                if role is not WebRole.ADMIN:
                    raise AuthError("le premier compte doit être ADMIN")
            if db.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
                raise AuthError("cet identifiant existe déjà")
            cursor = db.execute(
                "INSERT INTO users(username, role, password_hash, must_change_password, created_at, password_changed_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, role.value, encoded, int(must_change_password), now, now),
            )
            self._event(db, actor, "user.create", "ok", target=name, detail=f"role={role.value}")
            row = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._user(row)

    def reencrypt_mfa_secrets(self, actor: str) -> dict[str, int]:
        """Encrypt plaintext TOTP secrets (migration) and re-encrypt those under an older key (rotation).

        Nothing is deleted and MFA stays enabled: a secret that cannot be decrypted is left as it is and
        reported, so an administrator can investigate (or reset that account's MFA explicitly).
        """

        try:
            cipher = self._cipher()
        except MfaKeyError as exc:
            raise AuthUnavailable(str(exc)) from exc
        counts = {"migrated": 0, "rotated": 0, "unchanged": 0, "unreadable": 0}
        with self._write() as db:
            rows = db.execute("SELECT id, username, mfa_secret, mfa_pending_secret FROM users").fetchall()
            for row in rows:
                for column, purpose in (("mfa_secret", "active"), ("mfa_pending_secret", "pending")):
                    stored = row[column]
                    if not stored:
                        continue
                    sealed_before = is_sealed(stored)
                    if sealed_before and not cipher.needs_reencryption(stored):
                        counts["unchanged"] += 1
                        continue
                    try:
                        if sealed_before:
                            plain = cipher.open(row["id"], purpose, stored)
                        elif TOTP_SECRET.fullmatch(stored):
                            plain = stored  # a genuine plaintext base32 secret written before AUTH-2
                        else:
                            raise MfaKeyError("not a TOTP secret")
                        sealed = cipher.seal(row["id"], purpose, plain)
                    except MfaKeyError:
                        counts["unreadable"] += 1  # left untouched for an administrator to inspect
                        continue
                    db.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (sealed, row["id"]))
                    counts["rotated" if sealed_before else "migrated"] += 1
            self._event(db, actor, "mfa.reencrypt", "ok", detail=",".join(f"{k}={v}" for k, v in counts.items()))
        return counts

    def mfa_key_ids_in_use(self) -> set[str]:
        with self._connect() as db:
            rows = db.execute("SELECT mfa_secret, mfa_pending_secret FROM users").fetchall()
        return {kid for row in rows for value in row for kid in [sealed_key_id(value)] if kid}

    def plaintext_mfa_secrets(self) -> int:
        with self._connect() as db:
            rows = db.execute("SELECT mfa_secret, mfa_pending_secret FROM users").fetchall()
        return sum(1 for row in rows for value in row if value and not is_sealed(value))

    def password_epoch(self, username: str) -> str:
        """An opaque value that changes whenever the account's password changes or is reset (binds device cookies)."""

        with self._connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE username = ?", (normalize_username(username),)).fetchone()
        # Argon2id hashes are salted, so every new password (even the same one) gives a new epoch.
        return crypto.token_digest(row["password_hash"])[:32] if row else "none"

    def get_user(self, username: str) -> UserRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (normalize_username(username),)).fetchone()
        return self._user(row) if row else None

    def list_users(self) -> list[UserRecord]:
        with self._connect() as db:
            return [self._user(row) for row in db.execute("SELECT * FROM users ORDER BY username")]

    def set_active(self, actor: str, username: str, active: bool) -> None:
        name = normalize_username(username)
        with self._write() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
            if row is None:
                raise AuthError("compte inconnu")
            if not active and row["role"] == WebRole.ADMIN.value:
                admins = db.execute("SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND active = 1").fetchone()[0]
                if row["active"] and admins <= 1:
                    raise AuthError("impossible de désactiver le dernier administrateur actif")
            db.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), row["id"]))
            if not active:
                self._revoke_user(db, row["id"], "account_disabled")
            self._event(db, actor, "user.enable" if active else "user.disable", "ok", target=name)

    def set_role(self, actor: str, username: str, role: WebRole) -> None:
        name, role = normalize_username(username), WebRole(role)
        with self._write() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
            if row is None:
                raise AuthError("compte inconnu")
            if row["role"] == WebRole.ADMIN.value and role is not WebRole.ADMIN:
                admins = db.execute("SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND active = 1").fetchone()[0]
                if row["active"] and admins <= 1:
                    raise AuthError("impossible de retirer le rôle du dernier administrateur actif")
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role.value, row["id"]))
            # A privilege change ends every session; the user signs in again with the new role.
            self._revoke_user(db, row["id"], "role_changed")
            self._event(db, actor, "user.role", "ok", target=name, detail=f"role={role.value}")

    def reset_password(self, actor: str, username: str) -> str:
        """Generate a one-time password (returned once, never stored in clear) and force a change."""

        name = normalize_username(username)
        temporary = crypto.new_token()[:24]
        with self._write() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
            if row is None:
                raise AuthError("compte inconnu")
            db.execute("UPDATE users SET password_hash = ?, must_change_password = 1, password_changed_at = ? WHERE id = ?",
                       (crypto.hash_password(temporary), _iso(self._now()), row["id"]))
            self._revoke_user(db, row["id"], "password_reset")
            self._clear_throttle(db, f"user:{name}")
            self._event(db, actor, "user.password_reset", "ok", target=name)
        return temporary

    def reset_mfa(self, actor: str, username: str) -> None:
        name = normalize_username(username)
        with self._write() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
            if row is None:
                raise AuthError("compte inconnu")
            db.execute("UPDATE users SET mfa_secret = NULL, mfa_pending_secret = NULL, mfa_enabled = 0, mfa_last_step = 0"
                       " WHERE id = ?", (row["id"],))
            db.execute("DELETE FROM recovery_codes WHERE user_id = ?", (row["id"],))
            self._revoke_user(db, row["id"], "mfa_reset")
            self._event(db, actor, "user.mfa_reset", "ok", target=name)

    def revoke_user_sessions(self, actor: str, username: str) -> int:
        name = normalize_username(username)
        with self._write() as db:
            row = db.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()
            if row is None:
                raise AuthError("compte inconnu")
            count = self._revoke_user(db, row["id"], "revoked_by_admin")
            self._event(db, actor, "user.sessions_revoked", "ok", target=name, detail=f"count={count}")
        return count

    # -- throttle ----------------------------------------------------------------------------

    def _throttle_wait(self, db: sqlite3.Connection, keys: list[str]) -> int:
        now = self._now()
        wait = 0
        for key in keys:
            row = db.execute("SELECT blocked_until FROM login_throttle WHERE throttle_key = ?", (key,)).fetchone()
            if row and row["blocked_until"]:
                remaining = (_parse(row["blocked_until"]) - now).total_seconds()
                wait = max(wait, int(remaining) + 1 if remaining > 0 else 0)
        return wait

    def _record_failure(self, db: sqlite3.Connection, keys: list[str]) -> None:
        now = self._now()
        for key in keys:
            row = db.execute("SELECT failures, last_failure_at FROM login_throttle WHERE throttle_key = ?", (key,)).fetchone()
            failures = 1
            if row:
                # The counter decays: after a quiet hour it starts again.
                failures = row["failures"] + 1 if now - _parse(row["last_failure_at"]) < timedelta(hours=1) else 1
            blocked = None
            excess = failures - self.policy.throttle_free_failures
            if excess >= 0:
                delay = min(self.policy.throttle_base_seconds * 2 ** min(excess, 20), self.policy.throttle_max_seconds)
                blocked = _iso(now + timedelta(seconds=delay))
            db.execute(
                "INSERT INTO login_throttle(throttle_key, failures, last_failure_at, blocked_until) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(throttle_key) DO UPDATE SET failures = excluded.failures,"
                " last_failure_at = excluded.last_failure_at, blocked_until = excluded.blocked_until",
                (key, failures, _iso(now), blocked),
            )

    @staticmethod
    def _keys(label: str, client_ip: str | None, device: str | None = None) -> list[str]:
        """Throttle keys: [account-wide or known device, (account, address), address].

        OWASP "device cookies": a browser that already signed in successfully to this account (``device``,
        verified by the caller) is throttled on its own key instead of the account-wide key, so an attacker
        failing from other addresses cannot lock the legitimate user out. Unknown browsers share the
        account-wide key, which limits online guessing spread over many addresses.
        """

        ip = client_ip or "unknown"
        first = f"device:{label}|{device}" if device else f"user:{label}"
        return [first, f"userip:{label}|{ip}", f"ip:{ip}"]

    @staticmethod
    def _clear_throttle(db: sqlite3.Connection, key: str) -> None:
        db.execute("DELETE FROM login_throttle WHERE throttle_key = ?", (key,))

    # -- sessions ----------------------------------------------------------------------------

    def _new_session(self, db: sqlite3.Connection, user_id: int, stage: SessionStage, client_ip: str | None,
                     user_agent: str | None, created_at: datetime | None = None) -> str:
        now = self._now()
        token = crypto.new_token()
        start = created_at or now
        lifetime = self.policy.absolute_seconds if stage is SessionStage.FULL else self.policy.pending_seconds
        absolute = min(start + timedelta(seconds=self.policy.absolute_seconds), now + timedelta(seconds=lifetime))
        db.execute(
            "INSERT INTO sessions(token_digest, user_id, stage, created_at, last_seen_at, absolute_expires_at,"
            " created_ip, last_ip, user_agent_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (crypto.token_digest(token), user_id, stage.value, _iso(start), _iso(now), _iso(absolute),
             client_ip, client_ip, crypto.token_digest(user_agent or "")),
        )
        if stage is SessionStage.FULL:
            active = db.execute(
                "SELECT token_digest FROM sessions WHERE user_id = ? AND stage = 'FULL' AND revoked_at IS NULL"
                " AND absolute_expires_at > ? ORDER BY created_at DESC, rowid DESC", (user_id, _iso(now))).fetchall()
            for row in active[self.policy.max_sessions_per_user:]:
                self._revoke(db, row["token_digest"], "too_many_sessions")
        return token

    def _revoke(self, db: sqlite3.Connection, digest: str, reason: str) -> None:
        db.execute("UPDATE sessions SET revoked_at = ?, revoke_reason = ? WHERE token_digest = ? AND revoked_at IS NULL",
                   (_iso(self._now()), reason, digest))

    def _revoke_user(self, db: sqlite3.Connection, user_id: int, reason: str, keep: str | None = None) -> int:
        cursor = db.execute(
            "UPDATE sessions SET revoked_at = ?, revoke_reason = ? WHERE user_id = ? AND revoked_at IS NULL"
            " AND token_digest IS NOT ?", (_iso(self._now()), reason, user_id, keep))
        return cursor.rowcount

    def _first_stage(self, row: sqlite3.Row) -> SessionStage:
        if row["mfa_enabled"]:
            return SessionStage.MFA_PENDING
        return self._after_mfa(row)

    def _after_mfa(self, row: sqlite3.Row) -> SessionStage:
        if not row["mfa_enabled"] and WebRole(row["role"]) in self.policy.mfa_required_roles:
            return SessionStage.MFA_ENROLL
        if row["must_change_password"]:
            return SessionStage.PASSWORD_CHANGE
        return SessionStage.FULL

    def authenticate(self, username: str, password: str, client_ip: str | None,
                     user_agent: str | None = None, device: str | None = None) -> LoginResult:
        name = normalize_username(username) if isinstance(username, str) else ""
        label = _safe_label(name)
        keys = self._keys(label, client_ip, device)
        with self._write() as db:
            wait = self._throttle_wait(db, keys)
            if wait:
                # Not audited: repeated throttled attempts would only grow the append-only table.
                return LoginResult("throttled", retry_after_seconds=wait)
            row = db.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
        # Password verification (about 0.1 s) runs outside the write transaction.
        ok = isinstance(password, str) and len(password) <= self.policy.max_password_length and \
            crypto.verify_password(row["password_hash"] if row else None, password)
        with self._write() as db:
            if not ok or row is None or not row["active"]:
                self._record_failure(db, keys)
                reason = "disabled" if ok and row is not None else "bad_credentials"
                self._event(db, f"web:{label}", "login", "refused", target=label, client_ip=client_ip, detail=reason)
                return LoginResult("invalid")
            age = self._now() - _parse(row["password_changed_at"])
            if row["must_change_password"] and age > timedelta(seconds=self.policy.temporary_password_seconds):
                # An initial or reset password nobody used in time is dead: an administrator issues a new one.
                self._event(db, f"web:{label}", "login", "refused", target=label, client_ip=client_ip,
                            detail="temporary_password_expired")
                return LoginResult("invalid")
            if crypto.password_needs_rehash(row["password_hash"]):
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (crypto.hash_password(password), row["id"]))
            for key in keys[:2]:  # the per-address key keeps counting: it guards against password spraying
                self._clear_throttle(db, key)
            stage = self._first_stage(row)
            token = self._new_session(db, row["id"], stage, client_ip, user_agent)
            self._event(db, f"web:{label}", "login", "ok", target=label, client_ip=client_ip, detail=f"stage={stage.value}")
        return LoginResult("ok", token=token, stage=stage)

    def resolve(self, token: str | None, client_ip: str | None = None) -> SessionRecord | None:
        """The live session for a token, or None. Touches last activity. Raises AuthUnavailable on storage errors."""

        if not isinstance(token, str) or not 20 <= len(token) <= 128:
            return None
        digest = crypto.token_digest(token)
        now = self._now()
        with self._write() as db:
            row = db.execute(
                "SELECT s.*, u.username, u.role, u.active, u.must_change_password, u.mfa_enabled, u.created_at AS u_created,"
                " u.id AS uid FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_digest = ?", (digest,)).fetchone()
            if row is None or row["revoked_at"] is not None:
                return None
            idle_limit = _parse(row["last_seen_at"]) + timedelta(seconds=self.policy.idle_seconds)
            if not row["active"] or now >= _parse(row["absolute_expires_at"]) or now >= idle_limit:
                self._revoke(db, digest, "expired" if row["active"] else "account_disabled")
                return None
            db.execute("UPDATE sessions SET last_seen_at = ?, last_ip = ? WHERE token_digest = ?", (_iso(now), client_ip, digest))
        user = UserRecord(id=row["uid"], username=row["username"], role=WebRole(row["role"]), active=True,
                          must_change_password=bool(row["must_change_password"]), mfa_enabled=bool(row["mfa_enabled"]),
                          created_at=row["u_created"])
        return SessionRecord(token_digest=digest, user=user, stage=SessionStage(row["stage"]),
                             created_at=_parse(row["created_at"]), absolute_expires_at=_parse(row["absolute_expires_at"]))

    def _advance(self, db: sqlite3.Connection, session: SessionRecord, client_ip: str | None, user_agent: str | None,
                 reason: str) -> tuple[str, SessionStage]:
        row = db.execute("SELECT * FROM users WHERE id = ?", (session.user.id,)).fetchone()
        stage = self._after_mfa(row)
        self._revoke(db, session.token_digest, reason)
        return self._new_session(db, row["id"], stage, client_ip, user_agent), stage

    def verify_mfa(self, session: SessionRecord, code: str, client_ip: str | None,
                   user_agent: str | None = None) -> LoginResult:
        if session.stage is not SessionStage.MFA_PENDING:
            raise AuthError("aucune vérification MFA en attente")
        key = [f"mfa:{session.user.id}"]
        with self._mfa_guard(session.user.username, key[0]), self._write() as db:
            wait = self._throttle_wait(db, key)
            if wait:
                self._event(db, f"web:{session.user.username}", "mfa", "throttled", client_ip=client_ip)
                return LoginResult("throttled", retry_after_seconds=wait)
            row = db.execute("SELECT * FROM users WHERE id = ?", (session.user.id,)).fetchone()
            code = code if isinstance(code, str) else ""
            step = None
            # A six-digit code needs the encrypted TOTP secret; a recovery code (stored as a digest) does not,
            # so recovery stays possible while the key ring is unavailable.
            if row["mfa_secret"] and re.fullmatch(r"[0-9]{6}", code.strip().replace(" ", "")):
                secret = self._open(row["id"], row["username"], "active", row["mfa_secret"])
                step = crypto.totp_timestep(secret, code, self._now().timestamp())
            method = "totp"
            accepted = step is not None and step > row["mfa_last_step"]  # a code is accepted once (no replay)
            if accepted:
                db.execute("UPDATE users SET mfa_last_step = ? WHERE id = ?", (step, row["id"]))
            elif step is None:
                method = "recovery_code"
                digest = crypto.token_digest(crypto.normalize_recovery_code(code))
                used = db.execute(
                    "UPDATE recovery_codes SET used_at = ? WHERE user_id = ? AND code_digest = ? AND used_at IS NULL",
                    (_iso(self._now()), row["id"], digest))
                accepted = used.rowcount == 1
            if not accepted:
                self._record_failure(db, key)
                self._event(db, f"web:{session.user.username}", "mfa", "refused", client_ip=client_ip)
                return LoginResult("invalid")
            self._clear_throttle(db, key[0])
            token, stage = self._advance(db, session, client_ip, user_agent, "mfa_verified")
            self._event(db, f"web:{session.user.username}", "mfa", "ok", client_ip=client_ip, detail=f"method={method}")
        return LoginResult("ok", token=token, stage=stage)

    def begin_mfa_enrollment(self, session: SessionRecord) -> tuple[str, str]:
        """A new pending TOTP secret and its provisioning URI, for a session at the enrolment stage."""

        if session.stage is not SessionStage.MFA_ENROLL:
            raise AuthError("l'enrôlement MFA n'est pas attendu pour cette session")
        with self._mfa_guard(session.user.username, f"enroll:{session.user.id}"), self._write() as db:
            row = db.execute("SELECT id, username, mfa_pending_secret FROM users WHERE id = ?", (session.user.id,)).fetchone()
            if row["mfa_pending_secret"]:
                secret = self._open(row["id"], row["username"], "pending", row["mfa_pending_secret"])
            else:
                secret = crypto.new_totp_secret()
                db.execute("UPDATE users SET mfa_pending_secret = ? WHERE id = ?",
                           (self._seal(row["id"], "pending", secret), row["id"]))
        return secret, crypto.totp_uri(secret, session.user.username)

    def confirm_mfa_enrollment(self, session: SessionRecord, code: str, client_ip: str | None,
                               user_agent: str | None = None) -> tuple[LoginResult, list[str]]:
        if session.stage is not SessionStage.MFA_ENROLL:
            raise AuthError("l'enrôlement MFA n'est pas attendu pour cette session")
        with self._mfa_guard(session.user.username, f"enroll:{session.user.id}"), self._write() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (session.user.id,)).fetchone()
            secret = self._open(row["id"], row["username"], "pending", row["mfa_pending_secret"]) \
                if row["mfa_pending_secret"] else None
            typed = code if isinstance(code, str) else ""
            step = crypto.totp_timestep(secret, typed, self._now().timestamp()) if secret else None
            if step is None:
                self._event(db, f"web:{session.user.username}", "mfa.enroll", "refused", client_ip=client_ip)
                return LoginResult("invalid"), []
            codes = crypto.new_recovery_codes()
            db.execute("UPDATE users SET mfa_secret = ?, mfa_pending_secret = NULL, mfa_enabled = 1, mfa_last_step = ?"
                       " WHERE id = ?", (self._seal(row["id"], "active", secret), step, row["id"]))
            db.execute("DELETE FROM recovery_codes WHERE user_id = ?", (row["id"],))
            db.executemany("INSERT INTO recovery_codes(user_id, code_digest) VALUES (?, ?)",
                           [(row["id"], crypto.token_digest(crypto.normalize_recovery_code(c))) for c in codes])
            token, stage = self._advance(db, session, client_ip, user_agent, "mfa_enrolled")
            self._event(db, f"web:{session.user.username}", "mfa.enroll", "ok", client_ip=client_ip)
        return LoginResult("ok", token=token, stage=stage), codes

    def change_password(self, session: SessionRecord, current: str, new: str, client_ip: str | None,
                        user_agent: str | None = None) -> LoginResult:
        """Re-authenticate with the current password, store the new one, end every other session."""

        if session.stage not in (SessionStage.FULL, SessionStage.PASSWORD_CHANGE):
            raise AuthError("session non autorisée à changer le mot de passe")
        keys = self._session_keys(session, client_ip)
        with self._write() as db:
            wait = self._throttle_wait(db, keys)
            if wait:
                return LoginResult("throttled", retry_after_seconds=wait)
            row = db.execute("SELECT * FROM users WHERE id = ?", (session.user.id,)).fetchone()
        if not crypto.verify_password(row["password_hash"], current if isinstance(current, str) else ""):
            with self._write() as db:
                self._record_failure(db, keys)
                self._event(db, f"web:{row['username']}", "password.change", "refused", client_ip=client_ip,
                            detail="bad_current_password")
            return LoginResult("invalid")
        self._require_password(new, row["username"])
        if crypto.verify_password(row["password_hash"], new):
            raise AuthError("le nouveau mot de passe doit être différent")
        encoded = crypto.hash_password(new)
        with self._write() as db:
            db.execute("UPDATE users SET password_hash = ?, must_change_password = 0, password_changed_at = ? WHERE id = ?",
                       (encoded, _iso(self._now()), row["id"]))
            self._revoke_user(db, row["id"], "password_changed")
            token = self._new_session(db, row["id"], SessionStage.FULL, client_ip, user_agent)
            self._event(db, f"web:{row['username']}", "password.change", "ok", client_ip=client_ip)
        return LoginResult("ok", token=token, stage=SessionStage.FULL)

    def _session_keys(self, session: SessionRecord, client_ip: str | None) -> list[str]:
        # Re-authentication inside a FULL session (password plus MFA already passed) is throttled on keys of
        # its own: failed logins from other addresses (account-wide key) cannot block it (re-review M-NEW).
        return [f"stepup:{session.user.id}", f"stepupip:{session.user.id}|{client_ip or 'unknown'}"]

    def confirm_password(self, session: SessionRecord, password: str, client_ip: str | None) -> str:
        """Step-up re-authentication of the session's own user: "ok", "invalid" or "throttled"."""

        keys = self._session_keys(session, client_ip)
        with self._write() as db:
            if self._throttle_wait(db, keys):
                return "throttled"
            row = db.execute("SELECT password_hash FROM users WHERE id = ? AND active = 1", (session.user.id,)).fetchone()
        ok = crypto.verify_password(row["password_hash"] if row else None, password if isinstance(password, str) else "")
        with self._write() as db:
            if not ok:
                self._record_failure(db, keys)
                self._event(db, f"web:{session.user.username}", "step_up", "refused", client_ip=client_ip)
                return "invalid"
            for key in keys:
                self._clear_throttle(db, key)
        return "ok"

    def logout(self, session: SessionRecord, client_ip: str | None) -> None:
        with self._write() as db:
            self._revoke(db, session.token_digest, "logout")
            self._event(db, f"web:{session.user.username}", "logout", "ok", client_ip=client_ip)

    def record(self, actor: str, action: str, result: str, target: str | None = None,
               client_ip: str | None = None, detail: str | None = None) -> None:
        """Append an audit event for a sensitive web operation (never pass secrets)."""

        with self._write() as db:
            self._event(db, actor, action, result, target=target, client_ip=client_ip, detail=detail)

    def cleanup(self) -> int:
        """Delete sessions that expired or were revoked more than a day ago, and stale throttle rows."""

        cutoff = _iso(self._now() - timedelta(days=1))
        with self._write() as db:
            removed = db.execute("DELETE FROM sessions WHERE absolute_expires_at < ? OR revoked_at < ?",
                                 (cutoff, cutoff)).rowcount
            db.execute("DELETE FROM login_throttle WHERE last_failure_at < ? AND (blocked_until IS NULL OR blocked_until < ?)",
                       (cutoff, cutoff))
        return removed

    def events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT occurred_at, actor, action, target, result, client_ip, detail FROM auth_events"
                              " ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
        return [dict(row) for row in rows]

    def active_sessions(self, username: str) -> int:
        now = _iso(self._now())
        with self._connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.username = ?"
                " AND s.revoked_at IS NULL AND s.absolute_expires_at > ?", (normalize_username(username), now)).fetchone()[0])
