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

from .attempts import (
    AttemptLog,
    holdout_attempt_records,
    promotion_trial_failures,
    scope_for_plan,
    trial_statistics_before,
)
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
# Failures meaning the stored overfitting statistics are not confirmed by the hash-chained holdout
# attempt and a re-derivation from the bars (Lot 4, C-1/P-1/P-4): the overfitting check counts as missing.
OVERFITTING_UNVERIFIED_FAILURES = frozenset(
    {
        "holdout_attempt_record_missing",
        "holdout_attempt_ambiguous",
        "holdout_attempt_record_incomplete",
        "holdout_attempt_for_another_evaluation",
        "holdout_report_hash_mismatch",
        "holdout_trial_universe_differs_from_attempt_log",
        "holdout_overfitting_differs_from_rederivation",
        "statistical_policy_below_floor",
    }
)

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
        anchor: Any = None,
    ) -> None:
        self.path = Path(database_path)
        # Evidence anchor outside SQLite (astra.anchoring); by default ASTRA_EVIDENCE_ANCHOR, else none,
        # in which case holdout promotion fails closed (evidence_anchor_missing).
        if anchor is None:
            from .anchoring import anchor_from_environment

            anchor = anchor_from_environment()
        self.anchor = anchor
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
                -- Evidence and the experiment log are append-only through SQLite (Lot 4). The file
                -- owner can still drop these triggers; see SECURITY.md.
                CREATE TRIGGER IF NOT EXISTS model_evaluations_no_update BEFORE UPDATE ON model_evaluations
                BEGIN SELECT RAISE(ABORT, 'model evaluations are append-only: UPDATE refused'); END;
                CREATE TRIGGER IF NOT EXISTS model_evaluations_no_delete BEFORE DELETE ON model_evaluations
                BEGIN SELECT RAISE(ABORT, 'model evaluations are append-only: DELETE refused'); END;
                CREATE TRIGGER IF NOT EXISTS experiments_no_delete BEFORE DELETE ON experiments
                BEGIN SELECT RAISE(ABORT, 'experiments are append-only: DELETE refused'); END;
                CREATE TRIGGER IF NOT EXISTS experiments_decided_once BEFORE UPDATE ON experiments
                WHEN OLD.decision IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'a decided experiment cannot be changed'); END;
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
            evidence = {
                **evidence,
                PROVENANCE_KEY: {"holdout_evaluation_id": holdout_evaluation_id, **self._trial_snapshot(model_id, version)},
            }
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

    def check_promotion(
        self,
        model_id: str,
        version: str,
        *,
        holdout_bars: Any = None,
        allow_non_production_anchor: bool = False,
    ) -> tuple[str, ...]:
        """Return failed criteria; an empty tuple means the gates are met.

        With the default verifier, holdout evidence is re-derived from
        ``holdout_bars`` (the sealed plan's market bars); without them the gate
        fails with ``holdout_not_rederived_from_market_data``. The PBO, deflated
        Sharpe and trial count are re-derived too, with the trial universe the
        hash-chained holdout attempt recorded.

        ``allow_non_production_anchor`` reports the reviewer's waiver state: when
        False (the default) an evidence anchor that is not production grade (the
        shipped ``LocalFileAnchor``) fails with ``evidence_anchor_not_production_grade``;
        only a human reviewer may pass True, through ``promote``, which records it.
        """

        if not isinstance(allow_non_production_anchor, bool):
            raise ValueError("allow_non_production_anchor must be a bool")

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
        # Re-applied at promotion, so rows registered before the floor existed cannot use lax criteria.
        looser = criteria_looser_than_floor(criteria)
        if looser:
            return tuple(f"criteria_below_policy_floor:{name}" for name in looser)
        by_kind = {item["kind"]: json.loads(item["evidence_json"]) for item in evaluations}
        decisive_kind = "holdout" if criteria.require_holdout else "validation"
        evidence = by_kind.get(decisive_kind)
        if evidence is None:
            return (f"missing_{decisive_kind}_evaluation",)
        if decisive_kind == "holdout":
            with self._connect() as db:
                if self._verify_evidence is verify_holdout_provenance:
                    failures.extend(
                        verify_holdout_provenance(db, row, evidence, holdout_bars=holdout_bars, path=self.path)
                    )
                    failures.extend(
                        _anchor_and_reuse_failures(
                            db, self.path, self.anchor, evidence, allow_non_production_anchor=allow_non_production_anchor
                        )
                    )
                else:
                    failures.extend(self._verify_evidence(db, row, evidence))
                # One version per holdout, re-checked here: evidence copied to a sibling row bypasses
                # record_evaluation (Lot 4 QA, D-9).
                # The first version recorded against a holdout owns it.
                reference = (evidence.get(PROVENANCE_KEY) or {}).get("holdout_evaluation_id")
                backers = sorted(
                    (other["recorded_at"], other["evaluation_id"], other["model_id"], other["version"])
                    for other in db.execute(
                        "SELECT evaluation_id, model_id, version, evidence_json, recorded_at "
                        "FROM model_evaluations WHERE kind = 'holdout'"
                    ).fetchall()
                    if reference
                    and (json.loads(other["evidence_json"]).get(PROVENANCE_KEY) or {}).get("holdout_evaluation_id")
                    == reference
                )
                if backers and backers[0][2:] != (model_id, version):
                    failures.append("holdout_backs_another_version")
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
            unverified = bool(OVERFITTING_UNVERIFIED_FAILURES.intersection(failures))
            if pbo is None or dsr is None or variants is None or variants < 1 or unverified:
                failures.append("overfitting_check_missing")
            # The trial count must cover every relevant attempt in the store, not one plan's grid,
            # and the append-only attempt log must be intact.
            with self._connect() as db:
                anchor = (evidence.get(PROVENANCE_KEY) or {}).get("attempt_chain_head_at_record")
                failures.extend(promotion_trial_failures(db, json.loads(row["manifest_json"]), variants, anchor))
            if pbo is not None and pbo > criteria.max_pbo:
                failures.append("pbo_above_threshold")
            if dsr is not None and dsr < criteria.min_deflated_sharpe_probability:
                failures.append("deflated_sharpe_below_threshold")
        return tuple(failures)

    def promote(
        self,
        *,
        actor: Actor,
        model_id: str,
        version: str,
        reason: str,
        holdout_bars: Any = None,
        allow_non_production_anchor: bool = False,
    ) -> None:
        """Promote to CHAMPION when every gate is met.

        ``allow_non_production_anchor=True`` is the human reviewer's explicit
        acceptance of an evidence anchor that is not production grade (Lot 4,
        C-3); it is recorded in the audit event either way.
        """

        self._guard.require(actor, Action.PROMOTE_MODEL, target=f"{model_id}@{version}")
        if not isinstance(allow_non_production_anchor, bool):
            raise ValueError("allow_non_production_anchor must be a bool")
        failures = self.check_promotion(
            model_id, version, holdout_bars=holdout_bars, allow_non_production_anchor=allow_non_production_anchor
        )
        anchor_state = {
            "allow_non_production_anchor": allow_non_production_anchor,
            "evidence_anchor_kind": getattr(self.anchor, "kind", None),
            "evidence_anchor_production_grade": bool(getattr(self.anchor, "production_grade", False)),
        }
        if failures:
            with self._connect() as db:
                self._audit(
                    db,
                    "model.promotion_refused",
                    {
                        "model_id": model_id,
                        "version": version,
                        "failures": list(failures),
                        "actor": actor.actor_id,
                        **anchor_state,
                    },
                )
            raise PromotionError(f"promotion refused: {', '.join(failures)}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._audit(
                db,
                "model.promotion_approved",
                {"model_id": model_id, "version": version, "actor": actor.actor_id, **anchor_state},
            )
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

    def _trial_snapshot(self, model_id: str, version: str) -> dict[str, Any]:
        """Store-wide trial count and attempt-chain head when holdout evidence is recorded (informative)."""

        with self._connect() as db:
            manifest = json.loads(self._version(db, model_id, version)["manifest_json"])
            plan_row = db.execute(
                "SELECT plan_json FROM research_protocols WHERE protocol_id = ?", (manifest.get("plan_sha256"),)
            ).fetchone()
        log = AttemptLog(self.path)
        stats = log.trial_statistics(scope_for_plan(json.loads(plan_row["plan_json"]) if plan_row else None))
        return {"store_trial_count_at_record": stats.count, "attempt_chain_head_at_record": log.chain_head()["row_hash"]}

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


