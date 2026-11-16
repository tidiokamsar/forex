"""LOT 3 acceptance tests (written and committed before the implementation): the rollover verdict.

Master document 5.3 and mandate 5.5: one pre-registered experiment on the broker's bid/ask ticks; the rollover hour
comes from the server calendar and its clock changes; criterion: net Sharpe above 1 on data never used, otherwise the
track is closed; a provisional verdict on the history that exists, and an automatic re-evaluation once 6 months of
ticks exist. Prices are executable: a long position buys at the ask and sells at the bid.
"""

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra import rollover
from astra.agent import AstraClient
from astra.brokers.mt5_demo_orders import Mt5DemoTrader
from astra.evidence import EvidenceError, EvidenceRegistry
from astra.mt5_bridge import Bridge
from astra.ticks import TickError, TickStore
from test_mt5_bridge import LOGIN, FakeServer
from test_ticks import TickMt5, ms

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)


def hour_row(symbol, start, first_bid, first_ask, last_bid, last_ask, spread=1.0, ticks=100):
    return {"symbol": symbol, "hour": start, "first_bid": first_bid, "first_ask": first_ask, "last_bid": last_bid,
            "last_ask": last_ask, "median_spread_pips": spread, "p90_spread_pips": spread * 2, "ticks": ticks}


