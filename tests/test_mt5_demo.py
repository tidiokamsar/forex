"""MT5 DEMO feed: DEMO-only guard, closed bars only, server clock to UTC, CSV import round trip (fake terminal)."""

import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from _support import workspace_tempdir
from astra.brokers import mt5_demo
from astra.marketdata import CandleStore


def server_seconds(wall: str) -> int:
    """A wall-clock time of the trade server, written as MT5 does (as if it were UTC)."""

    return int(datetime.fromisoformat(wall).replace(tzinfo=UTC).timestamp())


class FakeMt5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
    TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 5, 15, 16385, 16388, 16408

    def __init__(self, trade_mode=0, server="MetaQuotes-Demo", logged_in=True):
        self.trade_mode, self.server, self.logged_in = trade_mode, server, logged_in
        self.calls, self.closed = [], False
        self.rates = [
            {"time": server_seconds("2025-12-15 10:00"), "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "tick_volume": 9},
            {"time": server_seconds("2026-07-15 10:00"), "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "tick_volume": 7},
        ]

    def initialize(self, path=None, timeout=None, **signin):
        self.signin = signin
        return True

    def last_error(self):
        return (-1, "fake")

    def account_info(self):
        if not self.logged_in:
            return None
        return SimpleNamespace(login=114765880, server=self.server, trade_mode=self.trade_mode, currency="USD",
                               balance=100000.0, equity=100000.0, leverage=100)

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=False)

    def history_deals_get(self, start, end):
        return ()

    def history_orders_get(self, start, end):
        return ()

    def symbol_select(self, symbol, enable):
        return True

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.calls.append((symbol, timeframe, start, count))
        return self.rates

    def shutdown(self):
        self.closed = True

    def __getattr__(self, name):  # a read-only feed must never reach an order function
        raise AssertionError(f"unexpected MT5 call: {name}")


class Mt5DemoTests(unittest.TestCase):
    def test_only_an_allowed_demo_account_is_read(self):
        for fake, reason in ((FakeMt5(trade_mode=2), "pas un compte DEMO"), (FakeMt5(trade_mode=1), "pas un compte DEMO"),
                             (FakeMt5(server="Broker-Live"), "non vérifié"), (FakeMt5(logged_in=False), "aucun compte")):
            with self.subTest(reason=reason), self.assertRaises(mt5_demo.Mt5DemoError) as caught:
                mt5_demo.Mt5DemoFeed(module=fake).connect()
            self.assertIn(reason, str(caught.exception))
            self.assertTrue(fake.closed)
        account = mt5_demo.Mt5DemoFeed(module=FakeMt5()).connect()
        self.assertEqual((account["environment"], account["source"]), ("DEMO", "MT5_METAQUOTES_DEMO"))
        self.assertFalse(account["algo_trading_enabled"])

    def test_unattended_sign_in_checks_the_server_first_and_never_returns_the_password(self):
        with self.assertRaises(mt5_demo.Mt5DemoError):
            mt5_demo.Mt5DemoFeed(module=FakeMt5(), credentials=(114765880, "pw-123", "Broker-Live"))
        fake = FakeMt5()
        account = mt5_demo.Mt5DemoFeed(module=fake, credentials=(114765880, "pw-123", "MetaQuotes-Demo")).connect()
        self.assertEqual(fake.signin, {"login": 114765880, "password": "pw-123", "server": "MetaQuotes-Demo"})
        self.assertNotIn("pw-123", repr(account))

    def test_closed_bars_only_and_server_clock_converted_to_utc(self):
        fake = FakeMt5()
        with mt5_demo.Mt5DemoFeed(module=fake) as feed:
            candles = feed.candles("eur/usd", "h1", 2)
        self.assertEqual(fake.calls, [("EURUSD", FakeMt5.TIMEFRAME_H1, 1, 2)])  # position 0 is the bar still forming
        self.assertEqual([c.timestamp for c in candles],
                         [datetime(2025, 12, 15, 8, tzinfo=UTC), datetime(2026, 7, 15, 7, tzinfo=UTC)])  # UTC+2, UTC+3
        with mt5_demo.Mt5DemoFeed(module=FakeMt5()) as feed, self.assertRaises(mt5_demo.Mt5DemoError):
            feed.candles("EURUSD", "M1", 2)

    def test_csv_imports_unchanged_into_the_candle_store(self):
        with mt5_demo.Mt5DemoFeed(module=FakeMt5()) as feed:
            candles = feed.candles("EURUSD", "H1", 2)
        with workspace_tempdir() as temp:
            store = CandleStore(Path(temp) / "astra.sqlite3")
            store.initialize()
            report = store.import_csv(mt5_demo.to_csv(candles), "EURUSD", "H1", "MT5_METAQUOTES_DEMO")
            self.assertEqual((report["status"], report["inserted"]), ("OK", 2))
            stored = store.candles("EURUSD", "H1", "MT5_METAQUOTES_DEMO")
        self.assertEqual([(c.timestamp, c.open, c.close) for c in stored], [(c.timestamp, c.open, c.close) for c in candles])


if __name__ == "__main__":
    unittest.main()
