"""Agent tokens (machine access) and the ASTRA agent, in SIMULATION only.

Every protection has a negative test: scope, owner role, expiry, revocation, owner changes, address list,
cookie plus bearer, throttling, routes closed to every token, and the agent's own guards (SIMULATION only,
kill switch, route allowlist, halting on a refused token). Offline: Flask test client and temporary SQLite.
"""

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra.agent import Agent, AgentHalt, AgentPolicy, AstraClient
from astra.auth import AuthError, WebRole
from astra.auth.agent_tokens import AgentTokens
from astra.execution import ExecutionStore
from astra.jobs import JobStore, Worker
from astra.webapp import AGENT_ENDPOINTS, SESSION_COOKIE
from test_labweb import CSV
from test_webapp import ORIGIN, LoopbackClient, WebAppCase

EXPECTED_AGENT_ENDPOINTS = {
    "read": {"agent_whoami", "status", "summary", "runs", "model_versions", "experiments", "controls",
             "market_overview", "market_quality", "market_candles", "lab_catalog", "lab_backtests", "lab_backtest",
             "sim_sessions", "sim_session", "learn_overview", "system_jobs", "mt5_overview", "market_chart",
             "dashboard_overview"},
    "lab": {"market_ingest", "lab_backtest_create", "sim_create", "sim_action"},
    "mt5": {"mt5_snapshot", "mt5_quotes", "mt5_candles", "mt5_history"},
}


class AgentTokenCase(WebAppCase):
    def setUp(self):
        super().setUp()
        self.tokens = AgentTokens(self.auth)
        self.tokens.initialize()
        self.session_client("owner", WebRole.OPERATOR)  # creates the account and enrols its TOTP

    def issue(self, scopes=("read", "lab"), owner="owner", **kwargs):
        token, _info = self.tokens.issue("cli:test", "bot", owner, list(scopes), **kwargs)
        return token

    def bearer(self, token, path, method="GET", origin=ORIGIN, ip="203.0.113.10", **kwargs):
        headers = {"Authorization": f"Bearer {token}"}
        if origin:
            headers["Origin"] = origin
        return self.client(ip).open(path, method=method, headers=headers, **kwargs)

    def events(self, action):
        return [e for e in self.auth.events(limit=500) if e["action"] == action]


SNAPSHOT = {"account": {"login": 114765880, "server": "MetaQuotes-Demo", "environment": "DEMO", "currency": "USD",
                        "balance": 99999.88, "equity": 99505.39, "margin": 5012.66, "margin_free": 94492.73,
                        "leverage": 100, "algo_trading_enabled": True},
            "positions": [{"ticket": 1, "symbol": "AUDUSD", "side": "SHORT", "volume": 0.88, "price_open": 0.70553,
                           "price_current": 0.7064, "sl": 0.71119, "profit": -76.56, "astra": True,
                           "time_open": "2026-10-28T21:22:29+00:00"}],
            "actions": [{"kind": "open", "symbol": "AUDUSD", "side": "SHORT", "lots": 0.88, "price": 0.70553,
                         "sl": 0.71119, "reason": "position SHORT du compte simulé"}]}


class Mt5DashboardTests(AgentTokenCase):
    def test_only_a_token_with_the_mt5_scope_can_report_the_account(self):
        post = {"method": "POST", "json": SNAPSHOT}
        for name, scopes in (("reader", ("read",)), ("labber", ("read", "lab"))):
            token, _ = self.tokens.issue("cli:test", name, "owner", list(scopes))
            self.assertEqual(self.bearer(token, "/api/mt5/snapshot", **post).status_code, 403)
        browser = self.session_client("ops", WebRole.OPERATOR)
        headers = {"Origin": ORIGIN, "X-CSRF-Token": self.page_csrf(browser)}
        self.assertEqual(browser.post("/api/mt5/snapshot", json=SNAPSHOT, headers=headers).status_code, 403)  # not a person
        token = self.issue(scopes=("read", "mt5"))
        self.assertEqual(self.bearer(token, "/api/mt5/snapshot", **post).status_code, 200)
        overview = self.bearer(token, "/api/mt5/overview").get_json()
        self.assertEqual((overview["environment"], overview["connected"], overview["live_locked"]), ("DEMO", True, True))
        self.assertEqual(overview["snapshot"]["open_profit"], -76.56)
        self.assertEqual(overview["events"][0]["kind"], "open")
        viewer = self.session_client("viewer", WebRole.READ_ONLY)
        self.assertEqual(viewer.get("/api/mt5/overview").get_json()["snapshot"]["account"]["login"], 114765880)

    def test_a_non_demo_or_malformed_snapshot_is_refused_and_nothing_is_stored(self):
        token = self.issue(scopes=("read", "mt5"))
        real = {**SNAPSHOT, "account": {**SNAPSHOT["account"], "environment": "REAL"}}
        broken = {**SNAPSHOT, "positions": [{**SNAPSHOT["positions"][0], "side": "HOLD"}]}
        nan = {**SNAPSHOT, "account": {**SNAPSHOT["account"], "equity": float("nan")}}
        for payload in (real, broken, {"account": "x"}, nan):
            response = self.bearer(token, "/api/mt5/snapshot", method="POST", data=json.dumps(payload),
                                   content_type="application/json")
            self.assertEqual(response.status_code, 400, payload)
        self.assertIsNone(self.bearer(token, "/api/mt5/overview").get_json()["snapshot"])


