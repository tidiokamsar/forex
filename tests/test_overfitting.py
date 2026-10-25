import json
import math
import random
import statistics
import unittest
from itertools import pairwise

from astra.overfitting import (
    MAX_COMBINATIONS,
    OVERFITTING_VERSION,
    PBO_METHOD,
    DeflatedSharpeResult,
    PBOResult,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    probability_of_backtest_overfitting,
    sharpe_like,
)

GAMMA = 0.5772156649015329
NORMAL = statistics.NormalDist()


def _blocks_to_rows(per_block, rows_per_block=2):
    """Expand per-block constant values ``per_block[variant][block]`` into chronological rows."""

    blocks = len(per_block[0])
    return [[variant[block] for variant in per_block] for block in range(blocks) for _ in range(rows_per_block)]


def _noise(seed, rows, variants, means=None):
    rng = random.Random(seed)
    means = means or [0.0] * variants
    return [[rng.gauss(means[v], 1.0) for v in range(variants)] for _ in range(rows)]


class HandComputedPBOTest(unittest.TestCase):
    # Block means (2 identical rows per block), 4 slices, 3 variants:
    #   A = [3, 3, -3, -3], B = [-1, -1, 1, 1], C = [0.5] * 4.  Metric = mean.
    # Combinations in lexicographic order of in-sample blocks:
    #   {0,1}: IS A=3,B=-1,C=.5 -> A; OOS A=-3,B=1,C=.5 -> A rank 1, w=1/4, lambda=ln(1/3)
    #   {0,2},{0,3},{1,2},{1,3}: IS A=0,B=0,C=.5 -> C; OOS A=0,B=0,C=.5 -> C rank 3, w=3/4, lambda=ln 3
    #   {2,3}: IS A=-3,B=1,C=.5 -> B; OOS A=3,B=-1,C=.5 -> B rank 1, lambda=ln(1/3)
    # PBO = 2/6, median of sorted logits (-,-,+,+,+,+) = ln 3.
    PERFORMANCE = _blocks_to_rows([[3, 3, -3, -3], [-1, -1, 1, 1], [0.5, 0.5, 0.5, 0.5]])

    def test_exact_logits_and_pbo(self):
        result = probability_of_backtest_overfitting(self.PERFORMANCE, n_slices=4, metric=statistics.fmean)
        low, high = math.log(1 / 3), math.log(3)
        self.assertEqual(result.n_combinations, 6)
        self.assertEqual(result.n_variants, 3)
        self.assertEqual(result.n_slices, 4)
        for actual, expected in zip(result.logits, (low, high, high, high, high, low), strict=True):
            self.assertAlmostEqual(actual, expected, places=12)
        self.assertAlmostEqual(result.pbo, 2 / 6, places=12)
        self.assertAlmostEqual(result.median_logit, high, places=12)
        self.assertEqual(result.method, PBO_METHOD)

    def test_default_metric_matches_independent_recomputation(self):
        rows = _noise(11, 40, 4)
        result = probability_of_backtest_overfitting(rows, n_slices=4)
        bounds = [(0, 10), (10, 20), (20, 30), (30, 40)]

        def sharpe(variant, blocks):
            values = [rows[t][variant] for b in blocks for t in range(*bounds[b])]
            return statistics.fmean(values) / statistics.stdev(values)

        expected = []
        for in_blocks in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)):
            out_blocks = [b for b in range(4) if b not in in_blocks]
            best = max(range(4), key=lambda v: sharpe(v, in_blocks))
            ordered = sorted(range(4), key=lambda v: sharpe(v, out_blocks))
            w = (ordered.index(best) + 1) / 5
            expected.append(math.log(w / (1 - w)))
        for actual, wanted in zip(result.logits, expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=12)
        self.assertAlmostEqual(result.pbo, sum(1 for x in expected if x <= 0) / 6, places=12)

    def test_uneven_rows_use_floor_bounds(self):
        # 9 rows, 4 slices -> blocks [0,2) [2,4) [4,6) [6,9); the last block carries the extra row.
        rows = [[float(t), float(-t)] for t in range(9)]
        seen = []

        def metric(values):
            seen.append(len(values))
            return statistics.fmean(values)

        probability_of_backtest_overfitting(rows, n_slices=4, metric=metric)
        self.assertEqual(sorted(set(seen)), [4, 5])
        self.assertEqual(sum(seen), 6 * 2 * 9)

    def test_ties_lowest_index_in_sample_and_average_rank_out_of_sample(self):
        # Two identical variants: IS tie -> variant 0; OOS tie -> rank 1.5, w = 0.5, lambda = 0 -> overfit.
        rows = [[r[0], r[0]] for r in _noise(3, 16, 1)]
        result = probability_of_backtest_overfitting(rows, n_slices=4)
        self.assertTrue(all(value == 0.0 for value in result.logits))
        self.assertEqual(result.pbo, 1.0)

    def test_zero_stdev_window_scores_zero(self):
        self.assertEqual(sharpe_like([0.2, 0.2, 0.2]), 0.0)
        # Constant variants score 0.0 everywhere; variant 2 has a positive Sharpe-like edge in every block.
        rows = [[0.1, -0.1, 1.0 + (t % 2)] for t in range(16)]
        result = probability_of_backtest_overfitting(rows, n_slices=4)
        self.assertEqual(result.pbo, 0.0)
        self.assertAlmostEqual(result.logits[0], math.log(3), places=12)


