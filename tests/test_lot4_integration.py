"""Lot 4 integrator tests: holdout evidence is re-derived from market bars before promotion."""

import contextlib
import json
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from _support import workspace_tempdir
from astra import holdout_reuse
from astra.anchoring import LocalFileAnchor
from astra.backup import create_backup, restore_backup
from astra.governance import Actor, Role
from astra.registry import ModelRegistry, PromotionCriteria
from astra.storage import BacktestStore
from astra.strategy_validation import evaluate_strategy_holdout_once, holdout_evidence, rederive_holdout_evidence
from test_execution import OPERATOR
from test_strategy_validation import BARS, PROTOCOL, evaluate, overfitting_double

AGENT = Actor("agent", Role.RESEARCH_AGENT)
REDERIVATION_FAILURES = {
    "holdout_not_rederived_from_market_data",
    "holdout_report_differs_from_rederivation",
    "holdout_rederivation_unsupported_for_this_plan",
}


@patch("astra.strategy_validation.MIN_DSR_RETURNS", 0)
@patch("astra.strategy_validation.MIN_PBO_NONZERO_ROW_FRACTION", 0.0)
class HoldoutRederivationTests(unittest.TestCase):
    ANCHORED = False

    def setUp(self):
        self._tmp = workspace_tempdir()
        temp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.temp = temp
        self.database = f"{temp}/astra.sqlite3"
        self.anchor = LocalFileAnchor(f"{temp}/anchor.jsonl") if self.ANCHORED else None
        self.store = BacktestStore(self.database, anchor=self.anchor)
        self.store.initialize()
        self.registry = ModelRegistry(self.database, anchor=self.anchor)
        self.registry.initialize()
        with overfitting_double(pbo=0.25, probability=0.5):
            self.report = evaluate(store=self.store)
            selected = next(item for item in self.report.final_candidate_selection if item.selected)
            self.registry.register_candidate(
                actor=AGENT, model_id="wf", version="1",
                manifest={"dataset_identity": {"canonical_sha256": self.report.dataset_identity_sha256},
                          "feature_set_version": "1", "code_sha256": self.report.implementation_sha256,
                          "params": selected.config, "plan_sha256": self.report.plan_sha256,
                          "candidate_id": selected.candidate_id},
                criteria=PromotionCriteria(),
            )
            self.holdout = evaluate_strategy_holdout_once(BARS, None, report=self.report, protocol=PROTOCOL,
                                                          store=self.store)
        attempt = self.store.holdout_for_protocol(self.report.plan_sha256)
        self.registry.record_evaluation(actor=AGENT, model_id="wf", version="1", kind="holdout",
                                        evidence=holdout_evidence(self.holdout),
                                        holdout_evaluation_id=attempt["evaluation_id"])
        self.evaluation_id = attempt["evaluation_id"]

    def test_rederivation_reproduces_the_stored_holdout(self):
        # Lot 4 round 4 (C-2): re-derivation needs the store that consumed the holdout.
        derived = rederive_holdout_evidence(BARS, self.report.plan_manifest, store=self.store)
        evidence = holdout_evidence(self.holdout)
        self.assertEqual(derived["selected_candidate_id"], self.holdout.selected_candidate_id)
        for key in ("trade_count", "net_pnl", "profit_factor", "max_drawdown_pct", "edge_vs_baseline",
                    "cost_stress_net_pnl"):
            self.assertEqual(derived[key], evidence[key], key)
        failures = set(self.registry.check_promotion("wf", "1", holdout_bars=BARS))
        self.assertFalse(REDERIVATION_FAILURES & failures, failures)

    def test_promotion_without_market_bars_is_refused(self):
        self.assertIn("holdout_not_rederived_from_market_data", self.registry.check_promotion("wf", "1"))

    def test_other_bars_do_not_reproduce_the_plan(self):
        moved = [replace(bar, close=bar.close * 1.001, high=bar.high * 1.001, low=bar.low * 1.001,
                         open=bar.open * 1.001) for bar in BARS]
        failures = self.registry.check_promotion("wf", "1", holdout_bars=moved)
        self.assertTrue(any(item.startswith("holdout_rederivation_failed") for item in failures), failures)

    def test_report_forged_with_the_triggers_dropped_is_caught(self):
        with contextlib.closing(sqlite3.connect(self.database)) as db, db:
            report = json.loads(db.execute("SELECT report_json FROM holdout_evaluations WHERE evaluation_id = ?",
                                           (self.evaluation_id,)).fetchone()[0])
            if report.get("selected_candidate"):
                report["selected_candidate"]["net_pnl"] = 9_999.0
            report["edge_vs_baseline"] = 9_999.0
            db.execute("DROP TRIGGER holdout_evaluations_final")
            db.execute("UPDATE holdout_evaluations SET report_json = ? WHERE evaluation_id = ?",
                       (json.dumps(report), self.evaluation_id))
        failures = self.registry.check_promotion("wf", "1", holdout_bars=BARS)
        self.assertIn("holdout_report_differs_from_rederivation", failures)


INTEGRITY_FAILURES = REDERIVATION_FAILURES | {
    "evidence_anchor_missing", "evidence_anchor_unreadable", "holdout_not_anchored", "holdout_reuse_check_missing",
    "holdout_reuse_suspected", "holdout_reuse_undetermined", "holdout_reuse_policy_below_floor",
}


