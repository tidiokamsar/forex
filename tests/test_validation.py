import math
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

from astra.models import BacktestConfig, MarketBar
from astra.validation import WalkForwardProtocol, evaluate_walk_forward

START = datetime(2021, 1, 1, tzinfo=UTC)


def make_bars(count=100):
    bars = []
    previous = 1.1
    for index in range(count):
        close = 1.1 + 0.02 * math.sin(index * 0.52)
        open_price = previous
        bars.append(
            MarketBar(
                instrument="EURUSD",
                timestamp=START + timedelta(minutes=15 * index),
                open=open_price,
                high=max(open_price, close) + 0.001,
                low=min(open_price, close) - 0.001,
                close=close,
            )
        )
        previous = close
    return bars


def candidates():
    return (
        BacktestConfig(fast_window=2, slow_window=5, stop_lookback=3),
        BacktestConfig(fast_window=3, slow_window=8, stop_lookback=3),
    )


def protocol(**updates):
    values = {
        "fold_count": 2,
        "minimum_training_bars": 40,
        "holdout_bars": 15,
        "minimum_candidate_trades": 0,
    }
    values.update(updates)
    return WalkForwardProtocol(**values)


class WalkForwardTests(unittest.TestCase):
    def evaluate(self, bars=None, *, configs=None, research_protocol=None):
        return evaluate_walk_forward(
            make_bars() if bars is None else bars,
            candidates() if configs is None else configs,
            hypothesis="A short SMA crossover may persist after estimated costs.",
            protocol=protocol() if research_protocol is None else research_protocol,
        )

    def test_fold_boundaries_expand_training_and_reserve_latest_block(self):
        report = self.evaluate()
        first, second = report.folds

        self.assertEqual(first.train_observations, 40)
        self.assertEqual(first.validation_observations, 20)
        self.assertEqual(second.train_observations, first.train_observations + first.validation_observations + first.test_observations)
        self.assertEqual(first.train_end + timedelta(minutes=15), first.validation_start)
        self.assertEqual(first.validation_end + timedelta(minutes=15), first.test_start)
        self.assertEqual(first.test_end + timedelta(minutes=15), second.validation_start)
        self.assertEqual(second.validation_end + timedelta(minutes=15), second.test_start)
        self.assertEqual(second.test_end + timedelta(minutes=15), report.holdout_start)
        self.assertEqual(first.selected_candidate.start, first.test_start)
        self.assertEqual(first.selected_candidate.end, first.test_end)
        self.assertEqual(first.selected_candidate.observations, first.test_observations)
        self.assertEqual(report.holdout_end, make_bars()[-1].timestamp)
        self.assertEqual(report.holdout_status, "RESERVED_NOT_EVALUATED")
        self.assertFalse(hasattr(report, "holdout_metrics"))

    def test_reports_all_candidate_trials_baselines_and_cost_stress(self):
        report = self.evaluate()

        self.assertEqual(report.candidate_count, 2)
        self.assertEqual(report.candidate_variant_evaluations, 4)
        self.assertEqual(len(report.folds[0].candidate_trials), 2)
        self.assertTrue(any(trial.selected for trial in report.folds[0].candidate_trials))
        self.assertEqual(len(report.final_candidate_selection), 2)
        self.assertTrue(any(trial.selected for trial in report.final_candidate_selection))
        self.assertEqual(
            report.plan_manifest["final_holdout_selection"],
            "aggregate_mean_validation_score_across_validation_windows",
        )
        self.assertEqual(report.no_position_aggregate.net_pnl, 0.0)
        self.assertEqual(report.no_position_aggregate.trade_count, 0)
        self.assertEqual(report.no_position_aggregate.total_costs, 0.0)
        self.assertAlmostEqual(
            report.simple_reference_aggregate.net_pnl,
            sum(fold.simple_reference.net_pnl for fold in report.folds),
        )
        self.assertAlmostEqual(
            report.selected_candidate_aggregate.net_pnl,
            sum(
                fold.selected_candidate.net_pnl
                for fold in report.folds
                if fold.selected_candidate is not None
            ),
        )
        self.assertEqual(len(report.cost_stress_aggregates), 3)
        base, stressed = report.cost_stress_aggregates[0], report.cost_stress_aggregates[-1]
        self.assertGreaterEqual(stressed.simple_reference.total_costs, base.simple_reference.total_costs)
        self.assertLessEqual(stressed.simple_reference.net_pnl, base.simple_reference.net_pnl)
        self.assertEqual(report.evaluation_classification, "EXPLORATORY_WALK_FORWARD_NOT_PROMOTABLE")

    def test_plan_hash_changes_with_protocol_or_candidate_configuration(self):
        original = self.evaluate()
        repeated = self.evaluate()
        changed_split = self.evaluate(research_protocol=protocol(holdout_bars=16))
        changed_candidate = self.evaluate(
            configs=(
                BacktestConfig(fast_window=2, slow_window=6, stop_lookback=3),
                candidates()[1],
            )
        )

        self.assertEqual(original.plan_sha256, repeated.plan_sha256)
        self.assertNotEqual(original.plan_sha256, changed_split.plan_sha256)
        self.assertNotEqual(original.plan_sha256, changed_candidate.plan_sha256)
        self.assertEqual(len(original.plan_sha256), 64)

    def test_changes_after_first_test_window_do_not_change_that_window(self):
        bars = make_bars()
        original = self.evaluate(bars)
        changed = list(bars)
        first_test_end = next(
            index for index, bar in enumerate(bars) if bar.timestamp == original.folds[0].test_end
        )
        for index in range(first_test_end + 1, len(changed)):
            bar = changed[index]
            changed[index] = replace(
                bar,
                open=bar.open + 0.05,
                high=bar.high + 0.05,
                low=bar.low + 0.05,
                close=bar.close + 0.05,
            )
        updated = self.evaluate(changed)

        self.assertNotEqual(original.plan_sha256, updated.plan_sha256)
        self.assertEqual(original.folds[0].selected_candidate, updated.folds[0].selected_candidate)
        self.assertEqual(original.folds[0].simple_reference, updated.folds[0].simple_reference)

    def test_invalid_partitions_and_duplicate_candidates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "minimum_training_bars"):
            self.evaluate(research_protocol=protocol(minimum_training_bars=20))
        with self.assertRaisesRegex(ValueError, "at least one test bar per fold"):
            self.evaluate(research_protocol=protocol(fold_count=50))
        duplicate = candidates()[0]
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self.evaluate(configs=(duplicate, duplicate))

    def test_invalid_holdout_and_stress_protocols_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "holdout_bars"):
            protocol(holdout_bars=0)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            protocol(cost_stress_multipliers=(1.0, 1.0))

    def test_source_format_hashes_share_canonical_market_bar_identity(self):
        bars = make_bars()
        first = evaluate_walk_forward(
            bars,
            candidates(),
            hypothesis="A short SMA crossover may persist after estimated costs.",
            protocol=protocol(),
            dataset_sha256="a" * 64,
        )
        same_bars_different_source = evaluate_walk_forward(
            bars,
            candidates(),
            hypothesis="A short SMA crossover may persist after estimated costs.",
            protocol=protocol(),
            dataset_sha256="b" * 64,
        )

        self.assertNotEqual(first.dataset_sha256, same_bars_different_source.dataset_sha256)
        self.assertEqual(
            first.dataset_identity_sha256,
            same_bars_different_source.dataset_identity_sha256,
        )
        self.assertNotEqual(first.plan_sha256, same_bars_different_source.plan_sha256)

    def test_direct_api_offsets_and_instrument_case_share_canonical_identity(self):
        bars = make_bars()
        alternate_representation = [
            replace(
                bar,
                instrument=bar.instrument.lower(),
                timestamp=bar.timestamp.astimezone(timezone(timedelta(hours=5))),
            )
            for bar in bars
        ]
        first = self.evaluate(bars)
        second = self.evaluate(alternate_representation)

        self.assertEqual(first.dataset_identity_sha256, second.dataset_identity_sha256)
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.folds, second.folds)

    def test_final_holdout_bars_do_not_change_validation_candidate_selection(self):
        bars = make_bars()
        original = self.evaluate(bars)
        changed = list(bars)
        holdout_start_index = len(bars) - protocol().holdout_bars
        for index in range(holdout_start_index, len(changed)):
            bar = changed[index]
            changed[index] = replace(
                bar,
                open=bar.open + 0.05,
                high=bar.high + 0.05,
                low=bar.low + 0.05,
                close=bar.close + 0.05,
            )
        updated = self.evaluate(changed)

        self.assertNotEqual(original.dataset_identity_sha256, updated.dataset_identity_sha256)
        self.assertEqual(original.final_candidate_selection, updated.final_candidate_selection)


if __name__ == "__main__":
    unittest.main()
