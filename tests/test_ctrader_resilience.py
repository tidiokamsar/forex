"""F-03: cTrader reconnection, rate limiting, retryAfter and token handling (offline fixture only)."""

import unittest
from datetime import UTC, datetime, timedelta

from _support import workspace_tempdir
from astra.brokers import BrokerUnavailable, CapabilityNotSupported
from astra.brokers.base import ConnectionState
from astra.brokers.ctrader import (
    CTraderAdapter,
    CTraderConfigurationError,
    CTraderConnectionError,
    CTraderCredentials,
    LiveAccountRefused,
    RateLimiter,
)
from astra.brokers.ctrader import PayloadType as P
from astra.execution import ExecutionStore, OrderGateway
from astra.governance import SecretValue
from astra.models import RunMode
from ctrader_fixture import FakeCTraderServer, FakeTransport
from test_ctrader import wait_for

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def credentials(**overrides):
    values = dict(
        client_id=SecretValue("client-id"),
        client_secret=SecretValue("client-secret"),
        access_token=SecretValue("token-xyz"),
        ctid_trader_account_id=4242,
        refresh_token=SecretValue("refresh-abc"),
        access_token_expires_at=NOW + timedelta(days=20),
    )
    values.update(overrides)
    return CTraderCredentials(**values)


class Factory:
    """Builds a fresh fake transport per connection attempt; can fail the first N."""

    def __init__(self, server, fail_first=0):
        self.server = server
        self.fail_first = fail_first
        self.built = 0
        self.transports = []

    def __call__(self):
        self.built += 1
        transport = FakeTransport(self.server)
        if self.built <= self.fail_first:
            def refuse():
                raise OSError("connection refused (scripted)")

            transport.connect = refuse
        self.transports.append(transport)
        return transport


