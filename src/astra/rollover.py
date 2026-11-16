"""Rollover verdict (LOT 3, master document 5.3, mandate 5.5): one pre-registered experiment, decided, not pursued.

Data: hourly summaries of the broker's real bid/ask ticks (first and last bid and ask of each UTC hour, median and
90th percentile spread), read back from the terminal's tick history by the bridge and, from LOT 1 on, from the
recorded ticks. Prices are executable: a long position over an hour buys at the first ask and sells at the last bid;
a short sells at the first bid and buys back at the last ask.

The rollover is the server's midnight (MetaQuotes-Demo: Europe/Athens), so 22:00 UTC in winter and 21:00 UTC in
summer, computed for each day from the zone and its clock changes, never set by hand. The window is the two hours
before it and the two hours from it (in winter 20:00 to 23:59 UTC, the hours the first study pointed at).

Rule, fixed before the test and written in the sheet: on the study period (the first 70 % of the days), each
instrument and window hour gets the sign of its mean mid move when |t| >= 2, else no position. On the sealed period
(the last 30 %, never used before), those positions are held each day at executable prices; the daily portfolio return
is the mean of the positions taken. Criterion: annualised net Sharpe above 1 (PRESET), otherwise the track is closed.

Under ``SIX_MONTHS_DAYS`` days the verdict is provisional; ``maybe_reevaluate`` registers and runs a new sheet once,
when six months exist.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import __version__
from .evidence import EvidenceError, EvidenceRegistry, sha_of
from .marketdata import INSTRUMENTS

ZONE = ZoneInfo("Europe/Athens")  # MetaQuotes-Demo server clock (same as brokers.mt5_demo.SERVER_ZONES)
OFFSETS = (-2, -1, 0, 1)          # window hours, relative to the rollover hour
T_MIN = 2.0
STUDY_SHARE = 0.7
SHARPE_MIN = 1.0                  # PRESET (master document 5.3)
SIX_MONTHS_DAYS = 126             # trading days in about six months
DAYS_PER_YEAR = 252
TITLE = "Rollover 20-23 UTC"


def rollover_hour_utc(day: datetime) -> int:
    """UTC hour of the server's midnight on that day."""

    local_midnight = datetime(day.year, day.month, day.day, tzinfo=ZONE)
    return local_midnight.astimezone(UTC).hour


def hourly_from_ticks(symbol: str, ticks: list[tuple[int, int, float, float, int]]) -> list[dict[str, Any]]:
    """Ticks (raw ms, UTC ms, bid, ask, flags) to one row per UTC hour with its first and last bid and ask."""

    pip = INSTRUMENTS[symbol].pip
    groups: dict[str, list[tuple[int, float, float]]] = {}
    for _, utc_ms, bid, ask, _ in sorted(ticks, key=lambda t: t[1]):
        hour = datetime.fromtimestamp(utc_ms / 1000, UTC).replace(minute=0, second=0, microsecond=0).isoformat()
        groups.setdefault(hour, []).append((utc_ms, bid, ask))
    rows = []
    for hour, items in sorted(groups.items()):
        spreads = sorted((a - b) / pip for _, b, a in items)
        rows.append({"symbol": symbol, "hour": hour, "first_bid": items[0][1], "first_ask": items[0][2],
                     "last_bid": items[-1][1], "last_ask": items[-1][2],
                     "median_spread_pips": round(statistics.median(spreads), 4),
                     "p90_spread_pips": round(spreads[min(len(spreads) - 1, int(0.9 * len(spreads)))], 4),
                     "ticks": len(items)})
    return rows


def position_return(row: dict[str, Any], side: int) -> float:
    if side > 0:
        return row["last_bid"] / row["first_ask"] - 1
    if side < 0:
        return row["first_bid"] / row["last_ask"] - 1
    return 0.0


def _mid_move(row: dict[str, Any]) -> float:
    start = (row["first_bid"] + row["first_ask"]) / 2
    return ((row["last_bid"] + row["last_ask"]) / 2) / start - 1


def _offset(row: dict[str, Any]) -> int | None:
    moment = datetime.fromisoformat(row["hour"])
    for delta in OFFSETS:
        if moment.hour == (rollover_hour_utc(moment - timedelta(hours=delta)) + delta) % 24:
            return delta
    return None


