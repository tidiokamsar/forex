"""Lot 4 integrator tests: holdout evidence is re-derived from market bars before promotion."""

import contextlib
import json
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from _support import workspace_tempdir
from astra.governance import Actor, Role
from astra.registry import ModelRegistry, PromotionCriteria
from astra.storage import BacktestStore
from astra.strategy_validation import evaluate_strategy_holdout_once, holdout_evidence, rederive_holdout_evidence
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
    def setUp(self):
        self._tmp = workspace_tempdir()
        temp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.database = f"{temp}/astra.sqlite3"
        self.store = BacktestStore(self.database)
        self.store.initialize()
        self.registry = ModelRegistry(self.database)
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
        derived = rederive_holdout_evidence(BARS, self.report.plan_manifest)
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


if __name__ == "__main__":
    unittest.main()
