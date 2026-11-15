"""Hour-of-day study with the real, executable spread (research only, read only, never trades).

The history showed a strong hour-of-day pattern around the daily rollover (20:00 to 23:59 UTC) on most instruments. Bars
built from quotes can show such a pattern while it cannot be traded: the spread widens at the rollover. This module
answers the question with the bid and ask the bridge records each minute:

1. ``spread_by_hour``: median and 90th percentile spread, in pips, per UTC hour and instrument;
2. ``hour_effect``: hours chosen on the first half of the H1 history (|t| >= T_MIN), traded on the second half only,
   each change of position paying the median spread measured at that hour (a full spread per round trip).

Verdict per instrument: an effect counts only when its out-of-sample net result stays positive with the measured
spreads. Nothing here feeds the bridge: a strategy built on it would still go through learning and the risk gateway.
"""

from __future__ import annotations

import itertools
import math
import statistics
from datetime import datetime
from typing import Any

from .marketdata import INSTRUMENTS, CandleStore

T_MIN = 3.0
MIN_QUOTE_DAYS = 5      # below this, the measured spreads do not cover each hour of the week enough times
BARS_PER_YEAR = 6200    # hourly bars of a forex year (about 24 x 5 x 52)
HISTORY_SOURCE = "MASSIVE_FX_QUOTES"


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def spread_by_hour(quotes: list[tuple[str, float, float]], symbol: str) -> dict[int, dict[str, float]]:
    """Median and 90th percentile spread in pips for each UTC hour, from (minute, bid, ask) rows."""

    pip = INSTRUMENTS[symbol].pip
    by_hour: dict[int, list[float]] = {}
    for minute, bid, ask in quotes:
        by_hour.setdefault(int(minute[11:13]), []).append((ask - bid) / pip)
    return {hour: {"median": round(statistics.median(values), 2), "p90": round(_percentile(values, 0.9), 2),
                   "samples": len(values)} for hour, values in sorted(by_hour.items())}


def _sharpe(returns: list[float]) -> float | None:
    if len(returns) < 30 or statistics.pstdev(returns) == 0:
        return None
    return round(statistics.fmean(returns) / statistics.pstdev(returns) * math.sqrt(BARS_PER_YEAR), 2)


def hour_effect(candles: CandleStore, symbol: str, spreads: dict[int, dict[str, float]],
                t_min: float = T_MIN) -> dict[str, Any]:
    """Hours chosen on the first half, traded on the second half with the measured spread of each hour."""

    spec = INSTRUMENTS[symbol]
    series = candles.candles(symbol, "H1", HISTORY_SOURCE)
    if len(series) < 3000:
        return {"symbol": symbol, "verdict": "HISTORIQUE_INSUFFISANT", "bars": len(series)}
    moves = [(bar.timestamp, bar.close / before.close - 1, bar.close) for before, bar in itertools.pairwise(series)]
    mid = len(moves) // 2
    by_hour: dict[int, list[float]] = {}
    for stamp, move, _ in moves[:mid]:
        by_hour.setdefault(stamp.hour, []).append(move)
    rule = {}
    for hour, values in by_hour.items():
        sd = statistics.pstdev(values) if len(values) >= 50 else 0
        if sd > 0:
            t = statistics.fmean(values) / (sd / math.sqrt(len(values)))
            if abs(t) >= t_min:
                rule[hour] = 1 if t > 0 else -1
    if not rule:
        return {"symbol": symbol, "verdict": "AUCUNE_HEURE", "hours": {}}
    fallback = max((h["median"] for h in spreads.values()), default=spec.spread_pips)
    gross, net, previous = [], [], 0
    for stamp, move, price in moves[mid:]:
        position = rule.get(stamp.hour, 0)
        cost_pips = (spreads.get(stamp.hour) or {}).get("median", fallback)
        cost = abs(position - previous) * cost_pips * spec.pip / 2 / price
        gross.append(position * move)
        net.append(position * move - cost)
        previous = position
    net_year = statistics.fmean(net) * BARS_PER_YEAR * 100
    return {"symbol": symbol, "hours": dict(sorted(rule.items())),
            "gross_pct_year": round(statistics.fmean(gross) * BARS_PER_YEAR * 100, 2), "gross_sharpe": _sharpe(gross),
            "net_pct_year": round(net_year, 2), "net_sharpe": _sharpe(net),
            "spread_used": {h: (spreads.get(h) or {}).get("median", fallback) for h in sorted(rule)},
            "verdict": "EFFET_REEL" if net_year > 0 and (_sharpe(net) or 0) > 0.5 else "ARTEFACT_OU_TROP_CHER"}


def study(candles: CandleStore, history: Any, now: datetime) -> dict[str, Any]:
    """The whole study: spreads per hour and the hour effect with them, per instrument. ``history`` is an Mt5View."""

    report: dict[str, Any] = {"generated_at": now.isoformat(), "t_min": T_MIN, "instruments": []}
    for symbol in INSTRUMENTS:
        quotes = history.quote_history(symbol)
        days = len({minute[:10] for minute, _, _ in quotes})
        entry: dict[str, Any] = {"symbol": symbol, "quote_days": days, "quote_minutes": len(quotes)}
        if days < MIN_QUOTE_DAYS:
            entry["verdict"] = "COTATIONS_INSUFFISANTES"
            entry["needed_days"] = MIN_QUOTE_DAYS
        else:
            spreads = spread_by_hour(quotes, symbol)
            entry["spread_by_hour"] = spreads
            entry.update(hour_effect(candles, symbol, spreads))
        report["instruments"].append(entry)
    verdicts = [i["verdict"] for i in report["instruments"]]
    report["summary"] = {v: verdicts.count(v) for v in sorted(set(verdicts))}
    return report
