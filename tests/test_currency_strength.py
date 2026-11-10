"""Relative strength of currencies: the maths, the direction of each pair, and the refusal to decide on missing data."""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra import learning
from astra.marketdata import KIND_REFERENCE, PAIRS, Candle, CandleStore
from astra.papertrade import BUY, FLAT, SELL, PeerPrices, StrategyParams, _build, currency_strengths, decide, run_backtest
from test_learning import daily_series

START = datetime(2026, 1, 5, tzinfo=UTC)


def stamps(count):
    return [START + timedelta(hours=i) for i in range(count)]


def candles(symbol, closes):
    return [Candle(symbol, "H1", t, None, None, None, c, None, "TEST", KIND_REFERENCE) for t, c in zip(stamps(len(closes)), closes, strict=True)]


def flat(count, value=1.0):
    return [value] * count


def peers_with(moves, count=8):
    """Every pair flat except those in ``moves`` ({symbol: final close}), which stay flat then jump on the last bar."""

    closes = {}
    for symbol in PAIRS:
        series = flat(count)
        if symbol in moves:
            series[-1] = moves[symbol]
        closes[symbol] = dict(zip(stamps(count), series, strict=True))
    return PeerPrices(closes)


def params(**kw):
    return _build(StrategyParams, {"fast": 3, "slow": 6, "atr_window": 3, "min_atr_pct": 0.0, "z_entry": 1.0, **kw})


def history(symbol, last, count=8):
    """A pair that was flat and ends on ``last``: its own ATR is made of that one move."""

    return candles(symbol, [*flat(count - 1), last])


class StrengthMathTests(unittest.TestCase):
    def test_strengths_are_each_currency_against_the_average_of_the_eight(self):
        peers = peers_with({"EURUSD": 1.03})
        result = currency_strengths(peers, stamps(8)[4], stamps(8)[7])
        move = 0.0295588  # ln(1.03)
        self.assertAlmostEqual(result["EUR"], move * 7 / 8, places=5)
        self.assertAlmostEqual(result["GBP"], -move / 8, places=5)
        self.assertAlmostEqual(sum(result.values()) - result["EUR"], -move * 7 / 8, places=5)  # the dollar's share is the rest

    def test_a_pair_with_the_dollar_first_gives_the_other_currency_the_opposite_move(self):
        result = currency_strengths(peers_with({"USDJPY": 1.03}), stamps(8)[4], stamps(8)[7])
        self.assertLess(result["JPY"], 0)  # USDJPY up means the yen is weaker

    def test_fewer_than_six_pairs_give_no_strengths(self):
        peers = peers_with({})
        for symbol in list(PAIRS)[:2]:
            peers.closes[symbol] = {}
        self.assertIsNone(currency_strengths(peers, stamps(8)[4], stamps(8)[7]))


class DecisionTests(unittest.TestCase):
    def test_momentum_follows_the_strong_currency_and_reversion_fades_it(self):
        peers = peers_with({"EURUSD": 1.03})
        data = history("EURUSD", 1.03)
        self.assertEqual(decide(data, params(family="currency_momentum"), peers).action, BUY)
        self.assertEqual(decide(data, params(family="currency_reversion"), peers).action, SELL)

    def test_a_pair_with_the_dollar_first_is_bought_when_the_other_currency_is_weak(self):
        peers = peers_with({"USDJPY": 1.03})  # the yen weakens
        data = history("USDJPY", 1.03)
        self.assertEqual(decide(data, params(family="currency_momentum"), peers).action, BUY)
        self.assertEqual(decide(data, params(family="currency_reversion"), peers).action, SELL)

    def test_a_move_inside_the_threshold_gives_no_position(self):
        peers = peers_with({"EURUSD": 1.03})
        data = history("EURUSD", 1.03)
        decision = decide(data, params(family="currency_momentum", z_entry=4.0), peers)
        self.assertEqual(decision.action, FLAT)
        self.assertIn("écart insuffisant", decision.reason)

    def test_missing_peers_or_history_give_no_decision_at_all(self):
        data = history("EURUSD", 1.03)
        self.assertIsNone(decide(data, params(family="currency_momentum"), None))
        broken = peers_with({"EURUSD": 1.03})
        for symbol in list(PAIRS)[1:4]:
            broken.closes[symbol] = {}
        self.assertIsNone(decide(data, params(family="currency_momentum"), broken))
        self.assertIsNone(decide(data[:3], params(family="currency_momentum", fast=5, slow=6), peers_with({})))


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.store = CandleStore(Path(self.temp.name) / "astra.sqlite3")
        self.store.initialize()
        self.store.register_source("ECB_EXR", "ECB_SDMX_API", "REFERENCE", "test")

    def tearDown(self):
        self.temp.cleanup()

    def request(self, family):
        strategy = {"fast": 5, "slow": 20, "atr_window": 5, "min_atr_pct": 0.0, "max_atr_pct": 0.03, "family": family}
        return learning._request("EURUSD", {"strategy": strategy, "stop_atr_multiple": 2.0}, datetime(2022, 1, 3, tzinfo=UTC),
                                 datetime(2023, 12, 29, tzinfo=UTC))

    def test_a_backtest_reads_the_other_pairs_and_says_so(self):
        for symbol in PAIRS:
            self.store.store(daily_series(symbol, years=4, start=datetime(2020, 1, 1, 13, 15, tzinfo=UTC)), "REFERENCE", None)
        result = run_backtest(self.store, self.request("currency_momentum"))
        self.assertTrue(result["signals"])
        self.assertTrue(any("compare les devises" in a for a in result["assumptions"]))

    def test_without_the_other_pairs_a_currency_strategy_takes_no_decision(self):
        self.store.store(daily_series("EURUSD", years=4, start=datetime(2020, 1, 1, 13, 15, tzinfo=UTC)), "REFERENCE", None)
        result = run_backtest(self.store, self.request("currency_momentum"))
        self.assertEqual((result["signals"], result["metrics"]["trade_count"]), ([], 0))


if __name__ == "__main__":
    unittest.main()