class ResilienceTests(unittest.TestCase):
    def make(self, server=None, fail_first=0, **kwargs):
        self.server = server or FakeCTraderServer()
        self.factory = Factory(self.server, fail_first)
        self.sleeps = []
        adapter = CTraderAdapter(
            kwargs.pop("creds", credentials()),
            transport_factory=self.factory,
            request_timeout=0.5,
            heartbeat_seconds=5,
            clock=lambda: NOW,
            sleep=self.sleeps.append,
            **kwargs,
        )
        self.addCleanup(adapter.disconnect)
        return adapter

    def sent(self, payload_type):
        return [m for m in self.server.received if m["payloadType"] == payload_type]

    def test_reconnect_after_link_loss_with_backoff_and_resubscription(self):
        adapter = self.make()
        adapter.connect()
        self.factory.transports[-1].break_link()
        self.assertTrue(wait_for(lambda: adapter.connection_state() is ConnectionState.DISCONNECTED))
        self.factory.fail_first = self.factory.built + 2  # next two attempts refused
        attempts = adapter.reconnect(max_attempts=5, base_delay=1.0)
        self.assertEqual(attempts, 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.assertIs(adapter.connection_state(), ConnectionState.CONNECTED)
        self.assertEqual(len(self.sent(P.SUBSCRIBE_SPOTS_REQ)), 2)

    def test_backoff_is_capped_and_attempts_are_bounded(self):
        adapter = self.make(fail_first=100)
        with self.assertRaises(BrokerUnavailable):
            adapter.reconnect(max_attempts=6, base_delay=1.0, max_delay=8.0)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0, 8.0, 8.0])

    def test_maintenance_is_transient_and_retried(self):
        server = FakeCTraderServer()
        server.fail_once[P.APPLICATION_AUTH_REQ] = "SERVER_IS_UNDER_MAINTENANCE"
        adapter = self.make(server)
        self.assertEqual(adapter.reconnect(max_attempts=3), 2)

    def test_permanent_failures_are_not_retried(self):
        cases = [
            (FakeCTraderServer(is_live=True), LiveAccountRefused),
            (FakeCTraderServer(account_id=1), CTraderConfigurationError),
        ]
        token = FakeCTraderServer()
        token.fail_with[P.APPLICATION_AUTH_REQ] = "CH_ACCESS_TOKEN_INVALID"
        cases.append((token, CTraderConnectionError))
        for server, error in cases:
            adapter = self.make(server)
            with self.assertRaises(error):
                adapter.reconnect(max_attempts=5)
            self.assertEqual(self.sleeps, [], error.__name__)

    def test_reconnecting_lifts_no_gateway_freeze(self):
        with workspace_tempdir() as temp:
            store = ExecutionStore(f"{temp}/s.sqlite3")
            store.initialize()
            adapter = self.make()
            adapter.connect()
            gateway = OrderGateway(store=store, broker=adapter, configured_mode=RunMode.DEMO, account_ref="CTRADER-****4242")
            self.factory.transports[-1].break_link()
            self.assertTrue(wait_for(lambda: adapter.connection_state() is ConnectionState.DISCONNECTED))
            gateway.check_connection()
            adapter.reconnect()
            self.assertIn("connection_lost", gateway.controls.freezes())
            # DEMO is not executable in this build, so reconciliation reports it and stays frozen.
            result = gateway.reconcile()
            self.assertIn("mode:mode_not_executable_in_this_build", result.divergences)
            self.assertIn("reconciliation_required", gateway.controls.freezes())

    def test_retry_after_is_honoured_once_for_reads(self):
        adapter = self.make()
        adapter.connect()
        original = self.server.handle
        state = {"refused": False}

        def rate_limited_once(message):
            if message["payloadType"] == P.RECONCILE_REQ and not state["refused"]:
                state["refused"] = True
                self.server.push({"clientMsgId": message["clientMsgId"], "payloadType": int(P.OA_ERROR_RES),
                                  "payload": {"errorCode": "BLOCKED_PAYLOAD_TYPE", "retryAfter": 2}})
                return
            original(message)

        self.server.handle = rate_limited_once
        self.assertEqual(adapter.open_positions(), ())
        self.assertEqual(self.sleeps, [2.0])

    def test_orders_are_never_retried_on_rate_limit(self):
        from astra.brokers import BrokerOrderRequest
        from astra.models import Direction

        adapter = self.make()
        adapter.connect()
        self.assertTrue(wait_for(lambda: adapter._quotes.get("EURUSD", {}).get("ask") is not None))
        original = self.server.handle

        def limit_orders(message):
            if message["payloadType"] == P.NEW_ORDER_REQ:
                self.server.received.append(message)
                self.server.push({"clientMsgId": message["clientMsgId"], "payloadType": int(P.OA_ERROR_RES),
                                  "payload": {"errorCode": "BLOCKED_PAYLOAD_TYPE", "retryAfter": 1}})
                return
            original(message)

        self.server.handle = limit_orders
        report = adapter._send_order(BrokerOrderRequest("ASTRA-rl", "EURUSD", Direction.LONG, 1_000.0, stop_loss=1.09))
        self.assertEqual(report.status.value, "REJECTED")
        self.assertEqual(len(self.sent(P.NEW_ORDER_REQ)), 1)
        self.assertEqual(self.sleeps, [])

    def test_expired_token_is_refused_before_any_message(self):
        adapter = self.make(creds=credentials(access_token_expires_at=NOW - timedelta(seconds=1)))
        with self.assertRaises(CTraderConnectionError):
            adapter.connect()
        self.assertEqual(self.server.received, [])
        self.assertEqual(self.factory.built, 0)

    def test_token_status_warns_near_expiry(self):
        adapter = self.make(creds=credentials(access_token_expires_at=NOW + timedelta(days=2)))
        self.assertTrue(adapter.token_status()["warning"])
        self.assertFalse(self.make().token_status()["warning"])

    def test_refresh_requires_a_secure_store_and_saves_before_use(self):
        adapter = self.make()
        adapter.connect()
        with self.assertRaises(CapabilityNotSupported):
            adapter.refresh_access_token(None)
        self.assertEqual(self.sent(P.REFRESH_TOKEN_REQ), [])

        saved = []

        class Store:
            def save(self, creds):
                saved.append(creds)

        expires = adapter.refresh_access_token(Store())
        self.assertEqual(expires, NOW + timedelta(seconds=2_628_000))
        self.assertEqual(saved[0].access_token.reveal(), "new-access")
        self.assertEqual(saved[0].refresh_token.reveal(), "new-refresh")
        self.assertNotIn("new-access", repr(saved[0]))

    def test_failed_store_keeps_old_credentials(self):
        adapter = self.make()
        adapter.connect()

        class BrokenStore:
            def save(self, creds):
                raise OSError("disk full")

        with self.assertRaises(OSError):
            adapter.refresh_access_token(BrokenStore())
        self.assertEqual(adapter._credentials.access_token.reveal(), "token-xyz")

    def test_rate_limit_during_authorization_is_transient(self):
        server = FakeCTraderServer()
        server.fail_once[P.APPLICATION_AUTH_REQ] = "BLOCKED_PAYLOAD_TYPE"
        adapter = self.make(server)
        self.assertEqual(adapter.reconnect(max_attempts=3, base_delay=1.0), 2)
        self.assertEqual(self.sleeps, [1.0])


class RateLimiterTests(unittest.TestCase):
    def test_bucket_waits_when_burst_is_exhausted(self):
        clock = {"t": 0.0}
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock["t"] += seconds

        limiter = RateLimiter(rate_per_second=40, burst=40, clock=lambda: clock["t"], sleep=sleep)
        for _ in range(40):
            limiter.acquire()
        self.assertEqual(sleeps, [])
        limiter.acquire()
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 1 / 40)
        clock["t"] += 1.0
        for _ in range(40):
            limiter.acquire()
        self.assertEqual(len(sleeps), 1)


if __name__ == "__main__":
    unittest.main()
