"""Local account administration, run on the server by someone with shell access (``astra auth ...``).

This is also the documented recovery path: losing the TOTP device and every recovery code, or the
last administrator password, is fixed here (``reset-mfa``, ``reset-password``), never through a
public web endpoint. Generated passwords are printed once to stdout and stored only as Argon2id
hashes; nothing is written to logs.
"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import string
import sys

from ..config import WebConfig
from . import mfa_crypto
from .store import AuthError, AuthStore, WebRole


def _operator() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except (KeyError, OSError):
        return "cli:unknown"


def _generated_password() -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@#%"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(24))
        if any(c.islower() for c in candidate) and any(c.isupper() for c in candidate) and any(c.isdigit() for c in candidate):
            return candidate


def _prompt_password(store: AuthStore, username: str) -> str:
    if not sys.stdin.isatty():
        raise AuthError("saisie interactive impossible (pas de terminal) : utilisez --generate")
    first = getpass.getpass("Nouveau mot de passe : ")
    problems = store.password_problems(first, username)
    if problems:
        raise AuthError("mot de passe refusé : " + ", ".join(problems))
    if getpass.getpass("Confirmez : ") != first:
        raise AuthError("les deux saisies diffèrent")
    return first


def run(args: argparse.Namespace) -> int:
    """Run one ``astra auth`` action; key-ring and storage failures become clear errors (exit 1)."""

    from .store import AuthUnavailable

    try:
        return _run(args)
    except (mfa_crypto.MfaKeyError, AuthUnavailable) as exc:
        raise AuthError(str(exc)) from exc


def _run(args: argparse.Namespace) -> int:
    web = WebConfig.from_env()
    actor = _operator()
    action = args.auth_action
    key_path = web.mfa_key_path
    if action == "init-admin" and not key_path.exists():
        # The first administrator must enrol TOTP: create the key ring first (outside the database).
        key_id = mfa_crypto.init_key_file(key_path)
        print(f"MFA key ring created: {key_path} (key {key_id}); back it up separately from the database.", file=sys.stderr)
    if action == "mfa-key-init":
        key_id = mfa_crypto.init_key_file(key_path, group=args.group)
        print(json.dumps({"mfa_key_file": str(key_path), "active_key": key_id}))
        return 0
    cipher = mfa_crypto.load_cipher(key_path) if key_path.exists() else None
    store = AuthStore(web.auth_database_path, mfa_cipher=cipher, mfa_key_path=key_path if cipher else None)
    store.initialize()
    if action == "mfa-status":
        print(json.dumps({"mfa_key_file": str(key_path), "key_ring": list(cipher.key_ids) if cipher else None,
                          "active_key": cipher.active_key_id if cipher else None,
                          "keys_in_use": sorted(store.mfa_key_ids_in_use()),
                          "plaintext_secrets": store.plaintext_mfa_secrets()}))
        return 0
    if action == "mfa-reencrypt":
        print(json.dumps(store.reencrypt_mfa_secrets(actor)))
        return 0
    if action == "mfa-key-rotate":
        new_id = mfa_crypto.add_active_key(key_path)
        counts = store.reencrypt_mfa_secrets(actor)  # the store reloads the ring because the file changed
        print(json.dumps({"active_key": new_id, **counts}))
        print("Running dashboards reload the key ring on their next MFA operation. Retire the old key only"
              " after mfa-status shows it is no longer in use.", file=sys.stderr)
        return 0
    if action == "mfa-key-retire":
        mfa_crypto.remove_key(key_path, args.kid, store.mfa_key_ids_in_use())
        store.record(actor, "mfa.key_retired", "ok", detail=f"kid={args.kid}")
        print(json.dumps({"retired": args.kid}))
        return 0
    if action == "init-admin":
        password = _generated_password() if args.generate else _prompt_password(store, args.username)
        user = store.create_user(actor, args.username, WebRole.ADMIN, password, must_change_password=args.generate,
                                 first_admin=True)
        print(json.dumps({"created": user.to_public()}, ensure_ascii=False))
        if args.generate:
            # Shown once; the operator copies it now. It must be changed at the first login.
            print(f"Mot de passe initial (affiché une seule fois) : {password}", file=sys.stderr)
        print("Connectez-vous : l'enrôlement MFA (TOTP) est obligatoire pour ce compte.", file=sys.stderr)
        return 0
    if action == "add-user":
        password = _generated_password()
        user = store.create_user(actor, args.username, WebRole(args.role), password, must_change_password=True)
        print(json.dumps({"created": user.to_public()}, ensure_ascii=False))
        print(f"Mot de passe initial (affiché une seule fois) : {password}", file=sys.stderr)
        return 0
    if action == "list":
        print(json.dumps({"users": [u.to_public() for u in store.list_users()]}, ensure_ascii=False, indent=2))
        return 0
    if action in {"disable", "enable"}:
        store.set_active(actor, args.username, action == "enable")
    elif action == "set-role":
        store.set_role(actor, args.username, WebRole(args.role))
    elif action == "revoke-sessions":
        print(json.dumps({"revoked": store.revoke_user_sessions(actor, args.username)}))
        return 0
    elif action == "reset-mfa":
        store.reset_mfa(actor, args.username)
    elif action == "reset-password":
        temporary = store.reset_password(actor, args.username)
        print(f"Mot de passe temporaire (affiché une seule fois) : {temporary}", file=sys.stderr)
    elif action == "cleanup":
        print(json.dumps({"sessions_removed": store.cleanup()}))
        return 0
    elif action == "events":
        print(json.dumps({"events": store.events(limit=args.limit)}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"ok": True, "action": action, "username": getattr(args, "username", None)}, ensure_ascii=False))
    return 0
