"""What the dashboard shows of the MT5 DEMO account: the latest snapshot and the orders the bridge sent.

The bridge (``mt5_bridge``), which sits next to the MT5 terminal, posts a snapshot after every cycle with an
agent token holding the ``mt5`` scope. This module only stores and returns what it is given, after checking
its shape: nothing here reaches MT5, and nothing here can send an order. A snapshot of anything that is not a
DEMO account is refused, so the page can never present a real account.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SERVERS = frozenset({"MetaQuotes-Demo"})  # same set as brokers.mt5_demo.ALLOWED_SERVERS (checked by a test)
CANDLE_TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")
MAX_CANDLES = 2000
MAX_PANEL_BYTES = 16_000
MAX_POSITIONS = 50
MAX_ACTIONS = 50
MAX_EVENTS_KEPT = 500
MAX_EQUITY_POINTS = 30_000
EQUITY_EVERY_SECONDS = 60
QUOTES_STALE_AFTER_SECONDS = 60
MAX_QUOTES = 10
STALE_AFTER_SECONDS = 15 * 60
ACCOUNT_FIELDS = ("balance", "equity", "margin", "margin_free", "leverage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mt5_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    taken_at TEXT NOT NULL, received_at TEXT NOT NULL, actor TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mt5_events (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, symbol TEXT, detail_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mt5_quotes (
    symbol TEXT PRIMARY KEY, bid REAL NOT NULL, ask REAL NOT NULL, tick_time TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mt5_equity (ts TEXT PRIMARY KEY, equity REAL NOT NULL, balance REAL NOT NULL);
CREATE TABLE IF NOT EXISTS mt5_meta (key TEXT PRIMARY KEY, value REAL NOT NULL);
"""


class Mt5ViewError(ValueError):
    pass


def _mean(items: list[dict[str, Any]], field: str) -> float | None:
    values = [i[field] for i in items if i.get(field) is not None]
    return round(sum(values) / len(values), 2) if values else None


def source_name(server: str) -> str:
    if server not in SERVERS:
        raise Mt5ViewError(f"serveur {server!r} non vérifié")
    return "MT5_" + server.upper().replace("-", "_")


def parse_candles(payload: Any, now: datetime | None = None) -> tuple[str, str, str, list[tuple[Any, ...]]]:
    """(source, symbol, timeframe, rows) of a candle post. Closed bars only, in the past, with coherent prices."""

    if not isinstance(payload, dict):
        raise Mt5ViewError("corps JSON attendu")
    source = source_name(payload.get("server") if isinstance(payload.get("server"), str) else "")
    symbol, timeframe = _text(payload.get("symbol"), "symbole", 12), payload.get("timeframe")
    if timeframe not in CANDLE_TIMEFRAMES:
        raise Mt5ViewError(f"unité de temps : {', '.join(CANDLE_TIMEFRAMES)}")
    raw = payload.get("candles")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CANDLES:
        raise Mt5ViewError(f"bougies : liste de 1 à {MAX_CANDLES} attendue")
    limit = now or _now()
    rows = []
    for c in raw:
        if not isinstance(c, dict):
            raise Mt5ViewError("bougie invalide")
        try:
            moment = datetime.fromisoformat(_text(c.get("ts"), "ts", 40))
        except ValueError as exc:
            raise Mt5ViewError("ts : date ISO attendue") from exc
        if moment.tzinfo is None or moment > limit:
            raise Mt5ViewError("ts : date UTC passée attendue")
        o, h, low, cl = (_number(c.get(k), k) for k in ("open", "high", "low", "close"))
        if not (0 < low <= min(o, cl) and max(o, cl) <= h < 1e6):
            raise Mt5ViewError("prix de bougie incohérents")
        rows.append((moment.astimezone(UTC), o, h, low, cl, _number(c.get("volume", 0), "volume")))
    return source, symbol, timeframe, rows


def _now() -> datetime:
    return datetime.now(UTC)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Mt5ViewError(f"{label} : nombre attendu")
    return float(value)


def _text(value: Any, label: str, limit: int = 40) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise Mt5ViewError(f"{label} : texte de 1 à {limit} caractères attendu")
    return value


