import contextlib
import importlib.util
import itertools
import json
import math
import sys
import types
import unittest
from dataclasses import asdict, dataclass
from unittest.mock import patch

from _support import workspace_tempdir
from astra import strategy_validation
from astra.governance import Actor, Role
from astra.registry import EVIDENCE_FIELDS, ModelRegistry, PromotionCriteria
from astra.storage import BacktestStore
from astra.strategies import MeanReversionConfig, MomentumConfig, SmaBaselineConfig
from astra.strategy_validation import (
    FAMILIES,
    StrategyCandidate,
    StrategyHoldoutEvaluation,
    default_candidates,
    evaluate_strategy_holdout_once,
    evaluate_strategy_walk_forward,
    holdout_evidence,
    pbo_slice_count,
)
from astra.validation import WalkForwardProtocol, _canonical_json, _sha256
from test_features import make_synthetic_bars, mutate_after

HAS_OVERFITTING_MODULE = importlib.util.find_spec("astra.overfitting") is not None
HYPOTHESIS = "A pre-declared trend or breakout variant beats flat and SMA baselines after costs out of sample."
PROTOCOL = WalkForwardProtocol(
    fold_count=2, minimum_training_bars=60, validation_bars=40, holdout_bars=40, minimum_candidate_trades=1
)
BARS = make_synthetic_bars(400)
DEVELOPMENT_END = len(BARS) - PROTOCOL.holdout_bars


# --- TEST DOUBLE ---------------------------------------------------------------
# Minimal stand-ins for astra.overfitting with the agreed interface. They return
# fixed, clearly fake values (method "TEST_DOUBLE") and record their inputs.
# They are injected only through unittest.mock.patch; the real module is
# exercised by OverfittingModuleIntegrationTests when it is installed.


@dataclass(frozen=True)
class _DoublePBOResult:
    pbo: float
    n_variants: int
    n_slices: int
    n_combinations: int
    logits: tuple
    median_logit: float
    method: str = "TEST_DOUBLE"

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class _DoubleDSRResult:
    observed_sharpe: float
    expected_max_sharpe: float
    probability: float
    n_trials: int
    n_observations: int
    skewness: float
    kurtosis: float

    def to_dict(self):
        return asdict(self)


