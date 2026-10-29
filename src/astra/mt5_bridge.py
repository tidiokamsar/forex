"""MT5 DEMO bridge: mirrors ASTRA's managed paper accounts onto one MetaTrader 5 DEMO account.

Runs on the Windows machine where the MT5 terminal is open (the server cannot reach MT5). Each cycle it reads
``/api/learn/overview`` with a ``read`` agent token: for every pair, the managed account "ASTRA auto · PAIR
(champion)" holds the position decided by the current champion (learning.py). The bridge makes the DEMO
account hold the same side:

- a position held by the paper account and not yet mirrored is opened on MT5 with a stop at the same distance
  and a size that loses at most ``RISK_PCT`` % of the MT5 equity at the stop (``MAX_LOTS`` at most);
- a pending exit or reversal on the paper account closes the MT5 position now (the reversal itself is opened
  once the paper account has opened it, so both always share the stop distance);
- a mirrored position stopped out on MT5 is not reopened for the same paper entry;
- only positions carrying ASTRA's magic number are closed; positions opened by hand are never touched.

Halts: the server must report SIMULATION with LIVE locked; a ``STOP`` file in the work directory stops every
order; a loss of ``DAILY_LOSS_PCT`` % of the MT5 equity since the start of the UTC day stops new entries.
Every decision goes to ``journal-<day>.jsonl`` in the work directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent import AgentHalt, ApiError, AstraClient, http_transport
from .brokers import mt5_demo
from .brokers.mt5_demo import DEFAULT_TERMINAL, Mt5DemoError
from .brokers.mt5_demo_orders import COMMENT, MAGIC, Mt5DemoTrader, Mt5OrderRejected, Mt5OrderUnknown
from .marketdata import pip_size
from .mt5_risk import AccountState, OrderRequest, RiskConfigError, RiskLimits, entries_state, evaluate_entry, load_limits

DEFAULT_INTERVAL_SECONDS = 300
CONTROLS_CACHE_SECONDS = 15
INTENT_GRACE_SECONDS = 120   # after an unknown outcome, look for the position this long before concluding "not executed"
INTENTS_KEPT = 40
PENDING = ("SOUMIS", "INCONNU")  # intention states that still block a new attempt on the same pair
STREAM_SECONDS = 5          # quotes, balance and open positions reach the dashboard this often
FAST_CANDLES_SECONDS = 60   # M5 and M15: the last bars, every minute
SLOW_CANDLES_SECONDS = 600  # H1, H4 and D1: the last bars, every ten minutes
BACKFILL_BARS = {"M5": 1500, "M15": 1500, "H1": 1500, "H4": 1500, "D1": 300}
CANDLES_PER_POST = 250  # about 35 KB of JSON
HISTORY_DAYS_FIRST = 30
HISTORY_DAYS_RECENT = 2
HISTORY_SECONDS = 60
ITEMS_PER_POST = 80  # keeps a history post well under the proxy's 64 KB body limit
RECENT_BARS = {"M5": 12, "M15": 8, "H1": 8, "H4": 6, "D1": 4}


def targets(overview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Wanted MT5 side per pair from the managed paper accounts (FLAT when nothing should be held)."""

    result = {}
    for pair in overview.get("pairs", []):
        symbol, session = pair["symbol"], pair.get("session")
        if not session or session.get("status") != "RUNNING":
            result[symbol] = {"side": "FLAT", "reason": "compte simulé absent ou arrêté"}
            continue
        account = session["account"]
        position = account["positions"].get(symbol)
        pending = account["pending"].get(symbol)
        side = position["side"] if position else "FLAT"
        if pending and pending["side"] != side:
            result[symbol] = {"side": "FLAT", "reason": f"décision en attente : {pending['label']}"}
        elif position:
            result[symbol] = {"side": side, "entry_ts": position["entry_ts"], "session_id": session["id"],
                              "stop_distance": abs(position["entry_ref"] - position["stop"]),
                              "reason": f"position {side} du compte simulé depuis {position['entry_ts']}"}
        else:
            result[symbol] = {"side": "FLAT", "reason": "aucune position sur le compte simulé"}
    return result


