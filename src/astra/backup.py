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
    source = Path(database)
    if not source.exists():
        raise BackupError(f"database not found: {source}")
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"astra-{stamp}.sqlite3"
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


def restore_backup(backup: str | Path, database: str | Path, *, actor: Actor, reason: str) -> dict:
    """Replace ``database`` with a verified backup. Requires a human operator.

    The service must be stopped first. The previous file is kept next to the
    target, and the restored store is frozen until reconciliation.
    """

    target = Path(database)
    PermissionGuard(ExecutionStore(target) if target.exists() else None).require(
        actor, Action.RESTORE_BACKUP, target=str(backup)
    )
    if not reason.strip():
        raise BackupError("a restore reason is required")
    record = verify_backup(backup)
    previous = None
    if target.exists():
        previous = target.with_name(f"{target.name}.pre-restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}")
        shutil.move(str(target), previous)
    staging = target.with_name(target.name + ".restoring")
    shutil.copy2(record.path, staging)
    if _sha256(staging) != record.sha256:
        staging.unlink(missing_ok=True)
        if previous is not None:
            shutil.move(str(previous), target)
        raise BackupError("copied backup does not match its manifest; previous database put back")
    staging.replace(target)
    store = ExecutionStore(target)
    store.initialize()
    # Freeze every account the restored store knows about; each one's gateway
    # lifts its own freeze with a consistent reconciliation.
    accounts = store.account_refs()
    for account_ref in accounts:
        TradingControls(store, scope=account_ref).freeze(
            FreezeReason.RECONCILIATION_REQUIRED, actor=actor.actor_id, context="restored_from_backup"
        )
    previous_text = str(previous) if previous else None
    store.audit(
        "backup.restored",
        {"actor": actor.actor_id, "reason": reason, "backup": record.path, "sha256": record.sha256,
         "previous_saved_as": previous_text, "accounts_frozen": accounts},
    )
    log_event(_LOG, "backup.restored", backup=record.path, sha256=record.sha256, previous=previous_text, accounts=accounts)
    return {"restored_from": record.path, "sha256": record.sha256, "previous_saved_as": previous_text,
            "accounts_frozen_until_reconciled": accounts}


def _prune(directory: Path, keep: int) -> None:
    backups = sorted(directory.glob("astra-*.sqlite3"))
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink(missing_ok=True)
        Path(str(old) + MANIFEST_SUFFIX).unlink(missing_ok=True)
