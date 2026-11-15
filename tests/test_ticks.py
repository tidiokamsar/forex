"""LOT 1 acceptance tests T1 to T14: raw bid/ask ticks, specifications, account facts, fingerprints and costs."""

import gzip
import hashlib
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

from _support import workspace_tempdir
from astra import ticks
from astra.agent import AstraClient
from astra.brokers import mt5_demo
from astra.brokers.mt5_demo_orders import Mt5DemoTrader
from astra.mt5_bridge import Bridge
from astra.ticks import TickError, TickStore
from test_mt5_bridge import LOGIN, FakeServer, TradingMt5

ZONE = ZoneInfo("Europe/Athens")


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def payload(symbol, rows, bridge_ms=None):
    return {"server": "MetaQuotes-Demo", "bridge_ms": bridge_ms, "bridge_version": "test",
            "batches": [{"symbol": symbol, "ticks": rows}]}


def every_minute(start, minutes, spread_pips=1.0, skip=()):
    out, base = [], ms(start)
    for i in range(minutes):
        if i in skip:
            continue
        out.append([base + i * 60_000, 1.1, round(1.1 + spread_pips * 0.0001, 6), 6])
    return out


class TickMt5(TradingMt5):
    COPY_TICKS_ALL = 1

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ticks = {}  # symbol -> list of raw ticks (server clock ms)
        self.asked = []

    def copy_ticks_from(self, symbol, start_seconds, count, flags):
        self.asked.append((symbol, start_seconds, count))
        rows = [t for t in self.ticks.get(symbol, []) if t["time_msc"] >= start_seconds * 1000]
        return rows[:count]

    def symbol_info_tick(self, symbol):
        rows = self.ticks.get(symbol)
        if not rows:
            return super().symbol_info_tick(symbol)
        last = rows[-1]
        return type("Tick", (), {"bid": last["bid"], "ask": last["ask"], "time": last["time_msc"] // 1000,
                                 "time_msc": last["time_msc"]})()


def raw(server_wall, bid=1.1, ask=1.10002, flags=6):
    return {"time_msc": ms(server_wall), "bid": bid, "ask": ask, "flags": flags}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.dir = Path(self.temp.name)
        self.store = TickStore(self.dir / "ticks.sqlite3", self.dir / "archive")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()


