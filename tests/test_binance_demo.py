"""Binance Spot DEMO adapter and the gateway's spot DEMO path (offline, in-process fake of Demo Mode).

Covers: the full lifecycle of a controlled test order (buy, lookup, reconcile, sell back), a lost answer after
a fill (UNKNOWN, then the lookup resolves it, never a second order), risk refusals, and every LIVE refusal.
"""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra.brokers import binance_spot_demo as demo
from astra.brokers.base import BrokerOrderRequest, BrokerUnavailable, EnvironmentSource
from astra.execution import ExecutionStore, OrderGateway, OrderState, verify_mode
from astra.execution.gateway import CONTROLLED_TEST_STRATEGY, ExecutionCommand
from astra.execution.mode import demo_executable
from astra.execution.spot_demo import SpotDemoPolicy, SpotPolicyError
from astra.governance import Actor, Role, SecretValue
from astra.models import Direction, RunMode
from astra.permits import ExecutionRefused
from binance_demo_fake import KEY, SECRET, FakeBinanceDemo

OPERATOR = Actor("ops", Role.HUMAN_OPERATOR)
SYSTEM = Actor("strategy-runner", Role.SYSTEM)


class DemoCase(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.fake = FakeBinanceDemo()
        self.now = datetime.fromtimestamp(self.fake.now_ms / 1000, UTC)
        self.paused = False
        self.adapter = self.make_adapter()
        self.store = ExecutionStore(Path(self.temp.name) / "astra.sqlite3")
        self.store.initialize()
        snapshot = self.adapter.account_snapshot()
        self.account_ref = snapshot.account_ref_masked
        self.gateway = OrderGateway(store=self.store, broker=self.adapter, configured_mode=RunMode.DEMO,
                                    spot_policy=SpotDemoPolicy(), position_tolerance_units=0.00001,
                                    account_ref=self.account_ref, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def clock(self):
        return self.now

    def make_adapter(self, **kwargs):
        options = {"base_url": "http://127.0.0.1:9", "loopback_role": "demo", "transport": self.fake,
                   "clock": self.clock, "paused": lambda: self.paused, "demo_trading_allowed": lambda: True,
                   "test_only_trust_loopback": True, **kwargs}
        return demo.BinanceSpotDemoAdapter(SecretValue(KEY), SecretValue(SECRET), ("BTCUSDT",), **options)

    def command(self, intent, *, purpose="ENTRY", direction=Direction.LONG, units=0.0001, stop=78000.0,
                strategy=CONTROLLED_TEST_STRATEGY, checks=("controlled_test",)):
        return ExecutionCommand(intent_id=intent, decision_id=f"d-{intent}", correlation_id=f"c-{intent}",
                                mode=RunMode.DEMO, account_ref=self.account_ref, instrument="BTCUSDT",
                                direction=direction, created_at=self.now, expires_at=self.now + timedelta(minutes=1),
                                strategy=strategy, strategy_version="1", checks_passed=checks, purpose=purpose,
                                stop_price=stop if purpose == "ENTRY" else None, requested_units=units)


class LifecycleTests(DemoCase):
    def test_controlled_test_buy_lookup_reconcile_and_close(self):
        self.assertTrue(demo_executable(self.adapter))
        buy = self.gateway.submit(self.command("t1"), OPERATOR)
        self.assertEqual(buy.state, OrderState.FILLED, buy.reason_codes)
        record = self.store.get_order("t1")
        self.assertEqual(record.client_order_id, buy.client_order_id)
        self.assertTrue(record.client_order_id.startswith("ASTRA-"))
        report = self.adapter.lookup_order(record.client_order_id)
        self.assertEqual((report.status.value, report.broker_order_id), ("FILLED", "1"))
        self.assertAlmostEqual(report.filled_units, 0.0001 * 0.999, places=10)  # net of the base-asset fee
        self.assertEqual(self.gateway.reconcile().status, "CONSISTENT")
        sell = self.gateway.submit(self.command("t1-close", purpose="EXIT", direction=Direction.SHORT, units=None), OPERATOR)
        self.assertEqual(sell.state, OrderState.FILLED, sell.reason_codes)
        self.assertEqual(len([o for o in self.fake.orders if o["side"] == "BUY"]), 1)
        self.assertEqual(self.gateway.reconcile().status, "CONSISTENT")
        self.assertEqual(self.adapter.open_positions(), ())  # only dust below the step remains

    def test_the_same_intent_is_never_sent_twice(self):
        self.gateway.submit(self.command("t2"), OPERATOR)
        again = self.gateway.submit(self.command("t2"), OPERATOR)
        self.assertTrue(again.duplicate)
        self.assertEqual(len(self.fake.orders), 1)

    def test_lost_answer_after_fill_is_resolved_by_lookup_not_by_resending(self):
        self.fake.fail_next[("POST", "/api/v3/order")] = "after_fill_timeout"
        lost = self.gateway.submit(self.command("t3"), OPERATOR)
        self.assertEqual(lost.state, OrderState.UNKNOWN)
        self.assertIn("unknown_order_state", self.gateway.controls.freezes())
        retry = self.gateway.submit(self.command("t3"), OPERATOR)  # same intent: lookup only
        self.assertEqual(retry.state, OrderState.FILLED)
        self.assertEqual(len(self.fake.orders), 1)

    def test_order_not_visible_inside_the_receive_window_stays_unknown(self):
        self.fake.fail_next[("POST", "/api/v3/order")] = "timeout"
        self.assertEqual(self.gateway.submit(self.command("t4"), OPERATOR).state, OrderState.UNKNOWN)
        with self.assertRaises(BrokerUnavailable):
            self.adapter.lookup_order(self.store.get_order("t4").client_order_id)
        self.now += timedelta(seconds=30)
        self.assertIsNone(self.adapter.lookup_order(self.store.get_order("t4").client_order_id))


class RiskTests(DemoCase):
    def test_controlled_test_needs_a_human_and_strategy_orders_need_a_signal(self):
        self.assertEqual(self.gateway.submit(self.command("r1"), SYSTEM).reason_codes, ("controlled_test_requires_human",))
        no_signal = self.gateway.submit(self.command("r2", strategy="sma_atr_trend", checks=()), SYSTEM)
        self.assertIn("missing_strategy_signal_check", no_signal.reason_codes)
        self.assertEqual(self.fake.orders, [])

    def test_spot_refusals(self):
        short = self.gateway.submit(self.command("r3", direction=Direction.SHORT), OPERATOR)
        self.assertIn("spot_short_refused", short.reason_codes)
        no_stop_above = self.gateway.submit(self.command("r4", stop=90000.0), OPERATOR)
        self.assertIn("stop_must_be_below_entry", no_stop_above.reason_codes)
        too_big = self.gateway.submit(self.command("r5", units=1.0), OPERATOR)
        self.assertIn("requested_size_exceeds_limits", too_big.reason_codes)
        dust = self.gateway.submit(self.command("r6", units=0.00001), OPERATOR)  # 0.8 USDT < 5 USDT notional
        self.assertTrue(any("notional" in code for code in dust.reason_codes), dust.reason_codes)
        nothing = self.gateway.submit(self.command("r7", purpose="EXIT", direction=Direction.SHORT, units=None), OPERATOR)
        self.assertIn("no_position_to_reduce", nothing.reason_codes)
        self.assertEqual(self.fake.orders, [])

    def test_stale_quote_pause_kill_switch_and_connection_loss_block_entries(self):
        stale_clock = {"now": self.now}
        adapter = self.make_adapter(clock=lambda: stale_clock["now"])
        adapter.account_snapshot()
        quote = adapter.quote("BTCUSDT")
        self.assertEqual(quote.source, "PROVIDER_API:binance_spot_demo:bookTicker")
        self.paused = True
        paused = self.gateway.submit(self.command("r8"), OPERATOR)
        self.assertIn("execution_refused:demo_execution_paused", paused.reason_codes)
        self.paused = False
        self.gateway.controls.activate_kill_switch("ops", "drill")
        self.assertIn("kill_switch_active", self.gateway.submit(self.command("r9"), OPERATOR).reason_codes)
        self.assertEqual(self.fake.orders, [])

    def test_policy_ceilings(self):
        for field, value in (("risk_per_trade", 0.05), ("max_positions", 9), ("max_drawdown", 0.9), ("allocation", 1e9)):
            with self.assertRaises(SpotPolicyError):
                replace(SpotDemoPolicy(), **{field: value})


class LiveRefusalTests(DemoCase):
    def test_no_host_other_than_demo(self):
        for url in ("https://api.binance.com", "https://api1.binance.com", "https://testnet.binance.vision",
                    "https://data-api.binance.vision", "https://demo-api.binance.com.evil.example", "http://demo-api.binance.com"):
            with self.assertRaises(BrokerUnavailable, msg=url):
                demo.BinanceSpotDemoAdapter(SecretValue(KEY), SecretValue(SECRET), ("BTCUSDT",), base_url=url)
        with self.assertRaises(BrokerUnavailable):  # loopback without the explicit test flag
            demo.BinanceSpotDemoAdapter(SecretValue(KEY), SecretValue(SECRET), ("BTCUSDT",),
                                        base_url="http://127.0.0.1:9", loopback_role="demo")
        source = Path(demo.__file__).read_text(encoding="utf-8")
        for host in ("api.binance.com", "api1.binance.com", "testnet.binance.vision", "withdraw", "/sapi/"):
            self.assertNotIn(f'"https://{host}', source)
            self.assertNotIn(f"'{host}'", source)
        self.assertEqual({path for _, path in demo.ALLOWED_CALLS} - {
            "/api/v3/time", "/api/v3/exchangeInfo", "/api/v3/ticker/bookTicker", "/api/v3/klines", "/api/v3/account",
            "/api/v3/order", "/api/v3/allOrders", "/api/v3/myTrades"}, set())

    def test_live_and_simulation_permits_are_refused_at_the_adapter(self):
        request = BrokerOrderRequest(client_order_id="ASTRA-" + "0" * 24, instrument="BTCUSDT", direction=Direction.LONG,
                                     units=0.0001)
        for permit in (None, object()):
            with self.assertRaises(ExecutionRefused):
                self.adapter.submit_order(request, permit)
        self.assertEqual(self.fake.orders, [])

    def test_live_mode_and_look_alike_adapters_are_not_executable(self):
        capabilities, account = self.adapter.capabilities(), self.adapter.account_snapshot()
        self.assertTrue(verify_mode(RunMode.DEMO, RunMode.DEMO, capabilities, account, self.adapter).verified)
        live = verify_mode(RunMode.LIVE, RunMode.LIVE, capabilities, account, self.adapter)
        self.assertIn("live_locked_requires_human_authorization", live.reason_codes)

        class LookAlike(demo.BinanceSpotDemoAdapter):
            pass

        look_alike = LookAlike(SecretValue(KEY), SecretValue(SECRET), ("BTCUSDT",), base_url="http://127.0.0.1:9",
                               loopback_role="demo", transport=self.fake, test_only_trust_loopback=True)
        self.assertFalse(demo_executable(look_alike))
        self.assertIn("mode_not_executable_in_this_build",
                      verify_mode(RunMode.DEMO, RunMode.DEMO, capabilities, account, look_alike).reason_codes)
        with self.assertRaises(ValueError):
            OrderGateway(store=self.store, broker=self.adapter, configured_mode=RunMode.SIMULATION, spot_policy=SpotDemoPolicy())

    def test_a_non_spot_or_unconfirmed_account_is_not_demo(self):
        self.fake.account_type = "MARGIN"
        snapshot = self.adapter.account_snapshot()
        self.assertEqual((snapshot.environment_reported.value, snapshot.environment_source), ("UNKNOWN", EnvironmentSource.UNVERIFIED))
        refused = self.gateway.submit(self.command("l1"), OPERATOR)
        self.assertNotEqual(refused.state, OrderState.FILLED)
        self.assertEqual(self.fake.orders, [])

    def test_credentials_loader_refuses_live_variables(self):
        good = {"BINANCE_DEMO_API_KEY": KEY, "BINANCE_DEMO_API_SECRET": SECRET}
        key, _secret = demo.load_demo_credentials(good)
        self.assertNotIn(KEY, repr(key))
        for extra in ("ASTRA_SECRET_BINANCE_API_KEY", "BINANCE_API_KEY", "BINANCE_LIVE_SECRET"):
            with self.assertRaises(BrokerUnavailable):
                demo.load_demo_credentials({**good, extra: "x"})
        with self.assertRaises(BrokerUnavailable):
            demo.load_demo_credentials({})
        self.assertNotIn(KEY, repr(self.adapter))


if __name__ == "__main__":
    unittest.main()


class RegistryGateTests(unittest.TestCase):
    def test_demo_gates_follow_the_evidence(self):
        from dataclasses import replace as swap

        from astra.venues.contract import VenueEnvironment
        from astra.venues.registry import StatusGrant, VenueStatus, can_trade, can_trade_demo, can_trade_demo_auto, load_registry

        record = load_registry().get("binance_spot_demo")
        self.assertEqual(record.environments, (VenueEnvironment.DEMO,))
        self.assertFalse(can_trade_demo(record))  # eligibility not confirmed yet

        def grant(rec, *statuses):
            kept = tuple(g for g in rec.statuses if g.status is not VenueStatus.ELIGIBILITY_UNKNOWN)
            return swap(rec, statuses=kept + tuple(StatusGrant(s, "2026-10-28", "test") for s in statuses))

        readable = grant(record, VenueStatus.REGION_ELIGIBILITY_VERIFIED, VenueStatus.READ_ONLY_CONNECTED)
        self.assertTrue(can_trade_demo(readable))
        self.assertFalse(can_trade_demo_auto(readable))
        self.assertTrue(can_trade_demo_auto(grant(readable, VenueStatus.DEMO_VALIDATED)))
        self.assertFalse(can_trade_demo(swap(readable, environments=(VenueEnvironment.DEMO, VenueEnvironment.LIVE))))
        self.assertFalse(can_trade_demo(swap(readable, venue_id="binance_spot")))
        self.assertFalse(can_trade(grant(readable, VenueStatus.DEMO_VALIDATED)))  # never real trading
