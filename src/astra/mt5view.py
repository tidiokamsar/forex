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

from . import mt5accounting

SERVERS = frozenset({"MetaQuotes-Demo"})  # same set as brokers.mt5_demo.ALLOWED_SERVERS (checked by a test)
CANDLE_TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")
MAX_CANDLES = 2000
ASTRA_MAGIC = 20261028  # the magic number of ASTRA's orders (same value as brokers.mt5_demo_orders.MAGIC, tested)
MANUAL_MAGIC = 20261029  # orders placed from the site (same value as brokers.mt5_demo_orders.MAGIC_MANUAL, tested)
MAX_HISTORY_ITEMS = 120
MAX_PANEL_BYTES = 16_000
REQUEST_KINDS = ("pause", "resume", "close", "order", "modify_sl")
REQUEST_STATUSES = ("EN_ATTENTE", "EXECUTE", "REFUSE", "ECHEC", "EXPIRE")
REQUEST_TTL_SECONDS = 120   # a request not taken within two minutes is cancelled, never executed late
MAX_PENDING_REQUESTS = 10
STOP_PIPS_RANGE = (5.0, 300.0)
PAIR_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD")
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
CREATE TABLE IF NOT EXISTS mt5_deals (
    ticket INTEGER PRIMARY KEY, order_ticket INTEGER NOT NULL, position_id INTEGER NOT NULL, ts TEXT NOT NULL,
    symbol TEXT NOT NULL, type INTEGER NOT NULL, entry INTEGER NOT NULL, volume REAL NOT NULL, price REAL NOT NULL,
    commission REAL NOT NULL, swap REAL NOT NULL, fee REAL NOT NULL, profit REAL NOT NULL, magic INTEGER NOT NULL,
    comment TEXT NOT NULL, reason INTEGER NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mt5_orders (
    ticket INTEGER PRIMARY KEY, position_id INTEGER NOT NULL, ts TEXT NOT NULL, symbol TEXT NOT NULL, type INTEGER NOT NULL,
    state INTEGER NOT NULL, volume REAL NOT NULL, magic INTEGER NOT NULL, comment TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mt5_sync (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mt5_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, params_json TEXT NOT NULL,
    status TEXT NOT NULL, result_json TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS mt5_deals_position ON mt5_deals(position_id);
"""


class Mt5ViewError(ValueError):
    pass


def _history_time(value: Any, limit: datetime) -> str:
    try:
        moment = datetime.fromisoformat(_text(value, "ts", 40))
    except ValueError as exc:
        raise Mt5ViewError("ts : date ISO attendue") from exc
    if moment.tzinfo is None or moment > limit:
        raise Mt5ViewError("ts : date UTC passée attendue")
    return moment.astimezone(UTC).isoformat()


def validate_request(payload: Any) -> tuple[str, dict[str, Any]]:
    """A site request, as the bridge will execute it. The page can ask; it can never choose the volume of an order:
    the bridge computes it from the risk limit."""

    if not isinstance(payload, dict) or payload.get("kind") not in REQUEST_KINDS:
        raise Mt5ViewError(f"demande : type parmi {', '.join(REQUEST_KINDS)}")
    kind = payload["kind"]
    params: dict[str, Any] = {}
    if kind in ("close", "modify_sl"):
        ticket = payload.get("ticket")
        if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
            raise Mt5ViewError("ticket : entier positif attendu")
        params["ticket"] = ticket
    if kind == "modify_sl":
        params["sl"] = _number(payload.get("sl"), "sl")
        if params["sl"] <= 0:
            raise Mt5ViewError("sl : prix positif attendu")
    if kind == "order":
        symbol = payload.get("symbol")
        if symbol not in PAIR_SYMBOLS or payload.get("side") not in ("LONG", "SHORT"):
            raise Mt5ViewError("ordre : paire connue et sens LONG ou SHORT attendus")
        stop_pips = _number(payload.get("stop_pips"), "stop_pips")
        if not STOP_PIPS_RANGE[0] <= stop_pips <= STOP_PIPS_RANGE[1]:
            raise Mt5ViewError(f"stop_pips : entre {STOP_PIPS_RANGE[0]:g} et {STOP_PIPS_RANGE[1]:g} pips")
        if "lots" in payload or "volume" in payload:
            raise Mt5ViewError("le volume n'est jamais saisi : il est calculé par le risque")
        params.update(symbol=symbol, side=payload["side"], stop_pips=stop_pips)
    if payload.get("reason"):
        params["reason"] = _text(payload["reason"], "motif", 120)
    return kind, params


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
        for field in ("swap", "tp"):
            item[field] = _number(p.get(field, 0.0), field)
        for field in ("magic", "identifier", "reason"):
            item[field] = int(_number(p.get(field, 0), field))
        item["comment"] = str(p.get("comment") or "")[:40]
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
        item["status"] = str(a["status"])[:20] if a.get("status") else None
        for field in ("lots", "price", "sl", "profit", "requested_price", "spread_pips", "slippage_pips", "latency_ms"):
            item[field] = _number(a[field], field) if a.get(field) is not None else None
        clean_actions.append(item)
    result = {"account": clean_account, "positions": clean_positions, "actions": clean_actions}
    for name in ("risk", "reconciliation", "bridge"):
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
        """One equity point per minute at most. No baseline is kept: the starting capital comes from the broker's
        deposit deals, the peak and the drawdown from this series (mt5accounting)."""

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

    # -- broker history ---------------------------------------------------------------------------------

    def record_history(self, payload: Any) -> dict[str, int]:
        """Deals and orders as the broker reports them, keyed by the broker's tickets (idempotent)."""

        if not isinstance(payload, dict):
            raise Mt5ViewError("corps JSON attendu")
        source_name(payload.get("server") if isinstance(payload.get("server"), str) else "")  # allowed server only
        deals, orders = payload.get("deals", []), payload.get("orders", [])
        if not isinstance(deals, list) or not isinstance(orders, list) or len(deals) > MAX_HISTORY_ITEMS \
                or len(orders) > MAX_HISTORY_ITEMS:
            raise Mt5ViewError(f"deals et orders : listes de {MAX_HISTORY_ITEMS} éléments au plus")
        limit = _now() + timedelta(minutes=5)
        clean_deals, clean_orders = [], []
        for d in deals:
            if not isinstance(d, dict):
                raise Mt5ViewError("transaction invalide")
            clean_deals.append((int(_number(d.get("ticket"), "ticket")), int(_number(d.get("order"), "order")),
                                int(_number(d.get("position_id"), "position_id")), _history_time(d.get("ts"), limit),
                                str(d.get("symbol") or "")[:12], int(_number(d.get("type"), "type")),
                                int(_number(d.get("entry"), "entry")), _number(d.get("volume"), "volume"),
                                _number(d.get("price"), "price"), _number(d.get("commission"), "commission"),
                                _number(d.get("swap"), "swap"), _number(d.get("fee"), "fee"), _number(d.get("profit"), "profit"),
                                int(_number(d.get("magic"), "magic")), str(d.get("comment") or "")[:60],
                                int(_number(d.get("reason"), "reason"))))
        for o in orders:
            if not isinstance(o, dict):
                raise Mt5ViewError("ordre invalide")
            clean_orders.append((int(_number(o.get("ticket"), "ticket")), int(_number(o.get("position_id"), "position_id")),
                                 _history_time(o.get("ts"), limit), str(o.get("symbol") or "")[:12],
                                 int(_number(o.get("type"), "type")), int(_number(o.get("state"), "state")),
                                 _number(o.get("volume"), "volume"), int(_number(o.get("magic"), "magic")),
                                 str(o.get("comment") or "")[:60]))
        now = _now().isoformat()
        with self._connect() as db:
            for row in clean_deals:
                db.execute("INSERT OR REPLACE INTO mt5_deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*row, now))
            for row in clean_orders:
                db.execute("INSERT OR REPLACE INTO mt5_orders VALUES (?,?,?,?,?,?,?,?,?,?)", (*row, now))
            db.execute("INSERT OR REPLACE INTO mt5_sync(key, value) VALUES ('history_synced_at', ?)", (now,))
        return {"deals": len(clean_deals), "orders": len(clean_orders)}

    def last_event_at(self, kinds: tuple[str, ...]) -> str | None:
        marks = ",".join("?" for _ in kinds)
        with self._connect() as db:
            row = db.execute(f"SELECT MAX(ts) FROM mt5_events WHERE kind IN ({marks})", kinds).fetchone()
        return row[0]

    # -- requests from the site, executed by the bridge ------------------------------------------------------

    def create_request(self, payload: Any, actor: str) -> int:
        kind, params = validate_request(payload)
        now = _now().isoformat()
        with self._connect() as db:
            waiting = db.execute("SELECT COUNT(*) FROM mt5_requests WHERE status = 'EN_ATTENTE'").fetchone()[0]
            if waiting >= MAX_PENDING_REQUESTS:
                raise Mt5ViewError("trop de demandes en attente")
            cursor = db.execute("INSERT INTO mt5_requests(ts, actor, kind, params_json, status) VALUES (?,?,?,?, 'EN_ATTENTE')",
                                (now, actor, kind, json.dumps(params, ensure_ascii=False)))
        return int(cursor.lastrowid)

    def _expire(self, db: sqlite3.Connection) -> None:
        cutoff = (_now() - timedelta(seconds=REQUEST_TTL_SECONDS)).isoformat()
        message = json.dumps({"message": "non prise en charge à temps : annulée, jamais exécutée"})
        db.execute("UPDATE mt5_requests SET status = 'EXPIRE', finished_at = ?, result_json = ? "
                   "WHERE status = 'EN_ATTENTE' AND ts < ?", (_now().isoformat(), message, cutoff))

    def pending_requests(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._expire(db)
            rows = db.execute("SELECT * FROM mt5_requests WHERE status = 'EN_ATTENTE' ORDER BY id").fetchall()
        return [{"id": r["id"], "ts": r["ts"], "actor": r["actor"], "kind": r["kind"], **json.loads(r["params_json"])}
                for r in rows]

    def finish_request(self, request_id: int, status: str, result: Any) -> None:
        if status not in ("EXECUTE", "REFUSE", "ECHEC") or not isinstance(result, dict):
            raise Mt5ViewError("résultat : statut EXECUTE, REFUSE ou ECHEC et objet attendus")
        if len(json.dumps(result)) > MAX_PANEL_BYTES:
            raise Mt5ViewError("résultat trop volumineux")
        with self._connect() as db:
            cursor = db.execute("UPDATE mt5_requests SET status = ?, result_json = ?, finished_at = ? "
                                "WHERE id = ? AND status = 'EN_ATTENTE'",
                                (status, json.dumps(result), _now().isoformat(), request_id))
            if cursor.rowcount != 1:
                raise Mt5ViewError("demande inconnue ou déjà traitée")

    def requests(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._expire(db)
            rows = db.execute("SELECT * FROM mt5_requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "actor": r["actor"], "kind": r["kind"], "params": json.loads(r["params_json"]),
                 "status": r["status"], "result": json.loads(r["result_json"]) if r["result_json"] else None,
                 "finished_at": r["finished_at"]} for r in rows]

    def deals(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mt5_deals ORDER BY ts, ticket").fetchall()
        return [{"ticket": r["ticket"], "order": r["order_ticket"], "position_id": r["position_id"], "ts": r["ts"],
                 "symbol": r["symbol"], "type": r["type"], "entry": r["entry"], "volume": r["volume"], "price": r["price"],
                 "commission": r["commission"], "swap": r["swap"], "fee": r["fee"], "profit": r["profit"],
                 "magic": r["magic"], "comment": r["comment"], "reason": r["reason"]} for r in rows]

    def orders(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mt5_orders ORDER BY ts, ticket").fetchall()
        return [{"ticket": r["ticket"], "position_id": r["position_id"], "ts": r["ts"], "symbol": r["symbol"],
                 "type": r["type"], "state": r["state"], "volume": r["volume"], "magic": r["magic"],
                 "comment": r["comment"]} for r in rows]

    def sync_info(self) -> dict[str, str]:
        with self._connect() as db:
            return {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM mt5_sync")}

    def equity_series(self, since: str | None = None) -> list[tuple[str, float]]:
        with self._connect() as db:
            rows = db.execute("SELECT ts, equity FROM mt5_equity WHERE ts >= ? ORDER BY ts", (since or "",)).fetchall()
        return [(r["ts"], r["equity"]) for r in rows]

    def accounting(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        """The single set of figures of the dashboard (mt5accounting), on the stored snapshot and history."""

        if snapshot is None:
            with self._connect() as db:
                row = db.execute("SELECT payload_json FROM mt5_snapshot WHERE id = 1").fetchone()
            snapshot = json.loads(row["payload_json"]) if row else None
        return mt5accounting.accounting(snapshot, self.deals(), self.orders(), self.equity_series(), _now(), ASTRA_MAGIC)

    def performance(self, days: int = 7, curve_points: int = 240) -> dict[str, Any]:
        """Figures behind the dashboard cards and the performance curve, from the snapshots and closed trades."""

        now = _now()
        since = (now - timedelta(days=days)).isoformat()
        with self._connect() as db:
            curve = db.execute("SELECT ts, equity FROM mt5_equity WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
            rows_open = db.execute("SELECT detail_json FROM mt5_events WHERE kind = 'open' AND ts >= ?", (since,)).fetchall()
            refused = db.execute("SELECT COUNT(*) FROM mt5_events WHERE kind = 'refused' AND ts >= ?", (since,)).fetchone()[0]
        opens = [json.loads(r["detail_json"]) for r in rows_open]
        stride = max(1, len(curve) // curve_points)
        points = [{"ts": r["ts"], "equity": r["equity"]} for r in curve[::stride]]
        if curve and (len(curve) - 1) % stride:
            points.append({"ts": curve[-1]["ts"], "equity": curve[-1]["equity"]})
        return {"days": days, "curve": points,
                "execution": {"orders": len(opens), "refused": refused, "avg_slippage_pips": _mean(opens, "slippage_pips"),
                              "avg_spread_pips": _mean(opens, "spread_pips"), "avg_latency_ms": _mean(opens, "latency_ms")}}

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
        deals = self.deals()
        positions = [{**p, "origin": mt5accounting.origin(p, deals, ASTRA_MAGIC, MANUAL_MAGIC)} for p in snapshot["positions"]]
        accounting = self.accounting({**snapshot, "positions": positions})
        recent = [{**d, "net": round(d["profit"] + d["commission"] + d["swap"] + d["fee"], 2),
                   "astra": d["magic"] == ASTRA_MAGIC and str(d["comment"]).startswith("ASTRA")}
                  for d in reversed(deals) if d["type"] in (0, 1)][:25]
        result.update(connected=age <= STALE_AFTER_SECONDS, age_seconds=int(age), received_at=row["received_at"],
                      actor=row["actor"], accounting=accounting, sync=self.sync_info(), recent_deals=recent,
                      snapshot={**snapshot, "positions": positions, "open_profit": accounting["floating_total"],
                                "astra_positions": sum(1 for p in positions if p["origin"]["astra"])})
        return result
