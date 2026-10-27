"""AUTH-2 regression tests: encrypted TOTP secrets, key ring rotation, migration, backups, recovery
codes, logs without cookies, proxy secret rotation and the production Python lock.

Offline only (Flask test client, temporary SQLite files, controlled clock). Each protection has a
test that fails without it.
"""

import contextlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

import pyotp

from astra.auth import AuthStore, AuthUnavailable, MfaKeyError, WebRole, mfa_crypto
from astra.auth.crypto import token_digest
from astra.config import WebConfig
from test_webapp import HOST, PASSWORD, PROXY_SECRET, SESSION_COOKIE, WebAppCase


def stored_secret(store, username, column="mfa_secret"):
    with contextlib.closing(sqlite3.connect(store.path)) as db:
        return db.execute(f"SELECT {column} FROM users WHERE username = ?", (username,)).fetchone()[0]


def write_secret(store, username, value, column="mfa_secret"):
    with contextlib.closing(sqlite3.connect(store.path)) as db:
        db.execute(f"UPDATE users SET {column} = ? WHERE username = ?", (value, username))
        db.commit()


class MfaCase(WebAppCase):
    def enrolled_admin(self, name="boss"):
        """An ADMIN with TOTP enrolled; returns the plaintext secret (known only to the test) and codes."""

        self.add_user(name, WebRole.ADMIN)
        client = self.client()
        self.login(client, name)
        secret, codes, _ = self.enroll(client)
        self.post(client, "/logout", {"csrf_token": self.page_csrf(client)})
        return secret, codes

    def mfa_login(self, name, code, auth=None):
        app = self.make_app(auth=auth) if auth else self.app
        client = self.client(ip="203.0.113.60", app=app)
        page = client.get("/login")
        self.post(client, "/login", {"username": name, "password": PASSWORD, "csrf_token": self.csrf(page)})
        page = client.get("/login/mfa")
        return self.post(client, "/login/mfa", {"code": code, "csrf_token": self.csrf(page)})

    def code(self, secret, step_offset=0):
        self.clock.advance(30)  # a fresh time step, so the replay guard does not refuse a reused code
        return pyotp.TOTP(secret).at(self.clock.now.timestamp())


class EncryptionAtRestTests(MfaCase):
    def test_secret_is_stored_encrypted_and_bound_to_the_account(self):
        secret, _ = self.enrolled_admin()
        stored = stored_secret(self.auth, "boss")
        self.assertTrue(stored.startswith("astra-mfa:v1:" + self.cipher.active_key_id + ":"))
        with contextlib.closing(sqlite3.connect(self.auth.path)) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(secret, dump)
        self.assertEqual(self.mfa_login("boss", self.code(secret)).status_code, 303)

    def test_each_encryption_uses_a_fresh_nonce(self):
        sealed = {self.cipher.seal(1, "active", "JBSWY3DPEHPK3PXP") for _ in range(50)}
        self.assertEqual(len(sealed), 50)
        nonces = {value.split(":")[3] for value in sealed}
        self.assertEqual(len(nonces), 50)

    def test_ciphertext_moved_to_another_account_or_column_fails(self):
        self.enrolled_admin("boss")
        self.enrolled_admin("chief")
        boss = stored_secret(self.auth, "boss")
        boss_id = self.auth.get_user("boss").id
        chief_id = self.auth.get_user("chief").id
        with self.assertRaises(MfaKeyError):
            self.cipher.open(chief_id, "active", boss)
        with self.assertRaises(MfaKeyError):
            self.cipher.open(boss_id, "pending", boss)
        write_secret(self.auth, "chief", boss)
        response = self.mfa_login("chief", "123456")
        self.assertEqual(response.status_code, 503)

    def test_pending_enrolment_secret_is_encrypted_too(self):
        self.add_user("boss", WebRole.ADMIN)
        client = self.client()
        self.login(client, "boss")
        page = client.get("/mfa/setup").get_data(as_text=True)
        shown = re.search(r'<code class="secret">([A-Z2-7]{16,})</code>', page).group(1)
        pending = stored_secret(self.auth, "boss", "mfa_pending_secret")
        self.assertTrue(pending.startswith("astra-mfa:v1:"))
        self.assertNotIn(shown, pending)


