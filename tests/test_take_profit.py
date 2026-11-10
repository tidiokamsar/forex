"""Profit target in the paper engine: set at entry, hit inside a candle, never ahead of the stop, and learned as a parameter."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from astra import learning
from astra.marketdata import KIND_OHLC, Candle
from astra.papertrade import CostParams, Engine, Recorder, RiskParams, SimulationError, StrategyParams

T0 = datetime(2026, 1, 5, 10, tzinfo=UTC)


def candle(i, o, h, low, c):
    return Candle("EURUSD", "H1", T0 + timedelta(hours=i), o, h, low, c, None, "TEST", KIND_OHLC)


def engine(take_profit_r):
    risk = replace(RiskParams(), take_profit_r=take_profit_r)
    return Engine(StrategyParams(), risk, CostParams(spread_pips=0.0, slippage_pips=0.0))


def with_long(take_profit_r):
    eng = engine(take_profit_r)
    rec = Recorder()
    eng._open(rec, T0, "EURUSD", "LONG", 1.10, 0.005)  # stop 1.09 (2 ATR), target 1.12 at 2 R
    return eng, rec


class TargetTests(unittest.TestCase):
    def test_the_target_is_set_at_entry_in_multiples_of_the_stop_distance(self):
        eng, _ = with_long(2.0)
        position = eng.state["positions"]["EURUSD"]
        self.assertAlmostEqual(position["stop"], 1.09, places=6)
        self.assertAlmostEqual(position["target"], 1.12, places=6)
        short = engine(1.5)
        short._open(Recorder(), T0, "EURUSD", "SHORT", 1.10, 0.005)
        self.assertAlmostEqual(short.state["positions"]["EURUSD"]["target"], 1.085, places=6)
        self.assertIsNone(with_long(0.0)[0].state["positions"]["EURUSD"]["target"])

    def test_a_candle_that_reaches_the_target_closes_the_trade_at_the_target(self):
        eng, rec = with_long(2.0)
        eng.process(candle(1, 1.105, 1.121, 1.102, 1.118), [candle(1, 1.105, 1.121, 1.102, 1.118)], rec)
        self.assertEqual(rec.trades[-1]["exit_reason"], "objectif de profit")
        self.assertAlmostEqual(rec.trades[-1]["exit_price"], 1.12, places=6)
        self.assertGreater(rec.trades[-1]["net_pnl"], 0)
        self.assertNotIn("EURUSD", eng.state["positions"])

    def test_a_candle_that_touches_both_counts_as_a_stop_loss(self):
        eng, rec = with_long(2.0)
        eng.process(candle(1, 1.10, 1.125, 1.085, 1.10), [candle(1, 1.10, 1.125, 1.085, 1.10)], rec)
        self.assertEqual(rec.trades[-1]["exit_reason"], "stop-loss")
        self.assertLess(rec.trades[-1]["net_pnl"], 0)

    def test_a_gap_above_the_target_fills_at_the_open(self):
        eng, rec = with_long(2.0)
        eng.process(candle(1, 1.13, 1.135, 1.128, 1.131), [candle(1, 1.13, 1.135, 1.128, 1.131)], rec)
        self.assertAlmostEqual(rec.trades[-1]["exit_price"], 1.13, places=6)

    def test_no_target_means_the_position_stays_open_through_a_rally(self):
        eng, rec = with_long(0.0)
        eng.process(candle(1, 1.105, 1.15, 1.102, 1.14), [candle(1, 1.105, 1.15, 1.102, 1.14)], rec)
        self.assertEqual(rec.trades, [])
        self.assertIn("EURUSD", eng.state["positions"])

    def test_a_position_saved_before_targets_existed_still_works(self):
        eng, rec = with_long(0.0)
        eng.state["positions"]["EURUSD"].pop("target")
        eng.process(candle(1, 1.105, 1.15, 1.102, 1.14), [candle(1, 1.105, 1.15, 1.102, 1.14)], rec)
        self.assertEqual(rec.trades, [])

    def test_the_target_is_validated(self):
        with self.assertRaises(SimulationError):
            replace(RiskParams(), take_profit_r=11.0).validate()
        replace(RiskParams(), take_profit_r=2.5).validate()


class LearnedParameterTests(unittest.TestCase):
    def test_the_reference_has_no_target_and_candidates_try_the_target_values(self):
        self.assertEqual(learning.default_params()["take_profit_r"], 0.0)
        pool = learning.candidates(3, learning.default_params(), count=120)
        self.assertEqual({c["take_profit_r"] for c in pool}, set(learning.SPACE["take_profit_r"]))
        legacy = {"strategy": learning.default_params()["strategy"], "stop_atr_multiple": 2.0}  # saved before targets existed
        self.assertNotIn({**legacy, "take_profit_r": 0.0}, learning.candidates(3, legacy, count=60))

    def test_a_champion_without_a_target_is_requested_without_one(self):
        request = learning._request("EURUSD", {"strategy": learning.default_params()["strategy"], "stop_atr_multiple": 2.0},
                                    T0, T0 + timedelta(days=30))
        self.assertEqual(request["risk"]["take_profit_r"], 0.0)


if __name__ == "__main__":
    unittest.main()
