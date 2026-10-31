"""Risk measures and regimes: known answers, and refusal to report ratios on a sample too short to mean anything."""

import math
import unittest
from datetime import UTC, datetime, timedelta

from astra import riskmetrics


def curve(values, hours=24):
    start = datetime(2026, 1, 5, tzinfo=UTC)
    return [((start + timedelta(hours=hours * i)).isoformat(), v) for i, v in enumerate(values)]


class MeasureTests(unittest.TestCase):
    def test_a_short_history_reports_no_ratio_but_still_the_drawdown(self):
        result = riskmetrics.measures(curve([100, 101, 99]))
        self.assertFalse(result["computable"])
        self.assertIsNone(result["sharpe"])
        self.assertEqual(result["max_drawdown_pct"], round((101 - 99) / 101 * 100, 3))
        self.assertIn("nécessaires", result["note"])

    def test_intraday_points_are_folded_to_the_last_value_of_each_day(self):
        folded = riskmetrics.daily_closes(curve([100, 101, 102, 103], hours=12))
        self.assertEqual([v for _d, v in folded], [101.0, 103.0])

    def test_ratios_match_a_hand_computation(self):
        values = [100.0]
        for r in (0.01, -0.005) * 6:
            values.append(values[-1] * (1 + r))
        result = riskmetrics.measures(curve(values))
        returns = [0.01, -0.005] * 6
        mean = sum(returns) / 12
        spread = math.sqrt(sum((r - mean) ** 2 for r in returns) / 11)
        downside = math.sqrt(sum(min(r, 0) ** 2 for r in returns) / 12)
        self.assertTrue(result["computable"])
        self.assertAlmostEqual(result["sharpe"], mean / spread * math.sqrt(252), places=2)
        self.assertAlmostEqual(result["sortino"], mean / downside * math.sqrt(252), places=2)
        self.assertAlmostEqual(result["max_drawdown_pct"], 0.5, places=2)
        self.assertGreater(result["calmar"], 0)

    def test_a_flat_curve_has_no_ratio_instead_of_a_division_by_zero(self):
        result = riskmetrics.measures(curve([100.0] * 15))
        self.assertTrue(result["computable"])
        self.assertIsNone(result["sharpe"])
        self.assertIsNone(result["sortino"])
        self.assertIsNone(result["calmar"])

    def test_a_losing_curve_has_negative_ratios(self):
        values = [100 * (0.99 ** i) * (1 + (0.002 if i % 2 else -0.002)) for i in range(20)]
        result = riskmetrics.measures(curve(values))
        self.assertLess(result["sharpe"], 0)
        self.assertLess(result["calmar"], 0)

    def test_empty_and_non_finite_points_are_ignored(self):
        self.assertEqual(riskmetrics.measures([])["days"], 0)
        self.assertEqual(riskmetrics.measures(curve([100, float("nan"), 101]))["days"], 2)


class RegimeTests(unittest.TestCase):
    def test_a_steady_climb_is_a_trend_and_a_zigzag_is_a_range(self):
        climb = [1.0 + 0.002 * i for i in range(100)]
        zigzag = [1.0 + (0.002 if i % 2 else 0.0) for i in range(100)]
        up = riskmetrics.regime(climb)
        self.assertEqual((up["trend"], up["direction"]), ("tendance", "hausse"))
        self.assertEqual(riskmetrics.regime(zigzag)["trend"], "range")
        self.assertEqual(riskmetrics.regime(climb[::-1])["direction"], "baisse")

    def test_volatility_is_high_when_recent_moves_exceed_the_earlier_median(self):
        calm = [1.0 + 0.0005 * (i % 2) for i in range(80)]
        wild = [1.0 + 0.01 * (i % 2) for i in range(20)]
        self.assertEqual(riskmetrics.regime(calm + wild)["volatility"], "haute")
        self.assertEqual(riskmetrics.regime(wild + calm)["volatility"], "basse")

    def test_too_few_bars_give_no_regime(self):
        result = riskmetrics.regime([1.0] * 10)
        self.assertIsNone(result["trend"])
        self.assertIn("nécessaires", result["note"])


if __name__ == "__main__":
    unittest.main()