class FailClosedTests(MfaCase):
    def test_wrong_key_refuses_totp_but_keeps_mfa_enabled_and_recovery_codes_work(self):
        secret, codes = self.enrolled_admin()
        other = Path(self.temp.name) / "other.key"
        mfa_crypto.init_key_file(other)
        wrong = AuthStore(self.auth.path, clock=self.clock, mfa_cipher=mfa_crypto.load_cipher(other))
        self.assertEqual(self.mfa_login("boss", self.code(secret), auth=wrong).status_code, 503)
        self.assertTrue(self.auth.get_user("boss").mfa_enabled)
        self.assertIn("mfa.secret_unavailable", json.dumps(self.auth.events(20)))
        self.assertEqual(self.mfa_login("boss", codes[0], auth=wrong).status_code, 303)

    def test_missing_key_ring_refuses_totp_and_enrolment(self):
        secret, _ = self.enrolled_admin()
        keyless = AuthStore(self.auth.path, clock=self.clock)
        self.assertEqual(self.mfa_login("boss", self.code(secret), auth=keyless).status_code, 503)
        self.add_user("chief", WebRole.ADMIN)
        client = self.client(app=self.make_app(auth=keyless))
        self.login(client, "chief")
        self.assertEqual(client.get("/mfa/setup").status_code, 503)
        self.assertTrue(self.auth.get_user("boss").mfa_enabled)

    def test_corrupted_ciphertext_is_refused(self):
        secret, _ = self.enrolled_admin()
        stored = stored_secret(self.auth, "boss")
        prefix, data = stored.rsplit(":", 1)
        flipped = data[:-2] + ("A" if data[-2] != "A" else "B") + data[-1]
        for bad in (prefix + ":" + flipped, stored[:-6], "astra-mfa:v1:unknownkid:AAAA:BBBB", "astra-mfa:v1:garbage"):
            write_secret(self.auth, "boss", bad)
            self.assertEqual(self.mfa_login("boss", self.code(secret)).status_code, 503, bad[:40])
        self.assertTrue(self.auth.get_user("boss").mfa_enabled)

    def test_malformed_key_files_are_refused(self):
        root = Path(self.temp.name)
        for name, body in (("empty.key", ""), ("header.key", "astra-mfa-keys v1\n"), ("short.key", "astra-mfa-keys v1\nk1abc AAAA\n"),
                           ("dupe.key", "astra-mfa-keys v1\nkaaa " + "A" * 43 + "\nkaaa " + "B" * 43 + "\n"),
                           ("noheader.key", "kaaa " + "A" * 43 + "\n")):
            (root / name).write_text(body, encoding="ascii")
            with self.assertRaises(MfaKeyError, msg=name):
                mfa_crypto.load_cipher(root / name)
        with self.assertRaises(MfaKeyError):
            mfa_crypto.load_cipher(root / "absent.key")

    @unittest.skipUnless(os.name == "posix", "file mode check is POSIX only")
    def test_world_readable_key_file_is_refused(self):
        path = Path(self.temp.name) / "mfa.key"
        os.chmod(path, 0o644)
        with self.assertRaises(MfaKeyError):
            mfa_crypto.load_cipher(path)


