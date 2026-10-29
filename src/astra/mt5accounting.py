"""Single, documented accounting of the MT5 DEMO account (see docs/MT5_ACCOUNTING.md).

Every figure shown by the dashboard comes from this module, computed on the server from what the broker reported
(the account snapshot, the deal history, the order history) and from the equity series ASTRA recorded. The page
computes nothing. Nothing here invents a deal: a missing history is reported as missing.

Definitions (one each, never mixed):
- order       a request recorded in the broker's order history;
- execution   a trade deal (buy or sell) in the broker's deal history, opening or closing a position;
- closed trade  a position that has at least one closing deal; its result is the sum of profit, commission, swap
                and fee of all its deals;
- open position  a position the terminal reports now.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

DEAL_BUY, DEAL_SELL, DEAL_BALANCE = 0, 1, 2
ENTRY_IN = 0
CLOSING_ENTRIES = (1, 2, 3)  # OUT, INOUT, OUT_BY
TOLERANCE = 0.011  # rounding of the broker's two-decimal figures

DEFINITIONS = {
    "balance": "Solde du compte rapporté par le courtier : dépôts, retraits, résultats réalisés, frais et swaps déjà prélevés.",
    "equity": "Fonds propres : balance plus le résultat latent (profit et swap des positions ouvertes).",
    "floating_profit": "Profit latent des positions ouvertes, sans les swaps.",
    "floating_swap": "Swaps déjà accumulés sur les positions ouvertes, pas encore passés en balance.",
    "floating_total": "Résultat latent = profit latent + swap latent.",
    "realized_gross": "Somme des profits des transactions clôturées ou ouvertes déjà passées en balance, hors frais.",
    "realized_costs": "Commissions, swaps et frais des transactions de l'historique.",
    "realized_net": "Résultat réalisé net = profits + commissions + swaps + frais de l'historique des transactions.",
    "net_deposits": "Dépôts moins retraits (opérations de balance de l'historique).",
    "net_result": "Résultat net depuis le début = fonds propres - dépôts nets = réalisé net + ajustements + latent.",
    "drawdown_current": "Baisse des fonds propres depuis leur plus haut, sur la série d'equity enregistrée par ASTRA.",
    "orders": "Ordres de l'historique du courtier.",
    "executions": "Transactions d'achat ou de vente de l'historique (ouvertures et fermetures).",
    "closed_trades": "Positions ayant au moins une transaction de fermeture.",
    "open_positions": "Positions ouvertes rapportées par le terminal.",
    "closed_today": "Positions dont la dernière transaction de fermeture date d'aujourd'hui (UTC).",
}


def _sum(values: list[float]) -> float:
    return round(sum(values), 2)


def _net(deal: dict[str, Any]) -> float:
    return deal["profit"] + deal["commission"] + deal["swap"] + deal["fee"]


def origin(position: dict[str, Any], deals: list[dict[str, Any]], magic: int) -> dict[str, Any]:
    """Whether a position can be proven to come from ASTRA, from the broker's own identifiers."""

    opening = [d for d in deals if d["position_id"] == position["ticket"] and d["type"] in (DEAL_BUY, DEAL_SELL)
               and d["entry"] == ENTRY_IN]
    marked = position.get("magic") == magic and str(position.get("comment") or "").startswith("ASTRA")
    if marked and opening and opening[0]["magic"] == magic and str(opening[0]["comment"]).startswith("ASTRA"):
        return {"astra": True, "label": "ouverte par ASTRA (confirmée)",
                "proof": f"transaction d'ouverture {opening[0]['ticket']}, ordre {opening[0]['order']}, magic {magic}"}
    if marked:
        return {"astra": False, "label": "position du compte — origine non confirmée",
                "proof": "marque ASTRA sur la position, mais transaction d'ouverture absente de l'historique reçu"}
    return {"astra": False, "label": "position du compte — origine non confirmée", "proof": "aucune marque ASTRA"}


