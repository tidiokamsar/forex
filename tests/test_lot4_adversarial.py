"""Lot 4 adversarial scenarios (QA agent D): research-integrity attacks on public paths.

Every test asserts the SECURE behaviour. Scenarios that fail on the Lot 3 baseline
(a2a1856) were marked ``@unittest.expectedFailure`` with a ``# BASELINE FAIL D-n``
comment; when a Lot 4 fix lands, the marker must be removed (an unexpected success
is reported by unittest). Tests named ``test_baseline_*`` pin today's observable,
insecure behaviour where the secure API does not exist yet; they are meant to be
replaced, not kept, once the global attempt registry exists.

All data is SYNTHETIC. No network, broker or order is involved.
"""

import contextlib
import csv
import functools
import io
import json
import os
import random
import shutil
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from _support import PROJECT_ROOT, workspace_tempdir
from astra import registry as registry_module
from astra.cli import main
from astra.governance import Actor, Role
from astra.registry import EVIDENCE_FIELDS, ModelRegistry, ModelStatus, PromotionCriteria, PromotionError
from astra.storage import BacktestStore
from astra.strategies import BreakoutConfig, SmaBaselineConfig
from astra.strategy_validation import (
    StrategyCandidate,
    assess_overfitting,
    default_candidates,
    evaluate_strategy_holdout_once,
    evaluate_strategy_walk_forward,
    holdout_evidence,
)
from astra.validation import WalkForwardProtocol
from test_features import make_synthetic_bars, mutate_after

HYPOTHESIS = "A pre-declared trend or breakout variant beats flat and SMA baselines after costs out of sample."
PROTOCOL = WalkForwardProtocol(
    fold_count=2, minimum_training_bars=60, validation_bars=40, holdout_bars=40, minimum_candidate_trades=1
)
BARS = tuple(make_synthetic_bars(400))
HOLDOUT_START = len(BARS) - PROTOCOL.holdout_bars
AGENT = Actor("agent-d", Role.RESEARCH_AGENT)
REVIEWER = Actor("reviewer-d", Role.HUMAN_REVIEWER)
PROVENANCE_FAILURES = {
    "evidence_provenance_missing",
    "holdout_evaluation_not_found_or_incomplete",
    "holdout_from_another_plan",
    "holdout_selected_another_candidate",
    "criteria_registered_after_holdout_was_scored",
    "evidence_does_not_match_holdout_report",
    "manifest_params_differ_from_evaluated_candidate",
    "manifest_code_differs_from_sealed_plan",
    "manifest_dataset_differs_from_sealed_plan",
}
TWO_CANDIDATES = (
    StrategyCandidate("sma", SmaBaselineConfig(fast_window=5, slow_window=13)),
    StrategyCandidate("breakout", BreakoutConfig(channel_window=10)),
)


# --- helpers -------------------------------------------------------------------------


def evaluate(bars=BARS, candidates=None, protocol=PROTOCOL, store=None):
    return evaluate_strategy_walk_forward(bars, candidates, hypothesis=HYPOTHESIS, protocol=protocol, store=store)


def score_holdout(store, report, bars=BARS, candidates=None, protocol=PROTOCOL):
    return evaluate_strategy_holdout_once(bars, candidates, report=report, protocol=protocol, store=store)


def selected_item(report):
    return next(item for item in report.final_candidate_selection if item.selected)


def bound_manifest(report, item=None):
    item = item or selected_item(report)
    return {
        "dataset_identity": {"canonical_sha256": report.dataset_identity_sha256},
        "feature_set_version": "1",
        "code_sha256": report.implementation_sha256,
        "params": item.config,
        "plan_sha256": report.plan_sha256,
        "candidate_id": item.candidate_id,
    }


def shift_prices(bar, delta):
    """Move every price of one bar by ``delta``; OHLC and quote ordering stay valid."""

    return replace(
        bar,
        open=bar.open + delta,
        high=bar.high + delta,
        low=bar.low + delta,
        close=bar.close + delta,
        bid=None if bar.bid is None else bar.bid + delta,
        ask=None if bar.ask is None else bar.ask + delta,
    )


def perturb_holdout(bars, delta=1e-5):
    """Shift one bar in the middle of the holdout by a sub-pip amount."""

    bars = list(bars)
    index = len(bars) - PROTOCOL.holdout_bars // 2
    bars[index] = shift_prices(bars[index], delta)
    return bars


def relabel(bars, instrument="EURUSD.X"):
    return [replace(bar, instrument=instrument) for bar in bars]


def time_shift(bars, days=730):
    return [replace(bar, timestamp=bar.timestamp + timedelta(days=days)) for bar in bars]


# Independent data for "fresh plan" scenarios: another seed, one month later, keeping the EURUSD label
# (the only instrument with a default spec; other labels produce no simulated trades).
INDEPENDENT_BARS = tuple(time_shift(make_synthetic_bars(400, seed=11), days=30))


def copy_database(source, target):
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


class WriteRefused(Exception):
    """A direct SQLite write was rejected by the store (for example an append-only trigger)."""


def sql(database, statement, parameters=()):
    db = sqlite3.connect(database)
    try:
        with db:
            return db.execute(statement, parameters).fetchall()
    except sqlite3.DatabaseError as exc:
        if statement.lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT")):
            raise WriteRefused(str(exc)) from exc
        raise
    finally:
        db.close()


def secure_if_write_refused(test):
    """Direct-database tampering tests: a store that refuses the write is the secure outcome."""

    @functools.wraps(test)
    def wrapper(self, *args, **kwargs):
        try:
            return test(self, *args, **kwargs)
        except WriteRefused:
            return None

    return wrapper