class RotationAndMigrationTests(MfaCase):
    def test_rotation_reencrypts_and_old_key_can_then_be_retired(self):
        secret, _ = self.enrolled_admin()
        key_file = Path(self.temp.name) / "mfa.key"
        old_id = self.cipher.active_key_id
        new_id = mfa_crypto.add_active_key(key_file)
        rotated = mfa_crypto.load_cipher(key_file)
        self.assertEqual((rotated.active_key_id, set(rotated.key_ids)), (new_id, {new_id, old_id}))
        store = AuthStore(self.auth.path, clock=self.clock, mfa_cipher=rotated)
        # Before re-encryption the old key is still in use: it cannot be retired.
        with self.assertRaises(MfaKeyError):
            mfa_crypto.remove_key(key_file, old_id, store.mfa_key_ids_in_use())
        self.assertEqual(store.reencrypt_mfa_secrets("test")["rotated"], 1)
        self.assertEqual(store.mfa_key_ids_in_use(), {new_id})
        with self.assertRaises(MfaKeyError):
            mfa_crypto.remove_key(key_file, new_id, store.mfa_key_ids_in_use())  # the active key
        mfa_crypto.remove_key(key_file, old_id, store.mfa_key_ids_in_use())
        final = AuthStore(self.auth.path, clock=self.clock, mfa_cipher=mfa_crypto.load_cipher(key_file))
        self.assertEqual(self.mfa_login("boss", self.code(secret), auth=final).status_code, 303)

    def test_plaintext_secret_is_refused_until_migrated(self):
        secret, _ = self.enrolled_admin()
        write_secret(self.auth, "boss", secret)  # a database written before AUTH-2
        self.assertEqual(self.auth.plaintext_mfa_secrets(), 1)
        self.assertEqual(self.mfa_login("boss", self.code(secret)).status_code, 503)
        counts = self.auth.reencrypt_mfa_secrets("cli:admin")
        self.assertEqual((counts["migrated"], counts["unreadable"]), (1, 0))
        self.assertEqual(self.auth.plaintext_mfa_secrets(), 0)
        self.assertNotIn(secret, stored_secret(self.auth, "boss"))
        self.assertEqual(self.mfa_login("boss", self.code(secret)).status_code, 303)

    def test_reencryption_keeps_unreadable_secrets_and_deletes_nothing(self):
        self.enrolled_admin()
        write_secret(self.auth, "boss", "astra-mfa:v1:lostkey:AAAAAAAAAAAAAAAA:BBBBBBBBBBBBBBBBBBBBBBBB")
        counts = self.auth.reencrypt_mfa_secrets("cli:admin")
        self.assertEqual(counts["unreadable"], 1)
        self.assertTrue(stored_secret(self.auth, "boss").startswith("astra-mfa:v1:lostkey:"))
        self.assertTrue(self.auth.get_user("boss").mfa_enabled)

    def test_cli_status_rotate_and_retire(self):
        from astra.cli import main

        self.enrolled_admin()
        key_file = Path(self.temp.name) / "mfa.key"
        env = {"ASTRA_AUTH_DATABASE": str(self.auth.path), "ASTRA_MFA_KEY_FILE": str(key_file)}

        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict("os.environ", env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["auth", *argv])
            return code, out.getvalue(), err.getvalue()

        code, out, _ = run("mfa-status")
        status = json.loads(out)
        self.assertEqual((code, status["plaintext_secrets"], status["keys_in_use"]), (0, 0, [self.cipher.active_key_id]))
        old_id = self.cipher.active_key_id
        code, out, _ = run("mfa-key-rotate")
        self.assertEqual((code, json.loads(out)["rotated"]), (0, 1))
        code, out, _ = run("mfa-key-retire", "--kid", old_id)
        self.assertEqual(code, 0)
        self.assertNotIn(old_id, mfa_crypto.load_cipher(key_file).key_ids)
        code, _, err = run("mfa-key-init")
        self.assertEqual(code, 1)  # refuses to overwrite an existing key ring
        self.assertNotIn(key_file.read_text(encoding="ascii").splitlines()[1].split()[1], out + err)


class BackupTests(MfaCase):
    def test_database_backup_holds_only_ciphertext_and_needs_the_separate_key(self):
        secret, _ = self.enrolled_admin()
        backup = Path(self.temp.name) / "auth-backup.sqlite3"
        with contextlib.closing(sqlite3.connect(self.auth.path)) as src, contextlib.closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
        with contextlib.closing(sqlite3.connect(backup)) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(secret, dump)
        restored = AuthStore(backup, clock=self.clock, mfa_cipher=self.cipher)
        self.assertEqual(self.mfa_login("boss", self.code(secret), auth=restored).status_code, 303)
        keyless = AuthStore(backup, clock=self.clock)
        self.assertEqual(self.mfa_login("boss", self.code(secret), auth=keyless).status_code, 503)

    def test_key_file_lives_outside_the_database_and_git(self):
        self.assertNotEqual(Path(self.web.mfa_key_path).parent / "auth.sqlite3", self.auth.path / "x")
        self.assertEqual(WebConfig.from_env({"ASTRA_MFA_KEY_FILE": "/etc/astra/mfa.key"}).mfa_key_path, Path("/etc/astra/mfa.key"))
        gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.key", gitignore)


