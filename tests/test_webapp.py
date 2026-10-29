"""Security and regression tests of the authenticated dashboard (astra.webapp, astra.auth).

Every protection has a negative test that fails if the protection is removed. Offline only: the
Flask test client, temporary SQLite files and a controlled clock; no network, no broker.
"""

import ast
import contextlib
import io
import json
import re
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pyotp
from flask.testing import FlaskClient

from _support import workspace_tempdir
from astra.auth import AuthStore, AuthUnavailable, WebRole
from astra.auth.mfa_crypto import init_key_file, load_cipher
from astra.auth.store import SessionStage
from astra.config import AppConfig, WebConfig
from astra.engine import run_paper_replay
from astra.execution import ExecutionStore, TradingControls
from astra.registry import ModelRegistry
from astra.storage import BacktestStore
from astra.webapp import PRE_COOKIE, SESSION_COOKIE, create_app
from test_engine import synthetic_bars

HOST = "astra.test"
PROXY_SECRET = "p" * 40  # test value; the real one is generated on the host
ORIGIN = f"https://{HOST}"
PASSWORD = "test-Horse-Battery-9"
READ_ONLY_ROUTES = ["/api/session", "/api/status", "/api/summary", "/api/runs", "/api/models", "/api/experiments"]
OPERATOR_ROUTES = ["/api/orders", "/api/controls", "/api/reconciliations", "/api/audit"]
ADMIN_ROUTES = ["/api/admin/users", "/api/admin/auth-events"]
# Market data, backtests and paper simulations (astra.labweb): reading is READ_ONLY, requests are OPERATOR.
LAB_READ_ROUTES = ["/api/market/overview", "/api/market/quality", "/api/market/candles", "/api/lab/catalog",
                   "/api/lab/backtests", "/api/lab/backtests/<int:backtest_id>", "/api/sim/sessions",
                   "/api/sim/sessions/<int:session_id>", "/api/system/jobs", "/api/learn/overview",
                   "/api/mt5/overview", "/api/market/chart", "/api/dashboard/overview", "/api/mt5/requests",
                   "/api/mt5/requests/pending"]
LAB_REQUEST_ROUTES = ["/api/market/import", "/api/market/ingest", "/api/lab/backtests", "/api/sim/sessions",
                      "/api/sim/sessions/<int:session_id>/<action>", "/api/mt5/snapshot", "/api/mt5/quotes", "/api/mt5/candles",
                      "/api/mt5/history",
                      "/api/mt5/requests", "/api/mt5/requests/<int:request_id>/result"]
# Binance Spot DEMO account: the state is READ_ONLY (its orders OPERATOR), requests (pause, resume, controlled
# test) are OPERATOR and only recorded; astra-demo acts on them through the gateway.
DEMO_READ_ROUTES = ["/api/demo/overview"]
DEMO_REQUEST_ROUTES = ["/api/demo/<action>"]


class HttpsClient(FlaskClient):
    """Sends every request to https://astra.test, the public name the app is configured for."""

    base = f"https://{HOST}"
    proxy_header = True

    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", self.base)
        if self.proxy_header:
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-Astra-Proxy", PROXY_SECRET)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


class LoopbackClient(HttpsClient):
    base = "http://127.0.0.1:8765"
    proxy_header = False


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class WebAppCase(unittest.TestCase):
    """Public mode: the app answers only to https://astra.test through one trusted proxy hop."""

    trusted_proxy = True
    public_hosts = (HOST,)
    client_class = None

    def setUp(self):
        self.temp = workspace_tempdir()
        root = Path(self.temp.name)
        self.db = root / "astra.sqlite3"
        self.store = BacktestStore(self.db)
        self.store.initialize()
        ExecutionStore(self.db).initialize()
        ModelRegistry(self.db).initialize()
        self.clock = Clock()
        init_key_file(root / "mfa.key")
        self.cipher = load_cipher(root / "mfa.key")
        self.auth = AuthStore(root / "auth.sqlite3", clock=self.clock, mfa_cipher=self.cipher)
        self.auth.initialize()
        self.web = WebConfig(auth_database_path=root / "auth.sqlite3", secret_key_path=root / "web_secret.key",
                             public_hosts=self.public_hosts, trusted_proxy=self.trusted_proxy, https=bool(self.public_hosts),
                             proxy_secret_path=root / "proxy.secret" if self.public_hosts else None)
        self.config = AppConfig(database_path=self.db)
        self.key = b"k" * 32
        self.app = self.make_app()

    def tearDown(self):
        self.temp.cleanup()

    def make_app(self, auth=None):
        return create_app(self.config, self.web, auth=auth or self.auth, secret_key=self.key, store=self.store,
                          proxy_secret=PROXY_SECRET if self.public_hosts else None)

    def client(self, ip="203.0.113.10", app=None):
        app = app or self.app
        app.test_client_class = self.client_class or HttpsClient
        client = app.test_client()
        client.environ_base["REMOTE_ADDR"] = ip
        return client

    def add_user(self, name, role, password=PASSWORD, must_change=False):
        return self.auth.create_user("test", name, role, password, must_change_password=must_change)

    @staticmethod
    def csrf(response):
        match = re.search(r'name="csrf_token" value="([0-9a-f]{64})"', response.get_data(as_text=True))
        assert match, "no CSRF token in page"
        return match.group(1)

    def post(self, client, path, data=None, origin=ORIGIN, **kwargs):
        headers = {"Origin": origin} if origin else {}
        headers.update(kwargs.pop("headers", {}))
        return client.post(path, data=data or {}, headers=headers, **kwargs)

    def login(self, client, name, password=PASSWORD):
        page = client.get("/login")
        return self.post(client, "/login", {"username": name, "password": password, "csrf_token": self.csrf(page)})

    def enroll(self, client):
        page = client.get("/mfa/setup")
        secret = re.search(r'<code class="secret">([A-Z2-7]{16,})</code>', page.get_data(as_text=True)).group(1)
        code = pyotp.TOTP(secret).at(self.clock.now.timestamp())
        response = self.post(client, "/mfa/setup", {"code": code, "csrf_token": self.csrf(page)})
        codes = re.findall(r"<li><code>([A-Z0-9-]{23})</code></li>", response.get_data(as_text=True))
        return secret, codes, response

    def session_client(self, name, role, ip="203.0.113.10"):
        """A client with a FULL session; privileged roles go through MFA enrolment."""

        if self.auth.get_user(name) is None:
            self.add_user(name, role)
        client = self.client(ip)
        response = self.login(client, name)
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        if response.headers["Location"] == "/mfa/setup":
            _secret, _codes, done = self.enroll(client)
            self.assertEqual(done.status_code, 200)
        self.assertEqual(client.get("/api/session").status_code, 200)
        return client

    def page_csrf(self, client):
        return re.search(r'<meta name="csrf-token" content="([0-9a-f]{64})">', client.get("/").get_data(as_text=True)).group(1)


