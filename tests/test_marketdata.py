"""Market data: CSV import, quality control, gaps, ECB reference rates, freshness (no network)."""

import unittest
import urllib.error
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra import marketdata
from astra.marketdata import CandleStore, MarketDataError, find_gaps, forex_market_closed, parse_csv

ECB_FIXTURE = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-15,1.1000,A
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-01-15,0.8500,A
EXR.D.JPY.EUR.SP00.A,D,JPY,EUR,SP00,A,2026-01-15,165.00,A
EXR.D.AUD.EUR.SP00.A,D,AUD,EUR,SP00,A,2026-01-15,1.7000,A
EXR.D.CHF.EUR.SP00.A,D,CHF,EUR,SP00,A,2026-01-15,0.9400,A
EXR.D.CAD.EUR.SP00.A,D,CAD,EUR,SP00,A,2026-01-15,1.5000,A
EXR.D.NZD.EUR.SP00.A,D,NZD,EUR,SP00,A,2026-01-15,1.9000,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-07-15,1.2000,A
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-07-15,0.8000,A
"""


def csv_rows(rows, header="timestamp,open,high,low,close,volume"):
    return ("\n".join([header, *rows]) + "\n").encode()


class CsvImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.store = CandleStore(Path(self.temp.name) / "astra.sqlite3")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_rows_are_stored_with_source_and_utc_time(self):
        raw = csv_rows(["2026-01-05T00:00:00Z,1.1,1.2,1.0,1.15,10", "2026-01-05T00:15:00+01:00,1.1,1.2,1.0,1.1,"])
        result = self.store.import_csv(raw, "eur/usd", "M15", "test-provider")
        self.assertEqual((result["status"], result["accepted"], result["inserted"], result["rejected"]), ("OK", 2, 2, 0))
        candles = self.store.candles("EURUSD", "M15", "test-provider")
        self.assertEqual([c.timestamp for c in candles], [datetime(2026, 1, 4, 23, 15, tzinfo=UTC),
                                                         datetime(2026, 1, 5, 0, 0, tzinfo=UTC)])
        self.assertIsNone(candles[0].volume)  # an absent volume is stored as absent, never as 0
        self.assertEqual(candles[1].volume, 10)
        self.assertEqual(self.store.source("test-provider")["nature"], "HISTORICAL")

    def test_invalid_rows_are_rejected_with_their_reason(self):
        raw = csv_rows([
            "2026-01-05T00:00:00Z,1.1,1.0,1.2,1.1,1",     # high below low
            "2026-01-05T00:15:00Z,-1,1.2,1.0,1.1,1",      # negative price
            "2026-01-05T00:07:00Z,1.1,1.2,1.0,1.1,1",     # not on the M15 grid
            "not a date,1.1,1.2,1.0,1.1,1",
            "2026-01-05T00:30:00Z,1.1,1.2,1.0,1.1,-5",    # negative volume
            "2099-01-05T00:30:00Z,1.1,1.2,1.0,1.1,1",     # future
            "2026-01-05T00:45:00Z,1.1,1.2,1.0,1.1,1",     # the only good row
        ])
        candles, report = parse_csv(raw, "EURUSD", "M15", "p")
        self.assertEqual(len(candles), 1)
        self.assertEqual(report.rejected, 6)
        codes = {issue["code"] for issue in report.issues}
        self.assertTrue({"INCONSISTENT_CANDLE", "NON_POSITIVE_PRICE", "MISALIGNED_TIMESTAMP", "INVALID_TIMESTAMP",
                         "NEGATIVE_VOLUME", "FUTURE_TIMESTAMP"} <= codes, codes)

    def test_duplicates_are_reported_and_reimport_is_idempotent(self):
        raw = csv_rows(["2026-01-05T00:00:00Z,1.1,1.2,1.0,1.1,1", "2026-01-05T00:00:00Z,1.1,1.2,1.0,1.1,1",
                        "2026-01-05T00:15:00Z,1.1,1.2,1.0,1.1,1", "2026-01-05T00:15:00Z,1.1,1.3,1.0,1.1,1"])
        first = self.store.import_csv(raw, "EURUSD", "M15", "p")
        self.assertEqual((first["accepted"], first["duplicates_in_file"], first["conflicts"]), (2, 2, 1))
        again = self.store.import_csv(raw, "EURUSD", "M15", "p")
        self.assertEqual((again["inserted"], again["already_stored"]), (0, 2))
        self.assertEqual(len(self.store.candles("EURUSD", "M15", "p")), 2)
        changed = csv_rows(["2026-01-05T00:00:00Z,1.1,1.25,1.0,1.1,1"])
        conflict = self.store.import_csv(changed, "EURUSD", "M15", "p")
        self.assertEqual((conflict["inserted"], conflict["conflicts"]), (0, 1))
        self.assertEqual(self.store.candles("EURUSD", "M15", "p")[0].high, 1.2)  # the stored value is kept

    def test_provider_formats(self):
        mt5 = csv_rows(["2026.01.05\t00:00:00\t1.1\t1.2\t1.0\t1.1\t42"], "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>")
        candles, _ = parse_csv(mt5, "EURUSD", "H1", "mt5")
        self.assertEqual((candles[0].timestamp, candles[0].volume), (datetime(2026, 1, 5, tzinfo=UTC), 42))
        dukascopy = csv_rows(["05.01.2026 01:00:00.000 GMT+0100,1.1,1.2,1.0,1.1,3.5"], "Local time,Open,High,Low,Close,Volume")
        candles, _ = parse_csv(dukascopy, "EURUSD", "H1", "duka")
        self.assertEqual(candles[0].timestamp, datetime(2026, 1, 5, tzinfo=UTC))
        semicolon = csv_rows(["2026-01-05 00:00;1,1;1,2;1,0;1,1"], "Date;Open;High;Low;Close")
        candles, _ = parse_csv(semicolon, "EURUSD", "D1", "semi")
        self.assertEqual((candles[0].close, candles[0].volume), (1.1, None))

    def test_local_time_zone_and_daylight_saving_changes(self):
        raw = csv_rows(["2026-03-29 01:00,1.1,1.2,1.0,1.1,1",   # 01:00 Paris (winter) = 00:00 UTC
                        "2026-03-29 02:00,1.1,1.2,1.0,1.1,1",   # does not exist in Paris (spring forward)
                        "2026-10-25 02:00,1.1,1.2,1.0,1.1,1",   # exists twice (fall back): ambiguous
                        "2026-07-01 12:00,1.1,1.2,1.0,1.1,1"])  # summer: 10:00 UTC
        candles, report = parse_csv(raw, "EURUSD", "H1", "paris", timezone="Europe/Paris")
        self.assertEqual([c.timestamp for c in candles], [datetime(2026, 3, 29, 0, tzinfo=UTC),
                                                         datetime(2026, 7, 1, 10, tzinfo=UTC)])
        self.assertEqual(report.rejected, 2)
        with self.assertRaises(MarketDataError):
            parse_csv(raw, "EURUSD", "H1", "x", timezone="Mars/Olympus")

    def test_refusals(self):
        for symbol, timeframe in (("BTCUSD", "M15"), ("EURUSD", "M7")):
            with self.assertRaises(MarketDataError):
                parse_csv(csv_rows([]), symbol, timeframe, "p")
        with self.assertRaises(MarketDataError):
            parse_csv(b"open,high,low,close\n1,1,1,1\n", "EURUSD", "M15", "p")  # no timestamp column
        with self.assertRaises(MarketDataError):
            parse_csv(csv_rows([]), "EURUSD", "M15", "../etc")  # a source is a name, not a path
        with self.assertRaises(MarketDataError):
            parse_csv(b"\xff\xfe", "EURUSD", "M15", "p")


class GapTests(unittest.TestCase):
    def test_weekly_closure_follows_new_york_time(self):
        self.assertTrue(forex_market_closed(datetime(2026, 1, 9, 22, 30, tzinfo=UTC)))   # Fri 17:30 NY (winter)
        self.assertFalse(forex_market_closed(datetime(2026, 1, 9, 21, 30, tzinfo=UTC)))  # Fri 16:30 NY
        self.assertFalse(forex_market_closed(datetime(2026, 7, 12, 21, 30, tzinfo=UTC)))  # Sun 17:30 NY (summer)
        self.assertTrue(forex_market_closed(datetime(2026, 7, 11, 12, 0, tzinfo=UTC)))    # Saturday

    def test_weekend_is_not_a_gap_but_a_missing_hour_is(self):
        friday = datetime(2026, 1, 9, 21, 0, tzinfo=UTC)   # last H1 bar before the close (16:00 NY)
        sunday = datetime(2026, 1, 11, 22, 0, tzinfo=UTC)  # first bar after the open (17:00 NY)
        self.assertEqual(find_gaps([friday, sunday], "H1"), [])
        monday = datetime(2026, 1, 12, 10, 0, tzinfo=UTC)
        gaps = find_gaps([monday, monday + timedelta(hours=3)], "H1")
        self.assertEqual(gaps[0]["missing"], 2)

    def test_daily_gaps_count_missing_weekdays(self):
        days = [datetime(2026, 1, 9, tzinfo=UTC), datetime(2026, 1, 12, tzinfo=UTC), datetime(2026, 1, 14, tzinfo=UTC)]
        gaps = find_gaps(days, "D1")
        self.assertEqual([g["missing"] for g in gaps], [1])  # Tuesday 13th; the weekend is not counted


class EcbTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.store = CandleStore(Path(self.temp.name) / "astra.sqlite3")
        self.store.initialize()
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, url, timeout):
        self.calls.append(url)
        return ECB_FIXTURE.encode()

    def age_last_attempt(self):
        with self.store._connect() as db:
            db.execute("UPDATE md_sources SET last_attempt_at = '2000-01-01T00:00:00+00:00' WHERE name = ?",
                       (marketdata.ECB_SOURCE,))

    def test_reference_rates_are_derived_and_timestamped_at_the_fix(self):
        candles = {(c.symbol, c.timestamp.date()): c for c in marketdata.ecb_candles(marketdata.parse_ecb_csv(ECB_FIXTURE.encode()))}
        jan = date(2026, 1, 15)
        self.assertAlmostEqual(candles[("EURUSD", jan)].close, 1.1)
        self.assertAlmostEqual(candles[("GBPUSD", jan)].close, 1.1 / 0.85, places=5)
        self.assertAlmostEqual(candles[("USDJPY", jan)].close, 165 / 1.1, places=3)
        self.assertAlmostEqual(candles[("USDCHF", jan)].close, 0.94 / 1.1, places=5)
        self.assertAlmostEqual(candles[("NZDUSD", jan)].close, 1.1 / 1.9, places=5)
        self.assertEqual(candles[("EURUSD", jan)].timestamp, datetime(2026, 1, 15, 13, 15, tzinfo=UTC))   # 14:15 CET
        self.assertEqual(candles[("EURUSD", date(2026, 7, 15))].timestamp, datetime(2026, 7, 15, 12, 15, tzinfo=UTC))  # CEST
        self.assertNotIn(("USDJPY", date(2026, 7, 15)), candles)  # a missing component is not invented
        eur = candles[("EURUSD", jan)]
        self.assertEqual((eur.open, eur.high, eur.low, eur.volume, eur.kind), (None, None, None, None, "REFERENCE_FIX"))

    def test_ingest_is_idempotent_and_never_real_time(self):
        first = marketdata.ingest_ecb(self.store, fetch=self.fetch)
        self.age_last_attempt()  # forced requests are spaced by one minute at least
        second = marketdata.ingest_ecb(self.store, fetch=self.fetch, force=True)
        self.assertEqual((first["status"], first["inserted"]), ("OK", 9))
        self.assertEqual((second["inserted"], second["already_stored"]), (0, 9))
        self.assertIn("startPeriod=2026-07-08", self.calls[1])  # resumes from the last stored day (minus a week)
        source = self.store.source(marketdata.ECB_SOURCE)
        fresh = marketdata.freshness(source, now=datetime(2026, 7, 16, tzinfo=UTC))
        self.assertEqual(fresh["state"], "UP_TO_DATE")
        self.assertIn("pas du temps réel", fresh["label"])
        self.assertEqual(marketdata.freshness(source, now=datetime(2026, 8, 1, tzinfo=UTC))["state"], "LATE")

    def test_unavailable_provider_fails_cleanly_and_backs_off(self):
        def down(url, timeout):
            raise urllib.error.URLError("network unreachable")

        slept = []
        result = marketdata.ingest_ecb(self.store, fetch=down, sleep=slept.append)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(len(slept), 2)  # three attempts, two waits
        source = self.store.source(marketdata.ECB_SOURCE)
        self.assertEqual(source["consecutive_failures"], 1)
        self.assertIn("network unreachable", source["last_error"])
        self.assertEqual(marketdata.ingest_ecb(self.store, fetch=self.fetch)["status"], "SKIPPED")
        self.age_last_attempt()
        self.assertEqual(marketdata.ingest_ecb(self.store, fetch=self.fetch, force=True)["status"], "OK")
        self.assertEqual(self.store.source(marketdata.ECB_SOURCE)["consecutive_failures"], 0)
        self.assertEqual(self.store.candles("EURUSD", "D1", marketdata.ECB_SOURCE)[0].close, 1.1)

    def test_malformed_answer_is_refused(self):
        result = marketdata.ingest_ecb(self.store, fetch=lambda url, timeout: b"<html>maintenance</html>",
                                       sleep=lambda _s: None)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(self.store.coverage(), [])


if __name__ == "__main__":
    unittest.main()