def rewrite_json(database, table, column, key_column, key, mutate):
    row = sql(database, f"SELECT {column} FROM {table} WHERE {key_column} = ?", (key,))[0]
    payload = mutate(json.loads(row[0]))
    sql(database, f"UPDATE {table} SET {column} = ? WHERE {key_column} = ?", (json.dumps(payload), key))
    return payload


class SealedHoldoutFixture(unittest.TestCase):
    """One sealed plan on BARS, three versions registered BEFORE the holdout, holdout scored once.

    Versions: ``wf@1`` and ``wf@2`` bind the selected candidate (identical manifests);
    ``wf@other`` binds a non-selected candidate. No evidence is recorded in the template;
    each test works on its own copy of the database.
    """

    @classmethod
    def setUpClass(cls):
        cls._class_tmp = workspace_tempdir()
        cls.template = f"{cls._class_tmp.__enter__()}/template.sqlite3"
        store = BacktestStore(cls.template)
        store.initialize()
        registry = ModelRegistry(cls.template)
        registry.initialize()
        cls.report = evaluate(store=store)
        selected = selected_item(cls.report)
        other = next(item for item in cls.report.final_candidate_selection if not item.selected)
        for version, item in (("1", selected), ("2", selected), ("other", other)):
            registry.register_candidate(
                actor=AGENT, model_id="wf", version=version, manifest=bound_manifest(cls.report, item),
                criteria=PromotionCriteria(),
            )
        cls.holdout = score_holdout(store, cls.report)
        cls.evidence = holdout_evidence(cls.holdout)
        cls.evaluation_id = store.holdout_for_protocol(cls.report.plan_sha256)["evaluation_id"]

    @classmethod
    def tearDownClass(cls):
        cls._class_tmp.__exit__(None, None, None)

    def setUp(self):
        self._tmp = workspace_tempdir()
        self.database = f"{self._tmp.__enter__()}/astra.sqlite3"
        copy_database(self.template, self.database)
        self.store = BacktestStore(self.database)
        self.registry = ModelRegistry(self.database)

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def record(self, version="1", evidence=None, evaluation_id=None):
        return self.registry.record_evaluation(
            actor=AGENT, model_id="wf", version=version, kind="holdout",
            evidence=dict(self.evidence if evidence is None else evidence),
            holdout_evaluation_id=evaluation_id or self.evaluation_id,
        )

    def assert_not_promoted(self, version="1"):
        with self.assertRaises(PromotionError):
            self.registry.promote(actor=REVIEWER, model_id="wf", version=version, reason="adversarial attempt")
        self.assertEqual(self.registry.get("wf", version)["status"], ModelStatus.CANDIDATE)


# --- 1. holdout data modified after evaluation ---------------------------------------


class HoldoutDataModifiedAfterEvaluationTests(SealedHoldoutFixture):
    def test_s01_rescoring_with_modified_holdout_bars_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not match the sealed plan"):
            score_holdout(self.store, self.report, bars=perturb_holdout(BARS))

    def test_s02_new_plan_on_modified_holdout_same_instrument_is_refused(self):
        with self.assertRaises(ValueError):
            evaluate(bars=mutate_after(BARS, HOLDOUT_START - 1), store=self.store)

    def test_s03_development_bars_modified_holdout_kept_is_refused(self):
        bars = list(BARS)
        bars[10] = shift_prices(bars[10], 1e-5)
        with self.assertRaisesRegex(ValueError, "price path matches|overlaps"):
            evaluate(bars=bars, store=self.store)

    def test_s04_version_bound_to_modified_dataset_fails_provenance(self):
        modified = evaluate(bars=perturb_holdout(BARS))  # no store: only to get the new identity
        manifest = {**bound_manifest(self.report),
                    "dataset_identity": {"canonical_sha256": modified.dataset_identity_sha256}}
        # Registered after the holdout as well, so two independent reasons must refuse it.
        self.registry.register_candidate(actor=AGENT, model_id="wf", version="modified", manifest=manifest,
                                         criteria=PromotionCriteria())
        self.registry.record_evaluation(actor=AGENT, model_id="wf", version="modified", kind="holdout",
                                        evidence=dict(self.evidence), holdout_evaluation_id=self.evaluation_id)
        failures = self.registry.check_promotion("wf", "modified")
        self.assertIn("manifest_dataset_differs_from_sealed_plan", failures)
        self.assertIn("criteria_registered_after_holdout_was_scored", failures)

    # FIXED IN LOT 4 (was BASELINE FAIL D-1): the stored holdout report has no integrity seal. Rewriting report_json and the
    # evidence row consistently (local SQLite write) turns a failing candidate into a CHAMPION.
    @secure_if_write_refused
    def test_s05_forged_stored_report_and_matching_evidence_cannot_promote(self):
        self.record("1")

        def improve(report):
            selected = report["selected_candidate"]
            selected.update(trade_count=120, net_pnl=5_000.0, profit_factor=3.0, max_drawdown_pct=1.0)
            for scenario in report["cost_sensitivity"]:
                if scenario["selected_candidate"]:
                    scenario["selected_candidate"]["net_pnl"] = 4_000.0
            report["edge_vs_baseline"] = 4_500.0
            report["overfitting"].update(pbo=0.05, deflated_sharpe_probability=0.99)
            return report

        forged = rewrite_json(self.database, "holdout_evaluations", "report_json", "evaluation_id",
                              self.evaluation_id, improve)
        evaluation_row = sql(self.database, "SELECT evaluation_id FROM model_evaluations WHERE version = '1'")[0][0]
        rewrite_json(self.database, "model_evaluations", "evidence_json", "evaluation_id", evaluation_row,
                     lambda payload: {**payload, **holdout_evidence(forged)})
        self.assert_not_promoted("1")


# --- 2. holdout reused under another identifier --------------------------------------


