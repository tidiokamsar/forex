"""Machine access for agents: per-agent bearer tokens, revocable, time-limited, scoped and audited.

An agent never uses a person's password or TOTP codes (that would turn two factors into one secret stored
on a machine). Instead an administrator with shell access on the server issues a token for a named agent
that acts on behalf of an existing account (its owner):

- The token is ``astra_agt_`` plus 256 random bits, shown once; only its SHA-256 digest is stored.
- It expires (30 days by default, 90 at most) and is never extended: issue a new one instead.
- It is refused once revoked, once its owner is disabled, holds a temporary password, or changed or reset
  their password since the token was issued (``owner_epoch``).
- Its scopes come from ``SCOPES`` only; the web application maps each scope to an explicit list of routes.
  No scope reaches account administration, DEMO or LIVE. ``lab`` needs an OPERATOR or ADMIN owner.
- An optional address list restricts where it may be used from.
- Issue, revocation and refused use of a known token are written to ``auth_events`` (append-only); invalid
  tokens are throttled per address and, like other anonymous refusals, not audited.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import crypto
from .store import AuthError, AuthStore, WebRole, _iso, _parse, normalize_username

TOKEN_PREFIX = "astra_agt_"
AGENT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
SCOPES = {"read": WebRole.READ_ONLY, "lab": WebRole.OPERATOR, "mt5": WebRole.OPERATOR}  # scope -> minimum role of the owner
DEFAULT_DAYS = 30
MAX_DAYS = 90
MAX_ACTIVE_PER_AGENT = 2  # the current token and its replacement during a renewal


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    token_id: int
    name: str
    owner: str
    owner_role: WebRole
    scopes: frozenset[str]
    expires_at: datetime

    @property
    def actor(self) -> str:
        return f"agent:{self.name}"

    def to_public(self) -> dict[str, Any]:
        return {"agent": self.name, "owner": self.owner, "owner_role": self.owner_role.value,
                "scopes": sorted(self.scopes), "expires_at": self.expires_at.isoformat()}


@dataclass(frozen=True, slots=True)
class AgentAuthResult:
    status: str  # "ok", "invalid", "refused", "throttled"
    principal: AgentPrincipal | None = None
    retry_after_seconds: int = 0


def _networks(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result = []
    for value in values or ():
        try:
            result.append(str(ipaddress.ip_network(value.strip(), strict=False)))
        except ValueError as exc:
            raise AuthError(f"adresse ou réseau invalide : {value!r}") from exc
    return result


def _address_allowed(networks: list[str], client_ip: str | None) -> bool:
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(client_ip or "")
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(net) for net in networks)


class AgentTokens:
    """Agent tokens, stored in the account database next to the sessions (same file, same audit table)."""

    def __init__(self, auth: AuthStore) -> None:
        self.auth = auth

    def initialize(self) -> None:
        with self.auth._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_tokens (
                    id INTEGER PRIMARY KEY,
                    token_digest TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    owner_id INTEGER NOT NULL REFERENCES users(id),
                    owner_epoch TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    allowed_networks TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revoke_reason TEXT,
                    last_used_at TEXT,
                    last_ip TEXT
                );
                CREATE INDEX IF NOT EXISTS agent_tokens_name ON agent_tokens(name, revoked_at);
                """
            )

    # -- administration (local CLI) -----------------------------------------------------------

    def issue(self, actor: str, name: str, owner: str, scopes: list[str] | tuple[str, ...], days: int = DEFAULT_DAYS,
              networks: list[str] | tuple[str, ...] | None = None) -> tuple[str, dict[str, Any]]:
        """Create a token; returns it once (never stored in clear) with its public description."""

        name = name.strip().casefold()
        if not AGENT_NAME.fullmatch(name):
            raise AuthError("nom d'agent invalide (3 à 64 caractères : a-z, 0-9, point, tiret, souligné)")
        wanted = sorted({s.strip() for s in scopes if s.strip()})
        if not wanted or any(s not in SCOPES for s in wanted):
            raise AuthError(f"portées autorisées : {', '.join(sorted(SCOPES))}")
        if not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
            raise AuthError(f"durée entre 1 et {MAX_DAYS} jours")
        allowed = _networks(networks)
        owner_name = normalize_username(owner)
        token = TOKEN_PREFIX + crypto.new_token()
        now = self.auth._now()
        with self.auth._write() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (owner_name,)).fetchone()
            if row is None or not row["active"]:
                raise AuthError("propriétaire inconnu ou désactivé")
            role = WebRole(row["role"])
            if row["must_change_password"]:
                raise AuthError("le propriétaire doit d'abord changer son mot de passe temporaire")
            if role in self.auth.policy.mfa_required_roles and not row["mfa_enabled"]:
                raise AuthError("le propriétaire doit d'abord enrôler sa MFA")
            for scope in wanted:
                if not role.allows(SCOPES[scope]):
                    raise AuthError(f"la portée {scope} exige un propriétaire {SCOPES[scope].value} ou plus")
            active = db.execute("SELECT COUNT(*) FROM agent_tokens WHERE name = ? AND revoked_at IS NULL AND expires_at > ?",
                                (name, _iso(now))).fetchone()[0]
            if active >= MAX_ACTIVE_PER_AGENT:
                raise AuthError(f"déjà {active} jetons actifs pour cet agent : révoquez l'ancien d'abord")
            expires = now + timedelta(days=days)
            cursor = db.execute(
                "INSERT INTO agent_tokens(token_digest, name, owner_id, owner_epoch, scopes, allowed_networks, created_at,"
                " created_by, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (crypto.token_digest(token), name, row["id"], row["password_changed_at"], ",".join(wanted),
                 json.dumps(allowed), _iso(now), actor, _iso(expires)))
            self.auth._event(db, actor, "agent.token_issue", "ok", target=name,
                             detail=f"id={cursor.lastrowid},owner={owner_name},scopes={','.join(wanted)},days={days},"
                                    f"networks={len(allowed)}")
        return token, {"id": cursor.lastrowid, "agent": name, "owner": owner_name, "scopes": wanted,
                       "networks": allowed, "expires_at": _iso(expires)}

    def revoke(self, actor: str, *, token_id: int | None = None, name: str | None = None,
               owner: str | None = None) -> int:
        """Revoke one token (by id), every token of an agent, or every token of an owner."""

        if [token_id, name, owner].count(None) != 2:
            raise AuthError("indiquez exactement un critère : --id, --agent ou --owner")
        now = _iso(self.auth._now())
        with self.auth._write() as db:
            if token_id is not None:
                where, value, target = "id = ?", token_id, f"id={token_id}"
            elif name is not None:
                where, value, target = "name = ?", name.strip().casefold(), name.strip().casefold()
            else:
                owner_name = normalize_username(owner or "")
                where = "owner_id = (SELECT id FROM users WHERE username = ?)"
                value, target = owner_name, f"owner={owner_name}"
            count = db.execute(f"UPDATE agent_tokens SET revoked_at = ?, revoke_reason = 'revoked_by_admin'"
                               f" WHERE {where} AND revoked_at IS NULL", (now, value)).rowcount
            self.auth._event(db, actor, "agent.token_revoke", "ok", target=target, detail=f"count={count}")
        return count

    def list(self) -> list[dict[str, Any]]:
        now = self.auth._now()
        with self.auth._connect() as db:
            rows = db.execute("SELECT t.*, u.username AS owner FROM agent_tokens t JOIN users u ON u.id = t.owner_id"
                              " ORDER BY t.id DESC").fetchall()
        result = []
        for row in rows:
            state = "revoked" if row["revoked_at"] else ("expired" if _parse(row["expires_at"]) <= now else "active")
            result.append({"id": row["id"], "agent": row["name"], "owner": row["owner"], "scopes": row["scopes"].split(","),
                           "networks": json.loads(row["allowed_networks"]), "state": state,
                           "created_at": row["created_at"], "created_by": row["created_by"],
                           "expires_at": row["expires_at"], "revoked_at": row["revoked_at"],
                           "last_used_at": row["last_used_at"], "last_ip": row["last_ip"]})
        return result

    # -- use (web) ----------------------------------------------------------------------------

    def authenticate(self, token: str, client_ip: str | None) -> AgentAuthResult:
        """Resolve a bearer token. Raises AuthUnavailable on storage errors (the caller answers 503)."""

        key = [f"agent-ip:{client_ip or 'unknown'}"]
        with self.auth._write() as db:
            wait = self.auth._throttle_wait(db, key)
            if wait:
                return AgentAuthResult("throttled", retry_after_seconds=wait)
            row = None
            if isinstance(token, str) and token.startswith(TOKEN_PREFIX) and 30 <= len(token) <= 128:
                row = db.execute(
                    "SELECT t.*, u.username, u.role, u.active, u.must_change_password, u.password_changed_at"
                    " FROM agent_tokens t JOIN users u ON u.id = t.owner_id WHERE t.token_digest = ?",
                    (crypto.token_digest(token),)).fetchone()
            if row is None:
                self.auth._record_failure(db, key)
                return AgentAuthResult("invalid")
            reason = self._refusal(row, client_ip)
            if reason:
                self.auth._record_failure(db, key)
                if reason in ("owner_password_changed", "owner_disabled") and row["revoked_at"] is None:
                    db.execute("UPDATE agent_tokens SET revoked_at = ?, revoke_reason = ? WHERE id = ?",
                               (_iso(self.auth._now()), reason, row["id"]))
                self.auth._event(db, f"agent:{row['name']}", "agent.auth", "refused", target=row["username"],
                                 client_ip=client_ip, detail=f"id={row['id']},reason={reason}")
                return AgentAuthResult("refused")
            db.execute("UPDATE agent_tokens SET last_used_at = ?, last_ip = ? WHERE id = ?",
                       (_iso(self.auth._now()), client_ip, row["id"]))
        principal = AgentPrincipal(token_id=row["id"], name=row["name"], owner=row["username"],
                                   owner_role=WebRole(row["role"]), scopes=frozenset(row["scopes"].split(",")),
                                   expires_at=_parse(row["expires_at"]))
        return AgentAuthResult("ok", principal=principal)

    def _refusal(self, row: sqlite3.Row, client_ip: str | None) -> str | None:
        if row["revoked_at"] is not None:
            return "revoked"
        if self.auth._now() >= _parse(row["expires_at"]):
            return "expired"
        if not row["active"]:
            return "owner_disabled"
        if row["must_change_password"] or row["password_changed_at"] != row["owner_epoch"]:
            return "owner_password_changed"
        if not _address_allowed(json.loads(row["allowed_networks"]), client_ip):
            return "address_refused"
        return None