class RecoveryCodeTests(MfaCase):
    def test_codes_have_100_bits_are_stored_as_digests_and_work_once(self):
        _secret, codes = self.enrolled_admin()
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        for code in codes:
            self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{5}(-[A-HJ-NP-Z2-9]{5}){3}$")  # 20 symbols of a 32-letter alphabet
        with contextlib.closing(sqlite3.connect(self.auth.path)) as db:
            stored = [row[0] for row in db.execute("SELECT code_digest FROM recovery_codes")]
        self.assertEqual(sorted(stored), sorted(token_digest(c.replace("-", "")) for c in codes))
        self.assertFalse(any(c.replace("-", "") in "".join(stored) for c in codes))
        self.assertEqual(self.mfa_login("boss", codes[3]).status_code, 303)
        self.assertEqual(self.mfa_login("boss", codes[3]).status_code, 401)


class SessionAndLogTests(MfaCase):
    def test_cookies_tokens_and_passwords_never_reach_the_logs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            client = self.session_client("alice", WebRole.READ_ONLY)
            token = client.get_cookie(SESSION_COOKIE, domain=HOST).value
            client.get("/api/summary")
            self.post(client, "/logout", {"csrf_token": self.page_csrf(client)})
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        text = stream.getvalue()
        for secret in (token, PASSWORD, PROXY_SECRET):
            self.assertNotIn(secret, text)

    def test_revoked_session_and_anonymous_access_are_refused_on_every_private_route(self):
        client = self.session_client("ops", WebRole.OPERATOR)
        token = client.get_cookie(SESSION_COOKIE, domain=HOST).value
        self.auth.revoke_user_sessions("test", "ops")
        replay = self.client()
        replay.set_cookie(SESSION_COOKIE, token, domain=HOST)
        for route in ("/api/session", "/api/summary", "/api/orders", "/api/controls", "/api/audit", "/static/app.js"):
            self.assertEqual(replay.get(route).status_code, 401, route)
            self.assertEqual(self.client().get(route).status_code, 401, route)


class ProxySecretRotationTests(MfaCase):
    def test_current_and_previous_secrets_are_accepted_others_refused(self):
        from astra.webapp import create_app, load_proxy_secret

        path = Path(self.temp.name) / "proxy.secret"
        new, old = "n" * 40, PROXY_SECRET
        path.write_text(f"{new}\n{old}\n", encoding="ascii")
        os.chmod(path, 0o640)  # as deployed (root:astra 0640); rewrites below keep this mode
        secrets = load_proxy_secret(path)
        app = create_app(self.config, self.web, auth=self.auth, secret_key=self.key, store=self.store, proxy_secret=secrets)
        for value, status in ((new, 200), (old, 200), ("x" * 40, 403), ("", 403)):
            client = self.client(app=app)
            self.assertEqual(client.get("/api/health", headers={"X-Astra-Proxy": value}).status_code, status, value[:4])
        path.write_text(f"{new}\n{old}\n{'z' * 40}\n", encoding="ascii")
        with self.assertRaisesRegex(RuntimeError, "one or two"):
            load_proxy_secret(path)  # at most two (current and previous)
        path.write_text("short\n", encoding="ascii")
        with self.assertRaisesRegex(RuntimeError, "one or two"):
            load_proxy_secret(path)

    @unittest.skipUnless(os.name == "posix", "file mode check is POSIX only")
    def test_world_readable_proxy_secret_is_refused(self):
        from astra.webapp import load_proxy_secret

        path = Path(self.temp.name) / "proxy.secret"
        path.write_text("n" * 40 + "\n", encoding="ascii")
        os.chmod(path, 0o644)
        with self.assertRaisesRegex(RuntimeError, "other users"):
            load_proxy_secret(path)