class _OverfittingDouble:
    def __init__(self, pbo=0.25, probability=0.5):
        self.pbo = pbo
        self.probability = probability
        self.pbo_calls = []
        self.dsr_calls = []

    def probability_of_backtest_overfitting(self, performance, n_slices):
        self.pbo_calls.append({"performance": performance, "n_slices": n_slices})
        return _DoublePBOResult(
            pbo=self.pbo,
            n_variants=len(performance[0]),
            n_slices=n_slices,
            n_combinations=math.comb(n_slices, n_slices // 2),
            logits=(0.0,),
            median_logit=0.0,
        )

    def deflated_sharpe_ratio(self, returns, *, n_trials, trial_sharpe_variance=None):
        self.dsr_calls.append({"returns": list(returns), "n_trials": n_trials, "variance": trial_sharpe_variance})
        return _DoubleDSRResult(0.1, 0.2, self.probability, n_trials, len(returns), 0.0, 3.0)


@contextlib.contextmanager
def overfitting_double(**kwargs):
    double = _OverfittingDouble(**kwargs)
    names = ("probability_of_backtest_overfitting", "deflated_sharpe_ratio")
    if HAS_OVERFITTING_MODULE:
        with contextlib.ExitStack() as stack:
            for name in names:
                stack.enter_context(patch(f"astra.overfitting.{name}", getattr(double, name)))
            yield double
    else:
        module = types.ModuleType("astra.overfitting")
        for name in names:
            setattr(module, name, getattr(double, name))
        with patch.dict(sys.modules, {"astra.overfitting": module}):
            yield double


# -------------------------------------------------------------------------------


def evaluate(bars=BARS, candidates=None, protocol=PROTOCOL, store=None):
    return evaluate_strategy_walk_forward(bars, candidates, hypothesis=HYPOTHESIS, protocol=protocol, store=store)


class StrategyWalkForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with overfitting_double():
            cls.report = evaluate()

    def test_every_candidate_of_every_family_is_a_recorded_trial(self):
        report = self.report
        self.assertEqual(report.families, FAMILIES)
        self.assertIn("mean_reversion", report.excluded_families)
        self.assertEqual(report.variants_tested, len(default_candidates()))
        self.assertEqual(len(report.plan_manifest["candidates"]), report.variants_tested)
        for fold in report.folds:
            self.assertEqual(len(fold.trials), report.variants_tested)
            self.assertEqual({trial.family for trial in fold.trials}, set(FAMILIES))
            self.assertLessEqual(sum(trial.selected for trial in fold.trials), 1)
        self.assertEqual(sum(item.selected for item in report.final_candidate_selection), 1)
        self.assertEqual(report.holdout_status, "RESERVED_NOT_EVALUATED")

    def test_candidate_id_is_sha256_of_canonical_family_and_config(self):
        candidate = StrategyCandidate("momentum", MomentumConfig(lookback=10))
        self.assertEqual(candidate.candidate_id, _sha256(_canonical_json(candidate.payload())))
        self.assertEqual(candidate.candidate_id, StrategyCandidate("momentum", MomentumConfig(lookback=10)).candidate_id)
        self.assertNotEqual(candidate.candidate_id, StrategyCandidate("momentum", MomentumConfig(lookback=11)).candidate_id)

    def test_fold_layout_matches_walk_forward_protocol(self):
        folds = self.report.plan_manifest["folds"]
        self.assertEqual(folds[0]["validation_start_index"], PROTOCOL.minimum_training_bars)
        for fold in folds:
            self.assertEqual(fold["validation_end_index_exclusive"] - fold["validation_start_index"], PROTOCOL.validation_bars)
            self.assertEqual(fold["test_start_index"], fold["validation_end_index_exclusive"])
        for left, right in itertools.pairwise(folds):
            self.assertEqual(right["validation_start_index"], left["test_end_index_exclusive"])
        self.assertEqual(folds[-1]["test_end_index_exclusive"], DEVELOPMENT_END)

    def test_no_lookahead_block_metrics_ignore_later_bars(self):
        layout = self.report.plan_manifest["folds"][0]
        for boundary, field in (
            (layout["validation_end_index_exclusive"], "validation"),
            (layout["test_end_index_exclusive"], "test"),
        ):
            with self.subTest(block=field), overfitting_double():
                mutated = evaluate(mutate_after(BARS, boundary - 1))
            original = {trial.candidate_id: getattr(trial, field) for trial in self.report.folds[0].trials}
            changed = {trial.candidate_id: getattr(trial, field) for trial in mutated.folds[0].trials}
            self.assertEqual(original, changed)
            if field == "test":
                self.assertEqual(self.report.folds[0].sma_reference, mutated.folds[0].sma_reference)
            # The mutation is real: a later development slice changed.
            self.assertNotEqual(
                self.report.overfitting.performance_matrix[-1], mutated.overfitting.performance_matrix[-1]
            )

    def test_selection_ignores_last_test_block_and_holdout(self):
        last = self.report.plan_manifest["folds"][-1]
        with overfitting_double():
            mutated = evaluate(mutate_after(BARS, last["validation_end_index_exclusive"] - 1))
        self.assertEqual(mutated.selected_candidate_id, self.report.selected_candidate_id)
        self.assertEqual(mutated.final_candidate_selection, self.report.final_candidate_selection)
        for original, changed in zip(self.report.folds, mutated.folds, strict=True):
            self.assertEqual(original.selected_candidate_id, changed.selected_candidate_id)
            self.assertEqual([t.validation for t in original.trials], [t.validation for t in changed.trials])
        self.assertNotEqual([t.test for t in self.report.folds[-1].trials], [t.test for t in mutated.folds[-1].trials])

    def test_holdout_perturbation_leaves_development_results_unchanged(self):
        with overfitting_double():
            mutated = evaluate(mutate_after(BARS, DEVELOPMENT_END - 1))
        self.assertEqual(mutated.selected_candidate_id, self.report.selected_candidate_id)
        self.assertEqual(mutated.folds, self.report.folds)
        self.assertNotEqual(mutated.dataset_identity_sha256, self.report.dataset_identity_sha256)

    @patch("astra.strategy_validation.MIN_DSR_RETURNS", 0)
    @patch("astra.strategy_validation.MIN_PBO_NONZERO_ROW_FRACTION", 0.0)
    def test_overfitting_inputs_use_all_development_slices_in_order(self):
        # Plumbing test: the information guards are disabled here and tested separately.
        with overfitting_double() as double:
            report = evaluate()
        overfitting = report.overfitting
        self.assertEqual(overfitting.status, "COMPLETED")
        # One row per PBO_PERIOD_BARS period of every development window, in time order.
        labels = overfitting.slice_labels
        matrix = overfitting.performance_matrix
        expected_windows = [f"fold{f}_{kind}" for f in range(1, PROTOCOL.fold_count + 1) for kind in ("validation", "test")]
        self.assertEqual(list(dict.fromkeys(label.rsplit("_p", 1)[0] for label in labels)), expected_windows)
        self.assertGreater(len(matrix), 2 * PROTOCOL.fold_count)
        self.assertTrue(all(len(row) == report.variants_tested for row in matrix))
        # The periods of each window sum back to that window's net P&L for every candidate.
        for fold in report.folds:
            for kind in ("validation", "test"):
                prefix = f"fold{fold.fold}_{kind}_p"
                rows = [row for label, row in zip(labels, matrix, strict=True) if label.startswith(prefix)]
                for column, trial in enumerate(fold.trials):
                    window_net = getattr(trial, kind).net_pnl
                    self.assertAlmostEqual(sum(row[column] for row in rows), window_net, places=9)
        self.assertEqual(double.pbo_calls[0]["performance"], [list(row) for row in matrix])
        expected_slices = pbo_slice_count(len(matrix) // 2)
        self.assertEqual(double.pbo_calls[0]["n_slices"], expected_slices)
        self.assertEqual(overfitting.n_slices, expected_slices)
        self.assertEqual(report.plan_manifest["overfitting"]["n_slices"], expected_slices)
        self.assertGreaterEqual(len(matrix), 2 * expected_slices)
        self.assertEqual(double.dsr_calls[0]["n_trials"], report.variants_tested)
        self.assertEqual((overfitting.pbo, overfitting.deflated_sharpe_probability), (0.25, 0.5))

    def test_pbo_slice_count_is_largest_even_number_up_to_eight(self):
        self.assertEqual([pbo_slice_count(n) for n in (2, 3, 4, 7, 8, 9, 20)], [2, 2, 4, 6, 8, 8, 8])
        with self.assertRaises(ValueError):
            pbo_slice_count(1)

    def test_missing_statistics_module_leaves_overfitting_unavailable(self):
        with patch.dict(sys.modules, {"astra.overfitting": None}):
            report = evaluate()
        self.assertEqual(report.overfitting.status, "UNAVAILABLE")
        self.assertIsNone(report.overfitting.pbo)
        self.assertIsNone(report.overfitting.deflated_sharpe_probability)
        self.assertEqual(report.selected_candidate_id, self.report.selected_candidate_id)

    def test_invalid_candidate_sets_are_rejected(self):
        with self.assertRaises(ValueError):
            StrategyCandidate("mean_reversion", MeanReversionConfig())
        with self.assertRaises(TypeError):
            StrategyCandidate("sma", MomentumConfig())
        with self.assertRaises(ValueError):
            evaluate(candidates=(StrategyCandidate("sma", SmaBaselineConfig()),))
        duplicate = StrategyCandidate("sma", SmaBaselineConfig())
        with self.assertRaises(ValueError):
            evaluate(candidates=(duplicate, duplicate))
        with self.assertRaises(ValueError):
            evaluate(protocol=WalkForwardProtocol(fold_count=2, minimum_training_bars=20, validation_bars=40, holdout_bars=40))


class SealedHoldoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        self.database = f"{self._tmp.__enter__()}/astra.sqlite3"
        self.store = BacktestStore(self.database)
        self.store.initialize()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def holdout(self, report, bars=BARS):
        return evaluate_strategy_holdout_once(bars, report=report, protocol=PROTOCOL, store=self.store)

    def test_plan_is_sealed_and_holdout_is_consumed_once(self):
        with overfitting_double():
            report = evaluate(store=self.store)
            first = self.holdout(report)
            second = self.holdout(report)
        self.assertIsInstance(first, StrategyHoldoutEvaluation)
        self.assertEqual(first.status, "COMPLETED")
        self.assertEqual(first.selected_candidate_id, report.selected_candidate_id)
        self.assertIsInstance(second, dict)
        self.assertEqual(second["selected_candidate_id"], first.selected_candidate_id)
        self.assertEqual([s.cost_multiplier for s in first.cost_sensitivity], [1.0, 1.5, 2.0])
        flat, reference = first.no_position_baseline.net_pnl, first.sma_reference.net_pnl
        self.assertAlmostEqual(first.edge_vs_baseline, first.selected_candidate.net_pnl - max(flat, reference))
        events = [event["event_type"] for event in self.store.audit_events()]
        self.assertEqual(events.count("research.holdout_completed"), 1)
        # A different plan for the same canonical dataset is rejected before scoring.
        other = WalkForwardProtocol(fold_count=2, minimum_training_bars=60, validation_bars=40, holdout_bars=40, minimum_candidate_trades=0)
        with overfitting_double(), self.assertRaisesRegex(ValueError, "already sealed"):
            evaluate(protocol=other, store=self.store)

    def test_failed_holdout_attempt_stays_consumed(self):
        with overfitting_double():
            report = evaluate(store=self.store)
            with patch.object(strategy_validation, "StrategyHoldoutEvaluation", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    self.holdout(report)
            with self.assertRaisesRegex(ValueError, "already consumed"):
                self.holdout(report)

    def test_holdout_rejects_bars_that_differ_from_the_sealed_plan(self):
        with overfitting_double():
            report = evaluate(store=self.store)
            with self.assertRaisesRegex(ValueError, "does not match the sealed plan"):
                self.holdout(report, bars=mutate_after(BARS, DEVELOPMENT_END - 1))
            # The mismatch is refused before the attempt is recorded, so the holdout is not burnt.
            self.assertEqual(self.holdout(report).status, "COMPLETED")

    @patch("astra.strategy_validation.MIN_DSR_RETURNS", 0)
    @patch("astra.strategy_validation.MIN_PBO_NONZERO_ROW_FRACTION", 0.0)
    def test_evidence_matches_registry_fields_and_feeds_the_gates(self):
        # Plumbing test: the information guards are disabled here and tested separately.
        with overfitting_double(pbo=0.25, probability=0.5):
            report = evaluate(store=self.store)
            evaluation = self.holdout(report)
            stored = self.holdout(report)
        evidence = holdout_evidence(evaluation)
        self.assertEqual(tuple(evidence), EVIDENCE_FIELDS)
        self.assertEqual(holdout_evidence(stored), evidence)
        self.assertIs(evidence["holdout_used"], True)
        self.assertEqual(evidence["variants_tested"], len(default_candidates()))
        self.assertEqual((evidence["pbo"], evidence["deflated_sharpe_probability"]), (0.25, 0.5))
        stressed = [s.selected_candidate.net_pnl for s in evaluation.cost_sensitivity if s.cost_multiplier > 1.0]
        self.assertEqual(evidence["cost_stress_net_pnl"], min(stressed))

        registry = ModelRegistry(self.database)
        registry.initialize()
        registry.register_candidate(
            actor=Actor("agent", Role.RESEARCH_AGENT), model_id="wf", version="1",
            manifest={"dataset_identity": "x", "feature_set_version": "1", "code_sha256": "y", "params": {}},
            criteria=PromotionCriteria(),
        )
        attempt = self.store.holdout_for_protocol(report.plan_sha256)
        registry.record_evaluation(
            actor=Actor("agent", Role.RESEARCH_AGENT), model_id="wf", version="1", kind="holdout",
            evidence=evidence, holdout_evaluation_id=attempt["evaluation_id"],
        )
        failures = registry.check_promotion("wf", "1")
        # Registered after the holdout and without plan/candidate binding: provenance fails.
        self.assertIn("criteria_registered_after_holdout_was_scored", failures)
        self.assertIn("holdout_from_another_plan", failures)
        self.assertIn("deflated_sharpe_below_threshold", failures)
        self.assertNotIn("overfitting_check_missing", failures)
        self.assertNotIn("pbo_above_threshold", failures)


@unittest.skipUnless(HAS_OVERFITTING_MODULE, "astra.overfitting is not integrated yet (developed in a separate worktree)")
class OverfittingModuleIntegrationTests(unittest.TestCase):
    def test_real_statistics_module_receives_the_development_matrix(self):
        report = evaluate()
        overfitting = report.overfitting
        self.assertIn(overfitting.status, ("COMPLETED", "INCOMPLETE", "LOW_INFORMATION"))
        if overfitting.pbo is not None:
            self.assertGreaterEqual(overfitting.pbo, 0.0)
            self.assertLessEqual(overfitting.pbo, 1.0)
            self.assertEqual(overfitting.pbo_result["n_slices"], overfitting.n_slices)
            self.assertEqual(overfitting.pbo_result["n_variants"], report.variants_tested)
        if overfitting.deflated_sharpe_probability is not None:
            self.assertGreaterEqual(overfitting.deflated_sharpe_probability, 0.0)
            self.assertLessEqual(overfitting.deflated_sharpe_probability, 1.0)
            self.assertEqual(overfitting.deflated_sharpe_result["n_trials"], report.variants_tested)


class InformationGuardTests(unittest.TestCase):
    """Lot 3 QA: sparse or tiny samples must not produce a reassuring PBO or deflated Sharpe."""

    def assess(self, matrix, returns):
        from astra.strategy_validation import assess_overfitting

        labels = [f"p{index}" for index in range(len(matrix))]
        return assess_overfitting(matrix, labels, variants_tested=len(matrix[0]), dsr_returns=returns,
                                  trial_sharpe_variance=None)

    def test_mostly_zero_matrix_is_low_information(self):
        matrix = [[0.0] * 4 for _ in range(16)]
        matrix[3][1] = 5.0
        result = self.assess(matrix, [0.01 * (i % 7 - 3) for i in range(40)])
        self.assertIsNone(result.pbo)
        self.assertEqual(result.status, "LOW_INFORMATION")
        self.assertTrue(any(error.startswith("pbo:") for error in result.errors))

    def test_three_positive_returns_do_not_pass_the_dsr_gate(self):
        matrix = [[float((r * 7 + c * 3) % 5 - 2) for c in range(4)] for r in range(16)]
        result = self.assess(matrix, [0.01, 0.02, 0.015])
        self.assertIsNone(result.deflated_sharpe_probability)
        self.assertIn("deflated_sharpe: 3 returns (need at least 30)", result.errors)

    def test_float_noise_is_not_a_huge_sharpe(self):
        from astra.overfitting import deflated_sharpe_ratio, sharpe_like

        self.assertEqual(sharpe_like([1.0, 1.0 + 1e-13, 1.0]), 0.0)
        with self.assertRaises(ValueError):
            deflated_sharpe_ratio([0.01, 0.01 + 1e-15, 0.01 - 1e-15], n_trials=7)


class HoldoutProvenanceTests(unittest.TestCase):
    """Evidence recorded from the real sealed holdout of a version registered beforehand passes provenance."""

    @patch("astra.strategy_validation.MIN_DSR_RETURNS", 0)
    @patch("astra.strategy_validation.MIN_PBO_NONZERO_ROW_FRACTION", 0.0)
    def test_bound_evidence_passes_provenance_and_tampering_is_caught(self):
        import sqlite3

        with workspace_tempdir() as temp:
            database = f"{temp}/astra.sqlite3"
            store = BacktestStore(database)
            store.initialize()
            registry = ModelRegistry(database)
            registry.initialize()
            with overfitting_double(pbo=0.25, probability=0.5):
                report = evaluate(store=store)
                selected = next(item for item in report.final_candidate_selection if item.selected)
                registry.register_candidate(
                    actor=Actor("agent", Role.RESEARCH_AGENT), model_id="wf", version="1",
                    manifest={"dataset_identity": {"canonical_sha256": report.dataset_identity_sha256},
                              "feature_set_version": "1", "code_sha256": report.implementation_sha256,
                              "params": selected.config, "plan_sha256": report.plan_sha256,
                              "candidate_id": selected.candidate_id},
                    criteria=PromotionCriteria(),
                )
                holdout = evaluate_strategy_holdout_once(
                    BARS, None, report=report, protocol=PROTOCOL, store=store
                )
            evidence = holdout_evidence(holdout)
            attempt = store.holdout_for_protocol(report.plan_sha256)
            registry.record_evaluation(
                actor=Actor("agent", Role.RESEARCH_AGENT), model_id="wf", version="1", kind="holdout",
                evidence=evidence, holdout_evaluation_id=attempt["evaluation_id"],
            )
            failures = registry.check_promotion("wf", "1")
            provenance = {"evidence_provenance_missing", "holdout_evaluation_not_found_or_incomplete",
                          "holdout_from_another_plan", "holdout_selected_another_candidate",
                          "criteria_registered_after_holdout_was_scored", "evidence_does_not_match_holdout_report",
                          "manifest_params_differ_from_evaluated_candidate", "manifest_code_differs_from_sealed_plan",
                          "manifest_dataset_differs_from_sealed_plan"}
            self.assertFalse(provenance & set(failures), failures)
            # Rewriting the recorded numbers is detected against the stored report.
            db = sqlite3.connect(database)
            try:
                with db:
                    row = db.execute("SELECT evaluation_id, evidence_json FROM model_evaluations").fetchone()
                    payload = json.loads(row[1])
                    payload["net_pnl"] = 1e9
                    db.execute("UPDATE model_evaluations SET evidence_json = ? WHERE evaluation_id = ?",
                               (json.dumps(payload), row[0]))
            finally:
                db.close()
            self.assertIn("evidence_does_not_match_holdout_report", registry.check_promotion("wf", "1"))


class HoldoutReuseTests(unittest.TestCase):
    """Lot 3 QA: appending a bar must not unlock a new attempt on (almost) the same holdout."""

    def test_extended_dataset_with_overlapping_holdout_is_refused(self):
        with workspace_tempdir() as temp:
            store = BacktestStore(f"{temp}/astra.sqlite3")
            store.initialize()
            with overfitting_double():
                evaluate(store=store)
                longer = make_synthetic_bars(401)
                with self.assertRaises(ValueError) as caught:
                    evaluate(bars=longer, store=store)
            self.assertIn("overlaps the holdout", str(caught.exception))


class ManifestBindingTests(unittest.TestCase):
    """Lot 3 QA (B): a version whose params, code or dataset differ from the evaluated candidate is refused."""

    @patch("astra.strategy_validation.MIN_DSR_RETURNS", 0)
    @patch("astra.strategy_validation.MIN_PBO_NONZERO_ROW_FRACTION", 0.0)
    def test_other_params_code_or_dataset_fail_provenance(self):
        with workspace_tempdir() as temp:
            database = f"{temp}/astra.sqlite3"
            store = BacktestStore(database)
            store.initialize()
            registry = ModelRegistry(database)
            registry.initialize()
            with overfitting_double(pbo=0.25, probability=0.5):
                report = evaluate(store=store)
                selected = next(item for item in report.final_candidate_selection if item.selected)
                registry.register_candidate(
                    actor=Actor("agent", Role.RESEARCH_AGENT), model_id="wf", version="1",
                    manifest={"dataset_identity": {"canonical_sha256": "another-dataset"},
                              "feature_set_version": "1", "code_sha256": "other-code",
                              "params": {"family": "sma", "fast": 2, "slow": 3}, "plan_sha256": report.plan_sha256,
                              "candidate_id": selected.candidate_id},
                    criteria=PromotionCriteria(),
                )
                holdout = evaluate_strategy_holdout_once(BARS, None, report=report, protocol=PROTOCOL, store=store)
            registry.record_evaluation(
                actor=Actor("agent", Role.RESEARCH_AGENT), model_id="wf", version="1", kind="holdout",
                evidence=holdout_evidence(holdout),
                holdout_evaluation_id=store.holdout_for_protocol(report.plan_sha256)["evaluation_id"],
            )
            failures = registry.check_promotion("wf", "1")
            for expected in ("manifest_params_differ_from_evaluated_candidate", "manifest_code_differs_from_sealed_plan",
                             "manifest_dataset_differs_from_sealed_plan"):
                self.assertIn(expected, failures)


class InstrumentScopedOverlapTests(unittest.TestCase):
    def test_same_period_on_another_instrument_is_allowed(self):
        from dataclasses import replace as dc_replace

        with workspace_tempdir() as temp:
            store = BacktestStore(f"{temp}/astra.sqlite3")
            store.initialize()
            with overfitting_double():
                evaluate(store=store)
                # Same calendar, genuinely different prices (another seed): independent data.
                other = [dc_replace(bar, instrument="GBPUSD") for bar in make_synthetic_bars(400, seed=11)]
                report = evaluate(bars=other, store=store)
            self.assertEqual(report.plan_manifest["instrument"], "GBPUSD")


class PerVariantGuardTests(unittest.TestCase):
    def test_a_barely_trading_variant_makes_pbo_low_information(self):
        from astra.strategy_validation import assess_overfitting

        matrix = [[float((r * 5 + 1) % 7 - 3), 0.0 if r else 1.0, float((r * 3) % 5 - 2)] for r in range(16)]
        result = assess_overfitting(matrix, [f"p{r}" for r in range(16)], variants_tested=3,
                                    dsr_returns=[0.01 * (i % 5 - 2) for i in range(40)], trial_sharpe_variance=None)
        self.assertIsNone(result.pbo)
        self.assertTrue(any("variants have fewer than" in error for error in result.errors))

class HoldoutContentReuseTests(unittest.TestCase):
    """Lot 3 QA round 3: the same holdout prices relabelled or shifted in time cannot be scored again."""

    def assert_refused(self, transform):
        with workspace_tempdir() as temp:
            store = BacktestStore(f"{temp}/astra.sqlite3")
            store.initialize()
            with overfitting_double():
                evaluate(store=store)
                with self.assertRaises(ValueError) as caught:
                    evaluate(bars=[transform(bar) for bar in BARS], store=store)
            self.assertIn("price path matches", str(caught.exception))

    def test_time_shifted_copy_is_refused(self):
        from dataclasses import replace as dc_replace
        from datetime import timedelta

        self.assert_refused(lambda bar: dc_replace(bar, timestamp=bar.timestamp + timedelta(days=730)))

    def test_relabelled_copy_is_refused(self):
        from dataclasses import replace as dc_replace

        self.assert_refused(lambda bar: dc_replace(bar, instrument="EURUSD.X"))

    def test_rescaled_copy_is_refused(self):
        from dataclasses import replace as dc_replace

        self.assert_refused(
            lambda bar: dc_replace(bar, open=bar.open * 2, high=bar.high * 2, low=bar.low * 2, close=bar.close * 2)
        )

if __name__ == "__main__":
    unittest.main()
