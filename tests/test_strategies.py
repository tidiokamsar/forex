import ast
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from astra import strategies
from astra.features import compute_feature_matrix
from astra.models import CostModel, Direction, MarketBar
from astra.regimes import TrendState, classify, fit_regime_thresholds
from astra.research import generate_sma_crossover_signals
from astra.strategies import (
    STRATEGY_REGISTRY,
    BreakoutConfig,
    MeanReversionConfig,
    MomentumConfig,
    SmaBaselineConfig,
    StrategyAction,
    decisions_to_signals,
    estimated_round_trip_cost_pips,
    evaluate_breakout,
    evaluate_mean_reversion,
    evaluate_momentum,
    evaluate_sma_baseline,
)
from test_features import CHECK_INDICES, START, make_synthetic_bars, mutate_after


def _regimes(bars, thresholds):
    return classify(compute_feature_matrix(bars), thresholds, bars)


def _thresholds(bars, end=200):
    matrix = compute_feature_matrix(bars[:end])
    return fit_regime_thresholds(matrix, train_bars=bars[:end])


def _runners(thresholds):
    return {
        "momentum": lambda b: evaluate_momentum(b, MomentumConfig(lookback=10)),
        "momentum_regime": lambda b: evaluate_momentum(
            b, MomentumConfig(lookback=10, require_trend_regime=True), regimes=_regimes(b, thresholds)
        ),
        "breakout": lambda b: evaluate_breakout(b, BreakoutConfig(channel_window=10)),
        "mean_reversion": lambda b: evaluate_mean_reversion(
            b, MeanReversionConfig(entry_z=1.5), regimes=_regimes(b, thresholds)
        ),
        "sma_baseline": lambda b: evaluate_sma_baseline(b, SmaBaselineConfig(fast_window=5, slow_window=20)),
    }


class NoLookaheadTests(unittest.TestCase):
    def test_every_strategy_ignores_bars_after_index(self):
        bars = make_synthetic_bars()
        thresholds = _thresholds(bars)
        for name, run in _runners(thresholds).items():
            full = run(bars)
            self.assertEqual(len(full), len(bars))
            for index in CHECK_INDICES:
                with self.subTest(strategy=name, index=index):
                    self.assertEqual(run(mutate_after(bars, index))[: index + 1], full[: index + 1])
                    self.assertEqual(run(bars[: index + 1]), full[: index + 1])

    def test_each_strategy_emits_some_directional_decisions(self):
        bars = make_synthetic_bars()
        for name, run in _runners(_thresholds(bars)).items():
            with self.subTest(strategy=name):
                actions = {decision.action for decision in run(bars)}
                self.assertIn(StrategyAction.ABSTAIN, actions)
                self.assertTrue(actions & {StrategyAction.LONG, StrategyAction.SHORT})


class DecisionContentTests(unittest.TestCase):
    def _trend_bars(self, count=40, step=0.002):
        bars = []
        price = 1.1
        for i in range(count):
            close = price + step
            bars.append(
                MarketBar(
                    "EURUSD", START + timedelta(minutes=15 * i), price,
                    max(price, close) + 0.0005, min(price, close) - 0.0005, close,
                )
            )
            price = close
        return bars

    def test_momentum_long_decision_has_stop_cost_and_reason(self):
        bars = self._trend_bars()
        decisions = evaluate_momentum(bars, MomentumConfig(lookback=5, atr_window=5))
        last = decisions[-1]
        self.assertIs(last.action, StrategyAction.LONG)
        self.assertLess(last.invalidation_price, last.reference_price)
        self.assertEqual(last.reference_price, bars[-1].close)
        self.assertAlmostEqual(last.estimated_cost_pips, estimated_round_trip_cost_pips(CostModel()))
        self.assertIsNone(last.abstain_reason)
        self.assertIsNone(last.uncertainty)
        self.assertEqual(last.strategy, strategies.MOMENTUM_NAME)
        self.assertEqual(last.horizon_bars, 10)
        self.assertIn("log return", last.reason)

    def test_breakout_short_below_prior_channel(self):
        bars = self._trend_bars(step=-0.002)
        decision = evaluate_breakout(bars, BreakoutConfig(channel_window=5, atr_window=5))[-1]
        self.assertIs(decision.action, StrategyAction.SHORT)
        self.assertGreater(decision.invalidation_price, decision.reference_price)

    def test_estimated_cost_pips(self):
        model = CostModel(pip_size=0.0001, spread_pips=1.0, slippage_pips_per_side=0.5, commission_per_unit_per_side=0.00002)
        self.assertAlmostEqual(estimated_round_trip_cost_pips(model), 1.0 + 1.0 + 0.4)
        with self.assertRaises(ValueError):
            estimated_round_trip_cost_pips(CostModel(spread_pips=-1.0))


