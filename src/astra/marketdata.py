"""Persistent Forex candle store: CSV import, ECB reference rates, quality control and coverage.

Every candle carries its symbol, timeframe, open time (UTC), prices, the volume when the source gives
one (NULL otherwise, never invented) and its source. Two kinds exist:

- ``OHLC``: a real candle (open, high, low, close), from a CSV file the user obtained from an
  authorised provider;
- ``REFERENCE_FIX``: one official reference rate per business day (European Central Bank, published
  around 16:00 CET for 14:15 CET). Only the close is known; open, high and low stay NULL. A backtest
  on these rates says so in its assumptions (no intraday extremes, stops checked on the fix).

Synthetic data stays labelled SYNTHETIC and is never shown as market data. Nothing here is real time.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import math
import sqlite3
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MARKETDATA_VERSION = "1.0.0"

PAIRS: dict[str, tuple[str, str, float, int]] = {
    # symbol: (base, quote, pip size, price decimals)
    "EURUSD": ("EUR", "USD", 0.0001, 5),
    "GBPUSD": ("GBP", "USD", 0.0001, 5),
    "USDJPY": ("USD", "JPY", 0.01, 3),
    "AUDUSD": ("AUD", "USD", 0.0001, 5),
    "USDCHF": ("USD", "CHF", 0.0001, 5),
    "USDCAD": ("USD", "CAD", 0.0001, 5),
    "NZDUSD": ("NZD", "USD", 0.0001, 5),
}
TIMEFRAMES: dict[str, int] = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
KIND_OHLC = "OHLC"
KIND_REFERENCE = "REFERENCE_FIX"
NATURES = ("HISTORICAL", "REFERENCE", "SYNTHETIC")
MAX_IMPORT_BYTES = 12 * 1024 * 1024
MAX_IMPORT_ROWS = 500_000
ECB_SOURCE = "ECB_EXR"
ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+GBP+JPY+AUD+CHF+CAD+NZD.EUR.SP00.A"
ECB_DEFAULT_START = date(2015, 1, 1)
_NEW_YORK = ZoneInfo("America/New_York")
_FRANKFURT = ZoneInfo("Europe/Berlin")


class MarketDataError(ValueError):
    """A refused import or a source failure, with a message meant for the operator."""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def canonical_symbol(value: str) -> str:
    symbol = (value or "").strip().upper().replace("/", "").replace("_", "").replace("-", "")
    if symbol not in PAIRS:
        raise MarketDataError(f"paire non prise en charge : {value!r} (paires : {', '.join(PAIRS)})")
    return symbol


def canonical_timeframe(value: str) -> str:
    timeframe = (value or "").strip().upper()
    aliases = {"5M": "M5", "15M": "M15", "1H": "H1", "60": "H1", "4H": "H4", "1D": "D1", "D": "D1", "DAILY": "D1"}
    timeframe = aliases.get(timeframe, timeframe)
    if timeframe not in TIMEFRAMES:
        raise MarketDataError(f"unité de temps non prise en charge : {value!r} (unités : {', '.join(TIMEFRAMES)})")
    return timeframe


def price_decimals(symbol: str) -> int:
    return PAIRS[symbol][3]


def pip_size(symbol: str) -> float:
    return PAIRS[symbol][2]


def trading_day(moment: datetime) -> date:
    """The Forex trading day of a daily bar: New York time + 7 h, so a bar opening at the 17:00 New York
    close (21:00 or 22:00 UTC) belongs to the next day, and 00:00 UTC or 14:15 CET bars to their own date."""

    return (moment.astimezone(_NEW_YORK) + timedelta(hours=7)).date()


def forex_market_closed(moment: datetime) -> bool:
    """True inside the weekly closure: Friday 17:00 to Sunday 17:00 New York time (follows US DST)."""

    local = moment.astimezone(_NEW_YORK)
    weekday, minutes = local.weekday(), local.hour * 60 + local.minute
    if weekday == 5:
        return True
    if weekday == 4:
        return minutes >= 17 * 60
    if weekday == 6:
        return minutes < 17 * 60
    return False


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    source: str
    kind: str = KIND_OHLC


@dataclass(slots=True)
class ImportReport:
    source: str
    symbol: str
    timeframe: str
    nature: str
    rows_read: int = 0
    accepted: int = 0
    inserted: int = 0
    duplicates_in_file: int = 0
    already_stored: int = 0
    conflicts: int = 0
    rejected: int = 0
    gaps: int = 0
    missing_bars: int = 0
    first: str | None = None
    last: str | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)
    file_sha256: str | None = None

    def add_issue(self, code: str, row: int | None, detail: str) -> None:
        if len(self.issues) < 200:  # the counts stay exact; only the examples are capped
            self.issues.append({"code": code, "row": row, "detail": detail})

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}


# -- CSV parsing ----------------------------------------------------------------------------------

_HEADER_ALIASES = {
    "timestamp": "timestamp", "time": "time", "datetime": "timestamp", "date": "date", "gmt time": "timestamp",
    "local time": "timestamp", "<date>": "date", "<time>": "time", "<dtyyyymmdd>": "date", "open": "open",
    "<open>": "open", "o": "open", "high": "high", "<high>": "high", "h": "high", "low": "low", "<low>": "low",
    "l": "low", "close": "close", "<close>": "close", "c": "close", "volume": "volume", "<vol>": "volume",
    "<tickvol>": "volume", "tickvol": "volume", "vol": "volume", "symbol": "symbol", "instrument": "symbol",
    "<ticker>": "symbol",
}
_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M", "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y%m%d %H%M%S",
            "%Y%m%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d", "%Y%m%d", "%d.%m.%Y")


def _parse_timestamp(text: str, zone: ZoneInfo) -> tuple[datetime | None, str | None]:
    """UTC time of a CSV timestamp; naive values are read in ``zone`` and DST gaps or folds are refused."""

    value = text.strip()
    if value.upper().endswith(" UTC"):
        value, zone = value[:-4], ZoneInfo("UTC")
    if " GMT" in value:  # Dukascopy: "02.01.2024 00:00:00.000 GMT+0100"
        stamp, offset = value.split(" GMT", 1)
        try:
            sign = -1 if offset.startswith("-") else 1
            hours, minutes = (int(offset[1:3]), int(offset[3:5])) if offset else (0, 0)
            naive = _parse_naive(stamp)
            if naive is None:
                return None, "horodatage illisible"
            return (naive - sign * timedelta(hours=hours, minutes=minutes)).replace(tzinfo=UTC), None
        except (ValueError, IndexError):
            return None, "décalage GMT illisible"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC), None
    except ValueError:
        parsed = _parse_naive(value)
        if parsed is None:
            return None, "horodatage illisible"
    if zone.key == "UTC":
        return parsed.replace(tzinfo=UTC), None
    local = parsed.replace(tzinfo=zone)
    # A local time that does not exist (spring forward) or exists twice (fall back) is ambiguous.
    round_trip = local.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != parsed:
        return None, "heure locale inexistante (passage à l'heure d'été)"
    if local.replace(fold=1).utcoffset() != local.replace(fold=0).utcoffset():
        return None, "heure locale ambiguë (retour à l'heure d'hiver) : utilisez des horodatages UTC"
    return local.astimezone(UTC), None


def _parse_naive(value: str) -> datetime | None:
    for pattern in _FORMATS:
        try:
            return datetime.strptime(value, pattern)  # noqa: DTZ007 - naive on purpose, zone applied by the caller
        except ValueError:
            continue
    return None


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    value = text.strip().replace(" ", "")
    if not value:
        return None
    if value.count(",") == 1 and "." not in value:
        value = value.replace(",", ".")
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_csv(raw: bytes, symbol: str, timeframe: str, source: str, *, timezone: str = "UTC",
              nature: str = "HISTORICAL") -> tuple[list[Candle], ImportReport]:
    """Validate a CSV OHLC file; bad rows are rejected and reported, good rows are returned in time order."""

    symbol, timeframe = canonical_symbol(symbol), canonical_timeframe(timeframe)
    source = (source or "").strip()
    if not source or len(source) > 80 or any(ch in source for ch in "/\\\n\r\t"):
        raise MarketDataError("nom de source obligatoire (80 caractères au plus, sans chemin)")
    if nature not in NATURES or nature == "REFERENCE":
        raise MarketDataError("nature des données : HISTORICAL ou SYNTHETIC")
    if len(raw) > MAX_IMPORT_BYTES:
        raise MarketDataError(f"fichier trop volumineux (plus de {MAX_IMPORT_BYTES // (1024 * 1024)} Mo)")
    try:
        zone = ZoneInfo(timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise MarketDataError(f"fuseau horaire inconnu : {timezone!r}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MarketDataError("le fichier doit être encodé en UTF-8") from exc
    report = ImportReport(source=source, symbol=symbol, timeframe=timeframe, nature=nature,
                          file_sha256=hashlib.sha256(raw).hexdigest())
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise MarketDataError("fichier vide") from exc
    columns = [_HEADER_ALIASES.get(name.strip().lower()) for name in header]
    if "timestamp" not in columns and "date" not in columns:
        raise MarketDataError("colonne d'horodatage introuvable (timestamp, date, Gmt time, <DATE>)")
    for needed in ("open", "high", "low", "close"):
        if needed not in columns:
            raise MarketDataError(f"colonne obligatoire absente : {needed}")
    position = {name: columns.index(name) for name in set(columns) if name}
    step = TIMEFRAMES[timeframe]
    seen: dict[datetime, Candle] = {}
    for row_number, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        report.rows_read += 1
        if report.rows_read > MAX_IMPORT_ROWS:
            raise MarketDataError(f"trop de lignes (plus de {MAX_IMPORT_ROWS})")
        if len(row) < len(header):
            report.rejected += 1
            report.add_issue("COLUMN_COUNT", row_number, f"{len(row)} colonnes au lieu de {len(header)}")
            continue
        if "symbol" in position and row[position["symbol"]].strip():
            try:
                if canonical_symbol(row[position["symbol"]]) != symbol:
                    raise MarketDataError("autre paire")
            except MarketDataError:
                report.rejected += 1
                report.add_issue("SYMBOL_MISMATCH", row_number, f"paire {row[position['symbol']]!r} ≠ {symbol}")
                continue
        stamp_text = row[position["timestamp"]] if "timestamp" in position else row[position["date"]]
        if "time" in position and "timestamp" not in position:
            stamp_text = f"{stamp_text} {row[position['time']]}"
        moment, problem = _parse_timestamp(stamp_text, zone)
        if moment is None:
            report.rejected += 1
            report.add_issue("INVALID_TIMESTAMP", row_number, f"{stamp_text!r} : {problem}")
            continue
        if moment > _now() + timedelta(minutes=5):
            report.rejected += 1
            report.add_issue("FUTURE_TIMESTAMP", row_number, _iso(moment))
            continue
        epoch = int(moment.timestamp())
        if timeframe != "D1" and epoch % min(step, 3600):
            report.rejected += 1
            report.add_issue("MISALIGNED_TIMESTAMP", row_number, f"{_iso(moment)} n'est pas aligné sur {timeframe}")
            continue
        o, h, low, c = (_number(row[position[name]]) for name in ("open", "high", "low", "close"))
        volume = _number(row[position["volume"]]) if "volume" in position else None
        if None in (o, h, low, c):
            report.rejected += 1
            report.add_issue("INVALID_PRICE", row_number, "prix manquant ou illisible")
            continue
        if min(o, h, low, c) <= 0:
            report.rejected += 1
            report.add_issue("NON_POSITIVE_PRICE", row_number, "prix nul ou négatif")
            continue
        if h < max(o, c) or low > min(o, c) or h < low:
            report.rejected += 1
            report.add_issue("INCONSISTENT_CANDLE", row_number, f"O={o} H={h} L={low} C={c}")
            continue
        if volume is not None and volume < 0:
            report.rejected += 1
            report.add_issue("NEGATIVE_VOLUME", row_number, str(volume))
            continue
        decimals = price_decimals(symbol) + 1
        candle = Candle(symbol, timeframe, moment, round(o, decimals), round(h, decimals), round(low, decimals),
                        round(c, decimals), volume, source, KIND_OHLC)
        if moment in seen:
            report.duplicates_in_file += 1
            if seen[moment] != candle:
                report.conflicts += 1
                report.add_issue("CONFLICTING_DUPLICATE", row_number,
                                 f"{_iso(moment)} : valeurs différentes, la première est gardée")
            continue
        seen[moment] = candle
    candles = sorted(seen.values(), key=lambda item: item.timestamp)
    if len(candles) >= 3:
        spacing = Counter(int((b.timestamp - a.timestamp).total_seconds()) for a, b in itertools.pairwise(candles))
        usual = spacing.most_common(1)[0][0]
        if usual != step:
            raise MarketDataError(f"l'écart habituel entre bougies est de {usual // 60} min, pas {step // 60} min : "
                                  f"ce fichier n'est pas en {timeframe} (vérifiez l'unité de temps choisie)")
    if timeframe == "H4" and candles:
        # Providers align H4 bars on different hours; one phase per file, the others are refused.
        phase = Counter(int(c.timestamp.timestamp()) % step for c in candles).most_common(1)[0][0]
        kept = [c for c in candles if int(c.timestamp.timestamp()) % step == phase]
        for c in candles:
            if int(c.timestamp.timestamp()) % step != phase:
                report.rejected += 1
                report.add_issue("MISALIGNED_TIMESTAMP", None, f"{_iso(c.timestamp)} hors de la grille H4 du fichier")
        candles = kept
    if timeframe == "D1":
        kept, days = [], set()
        for c in candles:
            day = trading_day(c.timestamp)
            if day in days:
                report.rejected += 1
                report.add_issue("DAILY_DUPLICATE_DATE", None, f"{_iso(c.timestamp)} : deuxième bougie D1 pour le {day}")
                continue
            days.add(day)
            kept.append(c)
        candles = kept
    report.accepted = len(candles)
    if candles:
        report.first, report.last = _iso(candles[0].timestamp), _iso(candles[-1].timestamp)
    gaps = find_gaps([item.timestamp for item in candles], timeframe)
    report.gaps, report.missing_bars = len(gaps), sum(item["missing"] for item in gaps)
    for gap in gaps[:20]:
        report.add_issue("GAP", None, f"{gap['missing']} bougie(s) manquante(s) entre {gap['after']} et {gap['before']}")
    return candles, report


def find_gaps(timestamps: list[datetime], timeframe: str) -> list[dict[str, Any]]:
    """Missing bars between consecutive timestamps, outside the weekly Forex closure.

    Daily series count missing weekdays (a bank holiday is reported, not hidden).
    """

    gaps: list[dict[str, Any]] = []
    if len(timestamps) < 2:
        return gaps
    if timeframe == "D1":
        for previous, current in itertools.pairwise(timestamps):
            day, missing = trading_day(previous) + timedelta(days=1), 0
            while day < trading_day(current):
                if day.weekday() < 5:
                    missing += 1
                day += timedelta(days=1)
            if missing:
                gaps.append({"after": _iso(previous), "before": _iso(current), "missing": missing})
        return gaps
    step = timedelta(seconds=TIMEFRAMES[timeframe])
    for previous, current in itertools.pairwise(timestamps):
        if current - previous <= step:
            continue
        missing, moment = 0, previous + step
        while moment < current:
            if not forex_market_closed(moment):
                missing += 1
            moment += step
            if missing > 100_000:
                break
        if missing:
            gaps.append({"after": _iso(previous), "before": _iso(current), "missing": missing})
    return gaps


# -- storage --------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS md_candles (
    symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts TEXT NOT NULL, source TEXT NOT NULL,
    kind TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
    nature TEXT NOT NULL, import_id INTEGER, ingested_at TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe, source, ts)
);
CREATE TABLE IF NOT EXISTS md_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, symbol TEXT, timeframe TEXT, nature TEXT,
    started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, actor TEXT, report_json TEXT
);
CREATE TABLE IF NOT EXISTS md_sources (
    name TEXT PRIMARY KEY, kind TEXT NOT NULL, nature TEXT NOT NULL, description TEXT,
    last_attempt_at TEXT, last_success_at TEXT, last_error TEXT, last_error_at TEXT, last_data_ts TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0, next_allowed_at TEXT
);
"""