def verify_holdout_provenance(
    db: sqlite3.Connection,
    version_row: sqlite3.Row,
    evidence: dict[str, Any],
    *,
    holdout_bars: Any = None,
    path: str | Path | None = None,
) -> tuple[str, ...]:
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
        "SELECT plan_json, dataset_sha256 FROM research_protocols WHERE protocol_id = ?", (holdout["protocol_id"],)
    ).fetchone()
    plan = json.loads(plan_row["plan_json"]) if plan_row else {}
    if plan.get("implementation_sha256") != manifest.get("code_sha256"):
        failures.append("manifest_code_differs_from_sealed_plan")
    failures.extend(_report_binding_failures(db, holdout["protocol_id"], plan, plan_row, report, manifest))
    failures.extend(
        _rederivation_failures(
            plan, report, holdout_bars, db=db, path=path, protocol_id=holdout["protocol_id"],
            report_json=holdout["report_json"], evaluation_id=evaluation_id,
        )
    )
    from .research_policy import statistical_policy_below_floor

    # The floors applied when the statistics were computed must not be below the reviewed policy (Lot 4, P-4).
    overfitting = report.get("overfitting") if isinstance(report.get("overfitting"), dict) else {}
    policy = overfitting.get("statistical_policy") if isinstance(overfitting.get("statistical_policy"), dict) else {}
    if statistical_policy_below_floor(policy.get("applied")):
        failures.append("statistical_policy_below_floor")
    # A holdout scored without an evidence anchor left no external trace: never promotable (Lot 4, C-4).
    anchor_record = report.get("evidence_anchor") if isinstance(report.get("evidence_anchor"), dict) else {}
    if anchor_record.get("anchored_before_scoring") is not True:
        failures.append("holdout_scored_without_anchor")
    # Data provenance (directive V5, Lot 4 round 5): read from the sealed plan only, never from a caller.
    # Synthetic or simulated bars can never back a promotion; a missing or unknown label fails closed.
    from .strategy_validation import AUTHENTIC_DATA_SOURCE_TYPES, NON_AUTHENTIC_DATA_SOURCE_TYPES, plan_data_source_type

    source_type = plan_data_source_type(plan)
    if source_type in NON_AUTHENTIC_DATA_SOURCE_TYPES:
        failures.append(f"dataset_not_authentic:{source_type}")
    elif source_type not in AUTHENTIC_DATA_SOURCE_TYPES:
        failures.append("dataset_provenance_unknown")
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


