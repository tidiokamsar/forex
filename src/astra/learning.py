"""Continuous improvement of the strategy parameters, pair by pair: champion and challengers (SIMULATION).

One learning run for a pair (a worker job, at most once a day):

1. Data: the pair's stored candles. The most recent 365 days are a FINAL CHECK window never used to choose.
2. Candidates: the champion plus a deterministic sample of the parameter space (seeded by the run number).
3. Selection, without look-ahead: walk-forward over yearly test windows before the final check; each test year
   is simulated with only earlier candles for the indicators. Score = average yearly net return (costs
   included) minus half the worst drawdown; at least ``MIN_TRADES`` trades over the selection period.
4. Promotion, only if every pre-registered criterion holds:
   - the best challenger's selection score beats the champion's by ``MIN_SCORE_GAIN`` points;
   - on the final check window it is not worse than the champion and its net result is >= 0;
   - with doubled spread and slippage its selection net result stays > 0;
   - its worst drawdown is <= ``MAX_DRAWDOWN_PCT``.
5. Every run, every candidate's metrics and every promotion are stored; a promotion is versioned and can be
   rolled back. The managed paper simulation of the pair then uses the new champion from its next candle.

What this is not: a proof of future profit. Many candidates are tried over time (the count is stored), and the
final check window is reused by later runs; both make an optimistic bias possible. Results stay SIMULATION.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .marketdata import PAIRS, CandleStore
from .papertrade import (
    STRATEGY_NAME,
    CostParams,
    LabStore,
    RiskParams,
    SimulationError,
    StrategyParams,
    _build,
    run_backtest,
    simulation_request,
)

LEARNING_VERSION = "1.0.0"
SOURCE, TIMEFRAME = "ECB_EXR", "D1"
FINAL_CHECK_DAYS = 365
CANDIDATES_PER_RUN = 24  # about eight per strategy family
MIN_TRADES = 20
MIN_SCORE_GAIN = 0.5
MAX_DRAWDOWN_PCT = 15.0
SPACE = {
    "family": ("sma_trend", "momentum", "mean_reversion"),
    "z_entry": (0.75, 1.0, 1.5, 2.0),  # mean_reversion only
    "fast": (5, 10, 15, 20, 30, 40),
    "slow": (20, 30, 50, 80, 120, 160),
    "atr_window": (10, 14, 20),
    "min_atr_pct": (0.0, 0.0001, 0.0003),
    "stop_atr_multiple": (1.5, 2.0, 3.0, 4.0),
}
DEFAULT_RISK = RiskParams()
DEFAULT_COSTS = CostParams()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS learn_champions (
    symbol TEXT NOT NULL, version INTEGER NOT NULL, params_json TEXT NOT NULL, promoted_at TEXT NOT NULL,
    run_id INTEGER, reason TEXT NOT NULL, active INTEGER NOT NULL, PRIMARY KEY (symbol, version)
);
CREATE TABLE IF NOT EXISTS learn_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, data_json TEXT, champion_version INTEGER, decision TEXT, decision_json TEXT,
    candidates_json TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS learn_managed (symbol TEXT PRIMARY KEY, session_id INTEGER NOT NULL, created_at TEXT NOT NULL);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def default_params() -> dict[str, Any]:
    return {"strategy": asdict(StrategyParams()), "stop_atr_multiple": DEFAULT_RISK.stop_atr_multiple}


class LearningStore:
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

    def champion(self, symbol: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM learn_champions WHERE symbol = ? AND active = 1", (symbol,)).fetchone()
            if row is None:
                params = default_params()
                db.execute("INSERT OR IGNORE INTO learn_champions(symbol, version, params_json, promoted_at, run_id, reason,"
                           " active) VALUES (?,?,?,?,?,?,1)",
                           (symbol, 1, json.dumps(params), _iso(_now()), None, "stratégie de référence (non optimisée)"))
                return {"symbol": symbol, "version": 1, "params": params, "reason": "stratégie de référence (non optimisée)"}
            return {"symbol": symbol, "version": row["version"], "params": json.loads(row["params_json"]),
                    "promoted_at": row["promoted_at"], "run_id": row["run_id"], "reason": row["reason"]}

    def promote(self, symbol: str, params: dict[str, Any], run_id: int | None, reason: str) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = (db.execute("SELECT COALESCE(MAX(version), 0) FROM learn_champions WHERE symbol = ?", (symbol,))
                       .fetchone()[0]) + 1
            db.execute("UPDATE learn_champions SET active = 0 WHERE symbol = ?", (symbol,))
            db.execute("INSERT INTO learn_champions(symbol, version, params_json, promoted_at, run_id, reason, active)"
                       " VALUES (?,?,?,?,?,?,1)", (symbol, version, json.dumps(params), _iso(_now()), run_id, reason))
            return version

    def rollback(self, symbol: str) -> int:
        """Reactivate the previous champion version (a new version row, so the history stays append-only)."""

        with self._connect() as db:
            rows = db.execute("SELECT * FROM learn_champions WHERE symbol = ? ORDER BY version DESC", (symbol,)).fetchall()
        if len(rows) < 2:
            raise SimulationError("aucune version précédente à rétablir")
        previous = rows[1]
        return self.promote(symbol, json.loads(previous["params_json"]), None,
                            f"retour arrière vers la version {previous['version']}")

    def history(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            if symbol:
                rows = db.execute("SELECT * FROM learn_champions WHERE symbol = ? ORDER BY promoted_at DESC LIMIT ?",
                                  (symbol, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM learn_champions ORDER BY promoted_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**{k: r[k] for k in ("symbol", "version", "promoted_at", "run_id", "reason", "active")},
                 "params": json.loads(r["params_json"])} for r in rows]

    def start_run(self, symbol: str) -> int:
        with self._connect() as db:
            return int(db.execute("INSERT INTO learn_runs(symbol, started_at, status) VALUES (?,?,?)",
                                  (symbol, _iso(_now()), "RUNNING")).lastrowid)

    def finish_run(self, run_id: int, **values: Any) -> None:
        columns = {k: (json.dumps(v, default=str) if k.endswith("_json") else v) for k, v in values.items()}
        assignments = ", ".join(f"{k} = ?" for k in columns)
        with self._connect() as db:
            db.execute(f"UPDATE learn_runs SET {assignments}, finished_at = ? WHERE id = ?",
                       (*columns.values(), _iso(_now()), run_id))

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM learn_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["data"] = json.loads(item.pop("data_json") or "null")
            item["evidence"] = json.loads(item.pop("decision_json") or "null")  # "decision" stays KEPT / PROMOTED
            item["candidates"] = json.loads(item.pop("candidates_json") or "null")
            out.append(item)
        return out

    def last_run_at(self, symbol: str) -> datetime | None:
        with self._connect() as db:
            row = db.execute("SELECT MAX(started_at) FROM learn_runs WHERE symbol = ?", (symbol,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def trials(self, symbol: str) -> int:
        with self._connect() as db:
            rows = db.execute("SELECT candidates_json FROM learn_runs WHERE symbol = ? AND status = 'DONE'", (symbol,)).fetchall()
        return sum(len(json.loads(r[0] or "[]")) for r in rows)

    def managed_session(self, symbol: str) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT session_id FROM learn_managed WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else None

    def set_managed_session(self, symbol: str, session_id: int) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO learn_managed(symbol, session_id, created_at) VALUES (?,?,?) ON CONFLICT(symbol)"
                       " DO UPDATE SET session_id = excluded.session_id", (symbol, session_id, _iso(_now())))


# -- evaluation -----------------------------------------------------------------------------------

def _request(symbol: str, params: dict[str, Any], start: datetime, end: datetime, cost_factor: float = 1.0) -> dict[str, Any]:
    risk = replace(DEFAULT_RISK, stop_atr_multiple=float(params["stop_atr_multiple"]))
    costs = replace(DEFAULT_COSTS, spread_pips=DEFAULT_COSTS.spread_pips * cost_factor,
                    slippage_pips=DEFAULT_COSTS.slippage_pips * cost_factor)
    strategy = _build(StrategyParams, params["strategy"])
    return {"symbol": symbol, "timeframe": TIMEFRAME, "source": SOURCE, "start": _iso(start), "end": _iso(end),
            "strategy": STRATEGY_NAME, "strategy_params": asdict(strategy), "risk": asdict(risk), "costs": asdict(costs)}


def evaluate(candles: CandleStore, symbol: str, params: dict[str, Any], windows: list[tuple[datetime, datetime]],
             cost_factor: float = 1.0, purpose: str = "selection") -> dict[str, Any]:
    """Out-of-sample metrics over the given windows (each simulated with earlier candles only as warm-up).

    ``purpose`` (selection, final_check, cost_stress) only labels the result."""

    yearly, trades, worst_dd, net = [], 0, 0.0, 0.0
    for start, end in windows:
        try:
            result = run_backtest(candles, _request(symbol, params, start, end, cost_factor))
        except SimulationError:
            continue
        m = result["metrics"]
        yearly.append(m["net_pnl"] / m["initial_capital"] * 100)
        trades += m["trade_count"]
        worst_dd = max(worst_dd, m["max_drawdown_pct"])
        net += m["net_pnl"]
    if not yearly:
        return {"purpose": purpose, "windows": 0, "score": None, "trades": 0}
    mean = sum(yearly) / len(yearly)
    return {"purpose": purpose, "windows": len(yearly), "mean_return_pct": round(mean, 3),
            "worst_drawdown_pct": round(worst_dd, 3),
            "trades": trades, "net_pnl": round(net, 2), "positive_windows": sum(1 for y in yearly if y > 0),
            "score": round(mean - 0.5 * worst_dd, 3) if trades >= MIN_TRADES else None,
            "yearly_returns_pct": [round(y, 3) for y in yearly]}


def candidates(seed: int, champion: dict[str, Any], count: int = CANDIDATES_PER_RUN) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    found: list[dict[str, Any]] = []
    # Champions stored before the strategy families have no family field: compare them with every field filled.
    normalized = {"strategy": asdict(_build(StrategyParams, champion["strategy"])),
                  "stop_atr_multiple": champion["stop_atr_multiple"]}
    seen = {json.dumps(normalized, sort_keys=True)}
    attempts = 0
    while len(found) < count and attempts < 1000:
        attempts += 1
        fast, slow = rng.choice(SPACE["fast"]), rng.choice(SPACE["slow"])
        if fast >= slow:
            continue
        family = rng.choice(SPACE["family"])
        strategy = {"fast": fast, "slow": slow, "atr_window": rng.choice(SPACE["atr_window"]),
                    "min_atr_pct": rng.choice(SPACE["min_atr_pct"]), "max_atr_pct": 0.03, "family": family,
                    "z_entry": rng.choice(SPACE["z_entry"]) if family == "mean_reversion" else 1.0}
        params = {"strategy": strategy, "stop_atr_multiple": rng.choice(SPACE["stop_atr_multiple"])}
        key = json.dumps(params, sort_keys=True)
        if key not in seen:
            seen.add(key)
            found.append(params)
    return found


def windows_for(first: datetime, last: datetime) -> tuple[list[tuple[datetime, datetime]], tuple[datetime, datetime]]:
    """Yearly selection windows (after three years of history) and the final check window (last 365 days)."""

    check_start = last - timedelta(days=FINAL_CHECK_DAYS)
    selection, start = [], datetime(first.year + 3, 1, 1, tzinfo=UTC)
    while start + timedelta(days=365) <= check_start:
        selection.append((start, start + timedelta(days=365) - timedelta(seconds=1)))
        start += timedelta(days=365)
    return selection, (check_start, last)


def learn(candles: CandleStore, store: LearningStore, symbol: str) -> dict[str, Any]:
    """One learning run for a pair; returns the decision (promoted or kept) with its evidence."""

    if symbol not in PAIRS:
        raise SimulationError(f"paire inconnue {symbol}")
    run_id = store.start_run(symbol)
    try:
        series = candles.candles(symbol, TIMEFRAME, SOURCE)
        if len(series) < 4 * 260:
            raise SimulationError(f"historique trop court pour apprendre ({len(series)} bougies D1)")
        selection, check = windows_for(series[0].timestamp, series[-1].timestamp)
        champion = store.champion(symbol)
        pool = candidates(run_id * 7919 + len(series), champion["params"])
        results = []
        for label, params in [("champion", champion["params"]), *[(f"candidat {i + 1}", p) for i, p in enumerate(pool)]]:
            results.append({"label": label, "params": params, "selection": evaluate(candles, symbol, params, selection)})
        champ = results[0]
        scored = [r for r in results[1:] if r["selection"]["score"] is not None]
        best = max(scored, key=lambda r: r["selection"]["score"], default=None)
        decision: dict[str, Any] = {"champion_version": champion["version"], "champion_selection": champ["selection"],
                                    "criteria": {"min_score_gain": MIN_SCORE_GAIN, "max_drawdown_pct": MAX_DRAWDOWN_PCT,
                                                 "min_trades": MIN_TRADES, "final_check_days": FINAL_CHECK_DAYS}}
        verdict, reason = "KEPT", "aucun candidat n'a un score de sélection valable"
        if best is not None:
            decision["best"] = {"label": best["label"], "params": best["params"], "selection": best["selection"]}
            champ_score = champ["selection"]["score"] if champ["selection"]["score"] is not None else -1e9
            gain = best["selection"]["score"] - champ_score
            if gain < MIN_SCORE_GAIN:
                reason = f"meilleur candidat seulement {gain:+.2f} point(s) au-dessus du champion (minimum {MIN_SCORE_GAIN})"
            elif best["selection"]["worst_drawdown_pct"] > MAX_DRAWDOWN_PCT:
                reason = f"drawdown du candidat {best['selection']['worst_drawdown_pct']:.1f} % > {MAX_DRAWDOWN_PCT} %"
            else:
                final_best = evaluate(candles, symbol, best["params"], [check], purpose="final_check")
                final_champ = evaluate(candles, symbol, champion["params"], [check], purpose="final_check")
                stressed = evaluate(candles, symbol, best["params"], selection, cost_factor=2.0, purpose="cost_stress")
                decision.update(final_check_best=final_best, final_check_champion=final_champ, cost_stress=stressed)
                if final_best["windows"] == 0 or final_best["net_pnl"] < 0:
                    reason = "résultat net du candidat négatif sur la période de contrôle finale"
                elif final_champ["windows"] and final_best["net_pnl"] < final_champ["net_pnl"]:
                    reason = "candidat moins bon que le champion sur la période de contrôle finale"
                elif stressed["net_pnl"] <= 0:
                    reason = "résultat net négatif avec des coûts doublés"
                else:
                    verdict = "PROMOTED"
                    reason = (f"{best['label']} : score {best['selection']['score']:+.2f} contre {champ_score:+.2f}, "
                              f"contrôle final {final_best['net_pnl']:+.2f} USD, coûts doublés {stressed['net_pnl']:+.2f} USD")
        decision["reason"] = reason
        decision["trials_so_far"] = store.trials(symbol) + len(results)
        if verdict == "PROMOTED":
            decision["new_version"] = store.promote(symbol, best["params"], run_id, reason)
        data = {"source": SOURCE, "timeframe": TIMEFRAME, "candles": len(series), "first": _iso(series[0].timestamp),
                "last": _iso(series[-1].timestamp), "selection_windows": len(selection),
                "final_check": [_iso(check[0]), _iso(check[1])], "learning_version": LEARNING_VERSION}
        compact = [{"label": r["label"], "params": r["params"], "score": r["selection"]["score"],
                    "mean_return_pct": r["selection"].get("mean_return_pct"), "trades": r["selection"]["trades"],
                    "worst_drawdown_pct": r["selection"].get("worst_drawdown_pct")} for r in results]
        store.finish_run(run_id, status="DONE", data_json=data, champion_version=champion["version"], decision=verdict,
                         decision_json=decision, candidates_json=compact)
        return {"run_id": run_id, "symbol": symbol, "decision": verdict, "reason": reason, "candidates": len(results)}
    except Exception as exc:
        store.finish_run(run_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise


# -- managed paper simulations ---------------------------------------------------------------------

MANAGED_START = datetime(2025, 10, 28, tzinfo=UTC)


def ensure_managed_sessions(candles: CandleStore, store: LearningStore, lab: LabStore) -> list[int]:
    """One continuous paper account per pair, trading the pair's champion; created once, never reset."""

    created = []
    available = {(c["symbol"], c["timeframe"], c["source"]) for c in candles.coverage()}
    for symbol in PAIRS:
        if (symbol, TIMEFRAME, SOURCE) not in available or store.managed_session(symbol) is not None:
            continue
        champion = store.champion(symbol)
        request = simulation_request({"name": f"ASTRA auto · {symbol} (champion)", "symbols": [symbol],
                                      "timeframe": TIMEFRAME, "source": SOURCE, "start": MANAGED_START.isoformat(),
                                      "strategy_params": champion["params"]["strategy"],
                                      "risk": {"stop_atr_multiple": champion["params"]["stop_atr_multiple"],
                                               "max_positions": 1}}, candles)
        session_id = lab.create_session(request, "worker:apprentissage")
        store.set_managed_session(symbol, session_id)
        created.append(session_id)
    return created


