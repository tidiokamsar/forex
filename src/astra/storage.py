"""Local SQLite persistence for reproducible simulation runs and audit events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import BacktestResult


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


class BacktestStore:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode = 'SIMULATION'),
                    dataset_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    dataset_sha256 TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    observations INTEGER NOT NULL,
                    trade_count INTEGER NOT NULL,
                    initial_equity REAL NOT NULL,
                    final_equity REAL NOT NULL,
                    gross_pnl REAL NOT NULL,
                    total_costs REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    max_drawdown_pct REAL NOT NULL,
                    win_rate REAL,
                    profit_factor REAL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    run_id TEXT REFERENCES backtest_runs(run_id),
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_protocols (
                    protocol_id TEXT PRIMARY KEY,
                    -- Legacy column name; new holdout records store canonical bar identity here.
                    dataset_sha256 TEXT NOT NULL,
                    protocol_version TEXT NOT NULL,
                    sealed_at TEXT NOT NULL,
                    holdout_start TEXT NOT NULL,
                    holdout_end TEXT NOT NULL,
                    plan_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS holdout_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL REFERENCES research_protocols(protocol_id),
                    -- Legacy column name; uniqueness is on canonical normalized bars.
                    dataset_sha256 TEXT NOT NULL UNIQUE,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('STARTED', 'COMPLETED', 'FAILED')),
                    report_json TEXT,
                    error_type TEXT,
                    CHECK (status <> 'COMPLETED' OR (completed_at IS NOT NULL AND report_json IS NOT NULL))
                );
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_created
                    ON backtest_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_events_occurred
                    ON audit_events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_protocols_dataset
                    ON research_protocols(dataset_sha256);
                """
            )

    def seal_research_protocol(
        self,
        *,
        protocol_id: str,
        dataset_identity_sha256: str,
        protocol_version: str,
        plan_manifest: dict[str, Any],
    ) -> None:
        """Store an immutable plan manifest before any holdout is scored."""

        plan_json = json.dumps(
            plan_manifest,
            default=_json_default,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        actual_id = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
        if actual_id != protocol_id:
            raise ValueError("protocol_id must be the SHA-256 of the canonical plan manifest")
        if plan_manifest.get("dataset_identity_sha256") != dataset_identity_sha256:
            raise ValueError("canonical dataset identity does not match the plan manifest")
        holdout = plan_manifest.get("holdout")
        if not isinstance(holdout, dict) or not holdout.get("start") or not holdout.get("end"):
            raise ValueError("protocol manifest must include final holdout boundaries")

        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing_for_dataset = db.execute(
                "SELECT protocol_id FROM research_protocols WHERE dataset_sha256 = ? LIMIT 1",
                (dataset_identity_sha256,),
            ).fetchone()
            if existing_for_dataset is not None and existing_for_dataset["protocol_id"] != protocol_id:
                raise ValueError(
                    "canonical market-bar identity is already sealed by another protocol; "
                    "use a fresh independent dataset"
                )
            # Extending or trimming a dataset changes its identity but not its holdout period:
            # refuse any other plan whose holdout overlaps an already sealed holdout of the same
            # instrument (plans without an instrument are compared conservatively with all).
            new_start = datetime.fromisoformat(str(holdout["start"]))
            new_end = datetime.fromisoformat(str(holdout["end"]))
            instrument = plan_manifest.get("instrument")
            content = holdout.get("content_sha256")
            for other in db.execute(
                "SELECT protocol_id, holdout_start, holdout_end, plan_json FROM research_protocols WHERE dataset_sha256 <> ?",
                (dataset_identity_sha256,),
            ).fetchall():
                other_plan = json.loads(other["plan_json"])
                if content and (other_plan.get("holdout") or {}).get("content_sha256") == content:
                    # Same price path relabelled, shifted in time or rescaled: the holdout is already spent.
                    raise ValueError(
                        "the holdout price path matches the holdout of an already sealed plan "
                        f"({other['protocol_id'][:12]}); a holdout can be scored only once"
                    )
                other_instrument = other_plan.get("instrument")
                if instrument and other_instrument and instrument != other_instrument:
                    continue  # another instrument's holdout is independent data
                other_start = datetime.fromisoformat(other["holdout_start"])
                other_end = datetime.fromisoformat(other["holdout_end"])
                if new_start <= other_end and other_start <= new_end:
                    raise ValueError(
                        "the holdout period overlaps the holdout of an already sealed plan "
                        f"({other['protocol_id'][:12]}); use data from a later, independent period"
                    )
            inserted = db.execute(
                """INSERT INTO research_protocols (
                       protocol_id, dataset_sha256, protocol_version, sealed_at,
                       holdout_start, holdout_end, plan_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(protocol_id) DO NOTHING""",
                (
                    protocol_id,
                    dataset_identity_sha256,
                    protocol_version,
                    now,
                    str(holdout["start"]),
                    str(holdout["end"]),
                    plan_json,
                ),
            ).rowcount == 1
            row = db.execute(
                "SELECT * FROM research_protocols WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
            if row is None or row["plan_json"] != plan_json:
                raise ValueError("sealed protocol ID already exists with different content")
            if row["dataset_sha256"] != dataset_identity_sha256:
                raise ValueError("sealed protocol ID is associated with a different market-bar identity")
            if inserted:
                db.execute(
                    """INSERT INTO audit_events
                       (event_id, occurred_at, event_type, run_id, details_json)
                       VALUES (?, ?, 'research.protocol_sealed', NULL, ?)""",
                    (
                        str(uuid.uuid4()),
                        now,
                        json.dumps(
                            {
                                "protocol_id": protocol_id,
                                "dataset_identity_sha256": dataset_identity_sha256,
                            },
                            sort_keys=True,
                        ),
                    ),
                )

    def begin_holdout_evaluation(
        self, *, protocol_id: str, dataset_identity_sha256: str
    ) -> dict[str, Any]:
        """Burn the dataset's sole holdout attempt before calculation begins."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            protocol = db.execute(
                "SELECT dataset_sha256 FROM research_protocols WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
            if protocol is None:
                raise ValueError("research protocol must be sealed before holdout evaluation")
            if protocol["dataset_sha256"] != dataset_identity_sha256:
                raise ValueError("market-bar identity does not match the sealed protocol")
            existing = db.execute(
                "SELECT * FROM holdout_evaluations WHERE dataset_sha256 = ?",
                (dataset_identity_sha256,),
            ).fetchone()
            if existing is not None:
                if existing["protocol_id"] != protocol_id:
                    raise ValueError(
                        "the final holdout for this dataset was reserved by another protocol"
                    )
                if existing["status"] == "COMPLETED":
                    return {
                        "evaluation_id": existing["evaluation_id"],
                        "status": existing["status"],
                        "report": json.loads(existing["report_json"]),
                    }
                raise ValueError(
                    "the holdout attempt is already consumed; STARTED or FAILED attempts cannot be rerun"
                )

            evaluation_id = str(uuid.uuid4())
            started_at = _now()
            db.execute(
                """INSERT INTO holdout_evaluations (
                    evaluation_id, protocol_id, dataset_sha256, started_at, status
                   ) VALUES (?, ?, ?, ?, 'STARTED')""",
                (evaluation_id, protocol_id, dataset_identity_sha256, started_at),
            )
            db.execute(
                """INSERT INTO audit_events
                   (event_id, occurred_at, event_type, run_id, details_json)
                   VALUES (?, ?, 'research.holdout_started', NULL, ?)""",
                (
                    str(uuid.uuid4()),
                    started_at,
                    json.dumps(
                        {
                            "protocol_id": protocol_id,
                            "dataset_identity_sha256": dataset_identity_sha256,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            return {
                "evaluation_id": evaluation_id,
                "status": "STARTED",
                "report": None,
            }

    def complete_holdout_evaluation(
        self, *, evaluation_id: str, report: dict[str, Any]
    ) -> None:
        completed_at = _now()
        report_json = json.dumps(
            report,
            default=_json_default,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as db:
            row = db.execute(
                "SELECT protocol_id, dataset_sha256, status FROM holdout_evaluations WHERE evaluation_id = ?",
                (evaluation_id,),
            ).fetchone()
            if row is None or row["status"] != "STARTED":
                raise ValueError("only a started holdout attempt can be completed")
            db.execute(
                """UPDATE holdout_evaluations
                   SET status = 'COMPLETED', completed_at = ?, report_json = ?
                   WHERE evaluation_id = ? AND status = 'STARTED'""",
                (completed_at, report_json, evaluation_id),
            )
            db.execute(
                """INSERT INTO audit_events
                   (event_id, occurred_at, event_type, run_id, details_json)
                   VALUES (?, ?, 'research.holdout_completed', NULL, ?)""",
                (
                    str(uuid.uuid4()),
                    completed_at,
                    json.dumps(
                        {
                            "evaluation_id": evaluation_id,
                            "protocol_id": row["protocol_id"],
                            "dataset_identity_sha256": row["dataset_sha256"],
                        },
                        sort_keys=True,
                    ),
                ),
            )

    def fail_holdout_evaluation(self, *, evaluation_id: str, error_type: str) -> None:
        failed_at = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT protocol_id, dataset_sha256 FROM holdout_evaluations WHERE evaluation_id = ? AND status = 'STARTED'",
                (evaluation_id,),
            ).fetchone()
            if row is None:
                return
            db.execute(
                """UPDATE holdout_evaluations
                   SET status = 'FAILED', completed_at = ?, error_type = ?
                   WHERE evaluation_id = ? AND status = 'STARTED'""",
                (failed_at, error_type, evaluation_id),
            )
            db.execute(
                """INSERT INTO audit_events
                   (event_id, occurred_at, event_type, run_id, details_json)
                   VALUES (?, ?, 'research.holdout_failed', NULL, ?)""",
                (
                    str(uuid.uuid4()),
                    failed_at,
                    json.dumps(
                        {
                            "evaluation_id": evaluation_id,
                            "protocol_id": row["protocol_id"],
                            "dataset_identity_sha256": row["dataset_sha256"],
                            "error_type": error_type,
                        },
                        sort_keys=True,
                    ),
                ),
            )

    def holdout_for_protocol(self, protocol_id: str) -> dict[str, Any] | None:
        """The holdout attempt reserved by a sealed plan, if any (id, status, timestamps)."""

        with self._connect() as db:
            row = db.execute(
                "SELECT evaluation_id, status, started_at, completed_at FROM holdout_evaluations WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_backtest(self, result: BacktestResult, dataset: Any) -> str:
        if not getattr(dataset, "usable", False):
            raise ValueError("Unusable datasets cannot be persisted as backtests")
        now = _now()
        run_id = str(uuid.uuid4())
        details = {
            "assumptions": result.assumptions,
            "risk_rejections": result.risk_rejections,
            "provenance": result.provenance,
        }
        source_type = str(dataset.source_type).upper()
        source_name = Path(str(dataset.source_name)).name
        with self._connect() as db:
            db.execute(
                """INSERT INTO backtest_runs (
                    run_id, created_at, mode, dataset_id, source_type, source_name,
                    dataset_sha256, instrument, strategy, strategy_version,
                    start_time, end_time, observations, trade_count,
                    initial_equity, final_equity, gross_pnl, total_costs, net_pnl,
                    max_drawdown_pct, win_rate, profit_factor, details_json
                ) VALUES (?, ?, 'SIMULATION', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    now,
                    str(dataset.dataset_id),
                    source_type,
                    source_name,
                    str(dataset.sha256),
                    result.instrument,
                    result.strategy,
                    result.strategy_version,
                    result.start.isoformat() if result.start else None,
                    result.end.isoformat() if result.end else None,
                    result.observations,
                    result.trade_count,
                    result.initial_equity,
                    result.final_equity,
                    result.gross_pnl,
                    result.total_costs,
                    result.net_pnl,
                    result.max_drawdown_pct,
                    result.win_rate,
                    result.profit_factor,
                    json.dumps(details, default=_json_default, sort_keys=True),
                ),
            )
            db.execute(
                """INSERT INTO audit_events
                   (event_id, occurred_at, event_type, run_id, details_json)
                   VALUES (?, ?, 'backtest.completed', ?, ?)""",
                (
                    str(uuid.uuid4()),
                    now,
                    run_id,
                    json.dumps(
                        {"mode": "SIMULATION", "dataset_sha256": str(dataset.sha256)},
                        sort_keys=True,
                    ),
                ),
            )
        return run_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    def latest_run(self) -> dict[str, Any] | None:
        runs = self.list_runs(limit=1)
        return runs[0] if runs else None

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM audit_events ORDER BY occurred_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def check_health(self) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1").fetchone()[0] == 1

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["details"] = json.loads(result.pop("details_json"))
        return result
