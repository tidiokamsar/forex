import unittest
from datetime import datetime, timedelta, timezone

from _support import workspace_tempdir

from astra.brokers import BrokerEnvironment, EnvironmentSource
from astra.brokers.base import BrokerAdapter
from astra.brokers.simulated import SimulatedBroker
from astra.execution import (
    ExecutionCommand,
    ExecutionStore,
    FreezeReason,
    InvalidTransition,
    OrderGateway,
    OrderState,
)
from astra.governance import Actor, PermissionDenied, Role
from astra.models import Direction, RunMode
from astra.storage import BacktestStore


T0 = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
SYSTEM = Actor("pipeline", Role.SYSTEM)
OPERATOR = Actor("alice", Role.HUMAN_OPERATOR)


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class ExecutionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        self.tmp = self._tmp.__enter__()
        self.clock = Clock()
        self.store = ExecutionStore(f"{self.tmp}/astra.sqlite3")
        self.store.initialize()
        self.broker = SimulatedBroker(clock=self.clock)
        self.broker.set_quote("EURUSD", 1.10000, 1.10010)
        self.gateway = self.make_gateway()
        self._counter = 0

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def make_gateway(self, broker=None):
        return OrderGateway(store=self.store, broker=broker or self.broker, clock=self.clock)

    def command(self, **overrides):
        self._counter += 1
        values = dict(
            intent_id=f"intent-{self._counter}",
            decision_id=f"decision-{self._counter}",
            correlation_id=f"corr-{self._counter}",
            mode=RunMode.SIMULATION,
            account_ref="SIM-****0001",
            instrument="EURUSD",
            direction=Direction.LONG,
            created_at=self.clock(),
            expires_at=self.clock() + timedelta(seconds=60),
            strategy="test_strategy",
            strategy_version="1.0.0",
            checks_passed=("strategy_signal",),
            purpose="ENTRY",
            stop_price=1.09800,
        )
        values.update(overrides)
        return ExecutionCommand(**values)

    def event_types(self):
        return [event["event_type"] for event in BacktestStore(self.store.path).audit_events(limit=200)]


class HappyPathTests(ExecutionTestCase):
    def test_entry_is_filled_and_traceable_by_correlation(self):
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.FILLED)
        self.assertEqual(result.reason_codes, ())
        # 0.25% of 10,000 over a 21-pip stop, floored to the 1,000-unit step.
        self.assertEqual(result.approved_units, 11_000.0)
        states = [row["to_state"] for row in self.store.transitions(result.intent_id)]
        self.assertEqual(states, ["CREATED", "VALIDATING", "AUTHORIZED", "SUBMITTED", "FILLED"])
        order = self.store.get_order(result.intent_id)
        self.assertEqual(order.correlation_id, "corr-1")
        self.assertEqual(self.broker.open_positions()[0].net_units, 11_000.0)

    def test_default_gateway_mode_is_simulation(self):
        self.assertIs(self.gateway._mode, RunMode.SIMULATION)

    def test_invalid_transition_is_refused(self):
        result = self.gateway.submit(self.command(), SYSTEM)
        with self.assertRaises(InvalidTransition):
            self.store.transition(result.intent_id, OrderState.SUBMITTED, reason="bad")

    def test_adapter_exposes_no_funds_movement(self):
        names = {name.lower() for name in set(dir(BrokerAdapter)) | set(dir(SimulatedBroker))}
        for forbidden in ("withdraw", "transfer", "beneficiary", "deposit", "payout"):
            self.assertFalse(any(forbidden in name for name in names), forbidden)


