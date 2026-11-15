"""LOT 4, second attempt: tests for the defects found by the independent verification (audit/lot4/VERIFICATION.md).

Written and committed before the fixes.
"""

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from _support import workspace_tempdir
from astra import cli, mt5_emergency, mt5_risk
from astra.agent import AstraClient
from astra.brokers.mt5_demo_orders import Mt5DemoTrader
from astra.mt5_bridge import Bridge
from test_lot4_risk import PRESET, account
from test_mt5_bridge import LOGIN, SHORT, FakeServer, TradingMt5, overview


class FixTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.dir = Path(self.temp.name)
        self.limits = replace(mt5_risk.RiskLimits(), **PRESET)

    def tearDown(self):
        self.temp.cleanup()

    def test_f1_the_test_order_command_never_sends_an_order_outside_the_gateway(self):
        fake = TradingMt5()
        with mock.patch("astra.brokers.mt5_demo_orders.Mt5DemoTrader",
                        lambda *a, **k: Mt5DemoTrader(module=fake, expected_login=LOGIN)):
            code = cli._mt5_test_order(LOGIN)
        self.assertNotEqual(code, 0)
        self.assertEqual(fake.sent, [])

    def test_f2_an_unreadable_equity_triggers_the_emergency_stop(self):
        self.assertIsNotNone(mt5_emergency.trigger(account(equity=float("nan")), self.limits))
        self.assertIsNotNone(mt5_emergency.trigger(account(peak_equity=float("inf")), self.limits))

    def test_f3_the_watcher_survives_any_error_and_keeps_watching(self):
        fake = TradingMt5()
        fake.account_info = lambda: (_ for _ in ()).throw(KeyError("broken"))
        code = mt5_emergency.main(["--workdir", str(self.dir), "--watch"], module=fake, login=LOGIN,
                                  sleep=lambda s: None, rounds=2, limits=self.limits)
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / "emergency.jsonl").read_text(encoding="utf-8").count("\n"), 2)

    def test_f4_an_emergency_stop_is_not_lifted_by_a_resume_and_the_bridge_closes_everything(self):
        server, fake = FakeServer(), TradingMt5()
        bridge = Bridge(AstraClient(server), Mt5DemoTrader(module=fake, expected_login=LOGIN), self.dir,
                        clock=lambda: datetime(2026, 10, 29, 9, tzinfo=UTC))
        server.view = overview(SHORT)
        bridge.cycle()
        self.assertEqual(len(fake.open_positions), 1)
        mt5_emergency.declare(self.dir, "test")  # the watcher could not reach the terminal: it hands over to the bridge
        self.assertTrue((self.dir / "STOP").exists() and (self.dir / "EMERGENCY").exists())
        with bridge.trader:
            status, _result, _ = bridge._handle_request({"id": 1, "kind": "resume"})
        self.assertEqual(status, "REFUSE")
        self.assertTrue((self.dir / "EMERGENCY").exists())
        bridge.cycle()
        self.assertEqual(fake.open_positions, [])

    def test_f5_without_the_terminal_the_watcher_decides_from_the_equity_the_bridge_recorded(self):
        state = {"peak_equity": 100_000.0, "last_equity": 87_000.0,
                 "last_equity_at": datetime.now(UTC).isoformat()}
        (self.dir / "bridge-state.json").write_text(json.dumps(state), encoding="utf-8")
        fake = TradingMt5()
        fake.initialize = lambda *a, **k: False  # IPC timeout: the terminal is held by the bridge
        mt5_emergency.main(["--workdir", str(self.dir), "--watch"], module=fake, login=LOGIN,
                           sleep=lambda s: None, rounds=1, limits=self.limits)
        self.assertTrue((self.dir / "EMERGENCY").exists())
        self.assertTrue((self.dir / "STOP").exists())


if __name__ == "__main__":
    unittest.main()
