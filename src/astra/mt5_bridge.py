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
from .brokers.mt5_demo_orders import Mt5DemoTrader

RISK_PCT = 0.5
DAILY_LOSS_PCT = 3.0
MAX_POSITIONS = 7
DEFAULT_INTERVAL_SECONDS = 300
STREAM_SECONDS = 5          # quotes, balance and open positions reach the dashboard this often
FAST_CANDLES_SECONDS = 60   # M5 and M15: the last bars, every minute
SLOW_CANDLES_SECONDS = 600  # H1, H4 and D1: the last bars, every ten minutes
BACKFILL_BARS = {"M5": 1500, "M15": 1500, "H1": 1500, "H4": 1500, "D1": 300}
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


class Bridge:
    def __init__(self, client: AstraClient, trader: Mt5DemoTrader, workdir: Path,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.client, self.trader, self.workdir, self.clock = client, trader, workdir, clock
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.state_path = workdir / "bridge-state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        self.state.setdefault("mirrored", {})

    def note(self, kind: str, **fields: Any) -> None:
        now = self.clock()
        line = json.dumps({"ts": now.isoformat(), "kind": kind, **fields}, ensure_ascii=False, default=str)
        with (self.workdir / f"journal-{now.date().isoformat()}.jsonl").open("a", encoding="utf-8") as out:
            out.write(line + "\n")

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def cycle(self) -> dict[str, Any]:
        status = self.client.get("/api/status")
        if status.get("mode") != "SIMULATION" or not status.get("live_locked", False):
            raise AgentHalt(f"server not in SIMULATION with LIVE locked: {status.get('mode')}")
        if (self.workdir / "STOP").exists():
            self.note("halted", reason="fichier STOP présent : aucun ordre")
            return {"halted": True, "actions": []}
        wanted = targets(self.client.get("/api/learn/overview"))
        actions: list[dict[str, Any]] = []
        with self.trader as trader:
            account = trader.refresh_account()
            today = self.clock().date().isoformat()
            if self.state.get("day") != today:
                self.state["day"], self.state["day_start_equity"] = today, account["equity"]
            loss_pct = (self.state["day_start_equity"] - account["equity"]) / self.state["day_start_equity"] * 100
            entries_allowed = loss_pct < DAILY_LOSS_PCT
            if not entries_allowed:
                self.note("entries_blocked", reason=f"perte du jour {loss_pct:.2f} % ≥ {DAILY_LOSS_PCT} %")
            positions = trader.positions()
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
                    continue
                if any(p.side == target["side"] for p in mine):
                    continue
                if self.state["mirrored"].get(symbol) == target["entry_ts"]:
                    continue  # already mirrored once for this paper entry: stopped out on MT5, not reopened
                held = sum(1 for p in trader.positions() if p.astra)
                if not entries_allowed or held >= MAX_POSITIONS:
                    actions.append({"kind": "entry_skipped", "symbol": symbol, "side": target["side"],
                                    "reason": "limite journalière" if not entries_allowed else "positions maximales"})
                    continue
                risk_usd = account["equity"] * RISK_PCT / 100
                lots, sl = trader.lots_for_risk(symbol, target["side"], target["stop_distance"], risk_usd)
                if lots <= 0:
                    actions.append({"kind": "entry_skipped", "symbol": symbol, "side": target["side"],
                                    "reason": f"risque {risk_usd:.2f} USD inférieur au volume minimal"})
                    continue
                report = trader.open(symbol, target["side"], lots, sl)
                self.state["mirrored"][symbol] = target["entry_ts"]
                actions.append({"kind": "open", "symbol": symbol, "side": target["side"], "lots": lots, "sl": sl,
                                "price": report["price"], "order": report["order"], "risk_usd": round(risk_usd, 2),
                                "reason": target["reason"]})
            everything = trader.positions()
            account = trader.refresh_account()
            manual_positions = [asdict(p) for p in everything if not p.astra]
            summary = {"account": {k: account[k] for k in ("login", "server", "environment", "equity")},
                       "actions": actions, "astra_positions": [asdict(p) for p in trader.positions() if p.astra]}
        for action in actions:
            self.note(action["kind"], **{k: v for k, v in action.items() if k != "kind"})
        self.note("cycle", actions=len(actions))
        self.save()
        self.publish(account, summary["astra_positions"] + manual_positions, actions)
        return summary

    def publish(self, account: dict[str, Any], positions: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
        """Show the DEMO account on the dashboard. A failure here never stops trading: it is journaled."""

        try:
            self.client.post("/api/mt5/snapshot", {"account": account, "positions": positions, "actions": actions})
        except (ApiError, OSError) as exc:
            self.note("publish_failed", reason=str(exc)[:200])

    def stream(self) -> dict[str, int]:
        """Continuous flow: quotes, account figures and open positions, without trading."""

        quotes = self.trader.quotes()
        if quotes:
            self.client.post("/api/mt5/quotes", {"quotes": quotes})
        account = self.trader.refresh_account()
        positions = [asdict(p) for p in self.trader.positions()]
        self.publish(account, positions, [])
        return {"quotes": len(quotes), "positions": len(positions)}

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
                self.client.post("/api/mt5/candles", {
                    "server": server, "symbol": symbol, "timeframe": timeframe,
                    "candles": [{"ts": c.timestamp.isoformat(), "open": c.open, "high": c.high, "low": c.low,
                                 "close": c.close, "volume": c.volume} for c in bars]})
                sent += len(bars)
        return sent

    def flow(self, cycle_seconds: int, now: Callable[[], float], sleep: Callable[[float], None],
             stream_seconds: int = STREAM_SECONDS, rounds: int | None = None) -> None:
        """One connection to the terminal, kept open: the stream every few seconds, the trading cycle every
        ``cycle_seconds``, candles every minute (fast timeframes) and ten minutes (slow ones)."""

        next_cycle = next_fast = next_slow = 0.0
        first = True
        with self.trader:
            done = 0
            while rounds is None or done < rounds:
                started = now()
                if first:
                    self.note("backfill", candles=self.push_candles(tuple(BACKFILL_BARS), recent=False))
                    first = False
                    next_fast, next_slow = started + FAST_CANDLES_SECONDS, started + SLOW_CANDLES_SECONDS
                if started >= next_cycle:
                    summary = self.cycle()
                    print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
                    next_cycle = started + cycle_seconds
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
    interval = max(60, int(os.environ.get("ASTRA_MT5_INTERVAL_SECONDS") or DEFAULT_INTERVAL_SECONDS))
    return Bridge(client, trader, workdir).run(interval, once=once)
