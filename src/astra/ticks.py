"""Market data and truth: raw bid/ask ticks of the DEMO account's server, daily specifications and costs (LOT 1).

What the broker really quoted, kept as it came: every tick with its server time in milliseconds (converted to UTC),
bid, ask and flags. Nothing here trades, and nothing here changes a stored tick:

- ``record``: a batch from the bridge is checked tick by tick; a bad tick is refused and counted with its reason,
  never stored silently and never "repaired". The same tick sent twice is stored once.
- ``seal``: once a UTC day is over, its ticks are written to a compressed CSV with a SHA-256 of the content and the
  provenance (server, bridge version). A sealed file is never rewritten; a different content for a sealed day is
  refused and counted.
- ``gaps``: silences longer than ``GAP_SECONDS`` while the market is open, each labelled (week-end, rollover, or
  unexplained). Nothing is filled in.
- ``costs``: median and 90th percentile spread per UTC hour, measured from the ticks (tick weighted).
- ``record_specs``: contract specifications and swaps, one version per change, older versions kept.

The ticks live in their own database next to the main one (millions of rows a month would bloat the main database and
its backups). Ticks older than ``TICK_DB_DAYS`` leave this database; the sealed daily files stay.

The MetaQuotes demonstration server is not a broker: costs measured there are labelled NON_REPRESENTATIF.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import itertools
import json
import math
import sqlite3
import statistics
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .marketdata import INSTRUMENTS

SERVER_ZONES = {"MetaQuotes-Demo": "Europe/Athens"}  # same as brokers.mt5_demo.SERVER_ZONES (checked by a test)
NON_REPRESENTATIVE_SERVERS = frozenset({"MetaQuotes-Demo"})  # a platform vendor's demo server, not a broker
MAX_HOUR_ROWS = 500
MAX_TICKS_PER_POST = 800      # about 45 KB of JSON: under the proxy's 64 KB body limit
TICK_DB_DAYS = 14             # PRESET: raw ticks kept in the database this long; sealed daily files are kept
STALE_SECONDS = 5             # PRESET: a quote older than this while the market is open is stale
GAP_SECONDS = 300             # PRESET: a silence longer than this while the market is open is a gap
SEAL_DELAY = timedelta(hours=1)  # a UTC day is sealed one hour after it ends (late ticks still arrive)
ROLLOVER_MINUTES = 30         # server clock: within this many minutes of midnight, a gap is labelled rollover
PURGE_EVERY = timedelta(hours=1)
CSV_HEADER = ("time_utc_ms", "bid", "ask", "flags")
SPEC_EXCLUDED = ("spread",)   # the current spread is market data, not a specification

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    symbol TEXT NOT NULL, t INTEGER NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL, flags INTEGER NOT NULL,
    PRIMARY KEY (symbol, t, bid, ask)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tick_batches (
    received INTEGER NOT NULL, server TEXT NOT NULL, symbol TEXT NOT NULL, n INTEGER NOT NULL,
    first_t INTEGER, last_t INTEGER, bridge_ms INTEGER);
CREATE INDEX IF NOT EXISTS tick_batches_received ON tick_batches(received);
CREATE TABLE IF NOT EXISTS tick_rejects (
    day TEXT NOT NULL, symbol TEXT NOT NULL, reason TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, symbol, reason));
CREATE TABLE IF NOT EXISTS tick_seals (
    symbol TEXT NOT NULL, day TEXT NOT NULL, sha256 TEXT NOT NULL, rows INTEGER NOT NULL, path TEXT NOT NULL,
    provenance TEXT NOT NULL, sealed_at TEXT NOT NULL, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS spec_versions (
    server TEXT NOT NULL, name TEXT NOT NULL, spec_hash TEXT NOT NULL, spec_json TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY (server, name, spec_hash));
CREATE TABLE IF NOT EXISTS account_facts (
    server TEXT NOT NULL, company TEXT NOT NULL, margin_mode INTEGER NOT NULL, currency TEXT NOT NULL,
    representative INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY (server, company, margin_mode, currency));
CREATE TABLE IF NOT EXISTS tick_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tick_hours (
    symbol TEXT NOT NULL, hour TEXT NOT NULL, first_bid REAL NOT NULL, first_ask REAL NOT NULL, last_bid REAL NOT NULL,
    last_ask REAL NOT NULL, median_spread_pips REAL NOT NULL, p90_spread_pips REAL NOT NULL, ticks INTEGER NOT NULL,
    PRIMARY KEY (symbol, hour)) WITHOUT ROWID;
"""

