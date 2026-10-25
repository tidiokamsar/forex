import math
import statistics
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from astra.data import load_bars_csv
from astra.models import BacktestConfig, Direction, RiskPolicy, Signal, Trade
from astra.research import (
    STRATEGY_NAME,
    STRATEGY_VERSION,
    generate_sma_crossover_signals,
    run_sma_backtest,
)
from astra.risk import RiskEngine
from astra.signal_backtest import extended_metrics, run_signal_backtest
from astra.strategies import (
    MOMENTUM_NAME,
    MOMENTUM_VERSION,
    MomentumConfig,
    decisions_to_signals,
    evaluate_momentum,
)
from test_features import START, make_synthetic_bars


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "synthetic_eurusd_m15.csv"


def _sma_equivalence(test, bars, config, policy=None, **kwargs):
    """Each run gets its own RiskEngine: engines remember intent ids."""

    reference = run_sma_backtest(bars, config=config, risk_engine=RiskEngine(policy), **kwargs)
    signals = generate_sma_crossover_signals(bars, config.fast_window, config.slow_window)
    result = run_signal_backtest(
        bars, signals, config, STRATEGY_NAME, STRATEGY_VERSION, risk_engine=RiskEngine(policy), **kwargs
    )
    test.assertEqual(result.trades, reference.trades)
    test.assertEqual(result.net_pnl, reference.net_pnl)
    test.assertEqual(result.gross_pnl, reference.gross_pnl)
    test.assertEqual(result.total_costs, reference.total_costs)
    test.assertEqual(result.final_equity, reference.final_equity)
    test.assertEqual(result.max_drawdown_pct, reference.max_drawdown_pct)
    test.assertEqual(result.risk_rejections, reference.risk_rejections)
    test.assertEqual(result.observations, reference.observations)
    test.assertEqual(result.start, reference.start)
    return reference, result


class EquivalenceTests(unittest.TestCase):
    def test_sma_signals_reproduce_sma_backtest_on_generated_bars(self):
        bars = make_synthetic_bars(600)
        for fast, slow in ((3, 8), (5, 20), (10, 30)):
            with self.subTest(fast=fast, slow=slow):
                config = BacktestConfig(fast_window=fast, slow_window=slow, stop_lookback=10, stop_range_multiple=1.5)
                reference, _ = _sma_equivalence(self, bars, config)
                self.assertGreater(reference.trade_count, 0)

    def test_sma_signals_reproduce_sma_backtest_on_bundled_fixture(self):
        dataset = load_bars_csv(FIXTURE, source_type="SYNTHETIC")
        self.assertTrue(dataset.usable)
        config = BacktestConfig(fast_window=3, slow_window=8, stop_lookback=5, stop_range_multiple=1.0)
        _sma_equivalence(self, dataset.bars, config)

    def test_equivalence_with_evaluation_window_and_risk_rejections(self):
        bars = make_synthetic_bars(600)
        config = BacktestConfig(fast_window=3, slow_window=8, stop_lookback=3, stop_range_multiple=0.5)
        _sma_equivalence(self, bars, config, evaluation_start_time=bars[250].timestamp)
        reference, _ = _sma_equivalence(self, bars, config, policy=RiskPolicy(daily_loss_limit=0.001))
        self.assertTrue(reference.risk_rejections)
        rejected, _ = _sma_equivalence(self, bars, config, policy=RiskPolicy(max_spread_pips=1.0))
        self.assertEqual(rejected.trade_count, 0)
        self.assertTrue(rejected.risk_rejections)