class ReviewFixTests(MfaCase):
    """Regression tests for the AUTH-2 review findings (M1, L1-L6)."""

    def test_running_store_picks_up_a_rotation_without_restart(self):
        secret, _ = self.enrolled_admin()
        key_file = Path(self.temp.name) / "mfa.key"
        running = AuthStore(self.auth.path, clock=self.clock, mfa_cipher=mfa_crypto.load_cipher(key_file), mfa_key_path=key_file)
        app = self.make_app(auth=running)
        new_id = mfa_crypto.add_active_key(key_file)
        os.utime(key_file, ns=(1, 2_000_000_000_000_000_000))  # make the change visible even on coarse clocks
        AuthStore(self.auth.path, clock=self.clock, mfa_cipher=mfa_crypto.load_cipher(key_file)).reencrypt_mfa_secrets("cli")
        client = self.client(ip="203.0.113.61", app=app)
        page = client.get("/login")
        self.post(client, "/login", {"username": "boss", "password": PASSWORD, "csrf_token": self.csrf(page)})
        page = client.get("/login/mfa")
        response = self.post(client, "/login/mfa", {"code": self.code(secret), "csrf_token": self.csrf(page)})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(running.mfa_cipher.active_key_id, new_id)

    def test_replacement_with_the_same_mtime_is_still_reloaded(self):
        key_file = Path(self.temp.name) / "mfa.key"
        store = AuthStore(self.auth.path, clock=self.clock, mfa_cipher=mfa_crypto.load_cipher(key_file), mfa_key_path=key_file)
        old_id = store._cipher().active_key_id
        before = key_file.stat()
        new_id = mfa_crypto.add_active_key(key_file)
        os.utime(key_file, ns=(before.st_atime_ns, before.st_mtime_ns))  # same mtime, as after a restore
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(store._cipher().active_key_id, new_id)

    def test_enrolment_with_a_broken_key_ring_is_throttled(self):
        self.add_user("boss", WebRole.ADMIN)
        keyless = AuthStore(self.auth.path, clock=self.clock)
        client = self.client(app=self.make_app(auth=keyless))
        self.login(client, "boss")
        statuses = [client.get("/mfa/setup").status_code for _ in range(12)]
        self.assertTrue(all(s == 503 for s in statuses), statuses)
        events = [e for e in self.auth.events(500) if e["action"] == "mfa.secret_unavailable"]
        self.assertLessEqual(len(events), 6)

    def test_migration_refuses_values_that_are_not_totp_secrets(self):
        self.enrolled_admin("boss")
        self.enrolled_admin("chief")
        write_secret(self.auth, "boss", "astra-mfa:v1::AAAA:BBBB")  # empty key id
        write_secret(self.auth, "chief", "not base32 \u00e9 at all")
        counts = self.auth.reencrypt_mfa_secrets("cli")
        self.assertEqual((counts["migrated"], counts["unreadable"]), (0, 2))
        self.assertEqual(stored_secret(self.auth, "boss"), "astra-mfa:v1::AAAA:BBBB")
        self.assertEqual(self.auth.plaintext_mfa_secrets(), 1)  # the non-sealed value is reported, not hidden
        with self.assertRaises(MfaKeyError):
            self.cipher.seal(1, "active", "not-a-secret")

    def test_unicode_digits_are_refused_not_errors(self):
        self.enrolled_admin()
        for code in ("\u0661\u0662\u0663\u0664\u0665\u0666", "12345\u00b2"):
            self.assertEqual(self.mfa_login("boss", code).status_code, 401, code.encode("unicode_escape"))

    def test_attempts_with_a_broken_key_ring_are_throttled(self):
        self.enrolled_admin()
        keyless = AuthStore(self.auth.path, clock=self.clock)
        statuses = [self.mfa_login("boss", "123456", auth=keyless).status_code for _ in range(12)]
        self.assertTrue(all(s in (503, 429) for s in statuses), statuses)
        events = [e for e in self.auth.events(500) if e["action"] == "mfa.secret_unavailable"]
        self.assertLessEqual(len(events), 6)  # the throttle stops the audit from growing at will

    def test_public_mode_refuses_to_start_without_a_separate_key_ring(self):
        from astra import webapp

        root = Path(self.temp.name)
        secret_file = root / "proxy.secret"
        secret_file.write_text("s" * 40 + "\n", encoding="ascii")
        env = {"ASTRA_DATABASE": str(root / "astra.sqlite3"), "ASTRA_AUTH_DATABASE": str(root / "auth.sqlite3"),
               "ASTRA_WEB_SECRET_FILE": str(root / "web.key"), "ASTRA_PUBLIC_HOSTS": "astra.example", "ASTRA_HTTPS": "1",
               "ASTRA_TRUSTED_PROXY": "1", "ASTRA_PROXY_SECRET_FILE": str(secret_file)}
        with mock.patch.object(webapp, "PRODUCTION_PYTHON", sys.version_info[:2]):
            with mock.patch.dict("os.environ", {**env, "ASTRA_MFA_KEY_FILE": str(root / "missing.key")}), \
                    self.assertRaisesRegex(RuntimeError, "MFA key ring"):
                webapp.create_app_from_env()
            with mock.patch.dict("os.environ", {**env, "ASTRA_MFA_KEY_FILE": str(root / "mfa.key")}), \
                    self.assertRaisesRegex(RuntimeError, "same directory"):
                webapp.create_app_from_env()

    def test_keys_never_appear_in_a_repr_and_write_errors_are_key_errors(self):
        key = mfa_crypto.MfaKey("kabc", b"\x01" * 32)
        self.assertNotIn("\\x01", repr(key))
        blocker = Path(self.temp.name) / "afile"
        blocker.write_text("x", encoding="ascii")
        with self.assertRaises(MfaKeyError):
            mfa_crypto.init_key_file(blocker / "mfa.key")  # parent is a file: OSError becomes MfaKeyError

    def test_short_proxy_secret_is_refused(self):
        from astra.webapp import create_app

        with self.assertRaises(RuntimeError):
            create_app(self.config, self.web, auth=self.auth, secret_key=self.key, store=self.store, proxy_secret="")

    @unittest.skipUnless(os.name == "posix", "ownership and modes are POSIX only")
    def test_rotation_keeps_the_file_mode(self):
        key_file = Path(self.temp.name) / "mfa.key"
        os.chmod(key_file, 0o640)
        mfa_crypto.add_active_key(key_file)
        self.assertEqual(key_file.stat().st_mode & 0o777, 0o640)

    @unittest.skipUnless(os.name == "posix", "symlinks and modes are POSIX only")
    def test_lock_file_symlink_is_refused(self):
        key_file = Path(self.temp.name) / "mfa.key"
        target = Path(self.temp.name) / "planted"
        lock = Path(self.temp.name) / ".mfa.key.lock"
        lock.unlink(missing_ok=True)  # left by the key ring setup
        os.symlink(target, lock)
        before = key_file.read_bytes()
        with self.assertRaises(MfaKeyError):
            mfa_crypto.add_active_key(key_file)
        self.assertFalse(target.exists())
        self.assertEqual(key_file.read_bytes(), before)

    def test_reload_fingerprint_includes_the_change_time(self):
        key_file = Path(self.temp.name) / "mfa.key"
        store = AuthStore(self.auth.path, clock=self.clock, mfa_key_path=key_file)
        info = key_file.stat()
        self.assertEqual(store._key_fingerprint(), (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))


