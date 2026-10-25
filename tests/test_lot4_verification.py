"""Lot 4 independent verification (QA agent D, phase 2) against the integrated branch 9eae4a6.

Three groups:

* independent checks of the statistics (hand-computed PBO/CSCV cases, the DSR paper example
  recomputed with a separate implementation, the ASTRA selection score);
* new adversarial scenarios against the Lot 4 code (attempt registry tampering, trial count
  lowering, TrialUniverse units, the holdout reuse detector, mark-to-market information guards);
* checks on the integrator's edits to ``test_lot4_adversarial``.

Secure behaviour is asserted. Scenarios that fail on 9eae4a6 carry ``@unittest.expectedFailure``
and a ``# VERIFY FAIL N-n`` comment (severity in the comment). All data is SYNTHETIC.
"""

import inspect
import math
import random
import shutil
import sqlite3
import statistics
import unittest
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from _support import workspace_tempdir
from astra import attempts, holdout_reuse
from astra.anchoring import LocalFileAnchor, verify_store_against_anchor
from astra.attempts import AttemptLog
from astra.governance import Actor, Role
from astra.holdout_reuse import holdout_returns, shares_price_path
from astra.models import BacktestConfig
from astra.overfitting import (
    TrialUniverse,
    deflated_sharpe_from_moments,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    probability_of_backtest_overfitting,
    resolve_trials,
    selection_score_metric,
)
from astra.registry import ModelRegistry, PromotionCriteria, PromotionError
from astra.research_policy import (
    HOLDOUT_REUSE_POLICY,
    STATISTICAL_POLICY,
    HoldoutReusePolicy,
    reuse_policy_looser_than_floor,
)
from astra.storage import BacktestStore
from astra.strategy_validation import DSR_SHARPE_UNIT, assess_overfitting, holdout_evidence
from astra.validation import evaluate_walk_forward
from test_features import make_synthetic_bars
from test_lot4_adversarial import (
    AGENT,
    BARS,
    INDEPENDENT_BARS,
    PROTOCOL,
    TWO_CANDIDATES,
    WriteRefused,
    bound_manifest,
    evaluate,
    perturb_holdout,
    relabel,
    rewrite_json,
    score_holdout,
    sql,
    time_shift,
)

REVIEWER = Actor("reviewer-d2", Role.HUMAN_REVIEWER)
NORMAL = statistics.NormalDist()


# --- helpers -------------------------------------------------------------------------


def drop_all_triggers(path):
    db = sqlite3.connect(path)
    try:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
            db.execute(f"DROP TRIGGER {name}")
        db.commit()
    finally:
        db.close()


def reinitialize(path):
    """Recreate every trigger (an attacker restoring the schema after tampering)."""

    BacktestStore(path).initialize()
    ModelRegistry(path).initialize()


def rewrite_attempt_chain_without(path, protocol_id):
    """Consistently rewrite the attempt chain without one plan's rows (hashes, links, sqlite_sequence)."""

    drop_all_triggers(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in db.execute(f"SELECT * FROM {attempts.TABLE} ORDER BY seq")]
        keep = [row for row in rows if row["protocol_id"] != protocol_id]
        db.execute(f"DELETE FROM {attempts.TABLE}")
        previous = attempts.GENESIS_HASH
        columns = (*attempts._COLUMNS, "row_hash")
        for seq, row in enumerate(keep, start=1):
            content = {**row, "seq": seq, "prev_hash": previous}
            content.pop("row_hash")
            digest = attempts.row_hash(content)
            db.execute(
                f"INSERT INTO {attempts.TABLE} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                (*(content.get(name) for name in attempts._COLUMNS), digest),
            )
            previous = digest
        db.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (len(keep), attempts.TABLE))
        db.commit()
    finally:
        db.close()
    reinitialize(path)
    return len(rows) - len(keep)


def my_expected_max_sharpe(n, variance):
    """Independent implementation of DSR paper eq. 1 (zero-mean null)."""

    gamma = 0.5772156649015329
    if n == 1:
        return 0.0
    return math.sqrt(variance) * ((1 - gamma) * NORMAL.inv_cdf(1 - 1 / n) + gamma * NORMAL.inv_cdf(1 - 1 / (n * math.e)))


def my_dsr(sharpe, t, skew, kurt, n, variance):
    """Independent implementation of DSR paper eq. 2 (kurtosis non-excess)."""

    sr0 = my_expected_max_sharpe(n, variance)
    z = (sharpe - sr0) * math.sqrt(t - 1) / math.sqrt(1 - skew * sharpe + (kurt - 1) / 4 * sharpe**2)
    return sr0, NORMAL.cdf(z)


