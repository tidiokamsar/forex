import inspect
import json
import time
import unittest

from astra.brokers import BrokerEnvironment, BrokerOrderRequest, BrokerOrderStatus, BrokerTimeout, BrokerUnavailable, EnvironmentSource
from astra.brokers import ctrader
from astra.brokers.base import ConnectionState
from astra.brokers.ctrader import CTraderAdapter, CTraderCredentials, LiveAccountRefused, PayloadType as P
from astra.execution import ExecutionCommand, ExecutionStore, OrderGateway, verify_mode
from astra.governance import Actor, Role, SecretValue
from astra.models import Direction, RunMode
from ctrader_fixture import FakeCTraderServer, FakeTransport
from _support import workspace_tempdir


def credentials(account_id=4242):
    return CTraderCredentials(SecretValue("client-id"), SecretValue("client-secret"), SecretValue("token-xyz"), account_id)


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class CTraderAdapterTests(unittest.TestCase):
    def make(self, server=None, account_id=4242, timeout=1.0):
        self.server = server or FakeCTraderServer()
        self.transport = FakeTransport(self.server)
        adapter = CTraderAdapter(credentials(account_id), transport=self.transport, request_timeout=timeout, heartbeat_seconds=0.05)
        self.addCleanup(adapter.disconnect)
        return adapter

    def connected(self, **kwargs):
        adapter = self.make(**kwargs)
        adapter.connect()
        self.assertTrue(wait_for(lambda: "EURUSD" in adapter._quotes and adapter._quotes["EURUSD"]["ask"] is not None))
        return adapter

    def sent_types(self):
        return [m["payloadType"] for m in self.server.received]

    def test_connect_demo_account_reports_provider_verified_demo(self):
        adapter = self.connected()
        caps = adapter.capabilities()
        self.assertIs(caps.declared_environment, BrokerEnvironment.DEMO)
        self.assertIs(caps.environment_source, EnvironmentSource.PROVIDER_API)
        self.assertFalse(caps.supports_order_lookup_by_client_id)
        self.assertEqual(caps.instruments, ("EURUSD",))
        account = adapter.account_snapshot()
        self.assertEqual(account.currency, "USD")
        self.assertEqual(account.balance, 10_000.0)
        self.assertIs(account.environment_reported, BrokerEnvironment.DEMO)
        self.assertEqual(account.account_ref_masked, "CTRADER-****4242")
        quote = adapter.quote("EURUSD")
        self.assertEqual((quote.bid, quote.ask), (1.1, 1.1001))
        spec = adapter.instrument_specs()["EURUSD"]
        self.assertEqual((spec.min_units, spec.unit_step, spec.pip_size), (1_000.0, 1_000.0, 0.0001))
        self.assertAlmostEqual(spec.margin_rate, 1 / 30)
        self.assertEqual(
            self.sent_types()[:3],
            [P.APPLICATION_AUTH_REQ, P.GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ, P.ACCOUNT_AUTH_REQ],
        )

    def test_live_account_is_refused_before_account_auth(self):
        adapter = self.make(FakeCTraderServer(is_live=True))
        with self.assertRaises(LiveAccountRefused):
            adapter.connect()
        self.assertNotIn(P.ACCOUNT_AUTH_REQ, self.sent_types())
        self.assertIs(adapter.connection_state(), ConnectionState.DISCONNECTED)

    def test_missing_is_live_flag_means_unknown_environment_and_blocks_mode(self):
        adapter = self.connected(server=FakeCTraderServer(include_is_live=False))
        account = adapter.account_snapshot()
        self.assertIs(account.environment_reported, BrokerEnvironment.UNKNOWN)
        verification = verify_mode(RunMode.DEMO, RunMode.DEMO, adapter.capabilities(), account)
        self.assertFalse(verification.verified)
        self.assertIn("account_environment_unknown", verification.reason_codes)

    def test_account_not_granted_to_token_fails_closed(self):
        adapter = self.make(account_id=1)
        with self.assertRaises(BrokerUnavailable):
            adapter.connect()

    def test_only_the_demo_host_is_ever_built(self):
        self.assertEqual(ctrader.create_demo_transport().url, "wss://demo.ctraderapi.com:5036")
        self.assertNotIn("live.ctraderapi.com", inspect.getsource(ctrader))

    def test_market_order_payload_and_fill(self):
        adapter = self.connected()
        report = adapter._send_order(
            BrokerOrderRequest("ASTRA-abc", "EURUSD", Direction.LONG, 11_000.0, stop_loss=1.0980)
        )
        sent = [m for m in self.server.received if m["payloadType"] == P.NEW_ORDER_REQ][0]["payload"]
        self.assertEqual(sent["volume"], 1_100_000)
        self.assertEqual((sent["orderType"], sent["tradeSide"]), (1, 1))
        self.assertEqual(sent["clientOrderId"], "ASTRA-abc")
        # 1.1001 - 1.0980 = 0.0021 price units, sent in 1/100000 of a unit.
        self.assertEqual(sent["relativeStopLoss"], 210)
        self.assertIn(report.status, (BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.FILLED))
        self.assertTrue(wait_for(lambda: adapter.lookup_order("ASTRA-abc").status is BrokerOrderStatus.FILLED))
        self.assertEqual(adapter.lookup_order("ASTRA-abc").filled_units, 11_000.0)
        self.assertEqual(adapter.open_positions()[0].net_units, 11_000.0)
        account = adapter.account_snapshot()
        self.assertAlmostEqual(account.equity, 9_997.0)
        self.assertAlmostEqual(account.margin_used, 366.7)

    def test_resubmitting_known_client_id_sends_nothing(self):
        adapter = self.connected()
        request = BrokerOrderRequest("ASTRA-dup", "EURUSD", Direction.LONG, 1_000.0, stop_loss=1.09)
        adapter._send_order(request)
        adapter._send_order(request)
        self.assertEqual(self.sent_types().count(P.NEW_ORDER_REQ), 1)

    def test_exit_uses_close_position_on_the_single_open_position(self):
        adapter = self.connected()
        adapter._send_order(BrokerOrderRequest("ASTRA-in", "EURUSD", Direction.LONG, 2_000.0, stop_loss=1.09))
        self.assertTrue(wait_for(lambda: bool(self.server.positions)))
        adapter._send_order(BrokerOrderRequest("ASTRA-out", "EURUSD", Direction.SHORT, 2_000.0, reduce_only=True))
        close = [m for m in self.server.received if m["payloadType"] == P.CLOSE_POSITION_REQ][0]["payload"]
        self.assertEqual((close["positionId"], close["volume"]), (77, 200_000))
        self.assertEqual(adapter.lookup_order("ASTRA-out").status, BrokerOrderStatus.FILLED)

    def test_order_error_is_a_rejection(self):
        adapter = self.connected()
        self.server.order_behavior = "error"
        report = adapter._send_order(BrokerOrderRequest("ASTRA-err", "EURUSD", Direction.LONG, 1_000.0, stop_loss=1.09))
        self.assertIs(report.status, BrokerOrderStatus.REJECTED)
        self.assertIn("NOT_ENOUGH_MONEY", report.message)

    def test_silence_after_order_is_a_timeout_not_a_rejection(self):
        adapter = self.connected(timeout=0.2)
        self.server.order_behavior = "silent"
        with self.assertRaises(BrokerTimeout):
            adapter._send_order(BrokerOrderRequest("ASTRA-t", "EURUSD", Direction.LONG, 1_000.0, stop_loss=1.09))
        self.assertIsNone(adapter.lookup_order("ASTRA-t"))

    def test_foreign_activity_is_reported_as_foreign(self):
        adapter = self.connected()
        self.server.foreign_fill()
        self.assertTrue(wait_for(lambda: any(e.report.client_order_id == "FOREIGN-5555" for e in adapter.poll_events(0))))

    def test_link_loss_marks_disconnected(self):
        adapter = self.connected()
        self.transport.break_link()
        self.assertTrue(wait_for(lambda: adapter.connection_state() is ConnectionState.DISCONNECTED))
        with self.assertRaises(BrokerUnavailable):
            adapter.account_snapshot()

    def test_heartbeats_are_sent(self):
        self.connected()
        self.assertTrue(wait_for(lambda: P.HEARTBEAT_EVENT in self.sent_types()))

    def test_credentials_never_print(self):
        text = repr(credentials())
        for secret in ("client-id", "client-secret", "token-xyz"):
            self.assertNotIn(secret, text)