def post_history(client: AstraClient, history: dict[str, Any], server: str) -> dict[str, Any]:
    """Send a broker history in small posts (the server keys it by the broker's tickets, so repeats are harmless)."""

    sent: dict[str, Any] = {"deals": 0, "orders": 0, "answered": history["answered"], "terminal_error": history["last_error"]}
    for kind in ("deals", "orders"):
        items = history[kind]
        for start in range(0, len(items), ITEMS_PER_POST):
            client.post("/api/mt5/history", {"server": server, kind: items[start:start + ITEMS_PER_POST]})
        sent[kind] = len(items)
    return sent


def history_sync_once(days: int = 90) -> dict[str, Any]:
    """Read the broker's history from the signed-in terminal of this machine and send it to the server (read only)."""

    url = os.environ.get("ASTRA_MT5_URL", "").strip()
    token_file = os.environ.get("ASTRA_MT5_TOKEN_FILE", "").strip()
    login = os.environ.get("ASTRA_MT5_LOGIN", "").strip()
    if not (url and token_file and login.isdigit()):
        raise Mt5DemoError("ASTRA_MT5_URL, ASTRA_MT5_TOKEN_FILE et ASTRA_MT5_LOGIN sont obligatoires")
    client = AstraClient(http_transport(url, Path(_native(token_file)).read_text(encoding="utf-8").strip()))
    with Mt5DemoTrader(os.environ.get("ASTRA_MT5_TERMINAL") or DEFAULT_TERMINAL, expected_login=int(login)) as trader:
        history = trader.history(days)
        return post_history(client, history, trader.account["server"])


