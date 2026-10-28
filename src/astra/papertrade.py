"""One simulation engine for backtests and persistent paper trading (SIMULATION only, no broker).

The same ``Engine`` processes candles in time order for a backtest and for a running simulation, so a
simulation is the continuation of what a backtest measures:

- decisions are taken at a candle's close with the candles up to that close only;
- an order decided at close ``t`` fills at the open of the next candle (no look-ahead);
- costs are explicit and configurable: half the spread plus slippage on each side, commission per side;
- the stop is mandatory (a multiple of the ATR at decision time); a candle whose low/high reaches it closes
  the position at the stop, an open beyond the stop closes it at that open;
- account currency is USD. P&L of USD/JPY, USD/CHF and USD/CAD (quote currency not USD) is converted at the
  exit price; sizing converts the stop distance the same way;
- risk limits: risk per trade, maximum position size, maximum simultaneous positions, daily loss limit
  (new entries blocked until the next UTC day), maximum drawdown (new entries blocked until an operator
  resets the simulation), margin at a fixed leverage.

Every order the engine fills or refuses is recorded with its reason; a signal is never counted as a trade.
"""

from __future__ import annotations

import itertools
import json
import math
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .marketdata import KIND_REFERENCE, PAIRS, TIMEFRAMES, Candle, CandleStore, MarketDataError, find_gaps

ENGINE_VERSION = "1.0.0"
STRATEGY_NAME = "sma_atr_trend"
STRATEGY_VERSION = "1.0.0"
BUY, SELL, FLAT = "ACHAT", "VENTE", "AUCUNE_POSITION"
MIN_UNITS, UNIT_STEP = 1000, 1000
MAX_EQUITY_POINTS = 600


class SimulationError(ValueError):
    """Invalid parameters or state, with a message meant for the operator."""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


# -- parameters -----------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StrategyParams:
    """Experimental reference strategy: moving-average trend with a volatility filter (not proven)."""

    fast: int = 20
    slow: int = 50
    atr_window: int = 14
    min_atr_pct: float = 0.0002   # ATR / price below this: market too quiet, no position
    max_atr_pct: float = 0.03     # ATR / price above this: too volatile, no position

    def validate(self) -> None:
        if not (2 <= self.fast < self.slow <= 400):
            raise SimulationError("moyennes mobiles : 2 ≤ rapide < lente ≤ 400")
        if not 2 <= self.atr_window <= 200:
            raise SimulationError("fenêtre ATR entre 2 et 200")
        if not (0 <= self.min_atr_pct < self.max_atr_pct <= 0.5):
            raise SimulationError("filtre de volatilité : 0 ≤ minimum < maximum ≤ 0,5")

    @property
    def warmup(self) -> int:
        return max(self.slow, self.atr_window + 1)


@dataclass(frozen=True, slots=True)
class RiskParams:
    initial_capital: float = 10_000.0
    risk_per_trade_pct: float = 0.5        # % of equity lost if the stop is hit
    stop_atr_multiple: float = 2.0
    max_position_units: float = 1_000_000
    max_positions: int = 1
    daily_loss_limit_pct: float = 2.0
    max_drawdown_pct: float = 15.0
    leverage: float = 30.0

    def validate(self) -> None:
        checks = (
            (100 <= self.initial_capital <= 100_000_000, "capital initial entre 100 et 100 000 000 USD"),
            (0.01 <= self.risk_per_trade_pct <= 2.0, "risque par transaction entre 0,01 % et 2 %"),
            (0.5 <= self.stop_atr_multiple <= 10, "stop entre 0,5 et 10 ATR"),
            (MIN_UNITS <= self.max_position_units <= 10_000_000, "taille maximale entre 1 000 et 10 000 000 unités"),
            (isinstance(self.max_positions, int) and 1 <= self.max_positions <= len(PAIRS),
             f"positions simultanées entre 1 et {len(PAIRS)}"),
            (0.1 <= self.daily_loss_limit_pct <= 10, "limite de perte journalière entre 0,1 % et 10 %"),
            (1 <= self.max_drawdown_pct <= 50, "drawdown maximal entre 1 % et 50 %"),
            (1 <= self.leverage <= 50, "levier entre 1 et 50"),
        )
        for ok, message in checks:
            if not ok:
                raise SimulationError(message)


@dataclass(frozen=True, slots=True)
class CostParams:
    """Simulation assumptions, not broker-reported costs."""

    spread_pips: float = 1.0
    slippage_pips: float = 0.2              # per side
    commission_per_100k: float = 0.0        # USD per side per 100 000 units

    def validate(self) -> None:
        if not 0 <= self.spread_pips <= 50:
            raise SimulationError("spread entre 0 et 50 pips")
        if not 0 <= self.slippage_pips <= 20:
            raise SimulationError("slippage entre 0 et 20 pips par côté")
        if not 0 <= self.commission_per_100k <= 100:
            raise SimulationError("commission entre 0 et 100 USD par côté et par 100 000 unités")


def _build(cls: type, values: dict[str, Any] | None) -> Any:
    values = dict(values or {})
    known = {name for name in cls.__slots__}
    unknown = set(values) - known
    if unknown:
        raise SimulationError(f"paramètre inconnu : {', '.join(sorted(unknown))}")
    defaults = asdict(cls())
    typed: dict[str, Any] = {}
    for name, value in values.items():
        default = defaults[name]
        try:
            typed[name] = int(value) if isinstance(default, int) and not isinstance(default, bool) else float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SimulationError(f"valeur invalide pour {name}") from exc
        if isinstance(typed[name], float) and not math.isfinite(typed[name]):
            raise SimulationError(f"valeur invalide pour {name}")
    params = cls(**{**defaults, **typed})
    params.validate()
    return params


