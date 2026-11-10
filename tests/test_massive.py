"""Massive client against a fake provider: closed bars only, key never exposed, pagination, plan-limited access."""

import json
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from _support import workspace_tempdir
from astra import massive
from astra.marketdata import CandleStore

KEY = "k3y_ABCDEFGHIJKLMNOP0123456789"
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def bar(stamp, o=1.1, h=1.102, low=1.099, c=1.101, v=50):
    return {"t": ms(stamp), "o": o, "h": h, "l": low, "c": c, "v": v, "vw": c, "n": v}


class FakeProvider:
    def __init__(self, pages, status=200):
        self.pages, self.status, self.requests = list(pages), status, []

    def __call__(self, request, timeout):
        self.requests.append((request.full_url, dict(request.header_items())))
        if self.status != 200:
            return self.status, b'{"status":"NOT_AUTHORIZED","message":"upgrade your plan"}'
        return 200, json.dumps(self.pages.pop(0)).encode()


class ClientTests(unittest.TestCase):
    def client(self, provider):
        return massive.MassiveClient(KEY, opener=provider, sleep=lambda s: None)

    def test_key_goes_in_the_header_never_in_the_url(self):
        provider = FakeProvider([{"status": "OK", "results": [bar("2026-10-28T10:00:00")]}])
        rows = self.client(provider).aggregates("eurusd", "H1", date(2026, 10, 28), date(2026, 10, 29))
        url, headers = provider.requests[0]
        self.assertNotIn(KEY, url)
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
        self.assertIn("/v2/aggs/ticker/C:EURUSD/range/1/hour/2026-10-28/2026-10-29", url)
        self.assertEqual(len(rows), 1)

    def test_pagination_is_followed_but_never_to_another_host(self):
        base = "https://api.massive.com"
        pages = [{"status": "OK", "results": [bar("2026-10-27T10:00:00")], "next_url": f"{base}/v2/aggs/next?cursor=abc"},
                 {"status": "OK", "results": [bar("2026-10-27T11:00:00")]}]
        provider = FakeProvider(pages)
        self.assertEqual(len(self.client(provider).aggregates("EURUSD", "H1", date(2026, 10, 27), date(2026, 10, 28))), 2)
        evil = FakeProvider([{"status": "OK", "results": [], "next_url": "https://evil.example/steal"}])
        with self.assertRaises(massive.MassiveError):
            self.client(evil).aggregates("EURUSD", "H1", date(2026, 10, 27), date(2026, 10, 28))
        self.assertEqual(len(evil.requests), 1)

    def test_plan_limited_access_and_errors_are_reported_without_the_key(self):
        denied = FakeProvider([], status=403)
        with self.assertRaises(massive.MassiveError) as caught:
            self.client(denied).aggregates("EURUSD", "M5", date(2026, 10, 28), date(2026, 10, 29))
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn(KEY, str(caught.exception))
        with self.assertRaises(massive.MassiveError):
            self.client(FakeProvider([])).aggregates("FOOBAR", "H1", date(2026, 10, 28), date(2026, 10, 29))
        with self.assertRaises(massive.MassiveError):
            massive.MassiveClient(KEY, base_url="http://api.massive.com")

    def test_a_request_limit_slows_the_client_down_for_good_and_gives_up_after_six_attempts(self):
        class Throttled(FakeProvider):
            def __init__(self, refusals, page):
                super().__init__([page])
                self.refusals = refusals

            def __call__(self, request, timeout):
                self.requests.append(request.full_url)
                if self.refusals > 0:
                    self.refusals -= 1
                    return 429, b'{"status":"ERROR"}'
                return 200, json.dumps(self.pages[0]).encode()

        sleeps, now = [], {"t": 0.0}
        page = {"status": "OK", "results": [bar("2026-10-27T10:00:00")]}
        provider = Throttled(1, page)
        client = massive.MassiveClient(KEY, opener=provider, sleep=lambda s: (sleeps.append(s), now.update(t=now["t"] + s)),
                                       clock=lambda: now["t"])
        self.assertEqual(len(client.aggregates("EURUSD", "H1", date(2026, 10, 27), date(2026, 10, 28))), 1)
        self.assertEqual(sleeps, [massive.THROTTLED_INTERVAL])  # waited once, then succeeded
        for symbol in ("GBPUSD", "USDJPY"):  # once refused, every request is spaced by the interval
            sleeps.clear()
            client.aggregates(symbol, "H1", date(2026, 10, 27), date(2026, 10, 28))
            self.assertEqual(sleeps, [massive.THROTTLED_INTERVAL])
        sleeps.clear()
        now["t"] += 60  # a long pause: no need to wait any more
        client.aggregates("AUDUSD", "H1", date(2026, 10, 27), date(2026, 10, 28))
        self.assertEqual(sleeps, [])
        hopeless = Throttled(99, page)
        with self.assertRaises(massive.MassiveError) as caught:
            massive.MassiveClient(KEY, opener=hopeless, sleep=lambda s: None).aggregates(
                "EURUSD", "H1", date(2026, 10, 27), date(2026, 10, 28))
        self.assertEqual((caught.exception.status, len(hopeless.requests)), (429, massive.ATTEMPTS))

    def test_check_reports_each_timeframe(self):
        pages = [{"status": "OK", "results": [bar("2026-10-29T09:00:00")]}]
        out = massive.check(self.client(FakeProvider(pages * 5)), today=date(2026, 10, 29))
        self.assertTrue(out["any_accessible"])
        self.assertEqual(out["series"]["H1"]["bars"], 1)
        none = massive.check(self.client(FakeProvider([], status=403)), today=date(2026, 10, 29))
        self.assertFalse(none["any_accessible"])
        self.assertEqual(none["series"]["D1"]["status"], 403)


