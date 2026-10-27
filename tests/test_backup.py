import contextlib
import io
import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from astra.backup import BackupError, create_backup, restore_backup, verify_backup
from astra.brokers.simulated import SimulatedBroker
from astra.cli import main
from astra.execution import ExecutionStore, OrderGateway
from astra.governance import Actor, PermissionDenied, Role
from test_execution import OPERATOR, SYSTEM, ExecutionTestCase

ACCOUNT = "SIM-****0001"


class BackupRestoreTests(ExecutionTestCase):
    """F-04: backup, verified restore, and mandatory reconciliation after restore (simulator only)."""

    def setUp(self):
        super().setUp()
        self.gateway = OrderGateway(store=self.store, broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        self.backups = Path(self.tmp) / "backups"

    def order_count(self, path=None):
        db = sqlite3.connect(path or self.store.path)
        try:
            return db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        finally:
            db.close()

    def test_backup_is_consistent_and_verifiable(self):
        self.gateway.submit(self.command(), SYSTEM)
        record = create_backup(self.store.path, self.backups)
        self.assertEqual(record.integrity, "ok")
        self.assertEqual(record.tables["orders"], 1)
        self.assertEqual(verify_backup(record.path).sha256, record.sha256)

    def test_tampered_backup_is_refused_and_target_untouched(self):
        self.gateway.submit(self.command(), SYSTEM)
        record = create_backup(self.store.path, self.backups)
        with open(record.path, "r+b") as handle:
            handle.seek(200)
            handle.write(b"\x00\xff\x00\xff")
        with self.assertRaises(BackupError):
            restore_backup(record.path, self.store.path, actor=OPERATOR, reason="drill")
        self.assertEqual(self.order_count(), 1)

    def test_restore_brings_back_known_state_and_requires_reconciliation(self):
        self.gateway.submit(self.command(), SYSTEM)
        record = create_backup(self.store.path, self.backups)
        result = restore_backup(record.path, self.store.path, actor=OPERATOR, reason="restore drill")
        self.assertEqual(result["accounts_frozen_until_reconciled"], [ACCOUNT])
        self.assertTrue(Path(result["previous_saved_as"]).exists())
        restored = OrderGateway(store=ExecutionStore(self.store.path), broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        self.assertIn("reconciliation_required", restored.controls.freezes())
        blocked = restored.submit(self.command(), SYSTEM)
        self.assertIn("entries_frozen:reconciliation_required", blocked.reason_codes)
        # The broker still holds exactly the position the backup knows about.
        self.assertEqual(restored.reconcile().status, "CONSISTENT")
        self.assertEqual(restored.controls.freezes(), ())

    def test_stale_backup_is_caught_by_reconciliation(self):
        record = create_backup(self.store.path, self.backups)
        self.gateway.submit(self.command(), SYSTEM)  # happens after the backup
        restore_backup(record.path, self.store.path, actor=OPERATOR, reason="restore older state")
        restored = OrderGateway(store=ExecutionStore(self.store.path), broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        result = restored.reconcile()
        self.assertEqual(result.status, "DIVERGENT")
        self.assertTrue(any(item.startswith("position_mismatch:EURUSD") for item in result.divergences))

    def test_restore_requires_a_human(self):
        record = create_backup(self.store.path, self.backups)
        for role in (Role.SYSTEM, Role.RESEARCH_AGENT, Role.LLM):
            with self.assertRaises(PermissionDenied):
                restore_backup(record.path, self.store.path, actor=Actor("bot", role), reason="x")

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX only")
    def test_backup_file_is_owner_only_whatever_the_umask(self):
        previous = os.umask(0o022)
        try:
            record = create_backup(self.store.path, self.backups)
        finally:
            os.umask(previous)
        self.assertEqual(Path(record.path).stat().st_mode & 0o777, 0o600)

    def test_retention_keeps_the_newest(self):
        for _ in range(4):
            create_backup(self.store.path, self.backups, keep=2)
        self.assertEqual(len(list(self.backups.glob("astra-*.sqlite3"))), 2)

    def test_cli_create_and_verify(self):
        env = {"ASTRA_DATABASE": str(self.store.path), "ASTRA_BACKUP_DIR": str(self.backups)}
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(main(["backup", "create"]), 0)
        path = json.loads(output.getvalue())["backup"]["path"]
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(main(["backup", "verify", path]), 0)
        self.assertEqual(json.loads(output.getvalue())["verified"]["integrity"], "ok")


class SimulatedBrokerAlias(SimulatedBroker):
    pass


if __name__ == "__main__":
    unittest.main()