class StatisticalBehaviourPBOTest(unittest.TestCase):
    def test_pure_noise_has_high_pbo(self):
        # With i.i.d. zero-mean variants the in-sample winner is a random draw out-of-sample,
        # so its OOS rank is roughly uniform and PBO is about 0.5 in expectation. A bound of
        # 0.3 leaves room for the sampling noise of 70 dependent combinations.
        result = probability_of_backtest_overfitting(_noise(20261025, 800, 50), n_slices=8)
        self.assertEqual(result.n_combinations, 70)
        self.assertGreater(result.pbo, 0.3)

    def test_persistent_edge_has_low_pbo(self):
        # Variant 0 has a per-period mean of 0.3 sd (Sharpe 0.3); 400 rows per half give a Sharpe
        # standard error near 0.05, far below the edge, so it is selected and ranks top OOS.
        means = [0.3] + [0.0] * 29
        result = probability_of_backtest_overfitting(_noise(7, 800, 30, means), n_slices=8)
        self.assertLess(result.pbo, 0.2)
        self.assertGreater(result.median_logit, 0.0)

    def test_deterministic(self):
        rows = _noise(5, 160, 10)
        first = probability_of_backtest_overfitting(rows, n_slices=8)
        second = probability_of_backtest_overfitting(rows, n_slices=8)
        self.assertEqual(first, second)


class PBOValidationTest(unittest.TestCase):
    def test_rejects_invalid_inputs(self):
        good = _noise(1, 16, 3)
        cases = {
            "one variant": ([[0.1], [0.2], [0.3], [0.4], [0.5], [0.6], [0.7], [0.8]], 4),
            "empty": ([], 4),
            "ragged": ([*good[:-1], [0.1, 0.2]], 4),
            "nan": ([*good[:-1], [0.1, math.nan, 0.2]], 4),
            "inf": ([*good[:-1], [0.1, math.inf, 0.2]], 4),
            "bool": ([*good[:-1], [0.1, True, 0.2]], 4),
            "odd slices": (good, 3),
            "too few slices": (good, 0),
            "too many slices for T": (good, 10),
            "non-int slices": (good, 4.0),
        }
        for label, (rows, slices) in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                probability_of_backtest_overfitting(rows, n_slices=slices)
        with self.assertRaises(TypeError):
            probability_of_backtest_overfitting("abc", n_slices=2)
        with self.assertRaises(TypeError):
            probability_of_backtest_overfitting(good, n_slices=4, metric=3)

    def test_combination_cap(self):
        self.assertLessEqual(math.comb(16, 8), MAX_COMBINATIONS)
        with self.assertRaisesRegex(ValueError, "exceeds the cap"):
            probability_of_backtest_overfitting(_noise(2, 36, 2), n_slices=18)

    def test_non_finite_metric_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            probability_of_backtest_overfitting(_noise(2, 16, 2), n_slices=4, metric=lambda values: math.nan)


