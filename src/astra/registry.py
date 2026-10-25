"""Experiment log and model/strategy registry with pre-registered promotion gates.

Promotion criteria are frozen (hashed) when a candidate is registered, before
any evaluation is recorded, so thresholds cannot be tuned after seeing the
final test. Every candidate, failure, promotion, rejection and rollback is
kept. Promotion requires a human actor and evidence that meets the criteria.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .governance import Action, Actor, PermissionGuard
from .storage import BacktestStore


class ModelStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    CHAMPION = "CHAMPION"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class PromotionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromotionCriteria:
    """Thresholds an out-of-sample evaluation must meet. Defaults are strict."""

    min_trades: int = 30
    min_net_pnl: float = 0.0
    min_profit_factor: float = 1.1
    max_drawdown_pct: float = 10.0
    min_edge_vs_baseline: float = 0.0
    require_cost_stress_non_negative: bool = True
    require_holdout: bool = True
    require_shadow: bool = False
    max_pbo: float = 0.3
    min_deflated_sharpe_probability: float = 0.95
    require_overfitting_check: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.min_trades, int) or self.min_trades < 1:
            raise ValueError("min_trades must be a positive integer")
        for name in ("min_net_pnl", "min_profit_factor", "max_drawdown_pct", "min_edge_vs_baseline"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        for name in ("max_pbo", "min_deflated_sharpe_probability"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a probability within [0, 1]")
        if not isinstance(self.require_overfitting_check, bool):
            raise ValueError("require_overfitting_check must be a bool")


PROVENANCE_KEY = "_provenance"
EVIDENCE_TOLERANCE = 1e-9

def criteria_looser_than_floor(criteria: PromotionCriteria) -> tuple[str, ...]:
    """Criteria may be stricter than the defaults, never looser. The floor is code, changed only by review."""

    floor = PromotionCriteria()
    looser = []
    for name in ("min_trades", "min_net_pnl", "min_profit_factor", "min_edge_vs_baseline", "min_deflated_sharpe_probability"):
        if getattr(criteria, name) < getattr(floor, name):
            looser.append(name)
    for name in ("max_drawdown_pct", "max_pbo"):
        if getattr(criteria, name) > getattr(floor, name):
            looser.append(name)
    for name in ("require_cost_stress_non_negative", "require_holdout", "require_overfitting_check"):
        if getattr(criteria, name) is not True:
            looser.append(name)
    return tuple(looser)


EVIDENCE_FIELDS = (
    "trade_count",
    "net_pnl",
    "profit_factor",
    "max_drawdown_pct",
    "edge_vs_baseline",
    "cost_stress_net_pnl",
    "holdout_used",
    "pbo",
    "deflated_sharpe_probability",
    "variants_tested",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class ModelRegistry:
    def __init__(
        self,
        database_path: str | Path,
        guard: PermissionGuard | None = None,
        evidence_verifier: Callable[[sqlite3.Connection, sqlite3.Row, dict[str, Any]], tuple[str, ...]] | None = None,
    ) -> None:
        self.path = Path(database_path)
        self._guard = guard or PermissionGuard(self)
        # Holdout evidence is never trusted as numbers: by default it must match
        # a completed, sealed holdout report stored in the same database.
        self._verify_evidence = evidence_verifier or verify_holdout_provenance

    def initialize(self) -> None:
        BacktestStore(self.path).initialize()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_versions (
                    model_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    criteria_json TEXT NOT NULL,
                    criteria_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    PRIMARY KEY (model_id, version)
                );
                CREATE TABLE IF NOT EXISTS model_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (model_id, version) REFERENCES model_versions(model_id, version)
                );
                CREATE TABLE IF NOT EXISTS model_status_events (
                    event_id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    author TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    mechanism TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    variants_tested INTEGER NOT NULL,
                    result_json TEXT,
                    decision TEXT,
                    decided_at TEXT
                );
                """
            )

    # -- experiments ----------------------------------------------------------

    def open_experiment(
        self,
        *,
        author: Actor,
        hypothesis: str,
        mechanism: str,
        dataset_id: str,
        plan: dict[str, Any],
        variants_tested: int,
    ) -> str:
        self._guard.require(author, Action.PROPOSE_RESEARCH, target="experiment")
        if not hypothesis.strip() or not mechanism.strip():
            raise ValueError("an experiment needs a falsifiable hypothesis and a mechanism")
        if not isinstance(variants_tested, int) or variants_tested < 1:
            raise ValueError("variants_tested must be a positive integer")
        experiment_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute(
                """INSERT INTO experiments (experiment_id, created_at, author, hypothesis, mechanism,
                       dataset_id, plan_json, variants_tested) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (experiment_id, _now(), author.actor_id, hypothesis, mechanism, dataset_id, _canonical(plan), variants_tested),
            )
            self._audit(db, "experiment.opened", {"experiment_id": experiment_id, "author": author.actor_id})
        return experiment_id

    def close_experiment(self, experiment_id: str, *, result: dict[str, Any], decision: str) -> None:
        """Record the outcome. Negative results are kept, never deleted."""

        if decision not in {"REJECTED", "NEEDS_MORE_RESEARCH", "SHADOW", "CANDIDATE", "OBSERVE"}:
            raise ValueError("unsupported experiment decision")
        with self._connect() as db:
            updated = db.execute(
                """UPDATE experiments SET result_json = ?, decision = ?, decided_at = ?
                   WHERE experiment_id = ? AND decision IS NULL""",
                (_canonical(result), decision, _now(), experiment_id),
            ).rowcount
            if updated != 1:
                raise ValueError("experiment does not exist or already has a decision")
            self._audit(db, "experiment.closed", {"experiment_id": experiment_id, "decision": decision})

    def experiments(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 200)),)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["plan"] = json.loads(item.pop("plan_json"))
            raw = item.pop("result_json")
            item["result"] = json.loads(raw) if raw else None
            result.append(item)
        return result

    # -- models -----------------------------------------------------------------

    def register_candidate(
        self,
        *,
        actor: Actor,
        model_id: str,
        version: str,
        manifest: dict[str, Any],
        criteria: PromotionCriteria,
    ) -> str:
        self._guard.require(actor, Action.REGISTER_CANDIDATE, target=f"{model_id}@{version}")
        required = ("dataset_identity", "feature_set_version", "code_sha256", "params")
        missing = [key for key in required if key not in manifest]
        if missing:
            raise ValueError(f"manifest is missing reproducibility fields: {missing}")
        looser = criteria_looser_than_floor(criteria)
        if looser:
            raise ValueError(f"criteria are looser than the policy floor: {', '.join(looser)}")
        manifest_sha = _sha256(manifest)
        criteria_payload = asdict(criteria)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO model_versions VALUES (?, ?, 'CANDIDATE', ?, ?, ?, ?, ?, ?)""",
                    (
                        model_id,
                        version,
                        _canonical(manifest),
                        manifest_sha,
                        _canonical(criteria_payload),
                        _sha256(criteria_payload),
                        _now(),
                        actor.actor_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("model version already registered; versions are immutable") from exc
            self._status_event(db, model_id, version, None, ModelStatus.CANDIDATE, actor.actor_id, "registered")
        return manifest_sha

    def record_evaluation(
        self,
        *,
        actor: Actor,
        model_id: str,
        version: str,
        kind: str,
        evidence: dict[str, Any],
        holdout_evaluation_id: str | None = None,
    ) -> str:
        self._guard.require(actor, Action.REGISTER_CANDIDATE, target=f"{model_id}@{version}:{kind}")
        if kind not in {"validation", "holdout", "shadow"}:
            raise ValueError("kind must be validation, holdout or shadow")
        unknown = set(evidence) - set(EVIDENCE_FIELDS)
        if unknown:
            raise ValueError(f"unknown evidence fields: {sorted(unknown)}")
        if kind == "holdout":
            if not isinstance(holdout_evaluation_id, str) or not holdout_evaluation_id:
                raise PromotionError("holdout evidence must reference the stored holdout evaluation it comes from")
            with self._connect() as db:
                for other in db.execute(
                    "SELECT model_id, version, evidence_json FROM model_evaluations WHERE kind = 'holdout'"
                ).fetchall():
                    reference = (json.loads(other["evidence_json"]).get(PROVENANCE_KEY) or {}).get("holdout_evaluation_id")
                    if reference == holdout_evaluation_id and (other["model_id"], other["version"]) != (model_id, version):
                        raise PromotionError(
                            f"holdout evaluation already backs {other['model_id']}@{other['version']}; one version per holdout"
                        )
            evidence = {**evidence, PROVENANCE_KEY: {"holdout_evaluation_id": holdout_evaluation_id}}
        evaluation_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._version(db, model_id, version)
            if kind == "holdout":
                prior = db.execute(
                    "SELECT 1 FROM model_evaluations WHERE model_id = ? AND version = ? AND kind = 'holdout'",
                    (model_id, version),
                ).fetchone()
                if prior is not None:
                    raise PromotionError("the holdout evaluation for this version is already recorded")
            if row["status"] not in (ModelStatus.CANDIDATE, ModelStatus.SHADOW):
                raise PromotionError("evaluations can only be added to candidate or shadow versions")
            db.execute(
                "INSERT INTO model_evaluations VALUES (?, ?, ?, ?, ?, ?)",
                (evaluation_id, model_id, version, kind, _canonical(evidence), _now()),
            )
            self._audit(db, "model.evaluation_recorded", {"model_id": model_id, "version": version, "kind": kind})
        return evaluation_id

    def check_promotion(self, model_id: str, version: str) -> tuple[str, ...]:
        """Return failed criteria; an empty tuple means the gates are met."""

        with self._connect() as db:
            row = self._version(db, model_id, version)
            evaluations = db.execute(
                "SELECT kind, evidence_json FROM model_evaluations WHERE model_id = ? AND version = ?",
                (model_id, version),
            ).fetchall()
        criteria_payload = json.loads(row["criteria_json"])
        failures: list[str] = []
        if _sha256(criteria_payload) != row["criteria_sha256"]:
            return ("criteria_tampered",)
        if _sha256(json.loads(row["manifest_json"])) != row["manifest_sha256"]:
            return ("manifest_tampered",)
        criteria = PromotionCriteria(**criteria_payload)
        by_kind = {item["kind"]: json.loads(item["evidence_json"]) for item in evaluations}
        decisive_kind = "holdout" if criteria.require_holdout else "validation"
        evidence = by_kind.get(decisive_kind)
        if evidence is None:
            return (f"missing_{decisive_kind}_evaluation",)
        if decisive_kind == "holdout":
            with self._connect() as db:
                failures.extend(self._verify_evidence(db, row, evidence))
        if criteria.require_shadow and "shadow" not in by_kind:
            failures.append("missing_shadow_evaluation")
        if row["status"] not in (ModelStatus.CANDIDATE, ModelStatus.SHADOW):
            failures.append("status_not_promotable")

        def number(key: str) -> float | None:
            value = evidence.get(key)
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            return float(value) if numeric and math.isfinite(value) else None

        trades = number("trade_count")
        if trades is None or trades < criteria.min_trades:
            failures.append("insufficient_trades")
        net = number("net_pnl")
        if net is None or net <= criteria.min_net_pnl:
            failures.append("net_pnl_not_positive_after_costs")
        pf = number("profit_factor")
        if pf is None or pf < criteria.min_profit_factor:
            failures.append("profit_factor_below_threshold")
        dd = number("max_drawdown_pct")
        if dd is None or dd > criteria.max_drawdown_pct:
            failures.append("drawdown_above_threshold")
        edge = number("edge_vs_baseline")
        if edge is None or edge <= criteria.min_edge_vs_baseline:
            failures.append("no_edge_vs_baseline")
        if criteria.require_cost_stress_non_negative:
            stress = number("cost_stress_net_pnl")
            if stress is None or stress < 0:
                failures.append("fails_cost_stress")
        if criteria.require_holdout and evidence.get("holdout_used") is not True:
            failures.append("holdout_not_confirmed")
        if criteria.require_overfitting_check:
            pbo = number("pbo")
            dsr = number("deflated_sharpe_probability")
            variants = number("variants_tested")
            if pbo is None or dsr is None or variants is None or variants < 1:
                failures.append("overfitting_check_missing")
            if pbo is not None and pbo > criteria.max_pbo:
                failures.append("pbo_above_threshold")
            if dsr is not None and dsr < criteria.min_deflated_sharpe_probability:
                failures.append("deflated_sharpe_below_threshold")
        return tuple(failures)

    def promote(self, *, actor: Actor, model_id: str, version: str, reason: str) -> None:
        self._guard.require(actor, Action.PROMOTE_MODEL, target=f"{model_id}@{version}")
        failures = self.check_promotion(model_id, version)
        if failures:
            with self._connect() as db:
                self._audit(
                    db,
                    "model.promotion_refused",
                    {"model_id": model_id, "version": version, "failures": list(failures), "actor": actor.actor_id},
                )
            raise PromotionError(f"promotion refused: {', '.join(failures)}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT version FROM model_versions WHERE model_id = ? AND status = 'CHAMPION'", (model_id,)
            ).fetchone()
            if current is not None:
                self._set_status(
                    db, model_id, current["version"], ModelStatus.RETIRED, actor.actor_id, f"superseded by {version}"
                )
            self._set_status(db, model_id, version, ModelStatus.CHAMPION, actor.actor_id, reason)

    def start_shadow(self, *, actor: Actor, model_id: str, version: str) -> None:
        self._guard.require(actor, Action.REGISTER_CANDIDATE, target=f"{model_id}@{version}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._version(db, model_id, version)
            if row["status"] != ModelStatus.CANDIDATE:
                raise PromotionError("only candidates can enter shadow mode")
            self._set_status(db, model_id, version, ModelStatus.SHADOW, actor.actor_id, "shadow observation, no orders")

    def reject(self, *, actor: Actor, model_id: str, version: str, reason: str) -> None:
        self._guard.require(actor, Action.REGISTER_CANDIDATE, target=f"{model_id}@{version}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._version(db, model_id, version)
            if row["status"] not in (ModelStatus.CANDIDATE, ModelStatus.SHADOW):
                raise PromotionError("only candidate or shadow versions can be rejected")
            self._set_status(db, model_id, version, ModelStatus.REJECTED, actor.actor_id, reason)

    def rollback(self, *, actor: Actor, model_id: str, reason: str) -> str:
        """Restore the most recently retired champion and verify its manifest."""

        self._guard.require(actor, Action.ROLLBACK_MODEL, target=model_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT version FROM model_versions WHERE model_id = ? AND status = 'CHAMPION'", (model_id,)
            ).fetchone()
            previous = db.execute(
                """SELECT e.version FROM model_status_events e
                   JOIN model_versions v ON v.model_id = e.model_id AND v.version = e.version
                   WHERE e.model_id = ? AND e.to_status = 'CHAMPION' AND v.status = 'RETIRED'
                   ORDER BY e.occurred_at DESC, e.rowid DESC LIMIT 1""",
                (model_id,),
            ).fetchone()
            if previous is None:
                raise PromotionError("no previously promoted version is available for rollback")
            target = self._version(db, model_id, previous["version"])
            if _sha256(json.loads(target["manifest_json"])) != target["manifest_sha256"]:
                raise PromotionError("rollback target manifest failed integrity verification")
            if current is not None:
                self._set_status(db, model_id, current["version"], ModelStatus.RETIRED, actor.actor_id, f"rolled back: {reason}")
            self._set_status(db, model_id, previous["version"], ModelStatus.CHAMPION, actor.actor_id, f"rollback: {reason}")
            return previous["version"]

    def champion(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_versions WHERE model_id = ? AND status = 'CHAMPION'", (model_id,)
            ).fetchone()
        return _version_dict(row) if row else None

    def get(self, model_id: str, version: str) -> dict[str, Any]:
        with self._connect() as db:
            return _version_dict(self._version(db, model_id, version))

    def list_versions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM model_versions ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 500)),)
            ).fetchall()
        return [_version_dict(row) for row in rows]

    def history(self, model_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM model_status_events WHERE model_id = ? ORDER BY occurred_at, rowid", (model_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # -- audit sink and internals -------------------------------------------------

    def audit(self, event_type: str, details: dict[str, Any]) -> None:
        with self._connect() as db:
            self._audit(db, event_type, details)

    @staticmethod
    def _version(db: sqlite3.Connection, model_id: str, version: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM model_versions WHERE model_id = ? AND version = ?", (model_id, version)
        ).fetchone()
        if row is None:
            raise KeyError(f"{model_id}@{version}")
        return row

    def _set_status(self, db, model_id: str, version: str, status: ModelStatus, actor: str, reason: str) -> None:
        row = self._version(db, model_id, version)
        db.execute(
            "UPDATE model_versions SET status = ? WHERE model_id = ? AND version = ?",
            (status.value, model_id, version),
        )
        self._status_event(db, model_id, version, ModelStatus(row["status"]), status, actor, reason)

    def _status_event(self, db, model_id, version, from_status, to_status, actor, reason) -> None:
        db.execute(
            "INSERT INTO model_status_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                model_id,
                version,
                from_status.value if from_status else None,
                to_status.value,
                actor,
                reason,
                _now(),
            ),
        )
        self._audit(
            db,
            "model.status_changed",
            {
                "model_id": model_id,
                "version": version,
                "from": from_status.value if from_status else None,
                "to": to_status.value,
                "actor": actor,
                "reason": reason,
            },
        )

    @staticmethod
    def _audit(db, event_type: str, details: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO audit_events (event_id, occurred_at, event_type, run_id, details_json) VALUES (?, ?, ?, NULL, ?)",
            (str(uuid.uuid4()), _now(), event_type, _canonical(details)),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            if db.in_transaction:
                db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()


def verify_holdout_provenance(db: sqlite3.Connection, version_row: sqlite3.Row, evidence: dict[str, Any]) -> tuple[str, ...]:
    """Check that holdout evidence comes from a sealed, completed holdout of this very candidate.

    The evidence must reference a COMPLETED holdout evaluation whose protocol is
    the plan in the version's manifest, whose selected candidate is the
    manifest's candidate, which started after the version (and therefore its
    criteria) was registered, and whose recomputed evidence equals the recorded
    numbers. Anyone able to rewrite the SQLite file could still forge both; this
    stops fabricated or reused evidence through the API.
    """

    provenance = evidence.get(PROVENANCE_KEY) or {}
    evaluation_id = provenance.get("holdout_evaluation_id")
    if not evaluation_id:
        return ("evidence_provenance_missing",)
    manifest = json.loads(version_row["manifest_json"])
    try:
        holdout = db.execute(
            "SELECT protocol_id, started_at, status, report_json FROM holdout_evaluations WHERE evaluation_id = ?",
            (evaluation_id,),
        ).fetchone()
    except sqlite3.Error:
        holdout = None
    if holdout is None or holdout["status"] != "COMPLETED" or not holdout["report_json"]:
        return ("holdout_evaluation_not_found_or_incomplete",)
    failures = []
    if holdout["protocol_id"] != manifest.get("plan_sha256"):
        failures.append("holdout_from_another_plan")
    report = json.loads(holdout["report_json"])
    if report.get("selected_candidate_id") != manifest.get("candidate_id"):
        failures.append("holdout_selected_another_candidate")
    if holdout["started_at"] <= version_row["created_at"]:
        failures.append("criteria_registered_after_holdout_was_scored")
    if report.get("selected_config") != manifest.get("params"):
        failures.append("manifest_params_differ_from_evaluated_candidate")
    plan_row = db.execute(
        "SELECT plan_json FROM research_protocols WHERE protocol_id = ?", (holdout["protocol_id"],)
    ).fetchone()
    plan = json.loads(plan_row["plan_json"]) if plan_row else {}
    if plan.get("implementation_sha256") != manifest.get("code_sha256"):
        failures.append("manifest_code_differs_from_sealed_plan")
    identity = manifest.get("dataset_identity")
    canonical = identity.get("canonical_sha256") if isinstance(identity, dict) else None
    if canonical is None or canonical != plan.get("dataset_identity_sha256"):
        failures.append("manifest_dataset_differs_from_sealed_plan")
    from .strategy_validation import holdout_evidence

    expected = holdout_evidence(report)
    for key in EVIDENCE_FIELDS:
        recorded, derived = evidence.get(key), expected.get(key)
        if isinstance(recorded, (int, float)) and isinstance(derived, (int, float)) and not isinstance(recorded, bool):
            if not math.isclose(float(recorded), float(derived), rel_tol=EVIDENCE_TOLERANCE, abs_tol=EVIDENCE_TOLERANCE):
                failures.append("evidence_does_not_match_holdout_report")
                break
        elif recorded != derived:
            failures.append("evidence_does_not_match_holdout_report")
            break
    return tuple(failures)


def _version_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["manifest"] = json.loads(item.pop("manifest_json"))
    item["criteria"] = json.loads(item.pop("criteria_json"))
    return item
