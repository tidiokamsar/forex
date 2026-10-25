import math
import unittest
from datetime import UTC, datetime, timedelta

from _support import workspace_tempdir
from astra.engine import ReplayDecision, run_paper_replay
from astra.execution import ExecutionStore, OrderState
from astra.models import Direction, MarketBar


def synthetic_bars(count=240):
    """Deterministic synthetic EURUSD bars with alternating trends (not market data)."""

    start = datetime(2026, 1, 5, tzinfo=UTC)
    bars = []
    price = 1.1
    for i in range(count):
        drift = 0.00025 * math.sin(i / 18.0)
        open_ = price
        close = price + drift
        bars.append(
            MarketBar(
                instrument="EURUSD",
                timestamp=start + timedelta(minutes=15 * i),
                open=open_,
                high=max(open_, close) + 0.0002,
                low=min(open_, close) - 0.0002,
                close=close,
            )
        )
        price = close
    return bars


class PaperReplayTests(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        tmp = self._tmp.__enter__()
        self.store = ExecutionStore(f"{tmp}/astra.sqlite3")
        self.store.initialize()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def test_end_to_end_replay_trades_and_reconciles(self):
        summary = run_paper_replay(synthetic_bars(), self.store, run_id="t1")
        self.assertEqual(summary.mode, "SIMULATION")
        self.assertGreater(summary.decisions, 0)
        self.assertGreater(summary.orders_by_state.get("FILLED", 0), 0)
        self.assertEqual(summary.reconciliation_end, "CONSISTENT")
        self.assertEqual(summary.final_positions, ())
        self.assertEqual(summary.freezes_end, ())
        for order in self.store.list_orders(limit=500, account_ref=summary.account_ref):
            states = [row["to_state"] for row in self.store.transitions(order.intent_id)]
            self.assertEqual(states[:2], ["CREATED", "VALIDATING"])
            if order.state is OrderState.FILLED and order.purpose == "ENTRY":
                self.assertIn("AUTHORIZED", states)

    def test_two_replays_in_one_store_are_isolated(self):
        first = run_paper_replay(synthetic_bars(), self.store, run_id="a")
        second = run_paper_replay(synthetic_bars(), self.store, run_id="b")
        self.assertEqual(first.orders_by_state, second.orders_by_state)
        self.assertEqual(second.reconciliation_start, "CONSISTENT")

    def test_decision_function_sees_only_past_bars(self):
        bars = synthetic_bars(80)
        seen = []

        def spy(history):
            seen.append(history[-1].timestamp)
            return None

        run_paper_replay(bars, self.store, decide=spy, run_id="spy")
        self.assertEqual(seen, [bar.timestamp for bar in bars])

    def test_kill_switch_blocks_replay_entries(self):
        from astra.execution import TradingControls

        TradingControls(self.store).activate_kill_switch("operator", "maintenance")
        summary = run_paper_replay(synthetic_bars(), self.store, run_id="k")
        self.assertEqual(summary.orders_by_state.get("FILLED", 0), 0)
        self.assertTrue(all("kill_switch_active" in item for item in summary.rejections))

    def test_abstaining_strategy_places_no_orders(self):
        summary = run_paper_replay(synthetic_bars(), self.store, decide=lambda history: None, run_id="flat")
        self.assertEqual(summary.orders_by_state, {})
        self.assertEqual(summary.final_balance, summary.initial_balance)

    def test_invalid_stop_is_rejected_by_risk(self):
        def bad(history):
            if len(history) == 10:
                return ReplayDecision(Direction.LONG, history[-1].close + 0.01, "bad", "0", "stop above entry")
            return None

        summary = run_paper_replay(synthetic_bars(40), self.store, decide=bad, run_id="bad")
        self.assertEqual(summary.orders_by_state, {"RISK_REJECTED": 1})
        self.assertIn("invalid_stop_direction", summary.rejections[0])


if __name__ == "__main__":
    unittest.main()