# -- strategy -------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Decision:
    action: str
    price: float
    indicators: dict[str, float | None]
    conditions: dict[str, bool]
    reason: str


def decide(history: Sequence[Candle], params: StrategyParams) -> Decision | None:
    """The decision at the close of ``history[-1]``, using only ``history`` (None during warm-up)."""

    if len(history) < params.warmup:
        return None
    closes = [c.close for c in history[-params.slow:]]
    fast = sum(closes[-params.fast:]) / params.fast
    slow = sum(closes) / params.slow
    ranges = []
    window = history[-(params.atr_window + 1):]
    for previous, current in itertools.pairwise(window):
        high = current.high if current.high is not None else current.close
        low = current.low if current.low is not None else current.close
        ranges.append(max(high - low, abs(high - previous.close), abs(low - previous.close)))
    atr = sum(ranges) / len(ranges)
    price = history[-1].close
    atr_pct = atr / price
    trend_up, trend_down = fast > slow, fast < slow
    vol_ok = params.min_atr_pct <= atr_pct <= params.max_atr_pct
    conditions = {"moyenne_rapide_au_dessus": trend_up, "moyenne_rapide_au_dessous": trend_down,
                  "volatilite_dans_la_plage": vol_ok}
    indicators = {"sma_rapide": round(fast, 6), "sma_lente": round(slow, 6), "atr": round(atr, 6),
                  "atr_pct": round(atr_pct * 100, 4)}
    if not vol_ok:
        side = "trop faible" if atr_pct < params.min_atr_pct else "trop forte"
        return Decision(FLAT, price, indicators, conditions,
                        f"volatilité {side} (ATR {atr_pct * 100:.3f} % du prix, plage {params.min_atr_pct * 100:.3f}"
                        f" à {params.max_atr_pct * 100:.3f} %)")
    if trend_up:
        return Decision(BUY, price, indicators, conditions,
                        f"SMA{params.fast} {fast:.5f} > SMA{params.slow} {slow:.5f} et volatilité dans la plage")
    if trend_down:
        return Decision(SELL, price, indicators, conditions,
                        f"SMA{params.fast} {fast:.5f} < SMA{params.slow} {slow:.5f} et volatilité dans la plage")
    return Decision(FLAT, price, indicators, conditions, "moyennes égales : pas de tendance")


STRATEGIES = {
    STRATEGY_NAME: {
        "version": STRATEGY_VERSION,
        "label": "Tendance SMA + filtre ATR (expérimentale, non validée)",
        "description": ("ACHAT si la moyenne rapide est au-dessus de la lente, VENTE si elle est au-dessous, AUCUNE "
                        "POSITION si la volatilité (ATR/prix) sort de la plage. Stratégie de référence pour tester la "
                        "chaîne : aucune performance n'est démontrée."),
        "params": asdict(StrategyParams()),
    },
}


# -- engine ---------------------------------------------------------------------------------------

def _cost_per_side(symbol: str, costs: CostParams) -> float:
    return (costs.spread_pips / 2 + costs.slippage_pips) * PAIRS[symbol][2]


def _to_usd(symbol: str, amount_quote: float, price: float) -> float:
    base, quote = PAIRS[symbol][0], PAIRS[symbol][1]
    if quote == "USD":
        return amount_quote
    if base == "USD":
        return amount_quote / price
    raise SimulationError(f"conversion USD non définie pour {symbol}")


def _notional_usd(symbol: str, units: float, price: float) -> float:
    return units * price if PAIRS[symbol][1] == "USD" else units


