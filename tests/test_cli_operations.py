import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import workspace_tempdir
from astra.cli import main


class CliOperationsTests(unittest.TestCase):
    def run_cli(self, env, *argv):
        output = io.StringIO()
        errors = io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(list(argv))
        text = output.getvalue() or errors.getvalue()
        return code, json.loads(text)

    def test_paper_replay_then_inspect_orders_and_controls(self):
        with workspace_tempdir() as temp:
            env = {"ASTRA_DATABASE": str(Path(temp) / "state.sqlite3")}
            code, replay = self.run_cli(env, "paper-replay")
            self.assertEqual(code, 0)
            self.assertFalse(replay["external_orders"])
            self.assertEqual(replay["replay"]["mode"], "SIMULATION")
            self.assertEqual(replay["replay"]["reconciliation_end"], "CONSISTENT")
            code, orders = self.run_cli(env, "orders")
            self.assertEqual(code, 0)
            self.assertTrue(orders["orders"])
            code, controls = self.run_cli(env, "controls", "kill", "--reason", "drill", "--operator", "alice")
            self.assertTrue(controls["kill_switch_active"])
            code, replay = self.run_cli(env, "paper-replay")
            self.assertEqual(replay["replay"]["orders_by_state"].get("FILLED", 0), 0)
            code, controls = self.run_cli(env, "controls", "release", "--reason", "drill over", "--operator", "alice")
            self.assertFalse(controls["kill_switch_active"])

    def test_ml_baseline_logs_experiment_and_registers_inactive_candidate(self):
        with workspace_tempdir() as temp:
            env = {"ASTRA_DATABASE": str(Path(temp) / "state.sqlite3")}
            code, result = self.run_cli(env, "ml-baseline")
            self.assertEqual(code, 0)
            self.assertEqual(result["registry_status"], "CANDIDATE")
            self.assertIn("missing_holdout_evaluation", result["promotion_blockers"])
            self.assertEqual(result["dataset"]["source_type"], "SYNTHETIC")
            code, models = self.run_cli(env, "models")
            self.assertEqual(models["models"][0]["status"], "CANDIDATE")
            self.assertEqual(models["experiments"][0]["decision"], result["decision"])

    def test_paper_replay_accepts_strategy_families(self):
        with workspace_tempdir() as temp:
            env = {"ASTRA_DATABASE": str(Path(temp) / "state.sqlite3")}
            for strategy in ("momentum", "breakout"):
                code, replay = self.run_cli(env, "paper-replay", "--strategy", strategy)
                self.assertEqual(code, 0)
                self.assertEqual(replay["replay"]["reconciliation_end"], "CONSISTENT")

    def test_paper_replay_with_csv_requires_source_type(self):
        with workspace_tempdir() as temp:
            env = {"ASTRA_DATABASE": str(Path(temp) / "state.sqlite3")}
            code, error = self.run_cli(env, "paper-replay", "--csv", "data/synthetic_eurusd_m15.csv")
            self.assertEqual(code, 1)
            self.assertIn("--source-type", error["reason"])


if __name__ == "__main__":
    unittest.main()
