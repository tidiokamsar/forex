"""Append-only, hash-chained registry of every research attempt (trial).

Every variant scored by a sealed evaluation is an attempt: each candidate of
each walk-forward fold (``fold_trial``), each candidate of each walk-forward
run as a whole (``walk_forward_run``) and each one-time holdout scoring
(``holdout``). Attempts live in the same SQLite store as the sealed plans, in
``research_attempt_events``. A status change is a new appended event row,
never an update: an attempt has one ``STARTED`` event and at most one terminal
event (``COMPLETED_SELECTED``, ``COMPLETED_REJECTED``, ``FAILED``,
``INTERRUPTED`` or ``ABANDONED``). An attempt with no terminal event is
reported as ``INTERRUPTED`` (the process died or is still running).

Integrity: SQLite triggers refuse UPDATE and DELETE on event rows and refuse
an INSERT whose ``prev_hash`` is not the current chain head; every row stores
``row_hash = sha256(prev_hash, canonical row)`` so ``verify_attempt_chain``
detects an edited, removed (gap or ``sqlite_sequence`` mismatch) or reordered
row. This is tamper-evidence through the API and against casual edits, not
tamper-proofing: whoever can write the database file can drop the triggers and
rewrite the whole chain consistently. Only a chain head kept outside the
database (``chain_head`` printed, exported or committed elsewhere, then passed
as ``expected_head``) detects such a rewrite.

Store-wide trial count (``trial_statistics``): the number of distinct trials
relevant to a candidate. Scope: every ``fold_trial`` and ``walk_forward_run``
attempt in the store, across every plan, run, experiment and status
(``STARTED``/``INTERRUPTED``, ``FAILED`` and ``ABANDONED`` included), on the
candidate's instrument (attempts or scopes without an instrument match every
instrument), over every data period of that instrument (an explicit
``TrialScope`` may still restrict the period). Holdout scorings are excluded: they
re-score an already counted candidate. Trials are distinct by
``(family, params_sha256, dataset fingerprint)``; the fingerprint is the
canonical market-bar identity (source-byte hash when absent), so retrying the
same plan does not inflate the count while the same grid on another dataset
counts again. Creating a new plan can only add trials.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import __version__

ATTEMPT_LOG_VERSION = "1.0.0"
TABLE = "research_attempt_events"
GENESIS_HASH = "0" * 64


class AttemptStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED_SELECTED = "COMPLETED_SELECTED"
    COMPLETED_REJECTED = "COMPLETED_REJECTED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    ABANDONED = "ABANDONED"


TERMINAL_STATUSES = frozenset(status for status in AttemptStatus if status is not AttemptStatus.STARTED)
KIND_FOLD = "fold_trial"
KIND_RUN = "walk_forward_run"
KIND_HOLDOUT = "holdout"
ATTEMPT_KINDS = (KIND_FOLD, KIND_RUN, KIND_HOLDOUT)
TRIAL_KINDS = (KIND_FOLD, KIND_RUN)
TRIAL_SCOPE_DEFINITION = (
    "distinct (family, params_sha256, dataset fingerprint) over every fold_trial and walk_forward_run attempt "
    "in the store, all plans and all statuses (failed, interrupted and abandoned included), same instrument "
    "(unknown instrument matches all), any data period (Lot 4: exploration on earlier or later data of the "
    "instrument informs the choice too); holdout scorings excluded"
)

# Hashed columns, in order. ``row_hash`` covers all of them (``seq`` included, so reordering breaks the chain).
_COLUMNS = (
    "seq",
    "event_id",
    "attempt_id",
    "status",
    "recorded_at",
    "kind",
    "run_id",
    "experiment_id",
    "protocol_id",
    "fold",
    "candidate_id",
    "model_id",
    "model_version",
    "family",
    "strategy",
    "strategy_version",
    "params_json",
    "params_sha256",
    "code_sha256",
    "astra_version",
    "dataset_sha256",
    "dataset_identity_sha256",
    "holdout_content_sha256",
    "instrument",
    "period_start",
    "period_end",
    "window_start",
    "window_end",
    "started_at",
    "finished_at",
    "metrics_json",
    "trial_sharpe",
    "reason",
    "prev_hash",
)
_IDENTITY_COLUMNS = _COLUMNS[_COLUMNS.index("kind"):_COLUMNS.index("started_at") + 1]
_TRIGGERS = (
    f"{TABLE}_no_update",
    f"{TABLE}_no_delete",
    f"{TABLE}_chain_head",
)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'STARTED', 'COMPLETED_SELECTED', 'COMPLETED_REJECTED', 'FAILED', 'INTERRUPTED', 'ABANDONED'
    )),
    recorded_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('fold_trial', 'walk_forward_run', 'holdout')),
    run_id TEXT NOT NULL,
    experiment_id TEXT,
    protocol_id TEXT,
    fold INTEGER,
    candidate_id TEXT,
    model_id TEXT,
    model_version TEXT,
    family TEXT,
    strategy TEXT,
    strategy_version TEXT,
    params_json TEXT NOT NULL,
    params_sha256 TEXT NOT NULL,
    code_sha256 TEXT,
    astra_version TEXT NOT NULL,
    dataset_sha256 TEXT,
    dataset_identity_sha256 TEXT,
    holdout_content_sha256 TEXT,
    instrument TEXT,
    period_start TEXT,
    period_end TEXT,
    window_start TEXT,
    window_end TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    metrics_json TEXT,
    trial_sharpe REAL,
    reason TEXT,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_attempt ON {TABLE}(attempt_id);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_protocol ON {TABLE}(protocol_id);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_instrument ON {TABLE}(instrument);
CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE}
BEGIN
    SELECT RAISE(ABORT, 'research attempts are append-only: UPDATE refused');
END;
CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE}
BEGIN
    SELECT RAISE(ABORT, 'research attempts are append-only: DELETE refused');
END;
CREATE TRIGGER IF NOT EXISTS {TABLE}_chain_head BEFORE INSERT ON {TABLE}
WHEN NEW.prev_hash IS NOT COALESCE(
    (SELECT row_hash FROM {TABLE} ORDER BY seq DESC LIMIT 1), '{GENESIS_HASH}'
)
BEGIN
    SELECT RAISE(ABORT, 'research attempt chain: prev_hash must be the current chain head');
END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _safe(value: Any) -> Any:
    """Replace non-finite floats by ``None`` so payloads serialize with ``allow_nan=False``."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if is_dataclass(value) and not isinstance(value, type):
        return _safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _safe(value), default=_json_default, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_hash(content: Mapping[str, Any]) -> str:
    """``sha256(prev_hash + "\\n" + canonical JSON of every hashed column)``."""

    payload = {name: content.get(name) for name in _COLUMNS}
    return _sha256_text(f"{payload['prev_hash']}\n{canonical_json(payload)}")