class AuthenticationTests(WebAppCase):
    def test_no_default_account_exists(self):
        self.assertEqual(self.auth.user_count(), 0)
        for name, password in (("admin", "admin"), ("admin", "password"), ("root", "root")):
            self.assertEqual(self.login(self.client(), name, password).status_code, 401)

    def test_first_admin_only_once_and_must_be_admin(self):
        self.auth.create_user("cli", "chief", WebRole.ADMIN, PASSWORD, first_admin=True)
        with self.assertRaisesRegex(ValueError, "existent déjà"):
            self.auth.create_user("cli", "second", WebRole.ADMIN, PASSWORD, first_admin=True)
        empty = AuthStore(Path(self.temp.name) / "other.sqlite3", clock=self.clock)
        empty.initialize()
        with self.assertRaisesRegex(ValueError, "ADMIN"):
            empty.create_user("cli", "viewer", WebRole.READ_ONLY, PASSWORD, first_admin=True)

    def test_password_is_stored_as_argon2id_only(self):
        self.add_user("alice", WebRole.READ_ONLY)
        with contextlib.closing(sqlite3.connect(self.auth.path)) as db:
            stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertTrue(stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$"))
        self.assertNotIn(PASSWORD, stored)

    def test_weak_passwords_are_refused(self):
        for bad in ("short", "alice-alice-alice-1", "aaaaaaaaaaaaaaaa"):
            with self.assertRaises(ValueError):
                self.auth.create_user("t", "alice", WebRole.READ_ONLY, bad)

    def test_correct_and_incorrect_login(self):
        self.add_user("alice", WebRole.READ_ONLY)
        bad = self.login(self.client(), "alice", "Wrong-Password-123")
        self.assertEqual(bad.status_code, 401)
        self.assertNotIn(SESSION_COOKIE, bad.headers.get("Set-Cookie", ""))
        client = self.client()
        ok = self.login(client, "alice")
        self.assertEqual((ok.status_code, ok.headers["Location"]), (303, "/"))
        self.assertEqual(client.get("/api/session").get_json()["role"], "READ_ONLY")

    def test_disabled_account_cannot_log_in_and_message_is_generic(self):
        self.add_user("alice", WebRole.READ_ONLY)
        self.add_user("boss", WebRole.ADMIN)
        self.auth.set_active("t", "alice", False)
        disabled = self.login(self.client(), "alice")
        unknown = self.login(self.client("203.0.113.11"), "nobody")
        self.assertEqual((disabled.status_code, unknown.status_code), (401, 401))
        self.assertIn("Identifiant ou mot de passe incorrect", disabled.get_data(as_text=True))
        self.assertIn("Identifiant ou mot de passe incorrect", unknown.get_data(as_text=True))

    def test_repeated_failures_are_throttled_progressively_never_permanently(self):
        self.add_user("alice", WebRole.READ_ONLY)
        for _ in range(5):
            self.assertEqual(self.login(self.client(), "alice", "Wrong-Password-123").status_code, 401)
        throttled = self.login(self.client(), "alice")
        self.assertEqual(throttled.status_code, 429)
        self.assertGreater(int(throttled.headers["Retry-After"]), 0)
        # Another address is throttled too for this account (account key), until the delay passes.
        self.assertEqual(self.login(self.client("198.51.100.7"), "alice").status_code, 429)
        self.clock.advance(3)
        self.assertEqual(self.login(self.client("198.51.100.7"), "alice").status_code, 303)
        # The delay is capped: after the maximum wait the account is usable again.
        for _ in range(40):
            self.clock.advance(1000)
            self.login(self.client(), "alice", "Wrong-Password-123")
        self.clock.advance(self.auth.policy.throttle_max_seconds + 1)
        self.assertEqual(self.login(self.client("198.51.100.8"), "alice").status_code, 303)

    def test_rotating_addresses_cannot_lock_out_a_known_device(self):
        # Review M1: an attacker failing from ever-new addresses blocks the account-wide key, but the
        # legitimate user's browser, known from an earlier successful login, keeps signing in.
        self.add_user("alice", WebRole.READ_ONLY)
        victim = self.client("203.0.113.5")
        self.assertEqual(self.login(victim, "alice").status_code, 303)
        device = victim.get_cookie("__Host-astra_device", domain=HOST).value
        for i in range(30):
            self.post(victim, "/logout", {"csrf_token": self.page_csrf(victim)})
            self.login(self.client(f"198.18.1.{i}"), "alice", "Wrong-Password-123")
            self.assertEqual(self.login(victim, "alice").status_code, 303, i)
            self.clock.advance(60)
        # An unknown browser is subject to the account-wide throttle the attacker triggered.
        self.assertEqual(self.login(self.client("203.0.113.6"), "alice").status_code, 429)
        # A forged device cookie gives nothing.
        intruder = self.client("198.18.2.1")
        intruder.set_cookie("__Host-astra_device", device[:-1] + ("0" if device[-1] != "0" else "1"), domain=HOST)
        self.assertEqual(self.login(intruder, "alice").status_code, 429)

    def test_login_attacks_cannot_block_step_up_or_password_change(self):
        # Re-review M-NEW: failed logins from rotating addresses must not block a signed-in user's
        # re-authentication (admin step-up) or password change.
        boss = self.session_client("boss", WebRole.ADMIN)
        self.add_user("carol", WebRole.READ_ONLY)
        for i in range(10):
            self.login(self.client(f"198.18.3.{i}"), "boss", "Wrong-Password-123")
        token = self.page_csrf(boss)
        disabled = self.post(boss, "/admin/users/carol/disable", {"admin_password": PASSWORD, "csrf_token": token})
        self.assertEqual(disabled.status_code, 303)
        self.assertFalse(self.auth.get_user("carol").active)
        page = boss.get("/account/password")
        changed = self.post(boss, "/account/password", {"current_password": PASSWORD, "new_password": "Brand-New-Pass-42",
                                                        "confirm_password": "Brand-New-Pass-42", "csrf_token": self.csrf(page)})
        self.assertEqual(changed.status_code, 303)

    def test_malformed_device_cookies_are_ignored_not_errors(self):
        # Raw Cookie header (the test client's jar would alter or drop these values): a superscript digit
        # and a 5000-digit timestamp must be ignored, not raise (was HTTP 500).
        self.add_user("alice", WebRole.READ_ONLY)
        from astra.auth import crypto

        prefix = crypto.token_digest("alice")[:16]  # the real account prefix, so the timestamp is parsed
        bad_values = ["x.y", "a.b.c.d", f"{prefix}." + "n" * 32 + ".\u00b2.mac",
                      f"{prefix}." + "n" * 32 + "." + "9" * 5000 + ".mac"]
        for value in bad_values:
            self.app.test_client_class = HttpsClient
            client = self.app.test_client(use_cookies=False)
            client.environ_base["REMOTE_ADDR"] = "203.0.113.77"
            page = client.get("/login")
            pre = re.search(r"__Host-astra_pre=([^;]+)", page.headers["Set-Cookie"]).group(1)
            response = client.post("/login", data={"username": "alice", "password": PASSWORD, "csrf_token": self.csrf(page)},
                                   headers={"Origin": ORIGIN, "Cookie": f"__Host-astra_pre={pre}; __Host-astra_device={value}"})
            self.assertEqual(response.status_code, 303, value[:40])

    def test_device_cookie_dies_with_a_password_reset(self):
        client = self.client("203.0.113.5")
        self.add_user("alice", WebRole.READ_ONLY)
        self.login(client, "alice")
        device = client.get_cookie("__Host-astra_device", domain=HOST).value
        self.auth.reset_password("cli:root", "alice")
        for i in range(6):
            self.login(self.client(f"198.18.4.{i}"), "alice", "Wrong-Password-123")
        stale = self.client("203.0.113.5")
        stale.set_cookie("__Host-astra_device", device, domain=HOST)
        self.assertEqual(self.login(stale, "alice", "Wrong-Password-123").status_code, 429)

    def test_temporary_password_expires(self):
        self.add_user("alice", WebRole.READ_ONLY, must_change=True)
        self.clock.advance(self.auth.policy.temporary_password_seconds + 1)
        self.assertEqual(self.login(self.client(), "alice").status_code, 401)
        self.assertIn("temporary_password_expired", json.dumps(self.auth.events(5)))

    def test_mfa_setup_page_keeps_the_same_pending_secret(self):
        self.add_user("boss", WebRole.ADMIN)
        client = self.client()
        self.login(client, "boss")
        first = re.search(r'<code class="secret">([A-Z2-7]{16,})</code>', client.get("/mfa/setup").get_data(as_text=True))
        second = re.search(r'<code class="secret">([A-Z2-7]{16,})</code>', client.get("/mfa/setup").get_data(as_text=True))
        self.assertEqual(first.group(1), second.group(1))

    def test_mfa_setup_page_shows_a_qr_code_of_the_pending_secret(self):
        import html

        from astra.auth import crypto

        self.add_user("boss", WebRole.ADMIN)
        client = self.client()
        self.login(client, "boss")
        body = client.get("/mfa/setup").get_data(as_text=True)
        secret = re.search(r'<code class="secret">([A-Z2-7]{16,})</code>', body).group(1)
        source = html.unescape(re.search(r'<img class="totp-qr" src="([^"]+)"', body).group(1))
        self.assertTrue(source.startswith("data:image/svg+xml"))
        # The QR code encodes exactly this account's provisioning URI (the generator is deterministic).
        self.assertEqual(source, crypto.totp_qr_data_uri(crypto.totp_uri(secret, "boss")))
        self.assertIn("img-src 'self' data:", client.get("/mfa/setup").headers["Content-Security-Policy"])

    def test_failures_from_one_address_throttle_every_username_from_it(self):
        for i in range(5):
            self.login(self.client("192.0.2.66"), f"guess{i}", "Wrong-Password-123")
        self.add_user("alice", WebRole.READ_ONLY)
        self.assertEqual(self.login(self.client("192.0.2.66"), "alice").status_code, 429)
        self.assertEqual(self.login(self.client("192.0.2.67"), "alice").status_code, 303)

    def test_admin_must_enrol_mfa_before_any_data(self):
        self.add_user("boss", WebRole.ADMIN)
        client = self.client()
        response = self.login(client, "boss")
        self.assertEqual(response.headers["Location"], "/mfa/setup")
        for route in READ_ONLY_ROUTES + ADMIN_ROUTES:
            self.assertEqual(client.get(route).status_code, 401, route)
        self.assertEqual(client.get("/").status_code, 303)
        _secret, codes, _done = self.enroll(client)
        self.assertEqual(len(codes), 10)
        self.assertEqual(client.get("/api/admin/users").status_code, 200)
        self.assertNotIn(codes[0], json.dumps(self.auth.events(1000)))

    def test_mfa_login_wrong_code_replay_and_recovery_code(self):
        client = self.client()
        self.add_user("boss", WebRole.ADMIN)
        self.login(client, "boss")
        secret, codes, _ = self.enroll(client)
        self.post(client, "/logout", {"csrf_token": self.page_csrf(client)})

        def start():
            fresh = self.client()
            self.assertEqual(self.login(fresh, "boss").headers["Location"], "/login/mfa")
            return fresh, self.csrf(fresh.get("/login/mfa"))

        fresh, token = start()
        self.assertEqual(fresh.get("/api/summary").status_code, 401)
        self.assertEqual(self.post(fresh, "/login/mfa", {"code": "000000", "csrf_token": token}).status_code, 401)
        self.clock.advance(30)
        good = pyotp.TOTP(secret).at(self.clock.now.timestamp())
        self.assertEqual(self.post(fresh, "/login/mfa", {"code": good, "csrf_token": token}).status_code, 303)
        self.assertEqual(fresh.get("/api/session").status_code, 200)
        replay, token = start()
        self.assertEqual(self.post(replay, "/login/mfa", {"code": good, "csrf_token": token}).status_code, 401)
        self.assertEqual(self.post(replay, "/login/mfa", {"code": codes[0], "csrf_token": token}).status_code, 303)
        again, token = start()
        self.assertEqual(self.post(again, "/login/mfa", {"code": codes[0], "csrf_token": token}).status_code, 401)

    def test_password_change_requires_current_password_and_ends_other_sessions(self):
        first = self.session_client("alice", WebRole.READ_ONLY)
        second = self.session_client("alice", WebRole.READ_ONLY, ip="203.0.113.20")
        page = first.get("/account/password")
        wrong = self.post(first, "/account/password", {"current_password": "Wrong-Password-123", "new_password": "Brand-New-Pass-42",
                                                        "confirm_password": "Brand-New-Pass-42", "csrf_token": self.csrf(page)})
        self.assertEqual(wrong.status_code, 401)
        ok = self.post(first, "/account/password", {"current_password": PASSWORD, "new_password": "Brand-New-Pass-42",
                                                     "confirm_password": "Brand-New-Pass-42", "csrf_token": self.csrf(page)})
        self.assertEqual(ok.status_code, 303)
        self.assertEqual(first.get("/api/session").status_code, 200)
        self.assertEqual(second.get("/api/session").status_code, 401)
        self.assertEqual(self.login(self.client("203.0.113.30"), "alice").status_code, 401)
        self.assertEqual(self.login(self.client("203.0.113.31"), "alice", "Brand-New-Pass-42").status_code, 303)

    def test_current_password_guessing_is_throttled(self):
        # Review M2: a stolen session cannot guess the current password without limit.
        client = self.session_client("alice", WebRole.READ_ONLY)
        page = client.get("/account/password")
        statuses = [self.post(client, "/account/password", {"current_password": f"Wrong-Password-{i:03d}",
                                                             "new_password": "Brand-New-Pass-42", "confirm_password": "Brand-New-Pass-42",
                                                             "csrf_token": self.csrf(page)}).status_code for i in range(8)]
        self.assertIn(429, statuses)
        right = self.post(client, "/account/password", {"current_password": PASSWORD, "new_password": "Brand-New-Pass-42",
                                                        "confirm_password": "Brand-New-Pass-42", "csrf_token": self.csrf(page)})
        self.assertEqual(right.status_code, 429)

    def test_forced_password_change_blocks_data_until_done(self):
        self.add_user("alice", WebRole.READ_ONLY, must_change=True)
        client = self.client()
        self.assertEqual(self.login(client, "alice").headers["Location"], "/account/password")
        self.assertEqual(client.get("/api/summary").status_code, 401)
        page = client.get("/account/password")
        self.post(client, "/account/password", {"current_password": PASSWORD, "new_password": "Brand-New-Pass-42",
                                                "confirm_password": "Brand-New-Pass-42", "csrf_token": self.csrf(page)})
        self.assertEqual(client.get("/api/summary").status_code, 200)


class SessionTests(WebAppCase):
    def test_cookie_is_opaque_host_prefixed_secure_httponly_strict(self):
        self.add_user("alice", WebRole.READ_ONLY)
        response = self.login(self.client(), "alice")
        cookie = next(h for h in response.headers.getlist("Set-Cookie") if h.startswith(SESSION_COOKIE + "="))
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain=", cookie)
        token = cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertNotIn("alice", token)
        self.assertNotIn("READ_ONLY", token)
        with contextlib.closing(sqlite3.connect(self.auth.path)) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(token, dump)

    def test_forged_unknown_and_malformed_tokens_are_refused(self):
        self.add_user("alice", WebRole.READ_ONLY)
        for value in ("x", "A" * 43, "forged-token-value-that-was-never-issued", "' OR 1=1 --" * 3):
            client = self.client()
            client.set_cookie(SESSION_COOKIE, value, domain=HOST)
            self.assertEqual(client.get("/api/summary").status_code, 401, value)

    def test_idle_and_absolute_expiry(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        self.clock.advance(self.auth.policy.idle_seconds + 1)
        self.assertEqual(client.get("/api/session").status_code, 401)
        active = self.session_client("alice", WebRole.READ_ONLY)
        for _ in range(self.auth.policy.absolute_seconds // 600 + 1):
            self.clock.advance(600)
            last = active.get("/api/session").status_code
        self.assertEqual(last, 401)

    def test_logout_revokes_the_token_server_side(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        token = client.get_cookie(SESSION_COOKIE, domain=HOST).value
        self.assertEqual(self.post(client, "/logout", {"csrf_token": self.page_csrf(client)}).status_code, 303)
        replay = self.client()
        replay.set_cookie(SESSION_COOKIE, token, domain=HOST)
        self.assertEqual(replay.get("/api/session").status_code, 401)

    def test_login_issues_a_new_token_and_old_one_is_refused(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        old = client.get_cookie(SESSION_COOKIE, domain=HOST).value
        # Sign in again from the same browser, still holding the old session cookie.
        helper = self.client()
        token = self.csrf(helper.get("/login"))
        client.set_cookie(PRE_COOKIE, helper.get_cookie(PRE_COOKIE, domain=HOST).value, domain=HOST)
        response = self.post(client, "/login", {"username": "alice", "password": PASSWORD, "csrf_token": token})
        self.assertEqual(response.status_code, 303)
        new = client.get_cookie(SESSION_COOKIE, domain=HOST).value
        self.assertNotEqual(old, new)
        replay = self.client()
        replay.set_cookie(SESSION_COOKIE, old, domain=HOST)
        self.assertEqual(replay.get("/api/session").status_code, 401)

    def test_planted_cookie_is_not_adopted_at_login(self):
        self.add_user("alice", WebRole.READ_ONLY)
        client = self.client()
        client.set_cookie(SESSION_COOKIE, "attacker-chosen-value-0123456789abcdef", domain=HOST)
        self.login(client, "alice")
        self.assertNotEqual(client.get_cookie(SESSION_COOKIE, domain=HOST).value, "attacker-chosen-value-0123456789abcdef")

    def test_sessions_survive_an_application_restart(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        token = client.get_cookie(SESSION_COOKIE, domain=HOST).value
        restarted = self.make_app(auth=AuthStore(self.auth.path, clock=self.clock, mfa_cipher=self.cipher))
        again = self.client(app=restarted)
        again.set_cookie(SESSION_COOKIE, token, domain=HOST)
        self.assertEqual(again.get("/api/session").status_code, 200)

    def test_storage_failure_never_grants_access(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        token = client.get_cookie(SESSION_COOKIE, domain=HOST).value
        broken = AuthStore(self.auth.path, clock=self.clock, mfa_cipher=self.cipher)
        with mock.patch.object(broken, "resolve", side_effect=AuthUnavailable("down")):
            failing = self.client(app=self.make_app(auth=broken))
            failing.set_cookie(SESSION_COOKIE, token, domain=HOST)
            for route in READ_ONLY_ROUTES + OPERATOR_ROUTES + ["/"]:
                self.assertEqual(failing.get(route).status_code, 503, route)
        corrupt = Path(self.temp.name) / "corrupt.sqlite3"
        corrupt.write_bytes(b"this is not a database" * 100)
        bad = self.client(app=self.make_app(auth=AuthStore(corrupt, clock=self.clock)))
        bad.set_cookie(SESSION_COOKIE, token, domain=HOST)
        self.assertEqual(bad.get("/api/summary").status_code, 503)

    def test_disabling_an_account_revokes_its_sessions(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        self.add_user("boss", WebRole.ADMIN)
        self.auth.set_active("t", "alice", False)
        self.assertEqual(client.get("/api/session").status_code, 401)

    def test_role_change_ends_sessions(self):
        client = self.session_client("alice", WebRole.OPERATOR)
        self.add_user("boss", WebRole.ADMIN)
        self.auth.set_role("t", "alice", WebRole.READ_ONLY)
        self.assertEqual(client.get("/api/session").status_code, 401)

    def test_concurrent_sessions_are_capped(self):
        clients = [self.session_client("alice", WebRole.READ_ONLY, ip=f"203.0.113.{40 + i}") for i in range(6)]
        self.assertEqual(clients[0].get("/api/session").status_code, 401)
        self.assertEqual(clients[-1].get("/api/session").status_code, 200)

    def test_cleanup_removes_old_sessions(self):
        self.session_client("alice", WebRole.READ_ONLY)
        self.clock.advance(self.auth.policy.absolute_seconds + 2 * 86400)
        self.assertGreaterEqual(self.auth.cleanup(), 1)


class PermissionTests(WebAppCase):
    def test_anonymous_access_is_refused_on_every_private_route(self):
        client = self.client()
        for route in READ_ONLY_ROUTES + OPERATOR_ROUTES + ADMIN_ROUTES + ["/static/app.js"]:
            self.assertEqual(client.get(route).status_code, 401, route)
        index = client.get("/", headers={"Accept": "text/html"})
        self.assertEqual((index.status_code, index.headers["Location"]), (303, "/login"))
        self.assertEqual(client.get("/admin/users", headers={"Accept": "text/html"}).status_code, 303)

    def test_every_registered_route_is_covered_by_this_test_file(self):
        app_routes = {rule.rule for rule in self.app.url_map.iter_rules()}
        known = set(READ_ONLY_ROUTES + OPERATOR_ROUTES + ADMIN_ROUTES + LAB_READ_ROUTES + LAB_REQUEST_ROUTES
                    + DEMO_READ_ROUTES + DEMO_REQUEST_ROUTES) | {
            "/", "/login", "/logout", "/login/mfa", "/mfa/setup", "/account/password", "/api/health",
            "/static/<name>", "/admin/users", "/admin/users/<username>/<action>",
            "/api/agent/whoami"}  # agent tokens only: permission tests in test_agent.py
        self.assertEqual(app_routes, known, "a new route needs explicit permission tests")

    def test_health_is_public_and_reveals_nothing(self):
        response = self.client().get("/api/health")
        self.assertEqual((response.status_code, response.get_json()), (200, {"status": "ok"}))

    def test_insufficient_roles_are_refused_server_side(self):
        viewer = self.session_client("viewer", WebRole.READ_ONLY)
        for route in READ_ONLY_ROUTES:
            self.assertEqual(viewer.get(route).status_code, 200, route)
        for route in OPERATOR_ROUTES + ADMIN_ROUTES:
            self.assertEqual(viewer.get(route).status_code, 403, route)
        operator = self.session_client("ops", WebRole.OPERATOR)
        for route in OPERATOR_ROUTES:
            self.assertEqual(operator.get(route).status_code, 200, route)
        for route in ADMIN_ROUTES:
            self.assertEqual(operator.get(route).status_code, 403, route)
        self.assertEqual(operator.get("/admin/users").status_code, 403)
        self.assertEqual(self.post(operator, "/admin/users", {"username": "x", "role": "ADMIN",
                                                              "csrf_token": self.page_csrf(operator)}).status_code, 403)

    def test_csrf_is_required_for_every_state_change(self):
        self.add_user("alice", WebRole.READ_ONLY)
        anonymous = self.client()
        anonymous.get("/login")
        no_token = self.post(anonymous, "/login", {"username": "alice", "password": PASSWORD})
        self.assertEqual(no_token.status_code, 403)
        other = self.client()
        other.get("/login")
        foreign_token = self.csrf(self.client().get("/login"))
        self.assertEqual(self.post(other, "/login", {"username": "alice", "password": PASSWORD,
                                                     "csrf_token": foreign_token}).status_code, 403)
        client = self.session_client("alice", WebRole.READ_ONLY)
        self.assertEqual(self.post(client, "/logout", {}).status_code, 403)
        self.assertEqual(client.get("/api/session").status_code, 200)
        boss = self.session_client("boss", WebRole.ADMIN)
        self.assertEqual(self.post(boss, "/admin/users/alice/disable", {"admin_password": PASSWORD}).status_code, 403)
        self.assertTrue(self.auth.get_user("alice").active)

    def test_origin_is_checked_on_state_changes(self):
        self.add_user("alice", WebRole.READ_ONLY)
        client = self.client()
        token = self.csrf(client.get("/login"))
        for origin in (None, "https://evil.example", "http://astra.test", "null"):
            response = self.post(client, "/login", {"username": "alice", "password": PASSWORD, "csrf_token": token},
                                 origin=origin)
            self.assertEqual(response.status_code, 403, origin)

    def test_requests_without_the_proxy_secret_are_refused(self):
        # A local process that reaches 127.0.0.1:8765 directly can forge Host and Origin, not the secret.
        for value in ("", "wrong", PROXY_SECRET[:-1], PROXY_SECRET + "x"):
            response = self.client().get("/api/health", headers={"X-Astra-Proxy": value})
            self.assertEqual(response.status_code, 403, value)
        self.assertEqual(self.client().get("/api/health").status_code, 200)

    def test_forged_host_header_is_refused(self):
        # In public mode the loopback names are refused too: a local process cannot bypass the proxy.
        for host in ("evil.example", "astra.test.evil.example", "astra.test:8443", "astra.test@evil.example",
                     "127.0.0.1:8765", "localhost:8765"):
            self.assertEqual(self.client().get("/api/health", headers={"Host": host}).status_code, 400, host)

    def test_static_files_and_paths_do_not_leak(self):
        client = self.client()
        for path in ("/static/../README.md", "/static/index.html", "/static/web_secret.key", "/../../README.md",
                     "/var/auth.sqlite3", "/static/%2e%2e/config.py", "/.git/config"):
            self.assertIn(client.get(path).status_code, (401, 404), path)
        self.assertEqual(client.get("/static/styles.css").status_code, 200)

    def test_security_headers(self):
        response = self.client().get("/login")
        headers = response.headers
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        # Not no-referrer: browsers would then send "Origin: null" on the login form POST (refused).
        self.assertEqual(headers["Referrer-Policy"], "same-origin")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("max-age=", headers["Strict-Transport-Security"])

    def test_oversized_request_is_refused(self):
        response = self.post(self.client(), "/login", data={"username": "a" * 200_000})
        self.assertEqual(response.status_code, 413)

    def test_responses_and_audit_contain_no_secret(self):
        client = self.session_client("boss", WebRole.ADMIN)
        token = client.get_cookie(SESSION_COOKIE, domain=HOST).value
        bodies = [client.get(route).get_data(as_text=True) for route in READ_ONLY_ROUTES + OPERATOR_ROUTES + ADMIN_ROUTES]
        audit = json.dumps(self.auth.events(1000))
        for text in [*bodies, audit]:
            self.assertNotIn(PASSWORD, text)
            self.assertNotIn(token, text)
            self.assertNotIn("$argon2", text)
        with contextlib.closing(sqlite3.connect(self.auth.path)) as db:
            secret = db.execute("SELECT mfa_secret FROM users WHERE username = 'boss'").fetchone()[0]
        self.assertNotIn(secret, "".join(bodies) + audit)

    def test_injection_attempts_are_inert(self):
        self.add_user("alice", WebRole.READ_ONLY)
        for name in ("alice' --", "' OR '1'='1", "alice\x00", "<script>alert(1)</script>"):
            response = self.login(self.client(f"192.0.2.{len(name)}"), name)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("<script>alert(1)</script>", response.get_data(as_text=True))
        self.assertEqual(self.auth.user_count(), 1)


class TradingIsolationTests(WebAppCase):
    def test_no_route_can_change_trading_state(self):
        mutating = {rule.rule for rule in self.app.url_map.iter_rules() if rule.methods & {"POST", "PUT", "PATCH", "DELETE"}}
        # Besides sign-in and administration, only research and paper-simulation requests (astra.labweb) mutate,
        # and none reaches the execution gateway (see test_webapp_never_calls_execution_mutators).
        self.assertEqual(mutating, {"/login", "/logout", "/login/mfa", "/mfa/setup", "/account/password", "/admin/users",
                                    "/admin/users/<username>/<action>", *LAB_REQUEST_ROUTES, *DEMO_REQUEST_ROUTES})
        operator = self.session_client("ops", WebRole.OPERATOR)
        for route in OPERATOR_ROUTES:
            self.assertEqual(self.post(operator, route, {"csrf_token": self.page_csrf(operator)}).status_code, 405, route)

    def test_webapp_never_calls_execution_mutators(self):
        source = Path(__file__).resolve().parents[1] / "src" / "astra" / "webapp.py"
        for module in (source, source.with_name("labweb.py"), source.with_name("papertrade.py"),
                       source.with_name("jobs.py"), source.with_name("marketdata.py"), source.with_name("demo_requests.py")):
            text = module.read_text(encoding="utf-8")
            names = {node.attr for node in ast.walk(ast.parse(text)) if isinstance(node, ast.Attribute)}
            forbidden_names = {"submit", "submit_order", "release_kill_switch", "activate_kill_switch", "clear_freeze",
                               "resolve_unknown_order", "approve_risk_policy_change", "promote", "rollback"}
            if module.name != "webapp.py":
                forbidden_names.discard("rollback")  # sqlite3 Connection.rollback in the stores, not a model rollback
            for forbidden in forbidden_names:
                self.assertNotIn(forbidden, names, f"{module.name}: {forbidden}")
            if module.name != "webapp.py":
                for package in ("execution", "brokers", "governance", "venues"):
                    self.assertNotRegex(text, rf"from \.{package}|import astra\.{package}", f"{module.name} imports {package}")
        auth_dir = source.parent / "auth"
        for path in auth_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for module in ("execution", "brokers", "risk", "governance"):
                self.assertNotRegex(text, rf"from \.\.{module}|import astra\.{module}", f"{path.name} imports {module}")

    def test_mode_stays_simulation_and_live_locked(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        info = client.get("/api/session").get_json()
        self.assertEqual((info["mode"], info["live_locked"]), ("SIMULATION", True))
        self.assertIn("RÉEL VERROUILLÉ", client.get("/").get_data(as_text=True))

    def test_operator_sees_replay_orders_and_controls_read_only(self):
        execution = ExecutionStore(self.db)
        run_paper_replay(synthetic_bars(), execution, run_id="web")
        operator = self.session_client("ops", WebRole.OPERATOR)
        orders = operator.get("/api/orders").get_json()["orders"]
        self.assertTrue(orders)
        self.assertTrue(all(order["mode"] == "SIMULATION" for order in orders))
        self.assertTrue(operator.get("/api/controls").get_json()["live_locked"])
        self.assertEqual(operator.get("/api/reconciliations").get_json()["reconciliations"][0]["status"], "CONSISTENT")
        TradingControls(execution).activate_kill_switch("operator", "drill")
        controls = operator.get("/api/controls").get_json()
        self.assertTrue(controls["kill_switch"]["active"])
        self.assertFalse(controls["entries_allowed"])

    def test_summary_and_data_label(self):
        client = self.session_client("alice", WebRole.READ_ONLY)
        summary = client.get("/api/summary").get_json()
        self.assertEqual((summary["mode"], summary["latest_run"]), ("SIMULATION", None))
        self.assertIn('id="data-label"', client.get("/").get_data(as_text=True))


class AdministrationTests(WebAppCase):
    def test_admin_creates_user_with_step_up_and_one_time_password(self):
        boss = self.session_client("boss", WebRole.ADMIN)
        page = boss.get("/admin/users")
        refused = self.post(boss, "/admin/users", {"username": "carol", "role": "READ_ONLY", "admin_password": "Wrong-Password-1",
                                                   "csrf_token": self.csrf(page)})
        self.assertEqual(refused.status_code, 401)
        self.assertIsNone(self.auth.get_user("carol"))
        created = self.post(boss, "/admin/users", {"username": "carol", "role": "READ_ONLY", "admin_password": PASSWORD,
                                                   "csrf_token": self.csrf(page)})
        temporary = re.search(r'<code class="secret">([^<]+)</code>', created.get_data(as_text=True)).group(1)
        self.assertNotIn(temporary, json.dumps(self.auth.events(1000)))
        carol = self.client("203.0.113.90")
        self.assertEqual(self.login(carol, "carol", temporary).headers["Location"], "/account/password")

    def test_admin_disables_user_and_cannot_disable_last_admin(self):
        alice = self.session_client("alice", WebRole.READ_ONLY)
        boss = self.session_client("boss", WebRole.ADMIN)
        token = self.page_csrf(boss)
        self.assertEqual(self.post(boss, "/admin/users/alice/disable", {"admin_password": PASSWORD, "csrf_token": token}).status_code, 303)
        self.assertEqual(alice.get("/api/session").status_code, 401)
        last = self.post(boss, "/admin/users/boss/disable", {"admin_password": PASSWORD, "csrf_token": token})
        self.assertEqual(last.status_code, 400)
        self.assertTrue(self.auth.get_user("boss").active)

    def test_recovery_by_local_cli_reset_mfa_and_password(self):
        self.session_client("boss", WebRole.ADMIN)
        temporary = self.auth.reset_password("cli:root", "boss")
        self.auth.reset_mfa("cli:root", "boss")
        client = self.client("203.0.113.99")
        self.assertEqual(self.login(client, "boss", temporary).headers["Location"], "/mfa/setup")
        self.enroll(client)
        self.assertEqual(client.get("/api/session").status_code, 401)  # forced password change comes next
        self.assertEqual(client.get("/account/password").status_code, 200)

    def test_auth_events_are_append_only(self):
        self.add_user("alice", WebRole.READ_ONLY)
        with contextlib.closing(sqlite3.connect(self.auth.path)) as db:
            for statement in ("UPDATE auth_events SET result = 'x'", "DELETE FROM auth_events"):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(statement)


class AuthCliTests(unittest.TestCase):
    def test_init_admin_generates_a_one_time_password_and_refuses_a_second_admin(self):
        from astra.cli import main

        with workspace_tempdir() as temp:
            env = {"ASTRA_AUTH_DATABASE": str(Path(temp) / "auth.sqlite3"), "ASTRA_MFA_KEY_FILE": str(Path(temp) / "mfa.key")}
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict("os.environ", env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["auth", "init-admin", "--username", "chief", "--generate"])
            self.assertEqual(code, 0)
            created = json.loads(out.getvalue())["created"]
            self.assertEqual((created["role"], created["must_change_password"]), ("ADMIN", True))
            self.assertNotIn("Mot de passe", out.getvalue())
            self.assertIn("affiché une seule fois", err.getvalue())
            self.assertTrue((Path(temp) / "mfa.key").exists(), "init-admin creates the MFA key ring")
            out2, err2 = io.StringIO(), io.StringIO()
            with mock.patch.dict("os.environ", env), contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
                self.assertEqual(main(["auth", "init-admin", "--username", "other", "--generate"]), 1)
            store = AuthStore(Path(temp) / "auth.sqlite3")
            self.assertEqual([u.username for u in store.list_users()], ["chief"])
            self.assertEqual(store.list_users()[0].role, WebRole.ADMIN)

    def test_pre_login_cookie_is_host_prefixed(self):
        with workspace_tempdir() as temp:
            root = Path(temp)
            BacktestStore(root / "a.sqlite3").initialize()
            web = WebConfig(auth_database_path=root / "auth.sqlite3", secret_key_path=root / "k.key")
            auth = AuthStore(web.auth_database_path)
            auth.initialize()
            app = create_app(AppConfig(database_path=root / "a.sqlite3"), web, auth=auth)
            app.test_client_class = LoopbackClient
            response = app.test_client().get("/login")
            cookie = next(h for h in response.headers.getlist("Set-Cookie") if h.startswith(PRE_COOKIE + "="))
            self.assertIn("Secure", cookie)
            self.assertEqual(len((root / "k.key").read_bytes()), 32)

    def test_public_host_requires_https_a_trusted_proxy_and_a_proxy_secret(self):
        secret = Path("unused.secret")
        with self.assertRaises(ValueError):
            WebConfig(public_hosts=("astra.example",), https=False, trusted_proxy=True, proxy_secret_path=secret)
        with self.assertRaises(ValueError):
            WebConfig(public_hosts=("astra.example",), https=True, trusted_proxy=False, proxy_secret_path=secret)
        with self.assertRaises(ValueError):
            WebConfig(public_hosts=("astra.example",), https=True, trusted_proxy=True)
        with self.assertRaises(ValueError):
            WebConfig(public_hosts=("*.example",), https=True, trusted_proxy=True, proxy_secret_path=Path("x"))


class PrivateModeTests(WebAppCase):
    """Loopback or SSH-tunnel access: no public host, no proxy; forwarded headers are ignored."""

    trusted_proxy = False
    public_hosts = ()
    client_class = LoopbackClient

    def post(self, client, path, data=None, origin="http://127.0.0.1:8765", **kwargs):
        return super().post(client, path, data, origin=origin, **kwargs)

    def test_forwarded_for_is_ignored_without_a_trusted_proxy(self):
        self.add_user("alice", WebRole.READ_ONLY)
        for i in range(5):
            self.login(self.client("192.0.2.80"), f"x{i}", "Wrong-Password-123")
        spoofed = self.client("192.0.2.80")
        page = spoofed.get("/login")
        response = self.post(spoofed, "/login", {"username": "alice", "password": PASSWORD, "csrf_token": self.csrf(page)},
                             headers={"X-Forwarded-For": "198.51.100.200"})
        self.assertEqual(response.status_code, 429)

    def test_public_names_are_refused_in_private_mode(self):
        self.assertEqual(self.client().get("/api/health", headers={"Host": HOST}).status_code, 400)


class TrustedProxyTests(WebAppCase):
    trusted_proxy = True

    def test_one_proxy_hop_is_trusted_for_the_client_address(self):
        self.add_user("alice", WebRole.READ_ONLY)
        for i in range(5):
            client = self.client("127.0.0.1")
            page = client.get("/login")
            self.post(client, "/login", {"username": f"x{i}", "password": "Wrong-Password-123", "csrf_token": self.csrf(page)},
                      headers={"X-Forwarded-For": "192.0.2.150"})
        blocked = self.client("127.0.0.1")
        page = blocked.get("/login")
        self.assertEqual(self.post(blocked, "/login", {"username": "alice", "password": PASSWORD, "csrf_token": self.csrf(page)},
                                   headers={"X-Forwarded-For": "192.0.2.150"}).status_code, 429)
        other = self.client("127.0.0.1")
        page = other.get("/login")
        # A client-supplied extra hop is not trusted: only the address appended by the proxy counts.
        self.assertEqual(self.post(other, "/login", {"username": "alice", "password": PASSWORD, "csrf_token": self.csrf(page)},
                                   headers={"X-Forwarded-For": "192.0.2.150, 192.0.2.151"}).status_code, 303)


class StageGuardTests(WebAppCase):
    def test_pending_stage_tokens_expire_quickly(self):
        self.add_user("boss", WebRole.ADMIN)
        client = self.client()
        self.login(client, "boss")
        self.clock.advance(self.auth.policy.pending_seconds + 1)
        self.assertEqual(client.get("/mfa/setup").status_code, 303)

    def test_stage_constants(self):
        self.assertEqual({s.value for s in SessionStage}, {"MFA_PENDING", "MFA_ENROLL", "PASSWORD_CHANGE", "FULL"})


if __name__ == "__main__":
    unittest.main()