class CandleStore:
    """SQLite candle store in the main ASTRA database (one row per symbol, timeframe, source and time)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
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
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    # imports -------------------------------------------------------------------------------------

    def begin_import(self, source: str, symbol: str | None, timeframe: str | None, nature: str, actor: str) -> int:
        with self._connect() as db:
            cursor = db.execute("INSERT INTO md_imports(source, symbol, timeframe, nature, started_at, status, actor)"
                                " VALUES (?,?,?,?,?,?,?)", (source, symbol, timeframe, nature, _iso(_now()), "RUNNING", actor))
            return int(cursor.lastrowid)

    def finish_import(self, import_id: int, status: str, report: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("UPDATE md_imports SET finished_at = ?, status = ?, report_json = ? WHERE id = ?",
                       (_iso(_now()), status, json.dumps(report, ensure_ascii=False, default=str), import_id))

    def store(self, candles: Iterable[Candle], nature: str, import_id: int | None) -> tuple[int, int, int]:
        """Insert candles idempotently: (inserted, already stored with the same values, conflicts kept as stored)."""

        inserted = same = conflicts = 0
        now = _iso(_now())
        batch = list(candles)
        for start in range(0, len(batch), 5000):
            i, s_, c_ = self._store_chunk(batch[start:start + 5000], nature, import_id, now)
            inserted, same, conflicts = inserted + i, same + s_, conflicts + c_
        return inserted, same, conflicts

    def _store_chunk(self, candles: list[Candle], nature: str, import_id: int | None, now: str) -> tuple[int, int, int]:
        inserted = same = conflicts = 0
        with self._connect() as db:
            for candle in candles:
                key = (candle.symbol, candle.timeframe, candle.source, _iso(candle.timestamp))
                existing = db.execute("SELECT open, high, low, close, volume FROM md_candles WHERE symbol = ? AND"
                                      " timeframe = ? AND source = ? AND ts = ?", key).fetchone()
                values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
                if existing is None:
                    db.execute("INSERT INTO md_candles(symbol, timeframe, ts, source, kind, open, high, low, close,"
                               " volume, nature, import_id, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (candle.symbol, candle.timeframe, key[3], candle.source, candle.kind, *values, nature,
                                import_id, now))
                    inserted += 1
                elif tuple(existing) == values:
                    same += 1
                else:
                    conflicts += 1
        return inserted, same, conflicts

    def import_csv(self, raw: bytes, symbol: str, timeframe: str, source: str, *, timezone: str = "UTC",
                   nature: str = "HISTORICAL", actor: str = "cli") -> dict[str, Any]:
        check_import_source(self, source, nature)
        candles, report = parse_csv(raw, symbol, timeframe, source, timezone=timezone, nature=nature)
        import_id = self.begin_import(report.source, report.symbol, report.timeframe, nature, actor)
        try:
            inserted, same, conflicts = self.store(candles, nature, import_id)
            report.inserted, report.already_stored = inserted, same
            report.conflicts += conflicts
            if conflicts:
                report.add_issue("CONFLICT_WITH_STORED", None,
                                 f"{conflicts} bougie(s) déjà stockée(s) avec d'autres valeurs : conservées")
            status = "OK" if report.accepted and not report.rejected else ("PARTIAL" if report.accepted else "REJECTED")
            self.register_source(report.source, "CSV_IMPORT", nature, "Fichier CSV importé par un opérateur")
            self.source_success(report.source, report.last)
            self.finish_import(import_id, status, report.to_dict())
        except BaseException as exc:
            self.finish_import(import_id, "FAILED", {**report.to_dict(), "error": str(exc)})
            raise
        return {"import_id": import_id, "status": status, **report.to_dict()}

    # sources -------------------------------------------------------------------------------------

    def register_source(self, name: str, kind: str, nature: str, description: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO md_sources(name, kind, nature, description) VALUES (?,?,?,?) ON CONFLICT(name)"
                       " DO UPDATE SET kind = excluded.kind, nature = excluded.nature, description = excluded.description",
                       (name, kind, nature, description))

    def source_attempt(self, name: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE md_sources SET last_attempt_at = ? WHERE name = ?", (_iso(_now()), name))

    def source_success(self, name: str, last_data_ts: str | None) -> None:
        with self._connect() as db:
            db.execute("UPDATE md_sources SET last_success_at = ?, last_attempt_at = COALESCE(last_attempt_at, ?),"
                       " consecutive_failures = 0, next_allowed_at = NULL, last_data_ts = MAX(COALESCE(last_data_ts, ''),"
                       " COALESCE(?, '')) WHERE name = ?", (_iso(_now()), _iso(_now()), last_data_ts, name))

    def source_failure(self, name: str, error: str) -> int:
        """Record a failure and back off exponentially (5 min, 10, 20, ... capped at 6 h)."""

        with self._connect() as db:
            row = db.execute("SELECT consecutive_failures FROM md_sources WHERE name = ?", (name,)).fetchone()
            failures = (row["consecutive_failures"] if row else 0) + 1
            delay = min(6 * 3600, 300 * 2 ** (failures - 1))
            db.execute("UPDATE md_sources SET last_error = ?, last_error_at = ?, consecutive_failures = ?,"
                       " next_allowed_at = ? WHERE name = ?",
                       (error[:500], _iso(_now()), failures, _iso(_now() + timedelta(seconds=delay)), name))
            return failures

    def source(self, name: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM md_sources WHERE name = ?", (name,)).fetchone()
            return dict(row) if row else None

    def sources(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM md_sources ORDER BY name")]

    # reads ---------------------------------------------------------------------------------------

    def coverage(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT symbol, timeframe, source, kind, nature, COUNT(*) AS candles, MIN(ts) AS first,"
                              " MAX(ts) AS last, SUM(volume IS NOT NULL) AS with_volume FROM md_candles"
                              " GROUP BY symbol, timeframe, source ORDER BY symbol, timeframe, source").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["gaps"] = None  # filled on demand by quality(), costly on large series
            result.append(item)
        return result

    def candles(self, symbol: str, timeframe: str, source: str, *, start: datetime | None = None,
                end: datetime | None = None, limit: int | None = None, newest: bool = False) -> list[Candle]:
        query = ("SELECT symbol, timeframe, ts, open, high, low, close, volume, source, kind FROM md_candles"
                 " WHERE symbol = ? AND timeframe = ? AND source = ?")
        args: list[Any] = [symbol, timeframe, source]
        if start is not None:
            query += " AND ts >= ?"
            args.append(_iso(start))
        if end is not None:
            query += " AND ts <= ?"
            args.append(_iso(end))
        query += " ORDER BY ts DESC" if newest else " ORDER BY ts"
        if limit is not None:
            query += " LIMIT ?"
            args.append(int(limit))
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        candles = [Candle(r["symbol"], r["timeframe"], _parse_iso(r["ts"]), r["open"], r["high"], r["low"], r["close"],
                          r["volume"], r["source"], r["kind"]) for r in rows]
        return list(reversed(candles)) if newest else candles

    def quality(self, symbol: str, timeframe: str, source: str) -> dict[str, Any]:
        stamps = [candle.timestamp for candle in self.candles(symbol, timeframe, source)]
        gaps = find_gaps(stamps, timeframe)
        return {"symbol": symbol, "timeframe": timeframe, "source": source, "candles": len(stamps),
                "first": _iso(stamps[0]) if stamps else None, "last": _iso(stamps[-1]) if stamps else None,
                "gaps": len(gaps), "missing_bars": sum(item["missing"] for item in gaps), "gap_examples": gaps[-20:]}

    def imports(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM md_imports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            report = json.loads(item.pop("report_json") or "{}")
            item["report"] = {key: report.get(key) for key in (
                "rows_read", "accepted", "inserted", "already_stored", "duplicates_in_file", "conflicts", "rejected",
                "gaps", "missing_bars", "first", "last", "error")}
            item["issues"] = (report.get("issues") or [])[:30]
            result.append(item)
        return result

    def fingerprint(self, candles: list[Candle]) -> str:
        """SHA-256 of the exact candle rows a computation used (reproducibility)."""

        digest = hashlib.sha256()
        for c in candles:
            digest.update(f"{c.symbol}|{c.timeframe}|{c.source}|{_iso(c.timestamp)}|{c.open}|{c.high}|{c.low}|{c.close}"
                          f"|{c.volume}\n".encode())
        return digest.hexdigest()


def check_import_source(store: CandleStore, source: str, nature: str) -> None:
    """A CSV import may not reuse the ECB source name, nor mix natures (real and synthetic) in one source."""

    name = (source or "").strip()
    if name.upper() == ECB_SOURCE:
        raise MarketDataError(f"le nom de source {ECB_SOURCE} est réservé au connecteur BCE")
    existing = store.source(name)
    if existing and existing["nature"] != nature:
        raise MarketDataError(f"la source {name!r} contient déjà des données {existing['nature']} : "
                              f"choisissez un autre nom pour des données {nature}")


# -- ECB reference rates --------------------------------------------------------------------------

# ECB publishes EUR/XXX. Each pair is derived from one or two of those official rates.
_ECB_DERIVATION: dict[str, Callable[[dict[str, float]], float]] = {
    "EURUSD": lambda r: r["USD"],
    "GBPUSD": lambda r: r["USD"] / r["GBP"],
    "USDJPY": lambda r: r["JPY"] / r["USD"],
    "AUDUSD": lambda r: r["USD"] / r["AUD"],
    "USDCHF": lambda r: r["CHF"] / r["USD"],
    "USDCAD": lambda r: r["CAD"] / r["USD"],
    "NZDUSD": lambda r: r["USD"] / r["NZD"],
}
_ECB_NEEDS = {"EURUSD": ("USD",), "GBPUSD": ("USD", "GBP"), "USDJPY": ("USD", "JPY"), "AUDUSD": ("USD", "AUD"),
              "USDCHF": ("USD", "CHF"), "USDCAD": ("USD", "CAD"), "NZDUSD": ("USD", "NZD")}

Fetcher = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": "ASTRA-Forex-Intelligence/0.2"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # fixed https URL of the ECB
        if response.status != 200:
            raise MarketDataError(f"BCE : HTTP {response.status}")
        return response.read(MAX_IMPORT_BYTES + 1)


def parse_ecb_csv(raw: bytes) -> dict[date, dict[str, float]]:
    """{date: {currency: EUR/currency rate}} from the ECB SDMX csvdata format."""

    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    for needed in ("CURRENCY", "TIME_PERIOD", "OBS_VALUE"):
        if needed not in (reader.fieldnames or []):
            raise MarketDataError(f"BCE : réponse inattendue (colonne {needed} absente)")
    rates: dict[date, dict[str, float]] = {}
    for row in reader:
        value = _number(row.get("OBS_VALUE"))
        try:
            day = date.fromisoformat(row["TIME_PERIOD"])
        except ValueError:
            continue
        if value is None or value <= 0:
            continue
        rates.setdefault(day, {})[row["CURRENCY"]] = value
    return rates


def ecb_candles(rates: dict[date, dict[str, float]]) -> list[Candle]:
    """Daily REFERENCE_FIX candles at 14:15 Frankfurt time (converted to UTC, DST included)."""

    candles = []
    for day in sorted(rates):
        moment = datetime(day.year, day.month, day.day, 14, 15, tzinfo=_FRANKFURT).astimezone(UTC)
        for symbol, needs in _ECB_NEEDS.items():
            if all(currency in rates[day] for currency in needs):
                close = round(_ECB_DERIVATION[symbol](rates[day]), price_decimals(symbol) + 1)
                candles.append(Candle(symbol, "D1", moment, None, None, None, close, None, ECB_SOURCE, KIND_REFERENCE))
    return candles


def ingest_ecb(store: CandleStore, *, fetch: Fetcher = _http_get, actor: str = "worker", start: date | None = None,
               retries: int = 3, timeout: float = 30.0, sleep: Callable[[float], None] = time.sleep,
               force: bool = False) -> dict[str, Any]:
    """Fetch the ECB reference rates since the last stored day (idempotent), with retries and back-off."""

    store.register_source(ECB_SOURCE, "ECB_SDMX_API", "REFERENCE",
                          "Banque centrale européenne, taux de référence quotidiens (14 h 15 CET), sans identifiant ;"
                          " paires hors EUR/USD dérivées de deux taux officiels")
    info = store.source(ECB_SOURCE) or {}
    if not force and info.get("next_allowed_at") and _parse_iso(info["next_allowed_at"]) > _now():
        return {"status": "SKIPPED", "reason": f"pause après échec jusqu'à {info['next_allowed_at']}"}
    if info.get("last_attempt_at") and _now() - _parse_iso(info["last_attempt_at"]) < timedelta(seconds=60):
        return {"status": "SKIPPED", "reason": "dernière requête à la BCE il y a moins d'une minute"}
    last = (info.get("last_data_ts") or "")[:10]
    since = start or (date.fromisoformat(last) - timedelta(days=7) if last else ECB_DEFAULT_START)
    url = f"{ECB_URL}?startPeriod={since.isoformat()}&format=csvdata"
    store.source_attempt(ECB_SOURCE)
    import_id = store.begin_import(ECB_SOURCE, None, "D1", "REFERENCE", actor)
    error: str | None = None
    for attempt in range(1, retries + 1):
        try:
            raw = fetch(url, timeout)
            if len(raw) > MAX_IMPORT_BYTES:
                raise MarketDataError("BCE : réponse trop volumineuse")
            candles = ecb_candles(parse_ecb_csv(raw))
            inserted, same, conflicts = store.store(candles, "REFERENCE", import_id)
            last_ts = _iso(candles[-1].timestamp) if candles else None
            report = {"source": ECB_SOURCE, "url": url, "attempts": attempt, "accepted": len(candles),
                      "inserted": inserted, "already_stored": same, "conflicts": conflicts, "rejected": 0,
                      "first": _iso(candles[0].timestamp) if candles else None, "last": last_ts,
                      "symbols": sorted({c.symbol for c in candles})}
            store.source_success(ECB_SOURCE, last_ts)
            store.finish_import(import_id, "OK", report)
            return {"status": "OK", "import_id": import_id, **report}
        except (urllib.error.URLError, TimeoutError, OSError, MarketDataError, UnicodeDecodeError, TypeError, KeyError,
                csv.Error) as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                sleep(min(60.0, 5.0 * 2 ** (attempt - 1)))
    failures = store.source_failure(ECB_SOURCE, error or "échec")
    store.finish_import(import_id, "FAILED", {"source": ECB_SOURCE, "url": url, "attempts": retries, "error": error,
                                              "consecutive_failures": failures})
    return {"status": "FAILED", "import_id": import_id, "error": error, "consecutive_failures": failures}


def freshness(source: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any]:
    """How old the newest data is. Nothing in this module is ever described as real time."""

    now = now or _now()
    if not source or not source.get("last_data_ts"):
        return {"state": "NO_DATA", "label": "aucune donnée", "age_hours": None}
    age = (now - _parse_iso(source["last_data_ts"])).total_seconds() / 3600
    if source.get("nature") == "REFERENCE":
        # One fix per TARGET business day: up to 4 days old over a weekend or a holiday is normal.
        state = "UP_TO_DATE" if age <= 4 * 24 + 6 else "LATE"
        label = "à jour (taux quotidien, pas du temps réel)" if state == "UP_TO_DATE" else "en retard"
    else:
        state, label = "HISTORICAL", "historique importé (pas du temps réel)"
    return {"state": state, "label": label, "age_hours": round(age, 1)}