class SafetyGateTests(ExecutionTestCase):
    def test_account_reporting_live_blocks_and_freezes(self):
        self.broker.report_environment(BrokerEnvironment.LIVE)
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.RISK_REJECTED)
        self.assertIn("account_environment_mismatch", result.reason_codes)
        self.assertIn("live_locked_requires_human_authorization", result.reason_codes)
        self.assertIn(FreezeReason.MODE_MISMATCH.value, self.gateway.controls.freezes())
        self.assertEqual(self.broker.submit_calls, 0)

    def test_declared_demo_but_account_live_blocks(self):
        self.broker.report_environment(
            BrokerEnvironment.LIVE, EnvironmentSource.PROVIDER_API, declared=BrokerEnvironment.DEMO
        )
        result = self.gateway.submit(self.command(mode=RunMode.DEMO), SYSTEM)
        self.assertEqual(result.state, OrderState.RISK_REJECTED)
        self.assertIn("command_mode_differs_from_configuration", result.reason_codes)
        self.assertEqual(self.broker.submit_calls, 0)

    def test_unknown_account_environment_blocks(self):
        self.broker.report_environment(BrokerEnvironment.UNKNOWN, EnvironmentSource.UNVERIFIED)
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertIn("account_environment_unknown", result.reason_codes)
        self.assertIn("account_environment_not_provider_verified", result.reason_codes)
        self.assertEqual(self.broker.submit_calls, 0)

    def test_stale_quote_blocks(self):
        self.broker.set_quote("EURUSD", 1.1, 1.1001, timestamp=T0 - timedelta(seconds=600))
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertIn("data_stale_or_unknown", result.reason_codes)
        self.assertEqual(self.broker.submit_calls, 0)

    def test_extreme_spread_blocks(self):
        self.broker.set_quote("EURUSD", 1.1000, 1.1010)
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertIn("spread_excessive_or_unknown", result.reason_codes)
        self.assertEqual(self.broker.submit_calls, 0)

    def test_daily_loss_limit_blocks_new_entries(self):
        first = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(first.state, OrderState.FILLED)
        # Mark the open position down by more than 1% of starting equity.
        self.broker.set_quote("EURUSD", 1.0890, 1.0891)
        exit_result = self.gateway.submit(
            self.command(purpose="EXIT", direction=Direction.SHORT, stop_price=None, checks_passed=()), SYSTEM
        )
        self.assertEqual(exit_result.state, OrderState.FILLED)
        result = self.gateway.submit(self.command(stop_price=1.0870), SYSTEM)
        self.assertIn("daily_loss_limit_reached", result.reason_codes)
        self.assertIn(FreezeReason.DAILY_LOSS_LIMIT.value, self.gateway.controls.freezes())

    def test_daily_loss_freeze_expires_on_next_utc_day(self):
        self.gateway.controls.freeze(FreezeReason.DAILY_LOSS_LIMIT)
        self.store.set_control("daily_loss_limit_day", T0.date().isoformat(), actor="t", audit_type="t", details={})
        self.assertIn("entries_frozen:daily_loss_limit", self.gateway.submit(self.command(), SYSTEM).reason_codes)
        self.clock.advance(86_400)
        self.broker.set_quote("EURUSD", 1.10000, 1.10010)
        self.assertEqual(self.gateway.submit(self.command(), SYSTEM).state, OrderState.FILLED)

    def test_command_for_another_account_is_blocked(self):
        result = self.gateway.submit(self.command(account_ref="SIM-****9999"), SYSTEM)
        self.assertIn("account_ref_mismatch", result.reason_codes)
        self.assertEqual(self.broker.submit_calls, 0)

    def test_second_position_is_blocked(self):
        self.gateway.submit(self.command(), SYSTEM)
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertIn("position_limit_reached", result.reason_codes)

    def test_command_without_signal_check_or_stop_is_rejected(self):
        result = self.gateway.submit(self.command(checks_passed=(), stop_price=None), SYSTEM)
        self.assertIsNone(result.state)
        self.assertIn("missing_strategy_signal_check", result.reason_codes)
        self.assertIn("entry_requires_stop", result.reason_codes)
        self.assertEqual(self.broker.submit_calls, 0)

    def test_expired_command_is_rejected(self):
        command = self.command()
        self.clock.advance(120)
        result = self.gateway.submit(command, SYSTEM)
        self.assertIn("command_expired", result.reason_codes)

    def test_future_dated_command_blocks_on_clock(self):
        result = self.gateway.submit(
            self.command(created_at=T0 + timedelta(minutes=10), expires_at=T0 + timedelta(minutes=11)), SYSTEM
        )
        self.assertIn("clock_inconsistent", result.reason_codes)

    def test_llm_and_strategy_roles_cannot_submit(self):
        for role in (Role.LLM, Role.STRATEGY, Role.RESEARCH_AGENT, Role.MODEL):
            with self.assertRaises(PermissionDenied):
                self.gateway.submit(self.command(), Actor("bot", role))
        self.assertEqual(self.broker.submit_calls, 0)
        self.assertIn("permission.denied", self.event_types())