class SignalBacktestTests(unittest.TestCase):
    def test_strategy_decisions_run_through_backtester(self):
        bars = make_synthetic_bars(600)
        signals = decisions_to_signals(evaluate_momentum(bars, MomentumConfig(lookback=10)))
        config = BacktestConfig()
        result = run_signal_backtest(bars, signals, config, MOMENTUM_NAME, MOMENTUM_VERSION)
        self.assertEqual(result.strategy, MOMENTUM_NAME)
        self.assertGreater(result.trade_count, 0)
        index_at = {bar.timestamp: i for i, bar in enumerate(bars)}
        signal_times = {s.timestamp for s in signals}
        for trade in result.trades:
            entry_index = index_at[trade.entry_time]
            self.assertEqual(trade.entry_price, bars[entry_index].open)
            self.assertIn(bars[entry_index - 1].timestamp, signal_times)
        self.assertAlmostEqual(result.net_pnl, result.gross_pnl - result.total_costs)
        self.assertEqual(result.provenance["signal_count"], len(signals))
        self.assertTrue(any("caller's responsibility" in a for a in result.assumptions))

    def test_invalid_signals_fail_closed(self):
        bars = make_synthetic_bars(50)
        config = BacktestConfig()

        def signal(**overrides):
            values = dict(
                instrument="EURUSD", timestamp=bars[10].timestamp, direction=Direction.LONG,
                reference_price=bars[10].close, strategy="s", strategy_version="1",
                reason="test",
            )
            values.update(overrides)
            return Signal(**values)

        with self.assertRaises(ValueError):
            run_signal_backtest(bars, [signal(timestamp=bars[10].timestamp + timedelta(seconds=1))], config, "s", "1")
        with self.assertRaises(ValueError):
            run_signal_backtest(bars, [signal(), signal()], config, "s", "1")
        with self.assertRaises(ValueError):
            run_signal_backtest(bars, [signal(strategy="other")], config, "s", "1")
        with self.assertRaises(ValueError):
            run_signal_backtest(bars, [signal(instrument="GBPUSD")], config, "s", "1")
        with self.assertRaises(ValueError):
            run_signal_backtest(bars, [signal()], config, "", "1")
        with self.assertRaises(TypeError):
            run_signal_backtest(bars, [object()], config, "s", "1")
        result = run_signal_backtest(bars, [signal()], config, "s", "1")
        self.assertEqual(result.trades[0].entry_time, bars[11].timestamp)

    def test_empty_inputs(self):
        result = run_signal_backtest([], [], BacktestConfig(), "s", "1")
        self.assertEqual(result.trade_count, 0)
        self.assertIsNone(result.start)
        flat = run_signal_backtest(make_synthetic_bars(20), [], BacktestConfig(), "s", "1")
        self.assertEqual(flat.trade_count, 0)
        self.assertEqual(flat.observations, 20)


def _trade(net, index):
    return Trade(
        instrument="EURUSD", direction=Direction.LONG,
        entry_time=START + timedelta(minutes=index), exit_time=START + timedelta(minutes=index + 1),
        entry_price=1.0, exit_price=1.0, units=1.0, gross_pnl=net, costs=0.0, net_pnl=net,
        exit_reason="test",
    )


class ExtendedMetricsTests(unittest.TestCase):
    def _result(self, pnl):
        base = run_signal_backtest(make_synthetic_bars(20), [], BacktestConfig(), "s", "1")
        return replace(base, trades=tuple(_trade(v, i) for i, v in enumerate(pnl)), trade_count=len(pnl))

    def test_known_values(self):
        pnl = [10.0, -5.0, -5.0, -2.0, 20.0, -1.0]
        metrics = extended_metrics(self._result(pnl), seed=7)
        self.assertEqual(metrics.trade_count, 6)
        self.assertAlmostEqual(metrics.expectancy, sum(pnl) / 6)
        self.assertAlmostEqual(metrics.average_win, 15.0)
        self.assertAlmostEqual(metrics.average_loss, -13.0 / 4)
        self.assertAlmostEqual(metrics.median_trade, -1.5)
        self.assertEqual(metrics.max_consecutive_losses, 3)
        mean = statistics.fmean(pnl)
        self.assertAlmostEqual(metrics.per_trade_sharpe_like, mean / statistics.stdev(pnl))
        downside = math.sqrt(sum(min(v, 0) ** 2 for v in pnl) / 6)
        self.assertAlmostEqual(metrics.per_trade_sortino_like, mean / downside)
        interval = metrics.mean_net_pnl_ci
        self.assertLessEqual(interval.lower, mean)
        self.assertGreaterEqual(interval.upper, mean)
        self.assertEqual(interval.seed, 7)
        self.assertIn("not annualized", metrics.convention)
        self.assertEqual(extended_metrics(self._result(pnl), seed=7), metrics)
        self.assertNotEqual(extended_metrics(self._result(pnl), seed=8).mean_net_pnl_ci, interval)

    def test_small_samples_report_none_not_fabricated_values(self):
        empty = extended_metrics(self._result([]))
        self.assertEqual(empty.trade_count, 0)
        self.assertIsNone(empty.expectancy)
        self.assertIsNone(empty.mean_net_pnl_ci)
        single = extended_metrics(self._result([3.0]))
        self.assertEqual(single.trade_count, 1)
        self.assertIsNone(single.per_trade_sharpe_like)
        self.assertIsNone(single.mean_net_pnl_ci)
        winners = extended_metrics(self._result([1.0, 2.0]))
        self.assertIsNone(winners.per_trade_sortino_like)
        self.assertIsNone(winners.average_loss)
        with self.assertRaises(ValueError):
            extended_metrics(self._result([1.0, 2.0]), resamples=10)

    def test_metrics_on_real_backtest_result(self):
        bars = make_synthetic_bars(600)
        config = BacktestConfig(fast_window=3, slow_window=8)
        result = run_signal_backtest(
            bars, generate_sma_crossover_signals(bars, 3, 8), config, STRATEGY_NAME, STRATEGY_VERSION
        )
        metrics = extended_metrics(result)
        self.assertEqual(metrics.trade_count, result.trade_count)
        self.assertAlmostEqual(metrics.expectancy * metrics.trade_count, result.net_pnl)


if __name__ == "__main__":
    unittest.main()
