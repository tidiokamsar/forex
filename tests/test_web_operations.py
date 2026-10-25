import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from astra.engine import run_paper_replay
from astra.execution import ExecutionStore, TradingControls
from astra.registry import ModelRegistry
from astra.storage import BacktestStore
from astra.web import DashboardServer, make_handler
from _support import workspace_tempdir
from test_engine import synthetic_bars


class OperationsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        path = Path(self.temp.name) / "astra.sqlite3"
        self.store = BacktestStore(path)
        self.store.initialize()
        self.execution = ExecutionStore(path)
        self.execution.initialize()
        ModelRegistry(path).initialize()
        self.server = DashboardServer(("127.0.0.1", 0), make_handler(self.store))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get(self, route):
        with urlopen(f"{self.base}{route}") as response:
            return json.load(response)

    def test_operation_routes_expose_replay_orders_and_controls(self):
        run_paper_replay(synthetic_bars(), self.execution, run_id="web")
        orders = self.get("/api/orders")["orders"]
        self.assertTrue(orders)
        self.assertTrue(all(order["mode"] == "SIMULATION" for order in orders))
        controls = self.get("/api/controls")
        self.assertTrue(controls["entries_allowed"])
        self.assertTrue(controls["live_locked"])
        self.assertEqual(self.get("/api/reconciliations")["reconciliations"][0]["status"], "CONSISTENT")
        self.assertEqual(self.get("/api/models"), {"models": []})
        self.assertEqual(self.get("/api/experiments"), {"experiments": []})

    def test_kill_switch_state_is_visible_and_not_mutable_over_http(self):
        TradingControls(self.execution).activate_kill_switch("operator", "drill")
        controls = self.get("/api/controls")
        self.assertTrue(controls["kill_switch"]["active"])
        self.assertFalse(controls["entries_allowed"])
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(f"{self.base}/api/controls", method="POST", data=b"{}"))
        self.assertEqual(error.exception.code, 405)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