class ConversionTests(unittest.TestCase):
    def test_only_closed_coherent_bars_are_kept_with_utc_times(self):
        rows = [bar("2026-10-29T08:00:00"), bar("2026-10-29T08:00:00"),  # repeated start
                bar("2026-10-29T09:00:00"), bar("2026-10-29T10:00:00"),  # forming at 10:00
                bar("2026-10-29T07:00:00", h=1.0), {"t": "x"}]
        candles, rejected = massive.to_candles(rows, "EURUSD", "H1", now=NOW)
        self.assertEqual([c.timestamp.hour for c in candles], [8, 9])
        self.assertEqual(rejected, 3)
        self.assertEqual({c.source for c in candles}, {massive.SOURCE})


class IngestTests(unittest.TestCase):
    def test_ingest_stores_idempotently_and_labels_the_source(self):
        with workspace_tempdir() as temp:
            store = CandleStore(Path(temp) / "astra.sqlite3")
            store.initialize()
            page = {"status": "OK", "results": [bar("2026-10-29T07:00:00"), bar("2026-10-29T08:00:00")]}
            client = massive.MassiveClient(KEY, opener=FakeProvider([page, page]), sleep=lambda s: None)
            first = massive.ingest(store, client, "EURUSD", "H1", date(2026, 10, 29), date(2026, 10, 29), now=NOW)
            again = massive.ingest(store, client, "EURUSD", "H1", date(2026, 10, 29), date(2026, 10, 29), now=NOW)
            self.assertEqual((first["status"], first["inserted"], again["inserted"], again["already_stored"]), ("OK", 2, 0, 2))
            source = store.source(massive.SOURCE)
            self.assertEqual(source["nature"], "HISTORICAL")
            self.assertIn("pas des transactions", source["description"])
            failed = massive.ingest(store, massive.MassiveClient(KEY, opener=FakeProvider([], status=401)),
                                    "EURUSD", "H1", date(2026, 10, 29), date(2026, 10, 29), now=NOW)
            self.assertEqual(failed["status"], "FAILED")
            self.assertNotIn(KEY, json.dumps(failed))
            self.assertEqual(store.source(massive.SOURCE)["consecutive_failures"], 1)


