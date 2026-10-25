import json
import math
import random
import statistics
import unittest
from itertools import pairwise

from astra.overfitting import (
    MAX_COMBINATIONS,
    NULL_TRIAL_VARIANCE,
    OVERFITTING_VERSION,
    PBO_METHOD,
    DeflatedSharpeResult,
    PBOResult,
    TrialUniverse,
    cross_sectional_variance,
    deflated_sharpe_from_moments,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    probability_of_backtest_overfitting,
    resolve_trials,
    selection_score_metric,
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

    def test_identical_variants_tie_in_and_out_of_sample(self):
        # Two identical variants: IS tie (either one); OOS tie -> rank 1.5, w = 0.5, lambda = 0 -> overfit.
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


class SelectionMetricTest(unittest.TestCase):
    def test_selection_score_matches_hand_computation(self):
        # Equity 1000 -> 1100 -> 1050 -> 1080; peak 1100, drawdown 50/1100; score = 80 - 1000 * 50 / 1100.
        metric = selection_score_metric(1000.0)
        self.assertAlmostEqual(metric([100.0, -50.0, 30.0]), 80.0 - 1000.0 * 50.0 / 1100.0, places=12)
        # Cash peak-to-trough variant: 80 - 50.
        self.assertAlmostEqual(selection_score_metric()([100.0, -50.0, 30.0]), 30.0, places=12)
        # A loss from the start counts against the initial peak.
        self.assertAlmostEqual(selection_score_metric()([-4.0, 1.0]), -3.0 - 4.0, places=12)
        with self.assertRaises(ValueError):
            selection_score_metric(0.0)

    def test_selection_score_equals_validation_selection_score(self):
        from types import SimpleNamespace

        from astra.validation import _selection_score

        pnl = [120.0, -300.0, 50.0, 400.0, -80.0]
        equity, peak, worst = 10_000.0, 10_000.0, 0.0
        for value in pnl:
            equity += value
            peak = max(peak, equity)
            worst = max(worst, (peak - equity) / peak * 100.0)
        result = SimpleNamespace(initial_equity=10_000.0, net_pnl=sum(pnl), max_drawdown_pct=worst)
        self.assertAlmostEqual(selection_score_metric(10_000.0)(pnl), _selection_score(result), places=9)


class HandComputedSelectionPBOTest(unittest.TestCase):
    # Two slices of two rows, two variants.
    #   block 0: X = [-5, 6], Y = [0.5, 0.4]     block 1: X = [1, 1], Y = [-1, 0]
    # Cash selection score (net - peak-to-trough):
    #   block 0: X = 1 - 5 = -4, Y = 0.9 - 0 = 0.9     block 1: X = 2, Y = -1 - 1 = -2
    #   IS=0 -> Y; OOS Y=-2 < X=2 -> rank 1, w = 1/3, lambda = ln(1/2) -> overfit, OOS loss
    #   IS=1 -> X; OOS X=-4 < Y=0.9 -> rank 1, lambda = ln(1/2) -> overfit, OOS loss
    #   PBO = 1, probability_oos_loss = 1.
    # With the mean instead: IS=0 -> X (0.5 > 0.45), OOS X=1 > Y -> rank 2, lambda = ln 2;
    #   IS=1 -> X, OOS X=0.5 > 0.45 -> lambda = ln 2; PBO = 0. The ranking criterion matters.
    ROWS = ((-5.0, 0.5), (6.0, 0.4), (1.0, -1.0), (1.0, 0.0))

    def test_selection_metric(self):
        result = probability_of_backtest_overfitting(self.ROWS, n_slices=2, metric=selection_score_metric())
        self.assertEqual(result.n_combinations, 2)
        for value in result.logits:
            self.assertAlmostEqual(value, math.log(0.5), places=12)
        self.assertEqual(result.pbo, 1.0)
        self.assertEqual(result.probability_oos_loss, 1.0)
        self.assertTrue(result.metric.startswith("astra_selection_score"))

    def test_mean_metric_disagrees(self):
        result = probability_of_backtest_overfitting(self.ROWS, n_slices=2, metric=statistics.fmean)
        for value in result.logits:
            self.assertAlmostEqual(value, math.log(2), places=12)
        self.assertEqual(result.pbo, 0.0)


class ConservativeTieAndEligibilityTest(unittest.TestCase):
    # block 0: V0 = [1, 1], V1 = [1, 1], V2 = [0, 0]   block 1: V0 = [2, 2], V1 = [-1, -1], V2 = [0, 0]
    ROWS = ((1.0, 1.0, 0.0), (1.0, 1.0, 0.0), (2.0, -1.0, 0.0), (2.0, -1.0, 0.0))

    def test_in_sample_tie_takes_worst_out_of_sample_variant(self):
        # IS=0: V0 and V1 tie at 1; V1 is worse OOS (-1) and is taken -> rank 1, w = 1/4, lambda = ln(1/3).
        # (The former lowest-index rule took V0: rank 3, lambda = ln 3, hiding the tie.)
        # IS=1: V0 wins (2); OOS V0 = V1 = 1 tie, V2 = 0 -> midrank 2.5, w = 0.625, lambda = ln(5/3).
        result = probability_of_backtest_overfitting(self.ROWS, n_slices=2, metric=statistics.fmean)
        self.assertAlmostEqual(result.logits[0], math.log(1 / 3), places=12)
        self.assertAlmostEqual(result.logits[1], math.log(5 / 3), places=12)
        self.assertEqual(result.pbo, 0.5)

    def test_ineligible_variants_cannot_be_selected_and_empty_halves_abstain(self):
        # No variant has an eligible count in block 0 -> that combination abstains.
        # In block 1 only V1 is eligible: IS=1 selects V1 (score -1) although V0 scores 2;
        # OOS block 0: V1 = 1 ties V0, V2 = 0 -> midrank 2.5, lambda = ln(5/3).
        counts = [[0, 0, 0], [0, 0, 0], [0, 1, 0], [0, 0, 0]]
        result = probability_of_backtest_overfitting(
            self.ROWS, n_slices=2, metric=statistics.fmean, eligibility=counts, minimum_eligibility=1
        )
        self.assertEqual((result.n_combinations, result.n_evaluated, result.n_abstained), (2, 1, 1))
        self.assertEqual(len(result.logits), 1)
        self.assertAlmostEqual(result.logits[0], math.log(5 / 3), places=12)
        self.assertEqual(result.pbo, 0.0)

    def test_all_abstained_raises_and_bad_eligibility_is_rejected(self):
        zeros = [[0, 0, 0]] * 4
        with self.assertRaisesRegex(ValueError, "no combination"):
            probability_of_backtest_overfitting(self.ROWS, n_slices=2, eligibility=zeros, minimum_eligibility=1)
        with self.assertRaises(ValueError):
            probability_of_backtest_overfitting(self.ROWS, n_slices=2, eligibility=[[0, 0]] * 4)
        with self.assertRaises(ValueError):
            probability_of_backtest_overfitting(self.ROWS, n_slices=2, eligibility=[[0, -1, 0]] * 4)


class DeflatedSharpePaperExampleTest(unittest.TestCase):
    """Bailey and Lopez de Prado (2014), section 'A numerical example'.

    Annualized SR 2.5 on 5 years of daily data (T = 1250, 250 per year), N = 100 trials, annualized
    V[SR] = 1/2, skewness -3, kurtosis 10 (non-excess). The paper reports SR0 = 0.1132 (non-annualized)
    and DSR = 0.9004; N = 46 would give 0.9505; with normal returns the 95% level holds up to N = 88.
    """

    def compute(self, n_trials, skewness=-3.0, kurtosis=10.0):
        return deflated_sharpe_from_moments(
            sharpe=2.5 / math.sqrt(250), n_observations=1250, skewness=skewness, kurtosis=kurtosis,
            n_trials=n_trials, trial_sharpe_variance=0.5 / 250,
        )

    def test_paper_values(self):
        sr0, dsr = self.compute(100)
        self.assertAlmostEqual(sr0, 0.1132, places=4)
        self.assertAlmostEqual(dsr, 0.9004, places=4)
        self.assertAlmostEqual(self.compute(46)[1], 0.9505, places=4)
        self.assertGreater(self.compute(46)[1], 0.95)
        self.assertLess(self.compute(47)[1], 0.95)
        self.assertGreater(self.compute(88, 0.0, 3.0)[1], 0.95)
        self.assertLess(self.compute(89, 0.0, 3.0)[1], 0.95)

    def test_excess_kurtosis_would_be_wrong(self):
        # Passing excess kurtosis (7) instead of kurtosis (10) understates the standard error.
        self.assertGreater(self.compute(100, kurtosis=7.0)[1], self.compute(100)[1])

    def test_result_records_variance_and_convention(self):
        result = deflated_sharpe_ratio(DeflatedSharpeTest.RETURNS, n_trials=3)
        self.assertAlmostEqual(result.trial_sharpe_variance, 1 / 249, places=15)
        self.assertEqual(result.trial_sharpe_variance_source, NULL_TRIAL_VARIANCE)
        self.assertEqual(result.kurtosis_convention, "non-excess (normal = 3)")
        supplied = deflated_sharpe_ratio(DeflatedSharpeTest.RETURNS, n_trials=3, trial_sharpe_variance=0.01)
        self.assertEqual((supplied.trial_sharpe_variance, supplied.trial_sharpe_variance_source), (0.01, "supplied"))


class ResolveTrialsTest(unittest.TestCase):
    UNIT = "per_bar"

    def resolve(self, **kwargs):
        options = {"plan_trials": 7, "plan_trial_sharpes": (), "sample_length": 101, "sharpe_unit": self.UNIT}
        options.update(kwargs)
        return resolve_trials(**options)

    def test_plan_only_uses_null_floor(self):
        resolution = self.resolve(plan_trial_sharpes=(0.1, 0.2))
        self.assertEqual((resolution.n_trials, resolution.n_trials_plan, resolution.n_trials_source), (7, 7, "plan"))
        self.assertAlmostEqual(resolution.trial_sharpe_variance, 0.01, places=15)
        self.assertIn(NULL_TRIAL_VARIANCE, resolution.trial_sharpe_variance_method)
        self.assertTrue(any("fewer than" in note for note in resolution.notes))

    def test_dispersed_plan_trials_raise_the_variance(self):
        sharpes = (-0.3, -0.1, 0.0, 0.1, 0.3)
        expected = cross_sectional_variance(sharpes)
        self.assertAlmostEqual(expected, 0.05, places=15)
        self.assertAlmostEqual(self.resolve(plan_trial_sharpes=sharpes).trial_sharpe_variance, 0.05, places=15)

    def test_universe_count_is_used_and_plan_is_a_floor(self):
        bigger = self.resolve(universe=TrialUniverse(n_trials=250, source="registry"))
        self.assertEqual((bigger.n_trials, bigger.n_trials_source), (250, "universe:registry"))
        smaller = self.resolve(universe=TrialUniverse(n_trials=3))
        self.assertEqual(smaller.n_trials, 7)
        self.assertTrue(any("below the plan" in note for note in smaller.notes))

    def test_universe_sharpes_only_in_the_same_unit_and_bar_size(self):
        sharpes = (-0.5, -0.2, 0.0, 0.2, 0.5, 0.4)
        variance = cross_sectional_variance(sharpes)
        same = self.resolve(bar_seconds=900, universe=TrialUniverse(20, sharpes, self.UNIT, 900))
        self.assertAlmostEqual(same.trial_sharpe_variance, variance, places=15)
        other_unit = self.resolve(bar_seconds=900, universe=TrialUniverse(20, sharpes, "annualized", 900))
        other_bars = self.resolve(bar_seconds=900, universe=TrialUniverse(20, sharpes, self.UNIT, 3600))
        for resolution in (other_unit, other_bars):
            self.assertAlmostEqual(resolution.trial_sharpe_variance, 0.01, places=15)
            self.assertTrue(any("ignored" in note for note in resolution.notes))
            self.assertEqual(resolution.n_trials, 20)

    def test_invalid_universe(self):
        with self.assertRaises(ValueError):
            TrialUniverse(n_trials=0)
        with self.assertRaises(ValueError):
            TrialUniverse(n_trials=2, trial_sharpes=(math.nan,))
        with self.assertRaises(TypeError):
            self.resolve(universe={"n_trials": 9})


class SerializationTest(unittest.TestCase):
    def test_to_dict_is_json_serializable_and_versioned(self):
        self.assertEqual(OVERFITTING_VERSION, "astra-overfitting-1.1.0")
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