class CTraderGatewayTests(unittest.TestCase):
    """DEMO stays non-executable: the gateway must not send any cTrader order."""

    def setUp(self):
        self._tmp = workspace_tempdir()
        tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.store = ExecutionStore(f"{tmp}/astra.sqlite3")
        self.store.initialize()
        self.server = FakeCTraderServer()
        self.adapter = CTraderAdapter(credentials(), transport=FakeTransport(self.server), request_timeout=1.0, heartbeat_seconds=5)
        self.addCleanup(self.adapter.disconnect)
        self.adapter.connect()
        self.assertTrue(wait_for(lambda: self.adapter._quotes.get("EURUSD", {}).get("ask") is not None))

    def command(self, mode):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        return ExecutionCommand(
            intent_id=f"demo-{mode}", decision_id="d", correlation_id="c", mode=mode,
            account_ref="CTRADER-****4242", instrument="EURUSD", direction=Direction.LONG,
            created_at=now, expires_at=now + timedelta(seconds=30), strategy="s", strategy_version="1",
            checks_passed=("strategy_signal",), stop_price=1.0980,
        )

    def test_demo_mode_is_not_executable_in_this_build(self):
        gateway = OrderGateway(store=self.store, broker=self.adapter, configured_mode=RunMode.DEMO)
        result = gateway.submit(self.command(RunMode.DEMO), Actor("pipeline", Role.SYSTEM))
        self.assertIn("mode_not_executable_in_this_build", result.reason_codes)
        self.assertIn("mode_not_supported", result.reason_codes)
        self.assertNotIn(P.NEW_ORDER_REQ, [m["payloadType"] for m in self.server.received])

    def test_simulation_gateway_refuses_a_demo_adapter(self):
        gateway = OrderGateway(store=self.store, broker=self.adapter)
        result = gateway.submit(self.command(RunMode.SIMULATION), Actor("pipeline", Role.SYSTEM))
        self.assertIn("adapter_environment_mismatch", result.reason_codes)
        self.assertNotIn(P.NEW_ORDER_REQ, [m["payloadType"] for m in self.server.received])


