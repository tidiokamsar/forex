import sqlite3
import unittest

from _support import workspace_tempdir
from astra.governance import Actor, PermissionDenied, Role
from astra.registry import ModelRegistry, ModelStatus, PromotionCriteria, PromotionError
from astra.storage import BacktestStore

REVIEWER = Actor("bob", Role.HUMAN_REVIEWER)
AGENT = Actor("agent-3", Role.RESEARCH_AGENT)
MANIFEST = {
    "dataset_identity": "abc123",
    "feature_set_version": "1",
    "code_sha256": "def456",
    "params": {"lookback": 20},
}
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
    "variants_tested": 7,
}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        tmp = self._tmp.__enter__()
        self.registry = ModelRegistry(f"{tmp}/astra.sqlite3")
        self.registry.initialize()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def register(self, version, evidence=None):
        self.registry.register_candidate(
            actor=AGENT, model_id="momentum", version=version, manifest={**MANIFEST, "params": {"v": version}},
            criteria=PromotionCriteria(),
        )
        if evidence is not None:
            self.registry.record_evaluation(model_id="momentum", version=version, kind="holdout", evidence=evidence)

    def test_unvalidated_candidate_cannot_be_promoted(self):
        self.register("1.0.0")
        with self.assertRaises(PromotionError) as caught:
            self.registry.promote(actor=REVIEWER, model_id="momentum", version="1.0.0", reason="try")
        self.assertIn("missing_holdout_evaluation", str(caught.exception))
        self.assertEqual(self.registry.get("momentum", "1.0.0")["status"], ModelStatus.CANDIDATE)

    def test_failing_evidence_is_refused_and_audited(self):
        self.register("1.0.0", {**GOOD_EVIDENCE, "net_pnl": -5.0, "trade_count": 3})
        failures = self.registry.check_promotion("momentum", "1.0.0")
        self.assertIn("net_pnl_not_positive_after_costs", failures)
        self.assertIn("insufficient_trades", failures)
        with self.assertRaises(PromotionError):
            self.registry.promote(actor=REVIEWER, model_id="momentum", version="1.0.0", reason="try")
        events = [e["event_type"] for e in BacktestStore(self.registry.path).audit_events()]
        self.assertIn("model.promotion_refused", events)

    def test_good_evidence_meets_every_gate(self):
        self.register("1.0.0", GOOD_EVIDENCE)
        self.assertEqual(self.registry.check_promotion("momentum", "1.0.0"), ())

    def test_missing_overfitting_evidence_blocks_promotion(self):
        evidence = {key: value for key, value in GOOD_EVIDENCE.items() if key not in ("pbo", "deflated_sharpe_probability")}
        self.register("1.0.0", evidence)
        failures = self.registry.check_promotion("momentum", "1.0.0")
        self.assertEqual(failures, ("overfitting_check_missing",))
        self.register("1.1.0", {**GOOD_EVIDENCE, "variants_tested": None})
        self.assertEqual(self.registry.check_promotion("momentum", "1.1.0"), ("overfitting_check_missing",))
        with self.assertRaises(PromotionError):
            self.registry.promote(actor=REVIEWER, model_id="momentum", version="1.0.0", reason="try")

    def test_high_pbo_blocks_promotion(self):
        self.register("1.0.0", {**GOOD_EVIDENCE, "pbo": 0.31})
        self.assertEqual(self.registry.check_promotion("momentum", "1.0.0"), ("pbo_above_threshold",))

    def test_low_deflated_sharpe_probability_blocks_promotion(self):
        self.register("1.0.0", {**GOOD_EVIDENCE, "deflated_sharpe_probability": 0.94})
        self.assertEqual(self.registry.check_promotion("momentum", "1.0.0"), ("deflated_sharpe_below_threshold",))

    def test_overfitting_gate_can_be_disabled_only_at_registration(self):
        self.registry.register_candidate(
            actor=AGENT, model_id="legacy", version="1", manifest=MANIFEST,
            criteria=PromotionCriteria(require_overfitting_check=False),
        )
        evidence = {key: value for key, value in GOOD_EVIDENCE.items() if key not in ("pbo", "deflated_sharpe_probability", "variants_tested")}
        self.registry.record_evaluation(model_id="legacy", version="1", kind="holdout", evidence=evidence)
        self.assertEqual(self.registry.check_promotion("legacy", "1"), ())
        with self.assertRaises(ValueError):
            PromotionCriteria(max_pbo=1.5)
        with self.assertRaises(ValueError):
            PromotionCriteria(min_deflated_sharpe_probability=-0.1)

    def test_agent_cannot_promote(self):
        self.register("1.0.0", GOOD_EVIDENCE)
        with self.assertRaises(PermissionDenied):
            self.registry.promote(actor=AGENT, model_id="momentum", version="1.0.0", reason="self-promotion")

    def test_holdout_can_only_be_recorded_once(self):
        self.register("1.0.0", GOOD_EVIDENCE)
        with self.assertRaises(PromotionError):
            self.registry.record_evaluation(model_id="momentum", version="1.0.0", kind="holdout", evidence=GOOD_EVIDENCE)

    def test_criteria_tampering_is_detected(self):
        self.register("1.0.0", GOOD_EVIDENCE)
        db = sqlite3.connect(self.registry.path)
        try:
            with db:
                db.execute("UPDATE model_versions SET criteria_json = '{\"min_trades\": 1}'")
        finally:
            db.close()
        self.assertEqual(self.registry.check_promotion("momentum", "1.0.0"), ("criteria_tampered",))

    def test_promotion_then_rollback_restores_known_version(self):
        self.register("1.0.0", GOOD_EVIDENCE)
        self.registry.promote(actor=REVIEWER, model_id="momentum", version="1.0.0", reason="passed gates")
        self.register("1.1.0", GOOD_EVIDENCE)
        self.registry.promote(actor=REVIEWER, model_id="momentum", version="1.1.0", reason="passed gates")
        self.assertEqual(self.registry.get("momentum", "1.0.0")["status"], ModelStatus.RETIRED)
        restored = self.registry.rollback(actor=Actor("watchdog", Role.SYSTEM), model_id="momentum", reason="drift alert")
        self.assertEqual(restored, "1.0.0")
        champion = self.registry.champion("momentum")
        self.assertEqual(champion["version"], "1.0.0")
        self.assertEqual(champion["status"], ModelStatus.CHAMPION)
        self.assertEqual(self.registry.get("momentum", "1.1.0")["status"], ModelStatus.RETIRED)

    def test_rollback_without_previous_version_fails(self):
        self.register("1.0.0", GOOD_EVIDENCE)
        self.registry.promote(actor=REVIEWER, model_id="momentum", version="1.0.0", reason="ok")
        with self.assertRaises(PromotionError):
            self.registry.rollback(actor=Actor("alice", Role.HUMAN_OPERATOR), model_id="momentum", reason="none")

    def test_versions_are_immutable_and_manifest_must_be_reproducible(self):
        self.register("1.0.0")
        with self.assertRaises(ValueError):
            self.register("1.0.0")
        with self.assertRaises(ValueError):
            self.registry.register_candidate(
                actor=AGENT, model_id="x", version="1", manifest={"params": {}}, criteria=PromotionCriteria()
            )

    def test_negative_experiments_are_kept(self):
        experiment = self.registry.open_experiment(
            author=AGENT,
            hypothesis="20-bar momentum predicts next-day sign",
            mechanism="slow information diffusion",
            dataset_id="synthetic",
            plan={"metric": "net_pnl"},
            variants_tested=4,
        )
        self.registry.close_experiment(experiment, result={"net_pnl": -12.0}, decision="REJECTED")
        stored = self.registry.experiments()[0]
        self.assertEqual(stored["decision"], "REJECTED")
        self.assertEqual(stored["variants_tested"], 4)
        with self.assertRaises(ValueError):
            self.registry.close_experiment(experiment, result={}, decision="CANDIDATE")


if __name__ == "__main__":
    unittest.main()
