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

import hashlib
import json
import math
import random
import sqlite3
import statistics
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import overfitting
from .marketdata import INSTRUMENTS, PAIRS, CandleStore
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
CANDIDATES_PER_RUN = 36  # about six per strategy family
MIN_TRADES = 20
MIN_SCORE_GAIN = 0.5
MAX_DRAWDOWN_PCT = 15.0
SPACE = {
    "family": ("sma_trend", "momentum", "mean_reversion", "breakout", "currency_momentum", "currency_reversion"),
    "z_entry": (0.75, 1.0, 1.5, 2.0),  # mean_reversion only
    "fast": (5, 10, 15, 20, 30, 40),
    "slow": (20, 30, 50, 80, 120, 160),
    "atr_window": (10, 14, 20),
    "min_atr_pct": (0.0, 0.0001, 0.0003),
    "stop_atr_multiple": (1.5, 2.0, 3.0, 4.0),
    "regime": ("none", "match"),
    "take_profit_r": (0.0, 1.5, 2.5),  # 0: no target, the signal alone exits
}
DEFAULT_RISK = RiskParams()
DEFAULT_COSTS = CostParams()

Z_FAMILIES = ("mean_reversion", "currency_momentum", "currency_reversion")  # families that use the z threshold
DSR_VALID = 0.95          # deflated Sharpe probability from which a promotion counts as statistically solid (protocol value)
# Pre-registered review of a promoted champion on its own prospective (observation) trades, one-sided t-test on the mean
# net result per trade. Confirmation at 5 % (z 1.645) once enough trades could confirm half of the in-sample edge;
# suspension at 10 % (z -1.28) from REVIEW_MIN_OBS trades, the floor below which a t statistic means very little.
REVIEW_MIN_OBS = 8
Z_CONFIRM = 1.645
Z_SUSPEND = -1.28
SPACE_H1 = {  # bars are hours: 6 to 48 hours fast, 2 to 14 days slow (the strategy accepts up to 400 bars)
    "family": SPACE["family"],
    "z_entry": SPACE["z_entry"],
    "fast": (6, 12, 24, 48),
    "slow": (48, 96, 168, 336),
    "atr_window": (14, 24),
    "min_atr_pct": (0.0, 0.00003, 0.0001),  # an hourly ATR is a small share of the price
    "stop_atr_multiple": SPACE["stop_atr_multiple"],
    "regime": SPACE["regime"],
    "take_profit_r": SPACE["take_profit_r"],
}


@dataclass(frozen=True)
class Profile:
    """What a learning pipeline trains on and follows: the daily one (ECB candles) or the hourly one.

    The hourly profile chooses on the long Massive history (end-of-day delivery, quote-based bars) and its managed paper
    accounts follow the broker's own live H1 candles, which is also what a mirrored order would be priced on."""

    name: str
    timeframe: str
    source: str            # history the selection and the final check are run on
    live_source: str       # candles the managed paper accounts follow
    prefix: str            # table prefix: each profile keeps its own champions, runs and accounts
    session_label: str
    min_trades: int
    min_bars: int
    final_check_days: int
    window_days: int | None  # None: yearly walk-forward after three years of history
    space: dict
    selection_text: str
    demo_feed: bool        # True: its managed account feeds the DEMO bridge (frozen until a human authorizes a change)
    universe: tuple[str, ...] = ()  # the instruments this pipeline learns on


D1_PROFILE = Profile("D1", TIMEFRAME, SOURCE, SOURCE, "", "ASTRA auto", MIN_TRADES, 4 * 260, FINAL_CHECK_DAYS, None, SPACE,
                     "walk-forward annuel hors échantillon, coûts inclus", True, tuple(PAIRS))
H1_WINDOW_DAYS, H1_FINAL_CHECK_DAYS, H1_MIN_WINDOWS = 90, 120, 3
H1_PROFILE = Profile("H1", "H1", "MASSIVE_FX_QUOTES", "MT5_METAQUOTES_DEMO", "h1_", "ASTRA H1", 30, 6000, H1_FINAL_CHECK_DAYS,
                     H1_WINDOW_DAYS, SPACE_H1,
                     "walk-forward par blocs de 90 jours hors échantillon, coûts inclus, "
                     "contrôle final sur les 120 derniers jours", False, tuple(INSTRUMENTS))