if __name__ == "__main__":
    unittest.main()


class CTraderCheckCliTests(unittest.TestCase):
    def test_read_only_check_reports_demo_and_sends_no_order(self):
        from astra.cli import ctrader_check
        from astra.config import AppConfig
        from pathlib import Path

        with workspace_tempdir() as temp:
            config = AppConfig(database_path=Path(temp) / "state.sqlite3")
            server = FakeCTraderServer()
            env = {
                "ASTRA_SECRET_CTRADER_CLIENT_ID": "client-id",
                "ASTRA_SECRET_CTRADER_CLIENT_SECRET": "client-secret",
                "ASTRA_SECRET_CTRADER_ACCESS_TOKEN": "token-xyz",
                "ASTRA_CTRADER_ACCOUNT_ID": "4242",
            }
            result = ctrader_check("alice", config, environ=env, transport=FakeTransport(server))
            self.assertEqual(result["status"], "connected")
            self.assertEqual(result["environment_reported"], "DEMO")
            self.assertEqual(result["orders_sent"], 0)
            self.assertEqual(result["eurusd_quote"]["ask"], 1.1001)
            # DEMO is provider-verified but still not executable in this build.
            self.assertEqual(result["demo_mode_checks"]["reason_codes"], ["mode_not_executable_in_this_build"])
            sent = {m["payloadType"] for m in server.received}
            self.assertFalse(sent & {int(P.NEW_ORDER_REQ), int(P.CLOSE_POSITION_REQ)})
            self.assertNotIn("token-xyz", json.dumps(result, default=str))

    def test_missing_configuration_is_reported_without_connecting(self):
        from astra.cli import ctrader_check
        from astra.config import AppConfig
        from pathlib import Path

        with workspace_tempdir() as temp:
            config = AppConfig(database_path=Path(temp) / "state.sqlite3")
            server = FakeCTraderServer()
            with self.assertRaises(ValueError) as caught:
                ctrader_check("alice", config, environ={}, transport=FakeTransport(server))
            self.assertIn("ASTRA_SECRET_CTRADER_ACCESS_TOKEN", str(caught.exception))
            self.assertEqual(server.received, [])


