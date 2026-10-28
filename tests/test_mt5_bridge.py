"""MT5 DEMO orders and the bridge from ASTRA's managed accounts (fake terminal, fake server)."""

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from _support import workspace_tempdir
from astra.agent import AgentHalt, AstraClient
from astra.brokers.mt5_demo import Mt5DemoError
from astra.brokers.mt5_demo_orders import MAGIC, Mt5DemoTrader
from astra.mt5_bridge import Bridge, targets
from test_mt5_demo import FakeMt5

LOGIN = 114765880


class TradingMt5(FakeMt5):
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, ORDER_TIME_GTC = 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.algo, self.equity, self.sent, self.open_positions, self.next_ticket = True, 100000.0, [], [], 1

    def account_info(self):
        info = super().account_info()
        return info and SimpleNamespace(**{**vars(info), "equity": self.equity})

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=self.algo)

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=1, volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_stops_level=0,
                               point=0.00001, digits=5)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=1.10000, ask=1.10002)

    def order_calc_profit(self, kind, symbol, volume, price_open, price_close):
        move = (price_close - price_open) if kind == self.ORDER_TYPE_BUY else (price_open - price_close)
        return move * 100000 * volume

    def positions_get(self):
        return tuple(self.open_positions)

    def order_send(self, request):
        self.sent.append(request)
        if "position" in request:
            self.open_positions = [p for p in self.open_positions if p.ticket != request["position"]]
        else:
            self.open_positions.append(SimpleNamespace(ticket=self.next_ticket, symbol=request["symbol"], type=request["type"],
                                                       volume=request["volume"], price_open=request["price"],
                                                       sl=request["sl"], profit=0.0, magic=request["magic"]))
            self.next_ticket += 1
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done", order=len(self.sent), deal=len(self.sent),
                               volume=request["volume"], price=request["price"])


def overview(position=None, pending=None, status="RUNNING"):
    account = {"positions": {"EURUSD": position} if position else {}, "pending": {"EURUSD": pending} if pending else {}}
    return {"pairs": [{"symbol": "EURUSD", "session": {"id": 1, "status": status, "account": account}}]}


SHORT = {"side": "SHORT", "entry_ts": "2026-09-29T12:15:00+00:00", "entry_ref": 1.1355, "stop": 1.1415, "units": 7000}


class FakeServer:
    def __init__(self):
        self.status = {"mode": "SIMULATION", "live_locked": True}
        self.view = overview(SHORT)

    def __call__(self, method, path, body):
        return 200, self.status if path == "/api/status" else self.view


class TraderTests(unittest.TestCase):
    def test_orders_need_the_pinned_demo_account_and_algo_trading(self):
        with self.assertRaises(Mt5DemoError):
            Mt5DemoTrader(module=TradingMt5(), expected_login=1).connect()
        fake = TradingMt5()
        with Mt5DemoTrader(module=fake, expected_login=LOGIN) as trader:
            fake.algo = False
            with self.assertRaises(Mt5DemoError) as caught:
                trader.open("EURUSD", "LONG", 0.01, 1.09)
            self.assertIn("Trading Algo", str(caught.exception))
            fake.algo, fake.trade_mode = True, fake.ACCOUNT_TRADE_MODE_REAL  # account switched in the terminal
            with self.assertRaises(Mt5DemoError):
                trader.open("EURUSD", "LONG", 0.01, 1.09)
        self.assertEqual(fake.sent, [])

    def test_risk_sizing_and_manual_positions_are_never_closed(self):
        fake = TradingMt5()
        with Mt5DemoTrader(module=fake, expected_login=LOGIN) as trader:
            lots, sl = trader.lots_for_risk("EURUSD", "SHORT", 0.0060, 500.0)
            self.assertEqual((lots, sl), (0.83, 1.106))  # 0.0060 x 100 000 x 0.83 = 498 USD at the stop
            fake.open_positions.append(SimpleNamespace(ticket=99, symbol="EURUSD", type=0, volume=1.0, price_open=1.1,
                                                       sl=0.0, profit=5.0, magic=0))
            manual = trader.positions()[0]
            self.assertFalse(manual.astra)
            with self.assertRaises(Mt5DemoError):
                trader.close(manual)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.dir = Path(self.temp.name)
        self.server, self.fake = FakeServer(), TradingMt5()
        self.bridge = Bridge(AstraClient(self.server), Mt5DemoTrader(module=self.fake, expected_login=LOGIN), self.dir,
                             clock=lambda: datetime(2026, 10, 29, 9, tzinfo=UTC))

    def tearDown(self):
        self.temp.cleanup()

    def test_targets_follow_positions_and_pending_exits(self):
        self.assertEqual(targets(overview(SHORT))["EURUSD"]["side"], "SHORT")
        self.assertEqual(targets(overview(SHORT, {"side": "FLAT", "label": "AUCUNE_POSITION"}))["EURUSD"]["side"], "FLAT")
        self.assertEqual(targets(overview(None, {"side": "LONG", "label": "ACHAT"}))["EURUSD"]["side"], "FLAT")
        self.assertEqual(targets(overview(SHORT, status="STOPPED"))["EURUSD"]["side"], "FLAT")

    def test_mirror_open_close_and_no_reopen_after_a_stop_out(self):
        first = self.bridge.cycle()
        self.assertEqual([a["kind"] for a in first["actions"]], ["open"])
        self.assertEqual(self.fake.sent[0]["magic"], MAGIC)
        self.assertEqual(len(self.bridge.cycle()["actions"]), 0)  # already mirrored
        self.fake.open_positions.clear()  # the stop was hit on MT5
        self.assertEqual(len(self.bridge.cycle()["actions"]), 0)  # not reopened for the same paper entry
        self.server.view = overview({**SHORT, "entry_ts": "2026-10-29T13:15:00+00:00"})
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["open"])  # a new paper entry
        self.server.view = overview(SHORT, {"side": "FLAT", "label": "AUCUNE_POSITION"})
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["close"])
        self.assertEqual(self.fake.open_positions, [])
        journal = (self.dir / "journal-2026-10-29.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertIn("close", [json.loads(line)["kind"] for line in journal])

    def test_halts_stop_file_and_daily_loss(self):
        (self.dir / "STOP").write_text("", encoding="utf-8")
        self.assertTrue(self.bridge.cycle()["halted"])
        (self.dir / "STOP").unlink()
        self.bridge.state.update({"day": "2026-10-29", "day_start_equity": 104000.0})  # -3.8 % today
        self.assertEqual([a["kind"] for a in self.bridge.cycle()["actions"]], ["entry_skipped"])
        self.server.status = {"mode": "LIVE", "live_locked": False}
        with self.assertRaises(AgentHalt):
            self.bridge.cycle()
        self.assertEqual(self.fake.sent, [])


if __name__ == "__main__":
    unittest.main()
