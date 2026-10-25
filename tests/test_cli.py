import contextlib
import csv
import io
import json
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from astra.cli import main
from _support import workspace_tempdir


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_status_reports_simulation_and_no_broker(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
            result = main(["status"])
        self.assertEqual(result, 0)
        status = json.loads(output.getvalue())
        self.assertEqual(status["mode"], "SIMULATION")
        self.assertTrue(status["live_locked"])
        self.assertIsNone(status["broker_adapter"])
        self.assertFalse(status["external_orders"])

    def test_demo_runs_and_persists_synthetic_dataset_locally(self):
        with workspace_tempdir() as temp:
            temp_path = Path(temp)
            data_path = temp_path / "data"
            data_path.mkdir()
            (data_path / "synthetic_eurusd_m15.csv").write_text(
                "timestamp,instrument,open,high,low,close\nnot-the-bundled-fixture\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            env = {
                "ASTRA_DATA_DIR": str(data_path),
                "ASTRA_DATABASE": str(temp_path / "state.sqlite3"),
            }
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
                result = main(["demo"])
            self.assertEqual(result, 0, output.getvalue())
            response = json.loads(output.getvalue())
            self.assertEqual(response["status"], "completed")
            self.assertEqual(response["mode"], "SIMULATION")
            self.assertEqual(response["dataset"]["source_type"], "SYNTHETIC")
            self.assertEqual(
                response["dataset"]["sha256"],
                "16402dc19dc55033e851b1f796aed2a872a19d0a9648ede39f1dfb59ab5d5ef8",
            )
            self.assertGreater(response["dataset"]["observations"], 0)
            self.assertFalse(response["external_orders"])
            self.assertTrue((temp_path / "state.sqlite3").exists())

    def test_walk_forward_cli_seals_plan_and_keeps_holdout_reserved_by_default(self):
        with workspace_tempdir() as temp:
            temp_path = Path(temp)
            database_path = temp_path / "state.sqlite3"
            output = io.StringIO()
            env = {"ASTRA_DATABASE": str(database_path)}
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
                result = main(
                    [
                        "walk-forward",
                        "--csv",
                        str(PROJECT_ROOT / "data" / "synthetic_eurusd_m15.csv"),
                        "--source-type",
                        "SYNTHETIC",
                        "--hypothesis",
                        "Short SMA crossovers persist after estimated costs.",
                    ]
                )

            self.assertEqual(result, 0, output.getvalue())
            response = json.loads(output.getvalue())
            self.assertEqual(response["report"]["holdout_status"], "RESERVED_NOT_EVALUATED")
            self.assertEqual(
                response["holdout_persistence"],
                "SQLITE_ONCE_PER_CANONICAL_MARKET_BAR_IDENTITY",
            )
            self.assertEqual(response["holdout_lock_scope"], "CONFIGURED_LOCAL_SQLITE_STORE")
            self.assertIsNone(response["holdout_evaluation"])
            self.assertEqual(response["protocol_id"], response["report"]["plan_sha256"])
            self.assertIn("exploratory", response["exploratory_warning"].lower())
            self.assertFalse(response["external_orders"])
            self.assertTrue(database_path.exists())

    def test_walk_forward_cli_rejects_a_second_plan_for_the_same_dataset(self):
        with workspace_tempdir() as temp:
            temp_path = Path(temp)
            env = {"ASTRA_DATABASE": str(temp_path / "state.sqlite3")}
            base_argv = [
                "walk-forward",
                "--csv",
                str(PROJECT_ROOT / "data" / "synthetic_eurusd_m15.csv"),
                "--source-type",
                "SYNTHETIC",
                "--hypothesis",
                "Short SMA crossovers persist after estimated costs.",
            ]
            first_output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(first_output):
                first_result = main(base_argv)
            self.assertEqual(first_result, 0, first_output.getvalue())

            second_output = io.StringIO()
            errors = io.StringIO()
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(
                second_output
            ), contextlib.redirect_stderr(errors):
                second_result = main([*base_argv, "--candidate", "4:9"])

            self.assertEqual(second_result, 1)
            self.assertEqual(second_output.getvalue(), "")
            self.assertIn("already sealed by another protocol", errors.getvalue())

    def test_walk_forward_cli_rejects_reformatted_bars_with_same_canonical_identity(self):
        with workspace_tempdir() as temp:
            temp_path = Path(temp)
            original_csv = PROJECT_ROOT / "data" / "synthetic_eurusd_m15.csv"
            alternate_csv = temp_path / "same-bars-different-representation.csv"
            with original_csv.open("r", encoding="utf-8", newline="") as source:
                rows = list(csv.reader(source))
            offset = timezone(timedelta(hours=5))
            for row in rows[1:]:
                timestamp = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                row[0] = timestamp.astimezone(offset).isoformat(timespec="seconds")
                row[1] = f" {row[1].lower()} "
            with alternate_csv.open("w", encoding="utf-8", newline="") as target:
                csv.writer(target, lineterminator="\n").writerows(rows)
            self.assertNotEqual(original_csv.read_bytes(), alternate_csv.read_bytes())

            env = {"ASTRA_DATABASE": str(temp_path / "state.sqlite3")}
            base_argv = [
                "walk-forward",
                "--csv",
                str(original_csv),
                "--source-type",
                "SYNTHETIC",
                "--hypothesis",
                "Short SMA crossovers persist after estimated costs.",
            ]
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
                first_result = main(base_argv)
            self.assertEqual(first_result, 0, output.getvalue())

            alternate_argv = list(base_argv)
            alternate_argv[alternate_argv.index(str(original_csv))] = str(alternate_csv)
            second_output = io.StringIO()
            errors = io.StringIO()
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(
                second_output
            ), contextlib.redirect_stderr(errors):
                second_result = main(alternate_argv)

            self.assertEqual(second_result, 1)
            self.assertEqual(second_output.getvalue(), "")
            self.assertIn("already sealed by another protocol", errors.getvalue())

    def test_holdout_cli_consumes_once_and_returns_saved_report_on_retry(self):
        with workspace_tempdir() as temp:
            temp_path = Path(temp)
            output_values = []
            env = {"ASTRA_DATABASE": str(temp_path / "state.sqlite3")}
            argv = [
                "walk-forward",
                "--csv",
                str(PROJECT_ROOT / "data" / "synthetic_eurusd_m15.csv"),
                "--source-type",
                "SYNTHETIC",
                "--hypothesis",
                "Short SMA crossovers persist after estimated costs.",
                "--evaluate-holdout",
            ]
            for _ in range(2):
                output = io.StringIO()
                with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
                    result = main(argv)
                self.assertEqual(result, 0, output.getvalue())
                output_values.append(json.loads(output.getvalue())["holdout_evaluation"])

            first, retry = output_values
            self.assertEqual(first["status"], "COMPLETED")
            self.assertEqual(first["dataset_sha256"], retry["dataset_sha256"])
            self.assertEqual(first["evaluated_at"], retry["evaluated_at"])
            reference = first["simple_reference"]
            self.assertEqual(reference["start"], first["holdout_start"])
            self.assertEqual(reference["end"], first["holdout_end"])
            self.assertEqual(reference["observations"], first["holdout_observations"])

    def test_live_configuration_fails_closed(self):
        output = io.StringIO()
        errors = io.StringIO()
        with patch.dict(os.environ, {"ASTRA_MODE": "LIVE"}, clear=True):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = main(["status"])
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(json.loads(errors.getvalue())["live_locked"])

    def test_raw_backtest_requires_explicit_exploratory_opt_in_and_warns_about_holdout(self):
        with workspace_tempdir() as temp:
            temp_path = Path(temp)
            csv_path = temp_path / "history.csv"
            shutil.copyfile(PROJECT_ROOT / "data" / "synthetic_eurusd_m15.csv", csv_path)
            env = {"ASTRA_DATABASE": str(temp_path / "state.sqlite3")}
            base_argv = [
                "backtest",
                "--csv",
                str(csv_path),
                "--source-type",
                "SYNTHETIC",
            ]
            errors = io.StringIO()
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(errors):
                with self.assertRaises(SystemExit) as missing_opt_in:
                    main(base_argv)
            self.assertEqual(missing_opt_in.exception.code, 2)

            output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
                result = main([*base_argv, "--exploratory"])
            self.assertEqual(result, 0, output.getvalue())
            response = json.loads(output.getvalue())
            self.assertEqual(
                response["result"]["evaluation_classification"],
                "EXPLORATORY_FULL_HISTORY_NON_PROMOTABLE",
            )
            self.assertIn("complete CSV history", response["exploratory_warning"])


if __name__ == "__main__":
    unittest.main()