class ExecutionBoundaryTests(unittest.TestCase):
    """C-01: no direct adapter path may send an order without every safety condition."""

    def connected(self, *, server=None, allow=False):
        from datetime import datetime, timezone

        self.server = server or FakeCTraderServer()
        adapter = CTraderAdapter(
            credentials(), transport=FakeTransport(self.server), request_timeout=1.0,
            heartbeat_seconds=5, allow_demo_orders=allow,
        )
        self.addCleanup(adapter.disconnect)
        adapter.connect()
        self.assertTrue(wait_for(lambda: adapter._quotes.get("EURUSD", {}).get("ask") is not None))
        self.request = BrokerOrderRequest("ASTRA-boundary", "EURUSD", Direction.LONG, 1_000.0, stop_loss=1.09)
        self.now = datetime.now(timezone.utc)
        return adapter

    def permit(self, mode=RunMode.DEMO):
        from astra.permits import _AUTHORITY

        return _AUTHORITY.issue(self.request, mode, self.now)

    def orders_sent(self):
        return [m for m in self.server.received if m["payloadType"] in (P.NEW_ORDER_REQ, P.CLOSE_POSITION_REQ)]

    def assert_refused(self, adapter, permit, reason_fragment):
        from astra.permits import ExecutionRefused

        with self.assertRaises(ExecutionRefused) as caught:
            adapter.submit_order(self.request, permit)
        self.assertIn(reason_fragment, str(caught.exception))
        self.assertEqual(self.orders_sent(), [])

    def test_direct_call_without_permit_is_refused(self):
        adapter = self.connected(allow=True)
        self.assert_refused(adapter, None, "missing_execution_permit")

    def test_forged_or_mismatched_permit_is_refused(self):
        from dataclasses import replace

        adapter = self.connected(allow=True)
        self.assert_refused(adapter, replace(self.permit(), signature="0" * 64), "invalid_permit_signature")
        self.assert_refused(adapter, replace(self.permit(), units=1_000_000.0), "invalid_permit_signature")
        other = BrokerOrderRequest("ASTRA-other", "EURUSD", Direction.LONG, 1_000.0)
        from astra.permits import _AUTHORITY

        self.assert_refused(adapter, _AUTHORITY.issue(other, RunMode.DEMO, self.now), "permit_does_not_match_request")

    def test_expired_permit_is_refused(self):
        from datetime import timedelta
        from astra.permits import _AUTHORITY

        adapter = self.connected(allow=True)
        old = _AUTHORITY.issue(self.request, RunMode.DEMO, self.now - timedelta(minutes=5))
        self.assert_refused(adapter, old, "permit_expired")

    def test_simulation_or_live_permit_is_refused_by_ctrader(self):
        adapter = self.connected(allow=True)
        self.assert_refused(adapter, self.permit(RunMode.SIMULATION), "requires_demo_permit")
        self.assert_refused(adapter, self.permit(RunMode.LIVE), "requires_demo_permit")

    def test_valid_permit_refused_while_demo_not_executable_or_not_allowed(self):
        adapter = self.connected(allow=False)
        self.assert_refused(adapter, self.permit(), "demo_orders_not_explicitly_allowed")
        self.assert_refused(adapter, self.permit(), "demo_not_executable_in_this_build")
        allowed = self.connected(allow=True)
        self.assert_refused(allowed, self.permit(), "demo_not_executable_in_this_build")

    def test_unknown_account_status_is_refused_even_if_demo_were_enabled(self):
        from unittest.mock import patch
        from astra.models import RunMode as Mode

        adapter = self.connected(server=FakeCTraderServer(include_is_live=False), allow=True)
        with patch("astra.execution.mode.EXECUTABLE_MODES", frozenset({Mode.SIMULATION, Mode.DEMO})):
            self.assert_refused(adapter, self.permit(), "account_status_not_confirmed_demo")

    def test_disconnected_adapter_is_refused(self):
        from unittest.mock import patch
        from astra.models import RunMode as Mode

        adapter = self.connected(allow=True)
        adapter.disconnect()
        with patch("astra.execution.mode.EXECUTABLE_MODES", frozenset({Mode.SIMULATION, Mode.DEMO})):
            self.assert_refused(adapter, self.permit(), "not_connected")

    def test_guard_is_not_a_blanket_refusal(self):
        """With every condition met (DEMO patched in for this test only) the boundary lets the order through."""
        from unittest.mock import patch
        from astra.models import RunMode as Mode

        adapter = self.connected(allow=True)
        with patch("astra.execution.mode.EXECUTABLE_MODES", frozenset({Mode.SIMULATION, Mode.DEMO})):
            report = adapter.submit_order(self.request, self.permit())
        self.assertEqual(len(self.orders_sent()), 1)
        self.assertEqual(report.client_order_id, "ASTRA-boundary")

    def test_build_has_demo_disabled(self):
        from astra.execution.mode import EXECUTABLE_MODES

        self.assertEqual(EXECUTABLE_MODES, frozenset({RunMode.SIMULATION}))

    def test_adapters_cannot_override_the_boundary(self):
        from astra.brokers.simulated import SimulatedBroker

        with self.assertRaises(TypeError):
            class Bypass(SimulatedBroker):
                def submit_order(self, request, permit=None):
                    return self._send_order(request)

    def test_simulated_broker_also_enforces_the_boundary(self):
        from astra.brokers.simulated import SimulatedBroker
        from astra.permits import ExecutionRefused, _AUTHORITY
        from datetime import datetime, timezone

        broker = SimulatedBroker()
        broker.set_quote("EURUSD", 1.1, 1.1001)
        request = BrokerOrderRequest("ASTRA-sim", "EURUSD", Direction.LONG, 1_000.0)
        with self.assertRaises(ExecutionRefused):
            broker.submit_order(request)
        with self.assertRaises(ExecutionRefused):
            broker.submit_order(request, _AUTHORITY.issue(request, RunMode.DEMO, datetime.now(timezone.utc)))
        self.assertEqual(broker.submit_calls, 0)
