"""astra-demo runtime: account check, controlled test (gate, pause, full cycle), requests, restart (offline)."""

import unittest
from datetime import UTC, datetime
from pathlib import Path

from _support import workspace_tempdir
from astra.brokers.binance_spot_demo import BinanceSpotDemoAdapter
from astra.demo_requests import DemoStore
from astra.demo_runner import DemoRuntime
from astra.execution import ExecutionStore
from astra.governance import SecretValue
from binance_demo_fake import KEY, SECRET, FakeBinanceDemo


class DemoRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.fake = FakeBinanceDemo()
        self.now = datetime.fromtimestamp(self.fake.now_ms / 1000, UTC)
        self.store = DemoStore(self.db)
        self.store.initialize()
        self.gate = True
        self.adapter = BinanceSpotDemoAdapter(
            SecretValue(KEY), SecretValue(SECRET), ("BTCUSDT",), base_url="http://127.0.0.1:9", loopback_role="demo",
            transport=self.fake, clock=lambda: self.now, paused=self.store.paused,
            demo_trading_allowed=lambda: self.gate, test_only_trust_loopback=True)
        self.runtime = DemoRuntime(self.db, self.adapter, clock=lambda: self.now, gate=lambda: self.gate)

    def tearDown(self):
        self.temp.cleanup()

    def test_check_stores_a_non_sensitive_status(self):
        status = self.runtime.check()
        self.assertTrue(status["connected"])
        self.assertEqual((status["environment_reported"], status["environment_source"]), ("DEMO", "PROVIDER_API"))
        self.assertEqual(status["quote"]["source"], "PROVIDER_API:binance_spot_demo:bookTicker")
        stored = self.store.get("status")
        self.assertNotIn(KEY, str(stored))
        self.assertNotIn(SECRET, str(stored))
        self.assertEqual(len(self.store.quotes("BTCUSDT")), 1)

    def test_controlled_test_needs_resume_and_an_open_gate(self):
        paused = self.runtime.controlled_test("ops")
        self.assertEqual(paused["result"], "ÉCHEC")
        self.assertIn("pause", paused["error"])
        self.store.set("paused", False)
        self.gate = False
        closed = self.runtime.controlled_test("ops")
        self.assertIn("registre", closed["error"])
        self.assertEqual(self.fake.orders, [])

    def test_controlled_test_full_cycle(self):
        self.store.set("paused", False)
        run = self.runtime.controlled_test("ops")
        self.assertEqual(run["result"], "RÉUSSI", run)
        steps = {s["step"]: s for s in run["steps"]}
        self.assertEqual((steps["buy"]["state"], steps["buy"]["broker_status"]), ("FILLED", "FILLED"))
        self.assertEqual(steps["buy"]["broker_order_id"], "1")
        self.assertEqual(steps["sell"]["state"], "FILLED")
        self.assertEqual(steps["reconciliation_after_sell"]["status"], "CONSISTENT")
        self.assertEqual([o["side"] for o in self.fake.orders], ["BUY", "SELL"])
        notional = float(self.fake.orders[0]["cummulativeQuoteQty"])
        self.assertTrue(5.0 <= notional <= 8.0, notional)  # just above the minimum notional
        stored = self.store.tests()[0]
        self.assertEqual((stored["status"], stored["actor"]), ("RÉUSSI", "ops"))
        orders = ExecutionStore(self.db).list_orders(limit=10)
        self.assertEqual({o.state.value for o in orders}, {"FILLED"})
        self.assertTrue(all(o.mode == "DEMO" for o in orders))

    def test_dashboard_requests_and_restart_recovery(self):
        self.assertTrue(self.store.paused())
        self.assertTrue(self.store.request("resume", "web:ops")["id"])
        self.assertTrue(self.store.request("resume", "web:ops")["duplicate"])
        self.runtime.tick()
        self.assertFalse(self.store.paused())
        self.store.request("controlled_test", "web:ops")
        running = self.store.next_request()  # astra-demo stops here
        self.assertEqual(running["kind"], "controlled_test")
        DemoRuntime(self.db, self.adapter, clock=lambda: self.now, gate=lambda: self.gate).demo.recover()
        self.assertEqual(self.store.requests()[0]["status"], "FAILED")
        self.assertEqual(self.fake.orders, [])

    def test_provider_down_is_reported_not_hidden(self):
        self.fake.fail_next[("GET", "/api/v3/time")] = "timeout"
        status = self.runtime.check()
        self.assertFalse(status["connected"])
        self.assertIn("unreachable", status["error"])


if __name__ == "__main__":
    unittest.main()
