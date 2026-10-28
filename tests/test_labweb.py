"""Worker jobs and the dashboard routes for data, backtests and simulations (permissions, CSRF, flows)."""

import io
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra import marketdata
from astra.auth import WebRole
from astra.jobs import JobStore, Worker, worker_health
from astra.marketdata import CandleStore
from astra.papertrade import LabStore
from test_marketdata import ECB_FIXTURE
from test_webapp import ORIGIN, WebAppCase


def _row(i):
    open_, close = 1.1 + (i % 7) * 0.001, 1.1 + ((i + 1) % 7) * 0.001
    stamp = (datetime(2026, 1, 5, tzinfo=UTC) + timedelta(hours=i)).isoformat()
    return f"{stamp},{open_:.5f},{max(open_, close) + 0.002:.5f},{min(open_, close) - 0.002:.5f},{close:.5f}"


CSV = ("timestamp,open,high,low,close\n" + "\n".join(_row(i) for i in range(120)) + "\n").encode()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        root = Path(self.temp.name)
        self.db, self.imports = root / "astra.sqlite3", root / "imports"
        self.imports.mkdir()
        self.calls = 0

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, url, timeout):
        self.calls += 1
        return ECB_FIXTURE.encode()

    def worker(self, fetch=None):
        worker = Worker(self.db, self.imports, ecb_fetch=fetch or self.fetch)
        worker.initialize()
        return worker

    def test_import_job_runs_once_and_removes_its_file(self):
        worker = self.worker()
        path = self.imports / "a.csv"
        path.write_bytes(CSV)
        params = {"path": str(path), "symbol": "EURUSD", "timeframe": "H1", "source": "fixture"}
        first = worker.jobs.enqueue("import_csv", params, "web:ops", dedupe_key="same")
        self.assertTrue(worker.jobs.enqueue("import_csv", params, "web:ops", dedupe_key="same")["duplicate"])
        worker.ecb_enabled = False
        worker.tick()
        job = worker.jobs.jobs()[0]
        self.assertEqual((job["id"], job["status"], job["result"]["inserted"]), (first["id"], "DONE", 120))
        self.assertFalse(path.exists())
        self.assertEqual(worker_health(worker.jobs)["state"], "OK")

    def test_ecb_is_scheduled_with_a_rate_limit_and_failures_do_not_stop_the_worker(self):
        def down(url, timeout):
            raise urllib.error.URLError("down")

        worker = self.worker(fetch=down)
        worker.tick()  # schedules and runs the first acquisition: it fails
        job = worker.jobs.jobs()[0]
        self.assertEqual((job["kind"], job["status"]), ("ingest_ecb", "FAILED"))
        worker.tick()
        self.assertEqual(len(worker.jobs.jobs()), 1)  # back-off: not rescheduled at once
        worker.ecb_fetch = self.fetch
        source = worker.candles.source(marketdata.ECB_SOURCE)
        with worker.candles._connect() as db:  # the back-off has elapsed
            db.execute("UPDATE md_sources SET next_allowed_at = ?, last_attempt_at = ? WHERE name = ?",
                       ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", source["name"]))
        worker.tick()
        self.assertEqual(worker.jobs.jobs()[0]["status"], "DONE")
        worker.tick()
        self.assertEqual(len(worker.jobs.jobs()), 2)  # fresh data: no new request for 6 hours
        self.assertEqual(self.calls, 1)

    def test_interrupted_job_is_marked_failed_after_a_restart(self):
        worker = self.worker()
        worker.jobs.enqueue("ingest_ecb", {}, "test")
        self.assertIsNotNone(worker.jobs.claim())  # the worker dies here
        restarted = self.worker()
        job = restarted.jobs.jobs()[0]
        self.assertEqual(job["status"], "FAILED")
        self.assertIn("redémarrage", job["error"])

    def test_backtest_job_and_simulation_step(self):
        worker = self.worker()
        worker.ecb_enabled = False
        worker.candles.import_csv(CSV, "EURUSD", "H1", "fixture")
        lab = LabStore(self.db)
        from astra.papertrade import backtest_request, simulation_request

        request = backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "fixture",
                                    "strategy_params": {"fast": 3, "slow": 8, "atr_window": 3, "min_atr_pct": 0}})
        backtest_id = lab.create_backtest(request, "test")
        worker.jobs.enqueue("backtest", {"backtest_id": backtest_id}, "test")
        session_id = lab.create_session(simulation_request({"symbols": ["EURUSD"], "timeframe": "H1", "source": "fixture",
                                                            "start": "2026-01-01"}, worker.candles), "test")
        summary = worker.tick()
        self.assertEqual(summary["job"]["status"], "DONE")
        self.assertEqual(lab.backtest(backtest_id)["status"], "DONE")
        self.assertEqual(summary["sessions"][session_id]["processed"], 120)