@dataclass(slots=True)
class Recorder:
    signals: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity: list[tuple[str, float]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


class Engine:
    """Bar-by-bar simulation; ``state`` is a plain JSON-serialisable dict so it can be persisted."""

    def __init__(self, strategy: StrategyParams, risk: RiskParams, costs: CostParams, state: dict[str, Any] | None = None):
        self.strategy, self.risk, self.costs = strategy, risk, costs
        self.state = state or {
            "cash": risk.initial_capital, "positions": {}, "pending": {}, "last_decision": {},
            "peak_equity": risk.initial_capital, "equity": risk.initial_capital, "day": None,
            "day_start_equity": risk.initial_capital, "daily_halt": False, "drawdown_halt": False,
            "last_close": {}, "order_seq": 0, "trade_seq": 0,
        }

    # -- helpers --------------------------------------------------------------------------------

    def _mark(self, symbol: str, price: float) -> float:
        position = self.state["positions"].get(symbol)
        if not position:
            return 0.0
        direction = 1 if position["side"] == "LONG" else -1
        exit_fill = price - direction * _cost_per_side(symbol, self.costs)
        commission = 2 * position["units"] / 100_000 * self.costs.commission_per_100k
        return _to_usd(symbol, (exit_fill - position["entry_fill"]) * position["units"] * direction, price) - commission

    def equity(self) -> float:
        return self.state["cash"] + sum(self._mark(symbol, self.state["last_close"].get(symbol, p["entry_ref"]))
                                        for symbol, p in self.state["positions"].items())

    def _used_margin(self) -> float:
        return sum(_notional_usd(s, p["units"], self.state["last_close"].get(s, p["entry_ref"])) / self.risk.leverage
                   for s, p in self.state["positions"].items())

    def _order(self, rec: Recorder, ts: datetime, symbol: str, kind: str, side: str, units: float, status: str,
               reference: float | None, fill: float | None, reason: str) -> None:
        self.state["order_seq"] += 1
        rec.orders.append({"seq": self.state["order_seq"], "ts": _iso(ts), "symbol": symbol, "kind": kind, "side": side,
                           "units": units, "status": status, "reference_price": reference, "fill_price": fill,
                           "reason": reason})

    def _close(self, rec: Recorder, ts: datetime, symbol: str, reference: float, reason: str) -> None:
        position = self.state["positions"].pop(symbol)
        direction = 1 if position["side"] == "LONG" else -1
        exit_fill = reference - direction * _cost_per_side(symbol, self.costs)
        units = position["units"]
        gross = _to_usd(symbol, (reference - position["entry_ref"]) * units * direction, reference)
        net_before_commission = _to_usd(symbol, (exit_fill - position["entry_fill"]) * units * direction, reference)
        commission = 2 * units / 100_000 * self.costs.commission_per_100k
        net = net_before_commission - commission
        self.state["cash"] += net
        self._order(rec, ts, symbol, "SORTIE", "VENTE" if direction == 1 else "ACHAT", units, "EXECUTE", reference,
                    round(exit_fill, 6), reason)
        self.state["trade_seq"] += 1
        rec.trades.append({"seq": self.state["trade_seq"], "symbol": symbol, "side": position["side"], "units": units,
                           "entry_ts": position["entry_ts"], "exit_ts": _iso(ts), "entry_price": position["entry_ref"],
                           "exit_price": reference, "entry_fill": position["entry_fill"], "exit_fill": round(exit_fill, 6),
                           "stop_price": position["stop"], "gross_pnl": round(gross, 4),
                           "costs": round(gross - net, 4), "net_pnl": round(net, 4), "exit_reason": reason})

    def _open(self, rec: Recorder, ts: datetime, symbol: str, side: str, reference: float, atr: float) -> None:
        direction = 1 if side == "LONG" else -1
        label = "ACHAT" if direction == 1 else "VENTE"
        refusal = code = None
        equity = self.equity()
        if self.state["drawdown_halt"]:
            code, refusal = "drawdown", (f"drawdown maximal atteint ({self.risk.max_drawdown_pct} %) : entrées arrêtées"
                                         " pour cette simulation")
        elif self.state["daily_halt"]:
            code, refusal = "daily", f"limite de perte journalière atteinte ({self.risk.daily_loss_limit_pct} %)"
        elif len(self.state["positions"]) >= self.risk.max_positions:
            code, refusal = "positions", f"nombre maximal de positions simultanées atteint ({self.risk.max_positions})"
        stop_distance = self.risk.stop_atr_multiple * atr
        if refusal is None and (not math.isfinite(stop_distance) or stop_distance <= 0):
            code, refusal = "stop", "distance de stop nulle (ATR nul) : pas de stop possible"
        units = 0.0
        if refusal is None:
            risk_usd = equity * self.risk.risk_per_trade_pct / 100
            loss_per_unit = _to_usd(symbol, stop_distance + 2 * _cost_per_side(symbol, self.costs), reference)
            raw_units = risk_usd / loss_per_unit
            capped = min(raw_units, self.risk.max_position_units)
            units = math.floor(capped / UNIT_STEP) * UNIT_STEP
            if units < MIN_UNITS:
                code = "size"
                refusal = f"taille calculée {raw_units:.0f} unités < minimum {MIN_UNITS} (capital ou stop trop petit)"
            elif self._used_margin() + _notional_usd(symbol, units, reference) / self.risk.leverage > equity:
                code = "margin"
                refusal = f"marge insuffisante au levier 1:{self.risk.leverage:g}"
        if refusal:
            refusals = self.state.setdefault("last_refusal", {})
            key = f"{code}:{side}"
            if refusals.get(symbol) != key:
                self._order(rec, ts, symbol, "ENTREE", label, units, "REFUSE", reference, None, refusal)
                refusals[symbol] = key
            return
        self.state.setdefault("last_refusal", {}).pop(symbol, None)
        entry_fill = reference + direction * _cost_per_side(symbol, self.costs)
        stop = reference - direction * stop_distance
        self.state["positions"][symbol] = {"side": side, "units": units, "entry_ts": _iso(ts), "entry_ref": reference,
                                           "entry_fill": round(entry_fill, 6), "stop": round(stop, 6)}
        note = " (plafonnée)" if units >= self.risk.max_position_units else ""
        self._order(rec, ts, symbol, "ENTREE", label, units, "EXECUTE", reference, round(entry_fill, 6),
                    f"signal {label} ; stop {stop:.5f} ({self.risk.stop_atr_multiple:g} ATR){note}")

    # -- the step -------------------------------------------------------------------------------

    def process(self, candle: Candle, history: Sequence[Candle], rec: Recorder) -> None:
        """Process one candle; ``history`` ends with ``candle`` (same symbol, oldest first)."""

        s, symbol, ts = self.state, candle.symbol, candle.timestamp
        day = ts.date().isoformat()
        if s["day"] is None or day > s["day"]:
            if s["daily_halt"]:
                rec.events.append({"ts": _iso(ts), "kind": "LIMITE_JOURNALIERE_LEVEE", "message": "nouveau jour UTC"})
            s["day"], s["day_start_equity"], s["daily_halt"] = day, self.equity(), False
            s["last_refusal"] = {}  # a refusal on a new day is recorded again
        opening = candle.open if candle.open is not None else candle.close
        # 1. the order decided at the previous close fills now
        target = s["pending"].pop(symbol, None)
        position = s["positions"].get(symbol)
        if target is not None:
            pending_atr = s.get("pending_atr", {}).pop(symbol, None)
            if position and target["side"] != position["side"]:
                crossed = (position["side"] == "LONG" and opening <= position["stop"]) or \
                          (position["side"] == "SHORT" and opening >= position["stop"])
                self._close(rec, ts, symbol, opening, "stop (ouverture au-delà du stop)" if crossed else
                            f"signal {target['label']}")
            if target["side"] in ("LONG", "SHORT") and symbol not in s["positions"]:
                s["last_close"][symbol] = opening
                self._open(rec, ts, symbol, target["side"], opening, pending_atr or 0.0)
        # 2. stop inside the candle (reference fixes: checked on the fix itself)
        position = s["positions"].get(symbol)
        if position:
            low = candle.low if candle.low is not None else candle.close
            high = candle.high if candle.high is not None else candle.close
            stop = position["stop"]
            if position["side"] == "LONG" and low <= stop:
                # Opened below the stop: the fill is that worse open, otherwise the stop itself.
                self._close(rec, ts, symbol, opening if opening < stop else stop, "stop-loss")
            elif position["side"] == "SHORT" and high >= stop:
                self._close(rec, ts, symbol, opening if opening > stop else stop, "stop-loss")
        # 3. mark to market and the account limits
        s["last_close"][symbol] = candle.close
        equity = self.equity()
        s["equity"] = equity
        s["peak_equity"] = max(s["peak_equity"], equity)
        drawdown = (s["peak_equity"] - equity) / s["peak_equity"] * 100 if s["peak_equity"] > 0 else 0.0
        if not s["drawdown_halt"] and drawdown >= self.risk.max_drawdown_pct:
            s["drawdown_halt"] = True
            rec.events.append({"ts": _iso(ts), "kind": "ARRET_DRAWDOWN",
                               "message": f"drawdown {drawdown:.2f} % ≥ {self.risk.max_drawdown_pct} % :"
                                          " nouvelles entrées arrêtées"})
        day_loss = (s["day_start_equity"] - equity) / s["day_start_equity"] * 100 if s["day_start_equity"] > 0 else 0.0
        if not s["daily_halt"] and day_loss >= self.risk.daily_loss_limit_pct:
            s["daily_halt"] = True
            rec.events.append({"ts": _iso(ts), "kind": "LIMITE_JOURNALIERE",
                               "message": f"perte du jour {day_loss:.2f} % ≥ {self.risk.daily_loss_limit_pct} % :"
                                          " entrées bloquées jusqu'au prochain jour UTC"})
        rec.equity.append((_iso(ts), round(equity, 4)))
        # 4. decision at the close, executed at the next open
        decision = decide(history, self.strategy)
        if decision is None:
            return
        previous = s["last_decision"].get(symbol)
        if previous != decision.action:
            rec.signals.append({"ts": _iso(ts), "symbol": symbol, "decision": decision.action, "price": decision.price,
                                "indicators": decision.indicators, "conditions": decision.conditions,
                                "reason": decision.reason})
            s["last_decision"][symbol] = decision.action
        wanted = {BUY: "LONG", SELL: "SHORT", FLAT: "FLAT"}[decision.action]
        if wanted == "FLAT":
            s.setdefault("last_refusal", {}).pop(symbol, None)
        current = s["positions"].get(symbol, {}).get("side", "FLAT")
        if wanted != current:
            s["pending"][symbol] = {"side": wanted, "label": decision.action}
            s.setdefault("pending_atr", {})[symbol] = decision.indicators["atr"]

    def close_all(self, rec: Recorder, ts: datetime, reason: str) -> None:
        for symbol in list(self.state["positions"]):
            self._close(rec, ts, symbol, self.state["last_close"][symbol], reason)
        self.state["pending"].clear()
        self.state["equity"] = self.equity()


# -- metrics --------------------------------------------------------------------------------------

def metrics(initial: float, trades: list[dict[str, Any]], equity: list[tuple[str, float]], final: float) -> dict[str, Any]:
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trades if t["net_pnl"] < 0]
    peak, max_dd = initial, 0.0
    for _, value in equity:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100)
    gross = sum(t["gross_pnl"] for t in trades)
    costs = sum(t["costs"] for t in trades)
    return {
        "initial_capital": round(initial, 2), "final_capital": round(final, 2),
        "gross_pnl": round(gross, 2), "total_costs": round(costs, 2), "net_pnl": round(final - initial, 2),
        "trade_count": len(trades), "winning_trades": len(wins), "losing_trades": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else None,
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and wins else (None if not losses else 0.0),
        "max_drawdown_pct": round(max_dd, 3),
        "average_win": round(sum(wins) / len(wins), 2) if wins else None,
        "average_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "expectancy": round(sum(t["net_pnl"] for t in trades) / len(trades), 2) if trades else None,
    }


