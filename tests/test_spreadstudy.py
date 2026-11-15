"""Spread study: quote history kept per minute, spreads per hour, hour effect priced with the measured spread."""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from _support import workspace_tempdir
from astra import mt5view, spreadstudy
from astra.marketdata import KIND_OHLC, Candle, CandleStore
from astra.mt5view import Mt5View


def quotes(symbol, start, minutes, spread_at):
    out = []
    for i in range(minutes):
        stamp = start + timedelta(minutes=i)
        bid = 1.1
        out.append({"symbol": symbol, "bid": bid, "ask": bid + spread_at(stamp.hour) * 0.0001,
                    "time": stamp.isoformat()})
    return out


class QuoteHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.view = Mt5View(self.db)
        self.view.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_one_quote_per_instrument_and_minute_is_kept_and_old_minutes_are_dropped(self):
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        first = {"symbol": "EURGBP", "bid": 0.85, "ask": 0.85012, "time": now.isoformat()}
        again = {**first, "ask": 0.85099, "time": (now + timedelta(seconds=20)).isoformat()}
        self.view.record_quotes({"quotes": [first]})
        self.view.record_quotes({"quotes": [again]})
        history = self.view.quote_history("EURGBP")
        self.assertEqual(len(history), 1)
        self.assertAlmostEqual(history[0][2], 0.85012)  # the first quote of the minute
        old = {**first, "time": (now - timedelta(days=mt5view.QUOTE_HISTORY_DAYS + 2)).isoformat()}
        self.view.record_quotes({"quotes": [old]})
        self.assertEqual(len(self.view.quote_history("EURGBP")), 1)  # older than the retention: removed at once

    def test_every_registry_instrument_fits_in_one_post(self):
        from astra.marketdata import INSTRUMENTS

        self.assertGreaterEqual(mt5view.MAX_QUOTES, len(INSTRUMENTS))

    def test_the_catalogue_keeps_the_swaps_and_an_older_bridge_without_them_still_works(self):
        item = {"name": "EURGBP", "group": "Forex", "path": "Forex\\EURGBP", "description": "x", "contract_size": 100000,
                "currency_base": "EUR", "currency_profit": "GBP", "currency_margin": "EUR", "digits": 5,
                "volume_min": 0.01, "volume_max": 500, "volume_step": 0.01, "trade_mode": 4, "spread": 2}
        self.view.record_instruments({"server": "MetaQuotes-Demo", "items": [item]})
        self.view.record_instruments({"server": "MetaQuotes-Demo",
                                      "items": [{**item, "name": "EURJPY", "swap_long": -6.1, "swap_short": 1.2,
                                                 "swap_mode": 1}]})
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(self.db)) as db:
            rows = dict((r[0], r[1:]) for r in db.execute("SELECT name, swap_long, swap_short, swap_mode FROM mt5_instruments"))
        self.assertEqual(rows["EURGBP"], (None, None, None))
        self.assertEqual(rows["EURJPY"], (-6.1, 1.2, 1))


class StudyTests(unittest.TestCase):
    def test_spreads_are_measured_per_hour_in_pips(self):
        start = datetime(2026, 11, 16, 0, tzinfo=UTC)
        rows = [(q["time"][:16], q["bid"], q["ask"]) for q in quotes("EURGBP", start, 24 * 60,
                                                                       lambda h: 8.0 if h == 21 else 1.0)]
        table = spreadstudy.spread_by_hour(rows, "EURGBP")
        self.assertAlmostEqual(table[21]["median"], 8.0, places=1)
        self.assertAlmostEqual(table[10]["median"], 1.0, places=1)
        self.assertEqual(table[10]["samples"], 60)

    def test_an_hour_pattern_that_only_exists_inside_the_spread_is_called_an_artefact(self):
        temp = workspace_tempdir()
        self.addCleanup(temp.cleanup)
        if True:
            db = Path(temp.name) / "astra.sqlite3"
            store = CandleStore(db)
            store.initialize()
            store.register_source(spreadstudy.HISTORY_SOURCE, "MASSIVE_REST", "HISTORICAL", "test")
            start, price, bars = datetime(2024, 1, 1, tzinfo=UTC), 1.1, []
            for i in range(6000):  # price dips by 3 pips at 21:00 and comes back at 22:00, every day
                stamp = start + timedelta(hours=i)
                price = 1.1 - (0.0003 if stamp.hour == 21 else 0.0) + 0.00001 * ((i * 7919) % 7 - 3)
                bars.append(Candle("EURGBP", "H1", stamp, price, price, price, price, None, spreadstudy.HISTORY_SOURCE,
                                   KIND_OHLC))
            store.store(bars, "HISTORICAL", None)
            wide = {h: {"median": 8.0 if h in (21, 22) else 1.0, "p90": 9.0, "samples": 60} for h in range(24)}
            narrow = {h: {"median": 0.1, "p90": 0.2, "samples": 60} for h in range(24)}
            self.assertEqual(spreadstudy.hour_effect(store, "EURGBP", wide)["verdict"], "ARTEFACT_OU_TROP_CHER")
            self.assertEqual(spreadstudy.hour_effect(store, "EURGBP", narrow)["verdict"], "EFFET_REEL")

    def test_without_enough_days_of_quotes_the_study_says_so_instead_of_concluding(self):
        view = mock.Mock()
        view.quote_history.return_value = [("2026-11-16T10:00", 1.1, 1.1001)]
        report = spreadstudy.study(mock.Mock(), view, datetime(2026, 11, 16, tzinfo=UTC))
        self.assertEqual(set(report["summary"]), {"COTATIONS_INSUFFISANTES"})