class AbstentionTests(unittest.TestCase):
    def test_warmup_bars_abstain(self):
        bars = make_synthetic_bars(60)
        for decision in evaluate_momentum(bars, MomentumConfig(lookback=20))[:20]:
            self.assertIs(decision.action, StrategyAction.ABSTAIN)
            self.assertEqual(decision.abstain_reason, "warmup")
            self.assertIsNone(decision.invalidation_price)
        for decision in evaluate_sma_baseline(bars)[:30]:
            self.assertEqual(decision.abstain_reason, "warmup")

    def test_cost_exceeding_move_proxy_abstains(self):
        bars = make_synthetic_bars()
        expensive = CostModel(spread_pips=500.0)
        for run in (
            lambda: evaluate_momentum(bars, MomentumConfig(lookback=10), expensive),
            lambda: evaluate_breakout(bars, BreakoutConfig(channel_window=10), expensive),
        ):
            decisions = run()
            self.assertTrue(all(d.action is StrategyAction.ABSTAIN for d in decisions))
            self.assertTrue(any(d.abstain_reason.startswith("cost_exceeds_move_proxy") for d in decisions))

    def test_threshold_and_channel_abstentions(self):
        bars = make_synthetic_bars()
        momentum = evaluate_momentum(bars, MomentumConfig(lookback=10, min_abs_return=0.5))
        self.assertTrue(all(d.action is StrategyAction.ABSTAIN for d in momentum))
        self.assertTrue(any(d.abstain_reason.startswith("below_threshold") for d in momentum))
        breakout = evaluate_breakout(bars, BreakoutConfig(channel_window=10))
        self.assertTrue(any(d.abstain_reason == "inside_channel" for d in breakout))

    def test_mean_reversion_requires_range_regime(self):
        bars = make_synthetic_bars()
        config = MeanReversionConfig(entry_z=1.5)
        without = evaluate_mean_reversion(bars, config)
        self.assertTrue(all(d.action is StrategyAction.ABSTAIN for d in without))
        self.assertTrue(any(d.abstain_reason == "regime_unavailable" for d in without))

        regimes = _regimes(bars, _thresholds(bars))
        with_regime = evaluate_mean_reversion(bars, config, regimes=regimes)
        for decision, regime in zip(with_regime, regimes):
            if decision.action is not StrategyAction.ABSTAIN:
                self.assertIs(regime.trend, TrendState.RANGE)
                self.assertAlmostEqual(decision.uncertainty, 1.0 - regime.confidence)
        self.assertTrue(any(d.abstain_reason.startswith("regime_mismatch") for d in with_regime))

        forced_trend = tuple(replace(r, trend=TrendState.TREND_UP, confidence=1.0) for r in regimes)
        forced = evaluate_mean_reversion(bars, config, regimes=forced_trend)
        self.assertTrue(all(d.action is StrategyAction.ABSTAIN for d in forced))

    def test_low_regime_confidence_abstains(self):
        bars = make_synthetic_bars()
        regimes = tuple(replace(r, confidence=0.05) for r in _regimes(bars, _thresholds(bars)))
        decisions = evaluate_momentum(bars, MomentumConfig(lookback=10, min_regime_confidence=0.5), regimes=regimes)
        self.assertTrue(all(d.action is StrategyAction.ABSTAIN for d in decisions))
        self.assertTrue(any(d.abstain_reason.startswith("low_confidence") for d in decisions))

    def test_misaligned_regimes_are_rejected(self):
        bars = make_synthetic_bars()
        regimes = _regimes(bars, _thresholds(bars))
        with self.assertRaises(ValueError):
            evaluate_momentum(bars, regimes=regimes[1:])


class ConversionAndContractTests(unittest.TestCase):
    def test_sma_baseline_reproduces_research_signals(self):
        bars = make_synthetic_bars()
        decisions = evaluate_sma_baseline(bars, SmaBaselineConfig(fast_window=5, slow_window=20))
        self.assertEqual(
            decisions_to_signals(decisions),
            generate_sma_crossover_signals(bars, 5, 20),
        )

    def test_decisions_to_signals_skips_abstentions(self):
        bars = make_synthetic_bars()
        decisions = evaluate_momentum(bars, MomentumConfig(lookback=10))
        signals = decisions_to_signals(decisions)
        directional = [d for d in decisions if d.action is not StrategyAction.ABSTAIN]
        self.assertEqual(len(signals), len(directional))
        for signal, decision in zip(signals, directional):
            self.assertEqual(signal.timestamp, decision.timestamp)
            self.assertIs(signal.direction, Direction(decision.action.value))
            self.assertEqual(signal.strategy_version, decision.strategy_version)
        with self.assertRaises(TypeError):
            decisions_to_signals([object()])

    def test_configs_validate_and_registry_has_hypotheses(self):
        with self.assertRaises(ValueError):
            MomentumConfig(lookback=0)
        with self.assertRaises(ValueError):
            BreakoutConfig(min_regime_confidence=2.0)
        with self.assertRaises(ValueError):
            MeanReversionConfig(entry_z=0.0)
        with self.assertRaises(ValueError):
            SmaBaselineConfig(fast_window=30, slow_window=10)
        with self.assertRaises(ValueError):
            MomentumConfig(version="9.9.9")
        with self.assertRaises(TypeError):
            evaluate_momentum(make_synthetic_bars(10), BreakoutConfig())
        for info in STRATEGY_REGISTRY.values():
            self.assertTrue(info.hypothesis)
            self.assertTrue(info.version)

    def test_strategies_do_not_import_storage_web_broker_or_execution(self):
        source = Path(strategies.__file__).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        forbidden = ("storage", "web", "cli", "broker", "execution", "socket", "http", "urllib", "sqlite")
        for name in imported:
            self.assertFalse(any(word in name.lower() for word in forbidden), name)


if __name__ == "__main__":
    unittest.main()