class HoldoutReuseTests(SealedHoldoutFixture):
    def assert_reuse_refused(self, bars, protocol=PROTOCOL, completed=1):
        # The transformed data must be valid on its own, so a refusal is about reuse, not bad input.
        evaluate(bars=bars, protocol=protocol)
        with self.assertRaises(ValueError):
            report = evaluate(bars=bars, protocol=protocol, store=self.store)
            score_holdout(self.store, report, bars=bars, protocol=protocol)
        self.assertEqual(
            sum(1 for row in sql(self.database, "SELECT status FROM holdout_evaluations") if row[0] == "COMPLETED"), completed
        )

    def test_s06_new_protocol_id_same_bars_is_refused(self):
        self.assert_reuse_refused(BARS, replace(PROTOCOL, minimum_candidate_trades=0))

    def test_s07_relabelled_instrument_is_refused(self):
        self.assert_reuse_refused(relabel(BARS))

    def test_s08_lowercase_instrument_maps_to_the_same_plan_and_stored_result(self):
        bars = relabel(BARS, " eurusd ")
        report = evaluate(bars=bars, store=self.store)
        self.assertEqual(report.plan_sha256, self.report.plan_sha256)
        stored = score_holdout(self.store, report, bars=bars)
        self.assertIsInstance(stored, dict)  # the completed attempt is returned, never recomputed
        self.assertEqual(stored["evaluated_at"], self.holdout.evaluated_at.isoformat())

    def test_s09_shifted_dates_are_refused(self):
        self.assert_reuse_refused(time_shift(BARS))

    def test_s10_rescaled_prices_are_refused(self):
        self.assert_reuse_refused(
            [replace(bar, open=bar.open * 3, high=bar.high * 3, low=bar.low * 3, close=bar.close * 3,
                     bid=bar.bid * 3, ask=bar.ask * 3) for bar in BARS]
        )

    def test_s11_perturbed_same_instrument_and_dates_is_refused(self):
        self.assert_reuse_refused(perturb_holdout(BARS))

    def test_s12_truncated_by_one_bar_same_instrument_is_refused(self):
        self.assert_reuse_refused(BARS[:-1])

    def test_s13_extended_by_one_bar_same_instrument_is_refused(self):
        self.assert_reuse_refused(make_synthetic_bars(401))

    def test_s14_first_bar_dropped_is_refused(self):
        self.assert_reuse_refused(BARS[1:])

    # FIXED IN LOT 4 (was BASELINE FAIL D-2): content fingerprint is an exact hash of rounded returns; a sub-pip change to one
    # holdout bar plus a new instrument label (which skips the period-overlap check) re-opens the holdout.
    def test_s15_perturbed_and_relabelled_is_refused(self):
        self.assert_reuse_refused(relabel(perturb_holdout(BARS)))

    # FIXED IN LOT 4 (was BASELINE FAIL D-3): same, with dates shifted instead of a new label (no period overlap, no exact match).
    def test_s16_perturbed_and_time_shifted_is_refused(self):
        self.assert_reuse_refused(time_shift(perturb_holdout(BARS)))

    # FIXED IN LOT 4 (was BASELINE FAIL D-4): dropping the last bar moves the 40-bar holdout window by one; 39 of its 40 returns
    # are already spent, but once the dates are shifted (or the label changed) neither check fires.
    def test_s17_truncated_by_one_bar_and_time_shifted_is_refused(self):
        self.assert_reuse_refused(time_shift(BARS[:-1]))

    # FIXED IN LOT 4 (was BASELINE FAIL D-4, label variant)
    def test_s17b_truncated_by_one_bar_and_relabelled_is_refused(self):
        self.assert_reuse_refused(relabel(BARS[:-1]))

    # FIXED IN LOT 4 (was BASELINE FAIL D-5): appending one bar, same mechanism as D-4.
    def test_s18_extended_by_one_bar_and_time_shifted_is_refused(self):
        self.assert_reuse_refused(time_shift(make_synthetic_bars(401)))

    # FIXED IN LOT 4 (was BASELINE FAIL D-5, label variant)
    def test_s18b_extended_by_one_bar_and_relabelled_is_refused(self):
        self.assert_reuse_refused(relabel(make_synthetic_bars(401)))

    # FIXED IN LOT 4 (was BASELINE FAIL D-6): a different holdout length (new protocol id) changes the fingerprint window.
    def test_s19_other_holdout_length_and_time_shifted_is_refused(self):
        self.assert_reuse_refused(time_shift(BARS), replace(PROTOCOL, holdout_bars=39))

    # FIXED IN LOT 4 (was BASELINE FAIL D-6, label variant)
    def test_s19b_other_holdout_length_and_relabelled_is_refused(self):
        self.assert_reuse_refused(relabel(BARS), replace(PROTOCOL, holdout_bars=39))

    # FIXED IN LOT 4 (was BASELINE FAIL D-7): deleting the sealed plan and its attempt rows (local SQLite write) re-opens the holdout;
    # the audit log still records research.protocol_sealed / research.holdout_completed but nothing reads it.
    @secure_if_write_refused
    def test_s20_deleted_seal_rows_do_not_reopen_the_holdout(self):
        sql(self.database, "DELETE FROM holdout_evaluations")
        sql(self.database, "DELETE FROM research_protocols")
        self.assert_reuse_refused(BARS, replace(PROTOCOL, minimum_candidate_trades=0), completed=0)


# --- 3. parameters modified between evaluation and promotion -------------------------