PROFILES = {"D1": D1_PROFILE, "H1": H1_PROFILE}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {p}learn_champions (
    symbol TEXT NOT NULL, version INTEGER NOT NULL, params_json TEXT NOT NULL, promoted_at TEXT NOT NULL,
    run_id INTEGER, reason TEXT NOT NULL, active INTEGER NOT NULL, PRIMARY KEY (symbol, version)
);
CREATE TABLE IF NOT EXISTS {p}learn_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, data_json TEXT, champion_version INTEGER, decision TEXT, decision_json TEXT,
    candidates_json TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS {p}learn_managed (symbol TEXT PRIMARY KEY, session_id INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS {p}learn_shadow (symbol TEXT PRIMARY KEY, session_id INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS {p}learn_demo (
    symbol TEXT PRIMARY KEY, session_id INTEGER NOT NULL, active INTEGER NOT NULL, authorized_at TEXT NOT NULL,
    champion_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS {p}learn_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, symbol TEXT NOT NULL, run_id INTEGER, kind TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def default_params() -> dict[str, Any]:
    return {"strategy": asdict(StrategyParams()), "stop_atr_multiple": DEFAULT_RISK.stop_atr_multiple,
            "take_profit_r": DEFAULT_RISK.take_profit_r}


class LearningStore:
    def __init__(self, path: str | Path, profile: Profile = D1_PROFILE) -> None:
        self.path = Path(path)
        self.profile = profile
        self._p = profile.prefix

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
            db.executescript(_SCHEMA.replace("{p}", self._p))

    def champion(self, symbol: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(f"SELECT * FROM {self._p}learn_champions WHERE symbol = ? AND active = 1", (symbol,)).fetchone()
            if row is None:
                params = default_params()
                db.execute(f"INSERT OR IGNORE INTO {self._p}learn_champions(symbol, version, params_json, promoted_at, run_id,"
                           f" reason,"
                           " active) VALUES (?,?,?,?,?,?,1)",
                           (symbol, 1, json.dumps(params), _iso(_now()), None, "stratégie de référence (non optimisée)"))
                return {"symbol": symbol, "version": 1, "params": params, "reason": "stratégie de référence (non optimisée)"}
            return {"symbol": symbol, "version": row["version"], "params": json.loads(row["params_json"]),
                    "promoted_at": row["promoted_at"], "run_id": row["run_id"], "reason": row["reason"]}

    def promote(self, symbol: str, params: dict[str, Any], run_id: int | None, reason: str) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = (db.execute(f"SELECT COALESCE(MAX(version), 0) FROM {self._p}learn_champions WHERE symbol = ?", (symbol,))
                       .fetchone()[0]) + 1
            db.execute(f"UPDATE {self._p}learn_champions SET active = 0 WHERE symbol = ?", (symbol,))
            db.execute(f"INSERT INTO {self._p}learn_champions(symbol, version, params_json, promoted_at, run_id, reason, active)"
                       " VALUES (?,?,?,?,?,?,1)", (symbol, version, json.dumps(params), _iso(_now()), run_id, reason))
            return version

    def rollback(self, symbol: str) -> int:
        """Reactivate the previous champion version (a new version row, so the history stays append-only)."""

        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM {self._p}learn_champions WHERE symbol = ? ORDER BY version DESC",
                              (symbol,)).fetchall()
        if len(rows) < 2:
            raise SimulationError("aucune version précédente à rétablir")
        previous = rows[1]
        return self.promote(symbol, json.loads(previous["params_json"]), None,
                            f"retour arrière vers la version {previous['version']}")

    def history(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            if symbol:
                rows = db.execute(f"SELECT * FROM {self._p}learn_champions WHERE symbol = ? ORDER BY promoted_at DESC LIMIT ?",
                                  (symbol, limit)).fetchall()
            else:
                rows = db.execute(f"SELECT * FROM {self._p}learn_champions ORDER BY promoted_at DESC LIMIT ?",
                                  (limit,)).fetchall()
        return [{**{k: r[k] for k in ("symbol", "version", "promoted_at", "run_id", "reason", "active")},
                 "params": json.loads(r["params_json"])} for r in rows]

    def start_run(self, symbol: str) -> int:
        with self._connect() as db:
            return int(db.execute(f"INSERT INTO {self._p}learn_runs(symbol, started_at, status) VALUES (?,?,?)",
                                  (symbol, _iso(_now()), "RUNNING")).lastrowid)

    def finish_run(self, run_id: int, **values: Any) -> None:
        columns = {k: (json.dumps(v, default=str) if k.endswith("_json") else v) for k, v in values.items()}
        assignments = ", ".join(f"{k} = ?" for k in columns)
        with self._connect() as db:
            db.execute(f"UPDATE {self._p}learn_runs SET {assignments}, finished_at = ? WHERE id = ?",
                       (*columns.values(), _iso(_now()), run_id))

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM {self._p}learn_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
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
            row = db.execute(f"SELECT MAX(started_at) FROM {self._p}learn_runs WHERE symbol = ?", (symbol,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def trials(self, symbol: str) -> int:
        with self._connect() as db:
            rows = db.execute(f"SELECT candidates_json FROM {self._p}learn_runs WHERE symbol = ? AND status = 'DONE'",
                              (symbol,)).fetchall()
        return sum(len(json.loads(r[0] or "[]")) for r in rows)

    def managed_session(self, symbol: str) -> int | None:
        with self._connect() as db:
            row = db.execute(f"SELECT session_id FROM {self._p}learn_managed WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else None

    def set_managed_session(self, symbol: str, session_id: int) -> None:
        with self._connect() as db:
            db.execute(f"INSERT INTO {self._p}learn_managed(symbol, session_id, created_at) VALUES (?,?,?) ON CONFLICT(symbol)"
                       " DO UPDATE SET session_id = excluded.session_id", (symbol, session_id, _iso(_now())))


    def shadow_session(self, symbol: str) -> int | None:
        with self._connect() as db:
            row = db.execute(f"SELECT session_id FROM {self._p}learn_shadow WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else None

    def set_shadow_session(self, symbol: str, session_id: int) -> None:
        with self._connect() as db:
            db.execute(f"INSERT INTO {self._p}learn_shadow(symbol, session_id, created_at) VALUES (?,?,?) ON CONFLICT(symbol)"
                       " DO UPDATE SET session_id = excluded.session_id", (symbol, session_id, _iso(_now())))

    def demo_session(self, symbol: str) -> int | None:
        """The account that feeds the DEMO bridge for this pair under a person's authorization (None when not authorized)."""

        with self._connect() as db:
            row = db.execute(f"SELECT session_id FROM {self._p}learn_demo WHERE symbol = ? AND active = 1", (symbol,)).fetchone()
        return row[0] if row else None

    def set_demo_session(self, symbol: str, session_id: int, champion_version: int) -> None:
        with self._connect() as db:
            db.execute(f"INSERT INTO {self._p}learn_demo(symbol, session_id, active, authorized_at, champion_version)"
                       " VALUES (?,?,1,?,?) ON CONFLICT(symbol) DO UPDATE SET session_id = excluded.session_id, active = 1,"
                       " authorized_at = excluded.authorized_at, champion_version = excluded.champion_version",
                       (symbol, session_id, _iso(_now()), champion_version))

    def deactivate_demo(self, symbol: str) -> bool:
        with self._connect() as db:
            return db.execute(f"UPDATE {self._p}learn_demo SET active = 0 WHERE symbol = ? AND active = 1",
                              (symbol,)).rowcount == 1

    def record_event(self, symbol: str, kind: str, detail: dict[str, Any], run_id: int | None = None) -> None:
        """Append-only audit line: registration, promotion, refusal, suspension, rollback, authorization."""

        with self._connect() as db:
            db.execute(f"INSERT INTO {self._p}learn_events(ts, symbol, run_id, kind, detail_json) VALUES (?,?,?,?,?)",
                       (_iso(_now()), symbol, run_id, kind, json.dumps(detail, default=str, sort_keys=True)))

    def events(self, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            if symbol:
                rows = db.execute(f"SELECT * FROM {self._p}learn_events WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                                  (symbol, limit)).fetchall()
            else:
                rows = db.execute(f"SELECT * FROM {self._p}learn_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "symbol": r["symbol"], "run_id": r["run_id"], "kind": r["kind"],
                 "detail": json.loads(r["detail_json"])} for r in rows]


# -- evaluation -----------------------------------------------------------------------------------

def _request(symbol: str, params: dict[str, Any], start: datetime, end: datetime, cost_factor: float = 1.0,
             profile: Profile = D1_PROFILE) -> dict[str, Any]:
    risk = replace(DEFAULT_RISK, stop_atr_multiple=float(params["stop_atr_multiple"]),
                   take_profit_r=float(params.get("take_profit_r", 0.0)))
    spec = INSTRUMENTS[symbol]  # the costs of the instrument (hypotheses, doubled in the stress test)
    costs = replace(DEFAULT_COSTS, spread_pips=spec.spread_pips * cost_factor, slippage_pips=spec.slippage_pips * cost_factor)
    strategy = _build(StrategyParams, params["strategy"])
    return {"symbol": symbol, "timeframe": profile.timeframe, "source": profile.source, "start": _iso(start), "end": _iso(end),
            "strategy": STRATEGY_NAME, "strategy_params": asdict(strategy), "risk": asdict(risk), "costs": asdict(costs)}


def evaluate(candles: CandleStore, symbol: str, params: dict[str, Any], windows: list[tuple[datetime, datetime]],
             cost_factor: float = 1.0, purpose: str = "selection", profile: Profile = D1_PROFILE,
             keep_returns: bool = False) -> dict[str, Any]:
    """Out-of-sample metrics over the given windows (each simulated with earlier candles only as warm-up).

    ``purpose`` (selection, final_check, cost_stress) only labels the result."""

    yearly, trades, worst_dd, net, returns = [], 0, 0.0, 0.0, []
    for start, end in windows:
        try:
            result = run_backtest(candles, _request(symbol, params, start, end, cost_factor, profile))
        except SimulationError:
            continue
        m = result["metrics"]
        yearly.append(m["net_pnl"] / m["initial_capital"] * 100)
        trades += m["trade_count"]
        returns += [t["net_pnl"] / m["initial_capital"] for t in result["trades"]]
        worst_dd = max(worst_dd, m["max_drawdown_pct"])
        net += m["net_pnl"]
    if not yearly:
        return {"purpose": purpose, "windows": 0, "score": None, "trades": 0}
    mean = sum(yearly) / len(yearly)
    return {"purpose": purpose, "windows": len(yearly), "mean_return_pct": round(mean, 3),
            "worst_drawdown_pct": round(worst_dd, 3),
            "trades": trades, "net_pnl": round(net, 2), "positive_windows": sum(1 for y in yearly if y > 0),
            "score": round(mean - 0.5 * worst_dd, 3) if trades >= profile.min_trades else None,
            "yearly_returns_pct": [round(y, 3) for y in yearly],
            **({"returns": returns} if keep_returns else {})}


def candidates(seed: int, champion: dict[str, Any], count: int = CANDIDATES_PER_RUN,
               profile: Profile = D1_PROFILE) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    space = profile.space
    found: list[dict[str, Any]] = []
    # Champions stored before the strategy families have no family field: compare them with every field filled.
    normalized = {"strategy": asdict(_build(StrategyParams, champion["strategy"])),
                  "stop_atr_multiple": champion["stop_atr_multiple"],
                  "take_profit_r": champion.get("take_profit_r", 0.0)}
    seen = {json.dumps(normalized, sort_keys=True)}
    attempts = 0
    while len(found) < count and attempts < 1000:
        attempts += 1
        fast, slow = rng.choice(space["fast"]), rng.choice(space["slow"])
        if fast >= slow:
            continue
        family = rng.choice(space["family"])
        strategy = {"fast": fast, "slow": slow, "atr_window": rng.choice(space["atr_window"]),
                    "min_atr_pct": rng.choice(space["min_atr_pct"]), "max_atr_pct": 0.03, "family": family,
                    "z_entry": rng.choice(space["z_entry"]) if family in Z_FAMILIES else 1.0,
                    "regime": rng.choice(space["regime"])}
        params = {"strategy": strategy, "stop_atr_multiple": rng.choice(space["stop_atr_multiple"]),
                  "take_profit_r": rng.choice(space["take_profit_r"])}
        key = json.dumps(params, sort_keys=True)
        if key not in seen:
            seen.add(key)
            found.append(params)
    return found


def windows_for(first: datetime, last: datetime,
                profile: Profile = D1_PROFILE) -> tuple[list[tuple[datetime, datetime]], tuple[datetime, datetime]]:
    """Selection windows and the final check window (never used to choose).

    Daily profile: yearly windows after three years of history, final check = last 365 days. Hourly profile: blocks
    of 90 days walking back from the final check (last 120 days), keeping two weeks of history before the first one."""

    check_start = last - timedelta(days=profile.final_check_days)
    if profile.window_days:
        width = timedelta(days=profile.window_days)
        selection, end = [], check_start
        while end - width >= first + timedelta(days=14):
            selection.append((end - width, end - timedelta(seconds=1)))
            end -= width
        selection.reverse()
        return selection, (check_start, last)
    selection, start = [], datetime(first.year + 3, 1, 1, tzinfo=UTC)
    while start + timedelta(days=365) <= check_start:
        selection.append((start, start + timedelta(days=365) - timedelta(seconds=1)))
        start += timedelta(days=365)
    return selection, (check_start, last)


def _deflated(results: list[dict[str, Any]], best: dict[str, Any], trials: int) -> dict[str, Any] | None:
    """Deflated Sharpe ratio of the promoted candidate's trade returns, against every trial made so far (all runs)."""

    returns = best["selection"].get("returns") or []
    sharpes = [overfitting.sharpe_like(r["selection"]["returns"]) for r in results
               if len(r["selection"].get("returns") or []) >= 3]
    try:
        outcome = overfitting.deflated_sharpe_ratio(returns, n_trials=max(1, trials),
                                                    trial_sharpe_variance=overfitting.cross_sectional_variance(sharpes))
    except ValueError:
        return None
    return {"probability": round(outcome.probability, 4), "observed_sharpe": round(outcome.observed_sharpe, 4),
            "expected_max_sharpe": round(outcome.expected_max_sharpe, 4), "n_trials": trials, "n_trades": len(returns)}


def preregistered_review(returns: list[float]) -> dict[str, Any]:
    """Criteria of the prospective review, written down at promotion time from the candidate's own selection trades.

    ``n_required`` is the number of observed trades from which a one-sided 5 % test could confirm half of the in-sample
    mean return per trade; with no positive in-sample edge there is nothing to confirm (None), only to suspend."""

    review: dict[str, Any] = {"min_obs": REVIEW_MIN_OBS, "z_confirm": Z_CONFIRM, "z_suspend": Z_SUSPEND, "mu": None,
                              "sigma": None, "n_required": None}
    if len(returns) >= 3:
        mu, sigma = statistics.fmean(returns), statistics.stdev(returns)
        review.update(mu=round(mu, 8), sigma=round(sigma, 8))
        if mu > 0 and sigma > 0:
            review["n_required"] = max(REVIEW_MIN_OBS, math.ceil((Z_CONFIRM * sigma / (0.5 * mu)) ** 2))
    return review


def live_stats(champion: dict[str, Any], session: dict[str, Any] | None) -> dict[str, Any]:
    """The champion's prospective record: the net result per trade (as a share of the account) since its promotion."""

    since = champion.get("promoted_at") or ""
    capital = ((session or {}).get("account") or {}).get("initial_capital") or 10_000.0
    trades = [t for t in (session or {}).get("trades", []) if t["exit_ts"] >= since]
    returns = [t["net_pnl"] / capital for t in trades]
    n = len(returns)
    spread = statistics.stdev(returns) if n >= 2 else 0.0
    t_stat = statistics.fmean(returns) / (spread / math.sqrt(n)) if n >= 2 and spread > 0 else None
    return {"n": n, "net_pnl": round(sum(t["net_pnl"] for t in trades), 2), "t": t_stat}


def live_record(champion: dict[str, Any], session: dict[str, Any] | None) -> dict[str, Any]:
    """Compact prospective record for the dashboard: closed trades, net result, and the trades the review needs."""

    stats = live_stats(champion, session)
    review = champion["params"].get("review") or {}
    return {"trades": stats["n"], "net_pnl": stats["net_pnl"], "needed": review.get("n_required"),
            "min_obs": review.get("min_obs", REVIEW_MIN_OBS)}


def _observation_session(store: LearningStore, symbol: str) -> int | None:
    """The paper account that observes the champion without any order: the shadow account of a pipeline that feeds
    the DEMO bridge, otherwise its managed account."""

    return store.shadow_session(symbol) if store.profile.demo_feed else store.managed_session(symbol)


def review_live(store: LearningStore, lab: LabStore, symbol: str) -> str | None:
    """Judge a promoted champion on its prospective record, against the criteria registered at its promotion.

    Suspension (and the return to the previous version) when the one-sided test says the mean result is negative;
    confirmation when enough trades confirm half of the in-sample edge. Simulation only: neither touches the DEMO bridge."""

    champion = store.champion(symbol)
    params = champion["params"]
    review = params.get("review")
    session_id = _observation_session(store, symbol)
    if champion.get("run_id") is None or not review or session_id is None:
        return None
    stats = live_stats(champion, lab.session(session_id, item_limit=1000))
    if stats["t"] is None or stats["n"] < review["min_obs"]:
        return None
    detail = {"version": champion["version"], "trades": stats["n"], "net_pnl": stats["net_pnl"], "t": round(stats["t"], 3),
              "criteria": review}
    if stats["t"] <= review["z_suspend"]:
        store.record_event(symbol, "SUSPENSION", detail, champion["run_id"])
        version = store.rollback(symbol)
        apply_champion(store, lab, symbol)
        store.record_event(symbol, "RETOUR_ARRIERE", {**detail, "restored_as_version": version}, champion["run_id"])
        if not store.profile.demo_feed and store.deactivate_demo(symbol):
            # the authorization was given to a strategy that no longer holds: the pair goes back to the daily feed
            store.record_event(symbol, "SUSPENSION_DEMO", {**detail, "reason": "revue prospective négative"}, champion["run_id"])
        return "SUSPENDED"
    if (params.get("review_status") != "CONFIRME" and review.get("n_required") and stats["n"] >= review["n_required"]
            and stats["t"] >= review["z_confirm"]):
        store.promote(symbol, {**params, "review_status": "CONFIRME", "tier": "VALIDE"}, champion["run_id"],
                      f"confirmé en observation : {stats['n']} transactions, t = {stats['t']:.2f}, "
                      f"résultat net {stats['net_pnl']:+.2f} USD")
        store.record_event(symbol, "CONFIRMATION_PROSPECTIVE", detail, champion["run_id"])
        return "CONFIRMED"
    return None


def authorize_demo(store: LearningStore, lab: LabStore, symbol: str, operator: str, reason: str, *,
                   candles: CandleStore | None = None, accept_unconfirmed: bool = False) -> bool:
    """A person gives the DEMO bridge the current champion of ``symbol``. No automatic process calls this function.

    Daily pipeline: the account that already feeds the bridge takes the champion's parameters. Hourly pipeline: a
    dedicated account (created now, flat) is opened for the pair, and the bridge follows it instead of the daily one.
    The hourly path requires a champion promoted by a run and confirmed by the prospective review, unless the person
    states in the command that the champion is not yet confirmed (the audit line records it). Returns False when
    nothing changes."""

    if not operator.strip() or not reason.strip():
        raise SimulationError("un opérateur et une raison sont obligatoires")
    champion = store.champion(symbol)
    params = champion["params"]
    profile = store.profile
    unconfirmed = False
    if profile.demo_feed:
        session_id = store.managed_session(symbol)
        if session_id is None:
            raise SimulationError(f"aucun compte géré pour {symbol}")
        changed = lab.update_session_strategy(session_id, params["strategy"], float(params["stop_atr_multiple"]),
                                              f"autorisation DEMO par {operator} : champion v{champion['version']}",
                                              take_profit_r=float(params.get("take_profit_r", 0.0)))
    else:
        if champion.get("run_id") is None:
            raise SimulationError(f"le champion {profile.name} de {symbol} n'a pas été promu par un run : rien à autoriser")
        unconfirmed = params.get("review_status") != "CONFIRME"
        if unconfirmed and not accept_unconfirmed:
            raise SimulationError("champion pas encore confirmé par l'observation prospective : refaites la commande avec "
                                  "--accept-unconfirmed si vous assumez ce risque (la décision sera journalisée)")
        if candles is None:
            raise SimulationError("les bougies sont nécessaires pour ouvrir le compte DEMO H1")
        existing = store.demo_session(symbol)
        if existing is not None:
            changed = lab.update_session_strategy(existing, params["strategy"], float(params["stop_atr_multiple"]),
                                                  f"autorisation DEMO par {operator} : champion v{champion['version']}",
                                                  take_profit_r=float(params.get("take_profit_r", 0.0)))
            store.set_demo_session(symbol, existing, champion["version"])
        else:
            begin = _now().replace(minute=0, second=0, microsecond=0)
            request = simulation_request({"name": f"{profile.session_label} DEMO · {symbol}", "symbols": [symbol],
                                          "timeframe": profile.timeframe, "source": profile.live_source,
                                          "start": begin.isoformat(), "strategy_params": params["strategy"],
                                          "risk": {"stop_atr_multiple": params["stop_atr_multiple"],
                                                   "take_profit_r": params.get("take_profit_r", 0.0),
                                                   "max_positions": 1}}, candles)
            store.set_demo_session(symbol, lab.create_session(request, f"cli:{operator}"), champion["version"])
            changed = True
    store.record_event(symbol, "AUTORISATION_DEMO", {
        "operator": operator, "reason": reason, "changed": changed, "profile": profile.name, "unconfirmed": unconfirmed,
        "champion_version": champion["version"], "review_status": params.get("review_status"), "params": params},
        champion.get("run_id"))
    return changed


def revoke_demo(store: LearningStore, symbol: str, operator: str, reason: str) -> bool:
    """A person takes a pair back from the hourly DEMO feed: the bridge follows the daily pipeline again for it."""

    if not operator.strip() or not reason.strip():
        raise SimulationError("un opérateur et une raison sont obligatoires")
    if store.profile.demo_feed:
        raise SimulationError("le flux quotidien est le flux par défaut : rien à révoquer")
    revoked = store.deactivate_demo(symbol)
    store.record_event(symbol, "REVOCATION_DEMO", {"operator": operator, "reason": reason, "revoked": revoked})
    return revoked


def learn(candles: CandleStore, store: LearningStore, symbol: str) -> dict[str, Any]:
    """One learning run for a pair; returns the decision (promoted or kept) with its evidence."""

    profile = store.profile
    if symbol not in profile.universe:
        raise SimulationError(f"instrument {symbol} hors de l'univers du profil {profile.name}")
    run_id = store.start_run(symbol)
    try:
        series = candles.candles(symbol, profile.timeframe, profile.source)
        if len(series) < profile.min_bars:
            raise SimulationError(f"historique trop court pour apprendre ({len(series)} bougies {profile.timeframe})")
        selection, check = windows_for(series[0].timestamp, series[-1].timestamp, profile)
        if profile.window_days and len(selection) < H1_MIN_WINDOWS:
            raise SimulationError(f"pas assez de blocs de sélection ({len(selection)}, minimum {H1_MIN_WINDOWS})")
        champion = store.champion(symbol)
        pool = candidates(run_id * 7919 + len(series), champion["params"], profile=profile)
        identity = {"profile": profile.name, "source": profile.source, "timeframe": profile.timeframe,
                    "nature": (candles.source(profile.source) or {}).get("nature"), "candles": len(series),
                    "first": _iso(series[0].timestamp), "last": _iso(series[-1].timestamp),
                    "sha256": candles.fingerprint(series)}
        criteria = {"min_score_gain": MIN_SCORE_GAIN, "max_drawdown_pct": MAX_DRAWDOWN_PCT, "min_trades": profile.min_trades,
                    "final_check_days": profile.final_check_days, "dsr_valid": DSR_VALID,
                    "cost_stress_factor": 2.0, "selection": profile.selection_text}
        store.record_event(symbol, "CANDIDATS_ENREGISTRES", {
            "champion_version": champion["version"], "candidates": len(pool), "criteria": criteria, "data": identity,
            "pool_sha256": hashlib.sha256(json.dumps(pool, sort_keys=True).encode()).hexdigest(),
            "note": "paramètres et critères figés avant toute évaluation ; le contrôle final est réutilisé d'un run à "
                    "l'autre : c'est une présélection, la preuve indépendante est l'observation prospective"}, run_id)
        results = []
        for label, params in [("champion", champion["params"]), *[(f"candidat {i + 1}", p) for i, p in enumerate(pool)]]:
            results.append({"label": label, "params": params,
                            "selection": evaluate(candles, symbol, params, selection, profile=profile,
                                                  keep_returns=True)})
        champ = results[0]
        scored = [r for r in results[1:] if r["selection"]["score"] is not None]
        best = max(scored, key=lambda r: r["selection"]["score"], default=None)
        decision: dict[str, Any] = {"champion_version": champion["version"], "champion_selection": champ["selection"],
                                    "criteria": {"min_score_gain": MIN_SCORE_GAIN, "max_drawdown_pct": MAX_DRAWDOWN_PCT,
                                                 "min_trades": profile.min_trades,
                                                 "final_check_days": profile.final_check_days}}
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
                final_best = evaluate(candles, symbol, best["params"], [check], purpose="final_check", profile=profile)
                final_champ = evaluate(candles, symbol, champion["params"], [check], purpose="final_check", profile=profile)
                stressed = evaluate(candles, symbol, best["params"], selection, cost_factor=2.0, purpose="cost_stress",
                                    profile=profile)
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
        tier, review = None, None
        if verdict == "PROMOTED":
            review = preregistered_review(best["selection"].get("returns") or [])
            dsr = _deflated(results, best, decision["trials_so_far"])
            decision["dsr"] = dsr
            tier = "VALIDE" if dsr and dsr["probability"] >= DSR_VALID else "PROBATOIRE"
            solid = f", Sharpe dégonflé {dsr['probability']:.2f}" if dsr else ", Sharpe dégonflé incalculable"
            reason += f" ; niveau {tier.lower()}{solid}"
            decision["reason"], decision["tier"] = reason, tier
        for r in results:
            r["selection"].pop("returns", None)
        if verdict == "PROMOTED":
            promoted = {**best["params"], "tier": tier, "dsr": decision["dsr"]["probability"] if decision["dsr"] else None,
                        "review": review}
            decision["new_version"] = store.promote(symbol, promoted, run_id, reason)
            decision["review"] = review
        data = {**identity, "selection_windows": len(selection),
                "final_check": [_iso(check[0]), _iso(check[1])], "learning_version": LEARNING_VERSION}
        compact = [{"label": r["label"], "params": r["params"], "score": r["selection"]["score"],
                    "mean_return_pct": r["selection"].get("mean_return_pct"), "trades": r["selection"]["trades"],
                    "worst_drawdown_pct": r["selection"].get("worst_drawdown_pct")} for r in results]
        store.finish_run(run_id, status="DONE", data_json=data, champion_version=champion["version"], decision=verdict,
                         decision_json=decision, candidates_json=compact)
        best_selection = (decision.get("best") or {}).get("selection") or {}
        store.record_event(symbol, "PROMOTION_SIMULATION" if verdict == "PROMOTED" else "REFUS", {
            "reason": reason, "tier": tier, "dsr": decision.get("dsr"), "review": review,
            "trials_so_far": decision["trials_so_far"],
            "best_score": best_selection.get("score"), "champion_score": champ["selection"].get("score"),
            "new_version": decision.get("new_version")}, run_id)
        return {"run_id": run_id, "symbol": symbol, "decision": verdict, "reason": reason, "candidates": len(results)}
    except Exception as exc:
        store.finish_run(run_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        store.record_event(symbol, "ECHEC", {"error": f"{type(exc).__name__}: {exc}"}, run_id)
        raise


# -- managed paper simulations ---------------------------------------------------------------------

MANAGED_START = datetime(2025, 10, 28, tzinfo=UTC)


def ensure_managed_sessions(candles: CandleStore, store: LearningStore, lab: LabStore) -> list[int]:
    """One continuous paper account per pair, trading the pair's champion; created once, never reset."""

    created = []
    profile = store.profile
    available = {(c["symbol"], c["timeframe"], c["source"]) for c in candles.coverage()}
    # The daily accounts replay a year; the hourly ones start now, so no old bar is ever traded after the fact.
    begin = MANAGED_START if profile is D1_PROFILE else _now().replace(minute=0, second=0, microsecond=0)
    for symbol in store.profile.universe:
        if (symbol, profile.timeframe, profile.live_source) not in available or store.managed_session(symbol) is not None:
            continue
        champion = store.champion(symbol)
        request = simulation_request({"name": f"{profile.session_label} · {symbol} (champion)", "symbols": [symbol],
                                      "timeframe": profile.timeframe, "source": profile.live_source,
                                      "start": begin.isoformat(),
                                      "strategy_params": champion["params"]["strategy"],
                                      "risk": {"stop_atr_multiple": champion["params"]["stop_atr_multiple"],
                                               "take_profit_r": champion["params"].get("take_profit_r", 0.0),
                                               "max_positions": 1}}, candles)
        session_id = lab.create_session(request, "worker:apprentissage")
        store.set_managed_session(symbol, session_id)
        created.append(session_id)
    return created


def ensure_shadow_sessions(candles: CandleStore, store: LearningStore, lab: LabStore) -> list[int]:
    """For a pipeline that feeds the DEMO bridge: one observation account per pair, following the champion, starting now.

    It decides without any order and without any effect on the bridge: it is where a promoted champion is watched
    prospectively. Created once, never reset."""

    profile = store.profile
    if not profile.demo_feed:
        return []
    created = []
    available = {(c["symbol"], c["timeframe"], c["source"]) for c in candles.coverage()}
    begin = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    for symbol in store.profile.universe:
        if (symbol, profile.timeframe, profile.live_source) not in available or store.shadow_session(symbol) is not None:
            continue
        champion = store.champion(symbol)
        request = simulation_request({"name": f"{profile.session_label} · {symbol} (observation)", "symbols": [symbol],
                                      "timeframe": profile.timeframe, "source": profile.live_source,
                                      "start": begin.isoformat(), "strategy_params": champion["params"]["strategy"],
                                      "risk": {"stop_atr_multiple": champion["params"]["stop_atr_multiple"],
                                               "take_profit_r": champion["params"].get("take_profit_r", 0.0),
                                               "max_positions": 1}}, candles)
        session_id = lab.create_session(request, "worker:apprentissage")
        store.set_shadow_session(symbol, session_id)
        created.append(session_id)
    return created


def apply_champion(store: LearningStore, lab: LabStore, symbol: str) -> bool:
    """Give the observation account of ``symbol`` the current champion (from its next candle on). Never the account that
    feeds the DEMO bridge: that one changes only through ``authorize_demo``."""

    session_id = _observation_session(store, symbol)
    if session_id is None:
        return False
    champion = store.champion(symbol)
    return lab.update_session_strategy(session_id, champion["params"]["strategy"],
                                       float(champion["params"]["stop_atr_multiple"]),
                                       f"champion v{champion['version']} : {champion['reason']}",
                                       take_profit_r=float(champion["params"].get("take_profit_r", 0.0)))


def overview(store: LearningStore, lab: LabStore) -> dict[str, Any]:
    pairs = []
    for symbol in store.profile.universe:
        champion = store.champion(symbol)
        session_id = store.managed_session(symbol)
        session = lab.session(session_id, item_limit=0) if session_id else None
        shadow_id = store.shadow_session(symbol)
        demo_id = store.demo_session(symbol)
        demo = lab.session(demo_id, item_limit=0) if demo_id else None
        pairs.append({"symbol": symbol, "champion": champion, "trials": store.trials(symbol), "shadow_session_id": shadow_id,
                      "demo_session": ({"id": demo_id, "status": demo["status"], "account": demo["account"],
                                        "trades_total": demo["trades_total"], "orders_total": demo["orders_total"],
                                        "last_processed": demo["last_processed"]} if demo else None),
                      "last_run_at": (lambda d: d.isoformat() if d else None)(store.last_run_at(symbol)),
                      "session": ({"id": session_id, "status": session["status"], "account": session["account"],
                                   "trades_total": session["trades_total"], "orders_total": session["orders_total"],
                                   "last_processed": session["last_processed"]} if session else None)})
    profile = store.profile
    return {"learning_version": LEARNING_VERSION, "profile": profile.name, "pairs": pairs, "runs": store.runs(limit=30),
            "history": store.history(limit=40),
            "method": {"selection": profile.selection_text,
                       "final_check_days": profile.final_check_days, "min_score_gain": MIN_SCORE_GAIN,
                       "max_drawdown_pct": MAX_DRAWDOWN_PCT, "min_trades": profile.min_trades,
                       "candidates_per_run": CANDIDATES_PER_RUN,
                       "warning": "amélioration mesurée sur l'historique, aucune garantie de résultat futur"}}
