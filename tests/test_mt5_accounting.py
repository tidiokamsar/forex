"""MT5 accounting, reconciliation with the real figures read from the DEMO account on 2026-10-29.

The fixture below is the broker's own answer (account, positions, deal history, order history) captured by a
read-only audit of the terminal; times are converted from the server clock (UTC+2 on that date) to UTC.
"""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from _support import workspace_tempdir
from astra import boardstats, mt5accounting, mt5view
from astra.brokers import mt5_demo
from test_mt5_demo import FakeMt5, server_seconds

MAGIC = 20261028
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
ACCOUNT = {"login": 114765880, "server": "MetaQuotes-Demo", "environment": "DEMO", "currency": "USD", "balance": 99999.88,
           "equity": 99855.61, "margin": 5012.66, "margin_free": 94842.95, "leverage": 100, "credit": 0.0,
           "algo_trading_enabled": True}


def position(ticket, symbol, side, volume, open_price, current, sl, profit, swap, opened):
    return {"ticket": ticket, "identifier": ticket, "symbol": symbol, "side": side, "volume": volume, "price_open": open_price,
            "price_current": current, "sl": sl, "tp": 0.0, "profit": profit, "swap": swap, "magic": MAGIC, "comment": "ASTRA",
            "reason": 3, "astra": True, "time_open": opened}


POSITIONS = [
    position(11149214517, "AUDUSD", "SHORT", 0.88, 0.70553, 0.70525, 0.71119, 24.64, -39.07, "2026-10-28T21:22:29+00:00"),
    position(11149214519, "GBPUSD", "SHORT", 0.81, 1.33243, 1.3323, 1.33853, 10.53, -5.35, "2026-10-28T21:22:29+00:00"),
    position(11149214524, "NZDUSD", "SHORT", 1.0, 0.56252, 0.56199, 0.56751, 53.0, -20.1, "2026-10-28T21:22:29+00:00"),
    position(11149214525, "USDCAD", "LONG", 1.38, 1.39947, 1.3982, 1.39444, -125.35, -2.37, "2026-10-28T21:22:30+00:00"),
    position(11149214532, "USDJPY", "LONG", 1.37, 157.814, 157.768, 157.242, -39.94, -0.26, "2026-10-28T21:22:30+00:00"),
]


def deal(ticket, order, position_id, ts, symbol, kind, entry, volume, price, profit=0.0, magic=MAGIC, comment="ASTRA"):
    return {"ticket": ticket, "order": order, "position_id": position_id, "ts": ts, "symbol": symbol, "type": kind, "entry": entry,
            "volume": volume, "price": price, "commission": 0.0, "swap": 0.0, "fee": 0.0, "profit": profit, "magic": magic,
            "comment": comment, "reason": 3 if magic else 0}


DEALS = [
    deal(10888829314, 0, 0, "2026-10-28T15:45:45+00:00", "", 2, 0, 0.0, 0.0, 100000.0, magic=0, comment=""),
    deal(10896045589, 11149214323, 11149214323, "2026-10-28T21:22:08+00:00", "EURUSD", 0, 0, 0.01, 1.11551),
    deal(10896045590, 11149214324, 11149214323, "2026-10-28T21:22:08+00:00", "EURUSD", 1, 1, 0.01, 1.11539, -0.12),
    deal(10896045849, 11149214517, 11149214517, "2026-10-28T21:22:29+00:00", "AUDUSD", 1, 0, 0.88, 0.70553),
    deal(10896045851, 11149214519, 11149214519, "2026-10-28T21:22:29+00:00", "GBPUSD", 1, 0, 0.81, 1.33243),
    deal(10896045856, 11149214524, 11149214524, "2026-10-28T21:22:29+00:00", "NZDUSD", 1, 0, 1.0, 0.56252),
    deal(10896045857, 11149214525, 11149214525, "2026-10-28T21:22:30+00:00", "USDCAD", 0, 0, 1.38, 1.39947),
    deal(10896045864, 11149214532, 11149214532, "2026-10-28T21:22:30+00:00", "USDJPY", 0, 0, 1.37, 157.814),
]
ORDERS = [{"ticket": d["order"], "position_id": d["position_id"], "ts": d["ts"], "symbol": d["symbol"], "type": d["type"],
           "state": 4, "volume": d["volume"], "magic": MAGIC, "comment": "ASTRA"} for d in DEALS[1:]]


