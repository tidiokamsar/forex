import hashlib
import json
import sqlite3
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from _support import workspace_tempdir
from astra.models import BacktestResult
from astra.storage import BacktestStore


@dataclass
class UsableDataset:
    usable: bool = True
    dataset_id: str = "ds-1"
    source_type: str = "SYNTHETIC"
    source_name: str = "synthetic.csv"
    sha256: str = "a" * 64


def result():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    return BacktestResult(
        strategy="sma-crossover",
        strategy_version="1",
        instrument="EURUSD",
        start=start,
        end=end,
        initial_equity=10_000.0,
        final_equity=10_001.0,
        trade_count=1,
        gross_pnl=2.0,
        total_costs=1.0,
        net_pnl=1.0,
        max_drawdown_pct=0.0,
        win_rate=1.0,
        profit_factor=None,
        trades=(),
        risk_rejections=(),
        assumptions=("Synthetic test result",),
        observations=10,
        provenance={"fixture": True},
    )


class BacktestStoreTests(unittest.TestCase):
    def test_save_run_and_audit_event_are_persisted_together(self):
        with workspace_tempdir() as temp:
            store = BacktestStore(Path(temp) / "nested" / "astra.sqlite3")
            store.initialize()
            run_id = store.save_backtest(result(), UsableDataset())
            self.assertTrue(store.check_health())
            latest = store.latest_run()
            self.assertEqual(latest["run_id"], run_id)
            self.assertEqual(latest["mode"], "SIMULATION")
            self.assertEqual(latest["dataset_sha256"], "a" * 64)
            self.assertEqual(latest["details"]["assumptions"], ["Synthetic test result"])
            self.assertEqual(store.audit_events()[0]["event_type"], "backtest.completed")

    def test_unusable_dataset_cannot_be_persisted(self):
        with workspace_tempdir() as temp:
            store = BacktestStore(Path(temp) / "astra.sqlite3")
            store.initialize()
            with self.assertRaisesRegex(ValueError, "Unusable"):
                store.save_backtest(result(), UsableDataset(usable=False))

    def test_protocol_seal_burns_failed_holdout_attempt_before_calculation(self):
        with workspace_tempdir() as temp:
            store = BacktestStore(Path(temp) / "state.sqlite3")
            store.initialize()
            identity = "c" * 64
            manifest = {
                "dataset_sha256": "a" * 64,
                "dataset_identity_sha256": identity,
                "holdout": {
                    "start": "2024-01-01T00:00:00+00:00",
                    "end": "2024-01-02T00:00:00+00:00",
                },
                "protocol": {"version": "1.0.0"},
            }
            plan_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            protocol_id = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
            store.seal_research_protocol(
                protocol_id=protocol_id,
                dataset_identity_sha256=identity,
                protocol_version="1.0.0",
                plan_manifest=manifest,
            )
            store.seal_research_protocol(
                protocol_id=protocol_id,
                dataset_identity_sha256=identity,
                protocol_version="1.0.0",
                plan_manifest=manifest,
            )
            attempt = store.begin_holdout_evaluation(
                protocol_id=protocol_id, dataset_identity_sha256=identity
            )
            self.assertEqual(attempt["status"], "STARTED")
            store.fail_holdout_evaluation(
                evaluation_id=attempt["evaluation_id"], error_type="ArithmeticError"
            )

            with self.assertRaisesRegex(ValueError, "already consumed"):
                store.begin_holdout_evaluation(
                    protocol_id=protocol_id, dataset_identity_sha256=identity
                )
            db = sqlite3.connect(store.path)
            try:
                row = db.execute(
                    "SELECT status, error_type FROM holdout_evaluations WHERE dataset_sha256 = ?",
                    (identity,),
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(row, ("FAILED", "ArithmeticError"))

    def test_completed_holdout_report_is_idempotently_read_and_dataset_is_unique(self):
        with workspace_tempdir() as temp:
            store = BacktestStore(Path(temp) / "state.sqlite3")
            store.initialize()
            dataset_hash = "b" * 64
            identity = "d" * 64
            manifest = {
                "dataset_sha256": dataset_hash,
                "dataset_identity_sha256": identity,
                "holdout": {
                    "start": "2024-01-01T00:00:00+00:00",
                    "end": "2024-01-02T00:00:00+00:00",
                },
                "protocol": {"version": "1.0.0"},
            }
            protocol_id = hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            store.seal_research_protocol(
                protocol_id=protocol_id,
                dataset_identity_sha256=identity,
                protocol_version="1.0.0",
                plan_manifest=manifest,
            )
            attempt = store.begin_holdout_evaluation(
                protocol_id=protocol_id, dataset_identity_sha256=identity
            )
            stored_report = {"status": "COMPLETED", "net_pnl": 12.5}
            store.complete_holdout_evaluation(
                evaluation_id=attempt["evaluation_id"], report=stored_report
            )
            replay = store.begin_holdout_evaluation(
                protocol_id=protocol_id, dataset_identity_sha256=identity
            )
            self.assertEqual(replay["status"], "COMPLETED")
            self.assertEqual(replay["report"], stored_report)

            alternative_manifest = {
                "dataset_sha256": "e" * 64,
                "dataset_identity_sha256": identity,
                "holdout": {
                    "start": "2024-01-01T12:00:00+00:00",
                    "end": "2024-01-02T00:00:00+00:00",
                },
                "protocol": {"version": "1.0.0"},
            }
            alternative_id = hashlib.sha256(
                json.dumps(
                    alternative_manifest, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            with self.assertRaisesRegex(ValueError, "already sealed by another protocol"):
                store.seal_research_protocol(
                    protocol_id=alternative_id,
                    dataset_identity_sha256=identity,
                    protocol_version="1.0.0",
                    plan_manifest=alternative_manifest,
                )

    def test_initialize_preserves_rows_in_a_legacy_backtest_database(self):
        with workspace_tempdir() as temp:
            database_path = Path(temp) / "legacy.sqlite3"
            db = sqlite3.connect(database_path)
            try:
                db.executescript(
                    """
                    CREATE TABLE backtest_runs (
                        run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                        mode TEXT NOT NULL, dataset_id TEXT NOT NULL,
                        source_type TEXT NOT NULL, source_name TEXT NOT NULL,
                        dataset_sha256 TEXT NOT NULL, instrument TEXT NOT NULL,
                        strategy TEXT NOT NULL, strategy_version TEXT NOT NULL,
                        start_time TEXT, end_time TEXT, observations INTEGER NOT NULL,
                        trade_count INTEGER NOT NULL, initial_equity REAL NOT NULL,
                        final_equity REAL NOT NULL, gross_pnl REAL NOT NULL,
                        total_costs REAL NOT NULL, net_pnl REAL NOT NULL,
                        max_drawdown_pct REAL NOT NULL, win_rate REAL,
                        profit_factor REAL, details_json TEXT NOT NULL
                    );
                    CREATE TABLE audit_events (
                        event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        run_id TEXT REFERENCES backtest_runs(run_id),
                        details_json TEXT NOT NULL
                    );
                    INSERT INTO backtest_runs (
                        run_id, created_at, mode, dataset_id, source_type, source_name,
                        dataset_sha256, instrument, strategy, strategy_version,
                        observations, trade_count, initial_equity, final_equity,
                        gross_pnl, total_costs, net_pnl, max_drawdown_pct, details_json
                    ) VALUES (
                        'legacy-run', '2026-01-01T00:00:00+00:00', 'SIMULATION',
                        'ds-old', 'SYNTHETIC', 'old.csv', 'f', 'EURUSD',
                        'sma_crossover', '1.0.0', 10, 0, 10000, 10000,
                        0, 0, 0, 0, '{}'
                    );
                    INSERT INTO audit_events (
                        event_id, occurred_at, event_type, run_id, details_json
                    ) VALUES (
                        'legacy-audit', '2026-01-01T00:00:00+00:00',
                        'backtest.completed', 'legacy-run', '{}'
                    );
                    """
                )
            finally:
                db.close()

            store = BacktestStore(database_path)
            store.initialize()
            self.assertEqual(store.latest_run()["run_id"], "legacy-run")
            self.assertEqual(store.audit_events()[0]["event_id"], "legacy-audit")
            db = sqlite3.connect(database_path)
            try:
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                preserved = db.execute(
                    "SELECT run_id FROM backtest_runs WHERE run_id = 'legacy-run'"
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(preserved, ("legacy-run",))
            self.assertIn("research_protocols", tables)
            self.assertIn("holdout_evaluations", tables)

    def test_holdout_lock_is_scoped_to_each_configured_store(self):
        with workspace_tempdir() as temp:
            identity = "9" * 64
            manifest = {
                "dataset_sha256": "8" * 64,
                "dataset_identity_sha256": identity,
                "holdout": {
                    "start": "2024-01-01T00:00:00+00:00",
                    "end": "2024-01-02T00:00:00+00:00",
                },
                "protocol": {"version": "1.0.0"},
            }
            protocol_id = hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            attempts = []
            for name in ("first.sqlite3", "fresh.sqlite3"):
                store = BacktestStore(Path(temp) / name)
                store.initialize()
                store.seal_research_protocol(
                    protocol_id=protocol_id,
                    dataset_identity_sha256=identity,
                    protocol_version="1.0.0",
                    plan_manifest=manifest,
                )
                attempts.append(
                    store.begin_holdout_evaluation(
                        protocol_id=protocol_id,
                        dataset_identity_sha256=identity,
                    )
                )

            self.assertEqual([attempt["status"] for attempt in attempts], ["STARTED", "STARTED"])


if __name__ == "__main__":
    unittest.main()