class Bridge:
    def __init__(self, client: AstraClient, trader: Mt5DemoTrader, workdir: Path,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 limits: RiskLimits | RiskConfigError | None = None) -> None:
        self.client, self.trader, self.workdir, self.clock = client, trader, workdir, clock
        self.limits: RiskLimits | RiskConfigError = limits if limits is not None else RiskLimits()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.state_path = workdir / "bridge-state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        self.state.setdefault("mirrored", {})
        self.state.setdefault("last_refusal", {})
        self.state.setdefault("intents", {})
        self.targets: dict[str, dict[str, Any]] = {}
        self.last_error: dict[str, str] | None = None
        self.history_synced_at: str | None = None
        self._controls: tuple[datetime, bool | None, bool | None] | None = None

    def note(self, kind: str, **fields: Any) -> None:
        now = self.clock()
        if kind in ("error", "publish_failed", "history_failed"):
            self.last_error = {"at": now.isoformat(), "kind": kind, "reason": str(fields.get("reason", ""))[:160]}
        line = json.dumps({"ts": now.isoformat(), "kind": kind, **fields}, ensure_ascii=False, default=str)
        with (self.workdir / f"journal-{now.date().isoformat()}.jsonl").open("a", encoding="utf-8") as out:
            out.write(line + "\n")

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def controls(self) -> tuple[bool | None, bool | None]:
        """(entries allowed, kill switch active) as the server reports them; (None, None) when unreadable."""

        now = self.clock()
        if self._controls is not None and (now - self._controls[0]).total_seconds() < CONTROLS_CACHE_SECONDS:
            return self._controls[1], self._controls[2]
        try:
            answer = self.client.get("/api/controls")
            allowed = answer.get("entries_allowed") is True
            kill = bool((answer.get("kill_switch") or {}).get("active", False))
        except (ApiError, OSError, AttributeError):
            allowed, kill = None, None
        self._controls = (now, allowed, kill)
        return allowed, kill

    def account_state(self, account: dict[str, Any], positions: list[Any]) -> AccountState:
        """The gateway's view of the account. The day start and the equity peak are kept across restarts."""

        today = self.clock().date().isoformat()
        if self.state.get("day") != today or not self.state.get("day_start_equity"):
            self.state["day"], self.state["day_start_equity"] = today, account["equity"]
        self.state["peak_equity"] = max(self.state.get("peak_equity") or 0.0, account["equity"])
        allowed, kill = self.controls()
        if (self.workdir / "STOP").exists():
            allowed, kill = False, True
        return AccountState(
            environment=account.get("environment"), login=account.get("login"), expected_login=self.trader.expected_login,
            equity=account["equity"], margin=account.get("margin"), day_start_equity=self.state["day_start_equity"],
            peak_equity=self.state["peak_equity"], positions=tuple({"symbol": p.symbol, "side": p.side, "volume": p.volume}
                                                                    for p in positions if p.astra),
            connector_ok=bool(account.get("terminal_connected", True)),
            algo_trading_enabled=account.get("algo_trading_enabled"), entries_allowed_by_server=allowed,
            kill_switch_active=kill)

    def risk_panel(self, state: AccountState) -> dict[str, Any]:
        decision = entries_state(state, self.limits)
        limits = self.limits.public() if isinstance(self.limits, RiskLimits) else None
        daily = ((state.day_start_equity - state.equity) / state.day_start_equity * 100) if state.day_start_equity else None
        drawdown = ((state.peak_equity - state.equity) / state.peak_equity * 100) if state.peak_equity else None
        return {"entries_allowed": decision.allowed, "checks": [c.public() for c in decision.checks], "limits": limits,
                "daily_loss_pct": None if daily is None else round(max(daily, 0.0), 3),
                "drawdown_pct": None if drawdown is None else round(max(drawdown, 0.0), 3)}

    def reconcile(self, positions: list[Any]) -> dict[str, Any]:
        """Compare the broker's ASTRA positions with what the paper accounts want. Alerts need a look;
        information lines are normal waiting states."""

        out: list[dict[str, str]] = []
        mine: dict[str, list[Any]] = {}
        for p in positions:
            if p.astra:
                mine.setdefault(p.symbol, []).append(p)
        for symbol, held in sorted(mine.items()):
            target = self.targets.get(symbol, {"side": "FLAT"})
            if len(held) > 1:
                out.append({"level": "alerte", "symbol": symbol, "code": "positions_multiples",
                            "detail": f"{len(held)} positions ASTRA sur {symbol}"})
            for p in held:
                if not p.sl:
                    out.append({"level": "alerte", "symbol": symbol, "code": "position_sans_stop",
                                "detail": f"ticket {p.ticket} sans stop"})
                if target["side"] != p.side:
                    out.append({"level": "alerte", "symbol": symbol, "code": "position_non_prevue",
                                "detail": f"{p.side} ouverte alors que la décision est {target['side']}"})
        for symbol, target in sorted(self.targets.items()):
            if target["side"] == "FLAT" or symbol in mine:
                continue
            closed = self.state["mirrored"].get(symbol) == target.get("entry_ts")
            code = "stop_declenche_ou_ferme" if closed else "entree_en_attente"
            detail = ("position fermée par le stop ou à la main : pas de réouverture pour la même entrée" if closed
                      else "décision d'entrée pas encore exécutée (portail de risque)")
            out.append({"level": "info", "symbol": symbol, "code": code, "detail": detail})
        return {"checked_at": self.clock().isoformat(), "ok": not any(d["level"] == "alerte" for d in out),
                "divergences": out}

    # -- order intentions (idempotence) ------------------------------------------------------------------

    def _intent(self, symbol: str, side: str, lots: float, sl: float, entry_ts: str) -> str:
        """Record the intention on disk BEFORE the order leaves: a crash or a lost answer cannot cause a second order."""

        key = hashlib.sha256(f"{symbol}|{side}|{entry_ts}|{self.clock().isoformat()}".encode()).hexdigest()[:8]
        self.state["intents"][key] = {"id": key, "symbol": symbol, "side": side, "lots": lots, "sl": sl, "entry_ts": entry_ts,
                                      "status": "SOUMIS", "at": self.clock().isoformat()}
        for old in sorted(self.state["intents"])[:-INTENTS_KEPT]:
            self.state["intents"].pop(old, None)
        self.save()
        return key

    def _set_intent(self, key: str, status: str, **fields: Any) -> None:
        self.state["intents"][key].update(status=status, updated_at=self.clock().isoformat(), **fields)
        self.save()

    def pending_intent(self, symbol: str) -> dict[str, Any] | None:
        return next((i for i in self.state["intents"].values() if i["symbol"] == symbol and i["status"] in PENDING), None)

    def resolve_intents(self, positions: list[Any]) -> list[dict[str, Any]]:
        """Settle every intention left open (lost answer, crash, restart) from what the account really holds."""

        settled = []
        for intent in list(self.state["intents"].values()):
            if intent["status"] not in PENDING:
                continue
            tag = f"{COMMENT}:{intent['id']}"
            found = next((p for p in positions if p.comment == tag and p.magic == MAGIC and p.symbol == intent["symbol"]), None)
            age = (self.clock() - datetime.fromisoformat(intent["at"])).total_seconds()
            if found:
                self.state["mirrored"][intent["symbol"]] = intent["entry_ts"]
                self._set_intent(intent["id"], "EXECUTE", ticket=found.ticket, lots_filled=found.volume,
                                 resolved="position retrouvée")
            elif age >= INTENT_GRACE_SECONDS:
                self._set_intent(intent["id"], "ABSENT", resolved="aucune position après le délai : ordre non exécuté")
            else:
                continue
            settled.append(self.state["intents"][intent["id"]])
        return settled

    def intents_public(self) -> list[dict[str, Any]]:
        keep = ("id", "symbol", "side", "lots", "status", "at", "ticket", "lots_filled", "retcode", "resolved", "error")
        recent = sorted(self.state["intents"].values(), key=lambda i: i["at"], reverse=True)[:8]
        return [{k: v for k, v in i.items() if k in keep} for i in recent]

    def cycle(self) -> dict[str, Any]:
        status = self.client.get("/api/status")
        if status.get("mode") != "SIMULATION" or not status.get("live_locked", False):
            raise AgentHalt(f"server not in SIMULATION with LIVE locked: {status.get('mode')}")
        if (self.workdir / "STOP").exists():
            self.note("halted", reason="fichier STOP présent : aucun ordre")
            return {"halted": True, "actions": []}
        wanted = targets(self.client.get("/api/learn/overview"))
        self.targets = wanted
        actions: list[dict[str, Any]] = []
        with self.trader as trader:
            account = trader.refresh_account()
            positions = trader.positions()
            for settled in self.resolve_intents(positions):
                actions.append({"kind": "intent_resolved", "symbol": settled["symbol"], "side": settled["side"],
                                "reason": f"{settled['status']} : {settled['resolved']}"})
            for symbol, target in sorted(wanted.items()):
                mine = [p for p in positions if p.astra and p.symbol == symbol]
                for p in mine:
                    if target["side"] != p.side:
                        report = trader.close(p)
                        actions.append({"kind": "close", "symbol": symbol, "ticket": p.ticket, "side": p.side,
                                        "volume": p.volume, "profit": p.profit, "reason": target["reason"],
                                        "price": report["price"]})
                if target["side"] == "FLAT":
                    self.state["mirrored"].pop(symbol, None)
                    self.state["last_refusal"].pop(symbol, None)
                    continue
                if any(p.side == target["side"] for p in mine):
                    continue
                if self.state["mirrored"].get(symbol) == target["entry_ts"]:
                    continue  # already mirrored once for this paper entry: stopped out on MT5, not reopened
                if self.pending_intent(symbol) is not None:
                    continue  # an earlier order on this pair has no known outcome yet: look first, never send again
                state = self.account_state(trader.refresh_account(), trader.positions())
                limits = self.limits
                risk_pct = limits.risk_per_trade_pct if isinstance(limits, RiskLimits) else 0.0
                risk_usd = state.equity * risk_pct / 100
                entry = trader.prepare_entry(symbol, target["side"], target["stop_distance"], risk_usd)
                age = None
                if entry["tick_time"]:
                    age = (self.clock() - datetime.fromisoformat(entry["tick_time"])).total_seconds()
                order = OrderRequest(symbol, target["side"], entry["lots"], entry["loss_at_stop_usd"], entry["required_margin"],
                                     entry["bid"], entry["ask"], age, entry["volume_min"], entry["volume_max"],
                                     entry["volume_step"], entry["has_stop"])
                decision = evaluate_entry(state, order, limits)
                if not decision.allowed:
                    key = f"{target['entry_ts']}|{'|'.join(decision.reasons)}"
                    if self.state["last_refusal"].get(symbol) != key:  # one line per change, not one per cycle
                        self.state["last_refusal"][symbol] = key
                        actions.append({"kind": "refused", "symbol": symbol, "side": target["side"],
                                        "lots": entry["lots"], "reason": "; ".join(decision.reasons)[:190]})
                    continue
                intent = self._intent(symbol, target["side"], entry["lots"], entry["sl"], target["entry_ts"])
                try:
                    report = trader.open(symbol, target["side"], entry["lots"], entry["sl"], comment=f"{COMMENT}:{intent}")
                except Mt5OrderRejected as exc:
                    self._set_intent(intent, "REJETE", retcode=exc.retcode, error=str(exc)[:160])
                    key = f"{target['entry_ts']}|rejet {exc.retcode}"
                    if self.state["last_refusal"].get(symbol) != key:
                        self.state["last_refusal"][symbol] = key
                        actions.append({"kind": "refused", "symbol": symbol, "side": target["side"], "lots": entry["lots"],
                                        "reason": f"refus du courtier : {exc}"[:190]})
                    continue
                except Mt5OrderUnknown as exc:
                    self._set_intent(intent, "INCONNU", error=str(exc)[:160])
                    actions.append({"kind": "order_unknown", "symbol": symbol, "side": target["side"], "lots": entry["lots"],
                                    "reason": f"{exc} : vérification du compte avant tout nouvel essai"[:190]})
                    continue
                filled = report["volume"]
                self._set_intent(intent, "PARTIEL" if report.get("partial") else "EXECUTE", ticket=report["order"],
                                 lots_filled=filled, retcode=report["retcode"])
                self.state["mirrored"][symbol] = target["entry_ts"]
                self.state["last_refusal"].pop(symbol, None)
                pips = pip_size(symbol)
                sign = 1 if target["side"] == "LONG" else -1
                actions.append({"kind": "open", "symbol": symbol, "side": target["side"], "lots": filled,
                                "sl": entry["sl"], "price": report["price"], "order": report["order"],
                                "status": "PARTIEL" if report.get("partial") else "EXECUTE",
                                "risk_usd": round(risk_usd, 2), "reason": target["reason"],
                                "requested_price": report["requested_price"], "latency_ms": report["latency_ms"],
                                "spread_pips": round((entry["ask"] - entry["bid"]) / pips, 2),
                                "slippage_pips": round(sign * (report["price"] - report["requested_price"]) / pips, 2) + 0.0})
            everything = trader.positions()
            account = trader.refresh_account()
            manual_positions = [asdict(p) for p in everything if not p.astra]
            summary = {"account": {k: account[k] for k in ("login", "server", "environment", "equity")},
                       "actions": actions, "astra_positions": [asdict(p) for p in trader.positions() if p.astra]}
        for action in actions:
            self.note(action["kind"], **{k: v for k, v in action.items() if k != "kind"})
        self.note("cycle", actions=len(actions))
        self.save()
        self.publish(account, summary["astra_positions"] + manual_positions, actions, everything)
        return summary

    def publish(self, account: dict[str, Any], positions: list[dict[str, Any]], actions: list[dict[str, Any]],
                objects: list[Any]) -> None:
        """Show the DEMO account, the risk gateway and the reconciliation on the dashboard. A failure here never
        stops trading: it is journaled."""

        state = self.account_state(account, objects)
        body = {"account": account, "positions": positions, "actions": actions, "risk": self.risk_panel(state),
                "reconciliation": self.reconcile(objects),
                "bridge": {"last_error": self.last_error, "history_synced_at": self.history_synced_at,
                           "stop_file": (self.workdir / "STOP").exists(), "intents": self.intents_public()}}
        try:
            self.client.post("/api/mt5/snapshot", body)
        except (ApiError, OSError) as exc:
            self.note("publish_failed", reason=str(exc)[:200])

    def stream(self) -> dict[str, int]:
        """Continuous flow: quotes, account figures and open positions, without trading."""

        quotes = self.trader.quotes()
        if quotes:
            self.client.post("/api/mt5/quotes", {"quotes": quotes})
        account = self.trader.refresh_account()
        objects = self.trader.positions()
        positions = [asdict(p) for p in objects]
        self.publish(account, positions, [], objects)
        return {"quotes": len(quotes), "positions": len(positions)}

    def sync_history(self, days: int) -> dict[str, int]:
        """Send the broker's deal and order history. Idempotent on the server (keyed by the broker's tickets)."""

        sent = post_history(self.client, self.trader.history(days), self.trader.account["server"])
        self.history_synced_at = self.clock().isoformat()
        return sent

    def push_candles(self, timeframes: tuple[str, ...], recent: bool) -> int:
        """Closed candles of every pair to the server (idempotent: stored bars are kept, new ones added)."""

        sent = 0
        server = self.trader.account["server"]
        for timeframe in timeframes:
            count = RECENT_BARS[timeframe] if recent else BACKFILL_BARS[timeframe]
            for symbol in mt5_demo.PAIRS:
                bars = self.trader.candles(symbol, timeframe, count)
                if not bars:
                    continue
                # The proxy refuses bodies above 64 KB on API routes: send small batches.
                for start in range(0, len(bars), CANDLES_PER_POST):
                    self.client.post("/api/mt5/candles", {
                        "server": server, "symbol": symbol, "timeframe": timeframe,
                        "candles": [{"ts": c.timestamp.isoformat(), "open": c.open, "high": c.high, "low": c.low,
                                     "close": c.close, "volume": c.volume} for c in bars[start:start + CANDLES_PER_POST]]})
                sent += len(bars)
        return sent

    def flow(self, cycle_seconds: int, now: Callable[[], float], sleep: Callable[[float], None],
             stream_seconds: int = STREAM_SECONDS, rounds: int | None = None) -> None:
        """One connection to the terminal, kept open: the stream every few seconds, the trading cycle every
        ``cycle_seconds``, candles every minute (fast timeframes) and ten minutes (slow ones)."""

        next_cycle = next_fast = next_slow = next_history = 0.0
        first = True
        with self.trader:
            done = 0
            while rounds is None or done < rounds:
                started = now()
                if first:
                    self.note("backfill", candles=self.push_candles(tuple(BACKFILL_BARS), recent=False))
                    first = False
                    next_history = started  # the full history first, then only the recent days
                    next_fast, next_slow = started + FAST_CANDLES_SECONDS, started + SLOW_CANDLES_SECONDS
                if started >= next_cycle:
                    summary = self.cycle()
                    print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
                    next_cycle = started + cycle_seconds
                if started >= next_history:
                    days = HISTORY_DAYS_FIRST if self.history_synced_at is None else HISTORY_DAYS_RECENT
                    try:
                        self.note("history", **self.sync_history(days))
                    except (ApiError, OSError) as exc:
                        self.note("history_failed", reason=str(exc)[:160])
                    next_history = started + HISTORY_SECONDS
                if started >= next_fast:
                    self.push_candles(("M5", "M15"), recent=True)
                    next_fast = started + FAST_CANDLES_SECONDS
                if started >= next_slow:
                    self.push_candles(("H1", "H4", "D1"), recent=True)
                    next_slow = started + SLOW_CANDLES_SECONDS
                self.stream()
                done += 1
                sleep(max(0.0, stream_seconds - (now() - started)))

    def run(self, interval: int, once: bool = False, sleep: Callable[[float], None] = time.sleep) -> int:
        if once:
            try:
                print(json.dumps(self.cycle(), ensure_ascii=False, default=str), flush=True)
                return 0
            except AgentHalt as exc:
                self.note("halt", reason=str(exc))
                print(f"astra-mt5-bridge: arrêt : {exc}", file=sys.stderr)
                return 2
            except (Mt5DemoError, OSError) as exc:
                self.note("error", reason=str(exc))
                print(f"astra-mt5-bridge: {exc}", file=sys.stderr, flush=True)
                return 0
        while True:
            try:
                self.flow(interval, time.monotonic, sleep)
            except AgentHalt as exc:
                self.note("halt", reason=str(exc))
                print(f"astra-mt5-bridge: arrêt : {exc}", file=sys.stderr)
                return 2
            except (Mt5DemoError, ApiError, OSError) as exc:
                self.note("error", reason=str(exc))
                print(f"astra-mt5-bridge: {exc} : nouvelle tentative dans 30 s", file=sys.stderr, flush=True)
                sleep(30)  # terminal restarting or server unreachable: reconnect, never stop for good


