import contextlib
import io
import json
import os
import sqlite3
import statistics
import unittest
import uuid
from unittest.mock import patch

from _support import accept_unverified_evidence, workspace_tempdir
from astra import attempts, strategy_validation
from astra.attempts import (
    AttemptIdentity,
    AttemptLog,
    AttemptStatus,
    trial_statistics,
    trial_statistics_for_protocol,
    verify_attempt_chain,
)
from astra.governance import Actor, Role
from astra.models import BacktestConfig
from astra.registry import ModelRegistry, PromotionCriteria
from astra.storage import BacktestStore
from astra.strategy_validation import default_candidates, evaluate_strategy_holdout_once
from astra.validation import WalkForwardProtocol, _holdout_returns, evaluate_walk_forward
from test_features import make_synthetic_bars
from test_strategy_validation import BARS, PROTOCOL, evaluate, overfitting_double

AGENT = Actor("agent", Role.RESEARCH_AGENT)
TABLE = attempts.TABLE
GOOD_EVIDENCE = {
    "trade_count": 45,
    "net_pnl": 120.0,
    "profit_factor": 1.3,
    "max_drawdown_pct": 4.0,
    "edge_vs_baseline": 0.02,
    "cost_stress_net_pnl": 15.0,
    "holdout_used": True,
    "pbo": 0.1,
    "deflated_sharpe_probability": 0.97,
}


def raw(database):
    """A plain connection, as an owner with file access would use."""

    return contextlib.closing(sqlite3.connect(database, isolation_level=None))


def drop_triggers(database):
    with raw(database) as db:
        for name in ("no_update", "no_delete", "chain_head"):
            db.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{name}")


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        self.database = f"{self._tmp.__enter__()}/astra.sqlite3"
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.store = BacktestStore(self.database)
        self.store.initialize()
        self.log = AttemptLog(self.database)

    def register(self, report, version="1", criteria=None):
        registry = ModelRegistry(self.database, evidence_verifier=accept_unverified_evidence)
        registry.initialize()
        selected = next(item for item in report.final_candidate_selection if item.selected)
        registry.register_candidate(
            actor=AGENT, model_id="wf", version=version,
            manifest={"dataset_identity": {"canonical_sha256": report.dataset_identity_sha256}, "feature_set_version": "1",
                      "code_sha256": report.implementation_sha256, "params": selected.config,
                      "plan_sha256": report.plan_sha256, "candidate_id": selected.candidate_id},
            criteria=criteria or PromotionCriteria(),
        )
        return registry


