"""Emergency stop of the MT5 DEMO account: a separate process (master document, section VIII).

``run`` first writes the ``STOP`` file in the bridge's work directory (the bridge then sends no order at all), then
closes every position ASTRA opened (automatic and site orders). Positions opened by hand are never touched. A close
that fails is reported and the STOP file stays: only a person removes it (``astra mt5 emergency`` never does).

``trigger`` says when the stop is due on its own: equity ``emergency_drawdown_pct`` below its peak, or a peak that
cannot be read.

``main`` runs alone: it attaches to the terminal already signed in (no password), reads the peak kept by the bridge,
and either stops at once (``--reason``) or watches (``--watch``). It works when the bridge is down.
"""

from __future__ import annotations

import argparse
import json
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


def _native(path: str) -> str:
    """A Linux path seen from Windows Python under Wine (same rule as the bridge)."""

    return "Z:" + path.replace("/", "\\") if os.name == "nt" and path.startswith("/") else path


def trigger(account: AccountState, limits: RiskLimits) -> str | None:
    """The reason to stop everything now, or None."""

    if not account.peak_equity or not account.equity or account.peak_equity <= 0:
        return "sommet des fonds propres ou fonds propres illisibles"
    drawdown = (account.peak_equity - account.equity) / account.peak_equity * 100
    if drawdown >= limits.emergency_drawdown_pct:
        return f"drawdown {drawdown:.2f} % depuis le sommet (seuil {limits.emergency_drawdown_pct} %)"
    return None


def _journal(workdir: Path, entry: dict[str, Any]) -> None:
    with (workdir / "emergency.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def run(trader: Mt5DemoTrader, workdir: Path, reason: str) -> dict[str, Any]:
    """STOP first, then close every ASTRA position. Idempotent: a second run closes what is left."""

    workdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    stop = workdir / "STOP"
    with stop.open("a", encoding="utf-8") as out:
        out.write(f"arrêt d'urgence {now} : {reason}\n")
    closed, failed, kept = [], [], []
    for position in trader.positions():
        if position.magic not in (MAGIC, MAGIC_MANUAL):
            kept.append(position.ticket)
            continue
        try:
            trader.close(position)
            closed.append(position.ticket)
        except Mt5DemoError as exc:  # rejected, unknown outcome, terminal changed: reported, STOP stays
            failed.append({"ticket": position.ticket, "symbol": position.symbol, "reason": str(exc)[:160]})
    report = {"ts": now, "reason": reason, "closed": closed, "failed": failed, "untouched_manual": kept,
              "stop_file": str(stop)}
    _journal(workdir, report)
    return report


def _state(workdir: Path) -> dict[str, Any]:
    try:
        return json.loads((workdir / "bridge-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv: list[str] | None = None, module: ModuleType | None = None, login: int | None = None,
         sleep: Any = time.sleep, rounds: int | None = None) -> int:
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
        with trader:
            report = run(trader, workdir, args.reason or "arrêt manuel")
        print(json.dumps(report, ensure_ascii=False))
        return 0 if not report["failed"] else 1
    try:
        limits: RiskLimits | None = load_required_limits(
            _native(os.environ.get("ASTRA_MT5_RISK_FILE", "").strip()) or None)
    except RiskConfigError as exc:
        limits = None
        _journal(workdir, {"ts": datetime.now(UTC).isoformat(), "watch": "limites illisibles", "reason": str(exc)})
    done = 0
    while rounds is None or done < rounds:
        try:
            with trader:
                equity = trader.refresh_account()["equity"]
                state = _state(workdir)
                peak = max(float(state.get("peak_equity") or 0.0), equity)
                reason = "fichier de limites illisible" if limits is None else trigger(
                    AccountState(equity=equity, peak_equity=peak), limits)
                if reason and not (workdir / "STOP").exists():
                    run(trader, workdir, reason)
                elif reason and any(p.magic in (MAGIC, MAGIC_MANUAL) for p in trader.positions()):
                    run(trader, workdir, reason)  # already stopped: close what is still open
        except Mt5DemoError as exc:
            _journal(workdir, {"ts": datetime.now(UTC).isoformat(), "watch": "terminal indisponible",
                               "reason": str(exc)[:160]})
        done += 1
        if rounds is None or done < rounds:
            sleep(WATCH_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
