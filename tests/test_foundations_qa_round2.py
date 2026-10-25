"""Regression tests for the second QA round on the foundations (simulator / offline only)."""

import io
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from astra.backup import create_backup, restore_backup
from astra.brokers import BrokerRequestError
from astra.brokers.ctrader import provider_error
from astra.execution import ExecutionStore, OrderGateway
from astra.governance import SecretValue
from astra.logs import configure_logging, get_logger, log_event, reset_logging
from astra.service import TradingService, service_status
from test_execution import OPERATOR, SYSTEM, ExecutionTestCase

ACCOUNT = "SIM-****0001"


class NonTransientRefusalTests(ExecutionTestCase):
    """NEW-1: only rate limits are ignored; any other refusal still counts and can degrade."""

    def test_non_transient_reconnect_refusal_degrades(self):
        calls = []

        def reconnect():
            calls.append(1)
            raise BrokerRequestError("INVALID_REQUEST", "scripted")

        service = TradingService(store=self.store, broker=self.broker, decide=lambda history: None,
                                 account_ref=ACCOUNT, clock=self.clock, reconnect=reconnect)
        self.broker.disconnect()
        for _ in range(6):
            service.tick()
        self.assertTrue(service.stats.degraded)
        self.assertIn("service_degraded", service.gateway.controls.freezes())

    def test_provider_error_marks_only_rate_limits_transient(self):
        self.assertTrue(provider_error({"errorCode": "BLOCKED_PAYLOAD_TYPE", "retryAfter": 2}).transient)
        self.assertTrue(provider_error({"errorCode": "REQUEST_FREQUENCY_EXCEEDED"}).transient)
        self.assertFalse(provider_error({"errorCode": "INVALID_REQUEST"}).transient)
        self.assertFalse(provider_error({"errorCode": "TRADING_BAD_VOLUME"}).transient)


class CorruptTargetRestoreTests(ExecutionTestCase):
    """NEW-2: a corrupted live database can still be restored, and the corrupt file is kept aside."""

    def test_restore_over_a_corrupted_database(self):
        gateway = OrderGateway(store=self.store, broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        gateway.submit(self.command(), SYSTEM)
        record = create_backup(self.store.path, f"{self.tmp}/backups")
        Path(self.store.path).write_bytes(b"this is not a sqlite database" * 100)
        result = restore_backup(record.path, self.store.path, actor=OPERATOR, reason="corruption drill")
        self.assertTrue(Path(result["previous_saved_as"]).exists())
        self.assertEqual(len(ExecutionStore(self.store.path).list_orders(limit=10)), 1)
        self.assertEqual(result["accounts_frozen_until_reconciled"], [ACCOUNT])


class RestoredHeartbeatTests(ExecutionTestCase):
    """NEW-4: heartbeats inside a backup do not make the restored store look like a running service."""

    def test_restored_heartbeat_is_not_alive(self):
        self.store.put_state(f"service:{ACCOUNT}", {"status": "running", "heartbeat_at": datetime.now(UTC).isoformat()})
        record = create_backup(self.store.path, f"{self.tmp}/backups")
        self.store.put_state(f"service:{ACCOUNT}", {"status": "stopped", "heartbeat_at": datetime.now(UTC).isoformat()})
        restore_backup(record.path, self.store.path, actor=OPERATOR, reason="drill")
        statuses = service_status(ExecutionStore(self.store.path))
        self.assertEqual(statuses[0]["status"], "stopped_by_restore")
        self.assertFalse(statuses[0]["alive"])
        restore_backup(record.path, self.store.path, actor=OPERATOR, reason="second drill")


class MaskingPrecisionTests(unittest.TestCase):
    """NEW-3: masking never corrupts JSON, ignores short values and no longer hides harmless keys."""

    def setUp(self):
        self.stream = io.StringIO()
        configure_logging(level="DEBUG", stream=self.stream)
        self.addCleanup(reset_logging)

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]

    def test_short_values_are_not_registered_and_lines_stay_valid_json(self):
        SecretValue("1.10").reveal()
        log_event(get_logger("p"), "price", "price 1.1000 filled at 1.10", price=1.1001)
        entry = self.lines()[-1]
        self.assertEqual(entry["fields"]["price"], 1.1001)
        self.assertIn("1.10", entry["message"])

    def test_long_secret_inside_values_is_masked_and_json_stays_valid(self):
        secret = SecretValue('tok"en-with-quote-0123').reveal()
        log_event(get_logger("p"), "auth", f"using {secret}", info={"nested": [f"x{secret}y"]})
        entry = self.lines()[-1]
        self.assertNotIn(secret, json.dumps(entry))
        self.assertEqual(entry["fields"]["info"]["nested"], ["x****y"])

    def test_harmless_keys_are_not_masked(self):
        log_event(get_logger("p"), "cfg", refresh_interval=30, cookie_policy="none", refreshed_at="t")
        fields = self.lines()[-1]["fields"]
        self.assertEqual((fields["refresh_interval"], fields["cookie_policy"], fields["refreshed_at"]), (30, "none", "t"))
        log_event(get_logger("p"), "cfg", refresh_token="abcdefghijkl")
        self.assertEqual(self.lines()[-1]["fields"]["refresh_token"], "****")


if __name__ == "__main__":
    unittest.main()
