"""Evidence anchoring outside the SQLite store and its backups.

The attempt log (``astra.attempts``), the sealed plans (``research_protocols``)
and the consumed holdouts (``holdout_evaluations``) live in one SQLite file.
Whoever can write that file can drop the triggers and rewrite the chain
consistently, or replace the file with an older backup that brings a spent
holdout back. Neither is visible from inside the file. An anchor is a record
kept elsewhere: an append-only, hash-chained series of ``EvidenceCheckpoint``
values (attempt chain head, consumed holdouts, sealed plans, experiments).
``verify_store_against_anchor`` compares a store with every anchored
checkpoint and reports, among others, a store that is behind the anchor (an
older backup restored) or whose chain no longer contains an anchored head (a
rewritten or divergent chain).

Production target: an external immutable store (WORM / object lock or an
equivalent append-only service the store's owner cannot rewrite). No such
adapter is implemented here, and no provider, endpoint or credential is
assumed. ``EvidenceAnchor`` is the interface such an adapter must implement,
with ``production_grade = True`` only when its storage really is immutable.

``LocalFileAnchor`` is the only adapter shipped. It is NOT a production
guarantee: a local file can be edited, truncated, replaced or deleted by its
owner, who is usually the same account that owns the database. Its hash chain
detects an edited or reordered line, not a file truncated at a line boundary,
rewritten from scratch, or deleted (a deleted file is reported as
``anchor_empty``). It is useful for tests, drills and as a second copy kept
on separate media.

Configuration: only the ``ASTRA_EVIDENCE_ANCHOR`` environment variable (a file
path for ``LocalFileAnchor``, resolved to an absolute path), read by
``anchor_from_environment``.

Binding (Lot 4, C-3): the first checkpoint of an anchor carries a random
``anchor_id`` that every later checkpoint repeats. The first anchoring of a
store records that id, the anchor kind and its location in the store's
append-only ``evidence_anchor_bindings`` table. A store is then verified only
against an anchor with the same identity (``anchor_identity_mismatch``), an
anchor with checkpoints never meets an unbound store
(``store_not_bound_to_anchor``), and an empty anchor is refused for a store
that is already bound or already holds sealed plans or holdout evaluations
(``anchor_empty_for_used_store``): restoring a backup and pointing the store at
a fresh anchor (another file, or ``ASTRA_EVIDENCE_ANCHOR`` repointed) no longer
makes a spent holdout look unspent.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .attempts import GENESIS_HASH, TABLE, _head, _table_exists, _verify, canonical_json

ANCHOR_FORMAT_VERSION = "1.1.0"
LEGACY_FORMAT_VERSION = "1.0.0"  # checkpoints without anchor_id
ENVIRONMENT_VARIABLE = "ASTRA_EVIDENCE_ANCHOR"
CONSUMED_STATUSES = frozenset({"STARTED", "COMPLETED", "FAILED"})
FINAL_STATUSES = frozenset({"COMPLETED", "FAILED"})

# Verification codes.
ANCHOR_EMPTY = "anchor_empty"
STORE_MISSING = "store_missing"
STORE_BEHIND_ANCHOR = "store_behind_anchor"
ATTEMPT_CHAIN_DIVERGED = "attempt_chain_diverged"
ATTEMPT_LOG_TAMPERED = "attempt_log_tampered"
CONSUMED_HOLDOUT_MISSING = "consumed_holdout_missing"
SEALED_PROTOCOL_MISSING = "sealed_protocol_missing"
EXPERIMENT_MISSING = "experiment_missing"
ANCHOR_IDENTITY_MISMATCH = "anchor_identity_mismatch"
ANCHOR_WITHOUT_IDENTITY = "anchor_without_identity"
STORE_NOT_BOUND_TO_ANCHOR = "store_not_bound_to_anchor"
ANCHOR_EMPTY_FOR_USED_STORE = "anchor_empty_for_used_store"

BINDING_TABLE = "evidence_anchor_bindings"
# Append-only record, in the store, of the anchor it was first anchored to (Lot 4, C-3).
BINDING_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {BINDING_TABLE} (
    anchor_id TEXT PRIMARY KEY,
    anchor_kind TEXT NOT NULL,
    anchor_location TEXT NOT NULL,
    production_grade INTEGER NOT NULL,
    bound_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS {BINDING_TABLE}_no_update BEFORE UPDATE ON {BINDING_TABLE}
BEGIN SELECT RAISE(ABORT, 'evidence anchor bindings are append-only: UPDATE refused'); END;
CREATE TRIGGER IF NOT EXISTS {BINDING_TABLE}_no_delete BEFORE DELETE ON {BINDING_TABLE}
BEGIN SELECT RAISE(ABORT, 'evidence anchor bindings are append-only: DELETE refused'); END;
"""