def accounting(snapshot: dict[str, Any] | None, deals: list[dict[str, Any]], orders: list[dict[str, Any]],
               equity_series: list[tuple[str, float]], now: datetime, magic: int) -> dict[str, Any]:
    """All the figures of the dashboard, with the reconciliation checks and what is missing."""

    missing: list[str] = []
    if snapshot is None:
        return {"available": False, "definitions": DEFINITIONS, "missing": ["aucun instantané du compte reçu"]}
    account, positions = snapshot["account"], snapshot["positions"]
    trade = [d for d in deals if d["type"] in (DEAL_BUY, DEAL_SELL)]
    deposits = [d for d in deals if d["type"] == DEAL_BALANCE]
    adjustments = [d for d in deals if d["type"] not in (DEAL_BUY, DEAL_SELL, DEAL_BALANCE)]
    if not deals:
        missing.append("historique des transactions non synchronisé : réalisé, dépôts et ordres inconnus")
    elif not deposits:
        missing.append("aucun dépôt dans l'historique reçu : dépôts nets inconnus")
    net_deposits = _sum([d["profit"] for d in deposits])
    realized_gross = _sum([d["profit"] for d in trade])
    realized_costs = _sum([d["commission"] + d["swap"] + d["fee"] for d in trade])
    realized_net = round(realized_gross + realized_costs, 2)
    adjusted = _sum([_net(d) for d in adjustments])
    floating_profit = _sum([p["profit"] for p in positions])
    floating_swap = _sum([p.get("swap") or 0.0 for p in positions])
    floating_total = round(floating_profit + floating_swap, 2)
    equity, balance = account["equity"], account["balance"]

    by_position: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in trade:
        by_position[d["position_id"]].append(d)
    closed = {pid: ds for pid, ds in by_position.items() if any(d["entry"] in CLOSING_ENTRIES for d in ds)}
    results = {pid: round(sum(_net(d) for d in ds), 2) for pid, ds in closed.items()}
    wins = [r for r in results.values() if r > 0]
    losses = [r for r in results.values() if r < 0]
    today = now.date().isoformat()
    closed_today = sum(1 for ds in closed.values()
                       if max(d["ts"] for d in ds if d["entry"] in CLOSING_ENTRIES)[:10] == today)

    checks = []
    if deals:
        expected_balance = round(net_deposits + realized_net + adjusted, 2)
        checks.append({"name": "balance", "ok": abs(expected_balance - balance) <= TOLERANCE,
                       "detail": f"dépôts nets + réalisé net + ajustements = {expected_balance} ; balance = {balance}"})
    expected_equity = round(balance + account.get("credit", 0.0) + floating_total, 2)
    checks.append({"name": "equity", "ok": abs(expected_equity - equity) <= TOLERANCE,
                   "detail": f"balance + résultat latent = {expected_equity} ; fonds propres du courtier = {equity}"})
    checks.append({"name": "marge", "ok": abs(equity - account["margin"] - account["margin_free"]) <= TOLERANCE,
                   "detail": f"fonds propres - marge = {round(equity - account['margin'], 2)} ; "
                             f"marge libre = {account['margin_free']}"})
    net_result = round(equity - net_deposits, 2) if deposits else None
    if net_result is not None:
        parts = round(realized_net + adjusted + floating_total, 2)
        checks.append({"name": "resultat_net", "ok": abs(parts - net_result) <= TOLERANCE,
                       "detail": f"réalisé net + ajustements + latent = {parts} ; fonds propres - dépôts nets = {net_result}"})
    series = sorted(equity_series)
    if deposits:
        series = [(min(d["ts"] for d in deposits), _sum([d["profit"] for d in deposits[:1]])), *series]
    drawdown = drawdown_stats(series, equity)
    if not equity_series:
        missing.append("série d'equity vide : drawdown inconnu")
    return {
        "available": True, "definitions": DEFINITIONS, "missing": missing,
        "balance": balance, "equity": equity, "credit": account.get("credit", 0.0), "currency": account.get("currency"),
        "floating_profit": floating_profit, "floating_swap": floating_swap, "floating_total": floating_total,
        "margin": account["margin"], "margin_free": account["margin_free"],
        "net_deposits": net_deposits if deposits else None, "realized_gross": realized_gross if deals else None,
        "realized_costs": realized_costs if deals else None, "realized_net": realized_net if deals else None,
        "adjustments": adjusted if deals else None, "net_result": net_result,
        "drawdown": drawdown,
        "counts": {"orders": len(orders), "executions": len(trade), "closed_trades": len(closed) if deals else None,
                   "open_positions": len(positions), "closed_today": closed_today if deals else None},
        "closed_stats": {"wins": len(wins), "losses": len(losses),
                         "win_rate_pct": round(len(wins) / len(results) * 100, 1) if results else None,
                         "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None},
        "checks": checks, "consistent": all(c["ok"] for c in checks),
        "history": {"first_deal": min((d["ts"] for d in deals), default=None),
                    "last_deal": max((d["ts"] for d in trade), default=None)},
    }


def drawdown_stats(series: list[tuple[str, float]], equity_now: float) -> dict[str, Any]:
    """Current and maximum drawdown from the recorded equity series (the current equity ends the series)."""

    points = [e for _, e in series] + [equity_now]
    if len(points) < 2:
        return {"current_pct": None, "max_pct": None, "peak": None, "since": series[0][0] if series else None}
    peak, worst = points[0], 0.0
    for value in points:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100)
    current = (peak - equity_now) / peak * 100 if peak > 0 else None
    return {"current_pct": None if current is None else round(max(current, 0.0), 3), "max_pct": round(worst, 3),
            "peak": round(peak, 2), "since": series[0][0] if series else None, "points": len(points)}
