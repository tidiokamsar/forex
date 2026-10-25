"""Regression tests for audit items C-03, C-04, C-05 and C-07 (simulator and offline fixture only)."""

import contextlib
import io
import json
import os
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from astra.brokers import BrokerOrderRequest, BrokerRequestError
from astra.brokers.base import ConnectionState
from astra.brokers.ctrader import CTraderAdapter, CTraderConnectionError, CTraderError, PayloadType as P
from astra.brokers.simulated import SimulatedBroker
from astra.cli import main
from astra.execution import FreezeReason, OrderGateway, OrderState, TradingControls
from astra.execution.resolution import ResolutionRefused
from astra.governance import Actor, PermissionDenied, Role
from astra.models import Direction
from ctrader_fixture import FakeCTraderServer, FakeTransport
from test_ctrader import credentials, wait_for
from test_execution import OPERATOR, SYSTEM, ExecutionTestCase


class NoLookupBroker(SimulatedBroker):
    """Like the cTrader adapter: no documented lookup by client order id."""

    def capabilities(self):
        from dataclasses import replace

        return replace(super().capabilities(), supports_order_lookup_by_client_id=False)

    def lookup_order(self, client_order_id):
        self._require_connection()
        return None


class ResolutionTests(ExecutionTestCase):
    """C-03: an UNKNOWN order can be resolved by a human with evidence; freezes need reconciliation."""

    def unknown_order(self, broker):
        gateway = self.make_gateway(broker)
        broker.script_next_submit("timeout_before_accept")
        result = gateway.submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.UNKNOWN)
        return gateway, result.intent_id

    def test_without_lookup_the_order_stays_frozen_until_resolved(self):
        broker = NoLookupBroker(clock=self.clock)
        broker.set_quote("EURUSD", 1.10000, 1.10010)
        gateway, intent = self.unknown_order(broker)
        first = gateway.reconcile()
        self.assertEqual(first.status, "DIVERGENT")
        self.assertIn(f"order_missing_at_broker:{intent}", first.divergences)

        resolved = gateway.resolve_unknown(intent, OPERATOR, outcome="NOT_EXECUTED", evidence="cTrader history: no deal for ASTRA id")
        self.assertEqual(resolved.state, OrderState.CANCELLED)
        # Resolution alone does not unfreeze.
        self.assertIn("unknown_order_state", gateway.controls.freezes())
        self.assertEqual(gateway.reconcile().status, "CONSISTENT")
        self.assertEqual(gateway.controls.freezes(), ())

    def test_a_wrong_human_claim_is_caught_by_reconciliation(self):
        broker = NoLookupBroker(clock=self.clock)
        broker.set_quote("EURUSD", 1.10000, 1.10010)
        gateway, intent = self.unknown_order(broker)
        gateway.resolve_unknown(intent, OPERATOR, outcome="FILLED", evidence="claimed fill #123456", filled_units=5_000.0, average_fill_price=1.1001)
        order = self.store.get_order(intent)
        self.assertEqual((order.state, order.filled_units), (OrderState.CANCELLED, 5_000.0))
        result = gateway.reconcile()
        self.assertEqual(result.status, "DIVERGENT")
        self.assertIn("position_divergence", gateway.controls.freezes())

    def test_resolution_requires_human_role_and_evidence(self):
        broker = NoLookupBroker(clock=self.clock)
        broker.set_quote("EURUSD", 1.10000, 1.10010)
        gateway, intent = self.unknown_order(broker)
        for role in (Role.SYSTEM, Role.RESEARCH_AGENT, Role.LLM, Role.STRATEGY):
            with self.assertRaises(PermissionDenied):
                gateway.resolve_unknown(intent, Actor("bot", role), outcome="NOT_EXECUTED", evidence="looks fine to me")
        with self.assertRaises(ResolutionRefused):
            gateway.resolve_unknown(intent, OPERATOR, outcome="NOT_EXECUTED", evidence="")
        with self.assertRaises(ResolutionRefused):
            gateway.resolve_unknown(intent, OPERATOR, outcome="FILLED", evidence="deal 778899 confirmed", filled_units=50_000.0, average_fill_price=1.1)
        self.assertEqual(self.store.get_order(intent).state, OrderState.UNKNOWN)

    def test_resolution_refused_when_the_adapter_knows_the_order(self):
        self.broker.script_next_submit("timeout_after_accept")
        result = self.gateway.submit(self.command(), SYSTEM)
        self.assertEqual(result.state, OrderState.UNKNOWN)
        with self.assertRaises(ResolutionRefused):
            self.gateway.resolve_unknown(result.intent_id, OPERATOR, outcome="NOT_EXECUTED", evidence="operator thinks not executed")

    def test_terminal_orders_cannot_be_resolved(self):
        result = self.gateway.submit(self.command(), SYSTEM)
        with self.assertRaises(ResolutionRefused):
            self.gateway.resolve_unknown(result.intent_id, OPERATOR, outcome="NOT_EXECUTED", evidence="deal 778899 confirmed")

    def test_cli_resolution_records_evidence_and_keeps_freezes(self):
        broker = NoLookupBroker(clock=self.clock)
        broker.set_quote("EURUSD", 1.10000, 1.10010)
        gateway, intent = self.unknown_order(broker)
        output = io.StringIO()
        with patch.dict(os.environ, {"ASTRA_DATABASE": str(self.store.path)}, clear=True), contextlib.redirect_stdout(output):
            code = main(["orders", "--resolve", intent, "--outcome", "NOT_EXECUTED", "--evidence", "ticket 42: no deal", "--operator", "alice"])
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["resolved"]["state"], "CANCELLED")
        self.assertIn("unknown_order_state", gateway.controls.freezes())