def _native(path: str) -> str:
    """A Linux path seen from Windows Python under Wine (systemd credentials live in /run/credentials)."""

    return "Z:" + path.replace("/", "\\") if os.name == "nt" and path.startswith("/") else path


def run_from_env(once: bool = False) -> int:
    url = os.environ.get("ASTRA_MT5_URL", "").strip()
    token_file = os.environ.get("ASTRA_MT5_TOKEN_FILE", "").strip()
    credentials_dir = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()  # systemd LoadCredential (server)
    if credentials_dir and not token_file:
        token_file = f"{credentials_dir}/mt5-bridge-token"
    login = os.environ.get("ASTRA_MT5_LOGIN", "").strip()
    if not (url and token_file and login.isdigit()):
        print("astra-mt5-bridge: ASTRA_MT5_URL, ASTRA_MT5_TOKEN_FILE et ASTRA_MT5_LOGIN sont obligatoires", file=sys.stderr)
        return 2
    token = Path(_native(token_file)).read_text(encoding="utf-8").strip()
    client = AstraClient(http_transport(url, token))
    credentials = None
    password_file = os.environ.get("ASTRA_MT5_PASSWORD_FILE", "").strip()
    if credentials_dir and not password_file:
        password_file = f"{credentials_dir}/mt5-password"
    if password_file:  # unattended terminal (server): sign in with the deposited password
        password = Path(_native(password_file)).read_text(encoding="utf-8")  # written without a newline
        credentials = (int(login), password, os.environ.get("ASTRA_MT5_SERVER") or "MetaQuotes-Demo")
    trader = Mt5DemoTrader(os.environ.get("ASTRA_MT5_TERMINAL") or DEFAULT_TERMINAL, expected_login=int(login),
                           credentials=credentials)
    workdir = Path(_native(os.environ.get("ASTRA_MT5_DIR") or "var/mt5-bridge"))
    risk_file = os.environ.get("ASTRA_MT5_RISK_FILE", "").strip()
    try:
        limits: RiskLimits | RiskConfigError = load_limits(_native(risk_file) if risk_file else None)
    except RiskConfigError as exc:
        limits = exc  # an invalid profile refuses every entry; exits and the dashboard keep working
    interval = max(60, int(os.environ.get("ASTRA_MT5_INTERVAL_SECONDS") or DEFAULT_INTERVAL_SECONDS))
    return Bridge(client, trader, workdir, limits=limits).run(interval, once=once)
