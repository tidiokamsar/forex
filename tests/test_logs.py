import io
import json
import logging
import unittest

from _support import workspace_tempdir
from astra.logs import configure_logging, correlation, get_logger, log_event, redact
from test_execution import SYSTEM, ExecutionTestCase


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class JsonLoggingTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        configure_logging(level="DEBUG", stream=self.stream)
        self.addCleanup(configure_logging, level="WARNING", stream=io.StringIO())

    def test_lines_are_json_with_utc_timestamp_and_correlation(self):
        with correlation("corr-42"):
            log_event(get_logger("t"), "unit.event", "hello", answer=42)
        entry = records(self.stream)[-1]
        self.assertEqual(entry["event"], "unit.event")
        self.assertEqual(entry["correlation_id"], "corr-42")
        self.assertEqual(entry["fields"]["answer"], 42)
        self.assertTrue(entry["ts"].endswith("+00:00"))

    def test_secret_like_fields_are_redacted_recursively(self):
        log_event(get_logger("t"), "auth", access_token="tok", nested={"client_secret": "s", "ok": 1}, items=[{"password": "p"}])
        text = self.stream.getvalue()
        for value in ('"tok"', '"s"', '"p"'):
            self.assertNotIn(value, text)
        self.assertEqual(redact({"apiKey": "x", "name": "n"}), {"apiKey": "****", "name": "n"})

    def test_rotating_file_handler_writes_jsonl(self):
        with workspace_tempdir() as temp:
            configure_logging(level="INFO", log_dir=temp, stream=io.StringIO())
            log_event(get_logger("t"), "file.event")
            for handler in logging.getLogger("astra").handlers:
                handler.flush()
            with open(f"{temp}/astra.jsonl", encoding="utf-8") as handle:
                self.assertEqual(json.loads(handle.readline())["event"], "file.event")
            configure_logging(level="WARNING", stream=io.StringIO())

    def test_exceptions_are_captured(self):
        try:
            raise ValueError("boom")
        except ValueError:
            get_logger("t").exception("failed", extra={"event": "unit.failure"})
        entry = records(self.stream)[-1]
        self.assertIn("ValueError: boom", entry["exception"])


class GatewayLoggingTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.stream = io.StringIO()
        configure_logging(level="INFO", stream=self.stream)
        self.addCleanup(configure_logging, level="WARNING", stream=io.StringIO())

    def test_every_order_outcome_is_logged_with_its_correlation_id(self):
        accepted = self.gateway.submit(self.command(), SYSTEM)
        self.broker.set_quote("EURUSD", 1.1000, 1.1010)
        rejected = self.gateway.submit(self.command(), SYSTEM)
        results = [r for r in records(self.stream) if r["event"] == "order.result"]
        self.assertEqual([r["fields"]["intent_id"] for r in results], [accepted.intent_id, rejected.intent_id])
        self.assertEqual(results[0]["correlation_id"], "corr-1")
        self.assertEqual(results[1]["level"], "WARNING")
        self.assertIn("spread_excessive_or_unknown", results[1]["fields"]["reasons"])

    def test_freezes_and_reconciliation_are_logged(self):
        self.broker.disconnect()
        self.gateway.check_connection()
        self.gateway.reconcile()
        events = [r["event"] for r in records(self.stream)]
        self.assertIn("execution.connection_lost", events)
        self.assertIn("control.entries_frozen", events)
        self.assertIn("execution.reconciliation", events)


if __name__ == "__main__":
    unittest.main()
