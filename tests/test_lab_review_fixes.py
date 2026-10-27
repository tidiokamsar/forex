"""Regression tests for the independent review of the data, backtest and simulation lot (M1 to M5, lows)."""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from _support import workspace_tempdir
from astra import marketdata
from astra.jobs import Worker
from astra.marketdata import CandleStore, MarketDataError, find_gaps, parse_csv
from astra.papertrade import (
    BUY,
    CostParams,
    Engine,
    LabStore,
    Recorder,
    RiskParams,
    StrategyParams,
    backtest_request,
    run_backtest,
    simulation_request,
)
from test_papertrade import T0, candle


def rows(stamps, header="timestamp,open,high,low,close"):
    return ("\n".join([header, *[f"{s.isoformat()},1.1,1.2,1.0,1.1" for s in stamps]]) + "\n").encode()


class ImportReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.store = CandleStore(Path(self.temp.name) / "astra.sqlite3")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_m2_a_file_in_another_timeframe_is_refused(self):
        hourly = [T0 + timedelta(hours=i) for i in range(30)]
        for wrong in ("H4", "D1"):
            with self.assertRaisesRegex(MarketDataError, "pas en"):
                parse_csv(rows(hourly), "EURUSD", wrong, "p")
        candles, _ = parse_csv(rows(hourly), "EURUSD", "H1", "p")
        self.assertEqual(len(candles), 30)

    def test_m2_h4_phase_and_one_daily_bar_per_trading_day(self):
        h4 = [T0 + timedelta(hours=4 * i) for i in range(10)] + [T0 + timedelta(hours=41)]  # 17:00, other phase
        candles, report = parse_csv(rows(h4), "EURUSD", "H4", "p")
        self.assertEqual((len(candles), report.rejected), (10, 1))
        daily = [T0 + timedelta(days=i) for i in range(5)] + [T0 + timedelta(days=2, hours=3)]
        candles, report = parse_csv(rows(daily), "EURUSD", "D1", "p")
        self.assertEqual((len(candles), report.rejected), (5, 1))
        self.assertIn("DAILY_DUPLICATE_DATE", {issue["code"] for issue in report.issues})

    def test_m4_new_york_close_daily_bars_have_no_weekend_gap(self):
        # 22:00 UTC (17:00 New York) daily bars: Sunday evening opens Monday's session.
        bars = [datetime(2026, 1, 11, 22, tzinfo=UTC) + timedelta(days=i) for i in range(5)]  # Sun..Thu evenings
        bars.append(datetime(2026, 1, 18, 22, tzinfo=UTC))  # next Sunday evening
        self.assertEqual(find_gaps(bars, "D1"), [])
        bars.append(datetime(2026, 1, 20, 22, tzinfo=UTC))  # Tuesday evening: Tuesday session missing
        self.assertEqual(find_gaps(bars, "D1")[0]["missing"], 1)

    def test_m5_reserved_ecb_name_and_one_nature_per_source(self):
        good = rows([T0 + timedelta(hours=i) for i in range(5)])
        with self.assertRaisesRegex(MarketDataError, "réservé"):
            self.store.import_csv(good, "EURUSD", "H1", "ecb_exr")
        self.store.import_csv(good, "EURUSD", "H1", "mixed", nature="SYNTHETIC")
        with self.assertRaisesRegex(MarketDataError, "SYNTHETIC"):
            self.store.import_csv(good, "EURUSD", "H1", "mixed", nature="HISTORICAL")

    def test_m5_synthetic_data_is_named_in_the_backtest(self):
        synthetic = ("timestamp,open,high,low,close\n" + "\n".join(
            f"{(T0 + timedelta(hours=i)).isoformat()},{1.1 + i * 1e-4:.5f},{1.1 + i * 1e-4 + 0.002:.5f},"
            f"{1.1 + i * 1e-4 - 0.002:.5f},{1.1 + i * 1e-4:.5f}" for i in range(80)) + "\n").encode()
        self.store.import_csv(synthetic, "EURUSD", "H1", "fake", nature="SYNTHETIC")
        result = run_backtest(self.store, backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "fake"}))
        self.assertEqual(result["data"]["nature"], "SYNTHETIC")
        self.assertIn("SYNTHÉTIQUES", result["assumptions"][0])

    def test_l7_forced_ecb_requests_are_spaced(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return b"KEY,CURRENCY,TIME_PERIOD,OBS_VALUE\nx,USD,2026-01-15,1.1\n"

        marketdata.ingest_ecb(self.store, fetch=fetch)
        self.assertEqual(marketdata.ingest_ecb(self.store, fetch=fetch, force=True)["status"], "SKIPPED")
        self.assertEqual(len(calls), 1)

    def test_l12_date_only_end_includes_the_day(self):
        request = backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "s", "end": "2026-01-05"})
        self.assertTrue(request["end"].startswith("2026-01-05T23:59:59"))


