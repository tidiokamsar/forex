"""Consistent SQLite backups and verified restores.

A backup uses SQLite's online backup API (consistent even while the service
writes), is checked with ``PRAGMA integrity_check``, and gets a manifest with
its SHA-256. A restore verifies hash and integrity before replacing anything,
keeps the replaced file aside, and marks the restored store as requiring
reconciliation: restored local state is never trusted against the broker
until a reconciliation is consistent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .execution import ExecutionStore, FreezeReason, TradingControls
from .governance import Action, Actor, PermissionGuard
from .logs import get_logger, log_event

_LOG = get_logger("backup")
MANIFEST_SUFFIX = ".manifest.json"


class _TolerantAuditSink:
    """Audits into the target store when it is readable; otherwise only logs."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def audit(self, event_type: str, details: dict) -> None:
        try:
            if self._path.exists():
                ExecutionStore(self._path).audit(event_type, details)
                return
        except sqlite3.Error as exc:
            log_event(_LOG, "backup.audit_target_unreadable", level=logging.WARNING, error=str(exc))
        log_event(_LOG, event_type, **details)


class BackupError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BackupRecord:
    path: str
    sha256: str
    created_at: str
    source: str
    size_bytes: int
    integrity: str
    tables: dict[str, int]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect(path: Path) -> tuple[str, dict[str, int]]:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        tables = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        db.close()
    return integrity, tables


def create_backup(database: str | Path, backup_dir: str | Path, *, keep: int = 14) -> BackupRecord:
    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
        raise BackupError("keep must be an integer of at least 1")
    source = Path(database)
    if not source.exists():
        raise BackupError(f"database not found: {source}")
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"astra-{stamp}.sqlite3"
    # Created empty and owner-only first, so the copy never follows the umask.
    os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600))
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    integrity, tables = _inspect(target)
    if integrity != "ok":
        target.unlink(missing_ok=True)
        raise BackupError(f"backup failed integrity check: {integrity}")
    record = BackupRecord(
        path=str(target),
        sha256=_sha256(target),
        created_at=datetime.now(UTC).isoformat(),
        source=str(source),
        size_bytes=target.stat().st_size,
        integrity=integrity,
        tables=tables,
    )
    Path(str(target) + MANIFEST_SUFFIX).write_text(json.dumps(asdict(record), indent=2, sort_keys=True), encoding="utf-8")
    _prune(directory, keep)
    log_event(_LOG, "backup.created", path=record.path, sha256=record.sha256, tables=tables)
    return record


def verify_backup(backup: str | Path) -> BackupRecord:
    path = Path(backup)
    manifest_path = Path(str(path) + MANIFEST_SUFFIX)
    if not path.exists() or not manifest_path.exists():
        raise BackupError("backup file or manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = _sha256(path)
    if actual != manifest.get("sha256"):
        raise BackupError("backup SHA-256 does not match its manifest")
    integrity, tables = _inspect(path)
    if integrity != "ok":
        raise BackupError(f"backup failed integrity check: {integrity}")
    if tables != manifest.get("tables"):
        raise BackupError("backup table counts do not match its manifest")
    return BackupRecord(**manifest)


def restore_backup(
    backup: str | Path,
    database: str | Path,
    *,
    actor: Actor,
    reason: str,
    stale_after_seconds: float = 120.0,
) -> dict:
    """Replace ``database`` with a verified backup. Requires a human operator.

    Order of operations, so that no failure can leave the target missing or
    unfrozen: (1) verify the backup actually given, (2) refuse while a service
    heartbeat is alive, (3) copy it to a staging file and verify the copy,
    (4) freeze every account inside the staging copy, (5) only then move the
    live database aside and swap the staging file in. Any failure after the
    live file was moved puts it back.
    """

    target = Path(database)
    source = Path(backup)
    # A corrupted target is the usual reason to restore: auditing into it must not block the restore.
    PermissionGuard(_TolerantAuditSink(target)).require(actor, Action.RESTORE_BACKUP, target=str(source))
    if not reason.strip():
        raise BackupError("a restore reason is required")
    record = verify_backup(source)
    if target.exists():
        from .service import service_status

        try:
            statuses = service_status(ExecutionStore(target), stale_after_seconds=stale_after_seconds)
        except sqlite3.Error:
            statuses = []  # unreadable target, or no service_state table: no live heartbeat to honour
        alive = [status["account_ref"] for status in statuses if status["alive"]]
        if alive:
            raise BackupError(f"a trading service is still running for {alive}; stop it before restoring")

    staging = target.with_name(target.name + ".restoring")
    staging.unlink(missing_ok=True)
    try:
        shutil.copy2(source, staging)
        if _sha256(staging) != record.sha256:
            raise BackupError("copied backup does not match its manifest")
        staged_store = ExecutionStore(staging)
        staged_store.initialize()
        # Freeze every account the restored store knows about, before it goes live.
        accounts = staged_store.account_refs()
        # Heartbeats copied from a backup belong to a service that is not running now.
        for key, state, _updated in staged_store.states_with_prefix("service:"):
            if state.get("status") in ("running", "degraded"):
                staged_store.put_state(key, {**state, "status": "stopped_by_restore"})
        for account_ref in accounts:
            TradingControls(staged_store, scope=account_ref).freeze(
                FreezeReason.RECONCILIATION_REQUIRED, actor=actor.actor_id, context="restored_from_backup"
            )
    except BaseException:
        staging.unlink(missing_ok=True)
        raise

    previous = None
    if target.exists():
        previous = target.with_name(f"{target.name}.pre-restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}")
        shutil.move(str(target), previous)
    try:
        staging.replace(target)
    except BaseException:
        if previous is not None and not target.exists():
            shutil.move(str(previous), target)
        staging.unlink(missing_ok=True)
        raise

    previous_text = str(previous) if previous else None
    store = ExecutionStore(target)
    store.audit(
        "backup.restored",
        {"actor": actor.actor_id, "reason": reason, "backup": str(source), "sha256": record.sha256,
         "previous_saved_as": previous_text, "accounts_frozen": accounts},
    )
    log_event(_LOG, "backup.restored", backup=str(source), sha256=record.sha256, previous=previous_text, accounts=accounts)
    return {"restored_from": str(source), "sha256": record.sha256, "previous_saved_as": previous_text,
            "accounts_frozen_until_reconciled": accounts}


def _prune(directory: Path, keep: int) -> None:
    backups = sorted(directory.glob("astra-*.sqlite3"))
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink(missing_ok=True)
        Path(str(old) + MANIFEST_SUFFIX).unlink(missing_ok=True)
