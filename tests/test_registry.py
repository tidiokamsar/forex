import sqlite3
import unittest
import uuid

from _support import accept_unverified_evidence, workspace_tempdir
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
        # Numeric-gate tests run without provenance checks (tested in ProvenanceTests).
        self.registry = ModelRegistry(f"{tmp}/astra.sqlite3", evidence_verifier=accept_unverified_evidence)
        self.registry.initialize()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def register(self, version, evidence=None):
        self.registry.register_candidate(
            actor=AGENT, model_id="momentum", version=version, manifest={**MANIFEST, "params": {"v": version}},
            criteria=PromotionCriteria(),
        )
        if evidence is not None:
            self.registry.record_evaluation(actor=AGENT, holdout_evaluation_id=f"test-holdout-{uuid.uuid4()}", model_id="momentum", version=version, kind="holdout", evidence=evidence)

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

    def test_criteria_cannot_be_looser_than_the_policy_floor(self):
        """Lot 3 QA (A/C): nobody can register lax criteria or skip the holdout or the overfitting check."""
        lax = [
            PromotionCriteria(require_overfitting_check=False),
            PromotionCriteria(require_holdout=False),
            PromotionCriteria(min_trades=1),
            PromotionCriteria(min_profit_factor=0.0),
            PromotionCriteria(max_pbo=1.0),
            PromotionCriteria(min_deflated_sharpe_probability=0.0),
            PromotionCriteria(max_drawdown_pct=50.0),
        ]
        for number, criteria in enumerate(lax):
            with self.assertRaises(ValueError):
                self.registry.register_candidate(
                    actor=AGENT, model_id="lax", version=str(number), manifest=MANIFEST, criteria=criteria
                )
        self.registry.register_candidate(
            actor=AGENT, model_id="strict", version="1", manifest=MANIFEST,
            criteria=PromotionCriteria(min_trades=100, max_pbo=0.1),
        )
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
            self.registry.record_evaluation(actor=AGENT, holdout_evaluation_id=f"test-holdout-{uuid.uuid4()}", model_id="momentum", version="1.0.0", kind="holdout", evidence=GOOD_EVIDENCE)

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

    def test_legacy_candidate_without_overfitting_criteria_fails_closed(self):
        """A candidate registered before Lot 3 (no overfitting keys stored) cannot be promoted on old evidence."""
        import json

        from astra.registry import _canonical, _sha256

        self.register("0.9.0")
        legacy_criteria = {
            "min_trades": 30, "min_net_pnl": 0.0, "min_profit_factor": 1.1, "max_drawdown_pct": 10.0,
            "min_edge_vs_baseline": 0.0, "require_cost_stress_non_negative": True, "require_holdout": True,
            "require_shadow": False,
        }
        db = sqlite3.connect(self.registry.path)
        try:
            with db:
                db.execute(
                    "UPDATE model_versions SET criteria_json = ?, criteria_sha256 = ? WHERE version = '0.9.0'",
                    (_canonical(legacy_criteria), _sha256(legacy_criteria)),
                )
        finally:
            db.close()
        legacy_evidence = {key: value for key, value in GOOD_EVIDENCE.items()
                           if key not in ("pbo", "deflated_sharpe_probability", "variants_tested")}
        self.registry.record_evaluation(actor=AGENT, holdout_evaluation_id=f"test-holdout-{uuid.uuid4()}", model_id="momentum", version="0.9.0", kind="holdout", evidence=legacy_evidence)
        failures = self.registry.check_promotion("momentum", "0.9.0")
        self.assertIn("overfitting_check_missing", failures)
        self.assertNotIn("criteria_tampered", failures)
        self.assertEqual(json.loads(json.dumps(failures)), list(failures))
        with self.assertRaises(PromotionError):
            self.registry.promote(actor=REVIEWER, model_id="momentum", version="0.9.0", reason="legacy")

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


class ProvenanceTests(unittest.TestCase):
    """Lot 3 QA: fabricated or unbound evidence must never satisfy the gates."""

    def setUp(self):
        self._tmp = workspace_tempdir()
        tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.registry = ModelRegistry(f"{tmp}/astra.sqlite3")  # default verifier
        self.registry.initialize()

    def test_fabricated_evidence_cannot_promote(self):
        self.registry.register_candidate(
            actor=AGENT, model_id="fake", version="1", manifest=MANIFEST, criteria=PromotionCriteria()
        )
        self.registry.record_evaluation(
            actor=AGENT, model_id="fake", version="1", kind="holdout", evidence=GOOD_EVIDENCE,
            holdout_evaluation_id="made-up-id",
        )
        failures = self.registry.check_promotion("fake", "1")
        self.assertIn("holdout_evaluation_not_found_or_incomplete", failures)
        with self.assertRaises(PromotionError):
            self.registry.promote(actor=REVIEWER, model_id="fake", version="1", reason="fabricated")

    def test_one_holdout_backs_one_version_only(self):
        for version in ("1", "2"):
            self.registry.register_candidate(
                actor=AGENT, model_id="dup", version=version, manifest=MANIFEST, criteria=PromotionCriteria()
            )
        self.registry.record_evaluation(
            actor=AGENT, model_id="dup", version="1", kind="holdout", evidence=GOOD_EVIDENCE, holdout_evaluation_id="h-1"
        )
        with self.assertRaises(PromotionError):
            self.registry.record_evaluation(
                actor=AGENT, model_id="dup", version="2", kind="holdout", evidence=GOOD_EVIDENCE, holdout_evaluation_id="h-1"
            )

    def test_holdout_evidence_requires_a_reference_and_an_actor(self):
        self.registry.register_candidate(
            actor=AGENT, model_id="fake", version="2", manifest=MANIFEST, criteria=PromotionCriteria()
        )
        with self.assertRaises(PromotionError):
            self.registry.record_evaluation(actor=AGENT, model_id="fake", version="2", kind="holdout", evidence=GOOD_EVIDENCE)
        with self.assertRaises(PermissionDenied):
            self.registry.record_evaluation(
                actor=Actor("llm", Role.LLM), model_id="fake", version="2", kind="holdout", evidence=GOOD_EVIDENCE,
                holdout_evaluation_id="x",
            )


if __name__ == "__main__":
    unittest.main()