class AnchorError(ValueError):
    """A refused append, a broken anchor or a store that does not match its anchor."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceCheckpoint:
    """One anchored snapshot of the evidence held by a store.

    ``checkpoint_sha256`` is the SHA-256 of the canonical JSON of every other
    field, ``previous_checkpoint_sha256`` included, so checkpoints form a chain
    (the first one links to ``GENESIS_HASH``).
    """

    sequence: int
    attempt_seq: int
    attempt_head: str
    previous_attempt_head: str
    consumed_holdouts: tuple[dict, ...]
    experiment_ids: tuple[str, ...]
    sealed_protocols: tuple[str, ...]
    created_at: str
    previous_checkpoint_sha256: str
    checkpoint_sha256: str
    format_version: str = ANCHOR_FORMAT_VERSION
    # Random identity of the anchor, created with its first checkpoint and repeated by every later one.
    anchor_id: str = ""

    def content(self) -> dict[str, Any]:
        """Every hashed field (all except ``checkpoint_sha256``; a legacy empty ``anchor_id`` is omitted)."""

        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "checkpoint_sha256" and not (item.name == "anchor_id" and not self.anchor_id)
        }

    def computed_sha256(self) -> str:
        return checkpoint_hash(self.content())

    def to_dict(self) -> dict[str, Any]:
        return {**self.content(), "checkpoint_sha256": self.checkpoint_sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceCheckpoint:
        names = {item.name for item in fields(cls)}
        unknown = set(data) - names
        if unknown:
            raise AnchorError(f"unknown checkpoint fields: {', '.join(sorted(unknown))}")
        try:
            return cls(
                sequence=int(data["sequence"]),
                attempt_seq=int(data["attempt_seq"]),
                attempt_head=str(data["attempt_head"]),
                previous_attempt_head=str(data["previous_attempt_head"]),
                consumed_holdouts=tuple(dict(item) for item in data["consumed_holdouts"]),
                experiment_ids=tuple(str(item) for item in data["experiment_ids"]),
                sealed_protocols=tuple(str(item) for item in data["sealed_protocols"]),
                created_at=str(data["created_at"]),
                previous_checkpoint_sha256=str(data["previous_checkpoint_sha256"]),
                checkpoint_sha256=str(data["checkpoint_sha256"]),
                format_version=str(data.get("format_version", ANCHOR_FORMAT_VERSION)),
                anchor_id=str(data.get("anchor_id", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AnchorError(f"malformed checkpoint: {exc}") from exc


def checkpoint_hash(content: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in content.items() if key != "checkpoint_sha256"}
    return _sha256_text(canonical_json(payload))


@dataclass(frozen=True, slots=True)
class AnchorVerification:
    ok: bool
    errors: tuple[str, ...]
    production_grade: bool
    latest: EvidenceCheckpoint | None
    details: tuple[str, ...] = field(default_factory=tuple)


# -- anchor interface --------------------------------------------------------------


class EvidenceAnchor(ABC):
    """Append-only, hash-chained series of checkpoints kept outside the store.

    Implementations must persist each accepted checkpoint before ``append``
    returns and must never rewrite or drop an accepted one. ``production_grade``
    is True only when the underlying storage is immutable for the store's owner.
    """

    production_grade: bool = False
    kind: str = "abstract"

    @property
    def location(self) -> str:
        """Where the anchor lives (recorded in the store's binding); adapters override it."""

        return self.kind

    def anchor_id(self) -> str | None:
        """The identity created with the first checkpoint (``None`` while empty, ``""`` for a legacy anchor)."""

        items = self.checkpoints()
        return items[0].anchor_id if items else None

    @abstractmethod
    def _load(self) -> tuple[EvidenceCheckpoint, ...]:
        """Every stored checkpoint, oldest first, without chain checks."""

    @abstractmethod
    def _persist(self, checkpoint: EvidenceCheckpoint) -> None:
        """Durably append one already validated checkpoint."""

    def checkpoints(self) -> tuple[EvidenceCheckpoint, ...]:
        """Every checkpoint, oldest first; raises ``AnchorError`` when the stored chain is broken."""

        items = self._load()
        validate_chain(items)
        return items

    def latest(self) -> EvidenceCheckpoint | None:
        items = self.checkpoints()
        return items[-1] if items else None

    def append(self, checkpoint: EvidenceCheckpoint) -> None:
        latest = self.latest()
        _check_successor(latest, checkpoint)
        self._persist(checkpoint)


def _check_successor(latest: EvidenceCheckpoint | None, checkpoint: EvidenceCheckpoint) -> None:
    if checkpoint.computed_sha256() != checkpoint.checkpoint_sha256:
        raise AnchorError("checkpoint_sha256 does not match the checkpoint content")
    expected_previous = latest.checkpoint_sha256 if latest else GENESIS_HASH
    if checkpoint.previous_checkpoint_sha256 != expected_previous:
        raise AnchorError("previous_checkpoint_sha256 is not the hash of the latest anchored checkpoint")
    if latest is not None and checkpoint.anchor_id != latest.anchor_id:
        raise AnchorError("anchor_id differs from the anchor's identity")
    if latest is None and not checkpoint.anchor_id and checkpoint.format_version != LEGACY_FORMAT_VERSION:
        raise AnchorError("the first checkpoint must carry the anchor's identity (anchor_id)")
    expected_sequence = latest.sequence + 1 if latest else 1
    if checkpoint.sequence != expected_sequence:
        raise AnchorError(f"checkpoint sequence {checkpoint.sequence} is not {expected_sequence}")
    if latest is not None and checkpoint.attempt_seq < latest.attempt_seq:
        raise AnchorError(
            f"attempt_seq goes backwards ({checkpoint.attempt_seq} < {latest.attempt_seq}): "
            "the store is behind the anchor"
        )
    if latest is not None and checkpoint.attempt_seq == latest.attempt_seq and checkpoint.attempt_head != latest.attempt_head:
        raise AnchorError("same attempt_seq as the latest checkpoint with a different attempt head")


def validate_chain(items: tuple[EvidenceCheckpoint, ...]) -> None:
    previous: EvidenceCheckpoint | None = None
    for item in items:
        try:
            _check_successor(previous, item)
        except AnchorError as exc:
            raise AnchorError(f"anchor chain broken at checkpoint {item.sequence}: {exc}") from exc
        previous = item


class LocalFileAnchor(EvidenceAnchor):
    """JSON-lines file, one canonical checkpoint per line. NOT FOR PRODUCTION.

    The file is hash-chained and verified whenever it is read, so an edited,
    reordered or inserted line raises ``AnchorError``. It gives no production
    guarantee: its owner can truncate it at a line boundary, rewrite it
    entirely with a fresh consistent chain, replace it with an older copy or
    delete it. Keep production anchors in external immutable storage.
    """

    production_grade = False
    kind = "LOCAL_FILE_NOT_FOR_PRODUCTION"

    def __init__(self, path: str | Path) -> None:
        # Absolute, so the location recorded in the store's binding does not depend on the working directory.
        self.path = Path(path).resolve()

    @property
    def location(self) -> str:
        return str(self.path)

    def _load(self) -> tuple[EvidenceCheckpoint, ...]:
        if not self.path.exists():
            return ()
        data = self.path.read_bytes()
        if not data:
            return ()
        if not data.endswith(b"\n"):
            raise AnchorError(f"anchor file {self.path} ends with a partial line")
        items = []
        for number, raw in enumerate(data.split(b"\n")[:-1], start=1):
            try:
                text = raw.decode("utf-8")
                parsed = json.loads(text)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AnchorError(f"anchor file {self.path} line {number} is not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise AnchorError(f"anchor file {self.path} line {number} is not a checkpoint object")
            checkpoint = EvidenceCheckpoint.from_dict(parsed)
            if canonical_json(checkpoint.to_dict()) != text:
                raise AnchorError(f"anchor file {self.path} line {number} is not in canonical form (edited)")
            items.append(checkpoint)
        return tuple(items)

    def _persist(self, checkpoint: EvidenceCheckpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (canonical_json(checkpoint.to_dict()) + "\n").encode("utf-8")
        with self.path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def anchor_from_environment() -> LocalFileAnchor | None:
    """A ``LocalFileAnchor`` at ``$ASTRA_EVIDENCE_ANCHOR`` (resolved to an absolute path) when set, else ``None``."""

    value = os.environ.get(ENVIRONMENT_VARIABLE, "").strip()
    return LocalFileAnchor(Path(value).resolve()) if value else None


# -- reading the store ----------------------------------------------------------------


@contextmanager
def _read_only(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(database_path)
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path}")
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _store_snapshot(db: sqlite3.Connection) -> dict[str, Any]:
    seq, head = _head(db) if _table_exists(db) else (0, GENESIS_HASH)
    experiments: tuple[str, ...] = ()
    if _table_exists(db):
        experiments = tuple(
            str(row[0])
            for row in db.execute(
                f"SELECT DISTINCT experiment_id FROM {TABLE} WHERE experiment_id IS NOT NULL ORDER BY experiment_id"
            )
        )
    protocols: dict[str, dict] = {}
    if _has_table(db, "research_protocols"):
        for row in db.execute("SELECT protocol_id, plan_json FROM research_protocols ORDER BY protocol_id"):
            try:
                plan = json.loads(row["plan_json"])
            except (TypeError, json.JSONDecodeError):
                plan = {}
            protocols[str(row["protocol_id"])] = plan if isinstance(plan, dict) else {}
    holdouts: list[dict] = []
    if _has_table(db, "holdout_evaluations"):
        for row in db.execute(
            "SELECT evaluation_id, protocol_id, status FROM holdout_evaluations ORDER BY evaluation_id"
        ):
            if row["status"] not in CONSUMED_STATUSES:
                continue
            plan = protocols.get(str(row["protocol_id"]), {})
            holdout = plan.get("holdout") if isinstance(plan.get("holdout"), dict) else {}
            holdouts.append(
                {
                    "protocol_id": str(row["protocol_id"]),
                    "evaluation_id": str(row["evaluation_id"]),
                    "status": str(row["status"]),
                    "content_sha256": holdout.get("content_sha256"),
                }
            )
    return {
        "attempt_seq": seq,
        "attempt_head": head,
        "experiment_ids": experiments,
        "sealed_protocols": tuple(protocols),
        "consumed_holdouts": tuple(holdouts),
    }


def _store_bindings(db: sqlite3.Connection) -> tuple[str, ...]:
    if not _has_table(db, BINDING_TABLE):
        return ()
    return tuple(str(row[0]) for row in db.execute(f"SELECT anchor_id FROM {BINDING_TABLE} ORDER BY bound_at"))


def _store_in_use(db: sqlite3.Connection) -> bool:
    """A store with sealed plans or holdout evaluations holds evidence an empty anchor would forget."""

    for table in ("research_protocols", "holdout_evaluations"):
        if _has_table(db, table) and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            return True
    return False


def store_binding(database_path: str | Path) -> dict[str, Any] | None:
    """The anchor a store was bound to on its first anchoring (``None`` when never anchored)."""

    with _read_only(database_path) as db:
        if not _has_table(db, BINDING_TABLE):
            return None
        row = db.execute(f"SELECT * FROM {BINDING_TABLE} ORDER BY bound_at LIMIT 1").fetchone()
    return dict(row) if row else None


def _record_binding(database_path: str | Path, anchor: EvidenceAnchor, anchor_id: str) -> None:
    db = sqlite3.connect(Path(database_path), timeout=5.0)
    try:
        with db:
            db.executescript(BINDING_SCHEMA_SQL)
        with db:
            if db.execute(f"SELECT 1 FROM {BINDING_TABLE} LIMIT 1").fetchone() is None:
                db.execute(
                    f"INSERT INTO {BINDING_TABLE} VALUES (?, ?, ?, ?, ?)",
                    (anchor_id, anchor.kind, anchor.location, int(bool(anchor.production_grade)), _now()),
                )
    finally:
        db.close()


def build_checkpoint(database_path: str | Path, previous: EvidenceCheckpoint | None) -> EvidenceCheckpoint:
    """The next checkpoint for the store, chained to ``previous`` (read-only access to the store)."""

    with _read_only(database_path) as db:
        snapshot = _store_snapshot(db)
    content = {
        "sequence": previous.sequence + 1 if previous else 1,
        "attempt_seq": snapshot["attempt_seq"],
        "attempt_head": snapshot["attempt_head"],
        "previous_attempt_head": previous.attempt_head if previous else GENESIS_HASH,
        "consumed_holdouts": snapshot["consumed_holdouts"],
        "experiment_ids": snapshot["experiment_ids"],
        "sealed_protocols": snapshot["sealed_protocols"],
        "created_at": _now(),
        "previous_checkpoint_sha256": previous.checkpoint_sha256 if previous else GENESIS_HASH,
        "format_version": ANCHOR_FORMAT_VERSION if previous is None or previous.anchor_id else previous.format_version,
    }
    anchor_id = previous.anchor_id if previous else uuid.uuid4().hex
    if anchor_id:
        content["anchor_id"] = anchor_id
    return EvidenceCheckpoint(**content, checkpoint_sha256=checkpoint_hash(content))


def anchor_store(database_path: str | Path, anchor: EvidenceAnchor) -> EvidenceCheckpoint:
    """Build the store's next checkpoint and append it to ``anchor``.

    Refused (``AnchorError``) when the store does not verify against what is
    already anchored (``anchor_empty`` excepted), so a restored or rewritten
    store cannot be re-anchored silently. The first anchoring of a store binds
    it to the anchor's identity (``evidence_anchor_bindings``).
    """

    previous = anchor.latest()
    verification = verify_store_against_anchor(database_path, anchor)
    blocking = [code for code in verification.errors if code != ANCHOR_EMPTY]
    if blocking:
        raise AnchorError(f"store does not match its anchor: {', '.join(blocking)}")
    checkpoint = build_checkpoint(database_path, previous)
    anchor.append(checkpoint)
    if checkpoint.anchor_id:
        _record_binding(database_path, anchor, checkpoint.anchor_id)
    return checkpoint


def verify_store_against_anchor(database_path: str | Path, anchor: EvidenceAnchor) -> AnchorVerification:
    """Compare the store with every anchored checkpoint.

    Raises ``AnchorError`` when the anchor itself is broken. Codes:
    ``anchor_empty``, ``store_missing``, ``store_behind_anchor``,
    ``attempt_chain_diverged``, ``attempt_log_tampered``,
    ``consumed_holdout_missing``, ``sealed_protocol_missing``,
    ``experiment_missing``, and for the store's binding (Lot 4, C-3)
    ``anchor_identity_mismatch``, ``anchor_without_identity``,
    ``store_not_bound_to_anchor``, ``anchor_empty_for_used_store``.
    """

    items = anchor.checkpoints()
    production = bool(anchor.production_grade)
    latest = items[-1] if items else None
    errors: list[str] = []
    details: list[str] = []

    def report(code: str, detail: str) -> None:
        if code not in errors:
            errors.append(code)
        details.append(f"{code}: {detail}")

    if not items:
        report(ANCHOR_EMPTY, "no checkpoint anchored")
    if not Path(database_path).exists():
        report(STORE_MISSING, f"database not found: {database_path}")
        return AnchorVerification(False, tuple(errors), production, latest, tuple(details))
    with _read_only(database_path) as db:
        chain = _verify(db)
        if not chain.ok and _table_exists(db):
            for message in chain.errors:
                report(ATTEMPT_LOG_TAMPERED, message)
        elif not _table_exists(db) and any(item.attempt_seq > 0 for item in items):
            report(ATTEMPT_LOG_TAMPERED, "attempt log table is missing")
        store_seq = chain.head_seq if _table_exists(db) else 0
        hashes: dict[int, str] = {}
        if _table_exists(db):
            hashes = {int(row[0]): str(row[1]) for row in db.execute(f"SELECT seq, row_hash FROM {TABLE}")}
        snapshot = _store_snapshot(db)
        bindings = _store_bindings(db)
        in_use = _store_in_use(db)

    if not items:
        if bindings or in_use:
            report(
                ANCHOR_EMPTY_FOR_USED_STORE,
                "the store is already bound to an anchor or holds sealed plans or holdout evaluations; "
                "a fresh anchor would forget them",
            )
    else:
        anchor_id = items[0].anchor_id
        if not anchor_id:
            report(ANCHOR_WITHOUT_IDENTITY, "legacy anchor without anchor_id; start a new anchor with a new store")
        elif not bindings:
            report(STORE_NOT_BOUND_TO_ANCHOR, f"the store was never anchored to anchor {anchor_id}")
        elif any(binding != anchor_id for binding in bindings):
            report(ANCHOR_IDENTITY_MISMATCH, f"store bound to {', '.join(bindings)}, anchor is {anchor_id}")

    for item in items:
        if item.attempt_seq == 0:
            continue
        if item.attempt_seq > store_seq:
            report(
                STORE_BEHIND_ANCHOR,
                f"checkpoint {item.sequence} anchors attempt seq {item.attempt_seq}, store head is {store_seq}",
            )
        elif hashes.get(item.attempt_seq) != item.attempt_head:
            report(
                ATTEMPT_CHAIN_DIVERGED,
                f"checkpoint {item.sequence}: anchored head at seq {item.attempt_seq} is not in the store's chain",
            )

    current_holdouts = {entry["evaluation_id"]: entry for entry in snapshot["consumed_holdouts"]}
    seen: set[str] = set()
    for item in items:
        for anchored in item.consumed_holdouts:
            evaluation_id = str(anchored.get("evaluation_id"))
            current = current_holdouts.get(evaluation_id)
            anchored_status = anchored.get("status")
            if current is None:
                problem = "missing from the store"
            elif current["protocol_id"] != anchored.get("protocol_id"):
                problem = "bound to another protocol"
            elif anchored_status in FINAL_STATUSES and current["status"] != anchored_status:
                problem = f"status {anchored_status} became {current['status']}"
            else:
                continue
            if evaluation_id not in seen:
                seen.add(evaluation_id)
                report(CONSUMED_HOLDOUT_MISSING, f"holdout evaluation {evaluation_id} ({anchored_status}) {problem}")

    protocols = set(snapshot["sealed_protocols"])
    missing_protocols = sorted({pid for item in items for pid in item.sealed_protocols} - protocols)
    for protocol_id in missing_protocols:
        report(SEALED_PROTOCOL_MISSING, f"sealed protocol {protocol_id} missing from the store")

    experiments = set(snapshot["experiment_ids"])
    missing_experiments = sorted({eid for item in items for eid in item.experiment_ids} - experiments)
    for experiment_id in missing_experiments:
        report(EXPERIMENT_MISSING, f"experiment {experiment_id} missing from the attempt log")

    return AnchorVerification(not errors, tuple(errors), production, latest, tuple(details))


def consumed_holdout_fingerprints(anchor: EvidenceAnchor) -> set[str]:
    """``content_sha256`` of every holdout anchored as consumed, in any checkpoint.

    Sealing can refuse these even after the store was restored from a backup
    that predates their scoring.
    """

    return {
        str(entry["content_sha256"])
        for item in anchor.checkpoints()
        for entry in item.consumed_holdouts
        if entry.get("content_sha256")
    }