class SyncTests(unittest.TestCase):
    TODAY = date(2026, 11, 1)
    SYNC_NOW = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)

    def store(self, temp):
        store = CandleStore(Path(temp) / "astra.sqlite3")
        store.initialize()
        return store

    def test_each_series_resumes_from_its_last_stored_day_and_stops_at_yesterday(self):
        with workspace_tempdir() as temp:
            store = self.store(temp)
            seed = FakeProvider([{"status": "OK", "results": [bar("2026-10-28T22:00:00"), bar("2026-10-28T23:00:00")]}])
            massive.ingest(store, massive.MassiveClient(KEY, opener=seed, sleep=lambda s: None), "EURUSD", "H1",
                           date(2026, 10, 28), date(2026, 10, 28), now=NOW)
            page = {"status": "OK", "results": [bar("2026-10-29T10:00:00"), bar("2026-10-31T09:00:00")]}
            provider = FakeProvider([page] * 14)
            report = massive.sync_incremental(store, massive.MassiveClient(KEY, opener=provider, sleep=lambda s: None),
                                              symbols=("EURUSD",), today=self.TODAY, now=self.SYNC_NOW)
            urls = [u for u, _h in provider.requests]
            self.assertEqual(report["up_to"], "2026-10-31")
            self.assertIn("/range/1/hour/2026-10-28/2026-10-31", urls[1])  # H1 resumes at its last stored day
            self.assertIn("/range/1/day/2026-10-01/2026-10-31", urls[0])  # D1 never imported: the last 30 days
            self.assertIsNone(report["failed"])
            self.assertEqual({s["status"] for s in report["series"]}, {"OK"})

    def test_a_series_already_up_to_yesterday_is_not_requested(self):
        with workspace_tempdir() as temp:
            store = self.store(temp)
            page = {"status": "OK", "results": [bar("2026-10-31T00:00:00")]}
            client = massive.MassiveClient(KEY, opener=FakeProvider([page]), sleep=lambda s: None)
            massive.ingest(store, client, "EURUSD", "D1", date(2026, 10, 31), date(2026, 10, 31), now=self.SYNC_NOW)
            provider = FakeProvider([])
            report = massive.sync_incremental(store, massive.MassiveClient(KEY, opener=provider, sleep=lambda s: None),
                                              symbols=("EURUSD",), timeframes=("D1",), today=self.TODAY, now=self.SYNC_NOW)
            self.assertEqual(report["series"][0]["status"], "A_JOUR")
            self.assertEqual(provider.requests, [])

    def test_the_first_failure_stops_the_run_without_hammering_the_provider(self):
        with workspace_tempdir() as temp:
            store = self.store(temp)
            provider = FakeProvider([], status=401)
            report = massive.sync_incremental(store, massive.MassiveClient(KEY, opener=provider, sleep=lambda s: None),
                                              today=self.TODAY, now=self.SYNC_NOW)
            self.assertIn("EURUSD", report["failed"])
            self.assertEqual(report["not_attempted"], 2 * len(massive.SYMBOL_MAP) - 1)
            self.assertEqual(len(provider.requests), 1)
            self.assertNotIn(KEY, json.dumps(report))


class KeyFileTests(unittest.TestCase):
    def test_key_loading(self):
        with workspace_tempdir() as temp:
            path = Path(temp) / "massive.key"
            path.write_text(KEY + "\n", encoding="utf-8")
            self.assertEqual(massive.load_key(path), KEY)
            for bad in ("short", "x" * 200, "has space in the middle of it......"):
                path.write_text(bad, encoding="utf-8")
                with self.assertRaises(massive.MassiveError):
                    massive.load_key(path)
            with self.assertRaises(massive.MassiveError):
                massive.load_key(Path(temp) / "absent.key")


if __name__ == "__main__":
    unittest.main()
