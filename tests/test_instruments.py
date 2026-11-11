"""Extended instruments (crosses, gold, silver): registry, USD conversion, units, costs, and the tradable set staying at seven."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra import learning, massive, mt5_risk, mt5view
from astra.marketdata import (
    INSTRUMENTS,
    KIND_REFERENCE,
    PAIRS,
    Candle,
    CandleStore,
    MarketDataError,
    canonical_symbol,
    pip_size,
    price_decimals,
)
from astra.papertrade import (
    CROSS_FAMILIES,
    CostParams,
    Engine,
    PeerPrices,
    Recorder,
    RiskParams,
    StrategyParams,
    _build,
    decide,
    run_backtest,
    uses_peers,
)
from test_learning import daily_series
from test_mt5_risk import ACCOUNT, LIMITS, ORDER

T0 = datetime(2026, 1, 5, 10, tzinfo=UTC)
CROSSES = {"EURGBP", "EURCHF", "EURAUD", "EURCAD", "AUDCAD", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"}


class RegistryTests(unittest.TestCase):
    def test_the_seven_tradable_pairs_keep_their_specs_and_the_extension_is_research_only(self):
        for symbol, (base, quote, pip, decimals) in PAIRS.items():
            spec = INSTRUMENTS[symbol]
            self.assertEqual((spec.base, spec.quote, spec.pip, spec.decimals, spec.kind), (base, quote, pip, decimals, "forex"))
            self.assertEqual((spec.min_units, spec.unit_step, spec.spread_pips, spec.slippage_pips), (1000.0, 1000.0, 1.0, 0.2))
        self.assertEqual(set(INSTRUMENTS) - set(PAIRS), CROSSES | {"XAUUSD", "XAGUSD"})
        self.assertEqual(len(PAIRS), 7)

    def test_symbols_pips_and_decimals(self):
        self.assertEqual(canonical_symbol("eur/gbp"), "EURGBP")
        self.assertEqual(canonical_symbol("XAU-USD"), "XAUUSD")
        with self.assertRaises(MarketDataError):
            canonical_symbol("FOOBAR")
        self.assertEqual([pip_size(s) for s in ("GBPJPY", "EURGBP", "XAUUSD", "XAGUSD")], [0.01, 0.0001, 0.01, 0.001])
        self.assertEqual([price_decimals(s) for s in ("GBPJPY", "EURGBP", "XAUUSD", "XAGUSD")], [3, 5, 2, 3])

    def test_massive_has_a_ticker_for_every_instrument(self):
        self.assertEqual(set(massive.SYMBOL_MAP), set(INSTRUMENTS))
        self.assertEqual(massive.SYMBOL_MAP["XAUUSD"], "C:XAUUSD")

    def test_extension_instruments_pass_the_gateway_but_the_site_manual_panel_stays_on_the_seven_pairs(self):
        for symbol in ("EURGBP", "XAUUSD"):
            decision = mt5_risk.evaluate_entry(ACCOUNT, replace(ORDER, symbol=symbol), LIMITS)
            self.assertNotIn("instrument", {c.name for c in decision.checks if not c.passed})  # allowed since 2026-11-11
            with self.assertRaises(mt5view.Mt5ViewError):
                mt5view.validate_request({"kind": "order", "symbol": symbol, "side": "LONG", "stop_pips": 30})
        unknown = mt5_risk.evaluate_entry(ACCOUNT, replace(ORDER, symbol="BTCUSD"), LIMITS)
        self.assertIn("instrument", {c.name for c in unknown.checks if not c.passed})
        self.assertEqual(mt5view.PAIR_SYMBOLS, tuple(PAIRS))


class ConversionTests(unittest.TestCase):
    @staticmethod
    def engine(rates=None, **risk):
        eng = Engine(StrategyParams(), replace(RiskParams(), **risk), CostParams(spread_pips=0.0, slippage_pips=0.0))
        eng.state["usd_rates"] = dict(rates or {})
        return eng

    def test_the_profit_of_a_cross_is_converted_with_the_rate_of_its_quote_currency(self):
        eng, rec = self.engine({"GBP": 1.25, "EUR": 1.10}), Recorder()
        eng._open(rec, T0, "EURGBP", "LONG", 0.85, atr=0.005)  # stop 0.01 GBP per EUR = 0.0125 USD: 50 USD of risk = 4 000 units
        self.assertEqual(eng.state["positions"]["EURGBP"]["units"], 4000)
        eng._close(rec, T0 + timedelta(hours=1), "EURGBP", 0.86, "test")
        self.assertAlmostEqual(rec.trades[-1]["gross_pnl"], (0.86 - 0.85) * 4000 * 1.25, places=3)

    def test_a_cross_entry_is_refused_while_a_rate_is_unknown(self):
        eng, rec = self.engine({"EUR": 1.10}), Recorder()  # no GBP rate
        eng._open(rec, T0, "EURGBP", "LONG", 0.85, atr=0.005)
        self.assertNotIn("EURGBP", eng.state["positions"])
        self.assertEqual(rec.orders[-1]["status"], "REFUSE")
        self.assertIn("taux", rec.orders[-1]["reason"])

    def test_the_notional_of_each_kind_of_instrument(self):
        eng = self.engine({"EUR": 1.10, "GBP": 1.25})
        self.assertAlmostEqual(eng._notional("EURGBP", 4000, 0.85), 4400.0)  # a cross: units of the base, at the base rate
        self.assertEqual(eng._notional("USDJPY", 4000, 150.0), 4000)  # the dollar is the base
        self.assertAlmostEqual(eng._notional("EURUSD", 4000, 1.1), 4400.0)
        self.assertAlmostEqual(eng._notional("XAUUSD", 3, 2000.0), 6000.0)  # ounces at the price

    def test_the_rates_come_from_the_dollar_pairs_at_the_bar_time(self):
        eng = self.engine()
        eng.peers = PeerPrices({"EURUSD": {T0: 1.10}, "USDJPY": {T0: 150.0}, "GBPUSD": {}, "AUDUSD": {T0: 0.65},
                                "NZDUSD": {T0: 0.60}, "USDCHF": {T0: 0.90}, "USDCAD": {T0: 1.40}})
        eng._refresh_rates(T0)
        rates = eng.state["usd_rates"]
        self.assertAlmostEqual(rates["EUR"], 1.10)
        self.assertAlmostEqual(rates["JPY"], 1 / 150.0)
        self.assertAlmostEqual(rates["CAD"], 1 / 1.40)
        self.assertNotIn("GBP", rates)  # no price at that time: the last known rate would stay, here there is none

    def test_gold_and_silver_follow_their_own_unit_rules(self):
        gold, rec = self.engine(), Recorder()
        gold._open(rec, T0, "XAUUSD", "LONG", 2000.0, atr=15.0)  # a 30 USD stop per ounce, plus 0.4 of round-trip cost
        self.assertEqual(gold.state["positions"]["XAUUSD"]["units"], 1.0)  # 50 / 30.4 = 1.6 ounces, whole ounces
        silver, rec = self.engine(), Recorder()
        silver._open(rec, T0, "XAGUSD", "LONG", 25.0, atr=0.4)
        self.assertEqual(silver.state["positions"]["XAGUSD"]["units"], 50.0)  # the minimum is 50 ounces (0.01 lot)
        tiny, rec = self.engine(initial_capital=1000.0), Recorder()
        tiny._open(rec, T0, "XAGUSD", "LONG", 25.0, atr=0.4)
        self.assertEqual(rec.orders[-1]["status"], "REFUSE")  # 5 USD of risk cannot buy 50 ounces of that stop

    def test_when_the_dollar_pairs_are_needed(self):
        sma, currency = _build(StrategyParams, {"family": "sma_trend"}), _build(StrategyParams, {"family": "currency_momentum"})
        self.assertTrue(uses_peers(sma, ["EURGBP"]))  # a cross is converted with the dollar pairs
        self.assertFalse(uses_peers(sma, ["EURUSD", "XAUUSD"]))
        self.assertTrue(uses_peers(currency, ["EURUSD"]))
        self.assertIn("currency_momentum", CROSS_FAMILIES)


def candles(symbol, closes, start=T0):
    return [Candle(symbol, "H1", start + timedelta(hours=i), None, None, None, c, None, "TEST", KIND_REFERENCE)
            for i, c in enumerate(closes)]


class CrossStrategyTests(unittest.TestCase):
    def peers(self, moves):
        flat = [1.0] * 8
        closes = {}
        for symbol in PAIRS:
            series = list(flat)
            if symbol in moves:
                series[-1] = moves[symbol]
            closes[symbol] = {c.timestamp: c.close for c in candles(symbol, series)}
        return PeerPrices(closes)

    def params(self, family):
        return _build(StrategyParams, {"fast": 3, "slow": 6, "atr_window": 3, "min_atr_pct": 0.0, "z_entry": 1.0, "family": family})

    def test_a_cross_follows_the_strength_of_its_base_against_its_quote(self):
        peers = self.peers({"EURUSD": 1.03})  # the euro gains, the pound does not
        data = candles("EURGBP", [1.0] * 7 + [1.03])
        self.assertEqual(decide(data, self.params("currency_momentum"), peers).action, "ACHAT")
        self.assertEqual(decide(data, self.params("currency_reversion"), peers).action, "VENTE")
        weaker = self.peers({"GBPUSD": 1.03})  # now the pound gains: the cross falls
        data = candles("EURGBP", [1.0] * 7 + [0.97])
        self.assertEqual(decide(data, self.params("currency_momentum"), weaker).action, "VENTE")

    def test_gold_has_no_currency_strength_and_decides_nothing(self):
        data = candles("XAUUSD", [1.0] * 7 + [1.03])
        self.assertIsNone(decide(data, self.params("currency_momentum"), self.peers({"EURUSD": 1.03})))


class BacktestAndLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.store = CandleStore(self.db)
        self.store.initialize()
        self.store.register_source("ECB_EXR", "ECB_SDMX_API", "REFERENCE", "test")

    def tearDown(self):
        self.temp.cleanup()

    def test_a_backtest_of_a_cross_loads_the_dollar_pairs_and_reports_in_usd(self):
        for symbol in (*PAIRS, "EURGBP"):
            self.store.store(daily_series(symbol, years=4, start=datetime(2020, 1, 1, 13, 15, tzinfo=UTC)), "REFERENCE", None)
        strategy = {"fast": 5, "slow": 20, "atr_window": 5, "min_atr_pct": 0.0, "max_atr_pct": 0.03, "family": "sma_trend"}
        request = learning._request("EURGBP", {"strategy": strategy, "stop_atr_multiple": 2.0},
                                    datetime(2022, 1, 3, tzinfo=UTC), datetime(2023, 12, 29, tzinfo=UTC))
        result = run_backtest(self.store, request)
        self.assertGreater(result["metrics"]["trade_count"], 0)
        self.assertTrue(any("dollar" in a for a in result["assumptions"]))
        self.assertTrue(all(isinstance(t["net_pnl"], float) for t in result["trades"]))

    def test_the_hourly_pipeline_covers_the_extended_universe_and_the_daily_one_the_seven_pairs(self):
        self.assertEqual(learning.D1_PROFILE.universe, tuple(PAIRS))
        self.assertEqual(learning.H1_PROFILE.universe, tuple(INSTRUMENTS))
        daily = learning.LearningStore(self.db)
        daily.initialize()
        with self.assertRaises(learning.SimulationError):
            learning.learn(self.store, daily, "XAUUSD")  # hors de l'univers quotidien : aucune ligne de run créée
        self.assertEqual(daily.runs(), [])
        hourly = learning.LearningStore(self.db, learning.H1_PROFILE)
        hourly.initialize()
        lab = learning.LabStore(self.db)
        lab.initialize()
        self.assertEqual(len(learning.overview(hourly, lab)["pairs"]), len(INSTRUMENTS))
        self.assertEqual(len(learning.overview(daily, lab)["pairs"]), len(PAIRS))

    def test_the_costs_of_a_request_follow_the_instrument_and_double_in_the_stress_test(self):
        params = {"strategy": learning.default_params()["strategy"], "stop_atr_multiple": 2.0}
        window = (T0, T0 + timedelta(days=30))
        gold = learning._request("XAUUSD", params, *window)
        self.assertEqual((gold["costs"]["spread_pips"], gold["costs"]["slippage_pips"]), (30.0, 5.0))
        stressed = learning._request("XAUUSD", params, *window, cost_factor=2.0)
        self.assertEqual(stressed["costs"]["spread_pips"], 60.0)
        _build(CostParams, stressed["costs"])  # the doubled costs of a metal are still valid (they once exceeded a Forex bound)
        euro = learning._request("EURUSD", params, *window)
        self.assertEqual((euro["costs"]["spread_pips"], euro["costs"]["slippage_pips"]), (1.0, 0.2))  # unchanged for the 7


if __name__ == "__main__":
    unittest.main()