class TempStoreMixin(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        self.path = f"{self._tmp.__enter__()}/astra.sqlite3"
        self.store = BacktestStore(self.path)
        self.store.initialize()
        self.registry = ModelRegistry(self.path)
        self.registry.initialize()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def register_score_record(self, bars=BARS, candidates=None, version="1"):
        report = evaluate(bars=bars, candidates=candidates, store=self.store)
        self.registry.register_candidate(actor=AGENT, model_id="wf", version=version,
                                         manifest=bound_manifest(report), criteria=PromotionCriteria())
        holdout = score_holdout(self.store, report, bars=bars, candidates=candidates)
        evaluation_id = self.store.holdout_for_protocol(report.plan_sha256)["evaluation_id"]
        self.registry.record_evaluation(actor=AGENT, model_id="wf", version=version, kind="holdout",
                                        evidence=holdout_evidence(holdout), holdout_evaluation_id=evaluation_id)
        return report, holdout, evaluation_id


# --- 1. independent math checks --------------------------------------------------------


class PboHandExampleTests(unittest.TestCase):
    """CSCV with S = 2 slices of 2 rows: C(2, 1) = 2 combinations, computed by hand below."""

    # v0 = [1, 3 | 2, 2.5], v1 = [2, 2.5 | 1, 3]: each variant is best in one block and worst in the other.
    # Sharpe (mean / sample stdev): [1, 3] -> 2 / 1.4142 = 1.414; [2, 2.5] -> 2.25 / 0.3536 = 6.364.
    # IS = block 0: v1 best (6.364); OOS block 1: v1 1.414 < v0 6.364 -> rank 1, w = 1/3, logit < 0 (overfit).
    # IS = block 1: v0 best; OOS block 0: v0 1.414 < v1 6.364 -> overfit. PBO = 2/2 = 1.
    SWAP: ClassVar[list] = [[1.0, 2.0], [3.0, 2.5], [2.0, 1.0], [2.5, 3.0]]

    def test_symmetric_swap_is_fully_overfit_with_sharpe_and_selection_score(self):
        self.assertEqual(probability_of_backtest_overfitting(self.SWAP, n_slices=2).pbo, 1.0)
        # Selection score with cash drawdown (initial_equity=None): block sums 4 vs 4.5, no drawdown: same ranking.
        result = probability_of_backtest_overfitting(self.SWAP, n_slices=2, metric=selection_score_metric(None))
        self.assertEqual(result.pbo, 1.0)
        self.assertAlmostEqual(result.logits[0], math.log(0.5), places=12)  # w = 1/3 -> ln(1/2)

    def test_persistent_winner_is_not_overfit(self):
        # v1 dominates in both blocks: rank 2 of 2 each time, w = 2/3, logit ln 2 > 0. PBO = 0.
        matrix = [[1.0, 2.0], [3.0, 2.5], [-1.0, 0.5], [-2.0, 1.0]]
        result = probability_of_backtest_overfitting(matrix, n_slices=2)
        self.assertEqual(result.pbo, 0.0)
        for logit in result.logits:
            self.assertAlmostEqual(logit, math.log(2.0), places=12)

    # Ties, selection score with initial_equity=None (sum minus cash drawdown):
    # v0 = [1, 1 | 0, 5], v1 = [1, 1 | 3, 0]. Block 0: v0 2, v1 2 (tie). Block 1: v0 path 0, 5 -> 5;
    # v1 path 3, 0 -> sum 3, no drawdown below the running peak -> 3.
    TIE: ClassVar[list] = [[1.0, 1.0], [1.0, 1.0], [0.0, 3.0], [5.0, 0.0]]

    def test_in_sample_tie_selects_worst_out_of_sample(self):
        # IS = block 0: tie 2 = 2; conservative rule picks the worse OOS variant v1 (3 < 5) -> rank 1, overfit.
        # IS = block 1: v0 best (5); OOS block 0: v0 2 = v1 2 -> average rank 1.5, w = 0.5, logit 0 -> overfit.
        # PBO = 1. The old lowest-index rule would pick v0 in the first combination (rank 2): PBO = 0.5.
        result = probability_of_backtest_overfitting(self.TIE, n_slices=2, metric=selection_score_metric(None))
        self.assertEqual(result.pbo, 1.0)
        self.assertEqual(result.logits[1], 0.0)

    def test_eligibility_restricts_the_in_sample_choice(self):
        # v1 has no trade in block 0, so in IS = block 0 only v0 may be selected: OOS v0 5 > 3 -> not overfit.
        # IS = block 1 unchanged (overfit). PBO = 1/2.
        counts = [[1, 0], [1, 0], [1, 1], [1, 1]]
        result = probability_of_backtest_overfitting(self.TIE, n_slices=2, metric=selection_score_metric(None),
                                                     eligibility=counts, minimum_eligibility=1)
        self.assertEqual(result.pbo, 0.5)
        self.assertEqual(result.n_abstained, 0)

    def test_selection_score_matches_hand_computation(self):
        # Equity 10 000; P&L path +100, -300, +50: peak 10 100, trough 9 800 -> drawdown 300 / 10 100 = 2.9703 %.
        # Score = net (-150) - 10 000 * 2.9703 / 100 = -447.0297.
        metric = selection_score_metric(10_000.0)
        self.assertAlmostEqual(metric([100.0, -300.0, 50.0]), -150.0 - 10_000.0 * (300.0 / 10_100.0), places=9)


class DsrPaperExampleTests(unittest.TestCase):
    """Bailey and Lopez de Prado (2014), numerical example, recomputed with an independent implementation."""

    SR = 2.5 / math.sqrt(250)
    VARIANCE = 0.5 / 250
    ARGS: ClassVar[dict] = {"n_observations": 1250, "skewness": -3.0, "kurtosis": 10.0}

    def test_independent_implementation_reproduces_the_paper(self):
        sr0, dsr = my_dsr(self.SR, 1250, -3.0, 10.0, 100, self.VARIANCE)
        self.assertAlmostEqual(sr0, 0.1132, places=4)
        self.assertAlmostEqual(dsr, 0.9004, places=4)
        self.assertAlmostEqual(my_dsr(self.SR, 1250, -3.0, 10.0, 46, self.VARIANCE)[1], 0.9505, places=4)

    def test_library_agrees_with_the_independent_implementation(self):
        for n in (1, 2, 7, 46, 100, 1000):
            with self.subTest(n=n):
                expected = my_dsr(self.SR, 1250, -3.0, 10.0, n, self.VARIANCE)
                actual = deflated_sharpe_from_moments(sharpe=self.SR, n_trials=n, trial_sharpe_variance=self.VARIANCE,
                                                      **self.ARGS)
                self.assertAlmostEqual(actual[0], expected[0], places=12)
                self.assertAlmostEqual(actual[1], expected[1], places=12)
                self.assertAlmostEqual(expected_maximum_sharpe(n, self.VARIANCE), expected[0], places=12)

    def test_dsr_on_a_series_uses_population_moments_and_sample_stdev(self):
        rng = random.Random(17)
        returns = [0.001 + 0.01 * rng.gauss(0, 1) for _ in range(500)]
        mean = statistics.fmean(returns)
        sharpe = mean / statistics.stdev(returns)
        m2 = sum((x - mean) ** 2 for x in returns) / len(returns)
        skew = sum((x - mean) ** 3 for x in returns) / len(returns) / m2**1.5
        kurt = sum((x - mean) ** 4 for x in returns) / len(returns) / m2**2
        result = deflated_sharpe_ratio(returns, n_trials=10, trial_sharpe_variance=1 / 499)
        self.assertAlmostEqual(result.probability, my_dsr(sharpe, 500, skew, kurt, 10, 1 / 499)[1], places=12)

    def test_more_trials_never_raise_the_probability(self):
        rng = random.Random(4)
        returns = [0.002 + 0.01 * rng.gauss(0, 1) for _ in range(200)]
        values = [deflated_sharpe_ratio(returns, n_trials=n).probability for n in (1, 2, 5, 20, 100, 1000)]
        self.assertEqual(values, sorted(values, reverse=True))


# --- 2. integrator edits to test_lot4_adversarial ----------------------------------------


class IntegratorEditChecks(unittest.TestCase):
    def test_understated_count_gives_exactly_the_honest_dsr(self):
        # s44 now accepts "not above the honest value"; the floor should make them identical.
        rng = random.Random(5)
        matrix = [[rng.gauss(0.0, 1.0) for _ in range(7)] for _ in range(32)]
        returns = [0.002 + 0.01 * rng.gauss(0.0, 1.0) for _ in range(60)]
        labels = [f"p{i}" for i in range(32)]
        honest = assess_overfitting(matrix, labels, variants_tested=7, dsr_returns=returns)
        understated = assess_overfitting(matrix, labels, variants_tested=1, dsr_returns=returns)
        self.assertEqual(understated.n_trials_used, 7)
        self.assertEqual(understated.deflated_sharpe_probability, honest.deflated_sharpe_probability)

    def test_fixture_statistics_record_their_inputs(self):
        # s31/s37 now accept a COMPLETED fixture; the published numbers must at least carry the store-wide N.
        with workspace_tempdir() as temp:
            store = BacktestStore(f"{temp}/astra.sqlite3")
            store.initialize()
            holdout = score_holdout(store, evaluate(store=store))
        overfitting = holdout.overfitting
        if overfitting.status == "COMPLETED":
            self.assertGreaterEqual(overfitting.n_trials_used, 7)
            self.assertEqual(overfitting.trial_resolution["n_trials_source"], "universe:attempt_registry")
            self.assertEqual(overfitting.pbo_result["n_variants"], 7)
            self.assertEqual(holdout_evidence(holdout)["variants_tested"], overfitting.n_trials_used)


# --- 3. attempt registry tampering ---------------------------------------------------------


class AttemptRegistryTamperTests(TempStoreMixin):
    def test_api_cannot_update_delete_or_insert_unlinked_rows(self):
        evaluate(store=self.store)
        for statement in (f"UPDATE {attempts.TABLE} SET trial_sharpe = 0", f"DELETE FROM {attempts.TABLE}"):
            with self.subTest(statement=statement), self.assertRaises(WriteRefused):
                sql(self.path, statement)
        db = sqlite3.connect(self.path)
        try:
            row = db.execute(f"SELECT * FROM {attempts.TABLE} ORDER BY seq LIMIT 1").fetchone()
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute(f"INSERT INTO {attempts.TABLE} VALUES ({', '.join('?' for _ in row)})",
                           (10_000, "e", *row[2:-1], "unlinked-hash"))
        finally:
            db.close()

    def test_partial_delete_with_triggers_dropped_is_detected_at_promotion(self):
        self.register_score_record()
        drop_all_triggers(self.path)
        sql(self.path, f"DELETE FROM {attempts.TABLE} WHERE seq = 3")
        reinitialize(self.path)
        self.assertFalse(attempts.verify_attempt_chain(self.path).ok)
        self.assertIn("attempt_log_tampered", self.registry.check_promotion("wf", "1"))

    def test_edited_row_with_triggers_dropped_is_detected(self):
        self.register_score_record()
        drop_all_triggers(self.path)
        sql(self.path, f"UPDATE {attempts.TABLE} SET trial_sharpe = 9.9 WHERE kind = 'walk_forward_run'")
        reinitialize(self.path)
        self.assertIn("attempt_log_tampered", self.registry.check_promotion("wf", "1"))

    # FIXED in round 2 (was VERIFY FAIL N-2): a chain rewritten without an explored plan is refused with
    # attempt_log_missing_for_sealed_plan (the evidence itself still declares only 2 variants).
    def test_consistent_chain_rewrite_cannot_hide_an_explored_plan(self):
        explored = evaluate(store=self.store)
        self.assertGreater(rewrite_attempt_chain_without(self.path, explored.plan_sha256), 0)
        self.assertTrue(attempts.verify_attempt_chain(self.path).ok)
        _report, holdout, _ = self.register_score_record(bars=INDEPENDENT_BARS, candidates=TWO_CANDIDATES)
        failures = set(self.registry.check_promotion("wf", "1"))
        self.assertTrue(failures & {"attempt_log_tampered", "attempt_log_missing_for_plan", "trial_count_understated",
                                    "attempt_log_missing_for_sealed_plan"},
                        f"explored plan hidden; evidence variants_tested={holdout_evidence(holdout)['variants_tested']}; "
                        f"failures={sorted(failures)}")

    # VERIFY FAIL N-2b (Medium): the chain head snapshotted into the evidence at record time is never
    # checked; a consistent rewrite after recording passes as an intact chain.
    def test_rewrite_after_recording_breaks_the_snapshotted_head(self):
        evaluate(store=self.store)  # exploration on BARS
        self.register_score_record(bars=INDEPENDENT_BARS, candidates=TWO_CANDIDATES)
        first_protocol = sql(self.path, "SELECT protocol_id FROM research_protocols ORDER BY sealed_at LIMIT 1")[0][0]
        rewrite_attempt_chain_without(self.path, first_protocol)
        self.assertIn("attempt_log_tampered", self.registry.check_promotion("wf", "1"))

    # VERIFY FAIL N-3 (Medium; was D-1): the stored holdout report is protected by triggers only. With the
    # triggers dropped, report and evidence forged consistently and the triggers recreated, the version is
    # promoted to CHAMPION, although the chained holdout attempt still holds the genuine evidence metrics.
    def test_forged_report_is_caught_by_the_chained_holdout_metrics(self):
        _report, _holdout, evaluation_id = self.register_score_record()
        drop_all_triggers(self.path)

        def improve(report):
            report["selected_candidate"].update(trade_count=120, net_pnl=5_000.0, profit_factor=3.0,
                                                max_drawdown_pct=1.0)
            for scenario in report["cost_sensitivity"]:
                if scenario["selected_candidate"]:
                    scenario["selected_candidate"]["net_pnl"] = 4_000.0
            report["edge_vs_baseline"] = 4_500.0
            report["overfitting"].update(pbo=0.05, deflated_sharpe_probability=0.99)
            return report

        forged = rewrite_json(self.path, "holdout_evaluations", "report_json", "evaluation_id", evaluation_id, improve)
        row = sql(self.path, "SELECT evaluation_id FROM model_evaluations WHERE version = '1'")[0][0]
        rewrite_json(self.path, "model_evaluations", "evidence_json", "evaluation_id", row,
                     lambda payload: {**payload, **holdout_evidence(forged)})
        reinitialize(self.path)
        with self.assertRaises(PromotionError):
            self.registry.promote(actor=REVIEWER, model_id="wf", version="1", reason="adversarial")

    # VERIFY FAIL N-5 (Medium; was D-7): with triggers dropped, deleting the seal rows re-opens the holdout;
    # the chained holdout attempt (with its holdout_content_sha256) is not consulted when sealing.
    def test_deleted_seal_rows_are_caught_by_the_attempt_log(self):
        score_holdout(self.store, evaluate(store=self.store))
        drop_all_triggers(self.path)
        for table in ("holdout_evaluations", "holdout_signatures", "research_protocols"):
            sql(self.path, f"DELETE FROM {table}")
        reinitialize(self.path)
        protocol = replace(PROTOCOL, minimum_candidate_trades=0)
        with self.assertRaises(ValueError):
            report = evaluate(protocol=protocol, store=self.store)
            score_holdout(self.store, report, protocol=protocol)


# --- 4. trial count lowering --------------------------------------------------------------


class TrialCountLoweringTests(TempStoreMixin):
    def test_exploration_after_the_holdout_blocks_promotion(self):
        self.register_score_record(bars=INDEPENDENT_BARS, candidates=TWO_CANDIDATES)
        evaluate(store=self.store)  # 7 more variants on the same instrument, after the evidence was recorded
        self.assertIn("trial_count_understated", self.registry.check_promotion("wf", "1"))

    def test_holdout_dsr_uses_the_store_wide_count(self):
        evaluate(store=self.store)
        _report, holdout, _ = self.register_score_record(bars=INDEPENDENT_BARS, candidates=TWO_CANDIDATES)
        self.assertGreaterEqual(holdout_evidence(holdout)["variants_tested"], 9)
        if holdout.overfitting.n_trials_used is not None:
            self.assertGreaterEqual(holdout.overfitting.n_trials_used, 9)

    def test_legacy_walk_forward_attempts_count_for_the_instrument(self):
        legacy = time_shift(make_synthetic_bars(400, seed=21), days=60)
        evaluate_walk_forward(legacy, (BacktestConfig(fast_window=5, slow_window=13),
                                       BacktestConfig(fast_window=8, slow_window=21)),
                              hypothesis="legacy grid", protocol=PROTOCOL, store=self.store)
        report = evaluate(store=self.store)
        self.assertGreaterEqual(attempts.trial_statistics_for_protocol(self.path, report.plan_sha256).count, 9)

    # VERIFY FAIL N-6 (Low, latent: only EURUSD is tradable in this build): the trial scope is the literal
    # instrument label, so trials explored as EURUSD do not count for a plan labelled EURUSD.X.
    @unittest.expectedFailure
    def test_new_instrument_label_does_not_reset_the_count(self):
        evaluate(store=self.store)
        report = evaluate(bars=relabel(INDEPENDENT_BARS), candidates=TWO_CANDIDATES, store=self.store)
        self.assertGreaterEqual(attempts.trial_statistics_for_protocol(self.path, report.plan_sha256).count, 9)

    # RESIDUAL (D-11 class, documented): exploration in another database file is not counted.
    @unittest.expectedFailure
    def test_exploration_in_another_store_is_counted(self):
        with workspace_tempdir() as other:
            scratch = BacktestStore(f"{other}/scratch.sqlite3")
            scratch.initialize()
            evaluate(store=scratch)  # 7 variants, sealed elsewhere
        report = evaluate(bars=INDEPENDENT_BARS, candidates=TWO_CANDIDATES, store=self.store)
        self.assertGreaterEqual(attempts.trial_statistics_for_protocol(self.path, report.plan_sha256).count, 9)


# --- 5. DSR / TrialUniverse units -----------------------------------------------------------


def hourly(bars, days=0):
    start = bars[0].timestamp + timedelta(days=days)
    return [replace(bar, timestamp=start + timedelta(hours=index)) for index, bar in enumerate(bars)]


class TrialUniverseUnitTests(TempStoreMixin):
    def test_universe_in_another_unit_or_bar_size_is_ignored(self):
        plan = [0.01 * i for i in range(5)]
        base = {"plan_trials": 5, "plan_trial_sharpes": plan, "sample_length": 100, "sharpe_unit": DSR_SHARPE_UNIT,
                "bar_seconds": 900}
        huge = tuple(float(i) for i in range(10))
        for universe in (TrialUniverse(n_trials=10, trial_sharpes=huge, sharpe_unit="per_trade", bar_seconds=900),
                         TrialUniverse(n_trials=10, trial_sharpes=huge, sharpe_unit=DSR_SHARPE_UNIT, bar_seconds=3600)):
            with self.subTest(universe=universe):
                resolution = resolve_trials(**base, universe=universe)
                self.assertEqual(resolution.n_trials, 10)  # the count still applies
                self.assertNotIn("universe", resolution.trial_sharpe_variance_method)

    def test_universe_cannot_lower_the_count_or_the_variance(self):
        plan = [0.1, -0.2, 0.3, 0.05, -0.1, 0.2]
        alone = resolve_trials(plan_trials=6, plan_trial_sharpes=plan, sample_length=50, sharpe_unit=DSR_SHARPE_UNIT)
        tiny = TrialUniverse(n_trials=1, trial_sharpes=(0.0,) * 6, sharpe_unit=DSR_SHARPE_UNIT)
        merged = resolve_trials(plan_trials=6, plan_trial_sharpes=plan, sample_length=50, sharpe_unit=DSR_SHARPE_UNIT,
                                universe=tiny)
        self.assertEqual(merged.n_trials, 6)
        self.assertGreaterEqual(merged.trial_sharpe_variance, alone.trial_sharpe_variance)

    # VERIFY FAIL N-7 (Low, conservative direction): the store universe labels every recorded trial Sharpe
    # with the current plan's unit and bar size. Legacy walk-forward attempts record per-trade Sharpes and an
    # H1 plan records per-H1-bar Sharpes; both are mixed into the M15 per-bar variance.
    @unittest.expectedFailure
    def test_store_universe_excludes_sharpes_of_other_units_and_bar_sizes(self):
        legacy = time_shift(make_synthetic_bars(400, seed=21), days=60)
        evaluate_walk_forward(legacy, (BacktestConfig(fast_window=5, slow_window=13),
                                       BacktestConfig(fast_window=8, slow_window=21)),
                              hypothesis="legacy grid", protocol=PROTOCOL, store=self.store)
        evaluate(bars=hourly(make_synthetic_bars(400, seed=12), days=120), store=self.store)
        report = evaluate(store=self.store)
        own = sum(1 for state in AttemptLog(self.path).attempts(protocol_id=report.plan_sha256)
                  if state["kind"] == attempts.KIND_RUN and state.get("trial_sharpe") is not None)
        universe = attempts.trial_statistics_for_protocol(self.path, report.plan_sha256)
        self.assertLessEqual(len(universe.trial_sharpes), own)


# --- 6. holdout reuse detector ------------------------------------------------------------


def synthetic_holdout(seed, bars=40):
    closes = [bar.close for bar in make_synthetic_bars(400, seed=seed)[-bars - 1:]]
    return holdout_returns(closes)


class ReuseDetectorTests(TempStoreMixin):
    def test_no_false_positive_between_independent_dense_holdouts(self):
        series = [synthetic_holdout(seed) for seed in range(1, 31)]
        hits = [(i, j) for i in range(len(series)) for j in range(i + 1, len(series))
                if shares_price_path(series[i], series[j])]
        self.assertEqual(hits, [])

    def test_independent_datasets_are_all_sealed_end_to_end(self):
        for index, seed in enumerate((11, 12, 13, 17, 21)):
            bars = time_shift(make_synthetic_bars(400, seed=seed), days=30 * (index + 1))
            with self.subTest(seed=seed):
                evaluate(bars=bars, store=self.store)

    def test_reuse_with_rounding_noise_and_inversion_is_detected(self):
        base = synthetic_holdout(7)
        noisy = [value + 1e-7 * ((i % 3) - 1) for i, value in enumerate(base)]
        self.assertTrue(shares_price_path(noisy, base))
        self.assertTrue(shares_price_path([-value for value in base], base))
        self.assertTrue(shares_price_path(base[5:], base))

    def test_reuse_end_to_end_perturbed_and_shifted(self):
        score_holdout(self.store, evaluate(store=self.store))
        with self.assertRaisesRegex(ValueError, "price path"):
            evaluate(bars=time_shift(perturb_holdout(BARS, delta=2e-5), days=400), store=self.store)

    # VERIFY FAIL N-8 (Low, availability; fails closed): independent holdouts that are mostly flat (forward-filled
    # or quiet bars, 10% non-zero returns) match by chance: the MAD scale collapses and a single coincident
    # non-zero return makes a window correlation of about 1. About a quarter of random pairs are refused.
    def test_sparse_independent_holdouts_do_not_match(self):
        rng = random.Random(1)

        def sparse():
            return [round(rng.gauss(0, 1e-4), 12) if rng.random() < 0.1 else 0.0 for _ in range(40)]

        hits = sum(shares_price_path(sparse(), sparse()) for _ in range(100))
        self.assertLessEqual(hits, 1)

    # RESIDUAL (documented in holdout_reuse): a copy with noise comparable to the returns is not detected.
    @unittest.expectedFailure
    def test_reuse_with_noise_at_half_the_return_scale_is_detected(self):
        base = synthetic_holdout(7)
        scale = statistics.pstdev(base)
        rng = random.Random(3)
        noisy = [value + 0.5 * scale * rng.gauss(0, 1) for value in base]
        self.assertTrue(shares_price_path(noisy, base))


# --- 7. mark-to-market information guards ---------------------------------------------------


class MarkToMarketInformationTests(unittest.TestCase):
    # VERIFY FAIL N-1 (Medium-High for the statistics, Medium for promotion because min_trades still applies):
    # with mark-to-market per-bar returns, ONE position held for 60 bars passes the "30 non-zero returns" DSR
    # guard and the "non-zero periods" PBO guard: status COMPLETED, PBO about 0.01, DSR 1.0, from one trade.
    # The guards count periods, not independent decisions (trades). Regression of the intent of D-19.
    def test_single_held_trade_is_low_information(self):
        rng = random.Random(9)
        matrix = [[rng.gauss(0, 5) for _ in range(3)] + [rng.gauss(1.0, 3)] for _ in range(64)]
        counts = [[rng.choice((0, 1)) for _ in range(3)] + [1 if row == 63 else 0] for row in range(64)]
        returns = [0.0] * 100 + [0.0004 + rng.gauss(0, 0.0003) for _ in range(60)]
        result = assess_overfitting(matrix, [f"p{i}" for i in range(64)], variants_tested=4, dsr_returns=returns,
                                    trade_counts=counts, minimum_candidate_trades=0, initial_equity=10_000.0)
        self.assertIsNone(result.deflated_sharpe_probability, result.errors)
        self.assertIsNone(result.pbo, result.errors)

    def test_fixture_statistics_carry_trade_counts(self):
        with workspace_tempdir() as temp:
            store = BacktestStore(f"{temp}/astra.sqlite3")
            store.initialize()
            report = evaluate(store=store)
        overfitting = report.overfitting
        self.assertEqual(len(overfitting.trade_count_matrix), len(overfitting.performance_matrix))


# =====================================================================================================
# Phase 3 (QA agent D) against 0ef781b: research policy, reuse verdicts, re-derivation, evidence anchor.
# Expected failures are marked ``# PHASE3 FAIL P-n`` with a severity.
# =====================================================================================================


class AnchoredStoreMixin(unittest.TestCase):
    """A store and registry bound to a LocalFileAnchor, on the full promotion path (bars re-derived)."""

    def setUp(self):
        self._tmp = workspace_tempdir()
        self.temp = self._tmp.__enter__()
        self.path = f"{self.temp}/astra.sqlite3"
        self.anchor = LocalFileAnchor(f"{self.temp}/anchor.jsonl")
        self.store = BacktestStore(self.path, anchor=self.anchor)
        self.store.initialize()
        self.registry = ModelRegistry(self.path, anchor=self.anchor)
        self.registry.initialize()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def register_score_record(self, bars=BARS, version="1"):
        report = evaluate(bars=bars, store=self.store)
        self.registry.register_candidate(actor=AGENT, model_id="wf", version=version,
                                         manifest=bound_manifest(report), criteria=PromotionCriteria())
        holdout = score_holdout(self.store, report, bars=bars)
        evaluation_id = self.store.holdout_for_protocol(report.plan_sha256)["evaluation_id"]
        self.registry.record_evaluation(actor=AGENT, model_id="wf", version=version, kind="holdout",
                                        evidence=holdout_evidence(holdout), holdout_evaluation_id=evaluation_id)
        return report, holdout, evaluation_id

    def failures(self, bars=BARS, version="1"):
        return set(self.registry.check_promotion("wf", version, holdout_bars=bars))


INTEGRITY_CODES = {
    "evidence_anchor_missing", "evidence_anchor_unreadable", "holdout_not_anchored", "holdout_reuse_check_missing",
    "holdout_reuse_suspected", "holdout_reuse_undetermined", "holdout_reuse_policy_below_floor",
    "holdout_not_rederived_from_market_data", "holdout_report_differs_from_rederivation", "attempt_log_tampered",
}


def integrity(failures):
    return {item for item in failures if item in INTEGRITY_CODES or item.startswith("evidence_anchor")
            or item.startswith("holdout_rederivation")}


def cli_help_texts():
    from astra.cli import build_parser

    parser = build_parser()
    texts = [parser.format_help()]
    pending = [parser]
    while pending:
        current = pending.pop()
        for action in current._actions:
            for sub in (getattr(action, "choices", None) or {}).values() if isinstance(
                    getattr(action, "choices", None), dict) else ():
                texts.append(sub.format_help())
                pending.append(sub)
    return " ".join(texts).lower()


class ResearchPolicyTests(AnchoredStoreMixin):
    def test_policy_objects_reject_inconsistent_thresholds_and_are_frozen(self):
        for kwargs in ({"suspect_abs_correlation": 0.99}, {"conflict_abs_correlation": 1.0},
                       {"min_holdout_returns": 4}, {"min_window": 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                HoldoutReusePolicy(**kwargs)
        with self.assertRaises((AttributeError, TypeError)):
            HOLDOUT_REUSE_POLICY.conflict_abs_correlation = 0.5
        self.assertEqual((HOLDOUT_REUSE_POLICY.conflict_abs_correlation, HOLDOUT_REUSE_POLICY.suspect_abs_correlation,
                          HOLDOUT_REUSE_POLICY.min_holdout_returns, STATISTICAL_POLICY.min_dsr_trades),
                         (0.98, 0.95, 16, 30))

    def test_every_looser_or_malformed_recorded_threshold_is_flagged(self):
        floor = asdict(HOLDOUT_REUSE_POLICY)
        looser = {"conflict_abs_correlation": 0.99, "suspect_abs_correlation": 0.97, "min_holdout_returns": 10,
                  "min_informative_share": 0.7, "informative_threshold": 0.5, "match_tolerance": 0.01,
                  "conflict_match_share": 0.9}
        for name, value in looser.items():
            with self.subTest(name=name):
                self.assertIn(name, reuse_policy_looser_than_floor({**floor, name: value}))
        for value in (True, "0.98", None):
            with self.subTest(value=value):
                self.assertIn("conflict_abs_correlation",
                              reuse_policy_looser_than_floor({**floor, "conflict_abs_correlation": value}))
        self.assertEqual(reuse_policy_looser_than_floor(floor), ())

    def test_no_cli_flag_or_seal_argument_exposes_a_threshold(self):
        text = cli_help_texts()
        self.assertIn("evaluate-strategies", text)
        for word in ("conflict", "suspect", "min-dsr", "dsr-trades", "correlation", "policy"):
            self.assertNotIn(word, text)
        parameters = inspect.signature(BacktestStore.seal_research_protocol).parameters
        self.assertFalse({"policy", "threshold"} & {name.lower() for name in parameters})

    def test_monkeypatched_seal_policy_is_recorded_and_blocks_promotion(self):
        loose = replace(HOLDOUT_REUSE_POLICY, conflict_abs_correlation=0.999, suspect_abs_correlation=0.999)
        with patch("astra.storage.HOLDOUT_REUSE_POLICY", loose):
            self.register_score_record()
        self.assertIn("holdout_reuse_policy_below_floor", self.failures())

    # PHASE3 FAIL P-4 (Low; needs in-process code): the statistical floors are module constants read at run
    # time and the policy actually applied is not recorded in the plan, report or evidence. Lowering
    # MIN_DSR_TRADES / MIN_PBO_TRADES_PER_SLICE in-process publishes statistics from a handful of trades and
    # promotion cannot tell.
    @unittest.expectedFailure
    def test_lowered_statistical_floor_is_visible_at_promotion(self):
        with patch("astra.strategy_validation.MIN_DSR_TRADES", 0), \
                patch("astra.strategy_validation.MIN_PBO_TRADES_PER_SLICE", 0):
            _report, holdout, _ = self.register_score_record()
        self.assertLess(holdout.overfitting.dsr_trade_count, STATISTICAL_POLICY.min_dsr_trades)
        self.assertIsNotNone(holdout.overfitting.pbo)
        failures = self.failures()
        self.assertTrue(failures & {"overfitting_check_missing", "statistical_policy_below_floor"}, failures)


def _seal_returns(store, returns, label, start_year):
    from astra.validation import _canonical_json, _sha256

    manifest = {"dataset_identity_sha256": label * 64, "instrument": "EURUSD",
                "holdout": {"start": f"{start_year}-01-01T00:00:00+00:00", "end": f"{start_year}-01-02T00:00:00+00:00",
                            "content_sha256": holdout_reuse.content_sha256(returns)}}
    protocol_id = _sha256(_canonical_json(manifest))
    store.seal_research_protocol(protocol_id=protocol_id, dataset_identity_sha256=label * 64, protocol_version="t",
                                 plan_manifest=manifest, holdout_returns=returns)
    return protocol_id


def _suspect_copy(base):
    for seed in range(400):
        noise = random.Random(seed)
        candidate = [round(0.955 * v + 0.296 * noise.gauss(0, 1e-4), 12) for v in base]
        if holdout_reuse.assess_reuse(candidate, base).verdict == holdout_reuse.SUSPECT:
            return candidate
    raise AssertionError("no SUSPECT candidate found")


def _sparse(seed):
    rng = random.Random(seed)
    return [round(rng.gauss(0, 1e-4), 12) if rng.random() < 0.1 else 0.0 for _ in range(40)]


class ReuseVerdictPathTests(TempStoreMixin):
    def setUp(self):
        super().setUp()
        rng = random.Random(4)
        self.base = [round(rng.gauss(0, 1e-4), 12) for _ in range(40)]
        _seal_returns(self.store, self.base, "a", 2030)

    def test_conflict_is_refused_at_seal(self):
        with self.assertRaisesRegex(ValueError, "price path matches"):
            _seal_returns(self.store, [round(v + 1e-9, 12) for v in self.base], "b", 2031)

    def test_suspect_is_sealed_but_cannot_be_scored(self):
        protocol_id = _seal_returns(self.store, _suspect_copy(self.base), "c", 2032)
        self.assertEqual(self.store.reuse_check(protocol_id)["verdict"], holdout_reuse.SUSPECT)
        with self.assertRaisesRegex(ValueError, "suspect"):
            self.store.begin_holdout_evaluation(protocol_id=protocol_id, dataset_identity_sha256="c" * 64)

    def test_undetermined_is_sealed_but_cannot_be_scored(self):
        # Round 4: UNDETERMINED means the NEW holdout has too few moves to be compared at all (P-5 below:
        # a 10%-sparse holdout with 5 moves is now compared on its moves), so two moves in 40 bars.
        protocol_id = _seal_returns(self.store, _nearly_flat(), "d", 2033)
        self.assertEqual(self.store.reuse_check(protocol_id)["verdict"], holdout_reuse.UNDETERMINED)
        with self.assertRaisesRegex(ValueError, "undetermined"):
            self.store.begin_holdout_evaluation(protocol_id=protocol_id, dataset_identity_sha256="d" * 64)

    def test_verdict_rows_are_append_only(self):
        protocol_id = _seal_returns(self.store, _suspect_copy(self.base), "c", 2032)
        for statement in ("UPDATE holdout_reuse_checks SET verdict = 'CLEAR'", "DELETE FROM holdout_reuse_checks"):
            with self.subTest(statement=statement), self.assertRaises(WriteRefused):
                sql(self.path, statement)
        self.assertEqual(self.store.reuse_check(protocol_id)["verdict"], holdout_reuse.SUSPECT)

    # PHASE3 FAIL P-3 (Low-Medium; needs a DB write): scoring is refused only when a SUSPECT/UNDETERMINED row
    # exists. With the triggers dropped and the verdict row deleted, a suspect holdout is scored (leaked) again;
    # promotion still fails with holdout_reuse_check_missing, but the holdout information has leaked.
    @unittest.expectedFailure
    def test_missing_verdict_row_blocks_scoring(self):
        protocol_id = _seal_returns(self.store, _suspect_copy(self.base), "c", 2032)
        drop_all_triggers(self.path)
        sql(self.path, "DELETE FROM holdout_reuse_checks WHERE protocol_id = ?", (protocol_id,))
        reinitialize(self.path)
        with self.assertRaises(ValueError):
            self.store.begin_holdout_evaluation(protocol_id=protocol_id, dataset_identity_sha256="c" * 64)

    # FIXED IN LOT 4 ROUND 4 (was PHASE3 FAIL P-5, Low, availability): independent mostly-flat holdouts
    # (forward-filled or quiet bars) were always UNDETERMINED: sealed but never scored or promoted. They are now
    # compared on their moves; the calibration suite reports UNDETERMINED separately.
    def test_independent_sparse_holdout_is_clear(self):
        protocol_id = _seal_returns(self.store, _sparse(8), "d", 2033)
        self.assertEqual(self.store.reuse_check(protocol_id)["verdict"], holdout_reuse.CLEAR)


def _nearly_flat():
    # Two moves in 40 bars: fewer than min_informative_points, so it cannot be compared with anything.
    return [0.0] * 10 + [1e-4] + [0.0] * 20 + [-1e-4] + [0.0] * 8


def _quantized(closes):
    return holdout_returns([round(value, 5) for value in closes])


def _pairs(make, count):
    return [(_quantized(make(random.Random(2 * k))), _quantized(make(random.Random(2 * k + 1)))) for k in range(count)]


def _flagged(pairs, verdicts=(holdout_reuse.CONFLICT, holdout_reuse.SUSPECT)):
    return sum(holdout_reuse.assess_reuse(new, old).verdict in verdicts for new, old in pairs)


def _ticks(rng):
    price, closes = 1.1, [1.1]
    for _ in range(40):
        price += rng.choice((-1, 1)) * (2 if rng.random() < 0.15 else 1) * 1e-5 * (rng.random() < 0.8)
        closes.append(price)
    return closes


def _weekend(rng):
    price, closes = 1.1, [1.1]
    for index in range(40):
        if index == 25:
            price += rng.choice((-1, 1)) * 0.003
        elif not 10 <= index < 25:
            price += rng.gauss(0, 2e-4)
        closes.append(price)
    return closes


def _trend(drift, noise):
    def make(rng):
        price, closes = 1.1, [1.1]
        for _ in range(40):
            price += drift + rng.gauss(0, noise)
            closes.append(price)
        return closes

    return make


class ReuseFalsePositiveTests(unittest.TestCase):
    """Second statistic (match share) and correlation on real-like independent data, prices quantized to 1e-5."""

    # FIXED IN LOT 4 ROUND 4 (was PHASE3 FAIL P-9, Low, availability): tick-quantized independent random walks
    # (moves of 1 or 2 ticks of 1e-5) hit the match-share threshold (exactly 0.8) by chance: 1 of 600 pairs was
    # a CONFLICT. A match share now also needs a binomial chance probability (at the pair's own chance match
    # rate, Bonferroni-corrected by the spans tested) of at most 1e-4 (CONFLICT) or 1e-2 (SUSPECT).
    def test_tick_quantized_random_walks_are_not_flagged(self):
        self.assertEqual(_flagged(_pairs(_ticks, 150)), 0)

    def test_weekend_gap_paths_are_not_flagged(self):
        self.assertEqual(_flagged(_pairs(_weekend, 150)), 0)

    def test_realistic_trending_paths_are_not_flagged(self):
        self.assertEqual(_flagged(_pairs(_trend(1e-4, 3e-4), 100)), 0)
        self.assertEqual(_flagged(_pairs(_trend(2e-5, 1e-5), 100)), 0)

    # FIXED IN LOT 4 ROUND 4 (was PHASE3 FAIL P-6, Low, availability): two independent smooth trends with noise
    # below the 1e-5 tick (+2 ticks per bar, noise 0.2 tick) were a CONFLICT by match share in every pair.
    # Returns are now matched after removing each path's median (a shared drift is not a match), the robust
    # scale is floored at the match tolerance, and only an entirely constant old path is matched as a constant.
    def test_independent_sub_tick_trends_are_not_a_conflict(self):
        self.assertEqual(_flagged(_pairs(_trend(2e-5, 2e-6), 20), (holdout_reuse.CONFLICT,)), 0)

    # FIXED IN LOT 4 ROUND 4 (was PHASE3 FAIL P-7, Low, availability): long holdouts (400 returns) of independent
    # but serially correlated data were SUSPECT in a few percent of pairs (AR(1) 0.7: 6 of 200). A window pair
    # now also has to be correlated after prewhitening each path by its own lag-1 autocorrelation.
    def test_long_autocorrelated_independent_holdouts_are_not_flagged(self):
        rng = random.Random("ar1_0.7")

        def ar1():
            out, x = [], 0.0
            for _ in range(400):
                x = 0.7 * x + rng.gauss(0, 1e-4)
                out.append(x)
            return out

        self.assertEqual(sum(holdout_reuse.assess_reuse(ar1(), ar1()).verdict != holdout_reuse.CLEAR
                             for _ in range(200)), 0)


class RederivationAndAnchorTests(AnchoredStoreMixin):
    def test_clean_anchored_path_has_no_integrity_failure_and_anchor_is_not_production(self):
        self.register_score_record()
        self.assertEqual(integrity(self.failures()), set())
        self.assertFalse(self.anchor.production_grade)
        self.assertFalse(verify_store_against_anchor(self.path, self.anchor).production_grade)

    def test_no_anchor_and_no_bars_are_refused(self):
        self.register_score_record()
        with patch.dict("os.environ", {}, clear=True):
            bare = ModelRegistry(self.path)
        failures = set(bare.check_promotion("wf", "1"))
        self.assertIn("evidence_anchor_missing", failures)
        self.assertIn("holdout_not_rederived_from_market_data", failures)

    def test_forged_money_fields_are_caught_by_rederivation(self):
        # Strengthened N-3: the phase 2 test now also passes merely because the anchor and the bars are missing.
        _report, _holdout, evaluation_id = self.register_score_record()
        drop_all_triggers(self.path)

        def improve(report):
            if report.get("selected_candidate"):
                report["selected_candidate"].update(net_pnl=5_000.0, trade_count=120)
            report["edge_vs_baseline"] = 4_500.0
            return report

        forged = rewrite_json(self.path, "holdout_evaluations", "report_json", "evaluation_id", evaluation_id, improve)
        row = sql(self.path, "SELECT evaluation_id FROM model_evaluations WHERE version = '1'")[0][0]
        rewrite_json(self.path, "model_evaluations", "evidence_json", "evaluation_id", row,
                     lambda payload: {**payload, **holdout_evidence(forged)})
        reinitialize(self.path)
        self.assertIn("holdout_report_differs_from_rederivation", self.failures())

    # PHASE3 FAIL P-1 (Medium; needs a DB write): re-derivation compares only the money fields; the overfitting
    # statistics (pbo, deflated Sharpe) are taken from the stored report. Forging them in the report and the
    # evidence (triggers dropped, then recreated) removes the overfitting failures and adds none, although the
    # hash-chained holdout attempt still records the genuine values.
    @unittest.expectedFailure
    def test_forged_overfitting_statistics_are_caught(self):
        _report, _holdout, evaluation_id = self.register_score_record()
        before = self.failures()
        drop_all_triggers(self.path)

        def improve(report):
            report["overfitting"].update(status="COMPLETED", pbo=0.05, deflated_sharpe_probability=0.99)
            return report

        forged = rewrite_json(self.path, "holdout_evaluations", "report_json", "evaluation_id", evaluation_id, improve)
        row = sql(self.path, "SELECT evaluation_id FROM model_evaluations WHERE version = '1'")[0][0]
        rewrite_json(self.path, "model_evaluations", "evidence_json", "evaluation_id", row,
                     lambda payload: {**payload, **holdout_evidence(forged)})
        reinitialize(self.path)
        after = self.failures()
        self.assertTrue(before & {"overfitting_check_missing", "pbo_above_threshold", "deflated_sharpe_below_threshold"})
        self.assertTrue(after - before, f"forged statistics accepted: before={sorted(before)} after={sorted(after)}")

    def test_chain_divergence_is_reported_against_the_anchor(self):
        explored = evaluate(store=self.store)
        self.store.anchor_now()
        rewrite_attempt_chain_without(self.path, explored.plan_sha256)
        verification = verify_store_against_anchor(self.path, self.anchor)
        self.assertFalse(verification.ok)
        self.assertTrue({"attempt_chain_diverged", "store_behind_anchor", "experiment_missing"}
                        & set(verification.errors), verification.errors)
        with self.assertRaisesRegex(ValueError, "inconsistent with the evidence anchor"):
            self.store.require_anchor_consistency()

    def test_restored_old_backup_cannot_rescore(self):
        report, _holdout, _ = self.register_score_record()
        old = f"{self.temp}/old.sqlite3"
        with patch.dict("os.environ", {}, clear=True):
            old_store = BacktestStore(old)
        old_store.initialize()
        evaluate(store=old_store)  # same plan, holdout never scored: an older backup
        shutil.copyfile(old, self.path)
        restored = BacktestStore(self.path, anchor=self.anchor)
        with self.assertRaisesRegex(ValueError, "evidence anchor"):
            score_holdout(restored, report)

    # PHASE3 FAIL P-2 (Medium; documented limit of LocalFileAnchor, but not surfaced): promotion accepts any
    # anchor, production grade or not, and records nothing about it. A fresh database with a fresh local anchor
    # file re-scores a nudged, time-shifted copy of a holdout already consumed elsewhere, and its promotion
    # check shows no integrity failure and no production-grade warning.
    @unittest.expectedFailure
    def test_fresh_store_with_fresh_local_anchor_is_flagged(self):
        self.register_score_record()  # holdout consumed in the original store and anchor
        other = Path(self.temp) / "other"
        other.mkdir()
        anchor = LocalFileAnchor(other / "anchor.jsonl")
        store = BacktestStore(other / "astra.sqlite3", anchor=anchor)
        store.initialize()
        registry = ModelRegistry(other / "astra.sqlite3", anchor=anchor)
        registry.initialize()
        bars = time_shift(perturb_holdout(BARS, delta=2e-5), days=400)
        report = evaluate(bars=bars, store=store)
        registry.register_candidate(actor=AGENT, model_id="wf", version="1", manifest=bound_manifest(report),
                                    criteria=PromotionCriteria())
        holdout = score_holdout(store, report, bars=bars)
        registry.record_evaluation(actor=AGENT, model_id="wf", version="1", kind="holdout",
                                   evidence=holdout_evidence(holdout),
                                   holdout_evaluation_id=store.holdout_for_protocol(report.plan_sha256)["evaluation_id"])
        failures = set(registry.check_promotion("wf", "1", holdout_bars=bars))
        self.assertTrue(integrity(failures) | {item for item in failures if "production" in item}, failures)


class SealDeletionReuseTests(AnchoredStoreMixin):
    def wipe_seal_tables(self):
        drop_all_triggers(self.path)
        for table in ("holdout_evaluations", "holdout_signatures", "holdout_reuse_checks", "research_protocols"):
            sql(self.path, f"DELETE FROM {table}")
        reinitialize(self.path)

    def test_with_anchor_a_wiped_store_cannot_score_a_nudged_copy(self):
        score_holdout(self.store, evaluate(store=self.store))
        self.wipe_seal_tables()
        bars = time_shift(perturb_holdout(BARS, delta=2e-5), days=400)
        with self.assertRaises(ValueError):
            score_holdout(self.store, evaluate(bars=bars, store=self.store), bars=bars)

    # PHASE3 FAIL P-10 (Medium without an anchor, Low with one; needs a DB write): the attempt log keeps only
    # the exact holdout_content_sha256. After the seal tables are wiped (triggers dropped), a nudged and
    # time-shifted copy has no stored returns to be compared with and a different content hash: it is sealed
    # and scored again. Promotion still fails (evidence_anchor_missing), but the holdout leaks.
    @unittest.expectedFailure
    def test_without_anchor_a_wiped_store_cannot_score_a_nudged_copy(self):
        with patch.dict("os.environ", {}, clear=True):
            store = BacktestStore(f"{self.temp}/bare.sqlite3")
        store.initialize()
        path = store.path
        score_holdout(store, evaluate(store=store))
        drop_all_triggers(path)
        for table in ("holdout_evaluations", "holdout_signatures", "holdout_reuse_checks", "research_protocols"):
            sql(path, f"DELETE FROM {table}")
        reinitialize(path)
        bars = time_shift(perturb_holdout(BARS, delta=2e-5), days=400)
        with self.assertRaises(ValueError):
            score_holdout(store, evaluate(bars=bars, store=store), bars=bars)


class ExperimentIdTests(TempStoreMixin):
    def test_every_attempt_has_a_global_experiment_id(self):
        report = evaluate(store=self.store)
        score_holdout(self.store, report)
        legacy = time_shift(make_synthetic_bars(400, seed=21), days=60)
        evaluate_walk_forward(legacy, (BacktestConfig(fast_window=5, slow_window=13),
                                       BacktestConfig(fast_window=8, slow_window=21)),
                              hypothesis="legacy grid", protocol=PROTOCOL, store=self.store)
        states = AttemptLog(self.path).attempts()
        self.assertTrue(states)
        self.assertFalse([state for state in states if not state.get("experiment_id")])
        plan = {state["experiment_id"] for state in states
                if state.get("protocol_id") == report.plan_sha256 and state["kind"] != attempts.KIND_HOLDOUT}
        holdout = {state["experiment_id"] for state in states
                   if state.get("protocol_id") == report.plan_sha256 and state["kind"] == attempts.KIND_HOLDOUT}
        self.assertEqual(len(plan), 1)
        self.assertEqual(holdout, plan)


class LowInformationTests(unittest.TestCase):
    def test_fixture_publishes_no_statistic_below_the_floors(self):
        with workspace_tempdir() as temp:
            store = BacktestStore(f"{temp}/astra.sqlite3")
            store.initialize()
            holdout = score_holdout(store, evaluate(store=store))
        overfitting = holdout.overfitting
        if overfitting.dsr_trade_count is None or overfitting.dsr_trade_count < STATISTICAL_POLICY.min_dsr_trades:
            self.assertIsNone(overfitting.deflated_sharpe_probability)
            self.assertIsNone(holdout_evidence(holdout)["deflated_sharpe_probability"])
        if overfitting.pbo is None or overfitting.deflated_sharpe_probability is None:
            self.assertNotEqual(overfitting.status, "COMPLETED")

    def test_too_few_slices_or_observations_publish_nothing(self):
        rng = random.Random(2)
        for rows, returns in ((6, 60), (32, 10)):
            matrix = [[rng.gauss(0, 1) for _ in range(4)] for _ in range(rows)]
            counts = [[3] * 4 for _ in range(rows)]
            with self.subTest(rows=rows, returns=returns):
                try:
                    result = assess_overfitting(matrix, [f"p{i}" for i in range(rows)], variants_tested=4,
                                                dsr_returns=[0.001 + 0.01 * rng.gauss(0, 1) for _ in range(returns)],
                                                trade_counts=counts, dsr_trade_count=100, initial_equity=10_000.0)
                except ValueError:
                    continue
                self.assertNotEqual(result.status, "COMPLETED")
                self.assertTrue(result.pbo is None or result.deflated_sharpe_probability is None)

    # PHASE3 FAIL P-8 (Low; public API only, production paths pass trade counts): without trade_counts the DSR
    # trade guard is skipped, so one held position (60 mark-to-market bars) still yields a confident DSR (1.0).
    @unittest.expectedFailure
    def test_dsr_without_trade_information_is_not_published(self):
        rng = random.Random(9)
        matrix = [[rng.gauss(0, 5) for _ in range(4)] for _ in range(64)]
        returns = [0.0] * 100 + [0.0004 + rng.gauss(0, 0.0003) for _ in range(60)]
        result = assess_overfitting(matrix, [f"p{i}" for i in range(64)], variants_tested=4, dsr_returns=returns,
                                    initial_equity=10_000.0)
        self.assertIsNone(result.deflated_sharpe_probability)


if __name__ == "__main__":
    unittest.main()
