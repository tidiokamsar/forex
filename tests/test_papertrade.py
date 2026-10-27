"""Simulation engine: decisions, next-open fills, costs, conversion, stops, risk limits, determinism, restart."""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra.marketdata import Candle, CandleStore
from astra.papertrade import (
    BUY,
    FLAT,
    SELL,
    CostParams,
    Engine,
    LabStore,
    Recorder,
    RiskParams,
    SimulationError,
    StrategyParams,
    backtest_request,
    decide,
    run_backtest,
    simulation_request,
)

T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
FAST = StrategyParams(fast=2, slow=3, atr_window=2, min_atr_pct=0.0, max_atr_pct=0.5)


def candle(i, close, symbol="EURUSD", spread=0.001, open_=None, high=None, low=None, step=timedelta(hours=1)):
    open_ = close if open_ is None else open_
    return Candle(symbol, "H1", T0 + i * step, open_, high if high is not None else max(open_, close) + spread,
                  low if low is not None else min(open_, close) - spread, close, None, "test")


def run(engine, candles):
    rec, history = Recorder(), []
    for c in candles:
        history.append(c)
        engine.process(c, history, rec)
    return rec


class StrategyTests(unittest.TestCase):
    def test_warmup_then_buy_sell_flat_with_indicators(self):
        rising = [candle(i, 1.10 + i * 0.002) for i in range(5)]
        self.assertIsNone(decide(rising[:2], FAST))
        decision = decide(rising, FAST)
        self.assertEqual(decision.action, BUY)
        closes = [c.close for c in rising]
        self.assertAlmostEqual(decision.indicators["sma_rapide"], sum(closes[-2:]) / 2, places=6)
        self.assertAlmostEqual(decision.indicators["sma_lente"], sum(closes[-3:]) / 3, places=6)
        self.assertTrue(decision.conditions["moyenne_rapide_au_dessus"])
        falling = [candle(i, 1.20 - i * 0.002) for i in range(5)]
        self.assertEqual(decide(falling, FAST).action, SELL)
        quiet = StrategyParams(fast=2, slow=3, atr_window=2, min_atr_pct=0.05, max_atr_pct=0.5)
        flat = decide(rising, quiet)
        self.assertEqual(flat.action, FLAT)
        self.assertIn("trop faible", flat.reason)

    def test_invalid_parameters_are_refused(self):
        for raw in ({"fast": 30, "slow": 20}, {"atr_window": 1}, {"min_atr_pct": 0.1, "max_atr_pct": 0.05}):
            with self.assertRaises(SimulationError):
                backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "s", "strategy_params": raw})
        for raw in ({"risk_per_trade_pct": 5}, {"max_positions": 0}, {"initial_capital": -1}, {"unknown": 1}):
            with self.assertRaises(SimulationError):
                backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "s", "risk": raw})


