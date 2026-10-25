"""Regression tests for points raised while documenting the foundations (simulator only)."""

import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from astra.backup import create_backup, restore_backup
from astra.brokers.simulated import SimulatedBroker
from astra.cli import main
from astra.execution import FreezeReason, OrderGateway, OrderState, TradingControls
from test_execution import OPERATOR, SYSTEM, ExecutionTestCase


class EventCursorPerAccountTests(ExecutionTestCase):
    """Each adapter numbers its own events; one account's cursor must not hide another's events."""

    def test_events_of_a_second_account_are_not_skipped(self):
        broker_a = self.broker
        broker_b = SimulatedBroker(clock=self.clock, account_ref="SIM-****0002")
        broker_b.set_quote("EURUSD", 1.10000, 1.10010)
        gw_a = OrderGateway(store=self.store, broker=broker_a, clock=self.clock, account_ref="SIM-****0001")
        gw_b = OrderGateway(store=self.store, broker=broker_b, clock=self.clock, account_ref="SIM-****0002")
        # Advance account A's cursor well beyond B's sequence numbers.
        for _ in range(3):
            broker_a.script_next_submit("accept_only")
            sent = gw_a.submit(self.command(), SYSTEM)
            self.assertEqual(sent.state, OrderState.ACCEPTED)
            broker_a.cancel_order(sent.client_order_id)
            gw_a.process_events()
        self.assertGreater(self.store.get_state("broker_event_cursor:SIM-****0001"), 2)
        broker_b.script_next_submit("accept_only")
        result = gw_b.submit(self.command(account_ref="SIM-****0002"), SYSTEM)
        self.assertEqual(result.state, OrderState.ACCEPTED)
        broker_b.fill_accepted(result.client_order_id)
        gw_b.process_events()
        self.assertEqual(self.store.get_order(result.intent_id).state, OrderState.FILLED)


class RestoreAllAccountsTests(ExecutionTestCase):
    def test_restore_freezes_every_account_without_a_row_limit(self):
        store = self.store
        for index in range(3):
            store.create_order(
                {
                    "intent_id": f"bulk-{index}", "client_order_id": f"c-{index}", "decision_id": "d", "purpose": "ENTRY",
                    "mode": "SIMULATION", "account_ref": f"ACC-{index}", "instrument": "EURUSD", "direction": "LONG",
                    "requested_units": 1000, "stop_price": 1.0, "strategy": "s", "strategy_version": "1",
                    "expires_at": "2026-01-01T00:00:00+00:00",
                },
                correlation_id="x",
            )
        self.assertEqual(store.account_refs(), ["ACC-0", "ACC-1", "ACC-2"])
        record = create_backup(store.path, f"{self.tmp}/backups")
        result = restore_backup(record.path, store.path, actor=OPERATOR, reason="drill")
        self.assertEqual(result["accounts_frozen_until_reconciled"], ["ACC-0", "ACC-1", "ACC-2"])


class ClearFreezeCliTests(ExecutionTestCase):
    def run_cli(self, *argv):
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"ASTRA_DATABASE": str(self.store.path)}, clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(list(argv))
        return code, json.loads(output.getvalue() or errors.getvalue())

    def test_human_can_clear_service_degraded_for_an_account(self):
        TradingControls(self.store, scope="SIM-SERVICE-1").freeze(FreezeReason.SERVICE_DEGRADED)
        code, result = self.run_cli("controls", "clear-freeze", "--freeze", "service_degraded",
                                    "--account", "SIM-SERVICE-1", "--operator", "alice")
        self.assertEqual(code, 0)
        self.assertNotIn("SIM-SERVICE-1", result["entry_freezes_by_account"])

    def test_reconciliation_owned_freeze_is_refused(self):
        TradingControls(self.store, scope="SIM-SERVICE-1").freeze(FreezeReason.CONNECTION_LOST)
        code, result = self.run_cli("controls", "clear-freeze", "--freeze", "connection_lost",
                                    "--account", "SIM-SERVICE-1", "--operator", "alice")
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "denied")
        self.assertIn("connection_lost", TradingControls(self.store, scope="SIM-SERVICE-1").freezes())


if __name__ == "__main__":
    unittest.main()