class ParametersModifiedTests(SealedHoldoutFixture):
    def test_s21_registered_params_differ_from_evaluated_candidate(self):
        self.registry.register_candidate(
            actor=AGENT, model_id="wf", version="tuned",
            manifest={**bound_manifest(self.report), "params": {**selected_item(self.report).config, "tuned": True}},
            criteria=PromotionCriteria(),
        )
        self.record("tuned")
        self.assertIn("manifest_params_differ_from_evaluated_candidate", self.registry.check_promotion("wf", "tuned"))

    @secure_if_write_refused
    def test_s22_manifest_row_edited_without_hash_is_tampered(self):
        self.record("1")
        rewrite_json(self.database, "model_versions", "manifest_json", "version", "1",
                     lambda manifest: {**manifest, "params": {"family": "sma", "config": {"fast_window": 2}}})
        self.assertEqual(self.registry.check_promotion("wf", "1"), ("manifest_tampered",))
        self.assert_not_promoted("1")

    @secure_if_write_refused
    def test_s23_manifest_row_and_hash_edited_still_fails_binding(self):
        self.record("1")
        manifest = rewrite_json(self.database, "model_versions", "manifest_json", "version", "1",
                                lambda manifest: {**manifest, "params": {"family": "sma", "config": {"fast_window": 2}}})
        sql(self.database, "UPDATE model_versions SET manifest_sha256 = ? WHERE version = '1'",
            (registry_module._sha256(manifest),))
        self.assertIn("manifest_params_differ_from_evaluated_candidate", self.registry.check_promotion("wf", "1"))

    def test_s24_non_selected_candidate_cannot_use_the_holdout(self):
        self.record("other")
        failures = self.registry.check_promotion("wf", "other")
        self.assertIn("holdout_selected_another_candidate", failures)
        self.assertIn("manifest_params_differ_from_evaluated_candidate", failures)

    # FIXED IN LOT 4 (was BASELINE FAIL D-8): promotion never re-hashes the running implementation; code changed after the
    # holdout (here simulated by a different implementation digest) is promoted under the old evidence.
    def test_s25_code_changed_after_holdout_adds_a_promotion_failure(self):
        self.record("1")
        before = set(self.registry.check_promotion("wf", "1"))
        with patch("astra.strategy_validation._implementation_sha256", return_value="0" * 64):
            after = set(self.registry.check_promotion("wf", "1"))
        self.assertTrue(after - before, "implementation drift since the holdout is not reported")

    # FIXED IN LOT 4 (was BASELINE FAIL D-9): one-version-per-holdout is enforced only in record_evaluation; an evidence row
    # copied to an identical sibling version (local SQLite write) passes provenance at promotion time.
    @secure_if_write_refused
    def test_s26_holdout_copied_to_sibling_version_is_refused_at_promotion(self):
        self.record("1")
        with self.assertRaises(PromotionError):
            self.record("2")  # the API path is closed
        row = sql(self.database, "SELECT evidence_json FROM model_evaluations WHERE version = '1'")[0][0]
        sql(self.database, "INSERT INTO model_evaluations VALUES ('copied', 'wf', '2', 'holdout', ?, '2099-01-01')",
            (row,))
        first = set(self.registry.check_promotion("wf", "1"))
        second = set(self.registry.check_promotion("wf", "2"))
        self.assertTrue(second - first, "the sibling version inherits the holdout evidence unchallenged")


# --- 4. loosened promotion criteria --------------------------------------------------


