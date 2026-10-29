"""MT5 DEMO orders and the bridge from ASTRA's managed accounts (fake terminal, fake server)."""

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from _support import workspace_tempdir
from astra import mt5_risk
from astra.agent import AgentHalt, AstraClient
from astra.brokers.mt5_demo import Mt5DemoError
from astra.brokers.mt5_demo_orders import MAGIC, MAGIC_MANUAL, Mt5DemoTrader
from astra.mt5_bridge import Bridge, targets
from test_mt5_demo import FakeMt5, server_seconds

LOGIN = 114765880


class TradingMt5(FakeMt5):
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, ORDER_TIME_GTC = 1, 6, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10009, 10010

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.algo, self.equity, self.sent, self.open_positions, self.next_ticket = True, 100000.0, [], [], 1
        self.mode = "ok"  # ok, reject, lost (executed, no answer), lost_nothing (not executed, no answer), partial, timeout_code

    def account_info(self):
        info = super().account_info()
        return info and SimpleNamespace(**{**vars(info), "equity": self.equity})

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=self.algo)

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=1, volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_stops_level=0,
                               point=0.00001, digits=5)

    tick_time = server_seconds("2026-10-29 11:00")  # server clock UTC+2: 09:00 UTC, the bridge's test clock

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=1.10000, ask=1.10002, time=self.tick_time)

    def order_calc_margin(self, kind, symbol, volume, price):
        return 1000.0 * volume

    def order_calc_profit(self, kind, symbol, volume, price_open, price_close):
        move = (price_close - price_open) if kind == self.ORDER_TYPE_BUY else (price_open - price_close)
        return move * 100000 * volume

    def positions_get(self):
        return tuple(self.open_positions)

    def order_send(self, request):
        self.sent.append(request)
        if request.get("action") == self.TRADE_ACTION_SLTP:
            for p in self.open_positions:
                if p.ticket == request["position"]:
                    p.sl = request["sl"]
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done", order=0, deal=0, volume=0.0, price=0.0)
        if "position" in request:
            self.open_positions = [p for p in self.open_positions if p.ticket != request["position"]]
        else:
            if self.mode == "reject":
                return SimpleNamespace(retcode=10019, comment="No money", order=0, deal=0, volume=0.0, price=0.0)
            if self.mode == "lost_nothing":
                return None
            if self.mode == "timeout_code":
                return SimpleNamespace(retcode=10012, comment="Request canceled by timeout", order=0, deal=0, volume=0.0, price=0.0)
            volume = request["volume"] / 2 if self.mode == "partial" else request["volume"]
            self.open_positions.append(SimpleNamespace(ticket=self.next_ticket, symbol=request["symbol"], type=request["type"],
                                                       volume=volume, price_open=request["price"], sl=request["sl"], profit=0.0,
                                                       magic=request["magic"], comment=request["comment"]))
            self.next_ticket += 1
            if self.mode == "lost":
                return None
            if self.mode == "partial":
                return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE_PARTIAL, comment="partial", order=len(self.sent),
                                       deal=len(self.sent), volume=volume, price=request["price"])
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done", order=len(self.sent), deal=len(self.sent),
                               volume=request["volume"], price=request["price"])


def overview(position=None, pending=None, status="RUNNING"):
    account = {"positions": {"EURUSD": position} if position else {}, "pending": {"EURUSD": pending} if pending else {}}
    return {"pairs": [{"symbol": "EURUSD", "session": {"id": 1, "status": status, "account": account}}]}


SHORT = {"side": "SHORT", "entry_ts": "2026-09-29T12:15:00+00:00", "entry_ref": 1.1355, "stop": 1.1415, "units": 7000}


class FakeServer:
    def __init__(self):
        self.status = {"mode": "SIMULATION", "live_locked": True}
        self.view = overview(SHORT)
        self.posts = []
        self.controls = {"entries_allowed": True, "kill_switch": {"active": False}}
        self.requests, self.results, self.fail_pending = [], [], False

    def __call__(self, method, path, body):
        if method == "POST":
            self.posts.append((path, body))
            if "/api/mt5/requests/" in path:
                self.results.append((path, body))
            return 200, {}
        if path == "/api/mt5/requests/pending":
            if self.fail_pending:
                return 503, {"error": "down"}
            return 200, {"requests": self.requests}
        if path == "/api/controls":
            return 200, self.controls
        return 200, self.status if path == "/api/status" else self.view