def validate(payload: Any) -> dict[str, Any]:
    """The snapshot as stored, or Mt5ViewError. Only DEMO accounts are accepted."""

    if not isinstance(payload, dict) or not isinstance(payload.get("account"), dict):
        raise Mt5ViewError("instantané MT5 attendu")
    account = payload["account"]
    if account.get("environment") != "DEMO":
        raise Mt5ViewError("seul un compte DEMO est accepté")
    clean_account = {"login": int(_number(account.get("login"), "login")), "server": _text(account.get("server"), "serveur"),
                     "environment": "DEMO", "currency": _text(account.get("currency"), "devise", 8),
                     "algo_trading_enabled": bool(account.get("algo_trading_enabled"))}
    for field in ACCOUNT_FIELDS:
        clean_account[field] = _number(account.get(field), field)
    positions = payload.get("positions", [])
    actions = payload.get("actions", [])
    if not isinstance(positions, list) or len(positions) > MAX_POSITIONS:
        raise Mt5ViewError("positions : liste attendue")
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise Mt5ViewError("actions : liste attendue")
    clean_positions = []
    for p in positions:
        if not isinstance(p, dict):
            raise Mt5ViewError("position invalide")
        item = {"ticket": int(_number(p.get("ticket"), "ticket")), "symbol": _text(p.get("symbol"), "symbole", 12),
                "side": p.get("side"), "astra": bool(p.get("astra"))}
        if item["side"] not in ("LONG", "SHORT"):
            raise Mt5ViewError("sens de position invalide")
        for field in ("volume", "price_open", "sl", "profit"):
            item[field] = _number(p.get(field), field)
        item["price_current"] = _number(p["price_current"], "price_current") if p.get("price_current") is not None else None
        item["time_open"] = _text(p["time_open"], "time_open", 40) if p.get("time_open") else None
        clean_positions.append(item)
    clean_actions = []
    for a in actions:
        if not isinstance(a, dict):
            raise Mt5ViewError("action invalide")
        item = {"kind": _text(a.get("kind"), "kind", 30), "symbol": _text(a.get("symbol"), "symbole", 12),
                "side": a.get("side") if a.get("side") in ("LONG", "SHORT") else None,
                "reason": _text(a["reason"], "motif", 200) if a.get("reason") else None}
        for field in ("lots", "price", "sl", "profit", "requested_price", "spread_pips", "slippage_pips", "latency_ms"):
            item[field] = _number(a[field], field) if a.get(field) is not None else None
        clean_actions.append(item)
    result = {"account": clean_account, "positions": clean_positions, "actions": clean_actions}
    for name in ("risk", "reconciliation"):
        value = payload.get(name)
        if value is not None:
            if not isinstance(value, dict) or len(json.dumps(value)) > MAX_PANEL_BYTES:
                raise Mt5ViewError(f"{name} : objet de moins de 16 Ko attendu")
            result[name] = value
    return result


