import dataclasses
import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import workspace_tempdir
from astra import attempts
from astra.anchoring import (
    AnchorError,
    EvidenceAnchor,
    LocalFileAnchor,
    anchor_from_environment,
    anchor_store,
    build_checkpoint,
    checkpoint_hash,
    consumed_holdout_fingerprints,
    verify_store_against_anchor,
)
from astra.backup import create_backup, restore_backup
from astra.storage import BacktestStore
from astra.strategy_validation import evaluate_strategy_holdout_once
from test_execution import OPERATOR
from test_strategy_validation import BARS, PROTOCOL, evaluate, overfitting_double


class AnchoringTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        self.root = Path(self._tmp.__enter__())
        self.database = self.root / "astra.sqlite3"
        self.store = BacktestStore(self.database)
        self.store.initialize()
        self.anchor = LocalFileAnchor(self.root / "anchor" / "evidence.jsonl")

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def research(self, holdout=True):
        with overfitting_double():
            report = evaluate(store=self.store)
            if holdout:
                evaluate_strategy_holdout_once(BARS, report=report, protocol=PROTOCOL, store=self.store)
        return report


class CheckpointChainTests(AnchoringTestCase):
    def test_local_adapter_is_not_production_grade(self):
        self.assertFalse(self.anchor.production_grade)
        self.assertEqual(self.anchor.kind, "LOCAL_FILE_NOT_FOR_PRODUCTION")
        self.assertTrue(issubclass(LocalFileAnchor, EvidenceAnchor))
        self.assertIsNone(self.anchor.latest())
        self.assertEqual(self.anchor.checkpoints(), ())

    def test_checkpoints_chain_and_bad_appends_are_refused(self):
        first = anchor_store(self.database, self.anchor)
        self.assertEqual((first.sequence, first.attempt_seq), (1, 0))
        self.assertEqual(first.previous_checkpoint_sha256, attempts.GENESIS_HASH)
        self.assertEqual(first.checkpoint_sha256, checkpoint_hash(first.content()))
        self.research(holdout=False)
        second = anchor_store(self.database, self.anchor)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.previous_checkpoint_sha256, first.checkpoint_sha256)
        self.assertEqual(second.previous_attempt_head, first.attempt_head)
        self.assertGreater(second.attempt_seq, 0)
        self.assertEqual(second.attempt_head, attempts.AttemptLog(self.database).chain_head()["row_hash"])
        self.assertEqual(self.anchor.checkpoints(), (first, second))
        self.assertEqual(LocalFileAnchor(self.anchor.path).latest(), second)

        # Not linked to the latest checkpoint.
        stale = build_checkpoint(self.database, first)
        with self.assertRaisesRegex(AnchorError, "previous_checkpoint_sha256"):
            self.anchor.append(stale)
        # attempt_seq going backwards (correctly linked and hashed otherwise).
        following = build_checkpoint(self.database, second)
        content = {**following.content(), "attempt_seq": second.attempt_seq - 1}
        backwards = dataclasses.replace(
            following, attempt_seq=content["attempt_seq"], checkpoint_sha256=checkpoint_hash(content)
        )
        with self.assertRaisesRegex(AnchorError, "backwards"):
            self.anchor.append(backwards)
        # Content not matching its hash.
        forged = dataclasses.replace(build_checkpoint(self.database, second), sealed_protocols=())
        with self.assertRaisesRegex(AnchorError, "does not match"):
            self.anchor.append(forged)
        self.assertEqual(len(self.anchor.checkpoints()), 2)

    def test_tampered_anchor_file_is_detected(self):
        anchor_store(self.database, self.anchor)
        self.research(holdout=True)
        anchor_store(self.database, self.anchor)
        lines = self.anchor.path.read_text(encoding="utf-8").splitlines(keepends=True)

        edited = json.loads(lines[1])
        edited["consumed_holdouts"] = []
        cases = {
            "edited line": [lines[0], json.dumps(edited, sort_keys=True, separators=(",", ":")) + "\n"],
            "reordered": [lines[1], lines[0]],
            "first line removed": [lines[1]],
            "partial line": [lines[0], lines[1].rstrip("\n")],
            "garbage": [lines[0], "not json\n"],
            "non-canonical": [lines[0], json.dumps(json.loads(lines[1]), indent=1).replace("\n", " ") + "\n"],
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.anchor.path.write_text("".join(content), encoding="utf-8", newline="\n")
                with self.assertRaises(AnchorError):
                    self.anchor.checkpoints()
                with self.assertRaises(AnchorError):
                    verify_store_against_anchor(self.database, self.anchor)

    def test_environment_is_the_only_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(anchor_from_environment())
        with patch.dict(os.environ, {"ASTRA_EVIDENCE_ANCHOR": "  "}, clear=True):
            self.assertIsNone(anchor_from_environment())
        with patch.dict(os.environ, {"ASTRA_EVIDENCE_ANCHOR": str(self.anchor.path)}, clear=True):
            anchor = anchor_from_environment()
        self.assertIsInstance(anchor, LocalFileAnchor)
        self.assertEqual(anchor.path, self.anchor.path)


class StoreVerificationTests(AnchoringTestCase):
    def test_empty_anchor_is_reported(self):
        result = verify_store_against_anchor(self.database, self.anchor)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, ("anchor_empty",))
        self.assertIsNone(result.latest)
        self.assertFalse(result.production_grade)

    def test_clean_store_verifies(self):
        report = self.research(holdout=True)
        checkpoint = anchor_store(self.database, self.anchor)
        self.assertEqual(checkpoint.sealed_protocols, (report.plan_sha256,))
        (holdout,) = checkpoint.consumed_holdouts
        self.assertEqual(holdout["protocol_id"], report.plan_sha256)
        self.assertEqual(holdout["status"], "COMPLETED")
        db =sqlite3.connect(self.database)
        try:
            plan = json.loads(db.execute("SELECT plan_json FROM research_protocols").fetchone()[0])
        finally:
            db.close()
        self.assertEqual(holdout["content_sha256"], plan["holdout"]["content_sha256"])
        self.assertEqual(consumed_holdout_fingerprints(self.anchor), {plan["holdout"]["content_sha256"]})
        result = verify_store_against_anchor(self.database, self.anchor)
        self.assertTrue(result.ok, result.details)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.latest, checkpoint)
        self.assertFalse(result.production_grade)
        # More attempts after the anchor do not break verification.
        with overfitting_double():
            evaluate(store=self.store)
        self.assertTrue(verify_store_against_anchor(self.database, self.anchor).ok)

    def test_restored_older_backup_is_detected(self):
        backups = self.root / "backups"
        before_research = create_backup(self.database, backups)
        with overfitting_double():
            report = evaluate(store=self.store)
        before_holdout = create_backup(self.database, backups)
        with overfitting_double():
            evaluate_strategy_holdout_once(BARS, report=report, protocol=PROTOCOL, store=self.store)
        anchor_store(self.database, self.anchor)
        fingerprints = consumed_holdout_fingerprints(self.anchor)
        self.assertEqual(len(fingerprints), 1)

        restore_backup(before_holdout.path, self.database, actor=OPERATOR, reason="restore drill")
        result = verify_store_against_anchor(self.database, self.anchor)
        self.assertFalse(result.ok)
        self.assertIn("store_behind_anchor", result.errors)
        self.assertIn("consumed_holdout_missing", result.errors)
        self.assertNotIn("sealed_protocol_missing", result.errors)
        self.assertNotIn("attempt_log_tampered", result.errors)
        # The spent holdout stays known to the anchor after the restore.
        self.assertEqual(consumed_holdout_fingerprints(self.anchor), fingerprints)
        with self.assertRaisesRegex(AnchorError, "store_behind_anchor"):
            anchor_store(self.database, self.anchor)

        restore_backup(before_research.path, self.database, actor=OPERATOR, reason="restore drill")
        result = verify_store_against_anchor(self.database, self.anchor)
        self.assertIn("store_behind_anchor", result.errors)
        self.assertIn("consumed_holdout_missing", result.errors)
        self.assertIn("sealed_protocol_missing", result.errors)

    def rewrite_chain(self, *, restore_triggers):
        """Owner-level attack: drop the triggers and rewrite every row with a consistent chain."""

        db = sqlite3.connect(self.database, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            for name in attempts._TRIGGERS:
                db.execute(f"DROP TRIGGER IF EXISTS {name}")
            rows = [dict(row) for row in db.execute(f"SELECT * FROM {attempts.TABLE} ORDER BY seq")]
            db.execute(f"DELETE FROM {attempts.TABLE}")
            previous = attempts.GENESIS_HASH
            columns = (*attempts._COLUMNS, "row_hash")
            for row in rows:
                row["recorded_at"] = "2020-01-01T00:00:00+00:00"
                row["prev_hash"] = previous
                row["row_hash"] = attempts.row_hash(row)
                db.execute(
                    f"INSERT INTO {attempts.TABLE} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    tuple(row[name] for name in columns),
                )
                previous = row["row_hash"]
            if restore_triggers:
                db.executescript(attempts.SCHEMA_SQL)
        finally:
            db.close()

    def test_rewritten_chain_is_detected_as_diverged(self):
        self.research(holdout=True)
        anchor_store(self.database, self.anchor)
        self.rewrite_chain(restore_triggers=True)
        self.assertTrue(attempts.verify_attempt_chain(self.database).ok)  # invisible from inside the file
        result = verify_store_against_anchor(self.database, self.anchor)
        self.assertFalse(result.ok)
        self.assertIn("attempt_chain_diverged", result.errors)
        self.assertNotIn("store_behind_anchor", result.errors)
        self.assertNotIn("attempt_log_tampered", result.errors)
        with self.assertRaisesRegex(AnchorError, "attempt_chain_diverged"):
            anchor_store(self.database, self.anchor)

    def test_rewritten_chain_without_triggers_is_tampered_and_diverged(self):
        self.research(holdout=False)
        anchor_store(self.database, self.anchor)
        self.rewrite_chain(restore_triggers=False)
        result = verify_store_against_anchor(self.database, self.anchor)
        self.assertIn("attempt_log_tampered", result.errors)
        self.assertIn("attempt_chain_diverged", result.errors)

    def test_edited_row_is_tampered(self):
        self.research(holdout=False)
        anchor_store(self.database, self.anchor)
        db = sqlite3.connect(self.database, isolation_level=None)
        try:
            db.execute(f"DROP TRIGGER {attempts.TABLE}_no_update")
            db.execute(f"UPDATE {attempts.TABLE} SET reason = 'edited' WHERE seq = 1")
        finally:
            db.close()
        result = verify_store_against_anchor(self.database, self.anchor)
        self.assertIn("attempt_log_tampered", result.errors)

    def test_missing_store_is_reported(self):
        anchor_store(self.database, self.anchor)
        result = verify_store_against_anchor(self.root / "absent.sqlite3", self.anchor)
        self.assertEqual(result.errors, ("store_missing",))


if __name__ == "__main__":
    unittest.main()