class TraderTests(unittest.TestCase):
    def test_orders_need_the_pinned_demo_account_and_algo_trading(self):
        with self.assertRaises(Mt5DemoError):
            Mt5DemoTrader(module=TradingMt5(), expected_login=1).connect()
        fake = TradingMt5()
        with Mt5DemoTrader(module=fake, expected_login=LOGIN) as trader:
            fake.algo = False
            with self.assertRaises(Mt5DemoError) as caught:
                trader.open("EURUSD", "LONG", 0.01, 1.09)
            self.assertIn("Trading Algo", str(caught.exception))
            fake.algo, fake.trade_mode = True, fake.ACCOUNT_TRADE_MODE_REAL  # account switched in the terminal
            with self.assertRaises(Mt5DemoError):
                trader.open("EURUSD", "LONG", 0.01, 1.09)
        self.assertEqual(fake.sent, [])

    def test_risk_sizing_and_manual_positions_are_never_closed(self):
        fake = TradingMt5()
        with Mt5DemoTrader(module=fake, expected_login=LOGIN) as trader:
            lots, sl = trader.lots_for_risk("EURUSD", "SHORT", 0.0060, 500.0)
            self.assertEqual((lots, sl), (0.83, 1.106))  # 0.0060 x 100 000 x 0.83 = 498 USD at the stop
            fake.open_positions.append(SimpleNamespace(ticket=99, symbol="EURUSD", type=0, volume=1.0, price_open=1.1,
                                                       sl=0.0, profit=5.0, magic=0))
            manual = trader.positions()[0]
            self.assertFalse(manual.astra)
            with self.assertRaises(Mt5DemoError):
                trader.close(manual)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.dir = Path(self.temp.name)
        self.server, self.fake = FakeServer(), TradingMt5()
        self.bridge = Bridge(AstraClient(self.server), Mt5DemoTrader(module=self.fake, expected_login=LOGIN), self.dir,
                             clock=lambda: datetime(2026, 10, 29, 9, tzinfo=UTC))

    def tearDown(self):
        self.temp.cleanup()

    def test_targets_follow_positions_and_pending_exits(self):
        self.assertEqual(targets(overview(SHORT))["EURUSD"]["side"], "SHORT")
        self.assertEqual(targets(overview(SHORT, {"side": "FLAT", "label": "AUCUNE_POSITION"}))["EURUSD"]["side"], "FLAT")
        self.assertEqual(targets(overview(None, {"side": "LONG", "label": "ACHAT"}))["EURUSD"]["side"], "FLAT")
        self.assertEqual(targets(overview(SHORT, status="STOPPED"))["EURUSD"]["side"], "FLAT")

    def test_mirror_open_close_and_no_reopen_after_a_stop_out(self):
        first = self.bridge.cycle()
        self.assertEqual([a["kind"] for a in first["actions"]], ["open"])
        self.assertEqual(self.fake.sent[0]["magic"], MAGIC)
        self.assertEqual(len(self.bridge.cycle()["actions"]), 0)  # already mirrored
        self.fake.open_positions.clear()  # the stop was hit on MT5
        self.assertEqual(len(self.bridge.cycle()["actions"]), 0)  # not reopened for the same paper entry
        self.server.view = overview({**SHORT, "entry_ts": "2026-10-29T13:15:00+00:00"})
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["open"])  # a new paper entry
        self.server.view = overview(SHORT, {"side": "FLAT", "label": "AUCUNE_POSITION"})
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["close"])
        self.assertEqual(self.fake.open_positions, [])
        journal = (self.dir / "journal-2026-10-29.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertIn("close", [json.loads(line)["kind"] for line in journal])

    def test_halts_stop_file_and_daily_loss(self):
        (self.dir / "STOP").write_text("", encoding="utf-8")
        self.assertTrue(self.bridge.cycle()["halted"])
        (self.dir / "STOP").unlink()
        self.bridge.state.update({"day": "2026-10-29", "day_start_equity": 104000.0})  # -3.8 % today
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["refused"])
        self.server.status = {"mode": "LIVE", "live_locked": False}
        with self.assertRaises(AgentHalt):
            self.bridge.cycle()
        self.assertEqual(self.fake.sent, [])

    def test_stream_sends_quotes_account_and_positions_without_trading(self):
        with self.bridge.trader:
            sent = self.bridge.stream()
        self.assertEqual(sent["quotes"], 7)
        paths = [p for p, _ in self.server.posts]
        self.assertEqual(paths, ["/api/mt5/quotes", "/api/mt5/snapshot"])
        quote = self.server.posts[0][1]["quotes"][0]
        self.assertEqual((quote["bid"], quote["ask"], quote["time"]), (1.1, 1.10002, "2026-10-29T09:00:00+00:00"))
        self.assertEqual(self.fake.sent, [])

    def test_candles_go_to_the_server_in_utc_for_every_pair_and_timeframe(self):
        with self.bridge.trader:
            count = self.bridge.push_candles(("M5", "H1"), recent=True)
        posts = [(b["symbol"], b["timeframe"]) for p, b in self.server.posts if p == "/api/mt5/candles"]
        self.assertEqual(len(posts), 14)
        self.assertEqual(count, 14 * 2)  # the fake terminal returns two bars
        body = next(b for p, b in self.server.posts if p == "/api/mt5/candles")
        self.assertEqual(body["server"], "MetaQuotes-Demo")
        self.assertEqual(body["candles"][0]["ts"], "2025-12-15T08:00:00+00:00")

    def test_a_large_backfill_is_sent_in_small_batches(self):
        self.fake.rates = [{**self.fake.rates[0], "time": self.fake.rates[0]["time"] + 3600 * i} for i in range(600)]
        with self.bridge.trader:
            self.bridge.push_candles(("H1",), recent=False)
        sizes = [len(b["candles"]) for p, b in self.server.posts if p == "/api/mt5/candles" and b["symbol"] == "EURUSD"]
        self.assertEqual(sizes, [250, 250, 100])
        self.assertTrue(all(len(json.dumps(b)) < 60_000 for p, b in self.server.posts))

    def refused(self):
        return [a for a in self.bridge.cycle()["actions"] if a["kind"] == "refused"]

    def test_the_gateway_refuses_with_reasons_and_reports_each_refusal_once(self):
        self.server.controls = {"entries_allowed": False, "kill_switch": {"active": True}}
        first = self.refused()
        self.assertEqual(len(first), 1)
        self.assertIn("arret_d_urgence", first[0]["reason"])
        self.assertEqual(self.fake.sent, [])
        self.assertEqual(self.refused(), [])  # the same refusal is not repeated every cycle
        self.server.controls = {"entries_allowed": True, "kill_switch": {"active": False}}
        self.bridge._controls = None
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["open"])  # allowed again once cleared

    def test_unreadable_controls_stale_quotes_and_invalid_profiles_all_refuse(self):
        self.server.controls = None  # the answer cannot be read
        self.assertIn("arret_d_urgence", self.refused()[0]["reason"])
        self.assertEqual(self.fake.sent, [])
        self.server.controls = {"entries_allowed": True, "kill_switch": {"active": False}}
        self.bridge._controls = None
        self.bridge.state["last_refusal"].clear()
        self.fake.tick_time = server_seconds("2026-10-29 10:30")  # 30 minutes old
        self.assertIn("fraicheur_des_donnees", self.refused()[0]["reason"])
        self.fake.tick_time = TradingMt5.tick_time
        self.bridge.state["last_refusal"].clear()
        self.bridge.limits = mt5_risk.RiskConfigError("profil cassé")
        self.assertIn("profil_de_risque", self.refused()[0]["reason"])
        self.assertEqual(self.fake.sent, [])

    def test_exits_are_never_blocked_by_the_gateway(self):
        self.bridge.cycle()  # opens the position
        self.server.controls = {"entries_allowed": False, "kill_switch": {"active": True}}
        self.bridge._controls = None
        self.server.view = overview(SHORT, {"side": "FLAT", "label": "AUCUNE_POSITION"})
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["close"])

    def test_execution_quality_and_snapshots_carry_risk_and_reconciliation(self):
        actions = self.bridge.cycle()["actions"]
        opened = next(a for a in actions if a["kind"] == "open")
        self.assertEqual((opened["requested_price"], opened["slippage_pips"], opened["spread_pips"]), (1.1, 0.0, 0.2))  # a sale: the bid
        self.assertIsInstance(opened["latency_ms"], int)
        snapshot = next(b for p, b in reversed(self.server.posts) if p == "/api/mt5/snapshot")
        self.assertTrue(snapshot["risk"]["entries_allowed"])
        self.assertEqual(snapshot["risk"]["limits"]["max_positions"], 7)
        self.assertTrue(snapshot["reconciliation"]["ok"])

    def test_reconciliation_flags_a_position_without_stop_and_an_unplanned_one(self):
        self.bridge.cycle()
        self.fake.open_positions[0].sl = 0.0
        self.bridge.targets = {"EURUSD": {"side": "FLAT"}}
        with self.bridge.trader:
            report = self.bridge.reconcile(self.bridge.trader.positions())
        codes = {d["code"] for d in report["divergences"]}
        self.assertEqual(codes, {"position_sans_stop", "position_non_prevue"})
        self.assertFalse(report["ok"])

    def intent_states(self):
        return [i["status"] for i in self.bridge.state["intents"].values()]

    def test_every_order_is_recorded_before_it_is_sent_and_carries_its_intention_in_the_comment(self):
        self.bridge.cycle()
        (intent,) = self.bridge.state["intents"].values()
        self.assertEqual(intent["status"], "EXECUTE")
        self.assertEqual(self.fake.sent[0]["comment"], f"ASTRA:{intent['id']}")
        saved = json.loads((self.dir / "bridge-state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["intents"][intent["id"]]["status"], "EXECUTE")  # on disk, not only in memory

    def test_a_refusal_by_the_broker_is_a_rejection_reported_once_and_not_retried_every_cycle(self):
        self.fake.mode = "reject"
        first = self.bridge.cycle()["actions"]
        self.assertEqual([a["kind"] for a in first], ["refused"])
        self.assertIn("10019", first[0]["reason"])
        self.assertEqual(self.intent_states(), ["REJETE"])
        self.assertEqual(self.bridge.cycle()["actions"], [])  # same refusal, nothing new to say
        self.assertEqual(self.fake.open_positions, [])

    def test_a_lost_answer_after_a_real_execution_is_found_on_the_account_and_never_sent_twice(self):
        self.fake.mode = "lost"
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["order_unknown"])
        self.assertEqual(self.intent_states(), ["INCONNU"])
        self.fake.mode = "ok"
        later = self.bridge.cycle()["actions"]  # the position exists: the intention is settled, no second order
        self.assertEqual([a["kind"] for a in later], ["intent_resolved"])
        self.assertEqual(self.intent_states(), ["EXECUTE"])
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(len(self.fake.open_positions), 1)

    def test_a_lost_answer_with_no_execution_blocks_for_the_grace_period_then_allows_one_new_attempt(self):
        self.fake.mode = "lost_nothing"
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["order_unknown"])
        self.fake.mode = "ok"
        self.assertEqual(self.bridge.cycle()["actions"], [])  # still inside the grace period: it only looks
        self.assertEqual(len(self.fake.sent), 1)
        self.bridge.clock = lambda: datetime(2026, 10, 29, 9, 5, tzinfo=UTC)  # five minutes later
        self.fake.tick_time = server_seconds("2026-10-29 11:05")  # and the quote is fresh again (the gateway checks its age)
        resolved = self.bridge.cycle()["actions"]
        self.assertEqual([a["kind"] for a in resolved], ["intent_resolved", "open"])  # settled as absent, then one new order
        self.assertEqual(sorted(self.intent_states()), ["ABSENT", "EXECUTE"])
        self.assertEqual(len(self.fake.sent), 2)

    def test_a_timeout_code_is_an_unknown_outcome_not_a_rejection(self):
        self.fake.mode = "timeout_code"
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["order_unknown"])
        self.assertEqual(self.intent_states(), ["INCONNU"])

    def test_a_partial_fill_is_reported_with_the_volume_really_filled(self):
        self.fake.mode = "partial"
        opened = next(a for a in self.bridge.cycle()["actions"] if a["kind"] == "open")
        self.assertEqual((opened["status"], opened["lots"]), ("PARTIEL", self.fake.open_positions[0].volume))
        self.assertEqual(self.intent_states(), ["PARTIEL"])
        self.assertEqual(self.bridge.cycle()["actions"], [])  # the paper entry is mirrored: no top-up order

    def test_a_restart_in_the_middle_of_an_order_is_settled_from_the_account(self):
        self.fake.mode = "lost"
        self.bridge.cycle()
        restarted = Bridge(AstraClient(self.server), Mt5DemoTrader(module=self.fake, expected_login=LOGIN), self.dir,
                           clock=lambda: datetime(2026, 10, 29, 9, 0, tzinfo=UTC))
        self.assertEqual([i["status"] for i in restarted.state["intents"].values()], ["INCONNU"])  # kept across the restart
        self.fake.mode = "ok"
        restarted.cycle()
        self.assertEqual([i["status"] for i in restarted.state["intents"].values()], ["EXECUTE"])
        self.assertEqual(len(self.fake.sent), 1)
        snapshot = next(b for p, b in reversed(self.server.posts) if p == "/api/mt5/snapshot")
        self.assertEqual(snapshot["bridge"]["intents"][0]["status"], "EXECUTE")

    # -- requests queued on the site ---------------------------------------------------------------------

    def ask(self, **request):
        """Queue one request on the fake server, let the bridge take it, return (status, message, actions)."""

        self.server.requests = [{"id": len(self.server.results) + 1, "actor": "web:ops", **request}]
        with self.bridge.trader:
            done = self.bridge.process_requests()
        status, result = self.server.results[-1][1]["status"], self.server.results[-1][1]["result"]
        return status, result["message"], done[0]["actions"]

    def test_a_manual_order_goes_through_the_gateway_with_a_computed_volume_and_its_own_magic(self):
        status, message, actions = self.ask(kind="order", symbol="EURUSD", side="LONG", stop_pips=60.0)
        self.assertEqual((status, message), ("EXECUTE", "ordre exécuté"))
        self.assertEqual(self.fake.sent[0]["magic"], MAGIC_MANUAL)
        self.assertTrue(self.fake.sent[0]["comment"].startswith("ASTRA-M:"))
        self.assertEqual(self.fake.sent[0]["volume"], 0.83)  # 0.5 % of 100 000 at a 60 pip stop: computed, never typed
        self.assertTrue(actions[0]["manual"])
        self.assertEqual([i["status"] for i in self.bridge.state["intents"].values()], ["EXECUTE"])

    def test_a_manual_order_is_refused_under_the_stop_file_the_emergency_stop_and_the_gateway(self):
        (self.dir / "STOP").write_text("", encoding="utf-8")
        self.assertEqual(self.ask(kind="order", symbol="EURUSD", side="LONG", stop_pips=60.0)[0], "REFUSE")
        (self.dir / "STOP").unlink()
        self.server.controls = {"entries_allowed": False, "kill_switch": {"active": True}}
        status, message, _ = self.ask(kind="order", symbol="EURUSD", side="LONG", stop_pips=60.0)
        self.assertEqual(status, "REFUSE")
        self.assertIn("arret_d_urgence", message)
        self.server.controls = {"entries_allowed": True, "kill_switch": {"active": False}}
        self.fake.tick_time = server_seconds("2026-10-29 10:00")  # an hour-old quote
        self.assertIn("fraicheur", self.ask(kind="order", symbol="EURUSD", side="LONG", stop_pips=60.0)[1])
        self.assertEqual(self.fake.sent, [])

    def test_a_manual_position_is_never_closed_by_the_automatic_mirroring(self):
        self.ask(kind="order", symbol="EURUSD", side="LONG", stop_pips=60.0)
        self.server.view = overview(None)  # the paper account holds nothing on EURUSD
        self.assertEqual(self.bridge.cycle()["actions"], [])
        self.assertEqual(len(self.fake.open_positions), 1)

    def test_close_works_even_under_the_stop_file_and_never_touches_a_foreign_position(self):
        self.bridge.cycle()
        ticket = self.fake.open_positions[0].ticket
        self.fake.open_positions.append(SimpleNamespace(ticket=99, symbol="GBPUSD", type=0, volume=1.0, price_open=1.3, sl=1.2,
                                                        profit=0.0, magic=0, comment=""))
        (self.dir / "STOP").write_text("", encoding="utf-8")
        self.assertEqual(self.ask(kind="close", ticket=99)[0], "REFUSE")  # opened by hand: not ours
        status, message, actions = self.ask(kind="close", ticket=ticket)
        self.assertEqual((status, message), ("EXECUTE", "position fermée"))
        self.assertEqual(actions[0]["kind"], "close")
        self.assertEqual([p.ticket for p in self.fake.open_positions], [99])

    def test_a_stop_can_only_move_towards_the_price_on_the_protective_side(self):
        self.bridge.cycle()  # the paper account is short: a sale, so the stop sits above the price
        position = self.fake.open_positions[0]
        position.sl, position.price_current = 1.1060, 1.1000
        self.assertEqual(self.ask(kind="modify_sl", ticket=position.ticket, sl=1.1100)[0], "REFUSE")  # looser: farther away
        self.assertEqual(self.ask(kind="modify_sl", ticket=position.ticket, sl=1.0950)[0], "REFUSE")  # wrong side of the price
        status, message, _ = self.ask(kind="modify_sl", ticket=position.ticket, sl=1.1030)
        self.assertEqual((status, position.sl, message), ("EXECUTE", 1.1030, "stop déplacé à 1.103"))

    def test_pause_and_resume_move_the_stop_file_and_resume_waits_for_the_emergency_stop_to_be_released(self):
        self.assertEqual(self.ask(kind="pause")[0], "EXECUTE")
        self.assertTrue((self.dir / "STOP").exists())
        self.server.controls = {"entries_allowed": False, "kill_switch": {"active": True}}
        self.bridge.controls_cache_clear()
        status, _message, _ = self.ask(kind="resume")
        self.assertEqual(status, "REFUSE")
        self.assertTrue((self.dir / "STOP").exists())
        self.server.controls = {"entries_allowed": True, "kill_switch": {"active": False}}
        self.bridge.controls_cache_clear()
        self.assertEqual(self.ask(kind="resume")[0], "EXECUTE")
        self.assertFalse((self.dir / "STOP").exists())

    def test_the_bridge_survives_an_unreachable_queue_and_an_unknown_kind(self):
        self.server.fail_pending = True
        with self.bridge.trader:
            self.assertEqual(self.bridge.process_requests(), [])
        self.server.fail_pending = False
        self.assertEqual(self.ask(kind="teleport")[0], "REFUSE")

    def test_flow_keeps_one_connection_and_trades_only_when_the_cycle_is_due(self):
        clock = {"t": 0.0}
        self.bridge.flow(300, lambda: clock["t"], lambda s: clock.update(t=clock["t"] + 5), rounds=3)
        paths = [p for p, _ in self.server.posts]
        self.assertEqual(paths.count("/api/mt5/quotes"), 3)  # one stream per round
        self.assertEqual(len(self.fake.sent), 1)  # the paper position opened by the first cycle, nothing after
        self.assertTrue(self.fake.closed)  # the connection is closed when the flow ends


if __name__ == "__main__":
    unittest.main()
