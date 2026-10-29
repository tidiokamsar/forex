"""MetaTrader 5 DEMO data feed: account check and closed candles from the MT5 terminal running on Windows.

On a desktop, ASTRA never holds the account password: the account holder signs in once in the terminal
(password saved there), and the official ``MetaTrader5`` package (MetaQuotes Ltd., Windows only) attaches to
that running terminal. On the server (terminal under Wine, no one at the screen), the password deposited by the
account holder (deploy/astra-set-mt5-demo-password) is handed to the bridge by systemd and passed to
``initialize`` only. This module reads only; it has no order function.

Guards, checked on every connection:
- the account must report ``ACCOUNT_TRADE_MODE_DEMO`` and its server must be in ``ALLOWED_SERVERS``,
  otherwise nothing is read (a real or contest account is refused, whatever the server name says);
- the bar still forming is never returned, only closed candles.

Time: MT5 stamps bars in the trade server's clock, not UTC. On MetaQuotes-Demo that clock is UTC+2 in winter
and UTC+3 in summer with the European switch dates (``SERVER_ZONES``). Measured on 2026-10-28 (live tick
2.00 h ahead of UTC, between the European and US switches) and checked against the ECB 14:15 CET fixes: on
the 42 days where the two rules differ, the European rule is 3.36 pips from the fix on average, the US rule
(New York + 7) 11.72 pips; on 60 other days both give 1.55 pips.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from ..marketdata import KIND_OHLC, PAIRS, Candle, MarketDataError, canonical_symbol, canonical_timeframe

DEFAULT_TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SERVER_ZONES = {"MetaQuotes-Demo": "Europe/Athens"}  # server -> IANA zone with the same offsets and switch dates
ALLOWED_SERVERS = frozenset(SERVER_ZONES)
TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")
MAX_BARS = 100_000


class Mt5DemoError(RuntimeError):
    """The terminal is unreachable, or the account is not an allowed DEMO account: nothing was read."""


def source_name(server: str) -> str:
    return "MT5_" + server.upper().replace("-", "_")


def _load() -> ModuleType:
    try:
        import MetaTrader5  # official package, Windows only; not a dependency of the server

        return MetaTrader5
    except ImportError as exc:
        raise Mt5DemoError("paquet MetaTrader5 absent : pip install MetaTrader5 (Windows, terminal MT5 installé)") from exc


class Mt5DemoFeed:
    """Read-only access to one DEMO account of a running MT5 terminal."""

    def __init__(self, terminal_path: str = DEFAULT_TERMINAL, module: ModuleType | None = None,
                 timeout_ms: int = 20_000, credentials: tuple[int, str, str] | None = None) -> None:
        """``credentials`` (login, password, server) signs the terminal in, for an unattended terminal (server);
        without it the terminal must already be signed in by the account holder."""

        self.mt5 = module or _load()
        self.terminal_path = terminal_path
        self.timeout_ms = timeout_ms
        self.account: dict[str, Any] | None = None
        self._depth = 0  # the terminal connection can be entered again by the code running inside it
        if credentials is not None and credentials[2] not in ALLOWED_SERVERS:
            raise Mt5DemoError(f"serveur {credentials[2]!r} non vérifié (autorisés : {', '.join(sorted(ALLOWED_SERVERS))})")
        self._credentials = credentials  # never logged nor returned

    def __enter__(self) -> Mt5DemoFeed:
        if self._depth == 0:
            self.connect()
        self._depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            self.mt5.shutdown()

    def connect(self) -> dict[str, Any]:
        mt5 = self.mt5
        signin = {}
        if self._credentials is not None:
            login, password, server = self._credentials
            signin = {"login": int(login), "password": password, "server": server}
        if not mt5.initialize(path=self.terminal_path, timeout=self.timeout_ms, **signin):
            raise Mt5DemoError(f"terminal MT5 injoignable : {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            mt5.shutdown()
            raise Mt5DemoError("aucun compte connecté dans le terminal MT5")
        if info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            mt5.shutdown()
            raise Mt5DemoError(f"compte {info.login} refusé : ce n'est pas un compte DEMO")
        if info.server not in ALLOWED_SERVERS:
            mt5.shutdown()
            raise Mt5DemoError(f"serveur {info.server!r} non vérifié (autorisés : {', '.join(sorted(ALLOWED_SERVERS))})")
        terminal = mt5.terminal_info()
        self.account = {"login": info.login, "server": info.server, "environment": "DEMO", "currency": info.currency,
                        "balance": info.balance, "equity": info.equity, "leverage": info.leverage,
                        "margin": getattr(info, "margin", 0.0), "margin_free": getattr(info, "margin_free", info.equity),
                        "terminal_connected": bool(terminal and terminal.connected),
                        "algo_trading_enabled": bool(terminal and terminal.trade_allowed),
                        "source": source_name(info.server)}
        return self.account

    def refresh_account(self) -> dict[str, Any]:
        """The account figures as they are now (balance, equity and margins move with every price tick)."""

        info = self.mt5.account_info()
        if info is None or self.account is None:
            raise Mt5DemoError("compte indisponible")
        self.account.update(balance=info.balance, equity=info.equity, margin=getattr(info, "margin", 0.0),
                            margin_free=getattr(info, "margin_free", info.equity))
        return self.account

    def _zone(self) -> ZoneInfo:
        if self.account is None:
            raise Mt5DemoError("connect() d'abord")
        return ZoneInfo(SERVER_ZONES[self.account["server"]])

    def _utc(self, server_seconds: int) -> str:
        """A time written in the trade server's clock, as an ISO time in UTC."""

        wall = datetime.fromtimestamp(int(server_seconds), UTC).replace(tzinfo=self._zone())
        return wall.astimezone(UTC).isoformat()

    def quotes(self) -> list[dict[str, Any]]:
        """Latest bid and ask of the pairs the terminal quotes, with the tick time in UTC."""

        out = []
        for symbol in PAIRS:
            if not self.mt5.symbol_select(symbol, True):
                continue
            tick = self.mt5.symbol_info_tick(symbol)
            if tick is None or not tick.bid or not tick.ask:
                continue
            stamp = self._utc(tick.time) if getattr(tick, "time", 0) else datetime.now(UTC).isoformat()
            out.append({"symbol": symbol, "bid": float(tick.bid), "ask": float(tick.ask), "time": stamp})
        return out

    def candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        """The ``count`` most recent closed candles, timestamps in UTC (bar open time)."""

        try:
            symbol, timeframe = canonical_symbol(symbol), canonical_timeframe(timeframe)
        except MarketDataError as exc:
            raise Mt5DemoError(str(exc)) from exc
        if symbol not in PAIRS or timeframe not in TIMEFRAMES:
            raise Mt5DemoError(f"série non prise en charge : {symbol} {timeframe}")
        if not 1 <= count <= MAX_BARS:
            raise Mt5DemoError(f"nombre de bougies entre 1 et {MAX_BARS}")
        mt5 = self.mt5
        if not mt5.symbol_select(symbol, True):
            raise Mt5DemoError(f"symbole {symbol} indisponible : {mt5.last_error()}")
        # Position 1 skips the bar still forming at position 0.
        rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"), 1, count)
        if rates is None:
            raise Mt5DemoError(f"historique {symbol} {timeframe} indisponible : {mt5.last_error()}")
        return to_candles(rates, symbol, timeframe, self._zone(), self.account["source"])


def to_candles(rates: Iterable[Any], symbol: str, timeframe: str, zone: ZoneInfo, source: str) -> list[Candle]:
    result = []
    for row in rates:
        # The server clock is written as if it were UTC seconds: read it as wall time in the server zone.
        wall = datetime.fromtimestamp(int(row["time"]), UTC).replace(tzinfo=zone)
        result.append(Candle(symbol, timeframe, wall.astimezone(UTC), float(row["open"]), float(row["high"]),
                             float(row["low"]), float(row["close"]), float(row["tick_volume"]), source, KIND_OHLC))
    return result


def to_csv(candles: Iterable[Candle]) -> bytes:
    """CSV in the dashboard import format (UTC timestamps)."""

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    for c in candles:
        writer.writerow([c.timestamp.strftime("%Y-%m-%d %H:%M:%S"), repr(c.open), repr(c.high), repr(c.low),
                         repr(c.close), repr(c.volume)])
    return out.getvalue().encode()
