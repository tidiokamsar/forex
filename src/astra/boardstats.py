"""Separate timestamps of the dashboard: a fresh quote does not mean the strategy was evaluated again.

Each line says what it measures, when it last happened, how old that is and whether it is late for what that process is
supposed to do. Pure function: the caller reads the stores and passes the raw times.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .marketdata import forex_market_closed

QUOTE_MAX_AGE = 60               # seconds: the bridge streams every 5 s
SNAPSHOT_MAX_AGE = 15 * 60
HISTORY_MAX_AGE = 5 * 60         # synced every minute
H1_MAX_AGE = 2 * 3600 + 600      # an H1 candle closes every hour, with ten minutes of margin
ECB_PUBLISH_DELAY = timedelta(hours=3)  # the ECB reference is published about 14:15 CET; the worker polls every 6 h


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def next_ecb_expected(last_candle: datetime) -> datetime:
    """When the next daily ECB candle is expected: the next weekday after the last one, at 13:15 UTC, plus the delay."""

    day = last_candle.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, 13, 15, tzinfo=UTC) + ECB_PUBLISH_DELAY


def _line(key: str, label: str, at: str | None, now: datetime, state: str, note: str) -> dict[str, Any]:
    moment = _parse(at)
    return {"key": key, "label": label, "at": moment.isoformat() if moment else None,
            "age_seconds": None if moment is None else max(0, int((now - moment).total_seconds())), "state": state, "note": note}


def freshness(now: datetime, *, quote_at: str | None, snapshot_at: str | None, history_synced_at: str | None,
              mt5_candle_at: str | None, strategy_candle_at: str | None, strategy_evaluated_at: str | None,
              last_signal_at: str | None, last_attempt_at: str | None, last_confirmed_at: str | None, worker_state: str | None,
              stop_file: bool) -> list[dict[str, Any]]:
    """The lines of the "Fraîcheur" panel, in the order of the decision chain."""

    def judged(at: str | None, max_age: float, late: str, ok: str = "à jour") -> tuple[str, str]:
        moment = _parse(at)
        if moment is None:
            return "inconnu", "jamais reçu"
        return ("ok", ok) if (now - moment).total_seconds() <= max_age else ("retard", late)

    out = []
    state, note = judged(quote_at, QUOTE_MAX_AGE, "flux interrompu")
    if state == "retard" and forex_market_closed(now):
        state, note = "ok", "marché fermé"
    out.append(_line("quote", "Dernière cotation reçue", quote_at, now, state, note))
    state, note = judged(mt5_candle_at, H1_MAX_AGE, "bougie H1 en retard")
    if state == "retard" and forex_market_closed(now):
        state, note = "ok", "marché fermé"
    out.append(_line("candle", "Dernière bougie H1 clôturée (MT5)", mt5_candle_at, now, state, note))
    last_strategy = _parse(strategy_candle_at)
    if last_strategy is None:
        state, note = "inconnu", "aucune bougie traitée par la stratégie"
    else:
        expected = next_ecb_expected(last_strategy).strftime("%d/%m %H:%M UTC")
        late = now > next_ecb_expected(last_strategy)
        state = "retard" if late else "ok"
        note = (f"nouvelle bougie quotidienne attendue depuis {expected}" if late
                else f"bougie quotidienne : prochaine attendue après {expected}")
    out.append(_line("strategy_candle", "Dernière bougie traitée par la stratégie (jour)", strategy_candle_at, now, state, note))
    ok_worker = worker_state == "OK"
    out.append(_line("strategy_eval", "Dernière évaluation de stratégie", strategy_evaluated_at, now,
                     "ok" if ok_worker else "retard", "worker actif" if ok_worker else f"worker : {worker_state or 'inconnu'}"))
    out.append(_line("signal", "Dernier signal", last_signal_at, now, "info" if last_signal_at else "inconnu",
                     "un signal n'apparaît que lorsque la décision change"))
    out.append(_line("attempt", "Dernière tentative d'exécution", last_attempt_at, now, "info" if last_attempt_at else "inconnu",
                     "ouverture, fermeture ou refus du portail de risque"))
    out.append(_line("confirmed", "Dernière exécution confirmée (courtier)", last_confirmed_at, now,
                     "info" if last_confirmed_at else "inconnu", "transaction de l'historique du courtier"))
    state, note = judged(snapshot_at, SNAPSHOT_MAX_AGE, "pont MT5 silencieux")
    if stop_file:
        note = "ordres suspendus (fichier STOP)"
    out.append(_line("bridge", "Dernier message du pont MT5", snapshot_at, now, state, note))
    state, note = judged(history_synced_at, HISTORY_MAX_AGE, "historique du courtier non synchronisé")
    out.append(_line("history", "Dernière synchronisation de l'historique", history_synced_at, now, state, note))
    return out