class LookupEventTests(unittest.TestCase):
    """C-04 (audit claim checked): repeated lookups of a pending order emit one event, not many."""

    def test_repeated_lookup_does_not_duplicate_events(self):
        server = FakeCTraderServer()
        adapter = CTraderAdapter(credentials(), transport=FakeTransport(server), request_timeout=1.0, heartbeat_seconds=5)
        self.addCleanup(adapter.disconnect)
        adapter.connect()
        self.assertTrue(wait_for(lambda: adapter._quotes.get("EURUSD", {}).get("ask") is not None))
        server.order_behavior = "accept_only"
        server.positions = []
        original = server.handle

        def with_pending(message):
            if message["payloadType"] == P.RECONCILE_REQ:
                server.push({"clientMsgId": message["clientMsgId"], "payloadType": int(P.RECONCILE_RES), "payload": {
                    "position": [], "order": [{"orderId": 1, "clientOrderId": "ASTRA-pending", "tradeData": {"symbolId": 1, "tradeSide": 1, "volume": 100_000}}]}})
                return
            original(message)

        server.handle = with_pending
        for _ in range(3):
            self.assertEqual(adapter.lookup_order("ASTRA-pending").status.value, "ACCEPTED")
        events = [e for e in adapter.poll_events(0) if e.report.client_order_id == "ASTRA-pending"]
        self.assertEqual(len(events), 1)


class AccountScopedControlTests(ExecutionTestCase):
    """C-05: freezes and the daily-loss state belong to one account; the kill switch stays global."""

    def setUp(self):
        super().setUp()
        self.broker_b = SimulatedBroker(clock=self.clock, account_ref="SIM-****0002")
        self.broker_b.set_quote("EURUSD", 1.10000, 1.10010)
        self.gw_a = OrderGateway(store=self.store, broker=self.broker, clock=self.clock, account_ref="SIM-****0001")
        self.gw_b = OrderGateway(store=self.store, broker=self.broker_b, clock=self.clock, account_ref="SIM-****0002")

    def test_connection_loss_on_one_account_does_not_block_another(self):
        self.broker.disconnect()
        self.gw_a.check_connection()
        self.assertIn("connection_lost", self.gw_a.controls.freezes())
        self.assertEqual(self.gw_b.controls.freezes(), ())
        result = self.gw_b.submit(self.command(account_ref="SIM-****0002"), SYSTEM)
        self.assertEqual(result.state, OrderState.FILLED)
        by_account = TradingControls(self.store).all_freezes()
        self.assertIn("SIM-****0001", by_account)
        self.assertNotIn("SIM-****0002", by_account)

    def test_kill_switch_is_global(self):
        self.gw_a.controls.activate_kill_switch("monitor", "incident")
        for gateway, ref in ((self.gw_a, "SIM-****0001"), (self.gw_b, "SIM-****0002")):
            self.assertIn("kill_switch_active", gateway.submit(self.command(account_ref=ref), SYSTEM).reason_codes)

    def test_global_freeze_still_applies_to_every_account(self):
        TradingControls(self.store).freeze(FreezeReason.OPERATOR, actor="alice")
        self.assertIn("operator", self.gw_a.controls.freezes())
        self.assertIn("operator", self.gw_b.controls.freezes())

    def test_daily_loss_freeze_is_per_account(self):
        self.gw_a.submit(self.command(), SYSTEM)
        self.broker.set_quote("EURUSD", 1.0890, 1.0891)
        self.gw_a.submit(self.command(purpose="EXIT", direction=Direction.SHORT, stop_price=None, checks_passed=()), SYSTEM)
        self.assertIn("daily_loss_limit_reached", self.gw_a.submit(self.command(stop_price=1.0870), SYSTEM).reason_codes)
        self.assertIn("daily_loss_limit", self.gw_a.controls.freezes())
        self.assertNotIn("daily_loss_limit", self.gw_b.controls.freezes())
        self.assertEqual(self.gw_b.submit(self.command(account_ref="SIM-****0002"), SYSTEM).state, OrderState.FILLED)

    def test_reconciliation_of_one_account_does_not_clear_another(self):
        self.broker.disconnect()
        self.gw_a.check_connection()
        self.assertEqual(self.gw_b.reconcile().status, "CONSISTENT")
        self.assertIn("connection_lost", self.gw_a.controls.freezes())


