"""Risk-adjusted performance measures and market regimes, computed from series the platform already stores.

Pure functions, no I/O. Every measure says "not computable" (``None``) when the sample is too short, instead of
returning a flattering or arbitrary number. Ratios are annualised with 252 trading days and are descriptive: they say
nothing about the future, and a short DEMO history gives very wide uncertainty."""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from datetime import datetime
from typing import Any

TRADING_DAYS = 252
MIN_RETURNS = 10  # fewer daily returns than this: no ratio is reported
EFFICIENCY_WINDOW = 20
TREND_EFFICIENCY = 0.30  # Kaufman efficiency ratio above this: directional market
VOLATILITY_HISTORY = 60  # bars used to judge whether the latest volatility is high or low

DEFINITIONS = {
    "sharpe": "moyenne des rendements journaliers / écart-type, annualisée (252 jours), sans taux sans risque",
    "sortino": "moyenne des rendements journaliers / écart-type des seuls rendements négatifs, annualisée",
    "calmar": "rendement annualisé / baisse maximale des fonds propres",
    "max_drawdown_pct": "plus forte baisse des fonds propres depuis un sommet, en % du sommet",
    "volatility_pct": "écart-type des rendements journaliers annualisé, en %",
}


def daily_closes(series: Sequence[tuple[str, float]]) -> list[tuple[str, float]]:
    """The last value of each UTC day from ``(iso timestamp, value)`` pairs in time order."""

    last: dict[str, float] = {}
    for stamp, value in series:
        if value is None or not math.isfinite(value):
            continue
        last[datetime.fromisoformat(stamp).date().isoformat()] = float(value)
    return sorted(last.items())


def max_drawdown_pct(values: Sequence[float]) -> float | None:
    if not values:
        return None
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100)
    return worst


def measures(series: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """Sharpe, Sortino, Calmar, drawdown and volatility of an equity series (intraday points are folded by day)."""

    days = daily_closes(series)
    values = [v for _day, v in days]
    result: dict[str, Any] = {"days": len(days), "sharpe": None, "sortino": None, "calmar": None,
                              "volatility_pct": None, "max_drawdown_pct": None, "computable": False,
                              "definitions": DEFINITIONS}
    if values:
        result["max_drawdown_pct"] = round(max_drawdown_pct(values) or 0.0, 3)
    returns = [b / a - 1 for a, b in itertools.pairwise(values) if a > 0]
    if len(returns) < MIN_RETURNS:
        result["note"] = f"{len(returns)} rendement(s) journalier(s) : au moins {MIN_RETURNS} sont nécessaires pour les ratios"
        return result
    mean = statistics.fmean(returns)
    spread = statistics.stdev(returns)
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    scale = math.sqrt(TRADING_DAYS)
    result["computable"] = True
    result["volatility_pct"] = round(spread * scale * 100, 3)
    if spread > 0:
        result["sharpe"] = round(mean / spread * scale, 3)
    if downside > 0:
        result["sortino"] = round(mean / downside * scale, 3)
    drawdown = result["max_drawdown_pct"]
    total = values[-1] / values[0] - 1
    annual = (1 + total) ** (TRADING_DAYS / len(returns)) - 1 if total > -1 else -1.0
    if drawdown:
        result["calmar"] = round(annual * 100 / drawdown, 3)
    result["annualised_return_pct"] = round(annual * 100, 3)
    return result


def regime(closes: Sequence[float]) -> dict[str, Any]:
    """Market regime of the latest bar: directional or ranging, and whether volatility is high or low.

    Direction uses Kaufman's efficiency ratio (net move over the sum of absolute moves, 20 bars): near 1 the price
    went somewhere, near 0 it went back and forth. Volatility compares the latest 20-bar range of closes with
    the median of the previous 60 bars."""

    need = EFFICIENCY_WINDOW + 1
    if len(closes) < need:
        return {"trend": None, "volatility": None, "efficiency": None,
                "note": f"{len(closes)} bougie(s) : au moins {need} sont nécessaires"}
    window = list(closes[-need:])
    path = sum(abs(b - a) for a, b in itertools.pairwise(window))
    efficiency = abs(window[-1] - window[0]) / path if path else 0.0
    steps = [abs(b / a - 1) for a, b in itertools.pairwise(closes) if a]
    latest = statistics.fmean(steps[-EFFICIENCY_WINDOW:])
    history = steps[-(VOLATILITY_HISTORY + EFFICIENCY_WINDOW):-EFFICIENCY_WINDOW]
    level = None
    if len(history) >= EFFICIENCY_WINDOW:
        level = "haute" if latest > statistics.median(history) else "basse"
    return {"trend": "tendance" if efficiency >= TREND_EFFICIENCY else "range", "volatility": level,
            "efficiency": round(efficiency, 3), "direction": "hausse" if window[-1] > window[0] else "baisse"}
