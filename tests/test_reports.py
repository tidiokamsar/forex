"""Daily report: the account, the orders, each closed trade against its planned risk, decisions, learning; read only."""

import json
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from _support import workspace_tempdir
from astra import learning, reports
from astra.mt5view import Mt5View
from astra.papertrade import LabStore


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        Mt5View(self.db).initialize()
        LabStore(self.db).initialize()
        learning.LearningStore(self.db).initialize()

    def tearDown(self):
        self.temp.cleanup()

    def fill(self):
        with sqlite3.connect(self.db) as db:
            for ts, equity in (("2026-11-10T00:05:00+00:00", 100000.0), ("2026-11-10T23:55:00+00:00", 99600.0)):
                db.execute("INSERT INTO mt5_equity VALUES (?,?,?)", (ts, equity, 100000.0))
            deal = "INSERT INTO mt5_deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            db.execute(deal, (1, 11, 7, "2026-11-10T08:00:00+00:00", "EURUSD", 0, 0, 1.0, 1.10, -2.0, 0.0, 0.0, 0.0,
                              20261028, "ASTRA:x", 3, "now"))
            db.execute(deal, (2, 12, 7, "2026-11-10T14:00:00+00:00", "EURUSD", 1, 1, 1.0, 1.096, -2.0, -1.0, 0.0, -400.0,
                              20261028, "", 4, "now"))  # stopped out at the broker
            db.execute(deal, (3, 13, 8, "2026-11-10T09:00:00+00:00", "GBPUSD", 0, 0, 1.0, 1.30, 0.0, 0.0, 0.0, 0.0,
                              0, "", 0, "now"))  # a position opened by hand: not ASTRA's
            db.execute("INSERT INTO mt5_events(ts, kind, symbol, detail_json) VALUES (?,?,?,?)",
                       ("2026-11-10T08:00:04+00:00", "open", "EURUSD",
                        json.dumps({"kind": "open", "symbol": "EURUSD", "reason": "signal ACHAT", "risk_usd": 500.0})))
            db.execute("INSERT INTO mt5_events(ts, kind, symbol, detail_json) VALUES (?,?,?,?)",
                       ("2026-11-10T10:00:00+00:00", "refused", "USDJPY",
                        json.dumps({"kind": "refused", "symbol": "USDJPY", "reason": "spread : 4 pips"})))

    def test_a_day_with_a_stopped_trade_is_analysed_against_its_planned_risk(self):
        self.fill()
        report = reports.daily(self.db, date(2026, 11, 10))
        self.assertEqual(report["account"]["change"], -400.0)
        self.assertEqual(report["orders"], {"opened": 1, "closed": 1, "foreign": 1})
        trade = report["trades"][0]
        self.assertEqual((trade["symbol"], trade["side"], trade["net"]), ("EURUSD", "LONG", -405.0))
        self.assertEqual(trade["r_multiple"], -0.81)  # -405 against 500 planned at the entry
        self.assertEqual(trade["entry_reason"], "signal ACHAT")  # the bridge's line a few seconds after the deal
        self.assertFalse(trade["closed_by_astra"])
        self.assertIn("stop", trade["exit_reason"])
        self.assertIn("stop a joué son rôle", trade["lesson"])
        self.assertEqual(report["refusals"], [{"symbol": "USDJPY", "reason": "spread : 4 pips"}])
        self.assertTrue(any("1 ouverture" in line for line in report["summary"]))

    def test_another_day_or_an_empty_database_reports_nothing_invented(self):
        self.fill()
        quiet = reports.daily(self.db, date(2026, 11, 9))
        self.assertIsNone(quiet["account"])
        self.assertEqual((quiet["trades"], quiet["decisions"]), ([], []))
        self.assertIn("rester en dehors du marché", " ".join(quiet["summary"]))
        empty = Path(self.temp.name) / "empty.sqlite3"
        sqlite3.connect(empty).close()
        nothing = reports.daily(empty, date(2026, 11, 10))  # no table at all: every part is simply empty
        self.assertEqual(nothing["orders"], {"opened": 0, "closed": 0, "foreign": 0})

    def test_the_report_never_writes(self):
        self.fill()
        before = self.db.read_bytes()
        reports.daily(self.db, date(2026, 11, 10))
        self.assertEqual(self.db.read_bytes(), before)

    def test_lessons(self):
        self.assertIn("inconnu", reports._lesson(None, False))
        self.assertIn("sortie avant le stop", reports._lesson(-0.4, True))
        self.assertIn("supérieur à 1,5", reports._lesson(2.0, True))


if __name__ == "__main__":
    unittest.main()