def _days(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({r["hour"][:10] for r in rows})


def split(rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    days = _days(rows)
    cut = max(1, int(len(days) * STUDY_SHARE))
    return days[:cut], days[cut:]


def choose(rows: list[dict[str, Any]], study_days: set[str]) -> dict[str, int]:
    """Sign per instrument and window offset from the study period only (|t| >= T_MIN), else no position."""

    moves: dict[str, list[float]] = {}
    for r in rows:
        if r["hour"][:10] in study_days:
            off = _offset(r)
            if off is not None:
                moves.setdefault(f"{r['symbol']}|{off}", []).append(_mid_move(r))
    rule = {}
    for key, values in sorted(moves.items()):
        if len(values) >= 20 and statistics.pstdev(values) > 0:
            t = statistics.fmean(values) / (statistics.pstdev(values) / math.sqrt(len(values)))
            if abs(t) >= T_MIN:
                rule[key] = 1 if t > 0 else -1
    return rule


def evaluate(rows: list[dict[str, Any]], rule: dict[str, int], sealed_days: set[str]) -> dict[str, Any]:
    daily: dict[str, list[float]] = {}
    for r in rows:
        if r["hour"][:10] not in sealed_days:
            continue
        off = _offset(r)
        side = rule.get(f"{r['symbol']}|{off}", 0) if off is not None else 0
        if side:
            daily.setdefault(r["hour"][:10], []).append(position_return(r, side))
    series = [statistics.fmean(daily.get(d, [0.0]) or [0.0]) for d in sorted(sealed_days)]
    sd = statistics.pstdev(series) if len(series) > 1 else 0.0
    sharpe = statistics.fmean(series) / sd * math.sqrt(DAYS_PER_YEAR) if sd > 0 else 0.0
    return {"metrics": {"net_sharpe": round(sharpe, 4),
                        "net_pct_year": round(statistics.fmean(series) * DAYS_PER_YEAR * 100, 4) if series else 0.0,
                        "days": len(series), "positions": sum(len(v) for v in daily.values())},
            "prices": "exécutables : achat à l'Ask, vente au Bid, écart réel de chaque heure"}


def _sheet(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    study, sealed = split(rows)
    if not sealed:
        raise EvidenceError("historique trop court pour une période scellée")
    end = (datetime.fromisoformat(sealed[-1]) + timedelta(days=1)).replace(tzinfo=UTC).isoformat()
    return {"title": f"{TITLE} ({label})",
            "economic_reason": "au rollover, les fournisseurs de liquidité élargissent l'écart et règlent le financement "
                               "de nuit : un mouvement de prix récurrent peut exister, mais il est peut-être payé "
                               "entièrement par l'écart. Test unique pour trancher.",
            "source": "ticks Bid/Ask du terminal MT5 (MetaQuotes-Demo, NON_REPRESENTATIF d'un courtier)",
            "universe": sorted({r["symbol"] for r in rows}), "horizon": "1 heure, fenêtre de 4 heures autour du rollover",
            "study_period": [f"{study[0]}T00:00:00+00:00", f"{sealed[0]}T00:00:00+00:00"],
            "sealed_test_period": [f"{sealed[0]}T00:00:00+00:00", end],
            "parameters": {"offsets": list(OFFSETS), "t_min": T_MIN, "study_share": STUDY_SHARE},
            "costs": "écart réel de chaque heure, entrée à l'Ask et sortie au Bid ; pas de commission sur ce compte",
            "expected_result": "Sharpe net annualisé > 1 sur la période scellée",
            "abandon_criterion": "Sharpe net annualisé <= 1 sur la période scellée : piste close",
            "abandon_rule": {"metric": "net_sharpe", "op": "<=", "threshold": SHARPE_MIN},
            "planned_variants": 1, "code_version": __version__, "data_sha256": sha_of(rows)}


def run_final(registry: EvidenceRegistry, hid: int, rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    study, sealed = split(rows)
    rule = choose(rows, set(study))
    return registry.record_experiment(hid, "FINAL", {"offsets": list(OFFSETS), "t_min": T_MIN},
                                      sha_of(rows), {**evaluate(rows, rule, set(sealed)), "rule": rule}, now)


def run_experiment(registry: EvidenceRegistry, rows: list[dict[str, Any]], now: datetime,
                   label: str = "provisoire") -> dict[str, Any]:
    """Register the sheet, record the study (the positions chosen), open the sealed period once, give the verdict."""

    sheet = _sheet(rows, label)
    hid = registry.register(sheet, now)
    study, _ = split(rows)
    rule = choose(rows, set(study))
    params = {"offsets": list(OFFSETS), "t_min": T_MIN}
    registry.record_experiment(hid, "STUDY", params, sheet["data_sha256"], {"rule": rule, "study_days": len(study)}, now)
    final = run_final(registry, hid, rows, now)
    return {"hypothesis_id": hid, "final_run_id": final["run_id"], "rule": rule,
            "metrics": final["result"]["metrics"], "verdict": "PISTE_CLOSE" if final["abandon"] else "PISTE_OUVERTE",
            "provisional": len(_days(rows)) < SIX_MONTHS_DAYS,
            "study_period": sheet["study_period"], "sealed_period": sheet["sealed_test_period"],
            "costs": sheet["source"]}


def replay(registry: EvidenceRegistry, rows: list[dict[str, Any]], run_id: int) -> dict[str, Any]:
    study, sealed = split(rows)
    rule = choose(rows, set(study))
    digest = sha_of({**evaluate(rows, rule, set(sealed)), "rule": rule})
    return {"run_id": run_id, "identical": digest == registry.recorded_sha(run_id)}


def maybe_reevaluate(registry: EvidenceRegistry, rows: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Once six months of hourly rows exist, register and run the six-month sheet, once."""

    if len(_days(rows)) < SIX_MONTHS_DAYS or registry.find(f"{TITLE} (6 mois)"):
        return None
    return run_experiment(registry, rows, now, label="6 mois")
