"""Emergency stop of the MT5 DEMO account: a separate process (master document, section VIII).

``declare`` writes two files in the bridge's work directory: ``STOP`` (the bridge sends no new order) and ``EMERGENCY``
(the bridge closes every ASTRA position at its next cycle; a "resume" from the site never lifts it, only a person
removing the file does). ``run`` declares, then closes every ASTRA position itself through the terminal. Positions
opened by hand are never touched. A close that fails is reported; the files stay.

``trigger`` says when the stop is due: equity ``emergency_drawdown_pct`` below its peak, or figures that are not finite.

``main --watch`` runs apart from the bridge, every 30 s. It reads the equity from the terminal when it can attach to
it; the terminal often answers only one process (the bridge), so otherwise it uses the equity the bridge recorded in
its state file within the last ``FRESH_SECONDS``. When the stop is due and it cannot reach the terminal, it declares
the emergency and the bridge does the closing. With no fresh figure at all it writes a journal line and does nothing:
a bridge that is down cannot close anything either, and every position keeps its stop on the broker's server.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from .brokers.mt5_demo import DEFAULT_TERMINAL, Mt5DemoError
from .brokers.mt5_demo_orders import MAGIC, MAGIC_MANUAL, Mt5DemoTrader
from .mt5_risk import AccountState, RiskConfigError, RiskLimits, load_required_limits

WATCH_SECONDS = 30
FRESH_SECONDS = 180


def _native(path: str) -> str:
    """A Linux path seen from Windows Python under Wine (same rule as the bridge)."""

    return "Z:" + path.replace("/", "\\") if os.name == "nt" and path.startswith("/") else path


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trigger(account: AccountState, limits: RiskLimits) -> str | None:
    """The reason to stop everything now, or None. Figures that are not finite or not positive stop everything."""

    if not (_finite(account.peak_equity) and _finite(account.equity) and account.peak_equity > 0 and account.equity > 0):
        return "sommet des fonds propres ou fonds propres illisibles"
    drawdown = (account.peak_equity - account.equity) / account.peak_equity * 100
    if drawdown >= limits.emergency_drawdown_pct:
        return f"drawdown {drawdown:.2f} % depuis le sommet (seuil {limits.emergency_drawdown_pct} %)"
    return None


def _journal(workdir: Path, entry: dict[str, Any]) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    with (workdir / "emergency.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def declare(workdir: Path, reason: str) -> None:
    """STOP and EMERGENCY files: no new order, and the bridge closes every ASTRA position."""

    workdir.mkdir(parents=True, exist_ok=True)
    line = f"arrêt d'urgence {datetime.now(UTC).isoformat()} : {reason}\n"
    for name in ("STOP", "EMERGENCY"):
        with (workdir / name).open("a", encoding="utf-8") as out:
            out.write(line)


def run(trader: Mt5DemoTrader, workdir: Path, reason: str) -> dict[str, Any]:
    """Declare, then close every ASTRA position. Idempotent: a second run closes what is left."""

    declare(workdir, reason)
    closed, failed, kept = [], [], []
    for position in trader.positions():
        if position.magic not in (MAGIC, MAGIC_MANUAL):
            kept.append(position.ticket)
            continue
        try:
            trader.close(position)
            closed.append(position.ticket)
        except Mt5DemoError as exc:  # rejected, unknown outcome, terminal changed: reported, the files stay
            failed.append({"ticket": position.ticket, "symbol": position.symbol, "reason": str(exc)[:160]})
    report = {"ts": datetime.now(UTC).isoformat(), "reason": reason, "closed": closed, "failed": failed,
              "untouched_manual": kept, "stop_file": str(workdir / "STOP")}
    _journal(workdir, report)
    return report


def _state(workdir: Path) -> dict[str, Any]:
    try:
        return json.loads((workdir / "bridge-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _recorded_equity(state: dict[str, Any]) -> float | None:
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(state["last_equity_at"])).total_seconds()
        equity = float(state["last_equity"])
    except (KeyError, TypeError, ValueError):
        return None
    return equity if 0 <= age <= FRESH_SECONDS else None


def watch_once(trader: Mt5DemoTrader, workdir: Path, limits: RiskLimits | None) -> None:
    state = _state(workdir)
    problem = None
    try:
        with trader:
            equity = trader.refresh_account()["equity"]
            peak = max(float(state.get("peak_equity") or 0.0), equity)
            reason = "fichier de limites illisible" if limits is None else trigger(
                AccountState(equity=equity, peak_equity=peak), limits)
            if reason and (not (workdir / "EMERGENCY").exists()
                           or any(p.magic in (MAGIC, MAGIC_MANUAL) for p in trader.positions())):
                run(trader, workdir, reason)
            return
    except Exception as exc:  # noqa: BLE001 - the watcher must outlive any error; each one is written down
        problem = f"{type(exc).__name__}: {exc}"[:160]
    equity = _recorded_equity(state)
    entry: dict[str, Any] = {"ts": datetime.now(UTC).isoformat(), "watch": "terminal inaccessible", "reason": problem}
    if equity is None:
        entry["decision"] = "aucun chiffre récent du pont : rien à décider"
    else:
        peak = max(float(state.get("peak_equity") or 0.0), equity)
        reason = "fichier de limites illisible" if limits is None else trigger(
            AccountState(equity=equity, peak_equity=peak), limits)
        if reason and not (workdir / "EMERGENCY").exists():
            declare(workdir, reason)
            entry["decision"] = f"arrêt d'urgence déclaré, fermeture confiée au pont : {reason}"
        else:
            entry["decision"] = "fonds propres enregistrés par le pont dans les limites" if not reason else "déjà déclaré"
    _journal(workdir, entry)


def main(argv: list[str] | None = None, module: ModuleType | None = None, login: int | None = None,
         sleep: Any = time.sleep, rounds: int | None = None, limits: RiskLimits | None = None) -> int:
    parser = argparse.ArgumentParser(prog="astra mt5 emergency")
    parser.add_argument("--workdir", default=os.environ.get("ASTRA_MT5_DIR", ""))
    parser.add_argument("--reason", default="")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args(argv)
    if not args.workdir:
        print("astra mt5 emergency : --workdir ou ASTRA_MT5_DIR obligatoire", file=sys.stderr)
        return 2
    workdir = Path(_native(args.workdir))
    login = login if login is not None else int(os.environ.get("ASTRA_MT5_LOGIN", "0") or 0)
    if not login:
        print("astra mt5 emergency : ASTRA_MT5_LOGIN obligatoire", file=sys.stderr)
        return 2
    trader = Mt5DemoTrader(os.environ.get("ASTRA_MT5_TERMINAL", DEFAULT_TERMINAL), module=module, expected_login=login)
    if not args.watch:
        try:
            with trader:
                report = run(trader, workdir, args.reason or "arrêt manuel")
        except Mt5DemoError as exc:  # no terminal: the bridge closes at its next cycle
            declare(workdir, args.reason or "arrêt manuel")
            report = {"declared": True, "closed": [], "failed": [], "reason": str(exc)[:160]}
            _journal(workdir, report)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if not report["failed"] else 1
    if limits is None:
        try:
            limits = load_required_limits(_native(os.environ.get("ASTRA_MT5_RISK_FILE", "").strip()) or None)
        except RiskConfigError as exc:
            _journal(workdir, {"ts": datetime.now(UTC).isoformat(), "watch": "limites illisibles", "reason": str(exc)})
    done = 0
    while rounds is None or done < rounds:
        watch_once(trader, workdir, limits)
        done += 1
        if rounds is None or done < rounds:
            sleep(WATCH_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