MARGIN_MODES = {0: "NETTING", 1: "EXCHANGE", 2: "HEDGING"}  # MetaTrader 5 ACCOUNT_MARGIN_MODE_*


class TickError(ValueError):
    """A batch that cannot be read at all (a single bad tick is refused and counted, not raised)."""


def server_ms_to_utc_ms(raw_ms: int, zone: ZoneInfo) -> int:
    """A tick time written in the trade server's clock (as if it were UTC, as MT5 does) to real UTC milliseconds."""

    wall = datetime.fromtimestamp(raw_ms // 1000, UTC).replace(tzinfo=None)
    offset = zone.utcoffset(wall)
    return raw_ms - int(offset.total_seconds() * 1000)


def _server_wall(utc_ms: int, zone: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(utc_ms / 1000, UTC).astimezone(zone)


def market_open(utc_ms: int, zone: ZoneInfo) -> bool:
    """Forex on MetaQuotes-Demo: open from Monday 00:00 to Friday 23:59 in the server's clock."""

    return _server_wall(utc_ms, zone).weekday() < 5


def _near_rollover(utc_ms: int, zone: ZoneInfo) -> bool:
    wall = _server_wall(utc_ms, zone)
    minutes = wall.hour * 60 + wall.minute
    return minutes < ROLLOVER_MINUTES or minutes >= 24 * 60 - ROLLOVER_MINUTES


def _day(utc_ms: int) -> str:
    return datetime.fromtimestamp(utc_ms / 1000, UTC).strftime("%Y-%m-%d")


def _day_bounds(day: str) -> tuple[int, int]:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_tick(raw: Any, previous_t: int | None) -> tuple[tuple[int, float, float, int] | None, str | None]:
    """One tick [utc_ms, bid, ask, flags]: the clean tuple, or the reason it is refused."""

    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None, "format"
    t, bid, ask, flags = raw
    if not isinstance(t, int) or isinstance(t, bool) or not isinstance(flags, int) or isinstance(flags, bool):
        return None, "format"
    if not _finite(bid) or not _finite(ask):
        return None, "non_fini"
    if bid <= 0 or ask <= 0:
        return None, "prix_nul"
    if ask < bid:
        return None, "ask_inferieur_bid"
    if previous_t is not None and t < previous_t:
        return None, "desordre"
    return (t, float(bid), float(ask), flags), None


def _canonical_csv(rows: list[tuple[int, float, float, int]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for t, bid, ask, flags in rows:
        writer.writerow((t, repr(bid), repr(ask), flags))
    return out.getvalue().encode("ascii")


def spec_hash(item: dict[str, Any]) -> str:
    kept = {k: v for k, v in sorted(item.items()) if k not in SPEC_EXCLUDED}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TickStore:
    def __init__(self, path: str | Path, archive_dir: str | Path | None = None) -> None:
        self.path = Path(path)
        self.archive = Path(archive_dir) if archive_dir else self.path.parent / "ticks-archive"

    @classmethod
    def beside(cls, database: str | Path) -> TickStore:
        """The tick database and archive next to the main database."""

        base = Path(database).parent
        return cls(base / "ticks.sqlite3", base / "ticks-archive")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout = 30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(_SCHEMA)

    # -- ticks ---------------------------------------------------------------------------------------------------

    def record(self, payload: Any, now_ms: int) -> dict[str, Any]:
        """Store a batch from the bridge: {"server", "bridge_ms", "bridge_version", "batches": [{"symbol", "ticks"}]}."""

        if not isinstance(payload, dict):
            raise TickError("ticks : objet JSON attendu")
        server = payload.get("server")
        if server not in SERVER_ZONES:
            raise TickError("ticks : serveur non vérifié")
        batches = payload.get("batches")
        if not isinstance(batches, list) or not batches or len(batches) > len(INSTRUMENTS):
            raise TickError(f"ticks : liste de 1 à {len(INSTRUMENTS)} lots attendue")
        total = sum(len(b.get("ticks") or []) for b in batches if isinstance(b, dict))
        if total > MAX_TICKS_PER_POST:
            raise TickError(f"ticks : au plus {MAX_TICKS_PER_POST} par envoi")
        bridge_ms = payload.get("bridge_ms") if isinstance(payload.get("bridge_ms"), int) else None
        stored = duplicates = 0
        rejected: dict[str, int] = {}
        with self._connect() as db:
            sealed = {(r[0], r[1]) for r in db.execute("SELECT symbol, day FROM tick_seals")}
            for batch in batches:
                symbol = batch.get("symbol") if isinstance(batch, dict) else None
                ticks = batch.get("ticks") if isinstance(batch, dict) else None
                if symbol not in INSTRUMENTS or not isinstance(ticks, list):
                    raise TickError("ticks : lot illisible (symbole inconnu ou liste absente)")
                previous, clean = None, []
                for raw in ticks:
                    tick, reason = check_tick(raw, previous)
                    if tick is None:
                        rejected[reason] = rejected.get(reason, 0) + 1
                        day = _day(raw[0]) if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], int) else _day(now_ms)
                        self._count_reject(db, day, symbol, reason)
                        continue
                    previous = tick[0]
                    clean.append(tick)
                for t, bid, ask, flags in clean:
                    cursor = db.execute("INSERT OR IGNORE INTO ticks(symbol, t, bid, ask, flags) VALUES (?,?,?,?,?)",
                                        (symbol, t, bid, ask, flags))
                    if cursor.rowcount:
                        stored += 1
                        if (symbol, _day(t)) in sealed:  # kept in the database, the sealed file is not rewritten
                            self._count_reject(db, _day(t), symbol, "apres_scellement")
                    else:
                        duplicates += 1
                db.execute("INSERT INTO tick_batches(received, server, symbol, n, first_t, last_t, bridge_ms) "
                           "VALUES (?,?,?,?,?,?,?)", (now_ms, server, symbol, len(clean),
                                                      clean[0][0] if clean else None, clean[-1][0] if clean else None,
                                                      bridge_ms))
        sealed_now = self.seal_due(now_ms, server, str(payload.get("bridge_version") or "?")[:40])
        self.purge(now_ms)
        return {"stored": stored, "duplicates": duplicates, "rejected": rejected, "sealed": sealed_now}

    @staticmethod
    def _count_reject(db: sqlite3.Connection, day: str, symbol: str, reason: str) -> None:
        db.execute("INSERT INTO tick_rejects(day, symbol, reason, n) VALUES (?,?,?,1) "
                   "ON CONFLICT(day, symbol, reason) DO UPDATE SET n = n + 1", (day, symbol, reason))

    def record_hours(self, payload: Any) -> dict[str, int]:
        """Hourly bid/ask summaries (LOT 3): stored once per instrument and hour, never rewritten."""

        if not isinstance(payload, dict) or payload.get("server") not in SERVER_ZONES:
            raise TickError("heures : serveur non vérifié")
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_HOUR_ROWS:
            raise TickError(f"heures : liste de 1 à {MAX_HOUR_ROWS} lignes attendue")
        clean = []
        for r in rows:
            prices = [r.get(k) for k in ("first_bid", "first_ask", "last_bid", "last_ask")] if isinstance(r, dict) else []
            if (not isinstance(r, dict) or r.get("symbol") not in INSTRUMENTS or not isinstance(r.get("hour"), str)
                    or len(prices) != 4 or not all(_finite(p) and p > 0 for p in prices)
                    or prices[1] < prices[0] or prices[3] < prices[2]
                    or not _finite(r.get("median_spread_pips")) or not _finite(r.get("p90_spread_pips"))
                    or not isinstance(r.get("ticks"), int)):
                raise TickError("heures : ligne illisible ou incohérente (ask inférieur au bid)")
            try:
                datetime.fromisoformat(r["hour"])
            except ValueError as exc:
                raise TickError("heures : heure illisible") from exc
            clean.append((r["symbol"], r["hour"], *prices, r["median_spread_pips"], r["p90_spread_pips"], r["ticks"]))
        with self._connect() as db:
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO tick_hours VALUES (?,?,?,?,?,?,?,?,?)", clean)
            stored = db.total_changes - before
        return {"stored": stored}

    def hours(self, symbol: str | None = None) -> list[dict[str, Any]]:
        keys = ("symbol", "hour", "first_bid", "first_ask", "last_bid", "last_ask", "median_spread_pips",
                "p90_spread_pips", "ticks")
        with self._connect() as db:
            sql = "SELECT * FROM tick_hours" + (" WHERE symbol = ?" if symbol else "") + " ORDER BY symbol, hour"
            rows = db.execute(sql, (symbol,) if symbol else ()).fetchall()
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def ticks(self, symbol: str, day: str) -> list[tuple[int, float, float, int]]:
        start, end = _day_bounds(day)
        with self._connect() as db:
            return [tuple(r) for r in db.execute(
                "SELECT t, bid, ask, flags FROM ticks WHERE symbol = ? AND t >= ? AND t < ? ORDER BY t, bid, ask",
                (symbol, start, end))]

    def purge(self, now_ms: int, force: bool = False) -> int:
        """Ticks older than the retention leave the database (their sealed files stay). At most once an hour."""

        with self._connect() as db:
            row = db.execute("SELECT value FROM tick_meta WHERE key = 'last_purge'").fetchone()
            if not force and row and now_ms - int(row[0]) < PURGE_EVERY.total_seconds() * 1000:
                return 0
            cutoff = now_ms - TICK_DB_DAYS * 86_400_000
            removed = 0
            for symbol in INSTRUMENTS:
                removed += db.execute("DELETE FROM ticks WHERE symbol = ? AND t < ?", (symbol, cutoff)).rowcount
            db.execute("DELETE FROM tick_batches WHERE received < ?", (cutoff,))
            db.execute("INSERT OR REPLACE INTO tick_meta(key, value) VALUES ('last_purge', ?)", (str(now_ms),))
        return removed

    # -- sealing and fingerprints -----------------------------------------------------------------------------

    def fingerprint(self, symbol: str, day: str) -> tuple[str, int]:
        rows = self.ticks(symbol, day)
        return hashlib.sha256(_canonical_csv(rows)).hexdigest(), len(rows)

    def seal(self, symbol: str, day: str, server: str, bridge_version: str, now: datetime | None = None) -> dict[str, Any]:
        """Write the day's ticks to a compressed CSV with its SHA-256. A sealed day is never rewritten."""

        rows = self.ticks(symbol, day)
        content = _canonical_csv(rows)
        digest = hashlib.sha256(content).hexdigest()
        with self._connect() as db:
            existing = db.execute("SELECT sha256, rows, path FROM tick_seals WHERE symbol = ? AND day = ?",
                                  (symbol, day)).fetchone()
            if existing:
                if existing[0] != digest:
                    self._count_reject(db, day, symbol, "scelle_different")
                return {"symbol": symbol, "day": day, "sha256": existing[0], "rows": existing[1], "path": existing[2],
                        "already_sealed": True, "same_content": existing[0] == digest}
            target = self.archive / symbol / f"{day}.csv.gz"
            target.parent.mkdir(parents=True, exist_ok=True)
            buffer = io.BytesIO()
            with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as packed:
                packed.write(content)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(buffer.getvalue())
            temporary.replace(target)
            provenance = {"server": server, "representative": server not in NON_REPRESENTATIVE_SERVERS,
                          "bridge_version": bridge_version, "source": "MT5 copy_ticks_from, COPY_TICKS_ALL",
                          "time": "UTC milliseconds converted from the server clock",
                          "columns": list(CSV_HEADER), "content_sha256": digest}
            target.with_name(f"{day}.json").write_text(json.dumps(provenance, indent=1), encoding="utf-8")
            db.execute("INSERT INTO tick_seals(symbol, day, sha256, rows, path, provenance, sealed_at) VALUES (?,?,?,?,?,?,?)",
                       (symbol, day, digest, len(rows), str(target), json.dumps(provenance),
                        (now or datetime.now(UTC)).isoformat()))
        if rows:  # LOT 3: the sealed day also feeds the hourly bid/ask summaries
            from .rollover import hourly_from_ticks

            hourly = hourly_from_ticks(symbol, [(0, t, bid, ask, flags) for t, bid, ask, flags in rows])
            for start in range(0, len(hourly), MAX_HOUR_ROWS):
                self.record_hours({"server": server, "rows": hourly[start:start + MAX_HOUR_ROWS]})
        return {"symbol": symbol, "day": day, "sha256": digest, "rows": len(rows), "path": str(target),
                "already_sealed": False}

    def seal_due(self, now_ms: int, server: str, bridge_version: str) -> int:
        """Seal every finished UTC day (one hour after its end) that has ticks and no seal yet."""

        limit_day = _day(now_ms - int(SEAL_DELAY.total_seconds() * 1000) - 86_400_000)
        with self._connect() as db:
            sealed = {(r[0], r[1]) for r in db.execute("SELECT symbol, day FROM tick_seals")}
            days = {(r[0], r[1]) for r in db.execute(
                "SELECT DISTINCT symbol, day FROM (SELECT symbol, strftime('%Y-%m-%d', first_t / 1000, 'unixepoch') AS day "
                "FROM tick_batches WHERE first_t IS NOT NULL)")}
        count = 0
        for symbol, day in sorted(days - sealed):
            if day <= limit_day:
                self.seal(symbol, day, server, bridge_version)
                count += 1
        return count

    def verify_seals(self) -> list[dict[str, Any]]:
        """Replay: recompute each sealed day from its file and from the database, compare with the recorded hash."""

        with self._connect() as db:
            seals = db.execute("SELECT symbol, day, sha256, rows, path FROM tick_seals ORDER BY day, symbol").fetchall()
        out = []
        for symbol, day, digest, rows, path in seals:
            try:
                file_digest = hashlib.sha256(gzip.decompress(Path(path).read_bytes())).hexdigest()
            except OSError:
                file_digest = None
            db_digest, db_rows = self.fingerprint(symbol, day)
            out.append({"symbol": symbol, "day": day, "sha256": digest, "file_ok": file_digest == digest,
                        "database_ok": db_digest == digest if db_rows else None, "rows": rows})
        return out

    # -- quality: gaps, freshness, clock -------------------------------------------------------------------------

    def gaps(self, symbol: str, day: str, server: str = "MetaQuotes-Demo",
             now_ms: int | None = None) -> list[dict[str, Any]]:
        """Silences longer than GAP_SECONDS within the UTC day (its start and end count as edges, so a bridge down all
        evening is a gap too), labelled: week-end (market closed), rollover, or unexplained."""

        zone = ZoneInfo(SERVER_ZONES[server])
        start, end = _day_bounds(day)
        if now_ms is not None:
            end = min(end, now_ms)
        times = [start] + [t for t, _, _, _ in self.ticks(symbol, day)] + [end]
        out = []
        for before, after in itertools.pairwise(times):
            if after - before <= GAP_SECONDS * 1000:
                continue
            open_seconds = sum(60 for m in range(before, after, 60_000) if market_open(m, zone))
            if open_seconds <= GAP_SECONDS:
                label = "week-end"  # the silence is (almost) all inside the market's closure
            elif _near_rollover(before, zone) or _near_rollover(after, zone):
                label = "rollover"
            else:
                label = "inexplique"
            out.append({"from": datetime.fromtimestamp(before / 1000, UTC).isoformat(),
                        "to": datetime.fromtimestamp(after / 1000, UTC).isoformat(),
                        "seconds": round((after - before) / 1000), "open_seconds": open_seconds, "label": label})
        return out

    def status(self, now_ms: int, server: str = "MetaQuotes-Demo") -> dict[str, Any]:
        zone = ZoneInfo(SERVER_ZONES[server])
        today = _day(now_ms)
        start, _ = _day_bounds(today)
        with self._connect() as db:
            last = dict(db.execute("SELECT symbol, max(last_t) FROM tick_batches GROUP BY symbol").fetchall())
            counts = dict(db.execute("SELECT symbol, count(*) FROM ticks WHERE t >= ? GROUP BY symbol", (start,)).fetchall())
            rejects = db.execute("SELECT reason, sum(n) FROM tick_rejects WHERE day = ? GROUP BY reason", (today,)).fetchall()
            latencies = [r[0] - r[1] for r in db.execute(
                "SELECT received, last_t FROM tick_batches WHERE received >= ? AND last_t IS NOT NULL", (start,))]
            seals = db.execute("SELECT count(*), max(day) FROM tick_seals").fetchone()
            facts = db.execute("SELECT server, company, margin_mode, currency, representative, last_seen FROM account_facts "
                               "ORDER BY last_seen DESC LIMIT 1").fetchone()
        open_now = market_open(now_ms, zone)
        instruments = []
        for symbol in INSTRUMENTS:
            age = None if last.get(symbol) is None else round((now_ms - last[symbol]) / 1000, 1)
            instruments.append({"symbol": symbol, "last_tick_age_s": age, "ticks_today": counts.get(symbol, 0),
                                "stale": bool(open_now and (age is None or age > STALE_SECONDS))})
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {"day": today, "market_open": open_now, "stale_after_s": STALE_SECONDS, "instruments": instruments,
                "rejected_today": dict(rejects),
                "clock": {"batches": len(latencies),
                          "median_ms": statistics.median(latencies) if latencies else None,
                          "max_ms": max(latencies) if latencies else None,
                          "meaning": "réception serveur moins heure du dernier tick du lot (latence plus dérive)"},
                "seals": {"count": seals[0], "last_day": seals[1]}, "database_bytes": size,
                "account": None if facts is None else {
                    "server": facts[0], "company": facts[1], "margin_mode": MARGIN_MODES.get(facts[2], f"INCONNU({facts[2]})"),
                    "currency": facts[3], "costs": "REPRESENTATIF" if facts[4] else "NON_REPRESENTATIF", "seen": facts[5]}}

    # -- costs ---------------------------------------------------------------------------------------------------

    def costs(self, symbol: str, days: list[str], server: str = "MetaQuotes-Demo") -> dict[str, Any]:
        """Spread in pips per UTC hour from the ticks of the given days: median, 90th percentile, samples."""

        pip = INSTRUMENTS[symbol].pip
        by_hour: dict[int, list[float]] = {}
        for day in days:
            for t, bid, ask, _ in self.ticks(symbol, day):
                by_hour.setdefault(datetime.fromtimestamp(t / 1000, UTC).hour, []).append((ask - bid) / pip)
        table = {}
        for hour, values in sorted(by_hour.items()):
            ordered = sorted(values)
            table[hour] = {"median": round(statistics.median(ordered), 3),
                           "p90": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 3),
                           "samples": len(ordered)}
        return {"symbol": symbol, "days": days, "weighting": "par tick", "by_hour_utc": table,
                "costs": "NON_REPRESENTATIF" if server in NON_REPRESENTATIVE_SERVERS else "REPRESENTATIF"}

    # -- specifications and account --------------------------------------------------------------------------

    def record_specs(self, server: str, items: list[dict[str, Any]], account: Any, now: datetime) -> dict[str, int]:
        """One version per specification change (swaps included); the account's server, company and margin mode."""

        stamp = now.isoformat()
        new = 0
        with self._connect() as db:
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                digest = spec_hash(item)
                kept = {k: v for k, v in item.items() if k not in SPEC_EXCLUDED}
                cursor = db.execute("UPDATE spec_versions SET last_seen = ? WHERE server = ? AND name = ? AND spec_hash = ?",
                                    (stamp, server, item["name"], digest))
                if not cursor.rowcount:
                    db.execute("INSERT INTO spec_versions VALUES (?,?,?,?,?,?)",
                               (server, item["name"], digest, json.dumps(kept, sort_keys=True), stamp, stamp))
                    new += 1
            if isinstance(account, dict) and isinstance(account.get("margin_mode"), int):
                key = (server, str(account.get("company") or "?")[:80], account["margin_mode"],
                       str(account.get("currency") or "?")[:8])
                cursor = db.execute("UPDATE account_facts SET last_seen = ? WHERE server = ? AND company = ? AND "
                                    "margin_mode = ? AND currency = ?", (stamp, *key))
                if not cursor.rowcount:
                    db.execute("INSERT INTO account_facts VALUES (?,?,?,?,?,?,?)",
                               (*key, int(server not in NON_REPRESENTATIVE_SERVERS), stamp, stamp))
        return {"spec_versions_new": new}

    def spec_history(self, server: str, name: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT spec_json, first_seen, last_seen FROM spec_versions WHERE server = ? AND name = ? "
                              "ORDER BY first_seen", (server, name)).fetchall()
        return [{"spec": json.loads(r[0]), "first_seen": r[1], "last_seen": r[2]} for r in rows]