class EngineReviewTests(unittest.TestCase):
    def test_m3_the_account_day_never_goes_back(self):
        engine = Engine(StrategyParams(), RiskParams(), CostParams())
        rec = Recorder()
        tuesday = candle(24, 1.1)
        engine.process(tuesday, [tuesday], rec)
        engine.state["daily_halt"] = True
        monday = candle(1, 1.1)
        engine.process(monday, [monday], rec)
        self.assertTrue(engine.state["daily_halt"])  # an older candle does not start a new day
        self.assertEqual(engine.state["day"], tuesday.timestamp.date().isoformat())

    def test_l9_a_refusal_is_recorded_once_per_reason(self):
        engine = Engine(StrategyParams(), RiskParams(), CostParams())
        engine.state["drawdown_halt"] = True
        rec = Recorder()
        for i in range(5):
            engine.state["pending"]["EURUSD"] = {"side": "LONG", "label": BUY}
            engine.state["pending_atr"] = {"EURUSD": 0.005}
            c = candle(i, 1.1)
            engine.process(c, [c], rec)
        self.assertEqual(len([o for o in rec.orders if o["status"] == "REFUSE"]), 1)


class StepAndWorkerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        root = Path(self.temp.name)
        self.db = root / "astra.sqlite3"
        self.imports = root / "imports"
        self.imports.mkdir()
        self.candles = CandleStore(self.db)
        self.candles.initialize()
        self.lab = LabStore(self.db)
        self.lab.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_m3_symbols_advance_together_up_to_a_common_watermark(self):
        self.candles.store([candle(i, 1.1 + i * 1e-4) for i in range(60)], "HISTORICAL", None)
        self.candles.store([candle(i, 1.3, symbol="GBPUSD") for i in range(40)], "HISTORICAL", None)
        request = simulation_request({"symbols": ["EURUSD", "GBPUSD"], "timeframe": "H1", "source": "test",
                                      "start": T0.isoformat()}, self.candles)
        session_id = self.lab.create_session(request, "test")
        self.lab.step_session(session_id, self.candles)
        last = self.lab.session(session_id)["last_processed"]
        self.assertEqual(last["EURUSD"], last["GBPUSD"])  # EURUSD waits for GBPUSD at candle 39
        self.candles.store([candle(i, 1.3, symbol="GBPUSD") for i in range(40, 60)], "HISTORICAL", None)
        self.assertEqual(self.lab.step_session(session_id, self.candles)["processed"], 40)

    def test_m1_a_backtest_never_stays_running(self):
        worker = Worker(self.db, self.imports, ecb_enabled=False)
        worker.initialize()
        self.candles.store([candle(i, 1.1 + i * 1e-4) for i in range(80)], "HISTORICAL", None)
        backtest_id = self.lab.create_backtest(backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "test"}), "t")
        worker.jobs.enqueue("backtest", {"backtest_id": backtest_id}, "t")
        with mock.patch("astra.jobs.run_backtest", side_effect=OverflowError("boom")):
            worker.tick()
        self.assertEqual(self.lab.backtest(backtest_id)["status"], "FAILED")
        second = self.lab.create_backtest(backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "test"}), "t")
        self.lab.set_backtest_status(second, "RUNNING")  # the worker dies during this backtest
        Worker(self.db, self.imports, ecb_enabled=False).initialize()
        self.assertEqual(self.lab.backtest(second)["status"], "FAILED")
        self.assertIn("redémarrage", self.lab.backtest(second)["error"])

    def test_l2_a_path_outside_the_import_directory_is_not_touched(self):
        worker = Worker(self.db, self.imports, ecb_enabled=False)
        worker.initialize()
        outside = Path(self.temp.name) / "keep.csv"
        outside.write_text("x", encoding="utf-8")
        worker.jobs.enqueue("import_csv", {"path": str(outside), "symbol": "EURUSD", "timeframe": "H1", "source": "x"}, "t")
        worker.tick()
        self.assertTrue(outside.exists())
        self.assertEqual(worker.jobs.jobs()[0]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