def sharpe_like(values: Sequence[float]) -> float | None:
    """``mean / sample stdev``; ``None`` below two values or for (numerically) zero dispersion.

    Same rule as ``strategy_validation._sharpe`` so trial Sharpes are comparable.
    """

    values = [float(value) for value in values]
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    scale = max(abs(value) for value in values)
    if deviation <= 0 or deviation <= 1e-9 * max(scale, 1e-300):
        return None
    result = statistics.fmean(values) / deviation
    return result if math.isfinite(result) else None


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# -- data types -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    """What was tried, on which data and code. Copied onto every event of the attempt."""

    kind: str
    run_id: str
    params: Mapping[str, Any]
    candidate_id: str | None = None
    family: str | None = None
    strategy: str | None = None
    strategy_version: str | None = None
    protocol_id: str | None = None
    experiment_id: str | None = None
    fold: int | None = None
    model_id: str | None = None
    model_version: str | None = None
    code_sha256: str | None = None
    dataset_sha256: str | None = None
    dataset_identity_sha256: str | None = None
    holdout_content_sha256: str | None = None
    instrument: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    window_start: str | None = None
    window_end: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ATTEMPT_KINDS:
            raise ValueError(f"unsupported attempt kind {self.kind!r}")
        if self.kind in TRIAL_KINDS and not (self.candidate_id and self.family):
            raise ValueError("trial attempts need a candidate_id and a strategy family")


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    attempt_id: str
    status: AttemptStatus | str
    metrics: Mapping[str, Any] | None = None
    trial_sharpe: float | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TrialScope:
    """Which attempts are relevant to a candidate. ``None`` fields match everything."""

    instrument: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    source: str = "explicit"