class DeflatedSharpeTest(unittest.TestCase):
    RETURNS = tuple(row[0] + 0.08 for row in _noise(99, 250, 1))

    @staticmethod
    def _moments(values):
        n = len(values)
        mean = sum(values) / n
        m2 = sum((x - mean) ** 2 for x in values) / n
        m3 = sum((x - mean) ** 3 for x in values) / n
        m4 = sum((x - mean) ** 4 for x in values) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1))
        return n, mean / sd, m3 / m2**1.5, m4 / m2**2

    def test_more_trials_lower_probability(self):
        probabilities = [deflated_sharpe_ratio(self.RETURNS, n_trials=n).probability for n in (1, 2, 5, 10, 100, 1000)]
        for earlier, later in pairwise(probabilities):
            self.assertGreater(earlier, later)

    def test_single_trial_equals_probabilistic_sharpe_ratio(self):
        n, sr, skew, kurt = self._moments(self.RETURNS)
        psr = NORMAL.cdf(sr * math.sqrt(n - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2))
        result = deflated_sharpe_ratio(self.RETURNS, n_trials=1)
        self.assertEqual(result.expected_max_sharpe, 0.0)
        self.assertAlmostEqual(result.probability, psr, places=12)
        self.assertAlmostEqual(result.observed_sharpe, sr, places=12)
        self.assertAlmostEqual(result.skewness, skew, places=12)
        self.assertAlmostEqual(result.kurtosis, kurt, places=12)
        self.assertEqual(result.n_observations, 250)

    def test_zero_mean_noise_single_trial_is_near_half(self):
        raw = [row[0] for row in _noise(4, 500, 1)]
        centre = statistics.fmean(raw)
        result = deflated_sharpe_ratio([x - centre for x in raw], n_trials=1)
        self.assertAlmostEqual(result.probability, 0.5, places=9)

    def test_known_numeric_check(self):
        n, sr, skew, kurt = self._moments(self.RETURNS)
        variance = 0.004
        sr0 = math.sqrt(variance) * ((1 - GAMMA) * NORMAL.inv_cdf(1 - 1 / 25) + GAMMA * NORMAL.inv_cdf(1 - 1 / (25 * math.e)))
        expected = NORMAL.cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2))
        result = deflated_sharpe_ratio(self.RETURNS, n_trials=25, trial_sharpe_variance=variance)
        self.assertAlmostEqual(result.expected_max_sharpe, sr0, places=12)
        self.assertAlmostEqual(result.probability, expected, places=12)
        default = deflated_sharpe_ratio(self.RETURNS, n_trials=25)
        self.assertAlmostEqual(default.expected_max_sharpe, expected_maximum_sharpe(25, 1 / (n - 1)), places=12)

    def test_expected_max_close_to_true_expected_maximum_of_normals(self):
        # E[max of 10 i.i.d. N(0,1)] is about 1.5388; the Gumbel-based approximation lands within 0.05.
        self.assertAlmostEqual(expected_maximum_sharpe(10, 1.0), 1.5388, delta=0.05)

    def test_rejects_invalid_inputs(self):
        cases = {
            "too few returns": ([0.1, 0.2], {"n_trials": 1}),
            "zero variance": ([0.1, 0.1, 0.1, 0.1], {"n_trials": 1}),
            "nan": ([0.1, math.nan, 0.2], {"n_trials": 1}),
            "zero trials": (self.RETURNS, {"n_trials": 0}),
            "bool trials": (self.RETURNS, {"n_trials": True}),
            "bad variance": (self.RETURNS, {"n_trials": 3, "trial_sharpe_variance": -1.0}),
            "nan variance": (self.RETURNS, {"n_trials": 3, "trial_sharpe_variance": math.nan}),
        }
        for label, (returns, kwargs) in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                deflated_sharpe_ratio(returns, **kwargs)


class SerializationTest(unittest.TestCase):
    def test_to_dict_is_json_serializable_and_versioned(self):
        self.assertEqual(OVERFITTING_VERSION, "astra-overfitting-1.0.0")
        pbo = probability_of_backtest_overfitting(_noise(8, 32, 3), n_slices=4)
        dsr = deflated_sharpe_ratio(DeflatedSharpeTest.RETURNS, n_trials=4)
        self.assertIsInstance(pbo, PBOResult)
        self.assertIsInstance(dsr, DeflatedSharpeResult)
        for payload in (pbo.to_dict(), dsr.to_dict()):
            self.assertEqual(payload["version"], OVERFITTING_VERSION)
            self.assertEqual(json.loads(json.dumps(payload, allow_nan=False)), payload)
        self.assertEqual(len(pbo.to_dict()["logits"]), pbo.n_combinations)


if __name__ == "__main__":
    unittest.main()
