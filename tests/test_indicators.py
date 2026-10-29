"""Indicators against hand-computed values and known reference series."""

import unittest

from astra import indicators


class IndicatorTests(unittest.TestCase):
    def test_sma_matches_hand_values_and_aligns_with_the_input(self):
        out = indicators.sma([1, 2, 3, 4, 5], 3)
        self.assertEqual(out, [None, None, 2.0, 3.0, 4.0])

    def test_ema_is_seeded_with_the_sma(self):
        out = indicators.ema([1, 2, 3, 4, 5], 3)
        self.assertEqual(out[:2], [None, None])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[3], 3.0)  # alpha 0.5: 0.5 x 4 + 0.5 x 2
        self.assertAlmostEqual(out[4], 4.0)

    def test_rsi_reference_series_from_wilder(self):
        # Closes from the classic worked example (14 periods): the first RSI is 70.46.
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        out = indicators.rsi(closes, 14)
        self.assertEqual(out[:14], [None] * 14)
        self.assertAlmostEqual(out[14], 70.46, places=1)

    def test_rsi_extremes(self):
        self.assertEqual(indicators.rsi(list(range(1, 30)), 14)[-1], 100.0)
        self.assertEqual(indicators.rsi([5.0] * 30, 14)[-1], 50.0)
        self.assertLess(indicators.rsi(list(range(30, 1, -1)), 14)[-1], 1e-9)

    def test_macd_lines_and_alignment(self):
        closes = [1.0 + 0.01 * i + (0.02 if i % 3 == 0 else 0.0) for i in range(80)]
        out = indicators.macd(closes)
        self.assertTrue(all(len(v) == len(closes) for v in out.values()))
        self.assertIsNone(out["macd"][24])
        self.assertIsNotNone(out["macd"][25])  # slow EMA starts at index 25
        self.assertIsNone(out["signal"][32])
        self.assertIsNotNone(out["signal"][33])  # 25 + 9 - 1
        i = 60
        self.assertAlmostEqual(out["histogram"][i], out["macd"][i] - out["signal"][i])
        self.assertGreater(out["macd"][i], 0)  # a rising series: fast EMA above slow EMA

    def test_short_or_bad_input(self):
        self.assertEqual(indicators.sma([1.0], 3), [None])
        self.assertEqual(indicators.rsi([1.0, 2.0], 14), [None, None])
        with self.assertRaises(ValueError):
            indicators.sma([1.0, float("nan")], 2)
        with self.assertRaises(ValueError):
            indicators.macd([1.0] * 40, fast=26, slow=12)

    def test_bundle_has_the_dashboard_series(self):
        out = indicators.bundle([1.0 + i * 0.001 for i in range(120)])
        self.assertEqual(set(out), {"sma20", "sma50", "rsi14", "macd", "signal", "histogram"})
        self.assertIsNotNone(out["sma50"][-1])


if __name__ == "__main__":
    unittest.main()
