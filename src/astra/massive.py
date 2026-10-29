"""Massive (market data provider, formerly Polygon): research data for ASTRA. Never an order route.

Documented facts this module relies on (massive.com/docs/rest/forex/aggregates/custom-bars):
- ``GET https://api.massive.com/v2/aggs/ticker/C:EURUSD/range/{multiplier}/{timespan}/{from}/{to}`` returns
  ``results`` with ``t`` (start of the window, Unix milliseconds, UTC), ``o``, ``h``, ``l``, ``c``, ``v``, ``vw``, ``n``;
  ``limit`` up to 50 000; ``sort=asc`` oldest first.
- Forex aggregates are built from quoted bid/ask prices, not from executed trades. A bar with no quote update does
  not exist. Volume is therefore a count of quote activity, never a centralised traded volume: this module keeps it
  but the source is labelled so that nothing presents it as one.
- Access depends on the plan: the free Currencies plan is end of day with two years of history, paid plans are
  real time with the full history. ``check`` reports what the key really returns instead of assuming.

The key comes from a server secret (``MASSIVE_API_KEY_FILE`` or the systemd credential ``massive-key``), is sent in
the ``Authorization`` header and never appears in a URL, a log line, an error message or a report.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .marketdata import KIND_OHLC, TIMEFRAMES, Candle, CandleStore, MarketDataError, canonical_symbol

BASE_URL = "https://api.massive.com"
SOURCE = "MASSIVE_FX_QUOTES"
SYMBOL_MAP_VERSION = "1"
# ASTRA canonical symbol -> Massive ticker. Versioned: two similar tickers are not necessarily the same instrument.
SYMBOL_MAP = {"EURUSD": "C:EURUSD", "GBPUSD": "C:GBPUSD", "USDJPY": "C:USDJPY", "AUDUSD": "C:AUDUSD",
              "USDCHF": "C:USDCHF", "USDCAD": "C:USDCAD", "NZDUSD": "C:NZDUSD"}
# ASTRA timeframe -> (multiplier, timespan)
TIMESPANS = {"M5": (5, "minute"), "M15": (15, "minute"), "H1": (1, "hour"), "H4": (4, "hour"), "D1": (1, "day")}
MAX_PAGE = 50_000
MAX_PAGES = 40
MAX_BYTES = 30 * 1024 * 1024
Opener = Callable[[urllib.request.Request, float], tuple[int, bytes]]


class MassiveError(RuntimeError):
    """A Massive request failed. The message never contains the key."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def load_key(path: str | os.PathLike[str] | None = None) -> str:
    """The key from ``path``, ``MASSIVE_API_KEY_FILE`` or the systemd credential ``massive-key``."""

    chosen = path or os.environ.get("MASSIVE_API_KEY_FILE") or ""
    if not chosen and os.environ.get("CREDENTIALS_DIRECTORY"):
        chosen = str(Path(os.environ["CREDENTIALS_DIRECTORY"]) / "massive-key")
    if not chosen:
        raise MassiveError("clé Massive absente : MASSIVE_API_KEY_FILE ou crédential systemd massive-key")
    try:
        key = Path(chosen).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise MassiveError(f"clé Massive illisible ({type(exc).__name__})") from exc
    if not 16 <= len(key) <= 128 or not key.isascii() or not key.replace("_", "").replace("-", "").isalnum():
        raise MassiveError("clé Massive invalide : 16 à 128 caractères alphanumériques attendus")
    return key


def _urlopen(request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # https and host fixed by the caller
            return response.status, response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read(65_536)


class MassiveClient:
    def __init__(self, key: str, *, base_url: str = BASE_URL, opener: Opener = _urlopen, timeout: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise MassiveError("Massive : HTTPS obligatoire")
        self._key, self._base, self._host = key, base_url.rstrip("/"), parsed.netloc
        self._opener, self._timeout, self._sleep = opener, timeout, sleep

    def _get(self, url: str) -> dict[str, Any]:
        if urllib.parse.urlsplit(url).netloc != self._host:
            raise MassiveError("Massive : adresse de page suivante hors du fournisseur refusée")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json",
                                                       "User-Agent": "astra-massive"})
        for attempt in range(3):
            status, raw = self._opener(request, self._timeout)
            if status == 429 or status >= 500:
                self._sleep(min(30.0, 2.0 * 2 ** attempt))
                continue
            break
        if status in (401, 403):
            raise MassiveError("Massive : accès refusé (clé invalide ou fonction non incluse dans le plan)", status)
        if status != 200:
            raise MassiveError(f"Massive : HTTP {status}", status)
        if len(raw) > MAX_BYTES:
            raise MassiveError("Massive : réponse trop volumineuse")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise MassiveError("Massive : réponse illisible") from exc
        if not isinstance(data, dict) or data.get("status") not in ("OK", "DELAYED"):
            raise MassiveError(f"Massive : statut {data.get('status') if isinstance(data, dict) else '?'}")
        return data

    def aggregates(self, symbol: str, timeframe: str, start: date | datetime, end: date | datetime) -> list[dict[str, Any]]:
        """Raw aggregate rows for the period, oldest first, following the provider's pagination."""

        try:
            symbol = canonical_symbol(symbol)
        except MarketDataError as exc:
            raise MassiveError(str(exc)) from exc
        multiplier_span = TIMESPANS.get(timeframe)
        if symbol not in SYMBOL_MAP or multiplier_span is None:
            raise MassiveError(f"série non prise en charge : {symbol} {timeframe}")
        multiplier, span = multiplier_span
        first = start.date().isoformat() if isinstance(start, datetime) else start.isoformat()
        last = end.date().isoformat() if isinstance(end, datetime) else end.isoformat()
        query = urllib.parse.urlencode({"adjusted": "true", "sort": "asc", "limit": MAX_PAGE})
        url = f"{self._base}/v2/aggs/ticker/{SYMBOL_MAP[symbol]}/range/{multiplier}/{span}/{first}/{last}?{query}"
        rows: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            data = self._get(url)
            rows.extend(data.get("results") or [])
            url = data.get("next_url") or ""
            if not url:
                return rows
        raise MassiveError("Massive : trop de pages pour une seule requête")