def apply_champion(store: LearningStore, lab: LabStore, symbol: str) -> bool:
    """Give the managed paper account of ``symbol`` the current champion (from its next candle on)."""

    session_id = store.managed_session(symbol)
    if session_id is None:
        return False
    champion = store.champion(symbol)
    return lab.update_session_strategy(session_id, champion["params"]["strategy"],
                                       float(champion["params"]["stop_atr_multiple"]),
                                       f"champion v{champion['version']} : {champion['reason']}")


def overview(store: LearningStore, lab: LabStore) -> dict[str, Any]:
    pairs = []
    for symbol in PAIRS:
        champion = store.champion(symbol)
        session_id = store.managed_session(symbol)
        session = lab.session(session_id, item_limit=0) if session_id else None
        pairs.append({"symbol": symbol, "champion": champion, "trials": store.trials(symbol),
                      "last_run_at": (lambda d: d.isoformat() if d else None)(store.last_run_at(symbol)),
                      "session": ({"id": session_id, "status": session["status"], "account": session["account"],
                                   "trades_total": session["trades_total"], "orders_total": session["orders_total"],
                                   "last_processed": session["last_processed"]} if session else None)})
    return {"learning_version": LEARNING_VERSION, "pairs": pairs, "runs": store.runs(limit=30),
            "history": store.history(limit=40),
            "method": {"selection": "walk-forward annuel hors échantillon, coûts inclus",
                       "final_check_days": FINAL_CHECK_DAYS, "min_score_gain": MIN_SCORE_GAIN,
                       "max_drawdown_pct": MAX_DRAWDOWN_PCT, "min_trades": MIN_TRADES,
                       "candidates_per_run": CANDIDATES_PER_RUN,
                       "warning": "amélioration mesurée sur l'historique, aucune garantie de résultat futur"}}
