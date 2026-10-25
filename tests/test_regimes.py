import unittest
from dataclasses import replace
from datetime import datetime, timezone

from astra.features import FeatureSpec, compute_feature_matrix
from astra.regimes import (
    REGIME_MODEL_VERSION,
    RegimeConfig,
    Session,
    SpreadState,
    TrendState,
    VolatilityState,
    classify,
    fit_regime_thresholds,
    session_label,
)
from test_features import CHECK_INDICES, make_synthetic_bars, mutate_after


TRAIN_END = 200


def _fit(bars, end=TRAIN_END, with_bars=True):
    matrix = compute_feature_matrix(bars)
    train = matrix.slice(0, end)
    return matrix, fit_regime_thresholds(train, train_bars=bars[:end] if with_bars else None)


class RegimeThresholdTests(unittest.TestCase):
    def test_thresholds_use_training_slice_only(self):
        bars = make_synthetic_bars()
        _, thresholds = _fit(bars)
        _, mutated = _fit(mutate_after(bars, TRAIN_END - 1))
        self.assertEqual(thresholds, mutated)
        self.assertEqual(thresholds.training_end, bars[TRAIN_END - 1].timestamp)
        self.assertEqual(thresholds.training_rows, TRAIN_END)
        _, full = _fit(bars, end=len(bars))
        self.assertNotEqual(full.volatility_high, thresholds.volatility_high)
        self.assertEqual(thresholds.model_version, REGIME_MODEL_VERSION)
        self.assertLess(thresholds.volatility_low, thresholds.volatility_high)
        self.assertEqual(len(thresholds.sha256()), 64)

    def test_spread_threshold_source_is_recorded(self):
        bars = make_synthetic_bars()
        _, fitted = _fit(bars)
        self.assertEqual(fitted.spread_threshold_source, "training_quantile")
        _, fallback = _fit(make_synthetic_bars(with_quotes=False))
        self.assertEqual(fallback.spread_threshold_source, "configured_fallback")
        self.assertEqual(fallback.wide_spread_pips, RegimeConfig().fallback_wide_spread_pips)

    def test_insufficient_training_rows_fail_closed(self):
        matrix = compute_feature_matrix(make_synthetic_bars(60))
        with self.assertRaises(ValueError):
            fit_regime_thresholds(matrix.slice(0, 40))
        with self.assertRaises(ValueError):
            RegimeConfig(volatility_low_quantile=0.8, volatility_high_quantile=0.2)

    def test_misaligned_bars_or_spec_are_rejected(self):
        bars = make_synthetic_bars()
        matrix, thresholds = _fit(bars)
        with self.assertRaises(ValueError):
            fit_regime_thresholds(matrix.slice(0, TRAIN_END), train_bars=bars[1 : TRAIN_END + 1])
        with self.assertRaises(ValueError):
            classify(compute_feature_matrix(bars, FeatureSpec(atr_window=10)), thresholds)
        with self.assertRaises(ValueError):
            classify(matrix, thresholds, bars[:-1])


class ClassifyTests(unittest.TestCase):
    def test_classification_ignores_bars_after_index(self):
        bars = make_synthetic_bars()
        matrix, thresholds = _fit(bars)
        estimates = classify(matrix, thresholds, bars)
        for index in CHECK_INDICES:
            with self.subTest(index=index):
                mutated_bars = mutate_after(bars, index)
                mutated = classify(compute_feature_matrix(mutated_bars), thresholds, mutated_bars)
                self.assertEqual(mutated[: index + 1], estimates[: index + 1])

    def test_estimates_are_bounded_and_explained(self):
        bars = make_synthetic_bars()
        matrix, thresholds = _fit(bars)
        estimates = classify(matrix, thresholds, bars)
        self.assertEqual(len(estimates), len(bars))
        for estimate in estimates:
            self.assertGreaterEqual(estimate.confidence, 0.0)
            self.assertLessEqual(estimate.confidence, 1.0)
            self.assertTrue(estimate.reasons)
            self.assertEqual(estimate.confidence, min(estimate.trend_confidence, estimate.volatility_confidence))
        warmup = estimates[0]
        self.assertIs(warmup.trend, TrendState.UNKNOWN)
        self.assertIs(warmup.volatility, VolatilityState.UNKNOWN)
        self.assertEqual(warmup.confidence, 0.0)
        trends = {estimate.trend for estimate in estimates}
        self.assertTrue({TrendState.TREND_UP, TrendState.TREND_DOWN, TrendState.RANGE} <= trends)
        vols = {estimate.volatility for estimate in estimates}
        self.assertTrue({VolatilityState.LOW, VolatilityState.HIGH} <= vols)

    def test_spread_unknown_without_quotes_is_never_invented(self):
        bars = make_synthetic_bars(with_quotes=False)
        matrix, thresholds = _fit(bars)
        for estimate in classify(matrix, thresholds, bars):
            self.assertIs(estimate.spread, SpreadState.UNKNOWN)
            self.assertIsNone(estimate.spread_pips)
        for estimate in classify(matrix, thresholds):
            self.assertIs(estimate.spread, SpreadState.UNKNOWN)

    def test_wide_spread_is_flagged(self):
        bars = make_synthetic_bars()
        matrix, thresholds = _fit(bars)
        wide = list(bars)
        wide[300] = replace(wide[300], bid=wide[300].close - 0.0005, ask=wide[300].close + 0.0005)
        estimates = classify(matrix, thresholds, wide)
        self.assertIs(estimates[300].spread, SpreadState.WIDE)
        self.assertAlmostEqual(estimates[300].spread_pips, 10.0)
        self.assertIn(estimates[301].spread, (SpreadState.NORMAL, SpreadState.WIDE))

    def test_session_labels_by_utc_hour(self):
        def at(hour):
            return session_label(datetime(2026, 1, 5, hour, tzinfo=timezone.utc))

        self.assertIs(at(3), Session.ASIA)
        self.assertIs(at(8), Session.LONDON)
        self.assertIs(at(13), Session.LONDON_NEW_YORK_OVERLAP)
        self.assertIs(at(18), Session.NEW_YORK)
        self.assertIs(at(22), Session.OFF_HOURS)
        with self.assertRaises(ValueError):
            session_label(datetime(2026, 1, 5, 3))


if __name__ == "__main__":
    unittest.main()