def _current_implementation_sha256(plan: dict[str, Any]) -> str:
    if "strategy_validation_version" in plan:
        from .strategy_validation import _implementation_sha256
    else:
        from .validation import _implementation_sha256
    return _implementation_sha256()


def _anchor_and_reuse_failures(
    db: sqlite3.Connection,
    path: Path,
    anchor: Any,
    evidence: dict[str, Any],
    *,
    allow_non_production_anchor: bool = False,
) -> list[str]:
    """Promotion needs a store consistent with the external evidence anchor and a clear reuse verdict.

    After a restore of an older database, or a rewritten attempt chain, no
    promotion is allowed until the store agrees with the anchor again. A plan
    whose holdout the detector found SUSPECT or UNDETERMINED needs a new
    independent holdout: no agent statement can clear it. The holdout must
    have been anchored while STARTED (before scoring), and a non-production
    anchor needs the reviewer's explicit waiver (Lot 4, C-3 and C-4).
    """

    from .research_policy import reuse_policy_looser_than_floor

    failures: list[str] = []
    provenance = evidence.get(PROVENANCE_KEY) or {}
    holdout = db.execute(
        "SELECT protocol_id FROM holdout_evaluations WHERE evaluation_id = ?", (provenance.get("holdout_evaluation_id"),)
    ).fetchone()
    protocol_id = holdout["protocol_id"] if holdout else None
    if anchor is None:
        failures.append("evidence_anchor_missing")
    else:
        from .anchoring import AnchorError, verify_store_against_anchor

        try:
            verification = verify_store_against_anchor(path, anchor)
        except (AnchorError, OSError):
            failures.append("evidence_anchor_unreadable")
        else:
            failures.extend(f"evidence_anchor_inconsistent:{code}" for code in verification.errors)
            if not verification.production_grade and not allow_non_production_anchor:
                failures.append("evidence_anchor_not_production_grade")
            first_status = None
            for checkpoint in anchor.checkpoints():
                for item in checkpoint.consumed_holdouts:
                    if item.get("evaluation_id") == provenance.get("holdout_evaluation_id"):
                        first_status = item.get("status")
                        break
                if first_status is not None:
                    break
            if first_status is None:
                failures.append("holdout_not_anchored")
            elif first_status != "STARTED":
                failures.append("holdout_not_anchored_before_scoring")
    try:
        check = db.execute(
            "SELECT verdict, policy_json FROM holdout_reuse_checks WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
    except sqlite3.Error:
        check = None
    if check is None:
        failures.append("holdout_reuse_check_missing")
    else:
        if check["verdict"] == "SUSPECT":
            failures.append("holdout_reuse_suspected")
        elif check["verdict"] == "UNDETERMINED":
            failures.append("holdout_reuse_undetermined")
        elif check["verdict"] != "CLEAR":
            failures.append("holdout_reuse_check_invalid")
        if reuse_policy_looser_than_floor(json.loads(check["policy_json"])):
            failures.append("holdout_reuse_policy_below_floor")
    return failures


def _close(recorded: Any, value: Any) -> bool:
    numeric = (int, float)
    if isinstance(recorded, bool) or isinstance(value, bool):
        return recorded == value
    if isinstance(recorded, numeric) and isinstance(value, numeric):
        return math.isclose(float(recorded), float(value), rel_tol=EVIDENCE_TOLERANCE, abs_tol=EVIDENCE_TOLERANCE)
    return recorded == value


def _holdout_record_failures(
    db: sqlite3.Connection,
    protocol_id: str | None,
    report_json: str | None,
    evaluation_id: str | None,
) -> tuple[list[str], dict[str, Any] | None]:
    """Check the hash-chained holdout attempt against the stored report; return its trial universe payload.

    The attempt's terminal event records the SHA-256 of the stored report, the
    trial universe and the overfitting payload (Lot 4, C-1). The universe must
    be the store's trials as logged before the attempt's STARTED event.
    """

    failures: list[str] = []
    try:
        records = [
            item for item in holdout_attempt_records(db, protocol_id or "")
            if str(item.get("status", "")).startswith("COMPLETED")
        ]
    except (sqlite3.Error, ValueError):
        records = []
    if not records:
        return ["holdout_attempt_record_missing"], None
    if len(records) > 1:
        return ["holdout_attempt_ambiguous"], None
    (record,) = records
    metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    universe = metrics.get("trial_universe")
    if (
        not isinstance(metrics.get("report_sha256"), str)
        or not isinstance(universe, dict)
        or not isinstance(metrics.get("overfitting"), dict)
    ):
        return ["holdout_attempt_record_incomplete"], None
    if metrics.get("evaluation_id") != evaluation_id:
        failures.append("holdout_attempt_for_another_evaluation")
    if report_json is None or hashlib.sha256(report_json.encode("utf-8")).hexdigest() != metrics["report_sha256"]:
        failures.append("holdout_report_hash_mismatch")
    try:
        logged = trial_statistics_before(db, protocol_id or "", int(record["started_seq"]))
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        logged = None
    sharpes = universe.get("trial_sharpes")
    n_trials = universe.get("n_trials")
    if (
        logged is None
        or not isinstance(n_trials, int)
        or isinstance(n_trials, bool)
        or n_trials < logged.count
        or not isinstance(sharpes, list)
        or len(sharpes) != len(logged.trial_sharpes)
        or not all(_close(a, b) for a, b in zip(sharpes, logged.trial_sharpes, strict=True))
    ):
        failures.append("holdout_trial_universe_differs_from_attempt_log")
        return failures, None
    return failures, universe


def _rederivation_failures(
    plan: dict[str, Any],
    report: dict[str, Any],
    holdout_bars: Any,
    *,
    db: sqlite3.Connection | None = None,
    path: str | Path | None = None,
    protocol_id: str | None = None,
    report_json: str | None = None,
    evaluation_id: str | None = None,
) -> list[str]:
    """Recompute the holdout from market bars and compare it with the stored report (Lot 4, A-4, D-1, C-1).

    A stored report, however consistent with the plan and the attempt log, is
    only trusted once the same numbers come out of the sealed plan run again on
    the bars it was sealed for. The P&L fields are compared with
    ``holdout_report_differs_from_rederivation``; the PBO, deflated Sharpe and
    trial count are re-derived with the trial universe recorded in the
    hash-chained holdout attempt and compared with
    ``holdout_overfitting_differs_from_rederivation``.
    """

    if holdout_bars is None:
        return ["holdout_not_rederived_from_market_data"]
    if "strategy_validation_version" not in plan:
        return ["holdout_rederivation_unsupported_for_this_plan"]
    if db is None or path is None:
        return ["holdout_rederivation_needs_the_store"]
    from .strategy_validation import REDERIVED_FIELDS, REDERIVED_OVERFITTING_FIELDS, rederive_holdout_evidence

    failures, universe = _holdout_record_failures(db, protocol_id, report_json, evaluation_id)
    try:
        derived = rederive_holdout_evidence(
            holdout_bars, plan, store=BacktestStore(path), trial_universe=universe
        )
    except (ValueError, TypeError, KeyError) as exc:
        return [*failures, f"holdout_rederivation_failed:{type(exc).__name__}"]
    if "overfitting" in derived:
        stored_overfitting = report.get("overfitting") if isinstance(report.get("overfitting"), dict) else {}
        stored = {**stored_overfitting, "variants_tested": report.get("variants_tested")}
        recomputed = {**derived["overfitting"], "variants_tested": derived["variants_tested"]}
        if not all(_close(stored.get(key), recomputed.get(key)) for key in REDERIVED_OVERFITTING_FIELDS):
            failures.append("holdout_overfitting_differs_from_rederivation")
    if derived["selected_candidate_id"] != report.get("selected_candidate_id"):
        return [*failures, "holdout_report_differs_from_rederivation"]
    selected = report.get("selected_candidate") or {}
    stressed = [
        scenario["selected_candidate"]["net_pnl"]
        for scenario in report.get("cost_sensitivity", ())
        if isinstance(scenario, dict) and scenario.get("cost_multiplier", 0) > 1.0 and scenario.get("selected_candidate")
    ]
    stored = {
        "trade_count": selected.get("trade_count", 0),
        "net_pnl": selected.get("net_pnl"),
        "profit_factor": selected.get("profit_factor"),
        "max_drawdown_pct": selected.get("max_drawdown_pct"),
        "edge_vs_baseline": report.get("edge_vs_baseline"),
        "cost_stress_net_pnl": min(stressed) if stressed else None,
    }
    for key in REDERIVED_FIELDS:
        recorded, value = stored.get(key), derived.get(key)
        if isinstance(recorded, (int, float)) and isinstance(value, (int, float)):
            if not math.isclose(float(recorded), float(value), rel_tol=EVIDENCE_TOLERANCE, abs_tol=EVIDENCE_TOLERANCE):
                return [*failures, "holdout_report_differs_from_rederivation"]
        elif recorded != value:
            return [*failures, "holdout_report_differs_from_rederivation"]
    return failures


def _report_binding_failures(
    db: sqlite3.Connection,
    protocol_id: str,
    plan: dict[str, Any],
    plan_row: sqlite3.Row | None,
    report: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    """Bind a stored holdout report to its sealed plan, the current code and the attempt log (Lot 4, A-4).

    These checks raise the bar for a report minted through the public store API:
    the report must name its own plan and dataset, select one of the plan's
    sealed candidates with that candidate's parameters, come from the code that
    runs now, and appear in the hash-chained attempt log together with the
    plan's fold trials. In-process code that also writes consistent attempt
    events can still mint one; only re-deriving the holdout from the market
    data closes that, and it is not implemented.
    """

    if plan_row is None:
        return ["holdout_plan_not_sealed"]
    failures = []
    if report.get("protocol_id") != protocol_id or report.get("plan_sha256") != protocol_id:
        failures.append("holdout_report_names_another_plan")
    if report.get("dataset_identity_sha256") != plan_row["dataset_sha256"]:
        failures.append("holdout_report_dataset_differs_from_plan")
    candidates = {item.get("candidate_id"): item for item in plan.get("candidates") or () if isinstance(item, dict)}
    sealed = candidates.get(manifest.get("candidate_id"))
    sealed_params = {key: value for key, value in sealed.items() if key != "candidate_id"} if sealed else None
    if sealed_params is None or sealed_params != report.get("selected_config"):
        failures.append("holdout_candidate_not_in_sealed_plan")
    try:
        current = _current_implementation_sha256(plan)
    except (OSError, ImportError):
        current = None
    if plan.get("implementation_sha256") != current:
        failures.append("code_changed_since_holdout")
    try:
        recorded = db.execute(
            "SELECT 1 FROM research_attempt_events WHERE kind = 'holdout' AND protocol_id = ? "
            "AND status LIKE 'COMPLETED%' LIMIT 1",
            (protocol_id,),
        ).fetchone()
    except sqlite3.Error:
        recorded = None
    if recorded is None:
        failures.append("holdout_attempt_missing_from_log")
    return failures


def _version_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["manifest"] = json.loads(item.pop("manifest_json"))
    item["criteria"] = json.loads(item.pop("criteria_json"))
    return item
