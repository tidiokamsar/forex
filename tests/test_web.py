import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from astra.storage import BacktestStore
from astra.web import DashboardServer, make_handler
from _support import workspace_tempdir


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.store = BacktestStore(Path(self.temp.name) / "astra.sqlite3")
        self.store.initialize()
        self.server = DashboardServer(("127.0.0.1", 0), make_handler(self.store))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_health_and_summary_are_simulation_only(self):
        with urlopen(f"{self.base}/api/health") as response:
            health = json.load(response)
            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["mode"], "SIMULATION")
            self.assertTrue(health["live_locked"])
        with urlopen(f"{self.base}/api/summary") as response:
            summary = json.load(response)
            self.assertEqual(summary["mode"], "SIMULATION")
            self.assertIsNone(summary["latest_run"])

    def test_dashboard_and_api_are_read_only(self):
        with urlopen(self.base) as response:
            html = response.read().decode("utf-8")
            self.assertIn('lang="fr"', html)
            self.assertIn("RÉEL VERROUILLÉ", html)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        request = Request(f"{self.base}/api/summary", method="POST", data=b"{}")
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        try:
            self.assertEqual(error.exception.code, 405)
        finally:
            error.exception.close()

    def test_unknown_path_does_not_expose_files(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base}/../../README.md")
        try:
            self.assertEqual(error.exception.code, 404)
        finally:
            error.exception.close()

    def test_api_rejects_unrecognized_host_header(self):
        request = Request(
            f"{self.base}/api/summary",
            headers={"Host": "attacker.invalid"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        try:
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(json.load(error.exception), {"error": "invalid_host"})
        finally:
            error.exception.close()


if __name__ == "__main__":
    unittest.main()