CANDLES = {"server": "MetaQuotes-Demo", "symbol": "EURUSD", "timeframe": "H1", "candles": [
    {"ts": "2026-10-28T10:00:00+00:00", "open": 1.1, "high": 1.102, "low": 1.099, "close": 1.101, "volume": 10},
    {"ts": "2026-10-28T11:00:00+00:00", "open": 1.101, "high": 1.103, "low": 1.1, "close": 1.102, "volume": 12}]}


class Mt5FlowTests(AgentTokenCase):
    def send(self, token, path, payload):
        return self.bearer(token, path, method="POST", data=json.dumps(payload), content_type="application/json")

    def test_quotes_candles_and_the_chart_with_indicators(self):
        token = self.issue(scopes=("read", "mt5"))
        quotes = {"quotes": [{"symbol": "EURUSD", "bid": 1.1, "ask": 1.10002, "time": "2026-10-28T20:00:00+00:00"}]}
        self.assertEqual(self.send(token, "/api/mt5/quotes", quotes).status_code, 200)
        self.assertEqual(self.send(token, "/api/mt5/candles", CANDLES).get_json()["inserted"], 2)
        self.assertEqual(self.send(token, "/api/mt5/candles", CANDLES).get_json()["already_stored"], 2)
        chart = self.bearer(token, "/api/market/chart?symbol=EURUSD&timeframe=H1&source=MT5_METAQUOTES_DEMO").get_json()
        self.assertEqual(len(chart["candles"]), 2)
        self.assertEqual(set(chart["indicators"]), {"sma20", "sma50", "rsi14", "macd", "signal", "histogram"})
        self.assertEqual(len(chart["indicators"]["rsi14"]), 2)
        board = self.bearer(token, "/api/dashboard/overview").get_json()
        self.assertEqual(board["quotes"]["EURUSD"]["bid"], 1.1)
        self.assertTrue(board["quotes"]["EURUSD"]["live"])
        self.assertEqual(len(board["signals"]), 7)

    def test_chart_is_oldest_first_with_indicators_on_the_right_end_and_a_correct_24h_change(self):
        token = self.issue(scopes=("read", "mt5"))
        start = datetime(2026, 10, 1, tzinfo=UTC)
        bars = [{"ts": (start + timedelta(hours=i)).isoformat(), "open": 1.1 + 0.001 * i, "high": 1.1015 + 0.001 * i,
                 "low": 1.0995 + 0.001 * i, "close": 1.1005 + 0.001 * i, "volume": 1} for i in range(300)]
        self.assertEqual(self.send(token, "/api/mt5/candles", {**CANDLES, "candles": bars}).status_code, 200)
        chart = self.bearer(token, "/api/market/chart?symbol=EURUSD&timeframe=H1&source=MT5_METAQUOTES_DEMO&limit=50").get_json()
        stamps = [c["ts"] for c in chart["candles"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(stamps[-1], bars[-1]["ts"])  # the newest bar is the last one
        self.assertEqual(chart["indicators"]["rsi14"][-1], 100.0)  # a series that only rises
        self.assertGreater(chart["indicators"]["sma20"][-1], chart["indicators"]["sma50"][-1])  # fast above slow
        quotes = {"quotes": [{"symbol": "EURUSD", "bid": 1.5, "ask": 1.50002, "time": "2026-10-13T12:00:00+00:00"}]}
        self.send(token, "/api/mt5/quotes", quotes)
        change = self.bearer(token, "/api/dashboard/overview").get_json()["quotes"]["EURUSD"]["change_24h_pct"]
        expected = round((1.5 / bars[-25]["close"] - 1) * 100, 3)  # 24 bars before the newest one
        self.assertEqual(change, expected)

    def test_risk_panel_reconciliation_and_execution_quality_reach_the_dashboard(self):
        token = self.issue(scopes=("read", "mt5"))
        risk = {"entries_allowed": False, "checks": [{"name": "arret_d_urgence", "passed": False, "detail": "actif"}],
                "limits": {"max_positions": 7}, "daily_loss_pct": 0.4, "drawdown_pct": 0.2}
        recon = {"checked_at": "2026-10-29T09:00:00+00:00", "ok": False,
                 "divergences": [{"level": "alerte", "symbol": "EURUSD", "code": "position_sans_stop", "detail": "x"}]}
        actions = [{"kind": "open", "symbol": "EURUSD", "side": "LONG", "lots": 0.5, "price": 1.1, "slippage_pips": 0.4,
                    "spread_pips": 0.2, "latency_ms": 120, "requested_price": 1.0999},
                   {"kind": "open", "symbol": "GBPUSD", "side": "SHORT", "lots": 0.5, "price": 1.3, "slippage_pips": 0.2,
                    "spread_pips": 0.4, "latency_ms": 80, "requested_price": 1.3},
                   {"kind": "refused", "symbol": "USDJPY", "side": "LONG", "reason": "arret_d_urgence : actif"}]
        body = {**SNAPSHOT, "risk": risk, "reconciliation": recon, "actions": actions}
        self.assertEqual(self.send(token, "/api/mt5/snapshot", body).status_code, 200)
        board = self.bearer(token, "/api/dashboard/overview").get_json()
        self.assertFalse(board["mt5"]["snapshot"]["risk"]["entries_allowed"])
        self.assertEqual(board["mt5"]["snapshot"]["reconciliation"]["divergences"][0]["code"], "position_sans_stop")
        execution = board["performance"]["execution"]
        self.assertEqual((execution["orders"], execution["refused"], execution["avg_slippage_pips"],
                          execution["avg_spread_pips"], execution["avg_latency_ms"]), (2, 1, 0.3, 0.3, 100.0))
        too_big = {**SNAPSHOT, "risk": {"blob": "x" * 20_000}}
        self.assertEqual(self.send(token, "/api/mt5/snapshot", too_big).status_code, 400)
        self.assertEqual(self.send(token, "/api/mt5/snapshot", {**SNAPSHOT, "risk": [1]}).status_code, 400)

    def test_dashboard_overview_serves_one_backend_accounting_and_separate_freshness(self):
        from test_mt5_accounting import ACCOUNT, DEALS, ORDERS, POSITIONS

        token = self.issue(scopes=("read", "mt5"))
        history = {"server": "MetaQuotes-Demo", "deals": DEALS, "orders": ORDERS}
        self.assertEqual(self.send(token, "/api/mt5/history", history).status_code, 200)
        snap = {"account": ACCOUNT, "positions": POSITIONS, "actions": [], "bridge": {"stop_file": True, "last_error": None}}
        self.assertEqual(self.send(token, "/api/mt5/snapshot", snap).status_code, 200)
        board = self.bearer(token, "/api/dashboard/overview").get_json()
        acc = board["accounting"]
        self.assertEqual((acc["net_result"], acc["floating_total"], acc["realized_net"], acc["net_deposits"]),
                         (-144.39, -144.27, -0.12, 100000.0))
        self.assertTrue(acc["consistent"])
        self.assertEqual(acc["counts"]["orders"], 7)
        self.assertEqual(len(board["mt5"]["recent_deals"]), 7)  # the balance deposit is not a trade
        self.assertEqual({p["origin"]["astra"] for p in board["mt5"]["snapshot"]["positions"]}, {True})
        self.assertEqual([line["key"] for line in board["freshness"]],
                         ["quote", "candle", "strategy_candle", "strategy_eval", "signal", "attempt", "confirmed", "bridge", "history"])
        bridge = next(line for line in board["freshness"] if line["key"] == "bridge")
        self.assertIn("STOP", bridge["note"])
        self.assertNotIn("baseline_equity", board["performance"])
        for name, scopes in (("reader", ("read",)), ("labber", ("read", "lab"))):
            other, _ = self.tokens.issue("cli:test", name, "owner", list(scopes))
            self.assertEqual(self.send(other, "/api/mt5/history", history).status_code, 403)

    def test_bad_quotes_and_candles_are_refused(self):
        token = self.issue(scopes=("read", "mt5"))
        bad_quote = {"quotes": [{"symbol": "EURUSD", "bid": 1.2, "ask": 1.1, "time": "x"}]}
        self.assertEqual(self.send(token, "/api/mt5/quotes", bad_quote).status_code, 400)
        for change in ({"server": "Broker-Live"}, {"timeframe": "M1"}, {"symbol": "XAUUSD"},
                       {"candles": [{**CANDLES["candles"][0], "high": 1.0}]},
                       {"candles": [{**CANDLES["candles"][0], "ts": "2999-01-01T00:00:00+00:00"}]},
                       {"candles": [{**CANDLES["candles"][0], "ts": "2026-10-28T10:00:00"}]}):
            self.assertEqual(self.send(token, "/api/mt5/candles", {**CANDLES, **change}).status_code, 400, change)

    def test_a_token_without_the_mt5_scope_cannot_feed_the_dashboard(self):
        token = self.issue(scopes=("read", "lab"))
        self.assertEqual(self.send(token, "/api/mt5/quotes", {"quotes": []}).status_code, 403)
        self.assertEqual(self.send(token, "/api/mt5/candles", CANDLES).status_code, 403)

    def test_allowed_servers_are_the_same_in_the_connector_and_the_dashboard(self):
        from astra import mt5view
        from astra.brokers import mt5_demo

        self.assertEqual(mt5_demo.ALLOWED_SERVERS, mt5view.SERVERS)
        self.assertEqual(mt5_demo.source_name("MetaQuotes-Demo"), mt5view.source_name("MetaQuotes-Demo"))


class AgentTokenTests(AgentTokenCase):
    def test_server_allowlist_is_exactly_the_reviewed_one(self):
        # A new route must be added here on purpose before any token can reach it.
        self.assertEqual({k: set(v) for k, v in AGENT_ENDPOINTS.items()}, EXPECTED_AGENT_ENDPOINTS)
        endpoints = {rule.endpoint for rule in self.app.url_map.iter_rules()}
        self.assertLessEqual(set().union(*AGENT_ENDPOINTS.values()), endpoints)

    def test_read_token_reads_and_identifies_itself(self):
        token = self.issue(scopes=("read",))
        response = self.bearer(token, "/api/agent/whoami")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body["agent"], body["owner"], body["scopes"], body["mode"]), ("bot", "owner", ["read"], "SIMULATION"))
        self.assertEqual(self.bearer(token, "/api/status").get_json()["mode"], "SIMULATION")
        self.assertEqual(self.bearer(token, "/api/controls").status_code, 200)

    def test_whoami_needs_a_token(self):
        self.assertEqual(self.client().get("/api/agent/whoami").status_code, 401)
        browser = self.session_client("reader", WebRole.READ_ONLY)
        self.assertEqual(browser.get("/api/agent/whoami").status_code, 404)

    def test_token_is_stored_only_as_a_digest(self):
        token = self.issue()
        self.assertNotIn(token.encode(), Path(self.web.auth_database_path).read_bytes())
        self.assertNotIn(token, json.dumps(self.tokens.list()))
        self.assertNotIn(token, json.dumps(self.auth.events(limit=500)))

    def test_read_scope_cannot_request_lab_work(self):
        token = self.issue(scopes=("read",))
        response = self.bearer(token, "/api/lab/backtests", method="POST", json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.events("agent.access")[0]["result"], "forbidden")

    def test_lab_request_is_attributed_to_the_agent_and_audited(self):
        token = self.issue()
        self.assertEqual(self.bearer(token, "/api/market/ingest", method="POST", origin=None).status_code, 403)
        response = self.bearer(token, "/api/market/ingest", method="POST")
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        job = JobStore(self.db).jobs()[0]
        self.assertEqual((job["id"], job["actor"]), (response.get_json()["job"]["id"], "agent:bot"))
        event = self.events("agent.request")[0]
        self.assertEqual((event["actor"], event["result"]), ("agent:bot", "ok"))
        self.assertIn("owner=owner", event["detail"])

    def test_routes_closed_to_every_token(self):
        token = self.issue()
        for method, path in [("GET", "/api/audit"), ("GET", "/api/orders"), ("GET", "/api/reconciliations"),
                             ("GET", "/api/admin/users"), ("GET", "/api/admin/auth-events"), ("GET", "/api/session"),
                             ("GET", "/api/demo/overview"), ("POST", "/api/demo/pause"),
                             ("POST", "/api/demo/controlled-test"), ("POST", "/api/market/import")]:
            with self.subTest(path=path):
                self.assertEqual(self.bearer(token, path, method=method).status_code, 403)
        for path in ["/", "/admin/users", "/login", "/static/app.js"]:
            with self.subTest(path=path):
                self.assertEqual(self.bearer(token, path).status_code, 403)

    def test_agent_can_stop_but_never_restart_a_simulation(self):
        token = self.issue()
        self.assertEqual(self.bearer(token, "/api/sim/sessions/1/start", method="POST").status_code, 403)
        self.assertNotEqual(self.bearer(token, "/api/sim/sessions/1/stop", method="POST").status_code, 403)

    def test_cookie_and_bearer_together_are_refused(self):
        token = self.issue()
        client = self.client()
        client.set_cookie(SESSION_COOKIE, "x" * 43, domain="astra.test")
        response = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.bearer("Basic abc", "/api/status").status_code, 401)

    def test_expired_revoked_and_unknown_tokens_are_refused(self):
        token = self.issue(days=1)
        self.clock.advance(86400 + 1)
        self.assertEqual(self.bearer(token, "/api/status").status_code, 401)
        self.assertIn("reason=expired", self.events("agent.auth")[0]["detail"])
        fresh = self.issue()
        self.assertEqual(self.tokens.revoke("cli:test", name="bot"), 2)  # the expired one is revoked too
        self.assertEqual(self.bearer(fresh, "/api/status").status_code, 401)
        self.assertEqual(self.bearer("astra_agt_" + "A" * 43, "/api/status").status_code, 401)

    def test_owner_changes_end_the_token(self):
        token = self.issue()
        self.auth.reset_password("cli:test", "owner")
        self.assertEqual(self.bearer(token, "/api/status").status_code, 401)
        self.assertEqual(self.tokens.list()[0]["state"], "revoked")

    def test_disabled_owner_ends_the_token(self):
        token = self.issue()
        self.auth.set_active("cli:test", "owner", False)
        self.assertEqual(self.bearer(token, "/api/status").status_code, 401)

    def test_demoted_owner_loses_lab_routes(self):
        token = self.issue()
        self.auth.set_role("cli:test", "owner", WebRole.READ_ONLY)
        self.assertEqual(self.bearer(token, "/api/status").status_code, 200)
        self.assertEqual(self.bearer(token, "/api/market/ingest", method="POST").status_code, 403)

    def test_address_list(self):
        token = self.issue(networks=["198.51.100.0/24"])
        self.assertEqual(self.bearer(token, "/api/status").status_code, 401)
        self.assertEqual(self.bearer(token, "/api/status", ip="198.51.100.7").status_code, 200)

    def test_invalid_tokens_are_throttled_per_address(self):
        statuses = [self.bearer("astra_agt_" + "B" * 43, "/api/status").status_code for _ in range(7)]
        self.assertEqual(statuses[:5], [401] * 5)
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(self.bearer(self.issue(), "/api/status", ip="198.51.100.9").status_code, 200)

    def test_issue_refusals(self):
        self.add_user("viewer", WebRole.READ_ONLY)
        with self.assertRaises(AuthError):
            self.tokens.issue("cli:test", "bot", "viewer", ["lab"])
        self.add_user("newop", WebRole.OPERATOR)  # MFA not enrolled yet
        with self.assertRaises(AuthError):
            self.tokens.issue("cli:test", "bot", "newop", ["read"])
        for kwargs in ({"days": 91}, {"days": 0}, {"networks": ["not-an-ip"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AuthError):
                self.tokens.issue("cli:test", "bot", "owner", ["read"], **kwargs)
        for scopes in (["admin"], ["live"], ["demo"], []):
            with self.subTest(scopes=scopes), self.assertRaises(AuthError):
                self.tokens.issue("cli:test", "bot", "owner", scopes)
        self.issue()
        self.issue()
        with self.assertRaises(AuthError):  # at most two active tokens per agent
            self.issue()


def flask_transport(case, token):
    def send(method, path, body):
        response = case.bearer(token, path, method=method, json=body if method == "POST" else None)
        return response.status_code, response.get_json(silent=True)

    return send


class AgentIntegrationTests(AgentTokenCase):
    """The agent against the real application, with a CSV series imported by the real worker."""

    def setUp(self):
        super().setUp()
        root = Path(self.temp.name)
        self.worker = Worker(self.db, root / "imports", ecb_fetch=lambda url, timeout: (_ for _ in ()).throw(OSError()))
        self.worker.initialize()
        self.worker.ecb_enabled = False
        (root / "imports").mkdir(exist_ok=True)
        path = root / "imports" / "series.csv"
        path.write_bytes(CSV)
        self.worker.jobs.enqueue("import_csv", {"path": str(path), "symbol": "EURUSD", "timeframe": "H1",
                                                "source": "fixture"}, "test", dedupe_key="series")
        self.worker.tick()
        self.workdir = root / "agent"

    def agent(self, token, **policy):
        policy = {"min_candles": 50, "variants": ((5, 10), (10, 20), (20, 40), (30, 60)), **policy}
        return Agent(AstraClient(flask_transport(self, token)), self.workdir, AgentPolicy(**policy),
                     clock=lambda: datetime(2026, 10, 27, 12, 0, tzinfo=UTC))

    def test_one_cycle_requests_backtests_within_budget_and_reports(self):
        agent = self.agent(self.issue())
        self.assertEqual(agent.run(once=True), 0)
        backtests = self.bearer(self.issue_second(), "/api/lab/backtests").get_json()["backtests"]
        self.assertEqual(len(backtests), 3)  # max_pending_backtests
        self.assertTrue(all(b["actor"] == "agent:bot" for b in backtests))
        summary = (self.workdir / "summary-2026-10-27.md").read_text(encoding="utf-8")
        self.assertIn("mode SIMULATION", summary)
        journal = (self.workdir / "journal-2026-10-27.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum(json.loads(line)["kind"] == "backtest_requested" for line in journal), 3)
        for _ in range(5):  # the worker runs a backtest (after any learning job); the agent tops the queue up
            self.worker.tick()
        agent.run(once=True)
        self.assertEqual(agent.state.backtests_today, 4)

    def issue_second(self):
        token, _ = self.tokens.issue("cli:test", "reader", "owner", ["read"])
        return token

    def test_kill_switch_means_observe_only(self):
        ExecutionStore(self.db).set_control("kill_switch", {"active": True}, actor="test", audit_type="KILL_SWITCH",
                                            details={})
        agent = self.agent(self.issue())
        agent.run(once=True)
        self.assertEqual(self.events("agent.request"), [])
        self.assertIn("observe_only", agent.state.day_log)

    def test_read_only_token_never_writes(self):
        agent = self.agent(self.issue(scopes=("read",)))
        agent.run(once=True)
        self.assertEqual(self.events("agent.request"), [])

    def test_revoked_token_halts_the_agent(self):
        token = self.issue()
        self.tokens.revoke("cli:test", name="bot")
        self.assertEqual(self.agent(token).run(once=True), 2)


class AgentOverHttpTests(AgentTokenCase):
    """The real HTTP transport (urllib) against the application served on loopback (private mode)."""

    public_hosts = ()
    trusted_proxy = False
    client_class = LoopbackClient

    def post(self, client, path, data=None, origin="http://127.0.0.1:8765", **kwargs):
        return super().post(client, path, data, origin=origin, **kwargs)

    def test_agent_cycle_over_http(self):
        import socket
        import threading

        from werkzeug.serving import make_server

        from astra.agent import http_transport
        from astra.config import AppConfig

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.config = AppConfig(database_path=self.db, port=port)
        server = make_server("127.0.0.1", port, self.make_app(), threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            token = self.issue()
            transport = http_transport(f"http://127.0.0.1:{port}", token)
            self.assertEqual(transport("GET", "/api/agent/whoami", None)[1]["agent"], "bot")
            self.assertEqual(transport("POST", "/api/market/ingest", {})[0], 202)
            self.assertEqual(transport("GET", "/api/audit", None)[0], 403)
            workdir = Path(self.temp.name) / "agent-http"
            self.assertEqual(Agent(AstraClient(transport), workdir).run(once=True), 0)
            self.assertTrue(list(workdir.glob("summary-*.md")))
            self.assertEqual(http_transport(f"http://127.0.0.1:{port}", "astra_agt_bad" + "x" * 30)(
                "GET", "/api/status", None)[0], 401)
        finally:
            server.shutdown()
            thread.join(5)

    def test_transport_refuses_plain_http_off_loopback(self):
        from astra.agent import http_transport

        with self.assertRaises(ValueError):
            http_transport("http://astra.example.org", "t")


class FakeApi:
    """A scripted dashboard for the agent's decision logic."""

    def __init__(self, mode="SIMULATION"):
        self.mode = mode
        self.posts = []
        self.backtests = [{"id": 7, "status": "DONE", "symbol": "EURUSD", "timeframe": "D1", "source": "ECB_EXR",
                           "metrics": {"trade_count": 40, "profit_factor": 1.6, "max_drawdown_pct": 6.0, "net_pnl": 900}}]
        self.sessions = [{"id": 3, "name": "old", "status": "RUNNING", "actor": "agent:bot",
                          "account": {"initial_capital": 10000, "net_pnl": -800, "drawdown_halt": False}}]

    def __call__(self, method, path, body):
        if method == "POST":
            self.posts.append((path, body))
            return 201, {"session_id": 11} if path == "/api/sim/sessions" else {"id": 3, "status": "STOPPED"}
        routes = {
            "/api/agent/whoami": {"agent": "bot", "owner": "owner", "scopes": ["read", "lab"], "mode": "SIMULATION"},
            "/api/status": {"mode": self.mode, "live_locked": True},
            "/api/controls": {"entries_allowed": True},
            "/api/market/overview": {"sources": [{"name": "ECB_EXR", "freshness": {"state": "UP_TO_DATE"}}],
                                     "coverage": [], "worker": {"state": "OK"}},
            "/api/lab/backtests": {"backtests": self.backtests},
            "/api/lab/backtests/7": {"params": {"strategy_params": {"fast": 20, "slow": 50}, "risk": {}, "costs": {}}},
            "/api/sim/sessions": {"sessions": self.sessions},
        }
        return 200, routes[path]


class AgentDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.workdir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def agent(self, api):
        agent = Agent(AstraClient(api), self.workdir, clock=lambda: datetime(2026, 10, 27, tzinfo=UTC))
        agent.state.submitted["EURUSD|D1|ECB_EXR|20|50"] = 7  # the agent's own backtest
        return agent

    def test_stops_a_losing_session_and_starts_the_best_backtest(self):
        api = FakeApi()
        self.agent(api).run(once=True)
        self.assertEqual(api.posts[0], ("/api/sim/sessions/3/stop", {}))
        path, body = api.posts[1]
        self.assertEqual((path, body["symbols"], body["source"]), ("/api/sim/sessions", ["EURUSD"], "ECB_EXR"))
        self.assertEqual(body["strategy_params"], {"fast": 20, "slow": 50})

    def test_halts_unless_simulation(self):
        for mode in ("DEMO", "LIVE"):
            api = FakeApi(mode=mode)
            with self.subTest(mode=mode):
                self.assertEqual(self.agent(api).run(once=True), 2)
                self.assertEqual(api.posts, [])

    def test_client_refuses_routes_outside_its_allowlist(self):
        client = AstraClient(FakeApi())
        for method, path in [("POST", "/api/demo/pause"), ("POST", "/api/sim/sessions/3/start"),
                             ("GET", "/api/audit"), ("POST", "/api/admin/users"), ("GET", "/api/orders")]:
            with self.subTest(path=path), self.assertRaises(PermissionError):
                client._call(method, path)

    def test_refused_token_raises_halt(self):
        client = AstraClient(lambda method, path, body: (401, {"error": "invalid_token"}))
        with self.assertRaises(AgentHalt):
            client.get("/api/status")


if __name__ == "__main__":
    unittest.main()
