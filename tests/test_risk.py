import unittest
from datetime import datetime, timezone

from astra.models import Direction, OrderIntent, RiskContext, RiskPolicy, RunMode
from astra.risk import RiskEngine


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_intent(**overrides):
    values = {
        "intent_id": "intent-1",
        "instrument": "EURUSD",
        "direction": Direction.LONG,
        "entry_price": 1.1000,
        "stop_price": 1.0950,
        "created_at": NOW,
    }
    values.update(overrides)
    return OrderIntent(**values)


def make_context(**overrides):
    values = {
        "mode": RunMode.SIMULATION,
        "account_mode": RunMode.SIMULATION,
        "account_verified": True,
        "data_age_seconds": 0.0,
        "spread_pips": 1.2,
        "equity": 10_000.0,
        "day_start_equity": 10_000.0,
        "day_net_pnl": 0.0,
        "open_positions": 0,
        "instrument_specs_known": True,
        "margin_available": True,
    }
    values.update(overrides)
    return RiskContext(**values)


class RiskEngineTests(unittest.TestCase):
    def test_approves_simulation_intent_with_policy_sized_units(self):
        decision = RiskEngine().evaluate(make_intent(), make_context())
        self.assertTrue(decision.allowed)
        self.assertAlmostEqual(decision.risk_amount, 25.0)
        self.assertAlmostEqual(decision.approved_units, 5_000.0)

    def test_rejects_stale_or_unknown_data(self):
        for age in (None, 121.0, -1.0):
            with self.subTest(age=age):
                decision = RiskEngine().evaluate(make_intent(), make_context(data_age_seconds=age))
                self.assertIn("data_stale_or_unknown", decision.reason_codes)

    def test_rejects_unknown_or_excessive_spread(self):
        for spread in (None, 3.01):
            with self.subTest(spread=spread):
                decision = RiskEngine().evaluate(make_intent(), make_context(spread_pips=spread))
                self.assertIn("spread_excessive_or_unknown", decision.reason_codes)

    def test_rejects_mode_mismatch_and_unverified_account(self):
        for context in (
            make_context(account_mode=RunMode.DEMO),
            make_context(account_verified=False),
        ):
            decision = RiskEngine().evaluate(make_intent(), context)
            self.assertFalse(decision.allowed)
            self.assertIn("account_mode_unverified", decision.reason_codes)
        live = RiskEngine().evaluate(make_intent(), make_context(mode=RunMode.LIVE, account_mode=RunMode.LIVE))
        self.assertIn("mode_not_supported", live.reason_codes)

    def test_daily_loss_limit_blocks_entries(self):
        decision = RiskEngine().evaluate(make_intent(), make_context(day_net_pnl=-100.0))
        self.assertIn("daily_loss_limit_reached", decision.reason_codes)

    def test_spread_limit_and_other_independent_guards_block(self):
        context = make_context(
            open_positions=1,
            instrument_specs_known=False,
            margin_available=False,
            kill_switch_active=True,
        )
        decision = RiskEngine().evaluate(make_intent(instrument="GBPUSD"), context)
        self.assertFalse(decision.allowed)
        self.assertIn("position_limit_reached", decision.reason_codes)
        self.assertIn("instrument_not_allowed", decision.reason_codes)
        self.assertIn("instrument_specs_unknown", decision.reason_codes)
        self.assertIn("margin_unavailable", decision.reason_codes)
        self.assertIn("kill_switch_active", decision.reason_codes)

    def test_rejects_invalid_stop_geometry(self):
        decision = RiskEngine().evaluate(make_intent(stop_price=1.1010), make_context())
        self.assertIn("invalid_stop_direction", decision.reason_codes)

    def test_duplicate_intent_is_rejected(self):
        engine = RiskEngine()
        self.assertTrue(engine.evaluate(make_intent(), make_context()).allowed)
        retry = engine.evaluate(make_intent(), make_context())
        self.assertEqual(retry.reason_codes, ("duplicate_intent",))

    def test_oversized_requested_quantity_is_rejected(self):
        decision = RiskEngine().evaluate(
            make_intent(requested_units=5_001.0), make_context()
        )
        self.assertEqual(decision.reason_codes, ("requested_size_exceeds_risk",))

    def test_invalid_intent_schema_fails_closed_without_exception(self):
        intent = make_intent(
            intent_id=None,
            instrument=None,
            direction="LONG",
            entry_price=None,
            created_at="not-a-timestamp",
        )
        decision = RiskEngine().evaluate(intent, make_context())
        self.assertFalse(decision.allowed)
        self.assertIn("invalid_intent_id", decision.reason_codes)
        self.assertIn("instrument_not_allowed", decision.reason_codes)
        self.assertIn("invalid_direction", decision.reason_codes)
        self.assertIn("invalid_entry_price", decision.reason_codes)
        self.assertIn("invalid_intent_timestamp", decision.reason_codes)

    def test_unsafe_policy_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "risk_per_position"):
            RiskEngine(RiskPolicy(risk_per_position=1.1))

    def test_policy_cannot_raise_critical_caps(self):
        unsafe_policies = (
            RiskPolicy(risk_per_position=0.0026),
            RiskPolicy(daily_loss_limit=0.0101),
            RiskPolicy(max_open_positions=2),
            RiskPolicy(max_data_age_seconds=120.1),
            RiskPolicy(max_spread_pips=3.01),
            RiskPolicy(allowed_instruments=("EURUSD", "GBPUSD")),
        )
        for policy in unsafe_policies:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    RiskEngine(policy)

    def test_engine_copies_mutable_instrument_allowlist(self):
        symbols = ["EURUSD"]
        engine = RiskEngine(RiskPolicy(allowed_instruments=symbols))
        symbols[0] = "GBPUSD"

        allowed = engine.evaluate(make_intent(), make_context())
        blocked = engine.evaluate(
            make_intent(intent_id="gbpusd", instrument="GBPUSD"), make_context()
        )
        self.assertTrue(allowed.allowed)
        self.assertIn("instrument_not_allowed", blocked.reason_codes)
        self.assertEqual(engine.policy.allowed_instruments, ("EURUSD",))

    def test_malformed_required_boolean_flags_fail_closed(self):
        cases = (
            ("account_verified", "account_verification_invalid"),
            ("instrument_specs_known", "instrument_specs_state_invalid"),
            ("margin_available", "margin_state_invalid"),
            ("kill_switch_active", "kill_switch_state_invalid"),
        )
        malformed = ("false", None, object())
        for field, reason in cases:
            for value in malformed:
                with self.subTest(field=field, value=type(value).__name__):
                    context = make_context(**{field: value})
                    decision = RiskEngine().evaluate(make_intent(), context)
                    self.assertFalse(decision.allowed)
                    self.assertIn(reason, decision.reason_codes)

    def test_non_model_inputs_fail_closed_without_exception(self):
        self.assertEqual(
            RiskEngine().evaluate(None, make_context()).reason_codes,
            ("invalid_order_intent",),
        )
        self.assertEqual(
            RiskEngine().evaluate(make_intent(), None).reason_codes,
            ("invalid_risk_context",),
        )


if __name__ == "__main__":
    unittest.main()
