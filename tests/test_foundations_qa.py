"""Regression tests for the independent QA findings on the foundations (simulator / offline only)."""

import io
import shutil
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from astra.backup import BackupError, create_backup, restore_backup
from astra.brokers import BrokerRequestError, BrokerUnavailable
from astra.brokers.ctrader import CTraderConfigurationError
from astra.execution import ExecutionStore, FreezeReason, OrderGateway, OrderState
from astra.governance import SecretValue
from astra.logs import configure_logging, get_logger, log_event, reset_logging
from astra.service import TradingService
from test_execution import OPERATOR, SYSTEM, ExecutionTestCase

ACCOUNT = "SIM-****0001"


class RestoreSafetyTests(ExecutionTestCase):
    """D1/D7: a failed restore never leaves the target missing or unfrozen."""

    def setUp(self):
        super().setUp()
        self.gateway = OrderGateway(store=self.store, broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        self.gateway.submit(self.command(), SYSTEM)
        self.record = create_backup(self.store.path, f"{self.tmp}/backups")

    def order_count(self):
        return len(ExecutionStore(self.store.path).list_orders(limit=500))

    def test_copy_failure_leaves_the_live_database_untouched(self):
        with patch("astra.backup.shutil.copy2", side_effect=OSError("disk full")), self.assertRaises(OSError):
            restore_backup(self.record.path, self.store.path, actor=OPERATOR, reason="drill")
        self.assertTrue(Path(self.store.path).exists())
        self.assertEqual(self.order_count(), 1)
        self.assertFalse(Path(str(self.store.path) + ".restoring").exists())

    def test_swap_failure_puts_the_previous_database_back(self):
        with patch("astra.backup.Path.replace", side_effect=OSError("locked")), self.assertRaises(OSError):
            restore_backup(self.record.path, self.store.path, actor=OPERATOR, reason="drill")
        self.assertTrue(Path(self.store.path).exists())
        self.assertEqual(self.order_count(), 1)

    def test_backup_moved_elsewhere_restores_from_the_verified_file(self):
        moved = Path(self.tmp) / "offsite"
        shutil.move(str(Path(self.record.path).parent), moved)
        result = restore_backup(moved / Path(self.record.path).name, self.store.path, actor=OPERATOR, reason="drill")
        self.assertEqual(result["accounts_frozen_until_reconciled"], [ACCOUNT])
        restored = OrderGateway(store=ExecutionStore(self.store.path), broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        self.assertIn("reconciliation_required", restored.controls.freezes())

    def test_restore_refused_while_a_service_heartbeat_is_alive(self):
        self.store.put_state(f"service:{ACCOUNT}", {"status": "running", "heartbeat_at": datetime.now(UTC).isoformat()})
        with self.assertRaises(BackupError):
            restore_backup(self.record.path, self.store.path, actor=OPERATOR, reason="drill")
        self.assertEqual(self.order_count(), 1)

    def test_keep_must_be_positive(self):
        for keep in (0, -1):
            with self.assertRaises(BackupError):
                create_backup(self.store.path, f"{self.tmp}/backups", keep=keep)


class ServiceErrorHandlingTests(ExecutionTestCase):
    """D3/D4: permanent reconnect failures degrade at once; transient ones do not; degraded mode re-arms."""

    def service(self, reconnect):
        return TradingService(store=self.store, broker=self.broker, decide=lambda history: None,
                              account_ref=ACCOUNT, clock=self.clock, reconnect=reconnect)

    def test_permanent_reconnect_failure_degrades_once_and_stops_retrying(self):
        calls = []

        def reconnect():
            calls.append(1)
            raise CTraderConfigurationError("account not granted")

        service = self.service(reconnect)
        self.broker.disconnect()
        for _ in range(10):
            service.tick()
        self.assertEqual(len(calls), 1)
        self.assertTrue(service.stats.degraded)
        self.assertIn("service_degraded", service.gateway.controls.freezes())

    def test_transient_reconnect_failure_keeps_retrying_without_degrading(self):
        calls = []

        def reconnect():
            calls.append(1)
            raise BrokerUnavailable("timeout")

        service = self.service(reconnect)
        self.broker.disconnect()
        for _ in range(10):
            service.tick()
        self.assertEqual(len(calls), 10)
        self.assertFalse(service.stats.degraded)

    def test_rate_limit_inside_a_tick_is_not_counted_as_an_error(self):
        service = self.service(None)

        def limited(_instrument):
            raise BrokerRequestError("REQUEST_FREQUENCY_EXCEEDED")

        def limited_events(_after):
            raise BrokerRequestError("REQUEST_FREQUENCY_EXCEEDED")

        self.broker.quote = limited
        self.broker.poll_events = limited_events
        for _ in range(8):
            service.tick()
        self.assertEqual(service.stats.errors, 0)
        self.assertFalse(service.stats.degraded)

    def test_degraded_mode_rearms_after_a_human_clears_it(self):
        service = self.service(None)

        def broken(_instrument):
            raise RuntimeError("bug")

        self.broker.quote = broken
        for _ in range(5):
            service.tick()
        self.assertTrue(service.stats.degraded)
        service.gateway.controls.clear_freeze(FreezeReason.SERVICE_DEGRADED, OPERATOR)
        service.tick()
        self.assertFalse(service.stats.degraded)
        for _ in range(5):
            service.tick()
        self.assertTrue(service.stats.degraded)
        self.assertIn("service_degraded", service.gateway.controls.freezes())


class CursorEpochTests(ExecutionTestCase):
    """D5: a new adapter instance restarts its sequence numbers; a stale cursor must not hide its events."""

    def test_events_after_restart_are_processed(self):
        self.store.put_state(f"broker_event_cursor:{ACCOUNT}", {"epoch": "previous-process", "cursor": 1000})
        gateway = OrderGateway(store=self.store, broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        self.broker.script_next_submit("accept_only")
        result = gateway.submit(self.command(), SYSTEM)
        self.broker.fill_accepted(result.client_order_id)
        gateway.process_events()
        self.assertEqual(self.store.get_order(result.intent_id).state, OrderState.FILLED)

    def test_legacy_integer_cursor_is_not_reused(self):
        self.store.put_state(f"broker_event_cursor:{ACCOUNT}", 1000)
        gateway = OrderGateway(store=self.store, broker=self.broker, clock=self.clock, account_ref=ACCOUNT)
        self.broker.script_next_submit("accept_only")
        result = gateway.submit(self.command(), SYSTEM)
        self.broker.fill_accepted(result.client_order_id)
        gateway.process_events()
        self.assertEqual(self.store.get_order(result.intent_id).state, OrderState.FILLED)


class LogMaskingTests(unittest.TestCase):
    """D6: a revealed secret is masked even in messages, odd field names and exceptions."""

    def test_revealed_secret_is_masked_everywhere(self):
        stream = io.StringIO()
        configure_logging(level="DEBUG", stream=stream)
        self.addCleanup(reset_logging)
        secret = SecretValue("sup3r-s3cret-t0ken-value").reveal()
        logger = get_logger("mask")
        log_event(logger, "x", f"connecting with {secret}", detail=f"url?q={secret}", items=(secret,))
        try:
            raise ValueError(f"auth failed for {secret}")
        except ValueError:
            logger.exception("boom", extra={"event": "y"})
        log_event(logger, "z", name="reserved-name-is-safe")
        text = stream.getvalue()
        self.assertNotIn(secret, text)
        self.assertIn("reserved-name-is-safe", text)


if __name__ == "__main__":
    unittest.main()
