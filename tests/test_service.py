"""F-02: continuous service on the SIMULATED broker with a SYNTHETIC quote feed (no external system)."""

import threading
import unittest
from datetime import UTC, datetime, timedelta

from _support import workspace_tempdir
from astra.brokers.simulated import SimulatedBroker
from astra.engine import ReplayDecision, strategy_decisions
from astra.execution import ExecutionStore, OrderState, TradingControls
from astra.models import Direction
from astra.service import BarBuilder, SyntheticQuoteFeed, TradingService, service_status

ACCOUNT = "SIM-SERVICE-1"


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.store = ExecutionStore(f"{self.tmp}/astra.sqlite3")
        self.store.initialize()

    def make(self, *, broker=None, feed=None, decide=None, **kwargs):
        clock_holder = {}
        broker = broker or SimulatedBroker(account_ref=ACCOUNT, clock=lambda: clock_holder["feed"].clock())
        feed = feed or SyntheticQuoteFeed(broker, seed=kwargs.pop("seed", 7), step_seconds=10, volatility=0.00012)
        clock_holder["feed"] = feed
        service = TradingService(
            store=self.store,
            broker=broker,
            decide=decide or strategy_decisions("sma"),
            account_ref=ACCOUNT,
            timeframe_seconds=60,
            clock=feed.clock,
            **kwargs,
        )
        return service, broker, feed

    def orders(self):
        return self.store.list_orders(limit=500, account_ref=ACCOUNT)


class ServiceRunTests(ServiceTestCase):
    def test_long_run_trades_reconciles_and_stays_consistent(self):
        service, _broker, feed = self.make()
        stats = service.run(advance=feed.advance, max_ticks=1_500)
        self.assertEqual(stats.ticks, 1_500)
        self.assertGreater(stats.bars, 200)
        self.assertGreater(stats.submitted.get("FILLED", 0), 0)
        self.assertEqual(stats.errors, 0)
        self.assertNotIn("DIVERGENT", stats.reconciliations)
        self.assertEqual(service.gateway.reconcile().status, "CONSISTENT")
        for order in self.orders():
            self.assertEqual(order.mode, "SIMULATION")
            self.assertIn(order.state, (OrderState.FILLED, OrderState.RISK_REJECTED))
        status = service_status(self.store)[0]
        self.assertEqual(status["status"], "stopped")
        self.assertEqual(status["ticks"], 1_500)

    def test_stop_from_another_thread_is_graceful(self):
        service, _broker, feed = self.make()
        timer = threading.Timer(0.5, service.stop)
        timer.start()
        stats = service.run(advance=feed.advance, tick_interval_seconds=0.01)
        timer.cancel()
        self.assertGreater(stats.ticks, 0)
        self.assertEqual(service_status(self.store)[0]["status"], "stopped")

    def test_restart_on_same_store_never_duplicates_orders(self):
        def always_long(history):
            if len(history) >= 3:
                return ReplayDecision(Direction.LONG, history[-1].low - 0.002, "always_long", "1", "test")
            return None

        service, broker, feed = self.make(decide=always_long)
        service.run(advance=feed.advance, max_ticks=60)
        before = len(self.orders())
        self.assertEqual(before, 1)  # one entry, then holding
        # "Crash" and restart with the same store and broker: persisted last bar is honoured.
        restarted = TradingService(store=self.store, broker=broker, decide=always_long, account_ref=ACCOUNT,
                                   timeframe_seconds=60, clock=feed.clock)
        self.assertIsNotNone(restarted.last_bar)
        restarted.run(advance=feed.advance, max_ticks=60)
        self.assertEqual(len(self.orders()), before)
        self.assertEqual(len(broker.open_positions()), 1)

    def test_disconnect_freezes_then_reconnect_and_reconcile_resume(self):
        from astra.brokers import BrokerUnavailable

        attempts = []
        holder = {}

        def reconnect():
            # First attempt fails, second restores the simulated link.
            attempts.append(1)
            if len(attempts) == 1:
                raise BrokerUnavailable("still down")
            holder["broker"].reconnect()

        service, broker, feed = self.make(reconnect=reconnect)
        holder["broker"] = broker
        service.run(advance=feed.advance, max_ticks=50)
        broker.disconnect()
        service.tick()
        self.assertIn("connection_lost", service.gateway.controls.freezes())
        self.assertEqual(service.stats.reconnects, 0)
        feed.advance()
        service.tick()
        self.assertEqual(service.stats.reconnects, 1)
        # Reconnected and immediately reconciled: freezes lifted in the same tick.
        self.assertEqual(service.gateway.controls.freezes(), ())

    def test_repeated_errors_put_the_service_in_degraded_mode(self):
        service, broker, _feed = self.make()
        service.start()

        def broken(_instrument):
            raise RuntimeError("feed parser bug")

        broker.quote = broken
        for _ in range(6):
            service.tick()
        self.assertTrue(service.stats.degraded)
        self.assertIn("service_degraded", service.gateway.controls.freezes())
        self.assertEqual(service_status(self.store)[0]["status"], "degraded")

    def test_kill_switch_stops_new_entries(self):
        service, broker, feed = self.make()
        TradingControls(self.store).activate_kill_switch("operator", "drill")
        stats = service.run(advance=feed.advance, max_ticks=1_000)
        self.assertEqual(stats.submitted.get("FILLED", 0), 0)
        self.assertGreater(stats.submitted.get("RISK_REJECTED", 0), 0)
        self.assertEqual(broker.submit_calls, 0)

    def test_startup_with_unknown_external_position_stays_frozen(self):
        service, broker, _feed = self.make()
        broker.inject_position("EURUSD", 5_000, 1.1)
        self.assertEqual(service.start(), "DIVERGENT")
        self.assertIn("position_divergence", service.gateway.controls.freezes())


class BarBuilderTests(unittest.TestCase):
    def test_bar_is_emitted_only_after_it_closes(self):
        builder = BarBuilder("EURUSD", 60)
        t0 = datetime(2026, 1, 5, 7, 0, tzinfo=UTC)
        self.assertIsNone(builder.update(1.1000, 1.1002, t0))
        self.assertIsNone(builder.update(1.1010, 1.1012, t0 + timedelta(seconds=30)))
        self.assertIsNone(builder.update(1.0990, 1.0992, t0 + timedelta(seconds=59)))
        bar = builder.update(1.1005, 1.1007, t0 + timedelta(seconds=60))
        self.assertEqual(bar.timestamp, t0)
        self.assertAlmostEqual(bar.open, 1.1001)
        self.assertAlmostEqual(bar.high, 1.1011)
        self.assertAlmostEqual(bar.low, 1.0991)
        self.assertAlmostEqual(bar.close, 1.0991)


if __name__ == "__main__":
    unittest.main()
