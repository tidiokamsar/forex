import unittest
from datetime import UTC, datetime, timedelta

from astra.models import BacktestConfig, CostModel, Direction, MarketBar, RiskPolicy
from astra.research import generate_sma_crossover_signals, run_sma_backtest
from astra.risk import RiskEngine

START = datetime(2020, 1, 1, tzinfo=UTC)


def make_bar(index, close, *, open_price=None, high=None, low=None, day_offset=0):
    timestamp = START + timedelta(days=day_offset, minutes=index)
    open_price = close if open_price is None else open_price
    high = max(open_price, close) + 0.1 if high is None else high
    low = min(open_price, close) - 0.1 if low is None else low
    return MarketBar(
        instrument="EURUSD",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
    )


class ResearchTests(unittest.TestCase):
    def test_signal_uses_close_and_entry_occurs_at_next_bar_open(self):
        bars = [
            make_bar(0, 1.0),
            make_bar(1, 1.0),
            make_bar(2, 1.0),
            make_bar(3, 2.0, open_price=1.95, high=2.1, low=1.9),
            make_bar(4, 2.2, open_price=2.05, high=2.3, low=2.0),
            make_bar(5, 2.3, open_price=2.2, high=2.4, low=2.1),
        ]
        signals = generate_sma_crossover_signals(bars, fast_window=2, slow_window=3)
        self.assertEqual(len(signals), 1)
        self.assertIs(signals[0].direction, Direction.LONG)
        self.assertEqual(signals[0].timestamp, bars[3].timestamp)
        self.assertEqual(signals[0].reference_price, bars[3].close)
        self.assertEqual(
            generate_sma_crossover_signals(bars[:4], fast_window=2, slow_window=3),
            signals,
        )

        config = BacktestConfig(
            fast_window=2,
            slow_window=3,
            stop_lookback=1,
            stop_range_multiple=1.0,
        )
        result = run_sma_backtest(bars, config=config)
        self.assertEqual(result.trade_count, 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_time, bars[4].timestamp)
        self.assertEqual(trade.entry_price, bars[4].open)
        self.assertNotEqual(trade.entry_time, signals[0].timestamp)

    def test_evaluation_window_uses_warmup_signal_but_opens_no_warmup_trades(self):
        bars = [
            make_bar(0, 1.0),
            make_bar(1, 1.0),
            make_bar(2, 1.0),
            make_bar(3, 2.0, open_price=1.95, high=2.1, low=1.9),
            make_bar(4, 2.2, open_price=2.05, high=2.3, low=2.0),
            make_bar(5, 2.3, open_price=2.2, high=2.4, low=2.1),
        ]
        start = bars[4].timestamp
        result = run_sma_backtest(
            bars,
            config=BacktestConfig(
                fast_window=2,
                slow_window=3,
                stop_lookback=1,
                stop_range_multiple=1.0,
            ),
            evaluation_start_time=start,
        )

        self.assertEqual(result.start, start)
        self.assertEqual(result.observations, 2)
        self.assertEqual(result.provenance["warmup_observations"], 4)
        self.assertEqual(result.trade_count, 1)
        self.assertEqual(result.trades[0].entry_time, start)
        self.assertEqual(
            result.provenance["evaluation_classification"], "EXPLORATORY_NON_PROMOTABLE"
        )

    def test_round_trip_spread_slippage_and_commission_reduce_net_pnl(self):
        bars = [
            make_bar(0, 1.00),
            make_bar(1, 1.00),
            make_bar(2, 1.00),
            make_bar(3, 1.20, open_price=1.19, high=1.21, low=1.19),
            make_bar(4, 1.25, open_price=1.22, high=1.27, low=1.20),
            make_bar(5, 1.30, open_price=1.25, high=1.32, low=1.23),
        ]
        cost_model = CostModel(
            pip_size=0.01,
            spread_pips=1.0,
            slippage_pips_per_side=0.5,
            commission_per_unit_per_side=0.001,
        )
        result = run_sma_backtest(
            bars,
            config=BacktestConfig(
                fast_window=2,
                slow_window=3,
                stop_lookback=1,
                stop_range_multiple=1.0,
                cost_model=cost_model,
            ),
        )
        trade = result.trades[0]
        expected_costs = 2 * trade.units * (
            cost_model.spread_pips * cost_model.pip_size / 2
            + cost_model.slippage_pips_per_side * cost_model.pip_size
            + cost_model.commission_per_unit_per_side
        )
        self.assertAlmostEqual(trade.costs, expected_costs)
        self.assertGreater(trade.costs, 0)
        self.assertAlmostEqual(trade.net_pnl, trade.gross_pnl - expected_costs)
        self.assertAlmostEqual(result.total_costs, expected_costs)
        self.assertAlmostEqual(result.net_pnl, result.gross_pnl - result.total_costs)
        self.assertTrue(any("not broker-reported" in item for item in result.assumptions))

    def test_intrabar_stop_precedes_strategy_exit_and_uses_stop_price(self):
        bars = [
            make_bar(0, 1.0),
            make_bar(1, 1.0),
            make_bar(2, 1.0),
            make_bar(3, 2.0, open_price=1.9, high=2.1, low=1.9),
            # This bar both crosses the stop and closes below the short SMA.
            make_bar(4, 0.5, open_price=2.0, high=2.05, low=0.4),
            make_bar(5, 0.5, open_price=0.5, high=0.6, low=0.4),
        ]
        config = BacktestConfig(
            fast_window=2,
            slow_window=3,
            stop_lookback=1,
            stop_range_multiple=1.0,
        )
        result = run_sma_backtest(bars, config=config)
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, "stop_loss")
        self.assertAlmostEqual(trade.exit_price, 1.8)
        self.assertEqual(trade.exit_time, bars[4].timestamp)

    def test_reversal_exits_and_reenters_at_the_next_bar_open(self):
        bars = [
            make_bar(0, 1.0),
            make_bar(1, 1.0),
            make_bar(2, 1.0),
            make_bar(3, 2.0, open_price=1.8, high=2.25, low=1.75),
            make_bar(4, 1.9, open_price=2.0, high=2.05, low=1.85),
            make_bar(5, 1.8, open_price=1.7, high=1.85, low=1.65),
        ]
        result = run_sma_backtest(
            bars,
            config=BacktestConfig(
                fast_window=1,
                slow_window=2,
                stop_lookback=1,
                stop_range_multiple=1.0,
            ),
        )
        self.assertEqual(result.trade_count, 2)
        old_position, reverse_position = result.trades
        self.assertEqual(old_position.exit_reason, "strategy_exit")
        self.assertEqual(old_position.exit_time, bars[5].timestamp)
        self.assertEqual(old_position.exit_price, bars[5].open)
        self.assertEqual(reverse_position.direction, Direction.SHORT)
        self.assertEqual(reverse_position.entry_time, bars[5].timestamp)
        self.assertEqual(reverse_position.entry_price, bars[5].open)
        self.assertTrue(
            any("reversal exits" in item for item in result.assumptions)
        )

    def test_daily_loss_rejects_later_entry_on_same_event_day(self):
        bars = [
            make_bar(0, 1.0),
            make_bar(1, 1.0),
            make_bar(2, 1.0),
            make_bar(3, 2.0, open_price=1.9, high=2.1, low=1.9),
            make_bar(4, 1.9, open_price=2.0, high=2.05, low=1.7),
            make_bar(5, 0.5, open_price=1.9, high=2.0, low=0.4),
            make_bar(6, 0.5, open_price=0.5, high=0.6, low=0.4),
            make_bar(7, 0.5, open_price=0.5, high=0.6, low=0.4),
        ]
        config = BacktestConfig(
            fast_window=1,
            slow_window=2,
            stop_lookback=1,
            stop_range_multiple=1.0,
        )
        engine = RiskEngine(RiskPolicy(daily_loss_limit=0.001))
        result = run_sma_backtest(bars, config=config, risk_engine=engine)
        self.assertEqual(result.trade_count, 1)
        self.assertEqual(result.trades[0].exit_reason, "stop_loss")
        self.assertLess(result.trades[0].net_pnl, -10.0)
        self.assertTrue(
            any("daily_loss_limit_reached" in rejection for rejection in result.risk_rejections),
            result.risk_rejections,
        )

    def test_empty_and_no_signal_inputs_return_empty_trade_results(self):
        self.assertEqual(generate_sma_crossover_signals([], 2, 3), [])
        empty = run_sma_backtest([])
        self.assertEqual(empty.observations, 0)
        self.assertEqual(empty.trades, ())
        self.assertIsNone(empty.start)
        self.assertIsNone(empty.end)

        flat_bars = [make_bar(index, 1.0) for index in range(5)]
        self.assertEqual(generate_sma_crossover_signals(flat_bars, 2, 3), [])
        result = run_sma_backtest(
            flat_bars,
            config=BacktestConfig(fast_window=2, slow_window=3),
        )
        self.assertEqual(result.observations, len(flat_bars))
        self.assertEqual(result.trade_count, 0)
        self.assertEqual(result.net_pnl, 0.0)
        self.assertIsNone(result.win_rate)
        self.assertEqual(result.instrument, "EURUSD")
        self.assertTrue(any("wall-clock" in item for item in result.assumptions))
        self.assertEqual(result.provenance["backtest_config"]["fast_window"], 2)
        self.assertEqual(result.provenance["backtest_config"]["slow_window"], 3)

    def test_naive_event_timestamps_are_rejected(self):
        naive = MarketBar(
            instrument="EURUSD",
            timestamp=datetime(2020, 1, 1),  # noqa: DTZ001 - naive on purpose: must be rejected
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.0,
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            generate_sma_crossover_signals([naive], 1, 2)


if __name__ == "__main__":
    unittest.main()