def to_candles(rows: list[dict[str, Any]], symbol: str, timeframe: str, *,
               now: datetime | None = None) -> tuple[list[Candle], int]:
    """(closed candles, rejected rows). A bar still forming, incoherent prices or a repeated start are dropped."""

    now = now or datetime.now(UTC)
    width = timedelta(seconds=TIMEFRAMES[timeframe])
    out: dict[datetime, Candle] = {}
    rejected = 0
    for row in rows:
        try:
            start = datetime.fromtimestamp(int(row["t"]) / 1000, UTC)
            o, h, low, c = (float(row[k]) for k in ("o", "h", "l", "c"))
            volume = float(row.get("v") or 0)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            rejected += 1
            continue
        coherent = 0 < low <= min(o, c) and max(o, c) <= h and all(v == v and v < 1e6 for v in (o, h, low, c))
        if not coherent or start + width > now:  # incoherent, or the window is not over: not a closed bar
            rejected += 1
            continue
        out[start] = Candle(canonical_symbol(symbol), timeframe, start, o, h, low, c, volume, SOURCE, KIND_OHLC)
    return sorted(out.values(), key=lambda c: c.timestamp), rejected


def ingest(store: CandleStore, client: MassiveClient, symbol: str, timeframe: str, start: date, end: date, *,
           actor: str = "worker", now: datetime | None = None) -> dict[str, Any]:
    """Fetch one series and store it (idempotent). The report names the provider, the data nature and the counts."""

    store.register_source(SOURCE, "MASSIVE_REST", "HISTORICAL",
                          "Massive, agrégats Forex construits à partir des cotations bid/ask (pas des transactions) ; "
                          "le volume mesure l'activité de cotation, pas un volume négocié centralisé")
    store.source_attempt(SOURCE)
    import_id = store.begin_import(SOURCE, symbol, timeframe, "HISTORICAL", actor)
    try:
        rows = client.aggregates(symbol, timeframe, start, end)
        candles, rejected = to_candles(rows, symbol, timeframe, now=now)
        inserted, same, conflicts = store.store(candles, "HISTORICAL", import_id)
        last = candles[-1].timestamp.isoformat() if candles else None
        report = {"source": SOURCE, "symbol_map_version": SYMBOL_MAP_VERSION, "symbol": symbol, "timeframe": timeframe,
                  "received": len(rows), "accepted": len(candles), "rejected": rejected, "inserted": inserted,
                  "already_stored": same, "conflicts": conflicts, "first": candles[0].timestamp.isoformat() if candles else None,
                  "last": last}
        store.source_success(SOURCE, last)
        store.finish_import(import_id, "OK", report)
        return {"status": "OK", "import_id": import_id, **report}
    except (MassiveError, MarketDataError) as exc:
        failures = store.source_failure(SOURCE, str(exc))
        store.finish_import(import_id, "FAILED", {"source": SOURCE, "error": str(exc), "consecutive_failures": failures})
        return {"status": "FAILED", "import_id": import_id, "error": str(exc), "consecutive_failures": failures}


def check(client: MassiveClient, *, today: date | None = None) -> dict[str, Any]:
    """What this key really returns today: one small request per timeframe, nothing stored, nothing assumed."""

    today = today or datetime.now(UTC).date()
    out: dict[str, Any] = {"provider": "Massive", "checked_at": datetime.now(UTC).isoformat(), "series": {}}
    windows = {"M5": 1, "M15": 2, "H1": 5, "H4": 10, "D1": 30}
    for timeframe, days in windows.items():
        try:
            rows = client.aggregates("EURUSD", timeframe, today - timedelta(days=days), today)
            newest = max((int(r["t"]) for r in rows), default=None)
            out["series"][timeframe] = {"accessible": True, "bars": len(rows),
                                        "newest": datetime.fromtimestamp(newest / 1000, UTC).isoformat() if newest else None}
        except MassiveError as exc:
            out["series"][timeframe] = {"accessible": False, "status": exc.status, "error": str(exc)}
    out["any_accessible"] = any(v["accessible"] for v in out["series"].values())
    return out