class RecorderTests(StoreCase):
    def test_t1_bridge_reads_raw_ticks_converts_server_time_and_keeps_its_cursor(self):
        fake = TickMt5()
        fake.ticks["EURUSD"] = [raw("2026-11-18 12:00:00.250"), raw("2026-11-18 12:00:01.500", 1.10001, 1.10003)]
        server = FakeServer()
        bridge = Bridge(AstraClient(server), Mt5DemoTrader(module=fake, expected_login=LOGIN), self.dir,
                        clock=lambda: datetime(2026, 11, 18, 10, 0, 5, tzinfo=UTC))
        with bridge.trader:
            self.assertEqual(bridge.sync_ticks()["ticks"], 2)
        posted = [b for p, b in server.posts if p == "/api/mt5/ticks"]
        self.assertEqual(len(posted), 1)
        batch = posted[0]["batches"][0]
        self.assertEqual(batch["symbol"], "EURUSD")
        # winter: server clock UTC+2, so 12:00:00.250 on the server is 10:00:00.250 UTC, to the millisecond
        self.assertEqual(batch["ticks"][0], [ms("2026-11-18 10:00:00.250"), 1.1, 1.10002, 6])
        state = json.loads((self.dir / "bridge-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["tick_cursor"]["EURUSD"], ms("2026-11-18 12:00:01.500"))  # server clock, survives restarts
        result = self.store.record(posted[0], ms("2026-11-18 10:00:06"))
        self.assertEqual((result["stored"], result["rejected"]), (2, {}))
        self.assertEqual(self.store.ticks("EURUSD", "2026-11-18")[1], (ms("2026-11-18 10:00:01.500"), 1.10001, 1.10003, 6))

    def test_t2_the_same_batch_sent_twice_is_stored_once(self):
        rows = every_minute("2026-11-18 10:00", 5)
        self.assertEqual(self.store.record(payload("EURUSD", rows), ms("2026-11-18 10:06"))["stored"], 5)
        again = self.store.record(payload("EURUSD", rows), ms("2026-11-18 10:07"))
        self.assertEqual((again["stored"], again["duplicates"]), (0, 5))
        self.assertEqual(len(self.store.ticks("EURUSD", "2026-11-18")), 5)

    def test_t3_a_sealed_day_is_never_rewritten(self):
        self.store.record(payload("EURUSD", every_minute("2026-11-18 10:00", 3)), ms("2026-11-18 10:05"))
        first = self.store.seal("EURUSD", "2026-11-18", "MetaQuotes-Demo", "test")
        packed = Path(first["path"]).read_bytes()
        self.store.record(payload("EURUSD", [[ms("2026-11-18 23:00"), 1.1, 1.1001, 6]]), ms("2026-11-19 02:00"))
        again = self.store.seal("EURUSD", "2026-11-18", "MetaQuotes-Demo", "test")
        self.assertTrue(again["already_sealed"])
        self.assertFalse(again["same_content"])
        self.assertEqual(again["sha256"], first["sha256"])
        self.assertEqual(Path(first["path"]).read_bytes(), packed)
        with self.store._connect() as db:
            reasons = dict(db.execute("SELECT reason, n FROM tick_rejects WHERE day = '2026-11-18'").fetchall())
        self.assertEqual(reasons, {"apres_scellement": 1, "scelle_different": 1})

    def test_t4_bad_ticks_are_refused_and_counted_never_stored(self):
        t = ms("2026-11-18 10:00")
        rows = [[t, 1.1, 1.0999, 6], [t + 1, float("nan"), 1.1, 6], [t + 2, 0.0, 1.1, 6], [t + 3, 1.1, 1.1001],
                [t + 10, 1.1, 1.1001, 6], [t + 5, 1.1, 1.1001, 6], ["x", 1.1, 1.1001, 6]]
        result = self.store.record(payload("EURUSD", rows), t + 60_000)
        self.assertEqual(result["stored"], 1)
        self.assertEqual(result["rejected"], {"ask_inferieur_bid": 1, "non_fini": 1, "prix_nul": 1, "format": 2,
                                              "desordre": 1})
        status = self.store.status(t + 60_000)
        self.assertEqual(sum(status["rejected_today"].values()), 6)
        with self.assertRaises(TickError):
            self.store.record({**payload("EURUSD", rows), "server": "Some-Real-Server"}, t)
        with self.assertRaises(TickError):
            self.store.record(payload("NOTASYMBOL", rows), t)


class QualityTests(StoreCase):
    def test_t5_gaps_are_found_and_labelled(self):
        # Wednesday 18-11: a tick a minute from 00:00 UTC to 23:59, but nothing from 13:00 to 13:20 UTC
        rows = every_minute("2026-11-18 00:00", 24 * 60, skip=set(range(13 * 60, 13 * 60 + 20)))
        for start in range(0, len(rows), 700):
            self.store.record(payload("EURUSD", rows[start:start + 700]), ms("2026-11-19 00:00"))
        gaps = self.store.gaps("EURUSD", "2026-11-18")
        self.assertEqual([(g["from"][11:16], g["label"]) for g in gaps], [("12:59", "inexplique")])
        # a silence around the server's midnight (22:00 UTC in winter) is the rollover
        rows = every_minute("2026-11-19 00:00", 24 * 60, skip=set(range(21 * 60 + 55, 22 * 60 + 10)))
        for start in range(0, len(rows), 700):
            self.store.record(payload("EURUSD", rows[start:start + 700]), ms("2026-11-20 00:00"))
        self.assertEqual([g["label"] for g in self.store.gaps("EURUSD", "2026-11-19")], ["rollover"])
        # Saturday: no tick at all, the market is closed, nothing unexplained
        self.assertEqual({g["label"] for g in self.store.gaps("EURUSD", "2026-11-21")}, {"week-end"})
        # a bridge down all Wednesday evening is a gap, even with no tick after it
        self.store.record(payload("GBPUSD", every_minute("2026-11-18 08:00", 60)), ms("2026-11-18 20:00"))
        late = self.store.gaps("GBPUSD", "2026-11-18", now_ms=ms("2026-11-18 20:00"))
        self.assertEqual([g["label"] for g in late], ["inexplique", "inexplique"])

    def test_t6_freshness_is_published_and_only_counts_while_the_market_is_open(self):
        self.store.record(payload("EURUSD", [[ms("2026-11-18 10:00:00"), 1.1, 1.1001, 6]]), ms("2026-11-18 10:00:01"))
        wednesday = {i["symbol"]: i for i in self.store.status(ms("2026-11-18 10:00:10"))["instruments"]}
        self.assertTrue(wednesday["EURUSD"]["stale"])
        self.assertEqual(wednesday["EURUSD"]["last_tick_age_s"], 10.0)
        fresh = {i["symbol"]: i for i in self.store.status(ms("2026-11-18 10:00:03"))["instruments"]}
        self.assertFalse(fresh["EURUSD"]["stale"])
        saturday = self.store.status(ms("2026-11-21 10:00"))
        self.assertFalse(any(i["stale"] for i in saturday["instruments"]))

    def test_t7_the_clock_offset_is_measured_per_day(self):
        t = ms("2026-11-18 10:00")
        for delay in (120, 300, 900):
            self.store.record(payload("EURUSD", [[t, 1.1, 1.1001, 6]]), t + delay)
            t += 1000
        clock = self.store.status(ms("2026-11-18 11:00"))["clock"]
        self.assertEqual((clock["batches"], clock["median_ms"], clock["max_ms"]), (3, 300, 900))

    def test_t12_daylight_saving_change_keeps_utc_continuous(self):
        friday = ticks.server_ms_to_utc_ms(ms("2026-10-23 23:59:00"), ZONE)  # summer, UTC+3
        monday = ticks.server_ms_to_utc_ms(ms("2026-10-26 00:00:00"), ZONE)  # winter, UTC+2
        self.assertEqual(friday, ms("2026-10-23 20:59:00"))
        self.assertEqual(monday, ms("2026-10-25 22:00:00"))
        self.assertEqual(ticks.SERVER_ZONES, mt5_demo.SERVER_ZONES)
        self.assertFalse(ticks.market_open(ms("2026-10-24 12:00"), ZONE))
        self.assertTrue(ticks.market_open(ms("2026-10-25 22:00"), ZONE))


class SpecsAndFingerprintTests(StoreCase):
    ITEM: ClassVar[dict] = {"name": "EURUSD", "group": "Forex", "contract_size": 100000.0, "digits": 5, "volume_min": 0.01,
            "swap_long": -6.1, "swap_short": 1.2, "swap_mode": 1, "spread": 2, "margin_initial": 0.0}

    def test_t8_specifications_are_versioned_and_old_versions_kept(self):
        day1, day2, day3 = (datetime(2026, 11, d, 1, tzinfo=UTC) for d in (16, 17, 18))
        self.store.record_specs("MetaQuotes-Demo", [self.ITEM], None, day1)
        self.store.record_specs("MetaQuotes-Demo", [{**self.ITEM, "spread": 9}], None, day2)  # market data, no version
        self.assertEqual(len(self.store.spec_history("MetaQuotes-Demo", "EURUSD")), 1)
        new = self.store.record_specs("MetaQuotes-Demo", [{**self.ITEM, "swap_long": -7.4}], None, day3)
        self.assertEqual(new, {"spec_versions_new": 1})
        history = self.store.spec_history("MetaQuotes-Demo", "EURUSD")
        self.assertEqual([h["spec"]["swap_long"] for h in history], [-6.1, -7.4])
        self.assertEqual(history[0]["last_seen"], day2.isoformat())

    def test_t9_account_server_and_margin_mode_are_recorded_and_labelled(self):
        self.store.record_specs("MetaQuotes-Demo", [self.ITEM], {"company": "MetaQuotes Ltd.", "margin_mode": 2,
                                                                 "currency": "USD"}, datetime(2026, 11, 18, tzinfo=UTC))
        account = self.store.status(ms("2026-11-18 10:00"))["account"]
        self.assertEqual((account["margin_mode"], account["costs"]), ("HEDGING", "NON_REPRESENTATIF"))
        fake = TickMt5()
        server = FakeServer()
        fake.symbols = ()
        bridge = Bridge(AstraClient(server), Mt5DemoTrader(module=fake, expected_login=LOGIN), self.dir)
        with bridge.trader:
            self.assertIn("margin_mode", bridge.trader.account)
            bridge.sync_catalogue()

    def test_t10_a_day_has_a_stable_fingerprint_that_replays(self):
        rows = every_minute("2026-11-18 10:00", 30)
        self.store.record(payload("EURUSD", rows), ms("2026-11-18 11:00"))
        seal = self.store.seal("EURUSD", "2026-11-18", "MetaQuotes-Demo", "test")
        self.assertEqual(seal["sha256"], self.store.fingerprint("EURUSD", "2026-11-18")[0])
        content = gzip.decompress(Path(seal["path"]).read_bytes())
        self.assertEqual(hashlib.sha256(content).hexdigest(), seal["sha256"])
        provenance = json.loads(Path(seal["path"]).with_name("2026-11-18.json").read_text(encoding="utf-8"))
        self.assertEqual((provenance["server"], provenance["representative"]), ("MetaQuotes-Demo", False))
        self.assertEqual(self.store.verify_seals(), [{"symbol": "EURUSD", "day": "2026-11-18", "sha256": seal["sha256"],
                                                      "file_ok": True, "database_ok": True, "rows": 30}])
        other = TickStore(self.dir / "other.sqlite3", self.dir / "other-archive")
        other.initialize()
        other.record(payload("EURUSD", list(reversed(rows))[::-1]), ms("2026-11-18 11:00"))
        copy = other.seal("EURUSD", "2026-11-18", "MetaQuotes-Demo", "test")
        self.assertEqual(Path(copy["path"]).read_bytes(), Path(seal["path"]).read_bytes())  # byte for byte

    def test_finished_days_are_sealed_one_hour_after_their_end(self):
        self.store.record(payload("EURUSD", every_minute("2026-11-18 10:00", 3)), ms("2026-11-18 10:05"))
        self.assertEqual(self.store.record(payload("EURUSD", every_minute("2026-11-19 00:30", 1)),
                                           ms("2026-11-19 00:31"))["sealed"], 0)
        self.assertEqual(self.store.record(payload("EURUSD", every_minute("2026-11-19 01:30", 1)),
                                           ms("2026-11-19 01:31"))["sealed"], 1)

    def test_t11_costs_per_hour_come_from_the_ticks(self):
        self.store.record(payload("EURUSD", every_minute("2026-11-18 10:00", 60, spread_pips=0.2)), ms("2026-11-18 11:00"))
        self.store.record(payload("EURUSD", every_minute("2026-11-18 21:00", 60, spread_pips=3.5)), ms("2026-11-18 22:00"))
        costs = self.store.costs("EURUSD", ["2026-11-18"])
        self.assertAlmostEqual(costs["by_hour_utc"][10]["median"], 0.2, places=3)
        self.assertAlmostEqual(costs["by_hour_utc"][21]["p90"], 3.5, places=3)
        self.assertEqual(costs["by_hour_utc"][10]["samples"], 60)
        self.assertEqual(costs["costs"], "NON_REPRESENTATIF")


class VolumeAndIsolationTests(StoreCase):
    def test_t13_posts_are_bounded_and_old_ticks_leave_the_database(self):
        with self.assertRaises(TickError):
            self.store.record(payload("EURUSD", every_minute("2026-11-18 00:00", ticks.MAX_TICKS_PER_POST + 1)), 0)
        self.store.record(payload("EURUSD", every_minute("2026-11-01 10:00", 5)), ms("2026-11-01 11:00"))
        self.assertEqual(len(self.store.ticks("EURUSD", "2026-11-01")), 5)
        self.store.record(payload("EURUSD", every_minute("2026-11-18 10:00", 5)), ms("2026-11-18 11:00"))
        self.assertEqual(self.store.purge(ms("2026-11-18 11:00"), force=True), 0)  # already done by the last record
        self.assertEqual(len(self.store.ticks("EURUSD", "2026-11-01")), 0)
        self.assertEqual(len(self.store.ticks("EURUSD", "2026-11-18")), 5)
        self.assertTrue(Path(self.dir / "archive" / "EURUSD" / "2026-11-01.csv.gz").exists())  # sealed before leaving
        fake = TickMt5()
        start = ms("2026-11-18 12:00")
        fake.ticks["EURUSD"] = [{"time_msc": start + i * 100, "bid": 1.1, "ask": 1.10002, "flags": 6} for i in range(1900)]
        server = FakeServer()
        bridge = Bridge(AstraClient(server), Mt5DemoTrader(module=fake, expected_login=LOGIN), self.dir / "b")
        with bridge.trader:
            bridge.state["tick_cursor"]["EURUSD"] = start
            self.assertEqual(bridge.sync_ticks()["ticks"], 1900)
        sizes = [sum(len(b["ticks"]) for b in body["batches"]) for p, body in server.posts if p == "/api/mt5/ticks"]
        self.assertEqual(sizes, [800, 800, 300])

    def test_t14_a_failing_recorder_never_stops_the_stream(self):
        class Failing(FakeServer):
            def __call__(self, method, path, body):
                if path == "/api/mt5/ticks":
                    return 503, {"error": "down"}
                return super().__call__(method, path, body)

        fake = TickMt5()
        fake.ticks["EURUSD"] = [raw("2026-10-29 11:00:00")]
        server = Failing()
        bridge = Bridge(AstraClient(server), Mt5DemoTrader(module=fake, expected_login=LOGIN), self.dir,
                        clock=lambda: datetime(2026, 10, 29, 9, tzinfo=UTC))
        clock = {"t": 0.0}
        bridge.flow(300, lambda: clock["t"], lambda s: clock.update(t=clock["t"] + 5), rounds=2)
        self.assertEqual(sum(1 for p, _ in server.posts if p == "/api/mt5/quotes"), 2)
        self.assertEqual(bridge.state["tick_cursor"], {})  # nothing accepted: the same ticks go again next time
        journal = (self.dir / "journal-2026-10-29.jsonl").read_text(encoding="utf-8")
        self.assertIn("ticks_failed", journal)


if __name__ == "__main__":
    unittest.main()