def downsample(points: list[tuple[str, float]], limit: int = MAX_EQUITY_POINTS) -> list[tuple[str, float]]:
    if len(points) <= limit:
        return points
    step = len(points) / (limit - 1)
    picked = [points[min(len(points) - 1, int(i * step))] for i in range(limit - 1)]
    return [*picked, points[-1]]


ASSUMPTIONS = (
    "Décision à la clôture d'une bougie avec les seules bougies déjà closes ; exécution à l'ouverture de la suivante.",
    "Coûts simulés, pas des coûts de courtier : demi-spread et slippage à chaque côté, commission par côté.",
    "Stop obligatoire à un multiple de l'ATR au moment de la décision ; un plus bas/plus haut qui l'atteint ferme au stop, "
    "une ouverture au-delà ferme à l'ouverture.",
    "Compte en USD ; gains et pertes des paires USD/JPY, USD/CHF, USD/CAD convertis au prix de sortie.",
    "Aucun swap, financement ni écart de marge réel ; levier fixe pour la marge.",
    "Positions ouvertes en fin de période fermées au dernier cours (motif fin_de_periode).",
    "Un ordre refusé par une limite de risque est enregistré une fois par motif, puis la limite reste appliquée.",
)
SYNTHETIC_ASSUMPTION = "Données SYNTHÉTIQUES : ce résultat ne décrit aucun marché réel."
REFERENCE_ASSUMPTION = ("Taux de référence quotidiens (un point par jour, sans plus haut ni plus bas) : les stops ne sont "
                        "vérifiés que sur le taux du jour, ce qui sous-estime le risque intrajournalier.")