class LoosenedCriteriaTests(SealedHoldoutFixture):
    LOOSER: ClassVar[dict] = {
        "min_trades": 29,
        "min_net_pnl": -1.0,
        "min_profit_factor": 1.0,
        "max_drawdown_pct": 10.5,
        "min_edge_vs_baseline": -0.01,
        "require_cost_stress_non_negative": False,
        "require_holdout": False,
        "max_pbo": 0.31,
        "min_deflated_sharpe_probability": 0.94,
        "require_overfitting_check": False,
    }

    def test_s27_looser_criteria_are_refused_at_registration(self):
        for field, value in self.LOOSER.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.registry.register_candidate(
                    actor=AGENT, model_id="loose", version=field, manifest=bound_manifest(self.report),
                    criteria=PromotionCriteria(**{field: value}),
                )
        # Truthy non-bool flags and bool-as-int counts are not accepted as "strict".
        for kwargs in ({"require_holdout": 1}, {"min_trades": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.registry.register_candidate(
                    actor=AGENT, model_id="loose", version=str(kwargs), manifest=bound_manifest(self.report),
                    criteria=PromotionCriteria(**kwargs),
                )

    @secure_if_write_refused
    def test_s28_criteria_row_loosened_with_rehash_hits_the_floor_at_promotion(self):
        self.record("1")
        for field, value in self.LOOSER.items():
            with self.subTest(field=field):
                criteria = rewrite_json(self.database, "model_versions", "criteria_json", "version", "1",
                                        lambda payload, f=field, v=value: {**asdict_criteria(), f: v})
                sql(self.database, "UPDATE model_versions SET criteria_sha256 = ? WHERE version = '1'",
                    (registry_module._sha256(criteria),))
                self.assertIn(f"criteria_below_policy_floor:{field}", self.registry.check_promotion("wf", "1"))
                self.assert_not_promoted("1")

    @secure_if_write_refused
    def test_s29_criteria_row_loosened_without_rehash_is_tampered(self):
        self.record("1")
        rewrite_json(self.database, "model_versions", "criteria_json", "version", "1",
                     lambda payload: {**payload, "min_trades": 1})
        self.assertEqual(self.registry.check_promotion("wf", "1"), ("criteria_tampered",))

    @secure_if_write_refused
    def test_s30_malformed_criteria_rows_fail_closed(self):
        self.record("1")
        for mutate in (
            lambda payload: {**payload, "unknown_gate_disabled": True},
            lambda payload: {**payload, "min_trades": "30"},
            lambda payload: {**payload, "max_pbo": None},
        ):
            criteria = rewrite_json(self.database, "model_versions", "criteria_json", "version", "1",
                                    lambda payload, m=mutate: m(asdict_criteria()))
            sql(self.database, "UPDATE model_versions SET criteria_sha256 = ? WHERE version = '1'",
                (registry_module._sha256(criteria),))
            with self.subTest(criteria=criteria), self.assertRaises((TypeError, ValueError)):
                self.registry.promote(actor=REVIEWER, model_id="wf", version="1", reason="adversarial attempt")
            self.assertEqual(self.registry.get("wf", "1")["status"], ModelStatus.CANDIDATE)

    @secure_if_write_refused
    def test_s31_legacy_row_missing_gates_falls_back_to_strict_defaults(self):
        self.record("1")
        legacy = {key: value for key, value in asdict_criteria().items()
                  if key not in {"max_pbo", "min_deflated_sharpe_probability", "require_overfitting_check"}}
        sql(self.database, "UPDATE model_versions SET criteria_json = ?, criteria_sha256 = ? WHERE version = '1'",
            (json.dumps(legacy), registry_module._sha256(legacy)))
        failures = self.registry.check_promotion("wf", "1")
        self.assertFalse([f for f in failures if f.startswith("criteria_")], failures)
        # Lot 4 integration: with mark-to-market PBO the sample may be COMPLETED; either way a gate fails.
        self.assertTrue(OVERFITTING_FAILURES & set(failures), failures)


OVERFITTING_FAILURES = {"overfitting_check_missing", "pbo_above_threshold", "deflated_sharpe_below_threshold"}


def asdict_criteria():
    from dataclasses import asdict

    return asdict(PromotionCriteria())


# --- 5. fabricated or insufficient evidence ------------------------------------------

GOOD_NUMBERS = {
    "trade_count": 200, "net_pnl": 900.0, "profit_factor": 2.5, "max_drawdown_pct": 2.0, "edge_vs_baseline": 800.0,
    "cost_stress_net_pnl": 700.0, "holdout_used": True, "pbo": 0.05, "deflated_sharpe_probability": 0.99,
    "variants_tested": 7,
}


class FabricatedEvidenceTests(SealedHoldoutFixture):
    def test_s32_holdout_evidence_without_reference_is_refused(self):
        with self.assertRaises(PromotionError):
            self.registry.record_evaluation(actor=AGENT, model_id="wf", version="1", kind="holdout",
                                            evidence=GOOD_NUMBERS)

    def test_s33_forged_numbers_with_unknown_reference_fail(self):
        self.record("1", GOOD_NUMBERS, evaluation_id="forged-evaluation-id")
        self.assertIn("holdout_evaluation_not_found_or_incomplete", self.registry.check_promotion("wf", "1"))
        self.assert_not_promoted("1")

    def test_s34_forged_numbers_with_genuine_reference_fail(self):
        self.record("1", GOOD_NUMBERS)
        self.assertIn("evidence_does_not_match_holdout_report", self.registry.check_promotion("wf", "1"))
        self.assert_not_promoted("1")

    def test_s35_validation_only_evidence_is_not_decisive(self):
        self.registry.record_evaluation(actor=AGENT, model_id="wf", version="1", kind="validation",
                                        evidence=GOOD_NUMBERS)
        self.assertEqual(self.registry.check_promotion("wf", "1"), ("missing_holdout_evaluation",))
        self.assert_not_promoted("1")

    def test_s36_evidence_with_injected_provenance_or_unknown_field_is_refused(self):
        with self.assertRaises(ValueError):
            self.record("1", {**self.evidence, "_provenance": {"holdout_evaluation_id": self.evaluation_id}})
        with self.assertRaises(ValueError):
            self.record("1", {**self.evidence, "approved": True})

    def test_s37_low_information_overfitting_never_passes(self):
        # Lot 4 integration: Agent B's mark-to-market matrix can make this sample COMPLETED; when it
        # is not, the numbers must stay unpublished. Either way the overfitting gates must not pass.
        if self.holdout.overfitting.status in ("LOW_INFORMATION", "INCOMPLETE", "UNAVAILABLE"):
            self.assertIsNone(self.evidence["pbo"])
        self.record("1")
        failures = self.registry.check_promotion("wf", "1")
        self.assertFalse(PROVENANCE_FAILURES & set(failures), failures)  # genuine, bound evidence
        self.assertTrue(OVERFITTING_FAILURES & set(failures), failures)
        self.assert_not_promoted("1")

    def test_s38_low_information_with_forged_statistics_fails(self):
        self.record("1", {**self.evidence, "pbo": 0.01, "deflated_sharpe_probability": 0.999})
        self.assertIn("evidence_does_not_match_holdout_report", self.registry.check_promotion("wf", "1"))

    def test_s39_evidence_from_another_plan_fails(self):
        other_bars = relabel(make_synthetic_bars(400, seed=11), "GBPUSD")
        other = evaluate(bars=other_bars, store=self.store)
        score_holdout(self.store, other, bars=other_bars)
        other_id = self.store.holdout_for_protocol(other.plan_sha256)["evaluation_id"]
        evidence = holdout_evidence(sql_report(self.database, other_id))
        self.record("1", evidence, evaluation_id=other_id)
        self.assertIn("holdout_from_another_plan", self.registry.check_promotion("wf", "1"))

    def test_s40_failed_holdout_attempt_cannot_back_evidence(self):
        bars = relabel(make_synthetic_bars(400, seed=13), "USDJPY")
        report = evaluate(bars=bars, store=self.store)
        from astra import strategy_validation

        with patch.object(strategy_validation, "StrategyHoldoutEvaluation", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                score_holdout(self.store, report, bars=bars)
        failed_id = self.store.holdout_for_protocol(report.plan_sha256)["evaluation_id"]
        self.record("1", self.evidence, evaluation_id=failed_id)
        self.assertIn("holdout_evaluation_not_found_or_incomplete", self.registry.check_promotion("wf", "1"))

    def test_s41_version_registered_after_holdout_is_refused(self):
        self.registry.register_candidate(actor=AGENT, model_id="late", version="1",
                                         manifest=bound_manifest(self.report), criteria=PromotionCriteria())
        self.registry.record_evaluation(actor=AGENT, model_id="late", version="1", kind="holdout",
                                        evidence=dict(self.evidence), holdout_evaluation_id=self.evaluation_id)
        self.assertIn("criteria_registered_after_holdout_was_scored", self.registry.check_promotion("late", "1"))


def sql_report(database, evaluation_id):
    return json.loads(sql(database, "SELECT report_json FROM holdout_evaluations WHERE evaluation_id = ?",
                          (evaluation_id,))[0][0])


# --- 6. trial count (variants_tested) -------------------------------------------------


class TrialCountTests(unittest.TestCase):
    """The deflated Sharpe and the registry need the number of variants actually tried in the store.

    Today ``variants_tested`` is the size of the last sealed plan only.
    """

    def setUp(self):
        self._tmp = workspace_tempdir()
        self.database = f"{self._tmp.__enter__()}/astra.sqlite3"
        self.store = BacktestStore(self.database)
        self.store.initialize()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def two_plans(self):
        # Plan 1: 7 variants on EURUSD, sealed, holdout never opened (the "rejected" exploration).
        evaluate(store=self.store)
        # Plan 2: only 2 variants on an independent dataset (other seed, a later month), holdout scored.
        bars = INDEPENDENT_BARS
        report = evaluate(bars=bars, candidates=TWO_CANDIDATES, store=self.store)
        return holdout_evidence(score_holdout(self.store, report, bars=bars, candidates=TWO_CANDIDATES))

    # FIXED IN LOT 4 (was BASELINE FAIL D-10): variants of earlier sealed plans in the same store are not counted.
    def test_s42_variants_tested_counts_every_sealed_plan_in_the_store(self):
        distinct = {candidate.candidate_id for candidate in (*default_candidates(), *TWO_CANDIDATES)}
        self.assertGreaterEqual(self.two_plans()["variants_tested"], len(distinct))

    # RESIDUAL (BASELINE FAIL D-11, not fixed): an unsealed exploratory run (store=None) leaves no trace; the sealed plan then
    # declares only the winner and one filler, so the deflated Sharpe is computed with n_trials=2.
    @unittest.expectedFailure
    def test_s43_unsealed_exploration_then_reduced_plan_is_counted(self):
        exploration = evaluate()  # 7 variants, never sealed
        winner = next(c for c in default_candidates() if c.candidate_id == exploration.selected_candidate_id)
        filler = next(c for c in default_candidates() if c.candidate_id != winner.candidate_id)
        report = evaluate(candidates=(winner, filler), store=self.store)
        evidence = holdout_evidence(score_holdout(self.store, report, candidates=(winner, filler)))
        self.assertGreaterEqual(evidence["variants_tested"], len(default_candidates()))

    # FIXED IN LOT 4 (was BASELINE FAIL D-12): assess_overfitting accepts variants_tested (1) below the matrix width (7) and
    # computes the deflated Sharpe with n_trials=1 (no deflation): the probability rises instead of being refused.
    def test_s44_trial_count_below_matrix_width_is_refused(self):
        rng = random.Random(5)
        matrix = [[rng.gauss(0.0, 1.0) for _ in range(7)] for _ in range(32)]
        returns = [0.002 + 0.01 * rng.gauss(0.0, 1.0) for _ in range(60)]
        labels = [f"p{i}" for i in range(32)]
        honest = assess_overfitting(matrix, labels, variants_tested=7, dsr_returns=returns, trial_sharpe_variance=None)
        self.assertIsNotNone(honest.deflated_sharpe_probability)
        try:
            understated = assess_overfitting(matrix, labels, variants_tested=1, dsr_returns=returns,
                                             trial_sharpe_variance=None)
        except ValueError:
            return  # refusing an inconsistent trial count is the secure outcome
        # Lot 4: the matrix width is a floor on N, so an understated count cannot raise the probability.
        if understated.deflated_sharpe_probability is not None:
            self.assertLessEqual(understated.deflated_sharpe_probability, honest.deflated_sharpe_probability)


class CliMixin(unittest.TestCase):
    def run_cli(self, env, *argv):
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output),                 contextlib.redirect_stderr(errors):
            code = main(list(argv))
        return code, json.loads(output.getvalue() or errors.getvalue())

    def setUp(self):
        self._tmp = workspace_tempdir()
        self.temp = Path(self._tmp.__enter__())
        self.env = {"ASTRA_DATABASE": str(self.temp / "state.sqlite3")}
        self.other_csv = self.temp / "independent.csv"
        write_csv(self.other_csv, INDEPENDENT_BARS)

    def tearDown(self):
        self._tmp.__exit__(None, None, None)


class TrialLogTamperTests(CliMixin):
    """CLI path: experiments are logged, but the log is mutable and not read at promotion."""

    def explore_then_register(self):
        code, first = self.run_cli(self.env, "evaluate-strategies")  # default fixture, 7 variants, no holdout
        self.assertEqual(code, 0, first)
        code, second = self.run_cli(
            self.env, "evaluate-strategies", "--csv", str(self.other_csv), "--source-type", "SYNTHETIC",
            "--validation-bars", "40", "--holdout-bars", "40",
            "--evaluate-holdout", "--register", "--model-id", "wf", "--version", "1",
        )
        self.assertEqual(code, 0, second)
        return first, second

    # FIXED IN LOT 4 (was BASELINE FAIL D-13): the CLI logs 14 variants across two experiments but the evidence declares 7.
    def test_s45_cli_evidence_counts_all_logged_experiments(self):
        _first, second = self.explore_then_register()
        _code, models = self.run_cli(self.env, "models")
        logged = sum(item["variants_tested"] for item in models["experiments"])
        self.assertGreaterEqual(second["evidence"]["variants_tested"], logged)

    # FIXED IN LOT 4 (was BASELINE FAIL D-14): deleting an experiment row (local SQLite write) is not detected; the trial log
    # is not append-only and promotion does not consult it.
    @secure_if_write_refused
    def test_s46_deleted_experiment_row_is_detected_at_promotion(self):
        first, _second = self.explore_then_register()
        registry = ModelRegistry(self.env["ASTRA_DATABASE"])
        before = set(registry.check_promotion("wf", "1"))
        sql(self.env["ASTRA_DATABASE"], "DELETE FROM experiments WHERE experiment_id = ?", (first["experiment_id"],))
        after = set(registry.check_promotion("wf", "1"))
        self.assertTrue(after - before, "a deleted trial is invisible to the promotion gates")


def write_csv(path, bars):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "instrument", "open", "high", "low", "close", "volume", "bid", "ask"])
        for bar in bars:
            writer.writerow([
                bar.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"), bar.instrument, repr(bar.open), repr(bar.high),
                repr(bar.low), repr(bar.close), repr(bar.volume), repr(bar.bid), repr(bar.ask),
            ])