class PythonLockTests(unittest.TestCase):
    def test_production_entry_refuses_another_python(self):
        from astra import webapp

        self.assertEqual(webapp.PRODUCTION_PYTHON, (3, 14))
        with mock.patch.object(sys, "version_info", (3, 12, 3, "final", 0)), self.assertRaisesRegex(RuntimeError, "3.14"):
            webapp.create_app_from_env()
        version_file = Path(__file__).resolve().parents[1] / "deploy" / "PYTHON_VERSION"
        self.assertEqual(version_file.read_text(encoding="ascii").strip(), "3.14")


class StoreUnitTests(unittest.TestCase):
    def test_seal_without_key_ring_is_refused(self):
        store = AuthStore(Path("unused.sqlite3"))
        with self.assertRaises(MfaKeyError):
            store._seal(1, "active", "JBSWY3DPEHPK3PXP")
        with self.assertRaises(AuthUnavailable), store._mfa_guard("nobody"):
            raise MfaKeyError("probe")

    def test_init_key_file_refuses_to_overwrite(self):
        from _support import workspace_tempdir

        with workspace_tempdir() as temp:
            path = Path(temp) / "mfa.key"
            mfa_crypto.init_key_file(path)
            before = path.read_bytes()
            with self.assertRaises(MfaKeyError):
                mfa_crypto.init_key_file(path)
            self.assertEqual(path.read_bytes(), before)
            shutil.copy(path, Path(temp) / "copy.key")


if __name__ == "__main__":
    unittest.main()