class EngineTests(unittest.TestCase):
    def engine(self, risk=None, costs=None):
        return Engine(FAST, risk or RiskParams(), costs or CostParams(spread_pips=0, slippage_pips=0))

    def test_decision_at_close_fills_at_next_open(self):
        candles = [candle(i, 1.10 + i * 0.002) for i in range(4)]
        candles.append(candle(4, 1.12, open_=1.1111))
        rec = run(self.engine(), candles)
        entry = next(o for o in rec.orders if o["kind"] == "ENTREE")
        self.assertEqual(entry["ts"], candles[3].timestamp.isoformat())  # signal at close of candle 2, fill at open of 3
        self.assertEqual(entry["reference_price"], candles[3].open)
        self.assertEqual(rec.signals[0]["ts"], candles[2].timestamp.isoformat())

    def test_costs_are_explicit_and_net_is_gross_minus_costs(self):
        costs = CostParams(spread_pips=2, slippage_pips=0.5, commission_per_100k=5)
        engine = self.engine(costs=costs)
        engine.state["pending"]["EURUSD"] = {"side": "LONG", "label": BUY}
        engine.state["pending_atr"] = {"EURUSD": 0.0050}
        rec = Recorder()
        first = candle(0, 1.1000, spread=0.0001)
        engine.process(first, [first], rec)
        engine.close_all(rec, first.timestamp, "test")
        trade = rec.trades[0]
        units = trade["units"]
        expected_costs = units * 2 * (1 + 0.5) * 0.0001 + 2 * units / 100_000 * 5
        self.assertAlmostEqual(trade["gross_pnl"], 0.0, places=6)
        self.assertAlmostEqual(trade["costs"], expected_costs, places=3)
        self.assertAlmostEqual(trade["net_pnl"], -expected_costs, places=3)

    def test_usd_base_pairs_are_converted_to_usd(self):
        engine = self.engine()
        engine.state["pending"]["USDJPY"] = {"side": "LONG", "label": BUY}
        engine.state["pending_atr"] = {"USDJPY": 0.50}
        rec = Recorder()
        first = candle(0, 150.00, symbol="USDJPY", spread=0.01)
        engine.process(first, [first], rec)
        engine.state["last_close"]["USDJPY"] = 151.00
        engine.close_all(rec, first.timestamp, "test")
        trade = rec.trades[0]
        self.assertAlmostEqual(trade["net_pnl"], trade["units"] * 1.00 / 151.00, places=3)  # 1 JPY per unit, in USD
        risk_usd = 10_000 * 0.005
        self.assertLessEqual(trade["units"] * 1.0 / 150.0, risk_usd * 1.01)  # sized on a 1-yen (2 ATR) stop

    def test_stop_loss_inside_the_candle_and_gap_through_the_stop(self):
        for open_, low, expected_exit in ((1.1000, 1.0890, None), (1.0850, 1.0840, 1.0850)):
            engine = self.engine()
            engine.state["pending"]["EURUSD"] = {"side": "LONG", "label": BUY}
            engine.state["pending_atr"] = {"EURUSD": 0.0050}  # stop 2 ATR = 0.0100 below the entry
            rec = Recorder()
            first = candle(0, 1.1000, spread=0.0001)
            engine.process(first, [first], rec)
            stop = engine.state["positions"]["EURUSD"]["stop"]
            second = candle(1, 1.0950, open_=open_, low=low, high=max(open_, 1.0950) + 0.0001)
            engine.process(second, [first, second], rec)
            trade = rec.trades[0]
            self.assertEqual(trade["exit_reason"], "stop-loss")
            self.assertAlmostEqual(trade["exit_price"], expected_exit or stop, places=6)
            self.assertNotIn("EURUSD", engine.state["positions"])

    def test_position_limit_size_cap_and_minimum(self):
        engine = self.engine(risk=RiskParams(max_positions=1, max_position_units=5000))
        engine.state["pending"].update({"EURUSD": {"side": "LONG", "label": BUY}, "GBPUSD": {"side": "SHORT", "label": SELL}})
        engine.state["pending_atr"] = {"EURUSD": 0.0005, "GBPUSD": 0.0005}
        rec = Recorder()
        eur, gbp = candle(0, 1.10), candle(0, 1.30, symbol="GBPUSD")
        engine.process(eur, [eur], rec)
        engine.process(gbp, [gbp], rec)
        filled = [o for o in rec.orders if o["status"] == "EXECUTE"]
        refused = [o for o in rec.orders if o["status"] == "REFUSE"]
        self.assertEqual(filled[0]["units"], 5000)  # capped by the maximum position size
        self.assertIn("plafonnée", filled[0]["reason"])
        self.assertIn("positions simultanées", refused[0]["reason"])
        tiny = self.engine(risk=RiskParams(initial_capital=100, risk_per_trade_pct=0.01))
        tiny.state["pending"]["EURUSD"] = {"side": "LONG", "label": BUY}
        tiny.state["pending_atr"] = {"EURUSD": 0.0050}
        rec = Recorder()
        tiny.process(eur, [eur], rec)
        self.assertIn("minimum", rec.orders[0]["reason"])
        self.assertEqual(rec.trades, [])  # a refused order is never a trade

    def test_daily_loss_then_drawdown_block_new_entries(self):
        engine = self.engine(risk=RiskParams(risk_per_trade_pct=2.0, daily_loss_limit_pct=1.0, max_drawdown_pct=3.0))
        rec = Recorder()

        def losing_trade(day):
            engine.state["pending"]["EURUSD"] = {"side": "LONG", "label": BUY}
            engine.state["pending_atr"] = {"EURUSD": 0.0050}
            first = candle(day * 24, 1.1000, spread=0.0001)
            engine.process(first, [first], rec)
            crash = candle(day * 24 + 1, 1.0800, open_=1.0990, low=1.0790, high=1.0991)
            engine.process(crash, [first, crash], rec)

        losing_trade(0)
        self.assertTrue(engine.state["daily_halt"])
        engine.state["pending"]["EURUSD"] = {"side": "LONG", "label": BUY}
        engine.state["pending_atr"] = {"EURUSD": 0.0050}
        same_day = candle(2, 1.08)
        engine.process(same_day, [same_day], rec)
        self.assertIn("perte journalière", rec.orders[-1]["reason"])
        losing_trade(1)   # next day: allowed again, loses again
        losing_trade(2)
        self.assertTrue(engine.state["drawdown_halt"])
        engine.state["pending"]["EURUSD"] = {"side": "LONG", "label": BUY}
        engine.state["pending_atr"] = {"EURUSD": 0.0050}
        later = candle(24 * 5, 1.08)
        engine.process(later, [later], rec)
        self.assertIn("drawdown maximal", rec.orders[-1]["reason"])
        kinds = [event["kind"] for event in rec.events]
        self.assertIn("LIMITE_JOURNALIERE", kinds)
        self.assertIn("ARRET_DRAWDOWN", kinds)


class StoredRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.candles = CandleStore(self.db)
        self.candles.initialize()
        self.lab = LabStore(self.db)
        self.lab.initialize()
        series = [1.10 + 0.01 * (((i // 20) % 2 and (20 - i % 20)) or i % 20) / 20 for i in range(300)]
        self.candles.store([candle(i, value) for i, value in enumerate(series)], "HISTORICAL", None)
        self.request = backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "test",
                                         "strategy_params": {"fast": 3, "slow": 8, "atr_window": 3, "min_atr_pct": 0}})

    def tearDown(self):
        self.temp.cleanup()

    def test_backtest_is_deterministic_and_reproducible(self):
        first, second = run_backtest(self.candles, self.request), run_backtest(self.candles, self.request)
        self.assertEqual(first, second)
        self.assertGreater(first["metrics"]["trade_count"], 0)
        metrics = first["metrics"]
        self.assertAlmostEqual(metrics["net_pnl"], metrics["final_capital"] - metrics["initial_capital"], places=2)
        self.assertAlmostEqual(metrics["gross_pnl"] - metrics["total_costs"], metrics["net_pnl"], delta=0.05)
        self.assertEqual(len(first["data"]["fingerprint_sha256"]), 64)
        backtest_id = self.lab.create_backtest(self.request, "test")
        self.lab.save_backtest_result(backtest_id, first)
        stored = self.lab.backtest(backtest_id)
        self.assertEqual((stored["status"], stored["metrics"], stored["params"]), ("DONE", metrics, self.request))
        self.assertEqual(stored["trades_total"], metrics["trade_count"])

    def test_too_little_data_is_refused(self):
        request = dict(self.request, start="2030-01-01T00:00:00+00:00")
        with self.assertRaises(SimulationError):
            run_backtest(self.candles, request)

    def test_simulation_survives_restart_without_duplicates(self):
        request = simulation_request({"symbols": ["EURUSD"], "timeframe": "H1", "source": "test",
                                      "start": T0.isoformat(), "strategy_params": {"fast": 3, "slow": 8, "atr_window": 3,
                                                                                   "min_atr_pct": 0}}, self.candles)
        session_id = self.lab.create_session(request, "test")
        first = self.lab.step_session(session_id, self.candles)
        self.assertEqual(first["processed"], 300)
        before = self.lab.session(session_id)
        restarted = LabStore(self.db)  # a new process: everything comes back from SQLite
        self.assertEqual(restarted.step_session(session_id, self.candles)["processed"], 0)
        after = restarted.session(session_id)
        self.assertEqual((after["orders_total"], after["trades_total"], after["account"]),
                         (before["orders_total"], before["trades_total"], before["account"]))
        self.candles.store([candle(300, 1.12)], "HISTORICAL", None)
        self.assertEqual(restarted.step_session(session_id, self.candles)["processed"], 1)
        self.assertEqual(restarted.session(session_id)["last_processed"]["EURUSD"], (T0 + timedelta(hours=300)).isoformat())
        backtest = run_backtest(self.candles, backtest_request({**self.request, "start": T0.isoformat()}))
        sim_trades = restarted.session(session_id, item_limit=10_000)["trades"]
        self.assertEqual(len(sim_trades), backtest["metrics"]["trade_count"] - int(bool(
            [t for t in backtest["trades"] if t["exit_reason"] == "fin_de_periode"])))

    def test_stopped_simulation_does_not_advance(self):
        request = simulation_request({"symbols": "EURUSD", "timeframe": "H1", "source": "test", "start": T0.isoformat()},
                                     self.candles)
        session_id = self.lab.create_session(request, "test")
        self.lab.set_session_status(session_id, "STOPPED", "test")
        self.assertEqual(self.lab.step_session(session_id, self.candles)["processed"], 0)
        self.assertEqual(self.lab.session(session_id)["status"], "STOPPED")
        with self.assertRaises(SimulationError):
            simulation_request({"symbols": ["USDJPY"], "timeframe": "H1", "source": "test"}, self.candles)


if __name__ == "__main__":
    unittest.main()