def synthetic_days(days, drift_at=None, spread=0.2):
    """Hourly rows of EURUSD over ``days`` weekdays; the mid moves up by ``drift_at[hour]`` pips in that UTC hour."""

    rows, day, price = [], datetime(2026, 1, 5, tzinfo=UTC), 1.1
    made = 0
    while made < days:
        if day.weekday() < 5:
            for h in range(24):
                move = (drift_at or {}).get(h, 0.0) * 0.0001 + 0.00002 * (((made * 24 + h) * 7919) % 5 - 2)
                half = spread * 0.0001 / 2
                rows.append(hour_row("EURUSD", (day + timedelta(hours=h)).isoformat(), price - half, price + half,
                                     price + move - half, price + move + half, spread))
                price += move
            made += 1
        day += timedelta(days=1)
    return rows


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_o1_ticks_are_summarised_per_utc_hour_with_real_bid_and_ask(self):
        ticks = [(0, ms("2026-11-16 10:00:01"), 1.1000, 1.1002, 6), (0, ms("2026-11-16 10:30:00"), 1.1010, 1.1013, 6),
                 (0, ms("2026-11-16 10:59:59"), 1.1020, 1.1021, 6), (0, ms("2026-11-16 11:00:00"), 1.1030, 1.1032, 6)]
        rows = rollover.hourly_from_ticks("EURUSD", ticks)
        self.assertEqual([r["hour"][11:13] for r in rows], ["10", "11"])
        first = rows[0]
        self.assertEqual((first["first_bid"], first["first_ask"], first["last_bid"], first["last_ask"], first["ticks"]),
                         (1.1000, 1.1002, 1.1020, 1.1021, 3))
        self.assertAlmostEqual(first["median_spread_pips"], 2.0, places=6)

    def test_o2_hourly_rows_are_stored_once_and_bad_ones_refused(self):
        store = TickStore(self.dir / "ticks.sqlite3", self.dir / "archive")
        store.initialize()
        rows = synthetic_days(1)
        body = {"server": "MetaQuotes-Demo", "rows": rows}
        self.assertEqual(store.record_hours(body)["stored"], 24)
        self.assertEqual(store.record_hours(body)["stored"], 0)
        self.assertEqual(len(store.hours("EURUSD")), 24)
        bad = dict(rows[0], first_ask=rows[0]["first_bid"] - 0.001)
        with self.assertRaises(TickError):
            store.record_hours({"server": "MetaQuotes-Demo", "rows": [bad]})

    def test_o8_the_bridge_backfills_the_terminal_history_one_symbol_day_per_round(self):
        fake = TickMt5()
        fake.copy_ticks_range = lambda symbol, start, end, flags: (
            [{"time_msc": ms("2026-11-13 12:00:00"), "bid": 1.1, "ask": 1.1002, "flags": 6},
             {"time_msc": ms("2026-11-13 12:30:00"), "bid": 1.1004, "ask": 1.1005, "flags": 6}] if symbol == "EURUSD" else [])
        server = FakeServer()
        bridge = Bridge(AstraClient(server), Mt5DemoTrader(module=fake, expected_login=LOGIN), self.dir,
                        clock=lambda: datetime(2026, 11, 16, 9, tzinfo=UTC))
        (self.dir / "BACKFILL_HOURS").write_text("3", encoding="utf-8")
        with bridge.trader:
            for _ in range(60):
                bridge.backfill_hours_step()
        posted = [b for p, b in server.posts if p == "/api/mt5/hours"]
        self.assertTrue(any(r["symbol"] == "EURUSD" for b in posted for r in b["rows"]))
        state = json.loads((self.dir / "bridge-state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["hours_backfill"]["done"])
        self.assertFalse((self.dir / "BACKFILL_HOURS").exists())


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.reg = EvidenceRegistry(self.db)
        self.reg.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_o3_the_rollover_hour_follows_the_server_calendar(self):
        self.assertEqual(rollover.rollover_hour_utc(datetime(2026, 1, 14, tzinfo=UTC)), 22)  # winter, server UTC+2
        self.assertEqual(rollover.rollover_hour_utc(datetime(2026, 7, 14, tzinfo=UTC)), 21)  # summer, server UTC+3

    def test_o4_returns_are_measured_at_executable_prices(self):
        row = hour_row("EURUSD", "2026-01-05T10:00:00+00:00", 1.1000, 1.1002, 1.1000, 1.1002)
        self.assertLess(rollover.position_return(row, 1), 0)   # flat market: a long pays the spread
        self.assertLess(rollover.position_return(row, -1), 0)  # and so does a short
        self.assertEqual(rollover.position_return(row, 0), 0.0)

    def test_o5_o6_a_pre_registered_experiment_gives_a_replayable_verdict(self):
        rows = synthetic_days(60, drift_at={21: 3.0, 22: 3.0})  # a strong effect around the rollover
        report = rollover.run_experiment(self.reg, rows, NOW, label="test")
        self.assertIn(report["verdict"], ("PISTE_OUVERTE", "PISTE_CLOSE"))
        self.assertEqual(report["verdict"], "PISTE_OUVERTE")
        self.assertTrue(report["provisional"])  # under six months of ticks
        self.assertLessEqual(report["study_period"][1], report["sealed_period"][0])  # chronological, no overlap
        self.assertTrue(rollover.replay(self.reg, rows, report["final_run_id"])["identical"])
        with self.assertRaises(EvidenceError):  # the sealed set is used once
            rollover.run_final(self.reg, report["hypothesis_id"], rows, NOW)

    def test_o6_without_an_effect_the_track_is_closed(self):
        report = rollover.run_experiment(self.reg, synthetic_days(60), NOW, label="test")
        self.assertEqual(report["verdict"], "PISTE_CLOSE")
        self.assertEqual(self.reg.hypothesis(report["hypothesis_id"])["status"], "REJETEE")

    def test_o7_the_six_month_reevaluation_is_scheduled_once(self):
        short = synthetic_days(60)
        self.assertIsNone(rollover.maybe_reevaluate(self.reg, short, NOW))
        long = synthetic_days(135)  # about six months of weekdays
        first = rollover.maybe_reevaluate(self.reg, long, NOW)
        self.assertIsNotNone(first)
        self.assertFalse(first["provisional"])
        self.assertIsNone(rollover.maybe_reevaluate(self.reg, long, NOW))


if __name__ == "__main__":
    unittest.main()