# -- persistence ----------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, actor TEXT, status TEXT NOT NULL,
    symbol TEXT NOT NULL, timeframe TEXT NOT NULL, source TEXT NOT NULL, period_start TEXT, period_end TEXT,
    strategy TEXT NOT NULL, strategy_version TEXT NOT NULL, engine_version TEXT NOT NULL,
    params_json TEXT NOT NULL, data_json TEXT, metrics_json TEXT, equity_json TEXT, assumptions_json TEXT,
    error TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS lab_backtest_items (
    backtest_id INTEGER NOT NULL, kind TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, payload_json TEXT NOT NULL,
    PRIMARY KEY (backtest_id, kind, seq)
);
CREATE TABLE IF NOT EXISTS sim_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT,
    status TEXT NOT NULL, symbols_json TEXT NOT NULL, timeframe TEXT NOT NULL, source TEXT NOT NULL,
    start_at TEXT NOT NULL, params_json TEXT NOT NULL, state_json TEXT NOT NULL, last_processed_json TEXT NOT NULL,
    updated_at TEXT NOT NULL, stopped_at TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS sim_items (
    session_id INTEGER NOT NULL, kind TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, symbol TEXT,
    payload_json TEXT NOT NULL, PRIMARY KEY (session_id, kind, seq)
);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class LabStore:
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

    # backtests -----------------------------------------------------------------------------------

    def create_backtest(self, request: dict[str, Any], actor: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO lab_backtests(created_at, actor, status, symbol, timeframe, source, period_start, period_end,"
                " strategy, strategy_version, engine_version, params_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (_iso(_now()), actor, "QUEUED", request["symbol"], request["timeframe"], request["source"],
                 request.get("start"), request.get("end"), STRATEGY_NAME, STRATEGY_VERSION, ENGINE_VERSION,
                 _dumps(request)))
            return int(cursor.lastrowid)

    def fail_interrupted_backtests(self) -> int:
        with self._connect() as db:
            return db.execute("UPDATE lab_backtests SET status = 'FAILED', error = ?, finished_at = ? WHERE status = 'RUNNING'",
                              ("interrompu par un redémarrage du worker : relancez-le", _iso(_now()))).rowcount

    def set_backtest_status(self, backtest_id: int, status: str, error: str | None = None) -> None:
        with self._connect() as db:
            db.execute("UPDATE lab_backtests SET status = ?, error = ?, finished_at = CASE WHEN ? IN ('DONE','FAILED')"
                       " THEN ? ELSE finished_at END WHERE id = ?", (status, error, status, _iso(_now()), backtest_id))

    def save_backtest_result(self, backtest_id: int, result: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM lab_backtest_items WHERE backtest_id = ?", (backtest_id,))
            for kind in ("trades", "signals", "orders", "events"):
                db.executemany("INSERT INTO lab_backtest_items(backtest_id, kind, seq, ts, payload_json) VALUES (?,?,?,?,?)",
                               [(backtest_id, kind, i, item.get("ts") or item.get("exit_ts"), _dumps(item))
                                for i, item in enumerate(result[kind], start=1)])
            db.execute("UPDATE lab_backtests SET status = 'DONE', data_json = ?, metrics_json = ?, equity_json = ?,"
                       " assumptions_json = ?, finished_at = ?, error = NULL WHERE id = ?",
                       (_dumps(result["data"]), _dumps(result["metrics"]), _dumps(result["equity"]),
                        _dumps(result["assumptions"]), _iso(_now()), backtest_id))

    def backtests(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id, created_at, actor, status, symbol, timeframe, source, period_start, period_end,"
                              " strategy, strategy_version, metrics_json, error, finished_at FROM lab_backtests"
                              " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item.pop("metrics_json") or "null")
            out.append(item)
        return out

    def backtest(self, backtest_id: int, item_limit: int = 500) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM lab_backtests WHERE id = ?", (backtest_id,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            for name in ("params", "data", "metrics", "equity", "assumptions"):
                item[name] = json.loads(item.pop(f"{name}_json") or "null")
            for kind in ("trades", "signals", "orders", "events"):
                total = db.execute("SELECT COUNT(*) FROM lab_backtest_items WHERE backtest_id = ? AND kind = ?",
                                   (backtest_id, kind)).fetchone()[0]
                rows = db.execute("SELECT payload_json FROM lab_backtest_items WHERE backtest_id = ? AND kind = ?"
                                  " ORDER BY seq DESC LIMIT ?", (backtest_id, kind, item_limit)).fetchall()
                item[kind] = [json.loads(r[0]) for r in reversed(rows)]
                item[f"{kind}_total"] = total
        return item

    # simulation sessions -------------------------------------------------------------------------

    def create_session(self, request: dict[str, Any], actor: str) -> int:
        engine = Engine(request["strategy"], request["risk"], request["costs"])
        params = {"strategy": asdict(request["strategy"]), "risk": asdict(request["risk"]),
                  "costs": asdict(request["costs"]), "nature": request.get("nature", "UNKNOWN")}
        now = _iso(_now())
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO sim_sessions(name, created_at, actor, status, symbols_json, timeframe, source, start_at,"
                " params_json, state_json, last_processed_json, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (request["name"], now, actor, "RUNNING", _dumps(request["symbols"]), request["timeframe"],
                 request["source"], request["start"], _dumps(params), _dumps(engine.state), "{}", now))
            session_id = int(cursor.lastrowid)
            self._append(db, session_id, "events", [{"ts": now, "kind": "CREEE",
                                                     "message": f"simulation créée et démarrée par {actor}"}])
            return session_id

    def _append(self, db: sqlite3.Connection, session_id: int, kind: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        start = db.execute("SELECT COALESCE(MAX(seq), 0) FROM sim_items WHERE session_id = ? AND kind = ?",
                           (session_id, kind)).fetchone()[0]
        db.executemany("INSERT INTO sim_items(session_id, kind, seq, ts, symbol, payload_json) VALUES (?,?,?,?,?,?)",
                       [(session_id, kind, start + i, item.get("ts") or item.get("exit_ts"), item.get("symbol"),
                         _dumps(item)) for i, item in enumerate(items, start=1)])

    def set_session_status(self, session_id: int, status: str, actor: str) -> dict[str, Any]:
        if status not in ("RUNNING", "STOPPED"):
            raise SimulationError("statut inconnu")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM sim_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise SimulationError("simulation introuvable")
            if row["status"] == status:
                return {"id": session_id, "status": status, "changed": False}
            now = _iso(_now())
            db.execute("UPDATE sim_sessions SET status = ?, updated_at = ?, stopped_at = CASE WHEN ? = 'STOPPED' THEN ?"
                       " ELSE NULL END WHERE id = ?", (status, now, status, now, session_id))
            label = "arrêtée" if status == "STOPPED" else "relancée"
            self._append(db, session_id, "events", [{"ts": now, "kind": status, "message": f"simulation {label} par {actor}"}])
        return {"id": session_id, "status": status, "changed": True}

    def update_session_strategy(self, session_id: int, strategy: dict[str, Any], stop_atr_multiple: float,
                                note: str) -> bool:
        """New strategy parameters for a running paper account, from its next candle (positions are kept)."""

        validated = asdict(_build(StrategyParams, strategy))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT params_json FROM sim_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return False
            params = json.loads(row["params_json"])
            risk = asdict(_build(RiskParams, {**params["risk"], "stop_atr_multiple": stop_atr_multiple}))
            if params["strategy"] == validated and params["risk"] == risk:
                return False
            params["strategy"], params["risk"] = validated, risk
            db.execute("UPDATE sim_sessions SET params_json = ?, updated_at = ? WHERE id = ?",
                       (_dumps(params), _iso(_now()), session_id))
            self._append(db, session_id, "events", [{"ts": _iso(_now()), "kind": "NOUVEAU_CHAMPION", "message": note}])
            return True

    def sessions(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM sim_sessions ORDER BY id DESC").fetchall()
        return [self._session_summary(dict(row)) for row in rows]

    def _session_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        state = json.loads(row.pop("state_json"))
        params = json.loads(row.pop("params_json"))
        row["symbols"] = json.loads(row.pop("symbols_json"))
        row["last_processed"] = json.loads(row.pop("last_processed_json"))
        row["params"] = params
        row["nature"] = params.get("nature", "UNKNOWN")
        initial = params["risk"]["initial_capital"]
        row["account"] = {"cash": round(state["cash"], 2), "equity": round(state["equity"], 2),
                          "initial_capital": initial, "net_pnl": round(state["equity"] - initial, 2),
                          "peak_equity": round(state["peak_equity"], 2), "drawdown_halt": state["drawdown_halt"],
                          "daily_halt": state["daily_halt"], "positions": state["positions"],
                          "pending": state["pending"], "day": state["day"]}
        return row

    def session(self, session_id: int, item_limit: int = 300) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sim_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            summary = self._session_summary(dict(row))
            for kind in ("signals", "orders", "trades", "events", "equity"):
                limit = MAX_EQUITY_POINTS if kind == "equity" else item_limit
                total = db.execute("SELECT COUNT(*) FROM sim_items WHERE session_id = ? AND kind = ?",
                                   (session_id, kind)).fetchone()[0]
                rows = db.execute("SELECT payload_json FROM sim_items WHERE session_id = ? AND kind = ? ORDER BY seq DESC"
                                  " LIMIT ?", (session_id, kind, limit)).fetchall()
                summary[kind] = [json.loads(r[0]) for r in reversed(rows)]
                summary[f"{kind}_total"] = total
        return summary

    def step_session(self, session_id: int, candles_store: CandleStore, max_bars: int = 5000) -> dict[str, Any]:
        """Process the candles stored since the last step, in one transaction (idempotent, restart-safe)."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sim_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None or row["status"] != "RUNNING":
                return {"processed": 0}
            params = json.loads(row["params_json"])
            engine = Engine(_build(StrategyParams, params["strategy"]), _build(RiskParams, params["risk"]),
                            _build(CostParams, params["costs"]), json.loads(row["state_json"]))
            last = json.loads(row["last_processed_json"])
            start = _parse_iso(row["start_at"])
            symbols = json.loads(row["symbols_json"])
            newest = [candles_store.candles(sym, row["timeframe"], row["source"], limit=1, newest=True) for sym in symbols]
            if not all(newest):
                note = "en attente : " + ", ".join(s for s, f in zip(symbols, newest, strict=True) if not f) + " sans bougie"
                db.execute("UPDATE sim_sessions SET last_error = ? WHERE id = ?", (note, session_id))
                return {"processed": 0, "waiting": note}
            watermark = min(found[0].timestamp for found in newest)
            lagging = [s for s, f in zip(symbols, newest, strict=True) if f[0].timestamp == watermark]
            ahead = any(f[0].timestamp > watermark for f in newest)
            waiting_note = (f"en attente de données récentes pour {', '.join(lagging)} (dernière bougie {_iso(watermark)}) :"
                            " les autres paires attendent" if ahead else None)
            work: list[tuple[datetime, Candle, list[Candle]]] = []
            for symbol in symbols:
                since = _parse_iso(last[symbol]) if symbol in last else None
                warm = candles_store.candles(symbol, row["timeframe"], row["source"],
                                             end=since or start, limit=engine.strategy.warmup + 1, newest=True)
                fresh = candles_store.candles(symbol, row["timeframe"], row["source"], start=since or start,
                                              limit=max_bars + 1)
                fresh = [c for c in fresh if (since is None or c.timestamp > since) and start <= c.timestamp <= watermark]
                history = [c for c in warm if not fresh or c.timestamp < fresh[0].timestamp]
                for candle in fresh[:max_bars]:
                    history = [*history[-(engine.strategy.warmup + 2):], candle]
                    work.append((candle.timestamp, candle, history))
            work.sort(key=lambda item: (item[0], item[1].symbol))
            rec = Recorder()
            for _, candle, history in work[:max_bars]:
                engine.process(candle, history, rec)
                last[candle.symbol] = _iso(candle.timestamp)
            for kind in ("signals", "orders", "trades", "events"):
                self._append(db, session_id, kind, getattr(rec, kind))
            # Equity points: every candle of a live step, a sample of a long catch-up (the account state is exact).
            self._append(db, session_id, "equity", [{"ts": ts, "equity": value} for ts, value in downsample(rec.equity, 200)])
            db.execute("UPDATE sim_sessions SET state_json = ?, last_processed_json = ?, updated_at = ?, last_error = ?"
                       " WHERE id = ?", (_dumps(engine.state), _dumps(last), _iso(_now()), waiting_note, session_id))
            return {"processed": len(work[:max_bars]), "orders": len(rec.orders), "trades": len(rec.trades)}

    def running_sessions(self) -> list[int]:
        with self._connect() as db:
            return [r[0] for r in db.execute("SELECT id FROM sim_sessions WHERE status = 'RUNNING' ORDER BY id")]

    def session_error(self, session_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE sim_sessions SET last_error = ?, updated_at = ? WHERE id = ?",
                       (error[:500], _iso(_now()), session_id))


# -- requests and the backtest runner --------------------------------------------------------------

def _period(value: Any, label: str, *, end: bool = False) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SimulationError(f"{label} : date invalide (AAAA-MM-JJ)") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if end and len(text) == 10:  # a date alone: the whole day is included
        moment += timedelta(days=1, microseconds=-1)
    return _iso(moment)


def backtest_request(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a backtest request (from the web form or the CLI) into a reproducible parameter set."""

    if (raw.get("strategy") or STRATEGY_NAME) != STRATEGY_NAME:
        raise SimulationError("stratégie inconnue")
    from .marketdata import canonical_symbol, canonical_timeframe

    try:
        symbol, timeframe = canonical_symbol(raw.get("symbol", "")), canonical_timeframe(raw.get("timeframe", ""))
    except MarketDataError as exc:
        raise SimulationError(str(exc)) from exc
    source = str(raw.get("source") or "").strip()
    if not source:
        raise SimulationError("source de données obligatoire")
    start, end = _period(raw.get("start"), "début"), _period(raw.get("end"), "fin", end=True)
    if start and end and start >= end:
        raise SimulationError("la fin doit suivre le début")
    strategy = _build(StrategyParams, raw.get("strategy_params"))
    risk = _build(RiskParams, raw.get("risk"))
    costs = _build(CostParams, raw.get("costs"))
    return {"symbol": symbol, "timeframe": timeframe, "source": source, "start": start, "end": end,
            "strategy": STRATEGY_NAME, "strategy_version": STRATEGY_VERSION,
            "strategy_params": asdict(strategy), "risk": asdict(risk), "costs": asdict(costs)}


def run_backtest(candles_store: CandleStore, request: dict[str, Any]) -> dict[str, Any]:
    """Deterministic backtest over stored candles (same inputs, same outputs)."""

    strategy = _build(StrategyParams, request["strategy_params"])
    risk = _build(RiskParams, request["risk"])
    costs = _build(CostParams, request["costs"])
    start = _parse_iso(request["start"]) if request.get("start") else None
    end = _parse_iso(request["end"]) if request.get("end") else None
    sym, tf, src = request["symbol"], request["timeframe"], request["source"]
    period = candles_store.candles(sym, tf, src, start=start, end=end)
    if len(period) < 2:
        days = math.ceil(TIMEFRAMES[tf] * (strategy.warmup + 30) / 86400)
        raise SimulationError(f"pas assez de bougies {sym} {tf} ({src}) sur la période : {len(period)}. Choisissez une période "
                              f"plus longue (au moins {days} jours en {tf}) ou laissez les dates vides pour tout l'historique")
    warm = candles_store.candles(sym, tf, src, end=period[0].timestamp, limit=strategy.warmup + 1, newest=True)
    warm = [c for c in warm if c.timestamp < period[0].timestamp]
    engine, rec = Engine(strategy, risk, costs), Recorder()
    history: list[Candle] = list(warm)
    for candle in period:
        history.append(candle)
        if len(history) > strategy.warmup + 2:
            del history[0]
        engine.process(candle, history, rec)
    engine.close_all(rec, period[-1].timestamp, "fin_de_periode")
    final = engine.state["cash"]
    if rec.equity:
        rec.equity[-1] = (rec.equity[-1][0], round(final, 4))
    natures = {c.kind for c in period}
    gaps = find_gaps([c.timestamp for c in period], tf)
    data_nature = (candles_store.source(src) or {}).get("nature") or "UNKNOWN"
    assumptions = list(ASSUMPTIONS)
    if KIND_REFERENCE in natures:
        assumptions.append(REFERENCE_ASSUMPTION)
    if data_nature == "SYNTHETIC":
        assumptions.insert(0, SYNTHETIC_ASSUMPTION)
    if len(warm) < strategy.warmup:
        assumptions.append(f"Seulement {len(warm)} bougies avant le début : les premières bougies de la période servent au "
                           f"calcul des indicateurs ({strategy.warmup} nécessaires).")
    data = {"symbol": sym, "timeframe": tf, "source": src, "nature": data_nature, "kinds": sorted(natures),
            "candles": len(period),
            "warmup_candles": len(warm), "first": _iso(period[0].timestamp), "last": _iso(period[-1].timestamp),
            "gaps": len(gaps), "missing_bars": sum(g["missing"] for g in gaps),
            "fingerprint_sha256": candles_store.fingerprint([*warm, *period]),
            "with_volume": sum(c.volume is not None for c in period)}
    return {"data": data, "metrics": metrics(risk.initial_capital, rec.trades, rec.equity, final),
            "equity": downsample(rec.equity), "assumptions": assumptions, "trades": rec.trades,
            "signals": rec.signals, "orders": rec.orders, "events": rec.events,
            "versions": {"engine": ENGINE_VERSION, "strategy": STRATEGY_VERSION}}


def simulation_request(raw: dict[str, Any], candles_store: CandleStore) -> dict[str, Any]:
    from .marketdata import canonical_symbol, canonical_timeframe

    symbols = raw.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [s for s in symbols.replace(";", ",").split(",") if s.strip()]
    try:
        symbols = sorted({canonical_symbol(s) for s in symbols})
        timeframe = canonical_timeframe(raw.get("timeframe", ""))
    except MarketDataError as exc:
        raise SimulationError(str(exc)) from exc
    if not symbols:
        raise SimulationError("au moins une paire")
    source = str(raw.get("source") or "").strip()
    available = {(c["symbol"], c["timeframe"], c["source"]) for c in candles_store.coverage()}
    missing = [s for s in symbols if (s, timeframe, source) not in available]
    if missing:
        raise SimulationError(f"aucune donnée {timeframe} de la source {source!r} pour : {', '.join(missing)}")
    start = _period(raw.get("start"), "début") or _iso(_now())
    name = str(raw.get("name") or "").strip()[:80] or f"Simulation {', '.join(symbols)} {timeframe}"
    nature = (candles_store.source(source) or {}).get("nature") or "UNKNOWN"
    return {"name": name, "nature": nature, "symbols": symbols, "timeframe": timeframe, "source": source, "start": start,
            "strategy": _build(StrategyParams, raw.get("strategy_params")), "risk": _build(RiskParams, raw.get("risk")),
            "costs": _build(CostParams, raw.get("costs"))}


def strategy_catalog() -> dict[str, Any]:
    return {"strategies": STRATEGIES, "risk_defaults": asdict(RiskParams()), "cost_defaults": asdict(CostParams()),
            "timeframes": list(TIMEFRAMES), "pairs": list(PAIRS)}
