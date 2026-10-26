"""Local SQLite persistence for reproducible simulation runs and audit events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import holdout_reuse
from .anchoring import BINDING_SCHEMA_SQL, BINDING_TABLE
from .attempts import SCHEMA_SQL as ATTEMPT_SCHEMA_SQL
from .attempts import logged_holdout_returns
from .models import BacktestResult
from .research_policy import HOLDOUT_REUSE_POLICY


def _reuse_findings(
    other_id: str, returns: list[float], other_returns: list[float], source: str = "an already sealed plan"
) -> list[dict]:
    assessment = holdout_reuse.assess_reuse(returns, other_returns)
    if assessment.verdict == holdout_reuse.CONFLICT:
        # Same price path relabelled, shifted, rescaled, inverted, reversed, resampled or nudged.
        raise ValueError(
            f"the holdout price path matches the holdout of {source} "
            f"({other_id[:12]}, |correlation| {assessment.max_abs_correlation}); a holdout can be scored only once"
        )
    if assessment.verdict == holdout_reuse.CLEAR:
        return []
    return [{"protocol_id": other_id, **assessment.payload()}]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


class BacktestStore:
    def __init__(self, database_path: str | Path, anchor: Any = None) -> None:
        """``anchor`` is an ``astra.anchoring.EvidenceAnchor``; by default ``ASTRA_EVIDENCE_ANCHOR`` (or none)."""

        self.path = Path(database_path)
        if anchor is None:
            from .anchoring import anchor_from_environment

            anchor = anchor_from_environment()
        self.anchor = anchor

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
                -- Log returns of each sealed holdout, kept out of the plan so they are never
                -- shown before scoring; used to refuse a holdout that reuses a sealed price path.
                CREATE TABLE IF NOT EXISTS holdout_signatures (
                    protocol_id TEXT PRIMARY KEY REFERENCES research_protocols(protocol_id),
                    returns_json TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS research_protocols_no_update BEFORE UPDATE ON research_protocols
                BEGIN SELECT RAISE(ABORT, 'sealed research protocols are append-only: UPDATE refused'); END;
                CREATE TRIGGER IF NOT EXISTS research_protocols_no_delete BEFORE DELETE ON research_protocols
                BEGIN SELECT RAISE(ABORT, 'sealed research protocols are append-only: DELETE refused'); END;
                CREATE TRIGGER IF NOT EXISTS holdout_signatures_no_update BEFORE UPDATE ON holdout_signatures
                BEGIN SELECT RAISE(ABORT, 'holdout signatures are append-only: UPDATE refused'); END;
                CREATE TRIGGER IF NOT EXISTS holdout_signatures_no_delete BEFORE DELETE ON holdout_signatures
                BEGIN SELECT RAISE(ABORT, 'holdout signatures are append-only: DELETE refused'); END;
                -- Verdict of the reuse detector for each sealed plan, under the policy recorded with it.
                CREATE TABLE IF NOT EXISTS holdout_reuse_checks (
                    protocol_id TEXT PRIMARY KEY REFERENCES research_protocols(protocol_id),
                    verdict TEXT NOT NULL,
                    findings_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS holdout_reuse_checks_no_update BEFORE UPDATE ON holdout_reuse_checks
                BEGIN SELECT RAISE(ABORT, 'holdout reuse checks are append-only: UPDATE refused'); END;
                CREATE TRIGGER IF NOT EXISTS holdout_reuse_checks_no_delete BEFORE DELETE ON holdout_reuse_checks
                BEGIN SELECT RAISE(ABORT, 'holdout reuse checks are append-only: DELETE refused'); END;
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
                CREATE TRIGGER IF NOT EXISTS holdout_evaluations_no_delete BEFORE DELETE ON holdout_evaluations
                BEGIN SELECT RAISE(ABORT, 'holdout evaluations are append-only: DELETE refused'); END;
                CREATE TRIGGER IF NOT EXISTS holdout_evaluations_final BEFORE UPDATE ON holdout_evaluations
                WHEN OLD.status <> 'STARTED'
                BEGIN SELECT RAISE(ABORT, 'a completed or failed holdout evaluation cannot be changed'); END;
                """
            )
            # Append-only, hash-chained attempt registry (see astra.attempts).
            db.executescript(ATTEMPT_SCHEMA_SQL)
            # Append-only record of the evidence anchor this store is bound to (see astra.anchoring).
            db.executescript(BINDING_SCHEMA_SQL)

    def seal_research_protocol(
        self,
        *,
        protocol_id: str,
        dataset_identity_sha256: str,
        protocol_version: str,
        plan_manifest: dict[str, Any],
        holdout_returns: Sequence[float] | None = None,
    ) -> None:
        """Store an immutable plan manifest before any holdout is scored.

        A new plan must carry ``holdout.content_sha256`` and the holdout's log
        returns (``holdout_returns``, which must hash to it). The plan is refused
        when its holdout shares a price path with any sealed holdout, whatever
        the label, dates, scale or quote direction (see ``astra.holdout_reuse``).
        Re-sealing the identical plan is idempotent and needs no returns.

        With an evidence anchor configured, a new plan is sealed only when the
        store verifies against the anchor; the first seal of an unused store
        binds it to the anchor, and every new seal is anchored (Lot 4, C-3).
        Holdouts recorded by the attempt log whose seal rows are gone are
        compared too (Lot 4, P-10).
        """

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

        returns = [float(value) for value in holdout_returns] if holdout_returns is not None else []
        new_plan = not self._protocol_exists(protocol_id)
        if new_plan and self.anchor is not None:
            # Refuses a restored store or a fresh anchor for a used store; binds an unused store on first use.
            self.require_anchor_consistency()
            if self.anchor.latest() is None:
                self.anchor_now()
        # The similarity search is slow (see astra.holdout_reuse), so it runs before the write lock;
        # plans sealed in between are compared again inside the lock.
        checked, findings = self._assess_against_sealed(protocol_id, dataset_identity_sha256, returns)
        now = _now()
        inserted = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            same = db.execute(
                "SELECT plan_json, dataset_sha256 FROM research_protocols WHERE protocol_id = ?", (protocol_id,)
            ).fetchone()
            if same is not None:
                if same["plan_json"] != plan_json:
                    raise ValueError("sealed protocol ID already exists with different content")
                if same["dataset_sha256"] != dataset_identity_sha256:
                    raise ValueError("sealed protocol ID is associated with a different market-bar identity")
                return
            content = holdout.get("content_sha256")
            if not content or holdout_returns is None:
                raise ValueError("a new plan must include holdout.content_sha256 and the holdout returns")
            if len(returns) < holdout_reuse.MIN_HOLDOUT_RETURNS:
                raise ValueError(
                    f"a holdout needs at least {holdout_reuse.MIN_HOLDOUT_RETURNS} returns to be checked for reuse"
                )
            if holdout_reuse.content_sha256(returns) != content:
                raise ValueError("holdout returns do not match holdout.content_sha256")
            if self.anchor is not None:
                from .anchoring import consumed_holdout_fingerprints

                # The external anchor survives a restore of an older database (Lot 4, B-8).
                if content in consumed_holdout_fingerprints(self.anchor):
                    raise ValueError(
                        "the evidence anchor records this holdout price path as already scored; "
                        "a holdout can be scored only once"
                    )
            # The attempt log outlives deleted seal rows: a holdout it records as scored stays spent (Lot 4, N-5).
            spent = db.execute(
                "SELECT protocol_id FROM research_attempt_events WHERE kind = 'holdout' "
                "AND holdout_content_sha256 = ? AND protocol_id <> ? LIMIT 1",
                (content, protocol_id),
            ).fetchone()
            if spent is not None:
                raise ValueError(
                    "the attempt log records this holdout price path as already scored "
                    f"({spent['protocol_id'][:12]}); a holdout can be scored only once"
                )
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
            for other in db.execute(
                """SELECT p.protocol_id, p.holdout_start, p.holdout_end, p.plan_json, s.returns_json
                   FROM research_protocols p LEFT JOIN holdout_signatures s ON s.protocol_id = p.protocol_id
                   WHERE p.dataset_sha256 <> ?""",
                (dataset_identity_sha256,),
            ).fetchall():
                other_plan = json.loads(other["plan_json"])
                other_returns = json.loads(other["returns_json"]) if other["returns_json"] else None
                if (other_plan.get("holdout") or {}).get("content_sha256") == content:
                    raise ValueError(
                        "the holdout price path matches the holdout of an already sealed plan "
                        f"({other['protocol_id'][:12]}); a holdout can be scored only once"
                    )
                if other_returns is None:
                    # Sealed before Lot 4: its path cannot be compared, so reuse cannot be ruled out.
                    findings.append({"protocol_id": other["protocol_id"], "verdict": holdout_reuse.UNDETERMINED,
                                     "reason": "sealed plan without stored holdout returns"})
                elif other["protocol_id"] not in checked:
                    findings.extend(_reuse_findings(other["protocol_id"], returns, other_returns))
                other_instrument = other_plan.get("instrument")
                if other_returns is not None and instrument and other_instrument and instrument != other_instrument:
                    continue  # another instrument's holdout is independent data
                # A plan sealed without stored returns (before Lot 4) is compared by period with every instrument.
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
            if inserted:
                db.execute(
                    "INSERT INTO holdout_signatures (protocol_id, returns_json) VALUES (?, ?)",
                    (protocol_id, json.dumps(returns, allow_nan=False, separators=(",", ":"))),
                )
                db.execute(
                    "INSERT INTO holdout_reuse_checks VALUES (?, ?, ?, ?, ?)",
                    (
                        protocol_id,
                        holdout_reuse.worst([item["verdict"] for item in findings]),
                        json.dumps(findings, sort_keys=True, separators=(",", ":")),
                        json.dumps(HOLDOUT_REUSE_POLICY.payload(), sort_keys=True, separators=(",", ":")),
                        now,
                    ),
                )
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
        if inserted and self.anchor is not None:
            self.anchor_now()

    def _protocol_exists(self, protocol_id: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM research_protocols WHERE protocol_id = ?", (protocol_id,)).fetchone() is not None

    def _assess_against_sealed(
        self, protocol_id: str, dataset_identity_sha256: str, returns: list[float]
    ) -> tuple[set, list[dict]]:
        """Compare ``returns`` with every sealed holdout's stored returns outside any write lock.

        Raises on a ``CONFLICT``; returns the compared protocol ids and the
        ``SUSPECT`` / ``UNDETERMINED`` findings, recorded with the new plan.
        Holdouts recorded with a holdout attempt in the hash-chained attempt log
        whose plan is no longer sealed (seal rows deleted) are compared as well.
        """

        if not returns:
            return set(), []
        with self._connect() as db:
            if db.execute("SELECT 1 FROM research_protocols WHERE protocol_id = ?", (protocol_id,)).fetchone():
                return set(), []  # idempotent re-seal: checked when it was first sealed
            rows = db.execute(
                """SELECT p.protocol_id, s.returns_json FROM research_protocols p
                   JOIN holdout_signatures s ON s.protocol_id = p.protocol_id
                   WHERE p.dataset_sha256 <> ? AND p.protocol_id <> ?""",
                (dataset_identity_sha256, protocol_id),
            ).fetchall()
            sealed = {str(row[0]) for row in db.execute("SELECT protocol_id FROM research_protocols")}
            logged = logged_holdout_returns(db)
        findings: list[dict] = []
        for row in rows:
            findings.extend(_reuse_findings(row["protocol_id"], returns, json.loads(row["returns_json"])))
        compared: set[str] = set()
        for other_id, other_returns in logged:
            if other_id in sealed or other_id == protocol_id or other_id in compared:
                continue
            compared.add(other_id)
            findings.extend(
                _reuse_findings(other_id, returns, other_returns, "a holdout already scored (attempt log)")
            )
        return {row["protocol_id"] for row in rows}, findings

    def reuse_check(self, protocol_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM holdout_reuse_checks WHERE protocol_id = ?", (protocol_id,)).fetchone()
        if row is None:
            return None
        return {
            "verdict": row["verdict"],
            "findings": json.loads(row["findings_json"]),
            "policy": json.loads(row["policy_json"]),
            "checked_at": row["checked_at"],
        }

    def require_anchor_consistency(self) -> None:
        """Refuse to score a holdout when the store disagrees with the evidence anchor (restore, rewrite)."""

        if self.anchor is None:
            return
        from .anchoring import verify_store_against_anchor

        verification = verify_store_against_anchor(self.path, self.anchor)
        errors = [code for code in verification.errors if code != "anchor_empty"]
        if errors:
            raise ValueError(f"the store is inconsistent with the evidence anchor: {', '.join(errors)}")

    def anchor_now(self) -> None:
        """Append a checkpoint of the store to the evidence anchor, when one is configured."""

        if self.anchor is not None:
            from .anchoring import anchor_store

            anchor_store(self.path, self.anchor)

    def anchor_holdout_start(self) -> dict[str, Any]:
        """Anchor the STARTED holdout attempt before any holdout number is computed (Lot 4, C-4).

        Returns the anchor record kept in the holdout report. Without an anchor the
        holdout leaves no external trace: it is marked as such and is never
        promotable (``holdout_scored_without_anchor``).
        """

        if self.anchor is None:
            return {
                "configured": False,
                "anchored_before_scoring": False,
                "promotable": False,
                "note": "scored without an evidence anchor: no external trace, permanently non-promotable",
            }
        from .anchoring import anchor_store

        checkpoint = anchor_store(self.path, self.anchor)
        return {
            "configured": True,
            "anchored_before_scoring": True,
            "kind": self.anchor.kind,
            "location": self.anchor.location,
            "production_grade": bool(self.anchor.production_grade),
            "anchor_id": checkpoint.anchor_id,
            "checkpoint_sequence": checkpoint.sequence,
        }

    def begin_holdout_evaluation(
        self, *, protocol_id: str, dataset_identity_sha256: str
    ) -> dict[str, Any]:
        """Burn the dataset's sole holdout attempt before calculation begins.

        A store bound to an evidence anchor is never scored without one (Lot 4 round 5, A4-D-2): a file
        copy carries the binding row, so the holdout cannot be peeked at in an unanchored copy and then
        scored "for the first time" in the anchored original.
        """

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.anchor is None:
                try:
                    bound = db.execute(f"SELECT anchor_id FROM {BINDING_TABLE} LIMIT 1").fetchone()
                except sqlite3.OperationalError:
                    bound = None  # created by initialize(); a store without the table was never anchored
                if bound is not None:
                    raise ValueError(
                        f"this store is bound to evidence anchor {bound['anchor_id']}; "
                        "a holdout can only be scored with that anchor configured"
                    )
            protocol = db.execute(
                "SELECT dataset_sha256 FROM research_protocols WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
            if protocol is None:
                raise ValueError("research protocol must be sealed before holdout evaluation")
            if protocol["dataset_sha256"] != dataset_identity_sha256:
                raise ValueError("market-bar identity does not match the sealed protocol")
            check = db.execute(
                "SELECT verdict FROM holdout_reuse_checks WHERE protocol_id = ?", (protocol_id,)
            ).fetchone()
            if check is None:
                signature = db.execute(
                    "SELECT 1 FROM holdout_signatures WHERE protocol_id = ?", (protocol_id,)
                ).fetchone()
                if signature is not None:
                    # Sealed with returns, so a verdict was written: a missing row was deleted (Lot 4, P-3).
                    raise ValueError(
                        "the holdout reuse verdict of this plan is missing; "
                        "score a new, independent holdout instead"
                    )
            elif check["verdict"] != holdout_reuse.CLEAR:
                # Scoring a holdout that may already be spent would leak it again (Lot 4, s52): only a new,
                # independent holdout clears the doubt.
                raise ValueError(
                    f"holdout reuse is {str(check['verdict']).lower()} for this plan; "
                    "score a new, independent holdout instead"
                )
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
    ) -> str:
        """Store the report of a STARTED attempt; returns the SHA-256 of the stored ``report_json``."""

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
        return hashlib.sha256(report_json.encode("utf-8")).hexdigest()

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