class AnchoredPromotionTests(HoldoutRederivationTests):
    """Lot 4 round 3: the external evidence anchor gates promotion and survives a database restore."""

    ANCHORED = True

    def integrity(self, failures):
        return {item for item in failures if item in INTEGRITY_FAILURES or item.startswith("evidence_anchor")}

    def test_clean_anchored_store_has_no_integrity_failure(self):
        self.assertEqual(self.store.reuse_check(self.report.plan_sha256)["verdict"], holdout_reuse.CLEAR)
        # Lot 4 round 4 (C-3): a non-production anchor fails unless the human reviewer waives it explicitly.
        self.assertEqual(self.integrity(self.registry.check_promotion("wf", "1", holdout_bars=BARS)),
                         {"evidence_anchor_not_production_grade"})
        self.assertEqual(self.integrity(self.registry.check_promotion("wf", "1", holdout_bars=BARS,
                                                                      allow_non_production_anchor=True)), set())
        # Lot 4 round 5 (data provenance): the fixture's plan carries no source label, so it fails closed.
        self.assertIn("dataset_provenance_unknown", self.registry.check_promotion("wf", "1", holdout_bars=BARS))
        self.assertFalse(self.anchor.production_grade)

    def test_missing_anchor_blocks_promotion(self):
        with patch.dict("os.environ", {}, clear=True):
            registry = ModelRegistry(self.database)
        self.assertIn("evidence_anchor_missing", registry.check_promotion("wf", "1", holdout_bars=BARS))

    def test_restoring_an_older_backup_blocks_rescoring_and_promotion(self):
        # A backup taken before the holdout was scored is restored; the anchor still knows it was spent.
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            holdout_id = self.store.holdout_for_protocol(self.report.plan_sha256)["evaluation_id"]
            self.assertIsNotNone(db.execute("SELECT 1 FROM holdout_evaluations WHERE evaluation_id = ?",
                                            (holdout_id,)).fetchone())
        fresh = f"{self.temp}/fresh.sqlite3"
        # Built without the anchor: it stands for a backup taken before the holdout was scored.
        with patch.dict("os.environ", {}, clear=True):
            fresh_store = BacktestStore(fresh)
        fresh_store.initialize()
        with overfitting_double():
            evaluate(store=fresh_store)  # same plan, nothing scored: what an old backup looks like
        backup = create_backup(fresh, f"{self.temp}/backups")
        restore_backup(backup.path, self.database, actor=OPERATOR, reason="restore drill")
        restored = BacktestStore(self.database, anchor=self.anchor)
        with overfitting_double(), self.assertRaisesRegex(ValueError, "evidence anchor"):
            evaluate_strategy_holdout_once(BARS, None, report=self.report, protocol=PROTOCOL, store=restored)
        # The restored store no longer agrees with the anchor, which every holdout path and promotion checks.
        from astra.anchoring import verify_store_against_anchor

        verification = verify_store_against_anchor(self.database, self.anchor)
        self.assertFalse(verification.ok)
        self.assertIn("consumed_holdout_missing", verification.errors)
        with self.assertRaisesRegex(ValueError, "inconsistent with the evidence anchor"):
            restored.require_anchor_consistency()

    def test_consumed_holdout_cannot_be_resealed_in_a_new_store(self):
        other = BacktestStore(f"{self.temp}/other.sqlite3", anchor=self.anchor)
        other.initialize()
        # Lot 4 round 4 (C-3): refused even earlier, the anchor is bound to the original store's identity.
        with overfitting_double(), self.assertRaisesRegex(ValueError, "evidence anchor.*store_not_bound_to_anchor"):
            evaluate(store=other)


class ReuseVerdictTests(unittest.TestCase):
    """A SUSPECT or UNDETERMINED reuse verdict is recorded at seal time and cannot be cleared by assertion."""

    def seal(self, store, returns, label):
        manifest = {"dataset_identity_sha256": label * 64, "instrument": "EURUSD",
                    "holdout": {"start": "2030-01-01T00:00:00+00:00", "end": "2030-01-02T00:00:00+00:00",
                                "content_sha256": holdout_reuse.content_sha256(returns)}}
        if label != "a":
            manifest["holdout"].update(start=f"203{ord(label) - 96}-01-01T00:00:00+00:00",
                                       end=f"203{ord(label) - 96}-01-02T00:00:00+00:00")
        from astra.validation import _canonical_json, _sha256

        protocol_id = _sha256(_canonical_json(manifest))
        store.seal_research_protocol(protocol_id=protocol_id, dataset_identity_sha256=label * 64,
                                     protocol_version="t", plan_manifest=manifest, holdout_returns=returns)
        return protocol_id

    def test_suspect_and_conflict_verdicts(self):
        import random

        rng = random.Random(4)
        base = [round(rng.gauss(0, 1e-4), 12) for _ in range(40)]
        with workspace_tempdir() as temp:
            store = BacktestStore(f"{temp}/s.sqlite3")
            store.initialize()
            self.seal(store, base, "a")
            with self.assertRaisesRegex(ValueError, "price path matches"):
                self.seal(store, [round(-v, 12) for v in reversed(base)], "b")  # inverted and reversed copy
            suspect = None
            for seed in range(200):  # a correlated but distinct path that lands between the thresholds
                noise = random.Random(seed)
                candidate = [round(0.955 * v + 0.296 * noise.gauss(0, 1e-4), 12) for v in base]
                verdict = holdout_reuse.assess_reuse(candidate, base).verdict
                if verdict == holdout_reuse.SUSPECT:
                    suspect = candidate
                    break
            self.assertIsNotNone(suspect)
            protocol_id = self.seal(store, suspect, "c")
            self.assertEqual(store.reuse_check(protocol_id)["verdict"], holdout_reuse.SUSPECT)
            with contextlib.closing(sqlite3.connect(store.path)) as db, self.assertRaises(sqlite3.IntegrityError), db:
                db.execute("UPDATE holdout_reuse_checks SET verdict = 'CLEAR' WHERE protocol_id = ?", (protocol_id,))


if __name__ == "__main__":
    unittest.main()