class FailingReadsBroker(SimulatedBroker):
    def account_snapshot(self):
        raise BrokerRequestError("TRADING_DISABLED", "scripted")


class ErrorClassificationTests(ExecutionTestCase):
    """C-07: a provider refusal is not a connection loss; auth/session errors are."""

    def test_gateway_does_not_freeze_connection_on_request_error(self):
        broker = FailingReadsBroker(clock=self.clock)
        broker.set_quote("EURUSD", 1.10000, 1.10010)
        result = self.make_gateway(broker).submit(self.command(), SYSTEM)
        self.assertIn("broker_request_error:TRADING_DISABLED", result.reason_codes)
        controls = TradingControls(self.store)
        self.assertNotIn("connection_lost", controls.freezes())

    def test_reconcile_request_error_keeps_entries_frozen_without_link_loss(self):
        broker = FailingReadsBroker(clock=self.clock)
        result = self.make_gateway(broker).reconcile()
        self.assertEqual(result.status, "FAILED")
        self.assertIn("reconciliation_required", result.freezes_after)
        self.assertNotIn("connection_lost", result.freezes_after)

    def connected_adapter(self):
        self.server = FakeCTraderServer()
        adapter = CTraderAdapter(credentials(), transport=FakeTransport(self.server), request_timeout=1.0, heartbeat_seconds=5)
        self.addCleanup(adapter.disconnect)
        adapter.connect()
        self.assertTrue(wait_for(lambda: adapter._quotes.get("EURUSD", {}).get("ask") is not None))
        return adapter

    def test_ctrader_business_error_keeps_session(self):
        adapter = self.connected_adapter()
        self.server.fail_with[P.RECONCILE_REQ] = "POSITION_NOT_FOUND"
        with self.assertRaises(CTraderError) as caught:
            adapter.open_positions()
        self.assertIsInstance(caught.exception, BrokerRequestError)
        self.assertIs(adapter.connection_state(), ConnectionState.CONNECTED)

    def test_ctrader_rate_limit_carries_retry_after(self):
        adapter = self.connected_adapter()
        self.server.fail_with[P.RECONCILE_REQ] = "BLOCKED_PAYLOAD_TYPE"
        original = self.server.handle

        def with_retry(message):
            if message["payloadType"] == P.RECONCILE_REQ:
                self.server.push({"clientMsgId": message["clientMsgId"], "payloadType": int(P.OA_ERROR_RES),
                                  "payload": {"errorCode": "BLOCKED_PAYLOAD_TYPE", "retryAfter": 3}})
                return
            original(message)

        self.server.handle = with_retry
        with self.assertRaises(CTraderError) as caught:
            adapter.open_positions()
        self.assertEqual(caught.exception.retry_after_seconds, 3.0)
        self.assertIs(adapter.connection_state(), ConnectionState.CONNECTED)

    def test_ctrader_token_error_drops_the_session(self):
        adapter = self.connected_adapter()
        self.server.fail_with[P.RECONCILE_REQ] = "CH_ACCESS_TOKEN_INVALID"
        with self.assertRaises(CTraderConnectionError):
            adapter.open_positions()
        self.assertIs(adapter.connection_state(), ConnectionState.DISCONNECTED)


if __name__ == "__main__":
    unittest.main()