class IdempotencyAndRecoveryTests(ExecutionTestCase):
    def test_timeout_after_accept_is_not_blindly_retried(self):
        self.broker.script_next_submit("timeout_after_accept")
        command = self.command()
        first = self.gateway.submit(command, SYSTEM)
        self.assertEqual(first.state, OrderState.UNKNOWN)
        self.assertIn(FreezeReason.UNKNOWN_ORDER_STATE.value, self.gateway.controls.freezes())
        retry = self.gateway.submit(command, SYSTEM)
        self.assertTrue(retry.duplicate)
        self.assertEqual(self.broker.submit_calls, 1)
        self.assertEqual(retry.state, OrderState.FILLED)
        self.assertEqual(len(self.broker.open_positions()), 1)
        reconciliation = self.gateway.reconcile()
        self.assertEqual(reconciliation.status, "CONSISTENT")
        self.assertEqual(self.gateway.controls.freezes(), ())

    def test_timeout_before_accept_resolves_to_not_received(self):
        self.broker.script_next_submit("timeout_before_accept")
        first = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(first.state, OrderState.UNKNOWN)
        new_entry = self.gateway.submit(self.command(), SYSTEM)
        self.assertIn("entries_frozen:unknown_order_state", new_entry.reason_codes)
        reconciliation = self.gateway.reconcile()
        self.assertEqual(reconciliation.status, "CONSISTENT")
        self.assertEqual(self.store.get_order(first.intent_id).state, OrderState.CANCELLED)
        self.assertEqual(self.broker.submit_calls, 1)

    def test_connection_loss_freezes_entries_until_reconciled(self):
        self.gateway.submit(self.command(), SYSTEM)
        self.broker.disconnect()
        self.assertEqual(self.gateway.check_connection().value, "DISCONNECTED")
        blocked = self.gateway.submit(self.command(), SYSTEM)
        self.assertIn("broker_disconnected", blocked.reason_codes)
        self.assertEqual(self.gateway.reconcile().status, "FAILED")
        self.broker.reconnect()
        # Reconnecting alone does not unfreeze anything.
        self.assertIn("connection_lost", self.gateway.controls.freezes())
        self.assertEqual(self.gateway.reconcile().status, "CONSISTENT")
        self.assertEqual(self.gateway.controls.freezes(), ())

    def test_reconnection_with_divergent_position_keeps_freeze(self):
        self.gateway.submit(self.command(), SYSTEM)
        self.broker.disconnect()
        self.gateway.check_connection()
        self.broker.reconnect()
        self.broker.inject_position("EURUSD", 5_000, 1.1)
        result = self.gateway.reconcile()
        self.assertEqual(result.status, "DIVERGENT")
        self.assertTrue(any(item.startswith("position_mismatch:EURUSD") for item in result.divergences))
        self.assertIn("position_divergence", self.gateway.controls.freezes())
        with self.assertRaises(PermissionDenied):
            self.gateway.controls.clear_freeze(FreezeReason.POSITION_DIVERGENCE, OPERATOR)

    def test_restart_keeps_state_and_never_resends(self):
        command = self.command()
        self.gateway.submit(command, SYSTEM)
        restarted = self.make_gateway()
        again = restarted.submit(command, SYSTEM)
        self.assertTrue(again.duplicate)
        self.assertEqual(self.broker.submit_calls, 1)

    def test_partial_fill_then_completion_via_events(self):
        self.broker.script_next_submit("partial", fill_fraction=0.5)
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(self.store.get_order(result.intent_id).filled_units, 5_500.0)
        self.broker.complete_partial(result.client_order_id)
        self.gateway.process_events()
        order = self.store.get_order(result.intent_id)
        self.assertEqual(order.state, OrderState.FILLED)
        self.assertEqual(order.filled_units, 11_000.0)
        self.assertEqual(self.gateway.reconcile().status, "CONSISTENT")

    def test_out_of_order_events_do_not_regress_state(self):
        self.broker.script_next_submit("accept_only")
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.ACCEPTED)
        self.broker.fill_accepted(result.client_order_id)
        self.broker.deliver_events_in_reverse()
        self.gateway.process_events()
        order = self.store.get_order(result.intent_id)
        self.assertEqual(order.state, OrderState.FILLED)
        self.assertIn("order.stale_event_ignored", self.event_types())

    def test_refusal_at_adapter_boundary_is_recorded_and_nothing_sent(self):
        class RefusingBroker(SimulatedBroker):
            def _environment_guard(self, request, permit):
                return "test_boundary_refusal"

        broker = RefusingBroker(clock=self.clock)
        broker.set_quote("EURUSD", 1.10000, 1.10010)
        result = self.make_gateway(broker).submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.BROKER_REJECTED)
        self.assertIn("execution_refused:test_boundary_refusal", result.reason_codes)
        self.assertEqual(broker.submit_calls, 0)
        self.assertEqual(broker.open_positions(), ())

    def test_broker_rejection_is_terminal(self):
        self.broker.script_next_submit("reject")
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.BROKER_REJECTED)


class ControlTests(ExecutionTestCase):
    def test_kill_switch_blocks_all_and_requires_human_release(self):
        self.gateway.controls.activate_kill_switch("monitor", "test stop")
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertIn("kill_switch_active", result.reason_codes)
        with self.assertRaises(PermissionDenied):
            self.gateway.controls.release_kill_switch(Actor("agent-7", Role.RESEARCH_AGENT), "please")
        self.assertIn("permission.denied", self.event_types())
        self.gateway.controls.release_kill_switch(OPERATOR, "incident closed")
        self.assertFalse(self.gateway.controls.kill_switch_active())

    def test_exit_is_allowed_while_entries_are_frozen(self):
        self.gateway.submit(self.command(), SYSTEM)
        self.gateway.controls.freeze(FreezeReason.OPERATOR, actor="alice")
        exit_result = self.gateway.submit(
            self.command(purpose="EXIT", direction=Direction.SHORT, stop_price=None, checks_passed=()), SYSTEM
        )
        self.assertEqual(exit_result.state, OrderState.FILLED)
        self.assertEqual(self.broker.open_positions(), ())

    def test_exit_cannot_increase_position(self):
        self.gateway.submit(self.command(), SYSTEM)
        result = self.gateway.submit(
            self.command(purpose="EXIT", direction=Direction.LONG, stop_price=None, checks_passed=()), SYSTEM
        )
        self.assertIn("exit_direction_increases_position", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