@dataclass(frozen=True, slots=True)
class TrialStatistics:
    count: int
    trial_sharpes: tuple[float, ...]
    trial_sharpe_variance: float | None
    attempts: int
    by_status: dict
    protocols: tuple[str, ...]
    scope: TrialScope
    definition: str = TRIAL_SCOPE_DEFINITION


@dataclass(frozen=True, slots=True)
class ChainVerification:
    ok: bool
    events: int
    head_seq: int
    head_hash: str
    errors: tuple[str, ...] = field(default_factory=tuple)


# -- low-level helpers on an open connection -----------------------------------------


def _table_exists(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None


def _head(db: sqlite3.Connection) -> tuple[int, str]:
    row = db.execute(f"SELECT seq, row_hash FROM {TABLE} ORDER BY seq DESC LIMIT 1").fetchone()
    return (int(row[0]), str(row[1])) if row else (0, GENESIS_HASH)


def _sequence_value(db: sqlite3.Connection) -> int | None:
    try:
        row = db.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (TABLE,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[0]) if row else None


def _append(db: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> None:
    """Append rows in order inside the caller's write transaction, chaining each to the head."""

    head_seq, head_hash = _head(db)
    # Never reuse a sequence number freed by a deletion: the gap stays visible to verification.
    next_seq = max(head_seq, _sequence_value(db) or 0) + 1
    for row in rows:
        content = {**row, "seq": next_seq, "prev_hash": head_hash}
        digest = row_hash(content)
        columns = (*_COLUMNS, "row_hash")
        db.execute(
            f"INSERT INTO {TABLE} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            (*(content.get(name) for name in _COLUMNS), digest),
        )
        head_hash, next_seq = digest, next_seq + 1


def _event_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(f"SELECT * FROM {TABLE} ORDER BY seq").fetchall()


def _attempt_states(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest state per attempt; an attempt without a terminal event is INTERRUPTED."""

    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        current = states.get(item["attempt_id"])
        if current is None:
            states[item["attempt_id"]] = {**item, "first_seq": item["seq"], "events": 1}
        else:
            states[item["attempt_id"]] = {**current, **item, "first_seq": current["first_seq"], "events": current["events"] + 1}
    for state in states.values():
        state["recorded_status"] = state["status"]
        if state["status"] == AttemptStatus.STARTED:
            state["status"] = AttemptStatus.INTERRUPTED.value
            state["status_derived"] = "no terminal event: interrupted or still running"
        state["metrics"] = json.loads(state.pop("metrics_json")) if state.get("metrics_json") else None
        state["params"] = json.loads(state.pop("params_json"))
    return states


def _matches(state: Mapping[str, Any], scope: TrialScope) -> bool:
    if scope.instrument and state.get("instrument") and str(state["instrument"]).upper() != scope.instrument.upper():
        return False
    start, end = _parse_time(scope.period_start), _parse_time(scope.period_end)
    other_start, other_end = _parse_time(state.get("period_start")), _parse_time(state.get("period_end"))
    if start and other_end and other_end < start:
        return False
    return not (end and other_start and other_start > end)


def trial_key(state: Mapping[str, Any]) -> tuple[str, str, str]:
    fingerprint = state.get("dataset_identity_sha256") or state.get("dataset_sha256") or ""
    return (str(state.get("family") or ""), str(state["params_sha256"]), str(fingerprint))


def _statistics(db: sqlite3.Connection, scope: TrialScope) -> TrialStatistics:
    rows = _event_rows(db) if _table_exists(db) else []
    states = [state for state in _attempt_states(rows).values() if state["kind"] in TRIAL_KINDS and _matches(state, scope)]
    keys: dict[tuple[str, str, str], int] = {}
    sharpes: dict[tuple[str, str, str], tuple[int, float]] = {}
    by_status: dict[str, int] = {}
    for state in sorted(states, key=lambda item: item["first_seq"]):
        key = trial_key(state)
        keys.setdefault(key, state["first_seq"])
        by_status[state["status"]] = by_status.get(state["status"], 0) + 1
        value = state.get("trial_sharpe")
        if state["kind"] == KIND_RUN and value is not None and math.isfinite(value):
            sharpes[key] = (state["seq"], float(value))  # latest run of the same trial wins
    ordered = tuple(value for _key, (_seq, value) in sorted(sharpes.items(), key=lambda item: keys[item[0]]))
    return TrialStatistics(
        count=len(keys),
        trial_sharpes=ordered,
        trial_sharpe_variance=statistics.variance(ordered) if len(ordered) >= 2 else None,
        attempts=len(states),
        by_status=dict(sorted(by_status.items())),
        protocols=tuple(sorted({state["protocol_id"] for state in states if state.get("protocol_id")})),
        scope=scope,
    )


def scope_for_plan(plan: Mapping[str, Any] | None, *, source: str = "plan") -> TrialScope:
    """The instrument of a sealed plan, over every data period.

    A grid explored on earlier or later data of the same instrument informs the
    choice as much as one on this dataset (Lot 4 QA, D-10), so no period filter
    is applied; ``TrialScope`` still accepts one for explicit queries.
    """

    if not plan:
        return TrialScope(source="whole_store")
    return TrialScope(instrument=plan.get("instrument"), source=source)


def _plan(db: sqlite3.Connection, protocol_id: str | None) -> dict | None:
    if not protocol_id:
        return None
    try:
        row = db.execute("SELECT plan_json FROM research_protocols WHERE protocol_id = ?", (protocol_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return json.loads(row[0]) if row else None


def _verify(db: sqlite3.Connection, expected_head: str | None = None) -> ChainVerification:
    if not _table_exists(db):
        return ChainVerification(False, 0, 0, GENESIS_HASH, ("attempt log table is missing",))
    errors: list[str] = []
    triggers = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (TABLE,))}
    missing = [name for name in _TRIGGERS if name not in triggers]
    if missing:
        errors.append(f"append-only triggers missing: {', '.join(missing)}")
    rows = _event_rows(db)
    previous_hash, previous_seq = GENESIS_HASH, 0
    started: dict[str, dict[str, Any]] = {}
    terminal: set[str] = set()
    hashes = set()
    for row in rows:
        item = dict(row)
        seq = int(item["seq"])
        if seq != previous_seq + 1:
            errors.append(f"sequence gap before seq {seq} (expected {previous_seq + 1}): rows removed or reordered")
        if item["prev_hash"] != previous_hash:
            errors.append(f"seq {seq}: prev_hash does not link to the previous row")
        if row_hash(item) != item["row_hash"]:
            errors.append(f"seq {seq}: row_hash does not match the row content (edited row)")
        if _sha256_text(item["params_json"]) != item["params_sha256"]:
            errors.append(f"seq {seq}: params_sha256 does not match params_json")
        attempt = item["attempt_id"]
        if item["status"] == AttemptStatus.STARTED:
            if attempt in started:
                errors.append(f"seq {seq}: attempt {attempt} started twice")
            started[attempt] = item
        else:
            origin = started.get(attempt)
            if origin is None:
                errors.append(f"seq {seq}: terminal event for attempt {attempt} without a STARTED event")
            elif any(origin[name] != item[name] for name in _IDENTITY_COLUMNS):
                errors.append(f"seq {seq}: terminal event identity differs from its STARTED event")
            if attempt in terminal:
                errors.append(f"seq {seq}: attempt {attempt} has more than one terminal event")
            terminal.add(attempt)
        hashes.add(item["row_hash"])
        previous_hash, previous_seq = item["row_hash"], seq
    sequence = _sequence_value(db)
    if rows and sequence is not None and sequence != previous_seq:
        errors.append(f"sqlite_sequence ({sequence}) differs from the last seq ({previous_seq}): trailing rows removed")
    if rows and sequence is None:
        errors.append("sqlite_sequence entry missing for the attempt log")
    if not rows and sequence:
        errors.append(f"attempt log is empty but sqlite_sequence is {sequence}: rows removed")
    if expected_head and expected_head != GENESIS_HASH and expected_head not in hashes:
        errors.append("anchored chain head not found: the log was rewritten or truncated after it was anchored")
    return ChainVerification(not errors, len(rows), previous_seq, previous_hash, tuple(errors))


def promotion_trial_failures(
    db: sqlite3.Connection, manifest: Mapping[str, Any], variants_tested: float | None
) -> tuple[str, ...]:
    """Registry gate: the attempt chain is intact and the evidence does not understate the trial count.

    The scope is the plan named by the manifest's ``plan_sha256``; without a known plan
    the whole store is the scope (the most conservative count).
    """

    verification = _verify(db)
    if not verification.ok:
        return ("attempt_log_tampered",)
    failures = []
    protocol_id = manifest.get("plan_sha256") if isinstance(manifest, Mapping) else None
    plan = _plan(db, protocol_id)
    if plan is not None:
        own = {
            trial_key(state)
            for state in _attempt_states(_event_rows(db)).values()
            if state["kind"] in TRIAL_KINDS and state.get("protocol_id") == protocol_id
        }
        candidates = plan.get("candidates") or ()
        if not candidates or len(own) < len(candidates):
            failures.append("attempt_log_missing_for_plan")
    stats = _statistics(db, scope_for_plan(plan))
    # A missing count is already reported by the registry as ``overfitting_check_missing``.
    if variants_tested is not None and variants_tested < stats.count:
        failures.append("trial_count_understated")
    return tuple(failures)


# -- public API ---------------------------------------------------------------------


class AttemptLog:
    """Append-only access to the attempt registry of one SQLite store."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA_SQL)

    def start(self, identities: Sequence[AttemptIdentity]) -> list[str]:
        """Append one STARTED event per identity, atomically; returns the new attempt ids."""

        now = _now()
        ids, rows = [], []
        for identity in identities:
            attempt_id = str(uuid.uuid4())
            params_json = canonical_json(identity.params)
            values = {name: getattr(identity, name) for name in identity.__slots__ if name != "params"}
            rows.append(
                {
                    **values,
                    "event_id": str(uuid.uuid4()),
                    "attempt_id": attempt_id,
                    "status": AttemptStatus.STARTED.value,
                    "recorded_at": now,
                    "params_json": params_json,
                    "params_sha256": _sha256_text(params_json),
                    "astra_version": __version__,
                    "started_at": now,
                }
            )
            ids.append(attempt_id)
        with self._write() as db:
            _append(db, rows)
        return ids

    def finish(self, outcomes: Sequence[AttemptOutcome]) -> None:
        """Append one terminal event per outcome; each attempt must be STARTED and not yet terminal."""

        if not outcomes:
            return
        now = _now()
        with self._write() as db:
            rows = []
            for outcome in outcomes:
                status = AttemptStatus(outcome.status)
                if status not in TERMINAL_STATUSES:
                    raise ValueError("an attempt can only be finished with a terminal status")
                events = db.execute(
                    f"SELECT * FROM {TABLE} WHERE attempt_id = ? ORDER BY seq", (outcome.attempt_id,)
                ).fetchall()
                if not events or events[0]["status"] != AttemptStatus.STARTED:
                    raise ValueError(f"attempt {outcome.attempt_id} was never started")
                if len(events) > 1:
                    raise ValueError(f"attempt {outcome.attempt_id} already has a terminal status")
                origin = dict(events[0])
                sharpe = outcome.trial_sharpe
                rows.append(
                    {
                        **{name: origin[name] for name in _IDENTITY_COLUMNS},
                        "params_json": origin["params_json"],
                        "params_sha256": origin["params_sha256"],
                        "astra_version": origin["astra_version"],
                        "event_id": str(uuid.uuid4()),
                        "attempt_id": outcome.attempt_id,
                        "status": status.value,
                        "recorded_at": now,
                        "finished_at": now,
                        "metrics_json": canonical_json(outcome.metrics) if outcome.metrics is not None else None,
                        "trial_sharpe": float(sharpe) if isinstance(sharpe, (int, float)) and math.isfinite(sharpe) else None,
                        "reason": outcome.reason,
                    }
                )
            _append(db, rows)

    def abandon(self, attempt_id: str, *, reason: str) -> None:
        """Declare a STARTED attempt abandoned (it still counts as a trial)."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("abandoning an attempt requires a reason")
        self.finish([AttemptOutcome(attempt_id, AttemptStatus.ABANDONED, reason=reason.strip())])

    def events(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._read() as db:
            if not _table_exists(db):
                return []
            rows = _event_rows(db)
        items = [dict(row) for row in rows]
        return items[-limit:] if limit else items

    def attempts(
        self,
        *,
        protocol_id: str | None = None,
        instrument: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Current state of every attempt (oldest first), optionally filtered."""

        with self._read() as db:
            rows = _event_rows(db) if _table_exists(db) else []
        states = sorted(_attempt_states(rows).values(), key=lambda item: item["first_seq"])
        result = [
            state
            for state in states
            if (protocol_id is None or state.get("protocol_id") == protocol_id)
            and (instrument is None or str(state.get("instrument") or "").upper() == instrument.upper())
            and (status is None or state["status"] == status)
        ]
        return result[-limit:] if limit else result

    def trial_statistics(self, scope: TrialScope | None = None) -> TrialStatistics:
        with self._read() as db:
            return _statistics(db, scope or TrialScope(source="whole_store"))

    def trial_statistics_for_protocol(self, protocol_id: str) -> TrialStatistics:
        """Store-wide trials relevant to the candidates of a sealed plan (see the module docstring)."""

        with self._read() as db:
            plan = _plan(db, protocol_id)
            if plan is None:
                raise KeyError(f"sealed plan {protocol_id} not found")
            return _statistics(db, scope_for_plan(plan))

    def verify_chain(self, expected_head: str | None = None) -> ChainVerification:
        with self._read() as db:
            return _verify(db, expected_head)

    def chain_head(self) -> dict[str, Any]:
        """The current head, to be printed or exported and kept outside the database as an anchor."""

        with self._read() as db:
            seq, digest = _head(db) if _table_exists(db) else (0, GENESIS_HASH)
        return {"seq": seq, "row_hash": digest, "anchored_at": _now(), "attempt_log_version": ATTEMPT_LOG_VERSION}

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        if not self.path.exists():
            raise FileNotFoundError(f"database not found: {self.path}")
        db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
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


def trial_statistics(
    database_path: str | Path,
    *,
    instrument: str | None = None,
    period_start: str | datetime | None = None,
    period_end: str | datetime | None = None,
) -> TrialStatistics:
    """Store-wide trial count and per-trial Sharpes for an explicit scope (``None`` matches all)."""

    def text(value):
        return value.isoformat() if isinstance(value, datetime) else value

    return AttemptLog(database_path).trial_statistics(
        TrialScope(instrument=instrument, period_start=text(period_start), period_end=text(period_end))
    )


def trial_statistics_for_protocol(database_path: str | Path, protocol_id: str) -> TrialStatistics:
    """Store-wide trial count and per-trial Sharpes relevant to the candidates of a sealed plan."""

    return AttemptLog(database_path).trial_statistics_for_protocol(protocol_id)


def verify_attempt_chain(database_path: str | Path, expected_head: str | None = None) -> ChainVerification:
    return AttemptLog(database_path).verify_chain(expected_head)


# -- recorders used by the evaluation code paths ------------------------------------------


def log_for_store(store: Any) -> AttemptLog | None:
    """The attempt log of a store with a ``path`` (``BacktestStore``); ``None`` records nothing."""

    path = getattr(store, "path", None) if store is not None else None
    if path is None:
        return None
    log = AttemptLog(path)
    log.initialize()
    return log


def _plan_context(protocol_id: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    holdout = plan.get("holdout") or {}
    folds = plan.get("folds") or []
    return {
        "protocol_id": protocol_id,
        "code_sha256": plan.get("implementation_sha256"),
        "dataset_sha256": plan.get("dataset_sha256"),
        "dataset_identity_sha256": plan.get("dataset_identity_sha256"),
        "holdout_content_sha256": holdout.get("content_sha256"),
        "instrument": plan.get("instrument"),
        "period_start": folds[0].get("train_start") if folds else None,
        "period_end": holdout.get("end"),
    }


def _candidate_fields(descriptor: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": descriptor["candidate_id"],
        "family": descriptor.get("family") or plan.get("strategy") or "unknown",
        "strategy": descriptor.get("strategy") or plan.get("strategy"),
        "strategy_version": descriptor.get("strategy_version") or plan.get("strategy_version"),
        "params": descriptor.get("config", {}),
    }


def _metrics(item: Any) -> dict[str, Any]:
    data = asdict(item) if is_dataclass(item) and not isinstance(item, type) else dict(item)
    data.pop("config", None)
    return data


def _rejection_reason(item: Any) -> str:
    if not getattr(item, "eligible", True):
        return "ineligible: fewer validation trades than minimum_candidate_trades"
    return "not selected: lower validation selection score than the selected candidate"


def _failure(exc: BaseException) -> tuple[AttemptStatus, str]:
    status = AttemptStatus.FAILED if isinstance(exc, Exception) else AttemptStatus.INTERRUPTED
    return status, f"{type(exc).__name__}: {exc}"[:500]


class WalkForwardAttempts:
    """Records the attempts of one walk-forward run; with no store it records nothing.

    Call ``begin`` once the plan is sealed, ``start_fold``/``finish_fold`` around
    each fold's scoring and ``finish_run`` after the final selection. ``abort``
    (from an ``except BaseException`` that re-raises) closes every open attempt
    as FAILED (exception) or INTERRUPTED (KeyboardInterrupt, SystemExit).
    """

    def __init__(self, log: AttemptLog | None, *, experiment_id: str | None = None) -> None:
        self._log = log
        self._experiment_id = experiment_id
        self.run_id = str(uuid.uuid4())
        self._plan: Mapping[str, Any] = {}
        self._context: dict[str, Any] = {}
        self._candidates: tuple[dict[str, Any], ...] = ()
        self._run: dict[str, str] = {}
        self._fold: dict[str, str] = {}
        self._returns: dict[str, list[float]] = {}
        self._open: set[str] = set()

    @classmethod
    def for_store(cls, store: Any, *, experiment_id: str | None = None) -> WalkForwardAttempts:
        return cls(log_for_store(store), experiment_id=experiment_id)

    @property
    def enabled(self) -> bool:
        return self._log is not None

    def begin(self, protocol_id: str, plan: Mapping[str, Any], candidates: Iterable[Mapping[str, Any]]) -> None:
        if self._log is None:
            return
        self._plan = plan
        self._context = _plan_context(protocol_id, plan)
        self._candidates = tuple(_candidate_fields(item, plan) for item in candidates)
        folds = plan.get("folds") or []
        window = {
            "window_start": folds[0].get("train_start") if folds else None,
            "window_end": folds[-1].get("test_end") if folds else None,
        }
        ids = self._start(KIND_RUN, None, window)
        self._run = dict(zip((c["candidate_id"] for c in self._candidates), ids, strict=True))
        self._returns = {c["candidate_id"]: [] for c in self._candidates}

    def start_fold(self, fold: int) -> None:
        if self._log is None:
            return
        folds = self._plan.get("folds") or []
        bounds = folds[fold - 1] if 0 < fold <= len(folds) else {}
        window = {"window_start": bounds.get("validation_start"), "window_end": bounds.get("test_end")}
        ids = self._start(KIND_FOLD, fold, window)
        self._fold = dict(zip((c["candidate_id"] for c in self._candidates), ids, strict=True))

    def finish_fold(self, fold: int, trials: Iterable[Any], validation_returns: Mapping[str, Sequence[float]]) -> None:
        """Close the fold's attempts from its trials (``candidate_id``, ``selected``, ``eligible``)."""

        if self._log is None:
            return
        outcomes = []
        for trial in trials:
            cid = trial.candidate_id
            returns = list(validation_returns.get(cid, ()))
            self._returns.setdefault(cid, []).extend(returns)
            selected = bool(trial.selected)
            outcomes.append(
                AttemptOutcome(
                    self._fold[cid],
                    AttemptStatus.COMPLETED_SELECTED if selected else AttemptStatus.COMPLETED_REJECTED,
                    metrics={**_metrics(trial), "validation_trade_returns": returns},
                    trial_sharpe=sharpe_like(returns),
                    reason=None if selected else _rejection_reason(trial),
                )
            )
        self._close(outcomes)
        self._fold = {}

    def finish_run(self, final_selection: Iterable[Any]) -> None:
        if self._log is None:
            return
        outcomes = []
        for item in final_selection:
            cid = item.candidate_id
            selected = bool(item.selected)
            outcomes.append(
                AttemptOutcome(
                    self._run[cid],
                    AttemptStatus.COMPLETED_SELECTED if selected else AttemptStatus.COMPLETED_REJECTED,
                    metrics={**_metrics(item), "validation_trade_count_for_sharpe": len(self._returns.get(cid, ()))},
                    trial_sharpe=sharpe_like(self._returns.get(cid, ())),
                    reason=None if selected else _rejection_reason(item),
                )
            )
        self._close(outcomes)

    def abort(self, exc: BaseException) -> None:
        """Close every attempt still open; never masks the original exception."""

        if self._log is None or not self._open:
            return
        status, reason = _failure(exc)
        try:
            self._close([AttemptOutcome(attempt_id, status, reason=reason) for attempt_id in sorted(self._open)])
        except (sqlite3.Error, ValueError, OSError):
            # The STARTED rows remain and are reported as INTERRUPTED.
            return

    def _start(self, kind: str, fold: int | None, window: Mapping[str, Any]) -> list[str]:
        assert self._log is not None
        identities = [
            AttemptIdentity(
                kind=kind,
                run_id=self.run_id,
                experiment_id=self._experiment_id,
                fold=fold,
                **candidate,
                **self._context,
                **window,
            )
            for candidate in self._candidates
        ]
        ids = self._log.start(identities)
        self._open.update(ids)
        return ids

    def _close(self, outcomes: list[AttemptOutcome]) -> None:
        assert self._log is not None
        self._log.finish(outcomes)
        self._open.difference_update(outcome.attempt_id for outcome in outcomes)


class HoldoutAttempt:
    """The attempt of one holdout scoring; with no store it records nothing."""

    def __init__(self, log: AttemptLog | None, attempt_id: str | None, *, has_candidate: bool = False) -> None:
        self._log = log
        self.attempt_id = attempt_id
        self._has_candidate = has_candidate

    @classmethod
    def start(
        cls,
        store: Any,
        *,
        protocol_id: str,
        plan: Mapping[str, Any],
        candidate_id: str | None,
        experiment_id: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> HoldoutAttempt:
        log = log_for_store(store)
        if log is None:
            return cls(None, None)
        descriptor = next((item for item in plan.get("candidates") or () if item.get("candidate_id") == candidate_id), None)
        candidate = _candidate_fields(descriptor, plan) if descriptor else {"params": {}}
        holdout = plan.get("holdout") or {}
        (attempt_id,) = log.start(
            [
                AttemptIdentity(
                    kind=KIND_HOLDOUT,
                    run_id=str(uuid.uuid4()),
                    experiment_id=experiment_id,
                    model_id=model_id,
                    model_version=model_version,
                    window_start=holdout.get("start"),
                    window_end=holdout.get("end"),
                    **candidate,
                    **_plan_context(protocol_id, plan),
                )
            ]
        )
        return cls(log, attempt_id, has_candidate=descriptor is not None)

    def finish(self, metrics: Mapping[str, Any] | None, trial_sharpe: float | None = None) -> None:
        if self._log is None or self.attempt_id is None:
            return
        selected = self._has_candidate
        self._log.finish(
            [
                AttemptOutcome(
                    self.attempt_id,
                    AttemptStatus.COMPLETED_SELECTED if selected else AttemptStatus.COMPLETED_REJECTED,
                    metrics=metrics,
                    trial_sharpe=trial_sharpe,
                    reason=None if selected else "no candidate was selected on validation windows",
                )
            ]
        )

    def fail(self, exc: BaseException) -> None:
        if self._log is None or self.attempt_id is None:
            return
        status, reason = _failure(exc)
        try:
            self._log.finish([AttemptOutcome(self.attempt_id, status, reason=reason)])
        except (sqlite3.Error, ValueError, OSError):
            return