# --- 7. CLI flags versus direct calls -------------------------------------------------


class CliBypassTests(CliMixin):
    FIXTURE = PROJECT_ROOT / "data" / "synthetic_eurusd_m15.csv"

    def test_s47_register_without_holdout_flag_is_refused(self):
        code, error = self.run_cli(self.env, "evaluate-strategies", "--register", "--model-id", "wf", "--version", "1")
        self.assertEqual(code, 1)
        self.assertIn("--evaluate-holdout", error["reason"])

    def test_s48_cli_register_after_cli_holdout_is_refused(self):
        code, _ = self.run_cli(self.env, "evaluate-strategies", "--evaluate-holdout")
        self.assertEqual(code, 0)
        code, error = self.run_cli(self.env, "evaluate-strategies", "--evaluate-holdout", "--register",
                                   "--model-id", "wf", "--version", "1")
        self.assertEqual(code, 1)
        self.assertIn("before its holdout is scored", error["reason"])
        code, models = self.run_cli(self.env, "models")
        self.assertEqual(models["models"], [])

    def test_s49_direct_register_after_cli_holdout_fails_provenance(self):
        code, result = self.run_cli(self.env, "evaluate-strategies", "--evaluate-holdout")
        self.assertEqual(code, 0)
        report = result["report"]
        selected = next(item for item in report["final_candidate_selection"] if item["selected"])
        registry = ModelRegistry(self.env["ASTRA_DATABASE"])
        registry.register_candidate(
            actor=AGENT, model_id="direct", version="1",
            manifest={"dataset_identity": {"canonical_sha256": report["dataset_identity_sha256"]},
                      "feature_set_version": "1", "code_sha256": report["implementation_sha256"],
                      "params": selected["config"], "plan_sha256": report["plan_sha256"],
                      "candidate_id": selected["candidate_id"]},
            criteria=PromotionCriteria(),
        )
        attempt = BacktestStore(self.env["ASTRA_DATABASE"]).holdout_for_protocol(report["plan_sha256"])
        registry.record_evaluation(actor=AGENT, model_id="direct", version="1", kind="holdout",
                                   evidence=result["evidence"], holdout_evaluation_id=attempt["evaluation_id"])
        self.assertIn("criteria_registered_after_holdout_was_scored", registry.check_promotion("direct", "1"))

    def test_s50_cli_other_protocol_flags_on_same_data_are_refused(self):
        code, _ = self.run_cli(self.env, "evaluate-strategies", "--evaluate-holdout")
        self.assertEqual(code, 0)
        for flags in (("--holdout-bars", "19"), ("--minimum-candidate-trades", "0"), ("--validation-bars", "21")):
            with self.subTest(flags=flags):
                code, error = self.run_cli(self.env, "evaluate-strategies", "--evaluate-holdout", *flags)
                self.assertEqual(code, 1, error)

    def test_s51_cli_byte_level_copy_of_fixture_is_refused(self):
        code, _ = self.run_cli(self.env, "evaluate-strategies", "--evaluate-holdout")
        self.assertEqual(code, 0)
        copy = self.temp / "copy.csv"
        shutil.copyfile(self.FIXTURE, copy)
        copy.write_bytes(copy.read_bytes().replace(b"Z,", b"+00:00,"))  # different source bytes, same bars
        code, error = self.run_cli(self.env, "evaluate-strategies", "--csv", str(copy), "--source-type",
                                   "SYNTHETIC", "--evaluate-holdout")
        self.assertEqual(code, 1, error)
        self.assertRegex(error["reason"], "already sealed|already scored")

    # FIXED IN LOT 4 (was BASELINE FAIL D-15): CLI end-to-end version of D-3: the bundled fixture moved 30 days later and nudged
    # by 0.00001 on one holdout bar is scored a second time and a fresh version is registered against it.
    def test_s52_cli_time_shifted_and_nudged_fixture_is_refused(self):
        code, _ = self.run_cli(self.env, "evaluate-strategies", "--evaluate-holdout")
        self.assertEqual(code, 0)
        with open(self.FIXTURE, encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        nudge = len(rows) - 10
        for index, row in enumerate(rows):
            moved = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) + timedelta(days=30)
            row["timestamp"] = moved.strftime("%Y-%m-%dT%H:%M:%SZ")
            if index == nudge:
                for field in ("open", "high", "low", "close", "bid", "ask"):
                    row[field] = f"{float(row[field]) + 0.00001:.5f}"
        copy = self.temp / "reused.csv"
        with open(copy, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        code, _result = self.run_cli(self.env, "evaluate-strategies", "--csv", str(copy), "--source-type", "SYNTHETIC",
                                    "--evaluate-holdout", "--register", "--model-id", "reused", "--version", "1")
        self.assertNotEqual(code, 0, "the same holdout was scored a second time")


# --- 8. misleading PBO / DSR values ---------------------------------------------------

NON_CONFIDENT = ("LOW_INFORMATION", "UNAVAILABLE", "INCOMPLETE")


_RNG = random.Random(3)
DEGENERATE_BASE = tuple(_RNG.gauss(0.1, 1.0) for _ in range(32))
DEGENERATE_RETURNS = tuple(0.002 + 0.01 * _RNG.gauss(0.0, 1.0) for _ in range(60))
DEGENERATE_LABELS = tuple(f"p{i}" for i in range(32))


class DegenerateStatisticsTests(unittest.TestCase):
    BASE = DEGENERATE_BASE
    RETURNS = DEGENERATE_RETURNS
    LABELS = DEGENERATE_LABELS
    rng = random.Random(4)

    def assess(self, matrix, returns=None, variants=None):
        width = len(matrix[0]) if matrix else 2
        return assess_overfitting(matrix, self.LABELS[: len(matrix)], variants_tested=variants or width,
                                  dsr_returns=self.RETURNS if returns is None else returns,
                                  trial_sharpe_variance=None)

    def assert_no_confident_pbo(self, matrix):
        try:
            result = self.assess(matrix)
        except ValueError:
            return  # refusing to compute is acceptable (fail closed)
        self.assertIsNone(result.pbo, f"confident PBO {result.pbo} from degenerate input")
        self.assertIn(result.status, NON_CONFIDENT)

    # FIXED IN LOT 4 (was BASELINE FAIL D-16): identical variants give PBO=1.0 with status COMPLETED (fails safe, but the
    # number is an artefact of tie-breaking, not a measurement).
    def test_s53_all_equal_variants(self):
        self.assert_no_confident_pbo([[value] * 4 for value in self.BASE])

    # FIXED IN LOT 4 (was BASELINE FAIL D-17): variants differing only by float noise (1e-13) give PBO=0.0, COMPLETED: a
    # reassuring "no overfitting" that would pass max_pbo. Severity high.
    def test_s54_variants_equal_up_to_float_noise(self):
        self.assert_no_confident_pbo([[value + 1e-13 * column for column in range(4)] for value in self.BASE])

    # FIXED IN LOT 4 (was BASELINE FAIL D-18): positively rescaled copies have identical Sharpe; the PBO (about 0.84) comes from
    # float rounding in the tie-break.
    def test_s55_rescaled_copies_tie_on_sharpe(self):
        self.assert_no_confident_pbo([[value * (column + 1) for column in range(4)] for value in self.BASE])

    def test_s56_single_variant(self):
        self.assert_no_confident_pbo([[value] for value in self.BASE])

    def test_s57_empty_periods(self):
        self.assert_no_confident_pbo([])
        self.assert_no_confident_pbo([[0.0] * 4 for _ in range(32)])

    def test_s58_non_finite_cells(self):
        matrix = [[self.rng.gauss(0.0, 1.0) for _ in range(4)] for _ in range(32)]
        matrix[3][0] = float("nan")
        self.assert_no_confident_pbo(matrix)
        matrix[3][0] = float("inf")
        self.assert_no_confident_pbo(matrix)

    def test_s59_constant_or_short_returns_give_no_dsr(self):
        matrix = [[self.rng.gauss(0.0, 1.0) for _ in range(4)] for _ in range(32)]
        for name, returns in (("constant", [0.01] * 60), ("three", [0.01, 0.02, 0.015]), ("empty", []),
                              ("noise", [0.01 + 1e-15 * (i % 2) for i in range(60)])):
            with self.subTest(name):
                result = self.assess(matrix, returns=returns)
                self.assertIsNone(result.deflated_sharpe_probability)
                self.assertIn(result.status, NON_CONFIDENT)

    # FIXED IN LOT 4 (was BASELINE FAIL D-19): 59 zero-P&L trades and one winner (a single informative observation) pass the
    # 30-return minimum and yield a numeric deflated Sharpe with status COMPLETED.
    def test_s59b_single_informative_return_gives_no_dsr(self):
        matrix = [[self.rng.gauss(0.0, 1.0) for _ in range(4)] for _ in range(32)]
        result = self.assess(matrix, returns=[0.0] * 59 + [0.05])
        self.assertIsNone(result.deflated_sharpe_probability)

    def test_s60_end_to_end_report_on_flat_market_is_not_confident(self):
        flat = [replace(bar, open=1.1, high=1.1001, low=1.0999, close=1.1, bid=1.09995, ask=1.10005)
                for bar in BARS]
        try:
            report = evaluate(bars=flat)
        except ValueError:
            return
        self.assertIsNone(report.overfitting.pbo)
        self.assertIsNone(report.overfitting.deflated_sharpe_probability)
        self.assertIn(report.overfitting.status, NON_CONFIDENT)

    def test_s61_evidence_fields_stay_none_when_statistics_are_missing(self):
        for status in NON_CONFIDENT:
            evidence = holdout_evidence({"status": "COMPLETED", "selected_candidate": None, "cost_sensitivity": [],
                                         "overfitting": {"status": status, "pbo": None,
                                                         "deflated_sharpe_probability": None},
                                         "variants_tested": 7, "edge_vs_baseline": None})
            self.assertEqual(tuple(evidence), EVIDENCE_FIELDS)
            self.assertIsNone(evidence["pbo"])
            self.assertIsNone(evidence["deflated_sharpe_probability"])


if __name__ == "__main__":
    unittest.main()