class RecordingTests(StoreCase):
    def test_every_variant_of_every_fold_and_run_is_recorded_selected_or_rejected(self):
        with overfitting_double():
            report = evaluate(store=self.store)
        items = self.log.attempts(protocol_id=report.plan_sha256)
        folds = [item for item in items if item["kind"] == attempts.KIND_FOLD]
        runs = [item for item in items if item["kind"] == attempts.KIND_RUN]
        self.assertEqual(len(folds), len(default_candidates()) * PROTOCOL.fold_count)
        self.assertEqual(len(runs), len(default_candidates()))
        for fold in report.folds:
            statuses = {item["candidate_id"]: item["status"] for item in folds if item["fold"] == fold.fold}
            for trial in fold.trials:
                expected = "COMPLETED_SELECTED" if trial.selected else "COMPLETED_REJECTED"
                self.assertEqual(statuses[trial.candidate_id], expected)
        selected_runs = [item for item in runs if item["status"] == "COMPLETED_SELECTED"]
        self.assertEqual([item["candidate_id"] for item in selected_runs], [report.selected_candidate_id])
        rejected = [item for item in items if item["status"] == "COMPLETED_REJECTED"]
        self.assertTrue(rejected and all(item["reason"] for item in rejected))
        first = runs[0]
        for key in ("params_sha256", "code_sha256", "dataset_sha256", "dataset_identity_sha256",
                    "holdout_content_sha256", "instrument", "period_start", "period_end", "started_at", "finished_at",
                    "astra_version", "strategy_version"):
            self.assertTrue(first[key], key)
        self.assertEqual(first["code_sha256"], report.implementation_sha256)
        self.assertEqual(first["params_sha256"], attempts._sha256_text(attempts.canonical_json(first["params"])))

        stats = trial_statistics_for_protocol(self.database, report.plan_sha256)
        self.assertEqual(stats.count, report.variants_tested)
        # The store-wide Sharpe dispersion is the one the walk-forward already uses for the DSR.
        # Registry Sharpes are in the DSR unit (per-bar mark-to-market), equal to the plan's own trial Sharpes.
        for recorded, planned in zip(sorted(stats.trial_sharpes), sorted(report.overfitting.trial_sharpes), strict=True):
            self.assertAlmostEqual(recorded, planned)
        if len(stats.trial_sharpes) >= 2:
            self.assertAlmostEqual(statistics.variance(stats.trial_sharpes), stats.trial_sharpe_variance)
        self.assertTrue(verify_attempt_chain(self.database).ok)

    def test_retrying_the_same_plan_does_not_inflate_the_count(self):
        with overfitting_double():
            report = evaluate(store=self.store)
            evaluate(store=self.store)
        stats = trial_statistics_for_protocol(self.database, report.plan_sha256)
        self.assertEqual(stats.count, report.variants_tested)
        self.assertEqual(stats.attempts, 2 * report.variants_tested * (PROTOCOL.fold_count + 1))

    def test_an_exception_leaves_failed_attempts_that_still_count(self):
        with overfitting_double(), patch.object(strategy_validation, "_selection_score", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                evaluate(store=self.store)
        items = self.log.attempts()
        self.assertTrue(items)
        self.assertEqual({item["status"] for item in items}, {"FAILED"})
        self.assertTrue(all("RuntimeError: boom" in item["reason"] for item in items))
        self.assertEqual(trial_statistics(self.database).count, len(default_candidates()))
        self.assertTrue(verify_attempt_chain(self.database).ok)

    def test_interruption_is_recorded_and_a_started_only_attempt_counts_as_interrupted(self):
        with overfitting_double(), patch.object(strategy_validation, "_selection_score", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                evaluate(store=self.store)
        self.assertEqual({item["status"] for item in self.log.attempts()}, {"INTERRUPTED"})

        # A process killed mid-run leaves STARTED without a terminal event.
        (attempt_id,) = self.log.start([AttemptIdentity(
            kind=attempts.KIND_RUN, run_id="crashed", candidate_id="c-x", family="momentum", params={"lookback": 99},
            instrument="EURUSD", period_start=BARS[0].timestamp.isoformat(), period_end=BARS[-1].timestamp.isoformat(),
        )])
        state = next(item for item in self.log.attempts() if item["attempt_id"] == attempt_id)
        self.assertEqual(state["status"], "INTERRUPTED")
        self.assertEqual(state["recorded_status"], "STARTED")
        self.assertEqual(trial_statistics(self.database, instrument="EURUSD").count, len(default_candidates()) + 1)
        # Abandoning appends an event; the trial still counts.
        self.log.abandon(attempt_id, reason="operator: machine lost power")
        state = next(item for item in self.log.attempts() if item["attempt_id"] == attempt_id)
        self.assertEqual(state["status"], "ABANDONED")
        self.assertEqual(trial_statistics(self.database, instrument="EURUSD").count, len(default_candidates()) + 1)
        with self.assertRaises(ValueError):
            self.log.abandon(attempt_id, reason="twice")
        self.assertEqual(trial_statistics(self.database, instrument="GBPUSD").count, 0)

    def test_holdout_scoring_is_recorded_but_not_counted_as_a_trial(self):
        with overfitting_double():
            report = evaluate(store=self.store)
            evaluate_strategy_holdout_once(BARS, report=report, protocol=PROTOCOL, store=self.store, experiment_id="exp-1")
        holdouts = [item for item in self.log.attempts() if item["kind"] == attempts.KIND_HOLDOUT]
        self.assertEqual(len(holdouts), 1)
        self.assertEqual(holdouts[0]["status"], "COMPLETED_SELECTED")
        self.assertEqual(holdouts[0]["candidate_id"], report.selected_candidate_id)
        self.assertEqual(holdouts[0]["experiment_id"], "exp-1")
        self.assertEqual(trial_statistics_for_protocol(self.database, report.plan_sha256).count, report.variants_tested)

    def test_sma_walk_forward_records_attempts_too(self):
        candidates = (BacktestConfig(fast_window=5, slow_window=13), BacktestConfig(fast_window=8, slow_window=21))
        protocol = WalkForwardProtocol(fold_count=2, minimum_training_bars=60, validation_bars=40, holdout_bars=40)
        report = evaluate_walk_forward(BARS, candidates, hypothesis="SMA beats flat after costs", protocol=protocol,
                                       store=self.store)
        stats = trial_statistics_for_protocol(self.database, report.plan_sha256)
        self.assertEqual(stats.count, 2)
        self.assertEqual(len(self.log.attempts(protocol_id=report.plan_sha256)), 2 * (protocol.fold_count + 1))


class StoreWideCountTests(StoreCase):
    def test_new_plan_with_fewer_variants_does_not_reduce_the_count(self):
        with overfitting_double():
            first = evaluate(store=self.store)
            # A later plan on overlapping data (non-overlapping holdout) with a narrower grid.
            narrow = default_candidates()[:2]
            second = evaluate(bars=make_synthetic_bars(480), candidates=narrow, store=self.store)
        self.assertEqual(second.variants_tested, 2)
        before = first.variants_tested
        for plan in (first.plan_sha256, second.plan_sha256):
            stats = trial_statistics_for_protocol(self.database, plan)
            self.assertGreaterEqual(stats.count, before)
            self.assertEqual(stats.count, before + 2)
            self.assertEqual(set(stats.protocols), {first.plan_sha256, second.plan_sha256})

        registry = self.register(second)
        registry.record_evaluation(actor=AGENT, model_id="wf", version="1", kind="holdout",
                                   evidence={**GOOD_EVIDENCE, "variants_tested": second.variants_tested},
                                   holdout_evaluation_id=f"test-{uuid.uuid4()}")
        failures = registry.check_promotion("wf", "1")
        self.assertIn("trial_count_understated", failures)

        honest = self.register(second, version="2")
        honest.record_evaluation(actor=AGENT, model_id="wf", version="2", kind="holdout",
                                 evidence={**GOOD_EVIDENCE, "variants_tested": before + 2},
                                 holdout_evaluation_id=f"test-{uuid.uuid4()}")
        self.assertEqual(honest.check_promotion("wf", "2"), ())
        with raw(self.database) as db:
            row = db.execute("SELECT evidence_json FROM model_evaluations WHERE version = '2'").fetchone()
        provenance = json.loads(row[0])["_provenance"]
        self.assertEqual(provenance["store_trial_count_at_record"], before + 2)

    def test_deleting_a_candidate_or_retraining_keeps_every_attempt(self):
        with overfitting_double():
            report = evaluate(store=self.store)
        self.register(report)
        events_before = len(self.log.events())
        with raw(self.database) as db:
            db.execute("DELETE FROM model_versions WHERE model_id = 'wf'")
            db.execute("DELETE FROM model_status_events WHERE model_id = 'wf'")
        self.assertEqual(len(self.log.events()), events_before)
        with overfitting_double():
            evaluate(store=self.store)  # retraining on the same plan appends, never replaces
        self.assertGreater(len(self.log.events()), events_before)
        self.assertEqual(trial_statistics_for_protocol(self.database, report.plan_sha256).count, report.variants_tested)
        self.assertTrue(verify_attempt_chain(self.database).ok)

    def test_plan_sealed_without_its_attempts_blocks_promotion(self):
        with overfitting_double():
            report = evaluate()  # no store: nothing recorded
        self.store.seal_research_protocol(protocol_id=report.plan_sha256,
                                          dataset_identity_sha256=report.dataset_identity_sha256,
                                          protocol_version=report.protocol_version, plan_manifest=report.plan_manifest,
                                          holdout_returns=_holdout_returns(tuple(BARS), len(BARS) - PROTOCOL.holdout_bars))
        registry = self.register(report)
        registry.record_evaluation(actor=AGENT, model_id="wf", version="1", kind="holdout",
                                   evidence={**GOOD_EVIDENCE, "variants_tested": 7},
                                   holdout_evaluation_id=f"test-{uuid.uuid4()}")
        self.assertIn("attempt_log_missing_for_plan", registry.check_promotion("wf", "1"))


class AppendOnlyTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.ids = self.log.start([
            AttemptIdentity(kind=attempts.KIND_RUN, run_id="r", candidate_id=f"c{index}", family="sma",
                            params={"fast": index}, instrument="EURUSD")
            for index in range(4)
        ])
        self.log.finish([attempts.AttemptOutcome(attempt_id, AttemptStatus.COMPLETED_REJECTED, metrics={"net": 1.0},
                                                 trial_sharpe=0.1 * index, reason="lower score")
                         for index, attempt_id in enumerate(self.ids)])

    def test_update_and_delete_are_refused_by_triggers(self):
        with raw(self.database) as db:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only: UPDATE refused"):
                db.execute(f"UPDATE {TABLE} SET status = 'COMPLETED_SELECTED' WHERE seq = 5")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only: DELETE refused"):
                db.execute(f"DELETE FROM {TABLE} WHERE seq = 1")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "prev_hash must be the current chain head"):
                db.execute(f"INSERT INTO {TABLE} SELECT seq + 100, event_id || 'x', attempt_id, status, recorded_at, kind, "
                           "run_id, experiment_id, protocol_id, fold, candidate_id, model_id, model_version, family, strategy, "
                           "strategy_version, params_json, params_sha256, code_sha256, astra_version, dataset_sha256, "
                           "dataset_identity_sha256, holdout_content_sha256, instrument, period_start, period_end, "
                           "window_start, window_end, started_at, finished_at, metrics_json, trial_sharpe, reason, "
                           f"prev_hash, row_hash || 'x' FROM {TABLE} WHERE seq = 1")
        self.assertEqual(len(self.log.events()), 8)
        self.assertTrue(verify_attempt_chain(self.database).ok)

    def test_removed_row_is_detected_after_dropping_triggers(self):
        drop_triggers(self.database)
        with raw(self.database) as db:
            db.execute(f"DELETE FROM {TABLE} WHERE seq = 3")
        result = verify_attempt_chain(self.database)
        self.assertFalse(result.ok)
        self.assertTrue(any("sequence gap" in error for error in result.errors), result.errors)
        self.assertTrue(any("triggers missing" in error for error in result.errors), result.errors)
        # Re-initializing restores the triggers but not the evidence of the removal.
        self.store.initialize()
        self.assertFalse(verify_attempt_chain(self.database).ok)

    def test_removed_last_row_edited_row_and_reordering_are_detected(self):
        drop_triggers(self.database)
        with raw(self.database) as db:
            db.execute(f"DELETE FROM {TABLE} WHERE seq = (SELECT MAX(seq) FROM {TABLE})")
        errors = verify_attempt_chain(self.database).errors
        self.assertTrue(any("sqlite_sequence" in error for error in errors), errors)

        edited = (f"UPDATE {TABLE} SET trial_sharpe = 9.0 WHERE seq = 6",)
        reordered = (
            f"UPDATE {TABLE} SET seq = -1 WHERE seq = 1",
            f"UPDATE {TABLE} SET seq = 1 WHERE seq = 2",
            f"UPDATE {TABLE} SET seq = 2 WHERE seq = -1",
        )
        with workspace_tempdir() as temp:
            for statements, expected in ((edited, "row_hash does not match"), (reordered, "prev_hash does not link")):
                database = f"{temp}/{uuid.uuid4()}.sqlite3"
                BacktestStore(database).initialize()
                log = AttemptLog(database)
                ids = log.start([AttemptIdentity(kind=attempts.KIND_RUN, run_id="r", candidate_id=f"c{i}", family="sma",
                                                 params={"i": i}) for i in range(3)])
                log.finish([attempts.AttemptOutcome(i, "COMPLETED_REJECTED", trial_sharpe=0.5) for i in ids])
                drop_triggers(database)
                with raw(database) as db:
                    for statement in statements:
                        db.execute(statement)
                result = verify_attempt_chain(database)
                self.assertFalse(result.ok)
                self.assertTrue(any(expected in error for error in result.errors), result.errors)

    def test_tampered_log_blocks_promotion(self):
        with overfitting_double():
            report = evaluate(store=self.store)
        registry = self.register(report)
        registry.record_evaluation(actor=AGENT, model_id="wf", version="1", kind="holdout",
                                   evidence={**GOOD_EVIDENCE, "variants_tested": 50},
                                   holdout_evaluation_id=f"test-{uuid.uuid4()}")
        self.assertNotIn("attempt_log_tampered", registry.check_promotion("wf", "1"))
        drop_triggers(self.database)
        with raw(self.database) as db:
            db.execute(f"DELETE FROM {TABLE} WHERE kind = 'walk_forward_run' AND status = 'COMPLETED_REJECTED'")
        self.store.initialize()
        self.assertIn("attempt_log_tampered", registry.check_promotion("wf", "1"))

    def test_full_rewrite_is_only_caught_with_an_external_anchor(self):
        anchor = self.log.chain_head()["row_hash"]
        drop_triggers(self.database)
        with raw(self.database) as db:
            db.execute(f"DELETE FROM {TABLE}")
            db.execute("DELETE FROM sqlite_sequence WHERE name = ?", (TABLE,))
        self.store.initialize()
        # A consistent rewrite (here: an emptied log) passes the internal checks...
        self.assertTrue(verify_attempt_chain(self.database).ok)
        # ...but not the check against a head kept outside the database.
        result = verify_attempt_chain(self.database, expected_head=anchor)
        self.assertFalse(result.ok)
        self.assertIn("anchored chain head not found", result.errors[0])


class CliTests(StoreCase):
    def run_cli(self, *argv):
        from astra.cli import main

        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"ASTRA_DATABASE": self.database}, clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(list(argv))
        return code, json.loads(output.getvalue() or errors.getvalue())

    def test_list_count_and_verify_are_read_only(self):
        with overfitting_double():
            report = evaluate(store=self.store)
        events = len(self.log.events())
        code, listed = self.run_cli("attempts", "list", "--protocol", report.plan_sha256)
        self.assertEqual(code, 0)
        self.assertEqual(listed["count"], report.variants_tested * (PROTOCOL.fold_count + 1))
        code, counted = self.run_cli("attempts", "count", "--protocol", report.plan_sha256)
        self.assertEqual((code, counted["trials"]["count"]), (0, report.variants_tested))
        code, counted = self.run_cli("attempts", "count", "--instrument", "EURUSD")
        self.assertEqual(counted["trials"]["count"], report.variants_tested)
        code, verified = self.run_cli("attempts", "verify", "--expected-head", listed["chain_head"]["row_hash"])
        self.assertEqual(code, 0)
        self.assertTrue(verified["verification"]["ok"])
        self.assertEqual(len(self.log.events()), events)
        drop_triggers(self.database)
        code, verified = self.run_cli("attempts", "verify")
        self.assertEqual(code, 1)
        self.assertFalse(verified["verification"]["ok"])


if __name__ == "__main__":
    unittest.main()
