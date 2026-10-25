import json
import unittest

from _support import workspace_tempdir
from astra.execution import ExecutionStore
from astra.governance import (
    LLM_SCHEMA_VERSION,
    Actor,
    PermissionDenied,
    PermissionGuard,
    Role,
    SecretVault,
    approve_risk_policy_change,
    handle_llm_output,
)
from astra.models import RiskPolicy
from astra.storage import BacktestStore

ADMIN = Actor("root", Role.HUMAN_ADMIN)
REVIEWER = Actor("bob", Role.HUMAN_REVIEWER)
AGENT = Actor("agent-1", Role.RESEARCH_AGENT)


def advisory(**overrides):
    payload = {
        "schema": LLM_SCHEMA_VERSION,
        "instrument": "EURUSD",
        "bias": "LONG",
        "confidence": 0.6,
        "rationale": "ECB tone",
    }
    payload.update(overrides)
    return json.dumps(payload)


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        tmp = self._tmp.__enter__()
        self.store = ExecutionStore(f"{tmp}/astra.sqlite3")
        self.store.initialize()
        self.guard = PermissionGuard(self.store)

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def events(self):
        return BacktestStore(self.store.path).audit_events()

    def test_agent_secret_read_is_refused_and_audited(self):
        vault = SecretVault(self.guard, environ={"ASTRA_SECRET_BROKER_TOKEN": "s3cr3t"})
        for role in (Role.RESEARCH_AGENT, Role.LLM, Role.STRATEGY, Role.MODEL, Role.HUMAN_OPERATOR):
            with self.assertRaises(PermissionDenied):
                vault.get(Actor("x", role), "broker_token")
        denied = [e for e in self.events() if e["event_type"] == "permission.denied"]
        self.assertEqual(len(denied), 5)
        self.assertNotIn("s3cr3t", json.dumps(self.events()))

    def test_secret_value_never_prints(self):
        vault = SecretVault(self.guard, environ={"ASTRA_SECRET_BROKER_TOKEN": "s3cr3t"})
        value = vault.get(ADMIN, "broker_token")
        self.assertNotIn("s3cr3t", repr(value))
        self.assertNotIn("s3cr3t", str(value))
        self.assertEqual(value.reveal(), "s3cr3t")

    def test_agent_cannot_modify_risk_limits(self):
        with self.assertRaises(PermissionDenied):
            approve_risk_policy_change(
                guard=self.guard, requester=AGENT, reviewer=REVIEWER,
                current=RiskPolicy(), proposed=RiskPolicy(risk_per_position=0.002), reason="faster",
            )
        self.assertIn("permission.denied", [e["event_type"] for e in self.events()])

    def test_self_review_and_agent_review_are_refused(self):
        for reviewer in (ADMIN, Actor("agent", Role.LLM)):
            with self.assertRaises(PermissionDenied):
                approve_risk_policy_change(
                    guard=self.guard, requester=ADMIN, reviewer=reviewer,
                    current=RiskPolicy(), proposed=RiskPolicy(risk_per_position=0.002), reason="tighten",
                )

    def test_reviewed_change_cannot_exceed_hard_caps(self):
        with self.assertRaises(ValueError):
            approve_risk_policy_change(
                guard=self.guard, requester=ADMIN, reviewer=REVIEWER,
                current=RiskPolicy(), proposed=RiskPolicy(risk_per_position=0.05), reason="more risk",
            )
        approved = approve_risk_policy_change(
            guard=self.guard, requester=ADMIN, reviewer=REVIEWER,
            current=RiskPolicy(), proposed=RiskPolicy(risk_per_position=0.001), reason="tighten",
        )
        self.assertEqual(approved.risk_per_position, 0.001)
        self.assertIn("risk_policy.change_approved", [e["event_type"] for e in self.events()])

    def test_invalid_llm_output_means_no_action(self):
        cases = [
            None,
            "not json",
            "[]",
            advisory(bias="BUY NOW"),
            advisory(confidence=1.5),
            advisory(instrument="BTCUSD"),
            advisory(schema="other"),
            json.dumps({**json.loads(advisory()), "units": 100000}),
            "x" * 5000,
        ]
        for raw in cases:
            result = handle_llm_output(raw)
            self.assertEqual(result.action, "NO_ACTION", raw if raw is None else raw[:40])
            self.assertIsNone(result.advisory)

    def test_valid_llm_output_is_advisory_only(self):
        result = handle_llm_output(advisory())
        self.assertEqual(result.action, "ADVISORY_ONLY")
        self.assertEqual(result.advisory.bias, "LONG")

    def test_nobody_can_grant_permissions(self):
        from astra.governance import Action

        for role in Role:
            self.assertFalse(self.guard.allowed(Actor("x", role), Action.GRANT_PERMISSION))


if __name__ == "__main__":
    unittest.main()