def snapshot(account=None, positions=None, **extra):
    return {"account": {**ACCOUNT, **(account or {})}, "positions": POSITIONS if positions is None else positions,
            "actions": [], **extra}


class AccountingTests(unittest.TestCase):
    def compute(self, snap=None, deals=None, orders=None, series=None, now=NOW):
        snap = snap or snapshot()
        positions = [{**p, "origin": mt5accounting.origin(p, DEALS if deals is None else deals, MAGIC)} for p in snap["positions"]]
        return mt5accounting.accounting({**snap, "positions": positions}, DEALS if deals is None else deals,
                                        ORDERS if orders is None else orders, series or [], now, MAGIC)

    def test_the_real_account_reconciles_exactly(self):
        a = self.compute(series=[("2026-10-29T02:31:38+00:00", 99631.78), ("2026-10-29T03:00:00+00:00", 99895.54)])
        self.assertEqual((a["balance"], a["equity"], a["margin"], a["margin_free"]), (99999.88, 99855.61, 5012.66, 94842.95))
        self.assertEqual((a["floating_profit"], a["floating_swap"], a["floating_total"]), (-77.12, -67.15, -144.27))
        self.assertEqual((a["net_deposits"], a["realized_gross"], a["realized_costs"], a["realized_net"]), (100000.0, -0.12, 0.0, -0.12))
        self.assertEqual(a["net_result"], -144.39)  # not the +153.35 of the old baseline
        self.assertEqual(round(a["realized_net"] + a["adjustments"] + a["floating_total"], 2), a["net_result"])
        self.assertTrue(a["consistent"], a["checks"])
        self.assertEqual({c["name"] for c in a["checks"]}, {"balance", "equity", "marge", "resultat_net"})

    def test_orders_executions_closed_trades_and_positions_are_four_different_numbers(self):
        a = self.compute()
        self.assertEqual(a["counts"], {"orders": 7, "executions": 7, "closed_trades": 1, "open_positions": 5, "closed_today": 0})
        today = self.compute(now=datetime(2026, 10, 28, 23, 0, tzinfo=UTC))
        self.assertEqual(today["counts"]["closed_today"], 1)  # the test trade closed on the 28th
        self.assertEqual((a["closed_stats"]["wins"], a["closed_stats"]["losses"], a["closed_stats"]["win_rate_pct"]), (0, 1, 0.0))

    def test_swap_is_part_of_the_floating_result_and_a_missing_swap_is_flagged_by_the_equity_check(self):
        no_swap = [{**p, "swap": 0.0} for p in POSITIONS]
        a = self.compute(snapshot(positions=no_swap))
        self.assertEqual(a["floating_total"], -77.12)
        self.assertFalse(a["consistent"])  # equity 99855.61 no longer equals balance + floating
        self.assertFalse(next(c for c in a["checks"] if c["name"] == "equity")["ok"])

    def test_costs_and_withdrawals_are_separated(self):
        costly = [{**d, "commission": -3.5} if d["ticket"] == 10896045589 else d for d in DEALS]
        withdrawal = deal(10999999999, 0, 0, "2026-10-28T16:00:00+00:00", "", 2, 0, 0.0, 0.0, -250.0, magic=0, comment="")
        a = self.compute(snapshot(account={"balance": 99746.38}), deals=[*costly, withdrawal])
        self.assertEqual((a["net_deposits"], a["realized_costs"], a["realized_net"]), (99750.0, -3.5, -3.62))
        self.assertTrue(next(c for c in a["checks"] if c["name"] == "balance")["ok"])
        self.assertEqual(a["net_result"], round(99855.61 - 99750.0, 2))

    def test_a_missing_history_is_reported_and_never_filled_in(self):
        a = self.compute(deals=[], orders=[])
        self.assertIsNone(a["net_deposits"])
        self.assertIsNone(a["net_result"])
        self.assertIsNone(a["counts"]["closed_trades"])
        self.assertTrue(any("historique" in m for m in a["missing"]))
        self.assertEqual(a["counts"]["open_positions"], 5)  # what the terminal reports is still shown
        self.assertFalse(mt5accounting.accounting(None, [], [], [], NOW, MAGIC)["available"])

    def test_drawdown_comes_from_the_equity_series_and_the_deposit(self):
        series = [("2026-10-29T01:00:00+00:00", 99500.0), ("2026-10-29T02:00:00+00:00", 99900.0)]
        a = self.compute(series=series)
        # peak = the 100000 deposit; current equity 99855.61; worst point 99500
        self.assertEqual(a["drawdown"]["peak"], 100000.0)
        self.assertEqual(a["drawdown"]["current_pct"], round((100000 - 99855.61) / 100000 * 100, 3))
        self.assertEqual(a["drawdown"]["max_pct"], 0.5)
        self.assertEqual(mt5accounting.drawdown_stats([], 100.0)["current_pct"], None)
        self.assertTrue(any("série d'equity" in m for m in self.compute(series=[])["missing"]))

    def test_origin_is_proven_only_by_the_broker_identifiers(self):
        proven = mt5accounting.origin(POSITIONS[0], DEALS, MAGIC)
        self.assertTrue(proven["astra"])
        self.assertIn("10896045849", proven["proof"])
        no_history = mt5accounting.origin(POSITIONS[0], [], MAGIC)
        self.assertFalse(no_history["astra"])
        self.assertEqual(no_history["label"], "position du compte — origine non confirmée")
        manual = {**POSITIONS[0], "magic": 0, "comment": ""}
        self.assertFalse(mt5accounting.origin(manual, DEALS, MAGIC)["astra"])
        forged = {**POSITIONS[0], "comment": "ASTRA"}  # a deal opened by hand carries no ASTRA magic
        other = [{**d, "magic": 0, "comment": ""} for d in DEALS]
        self.assertFalse(mt5accounting.origin(forged, other, MAGIC)["astra"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.path = Path(self.temp.name) / "astra.sqlite3"
        self.view = mt5view.Mt5View(self.path)
        self.view.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def feed(self, view=None, **extra):
        view = view or self.view
        view.record_history({"server": "MetaQuotes-Demo", "deals": DEALS, "orders": ORDERS})
        view.record(snapshot(**extra), "agent:test")

    def test_displayed_positions_equal_the_terminal_positions_with_their_origin(self):
        self.feed()
        shown = self.view.overview()["snapshot"]["positions"]
        self.assertEqual([(p["ticket"], p["symbol"], p["side"], p["volume"], p["price_open"], p["price_current"], p["sl"],
                           p["profit"], p["swap"]) for p in shown],
                         [(p["ticket"], p["symbol"], p["side"], p["volume"], p["price_open"], p["price_current"], p["sl"],
                           p["profit"], p["swap"]) for p in POSITIONS])
        self.assertTrue(all(p["origin"]["astra"] for p in shown))
        overview = self.view.overview()
        self.assertEqual(overview["snapshot"]["open_profit"], -144.27)
        self.assertEqual(overview["snapshot"]["astra_positions"], 5)
        self.assertTrue(overview["accounting"]["consistent"])

    def test_history_is_idempotent_and_stored_in_utc(self):
        self.feed()
        self.feed()
        self.assertEqual((len(self.view.deals()), len(self.view.orders())), (8, 7))
        self.assertEqual(self.view.deals()[0]["ts"], "2026-10-28T15:45:45+00:00")

    def test_history_is_validated(self):
        base = {"server": "MetaQuotes-Demo", "deals": DEALS[:1], "orders": []}
        for bad in ({**base, "server": "Broker-Live"}, {**base, "deals": [{**DEALS[0], "ts": "2999-01-01T00:00:00+00:00"}]},
                    {**base, "deals": [{**DEALS[0], "ts": "2026-10-28T10:00:00"}]},
                    {**base, "deals": [{**DEALS[0], "profit": float("nan")}]}, {**base, "deals": [DEALS[0]] * 500}, [1]):
            with self.assertRaises(mt5view.Mt5ViewError):
                self.view.record_history(bad)
        self.assertEqual(self.view.deals(), [])

    def test_everything_survives_a_restart(self):
        self.feed()
        before = self.view.overview()["accounting"]
        reopened = mt5view.Mt5View(self.path)
        reopened.initialize()
        after = reopened.overview()["accounting"]
        self.assertEqual({k: v for k, v in before.items() if k != "history"}, {k: v for k, v in after.items() if k != "history"})
        self.assertEqual((len(reopened.deals()), len(reopened.orders())), (8, 7))

    def test_stale_data_and_sync_errors_are_visible(self):
        error = {"at": "2026-10-29T09:00:00+00:00", "kind": "history_failed", "reason": "connexion perdue"}
        self.feed(bridge={"last_error": error, "history_synced_at": None, "stop_file": True})
        fresh = self.view.overview()
        self.assertTrue(fresh["connected"])
        self.assertEqual(fresh["snapshot"]["bridge"]["last_error"]["reason"], "connexion perdue")
        with self.view._connect() as db:
            db.execute("UPDATE mt5_snapshot SET received_at = ?", ((datetime.now(UTC) - timedelta(hours=2)).isoformat(),))
        stale = self.view.overview()
        self.assertFalse(stale["connected"])
        self.assertGreater(stale["age_seconds"], 3600)

    def test_no_baseline_is_kept_from_the_first_snapshot(self):
        self.feed()
        self.assertNotIn("baseline_equity", self.view.performance())
        self.assertEqual(self.view.accounting()["net_result"], -144.39)

    def test_the_ledger_magic_is_the_one_the_broker_module_uses(self):
        from astra.brokers import mt5_demo_orders

        self.assertEqual(mt5view.ASTRA_MAGIC, mt5_demo_orders.MAGIC)


class ConnectorHistoryTests(unittest.TestCase):
    def test_history_is_read_from_the_terminal_in_utc_without_inventing_anything(self):
        fake = FakeMt5()
        stamp = server_seconds("2026-10-28 23:22")  # server clock, UTC+2
        fake.history_deals_get = lambda a, b: (SimpleNamespace(ticket=1, order=2, position_id=3, time=stamp, symbol="EURUSD", type=0,
                                                               entry=0, volume=0.01, price=1.1, commission=-0.5, swap=0.0, fee=0.0,
                                                               profit=0.0, magic=MAGIC, comment="ASTRA", reason=3),)
        fake.history_orders_get = lambda a, b: ()
        with mt5_demo.Mt5DemoFeed(module=fake) as feed:
            history = feed.history(30)
        self.assertEqual(history["deals"][0]["ts"], "2026-10-28T21:22:00+00:00")
        self.assertEqual((history["deals"][0]["commission"], history["orders"]), (-0.5, []))


class HistoryFallbackTests(unittest.TestCase):
    """A terminal that has not loaded its history answers empty: the connector asks three ways and says which answered."""

    def deal(self, ticket, position_id):
        return SimpleNamespace(ticket=ticket, order=ticket + 1, position_id=position_id, time=server_seconds("2026-10-28 23:22"),
                               symbol="AUDUSD", type=1, entry=0, volume=0.88, price=0.70553, commission=0.0, swap=0.0, fee=0.0,
                               profit=0.0, magic=MAGIC, comment="ASTRA", reason=3)

    def feed(self, **handlers):
        fake = FakeMt5()
        for name, handler in handlers.items():
            setattr(fake, name, handler)
        return mt5_demo.Mt5DemoFeed(module=fake)

    def test_recent_window_answers_first(self):
        with self.feed(history_deals_get=lambda *a, **k: (self.deal(1, 7),) if a else ()) as feed:
            self.assertEqual(feed.history(30)["answered"], "fenêtre récente")

    def test_a_very_wide_window_is_tried_when_the_recent_one_is_empty(self):
        def deals(*args, **kwargs):
            return (self.deal(1, 7),) if args and args[0].year == 2015 else ()

        with self.feed(history_deals_get=deals) as feed:
            result = feed.history(30)
        self.assertEqual((result["answered"], len(result["deals"])), ("fenêtre large", 1))

    def test_each_open_position_is_asked_by_its_ticket_as_a_last_resort(self):
        def deals(*args, **kwargs):
            return (self.deal(11, kwargs["position"]),) if "position" in kwargs else ()

        positions = lambda: (SimpleNamespace(ticket=1), SimpleNamespace(ticket=2))  # noqa: E731
        with self.feed(history_deals_get=deals, positions_get=positions) as feed:
            result = feed.history(30)
        self.assertIn("par position", result["answered"])
        self.assertEqual(len(result["deals"]), 1)  # both positions returned ticket 11: kept once, keyed by the broker's ticket

    def test_an_empty_terminal_stays_empty_and_says_so(self):
        with self.feed() as feed:
            result = feed.history(30)
        self.assertEqual((result["deals"], result["orders"], result["answered"]), ([], [], "aucune réponse"))


class FreshnessTests(unittest.TestCase):
    def lines(self, **kwargs):
        base = dict(quote_at=NOW.isoformat(), snapshot_at=NOW.isoformat(), history_synced_at=NOW.isoformat(),
                    mt5_candle_at=(NOW - timedelta(minutes=30)).isoformat(), strategy_candle_at="2026-10-28T13:15:00+00:00",
                    strategy_evaluated_at=NOW.isoformat(), last_signal_at="2026-10-22T12:15:00+00:00",
                    last_attempt_at="2026-10-28T21:22:30+00:00", last_confirmed_at="2026-10-28T21:22:30+00:00",
                    worker_state="OK", stop_file=False)
        return {line["key"]: line for line in boardstats.freshness(NOW, **{**base, **kwargs})}

    def test_each_timestamp_is_separate_and_a_fresh_quote_does_not_hide_a_stale_strategy(self):
        lines = self.lines(strategy_candle_at="2026-10-26T13:15:00+00:00")  # Monday's candle on Thursday morning
        self.assertEqual(lines["quote"]["state"], "ok")
        self.assertEqual(lines["strategy_candle"]["state"], "retard")
        self.assertEqual(len(lines), 9)
        waiting = self.lines()  # Wednesday's candle, Thursday 10:00 UTC: the next one is not due yet
        self.assertEqual(waiting["strategy_candle"]["state"], "ok")
        self.assertIn("prochaine attendue", waiting["strategy_candle"]["note"])

    def test_stopped_processes_and_missing_values_are_reported(self):
        lines = self.lines(quote_at=(NOW - timedelta(minutes=10)).isoformat(), worker_state="STALE", history_synced_at=None,
                           last_attempt_at=None, snapshot_at=(NOW - timedelta(hours=1)).isoformat(), stop_file=True)
        self.assertEqual(lines["quote"]["state"], "retard")
        self.assertEqual(lines["strategy_eval"]["state"], "retard")
        self.assertEqual((lines["history"]["state"], lines["attempt"]["state"]), ("inconnu", "inconnu"))
        self.assertEqual(lines["bridge"]["state"], "retard")
        self.assertIn("STOP", lines["bridge"]["note"])

    def test_the_weekend_is_not_a_delay_for_the_market_feed(self):
        saturday = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
        old = (saturday - timedelta(hours=14)).isoformat()
        lines = {line["key"]: line for line in boardstats.freshness(
            saturday, quote_at=old, snapshot_at=saturday.isoformat(), history_synced_at=saturday.isoformat(), mt5_candle_at=old,
            strategy_candle_at="2026-10-30T13:15:00+00:00", strategy_evaluated_at=saturday.isoformat(), last_signal_at=None,
            last_attempt_at=None, last_confirmed_at=None, worker_state="OK", stop_file=False)}
        self.assertEqual((lines["quote"]["state"], lines["candle"]["state"]), ("ok", "ok"))
        self.assertEqual(lines["quote"]["note"], "marché fermé")
        self.assertEqual(lines["strategy_candle"]["state"], "ok")  # Friday's candle: the next one is Monday


if __name__ == "__main__":
    unittest.main()
