import math
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from astra.features import (
    FEATURE_COLUMNS,
    FEATURE_SET_VERSION,
    FeatureSpec,
    TrainOnlyScaler,
    average_true_range,
    compute_feature_matrix,
    detect_unexpected_values,
    donchian_channel,
    efficiency_ratio,
    log_returns,
    relative_strength_index,
    rolling_volatility,
    rolling_zscore,
    sma_slope,
)
from astra.models import MarketBar


START = datetime(2026, 1, 5, tzinfo=timezone.utc)


def make_synthetic_bars(
    count=400, seed=7, *, with_quotes=True, start_price=1.10, start_index=0, segment=80
):
    """Return deterministic SYNTHETIC M15 bars; not market data.

    Segments alternate between up-drift, flat, and down-drift so trend,
    range, and volatility features all move.
    """

    rng = random.Random(seed)
    drifts = (0.00025, 0.0, -0.00025)
    vols = (0.0003, 0.0002, 0.0006)
    bars = []
    price = start_price
    for offset in range(count):
        index = start_index + offset
        phase = (index // segment) % 3
        open_price = price
        close = max(0.5, open_price + drifts[phase] + rng.gauss(0.0, vols[phase]))
        high = max(open_price, close) + abs(rng.gauss(0.0, 0.0002))
        low = min(open_price, close) - abs(rng.gauss(0.0, 0.0002))
        spread = 0.00008 + abs(rng.gauss(0.0, 0.00003))
        bars.append(
            MarketBar(
                instrument="EURUSD",
                timestamp=START + timedelta(minutes=15 * index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=100.0,
                bid=close - spread / 2 if with_quotes else None,
                ask=close + spread / 2 if with_quotes else None,
            )
        )
        price = close
    return bars


def mutate_after(bars, index, seed=99):
    """Replace every bar after ``index`` with a different SYNTHETIC path."""

    tail = make_synthetic_bars(
        len(bars) - index - 1,
        seed=seed,
        with_quotes=bars[0].bid is not None,
        start_price=bars[index].close * 1.01,
        start_index=index + 1,
    )
    tail = [replace(bar, close=bar.close * 1.002, high=bar.high * 1.002 + 0.001) for bar in tail]
    return list(bars[: index + 1]) + tail


CHECK_INDICES = (0, 3, 19, 20, 41, 150, 287, 398)


def _series_functions():
    return {
        "log_returns": lambda b: log_returns(b),
        "volatility": lambda b: rolling_volatility(b, 20),
        "atr": lambda b: average_true_range(b, 14),
        "rsi": lambda b: relative_strength_index(b, 14),
        "zscore": lambda b: rolling_zscore(b, 20),
        "donchian_high": lambda b: donchian_channel(b, 20)[0],
        "donchian_low": lambda b: donchian_channel(b, 20)[1],
        "efficiency": lambda b: efficiency_ratio(b, 10),
        "sma_slope": lambda b: sma_slope(b, 20, 5),
    }


class NoLookaheadTests(unittest.TestCase):
    def test_every_feature_ignores_bars_after_index(self):
        bars = make_synthetic_bars()
        for name, function in _series_functions().items():
            full = function(bars)
            self.assertEqual(len(full), len(bars))
            for index in CHECK_INDICES:
                with self.subTest(feature=name, index=index):
                    mutated = function(mutate_after(bars, index))
                    self.assertEqual(mutated[: index + 1], full[: index + 1])
                    self.assertEqual(function(bars[: index + 1]), full[: index + 1])

    def test_feature_matrix_rows_ignore_later_bars(self):
        bars = make_synthetic_bars()
        matrix = compute_feature_matrix(bars)
        for index in CHECK_INDICES:
            with self.subTest(index=index):
                mutated = compute_feature_matrix(mutate_after(bars, index))
                self.assertEqual(mutated.rows[: index + 1], matrix.rows[: index + 1])

    def test_mutating_later_bar_does_change_later_values(self):
        bars = make_synthetic_bars()
        full = rolling_zscore(bars, 20)
        mutated = rolling_zscore(mutate_after(bars, 200), 20)
        self.assertNotEqual(mutated[201:], full[201:])


class FeatureValueTests(unittest.TestCase):
    def _bars(self, closes, spread=0.01):
        return [
            MarketBar("EURUSD", START + timedelta(minutes=i), c, c + spread, c - spread, c)
            for i, c in enumerate(closes)
        ]

    def test_log_returns_and_warmup(self):
        bars = self._bars([1.0, 1.1, 1.0])
        values = log_returns(bars)
        self.assertIsNone(values[0])
        self.assertAlmostEqual(values[1], math.log(1.1))
        self.assertAlmostEqual(values[2], math.log(1.0 / 1.1))

    def test_donchian_excludes_current_bar(self):
        bars = self._bars([1.0, 1.2, 1.1, 1.5])
        highs, lows = donchian_channel(bars, 2)
        self.assertEqual(highs[:2], [None, None])
        self.assertAlmostEqual(highs[2], 1.21)
        self.assertAlmostEqual(lows[2], 0.99)
        self.assertAlmostEqual(highs[3], 1.21)
        self.assertGreater(bars[3].close, highs[3])

    def test_efficiency_ratio_straight_and_choppy(self):
        straight = efficiency_ratio(self._bars([1.0, 1.1, 1.2, 1.3]), 3)
        self.assertAlmostEqual(straight[3], 1.0)
        choppy = efficiency_ratio(self._bars([1.0, 1.1, 1.0, 1.1, 1.0]), 4)
        self.assertAlmostEqual(choppy[4], 0.0)
        self.assertEqual(efficiency_ratio(self._bars([1.0] * 4), 3)[3], 0.0)

    def test_rsi_extremes_and_flat(self):
        self.assertEqual(relative_strength_index(self._bars([1.0, 1.1, 1.2, 1.3]), 3)[3], 100.0)
        self.assertEqual(relative_strength_index(self._bars([1.3, 1.2, 1.1, 1.0]), 3)[3], 0.0)
        self.assertEqual(relative_strength_index(self._bars([1.0] * 4), 3)[3], 50.0)
        self.assertIsNone(relative_strength_index(self._bars([1.0] * 4), 3)[2])

    def test_wilder_atr_matches_hand_computation(self):
        bars = [
            MarketBar("EURUSD", START, 1.0, 1.2, 0.9, 1.1),
            MarketBar("EURUSD", START + timedelta(minutes=1), 1.1, 1.3, 1.0, 1.2),
            MarketBar("EURUSD", START + timedelta(minutes=2), 1.2, 1.25, 0.8, 0.9),
        ]
        atr = average_true_range(bars, 2)
        self.assertIsNone(atr[0])
        self.assertAlmostEqual(atr[1], (0.3 + 0.3) / 2)
        self.assertAlmostEqual(atr[2], (0.3 * 1 + 0.45) / 2)

    def test_zscore_and_volatility(self):
        bars = self._bars([1.0, 1.0, 1.0, 1.3])
        z = rolling_zscore(bars, 4)
        self.assertAlmostEqual(z[3], math.sqrt(3))
        self.assertEqual(rolling_zscore(self._bars([1.0] * 3), 3)[2], 0.0)
        vol = rolling_volatility(bars, 2)
        self.assertEqual(vol[:2], [None, None])
        self.assertAlmostEqual(vol[2], 0.0)

    def test_sma_slope(self):
        slope = sma_slope(self._bars([1.0, 1.0, 2.0]), 1, 2)
        self.assertEqual(slope[:2], [None, None])
        self.assertAlmostEqual(slope[2], 0.5)

    def test_invalid_windows_are_rejected(self):
        bars = self._bars([1.0, 1.1])
        with self.assertRaises(ValueError):
            rolling_volatility(bars, 1)
        with self.assertRaises(ValueError):
            efficiency_ratio(bars, 0)
        with self.assertRaises(ValueError):
            FeatureSpec(rsi_window=True)
        with self.assertRaises(ValueError):
            FeatureSpec(feature_set_version="other")


class FeatureMatrixTests(unittest.TestCase):
    def test_matrix_metadata_and_warmup(self):
        bars = make_synthetic_bars(200)
        spec = FeatureSpec()
        matrix = compute_feature_matrix(bars, spec)
        self.assertEqual(matrix.columns, FEATURE_COLUMNS)
        self.assertEqual(matrix.feature_set_version, FEATURE_SET_VERSION)
        self.assertEqual(matrix.spec_sha256, spec.sha256())
        self.assertNotEqual(matrix.spec_sha256, FeatureSpec(atr_window=10).sha256())
        self.assertEqual(len(matrix.rows), len(bars))
        for name, first in matrix.warmup.items():
            column = matrix.column(name)
            self.assertTrue(all(value is None for value in column[:first]), name)
            self.assertTrue(all(value is not None for value in column[first:]), name)
        self.assertEqual(matrix.first_complete_index(), max(matrix.warmup.values()))
        self.assertEqual(detect_unexpected_values(matrix), ())

    def test_detect_unexpected_values_flags_problems(self):
        matrix = compute_feature_matrix(make_synthetic_bars(100))
        rows = [list(row) for row in matrix.rows]
        rsi = FEATURE_COLUMNS.index("rsi")
        vol = FEATURE_COLUMNS.index("volatility")
        er = FEATURE_COLUMNS.index("efficiency_ratio")
        high = FEATURE_COLUMNS.index("donchian_high")
        rows[60][rsi] = 150.0
        rows[61][vol] = float("nan")
        rows[62][er] = None
        rows[63][high] = 0.5
        broken = replace(matrix, rows=tuple(tuple(row) for row in rows))
        codes = {(issue.code, issue.row_index) for issue in detect_unexpected_values(broken)}
        self.assertIn(("OUT_OF_RANGE", 60), codes)
        self.assertIn(("NON_FINITE", 61), codes)
        self.assertIn(("MISSING_AFTER_WARMUP", 62), codes)
        self.assertIn(("INVERTED_CHANNEL", 63), codes)

    def test_slice_keeps_spec_and_shifts_warmup(self):
        matrix = compute_feature_matrix(make_synthetic_bars(100))
        head = matrix.slice(0, 50)
        self.assertEqual(head.rows, matrix.rows[:50])
        self.assertEqual(head.spec_sha256, matrix.spec_sha256)
        tail = matrix.slice(60, 100)
        self.assertEqual(tail.warmup["volatility"], 0)
        with self.assertRaises(ValueError):
            matrix.slice(10, 10)


class TrainOnlyScalerTests(unittest.TestCase):
    def test_refuses_transform_before_fit(self):
        with self.assertRaises(RuntimeError):
            TrainOnlyScaler().transform([(1.0,)])

    def test_fit_uses_training_rows_only(self):
        bars = make_synthetic_bars()
        columns = ("rsi", "zscore", "volatility")
        matrix = compute_feature_matrix(bars)
        first = matrix.first_complete_index(columns)
        train_idx = range(first, 200)
        scaler = TrainOnlyScaler().fit(matrix.select(columns, train_idx), columns)
        rsi_train = [matrix.column("rsi")[i] for i in train_idx]
        self.assertAlmostEqual(scaler.means[0], sum(rsi_train) / len(rsi_train))
        self.assertEqual(scaler.fitted_rows, len(train_idx))

        mutated = compute_feature_matrix(mutate_after(bars, 199))
        other = TrainOnlyScaler().fit(mutated.select(columns, train_idx), columns)
        self.assertEqual(other.means, scaler.means)
        self.assertEqual(other.stds, scaler.stds)
        full_fit = TrainOnlyScaler().fit(matrix.select(columns, range(first, 400)), columns)
        self.assertNotEqual(full_fit.means, scaler.means)

        transformed = scaler.transform(matrix.select(columns, range(200, 210)), columns)
        expected = (matrix.column("rsi")[200] - scaler.means[0]) / scaler.stds[0]
        self.assertAlmostEqual(transformed[0][0], expected)

    def test_rejects_missing_values_column_mismatch_and_refit(self):
        scaler = TrainOnlyScaler()
        with self.assertRaises(ValueError):
            scaler.fit([(1.0,), (None,)], ("a",))
        scaler.fit([(1.0, 5.0), (3.0, 5.0)], ("a", "b"))
        self.assertEqual(scaler.constant_columns, ("b",))
        self.assertEqual(scaler.transform([(2.0, 5.0)]), [(0.0, 0.0)])
        with self.assertRaises(ValueError):
            scaler.transform([(1.0, 2.0)], ("b", "a"))
        with self.assertRaises(ValueError):
            scaler.transform([(float("inf"), 2.0)])
        with self.assertRaises(RuntimeError):
            scaler.fit([(1.0, 5.0), (3.0, 5.0)], ("a", "b"))


if __name__ == "__main__":
    unittest.main()