class Mt5View:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
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
            db.executescript(_SCHEMA)

    def record(self, payload: Any, actor: str) -> dict[str, Any]:
        snapshot = validate(payload)
        now = _now().isoformat()
        actions = snapshot.pop("actions")
        account = snapshot["account"]
        with self._connect() as db:
            self._track_equity(db, now, account["equity"], account["balance"])
            db.execute("INSERT INTO mt5_snapshot(id, taken_at, received_at, actor, payload_json) VALUES (1, ?, ?, ?, ?) "
                       "ON CONFLICT(id) DO UPDATE SET taken_at = excluded.taken_at, received_at = excluded.received_at, "
                       "actor = excluded.actor, payload_json = excluded.payload_json",
                       (now, now, actor, json.dumps(snapshot, ensure_ascii=False)))
            for a in actions:
                db.execute("INSERT INTO mt5_events(ts, kind, symbol, detail_json) VALUES (?,?,?,?)",
                           (now, a["kind"], a["symbol"], json.dumps(a, ensure_ascii=False)))
            db.execute("DELETE FROM mt5_events WHERE id NOT IN (SELECT id FROM mt5_events ORDER BY id DESC LIMIT ?)",
                       (MAX_EVENTS_KEPT,))
        return {"positions": len(snapshot["positions"]), "events": len(actions)}

    def _track_equity(self, db: sqlite3.Connection, now: str, equity: float, balance: float) -> None:
        """Baseline (first equity seen), peak equity and one curve point per minute at most."""

        db.execute("INSERT OR IGNORE INTO mt5_meta(key, value) VALUES ('baseline_equity', ?)", (equity,))
        db.execute("INSERT INTO mt5_meta(key, value) VALUES ('peak_equity', ?) ON CONFLICT(key) DO UPDATE SET "
                   "value = MAX(value, excluded.value)", (equity,))
        last = db.execute("SELECT ts FROM mt5_equity ORDER BY ts DESC LIMIT 1").fetchone()
        gap = None if last is None else (datetime.fromisoformat(now) - datetime.fromisoformat(last["ts"])).total_seconds()
        if gap is None or gap >= EQUITY_EVERY_SECONDS:
            db.execute("INSERT OR REPLACE INTO mt5_equity(ts, equity, balance) VALUES (?,?,?)", (now, equity, balance))
            db.execute("DELETE FROM mt5_equity WHERE ts NOT IN (SELECT ts FROM mt5_equity ORDER BY ts DESC LIMIT ?)",
                       (MAX_EQUITY_POINTS,))

    def record_quotes(self, payload: Any) -> dict[str, Any]:
        """Latest bid and ask per pair, as read from the terminal by the bridge."""

        quotes = payload.get("quotes") if isinstance(payload, dict) else None
        if not isinstance(quotes, list) or not 1 <= len(quotes) <= MAX_QUOTES:
            raise Mt5ViewError("cotations : liste de 1 à 10 attendue")
        clean = []
        for q in quotes:
            if not isinstance(q, dict):
                raise Mt5ViewError("cotation invalide")
            symbol = _text(q.get("symbol"), "symbole", 12)
            bid, ask = _number(q.get("bid"), "bid"), _number(q.get("ask"), "ask")
            if not (0 < bid <= ask < 1e6):
                raise Mt5ViewError("cotation incohérente (0 < bid <= ask attendu)")
            clean.append((symbol, bid, ask, _text(q.get("time"), "heure", 40)))
        now = _now().isoformat()
        with self._connect() as db:
            for symbol, bid, ask, tick_time in clean:
                db.execute("INSERT OR REPLACE INTO mt5_quotes(symbol, bid, ask, tick_time, received_at) VALUES (?,?,?,?,?)",
                           (symbol, bid, ask, tick_time, now))
        return {"quotes": len(clean)}

    def quotes(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mt5_quotes ORDER BY symbol").fetchall()
        now = _now()
        def age(row: sqlite3.Row) -> float:
            return (now - datetime.fromisoformat(row["received_at"])).total_seconds()

        return {r["symbol"]: {"bid": r["bid"], "ask": r["ask"], "tick_time": r["tick_time"],
                              "received_at": r["received_at"], "live": age(r) <= QUOTES_STALE_AFTER_SECONDS}
                for r in rows}

    def performance(self, days: int = 7, curve_points: int = 240) -> dict[str, Any]:
        """Figures behind the dashboard cards and the performance curve, from the snapshots and closed trades."""

        now = _now()
        since = (now - timedelta(days=days)).isoformat()
        today = now.date().isoformat()
        with self._connect() as db:
            meta = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM mt5_meta")}
            curve = db.execute("SELECT ts, equity FROM mt5_equity WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
            closes = [json.loads(r["detail_json"]) | {"ts": r["ts"]} for r in db.execute(
                "SELECT ts, detail_json FROM mt5_events WHERE kind = 'close' AND ts >= ? ORDER BY id", (since,))]
            rows_open = db.execute("SELECT detail_json FROM mt5_events WHERE kind = 'open' AND ts >= ?", (since,)).fetchall()
            refused = db.execute("SELECT COUNT(*) FROM mt5_events WHERE kind = 'refused' AND ts >= ?", (since,)).fetchone()[0]
        profits = [c["profit"] for c in closes if c.get("profit") is not None]
        opens = [json.loads(r["detail_json"]) for r in rows_open]
        today_profits = [c["profit"] for c in closes if c["ts"].startswith(today) and c.get("profit") is not None]
        wins, losses = [p for p in profits if p > 0], [p for p in profits if p < 0]
        stride = max(1, len(curve) // curve_points)
        points = [{"ts": r["ts"], "equity": r["equity"]} for r in curve[::stride]]
        if curve and (len(curve) - 1) % stride:
            points.append({"ts": curve[-1]["ts"], "equity": curve[-1]["equity"]})
        peak, baseline = meta.get("peak_equity"), meta.get("baseline_equity")
        return {"days": days, "baseline_equity": baseline, "peak_equity": peak, "curve": points,
                "trades": len(profits), "wins": len(wins), "losses": len(losses),
                "win_rate_pct": round(len(wins) / len(profits) * 100, 1) if profits else None,
                "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
                "net_closed": round(sum(profits), 2),
                "execution": {"orders": len(opens), "refused": refused, "avg_slippage_pips": _mean(opens, "slippage_pips"),
                              "avg_spread_pips": _mean(opens, "spread_pips"), "avg_latency_ms": _mean(opens, "latency_ms")},
                "today": {"trades": len(today_profits), "wins": sum(1 for p in today_profits if p > 0),
                          "losses": sum(1 for p in today_profits if p < 0)}}

    def overview(self, event_limit: int = 100) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM mt5_snapshot WHERE id = 1").fetchone()
            events = db.execute("SELECT ts, kind, symbol, detail_json FROM mt5_events ORDER BY id DESC LIMIT ?",
                                (event_limit,)).fetchall()
        result: dict[str, Any] = {"environment": "DEMO", "live_locked": True, "connected": False, "snapshot": None,
                                  "events": [{"ts": e["ts"], "kind": e["kind"], "symbol": e["symbol"],
                                              **json.loads(e["detail_json"])} for e in events]}
        if row is None:
            return result
        snapshot = json.loads(row["payload_json"])
        age = (_now() - datetime.fromisoformat(row["received_at"])).total_seconds()
        positions = snapshot["positions"]
        result.update(connected=age <= STALE_AFTER_SECONDS, age_seconds=int(age), received_at=row["received_at"],
                      actor=row["actor"],
                      snapshot={**snapshot, "open_profit": round(sum(p["profit"] for p in positions), 2),
                                "astra_positions": sum(1 for p in positions if p["astra"])})
        return result