class LabRouteTests(WebAppCase):
    def setUp(self):
        super().setUp()
        self.candles = CandleStore(self.db)
        self.candles.initialize()

    def call(self, client, method, path, json=None, data=None, csrf=True):
        headers = {"Origin": ORIGIN}
        if csrf:
            headers["X-CSRF-Token"] = self.page_csrf(client)
        return client.open(path, method=method, json=json, data=data, headers=headers)

    def test_anonymous_and_read_only_limits(self):
        anonymous = self.client()
        for path in ("/api/market/overview", "/api/lab/backtests", "/api/sim/sessions", "/api/system/jobs"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        viewer = self.session_client("viewer", WebRole.READ_ONLY)
        overview = viewer.get("/api/market/overview").get_json()
        self.assertEqual(overview["mode"], "SIMULATION")
        self.assertFalse(overview["live_feed"]["connected"])
        self.assertEqual(overview["pairs"], list(marketdata.PAIRS))
        self.assertEqual(self.call(viewer, "POST", "/api/market/ingest").status_code, 403)
        self.assertEqual(self.call(viewer, "POST", "/api/lab/backtests", json={}).status_code, 403)
        self.assertEqual(self.call(viewer, "POST", "/api/sim/sessions/1/stop").status_code, 403)

    def test_operator_requests_need_csrf(self):
        operator = self.session_client("ops", WebRole.OPERATOR)
        self.assertEqual(self.call(operator, "POST", "/api/market/ingest", csrf=False).status_code, 403)
        response = self.call(operator, "POST", "/api/market/ingest")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(JobStore(self.db).jobs()[0]["kind"], "ingest_ecb")

    def test_import_backtest_and_simulation_flow(self):
        operator = self.session_client("ops", WebRole.OPERATOR)
        bad = self.call(operator, "POST", "/api/market/import", data={
            "file": (io.BytesIO(b"timestamp,open,high,low,close\nx,1,1,1,1\n"), "bad.csv"),
            "symbol": "EURUSD", "timeframe": "H1", "source": "fixture"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("INVALID_TIMESTAMP", bad.get_json()["message"])
        good = self.call(operator, "POST", "/api/market/import", data={
            "file": (io.BytesIO(CSV), "eurusd.csv"), "symbol": "EURUSD", "timeframe": "H1", "source": "fixture"})
        self.assertEqual(good.status_code, 202, good.get_data(as_text=True))
        self.assertEqual(good.get_json()["preview"]["accepted"], 120)
        worker = Worker(self.db, Path(self.db).parent / "imports", ecb_enabled=False)
        worker.initialize()
        worker.tick()
        overview = operator.get("/api/market/overview").get_json()
        self.assertEqual(overview["coverage"][0]["candles"], 120)
        quality = operator.get("/api/market/quality?symbol=EURUSD&timeframe=H1&source=fixture").get_json()
        self.assertEqual(quality["gaps"], 0)
        self.assertEqual(len(operator.get("/api/market/candles?symbol=EURUSD&timeframe=H1&source=fixture&limit=50")
                             .get_json()["candles"]), 50)
        refused = self.call(operator, "POST", "/api/lab/backtests", json={"symbol": "EURUSD", "timeframe": "H1",
                                                                          "source": "fixture", "risk": {"risk_per_trade_pct": 9}})
        self.assertEqual(refused.status_code, 400)
        created = self.call(operator, "POST", "/api/lab/backtests", json={
            "symbol": "EURUSD", "timeframe": "H1", "source": "fixture",
            "strategy_params": {"fast": 3, "slow": 8, "atr_window": 3, "min_atr_pct": 0}})
        self.assertEqual(created.status_code, 202)
        worker.tick()
        detail = operator.get(f"/api/lab/backtests/{created.get_json()['backtest_id']}").get_json()
        self.assertEqual(detail["status"], "DONE")
        self.assertIn("equity", detail)
        session = self.call(operator, "POST", "/api/sim/sessions", json={
            "symbols": ["EURUSD"], "timeframe": "H1", "source": "fixture", "start": "2026-01-01"})
        self.assertEqual(session.status_code, 201)
        session_id = session.get_json()["session_id"]
        worker.tick()
        self.assertEqual(operator.get(f"/api/sim/sessions/{session_id}").get_json()["last_processed"]["EURUSD"],
                         (datetime(2026, 1, 5, tzinfo=UTC) + timedelta(hours=119)).isoformat())
        self.assertEqual(self.call(operator, "POST", f"/api/sim/sessions/{session_id}/stop").get_json()["status"], "STOPPED")
        self.assertEqual(self.call(operator, "POST", f"/api/sim/sessions/{session_id}/explode").status_code, 404)
        jobs = operator.get("/api/system/jobs").get_json()
        self.assertNotIn("path", jobs["jobs"][-1]["params"])  # internal file paths are not shown


if __name__ == "__main__":
    unittest.main()


class DemoRouteTests(WebAppCase):
    def call(self, client, path, csrf=True):
        headers = {"Origin": ORIGIN}
        if csrf:
            headers["X-CSRF-Token"] = self.page_csrf(client)
        return client.post(path, headers=headers)

    def test_demo_state_is_readable_and_requests_are_operator_only(self):
        from astra.demo_requests import DemoStore

        self.assertEqual(self.client().get("/api/demo/overview").status_code, 401)
        viewer = self.session_client("viewer", WebRole.READ_ONLY)
        overview = viewer.get("/api/demo/overview").get_json()
        self.assertEqual((overview["environment"], overview["live_locked"], overview["paused"]), ("DEMO", True, True))
        self.assertIsNone(overview["orders"])  # orders stay OPERATOR-only
        self.assertEqual(self.call(viewer, "/api/demo/resume").status_code, 403)
        operator = self.session_client("ops", WebRole.OPERATOR)
        self.assertEqual(self.call(operator, "/api/demo/resume", csrf=False).status_code, 403)
        self.assertEqual(self.call(operator, "/api/demo/resume").status_code, 202)
        self.assertEqual(self.call(operator, "/api/demo/controlled-test").status_code, 202)
        self.assertEqual(self.call(operator, "/api/demo/live").status_code, 404)
        kinds = [r["kind"] for r in DemoStore(self.db).requests()]
        self.assertEqual(sorted(kinds), ["controlled_test", "resume"])
        self.assertEqual(operator.get("/api/demo/overview").get_json()["orders"], [])
