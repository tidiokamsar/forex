"""Daily report: what ASTRA observed, decided, did and learned on one UTC day, from what the services really stored.

Read only (the database is opened in read-only mode). No figure is invented: a part with no data says so. The analysis of
each closed trade is deterministic (reasons recorded at entry and exit, duration, result against the risk planned at
entry); it describes what happened, it does not predict anything.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ASTRA_MAGICS = (20261028, 20261029)  # automatic and manual ASTRA orders on the DEMO account


def _rows(db: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return db.execute(query, params).fetchall()
    except sqlite3.OperationalError:  # a table that does not exist yet: that part of the report is empty
        return []


def _lesson(r_multiple: float | None, closed_by_astra: bool) -> str:
    if r_multiple is None:
        return "risque prévu inconnu : résultat non rapportable au risque"
    if r_multiple <= -0.8:
        return "perte proche du risque prévu : le stop a joué son rôle"
    if r_multiple < 0:
        return ("perte inférieure au risque prévu : sortie avant le stop" if closed_by_astra
                else "perte inférieure au risque prévu")
    if r_multiple >= 1.5:
        return "gain supérieur à 1,5 fois le risque prévu"
    return "gain inférieur à 1,5 fois le risque prévu"


MATCH_MINUTES = 15  # the bridge writes its line a few seconds after the broker's deal time


def _nearest(db: sqlite3.Connection, kind: str, symbol: str, moment: str) -> dict[str, Any]:
    """The bridge's line of ``kind`` for ``symbol`` closest to ``moment`` (within MATCH_MINUTES), or {} when there is none."""

    at = datetime.fromisoformat(moment)
    low, high = (at - timedelta(minutes=MATCH_MINUTES)).isoformat(), (at + timedelta(minutes=MATCH_MINUTES)).isoformat()
    rows = _rows(db, "SELECT ts, detail_json FROM mt5_events WHERE kind = ? AND symbol = ? AND ts >= ? AND ts <= ?",
                 (kind, symbol, low, high))
    if not rows:
        return {}
    best = min(rows, key=lambda r: abs((datetime.fromisoformat(r["ts"]) - at).total_seconds()))
    return json.loads(best["detail_json"])


def closed_trades(db: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    """Every ASTRA position closed during the day, joined to its entry, with the reasons ASTRA recorded."""

    out = []
    for close in _rows(db, "SELECT * FROM mt5_deals WHERE entry = 1 AND type IN (0, 1) AND ts >= ? AND ts < ? ORDER BY ts",
                       (start, end)):
        entry = next(iter(_rows(db, "SELECT * FROM mt5_deals WHERE entry = 0 AND position_id = ? ORDER BY ts LIMIT 1",
                                (close["position_id"],))), None)
        if entry is None or entry["magic"] not in ASTRA_MAGICS:
            continue
        net = close["profit"] + close["commission"] + close["swap"] + close["fee"] + entry["commission"] + entry["fee"]
        opened_detail = _nearest(db, "open", close["symbol"], entry["ts"])
        closing_detail = _nearest(db, "close", close["symbol"], close["ts"])
        risk = opened_detail.get("risk_usd")
        r_multiple = round(net / risk, 2) if isinstance(risk, (int, float)) and risk > 0 else None
        hours = (datetime.fromisoformat(close["ts"]) - datetime.fromisoformat(entry["ts"])).total_seconds() / 3600
        out.append({"symbol": close["symbol"], "side": "LONG" if entry["type"] == 0 else "SHORT", "volume": entry["volume"],
                    "entry_ts": entry["ts"], "exit_ts": close["ts"], "entry_price": entry["price"], "exit_price": close["price"],
                    "net": round(net, 2), "hours": round(hours, 1), "risk_usd": risk, "r_multiple": r_multiple,
                    "entry_reason": opened_detail.get("reason") or "raison d'entrée non retrouvée",
                    "exit_reason": closing_detail.get("reason") or "fermeture chez le courtier (stop touché ou action manuelle)",
                    "closed_by_astra": bool(closing_detail),
                    "lesson": _lesson(r_multiple, bool(closing_detail))})
    return out


def daily(database: str | Path, day: date) -> dict[str, Any]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    s, e = start.isoformat(), (start + timedelta(days=1)).isoformat()
    report: dict[str, Any] = {"date": day.isoformat(), "generated_at": datetime.now(UTC).isoformat(), "nature": "DEMO",
                              "summary": []}
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        equity = _rows(db, "SELECT ts, equity, balance FROM mt5_equity WHERE ts >= ? AND ts < ? ORDER BY ts", (s, e))
        report["account"] = ({"equity_open": equity[0]["equity"], "equity_close": equity[-1]["equity"],
                              "equity_low": min(r["equity"] for r in equity), "equity_high": max(r["equity"] for r in equity),
                              "change": round(equity[-1]["equity"] - equity[0]["equity"], 2), "points": len(equity)}
                             if equity else None)
        deals = _rows(db, "SELECT entry, magic FROM mt5_deals WHERE type IN (0, 1) AND ts >= ? AND ts < ?", (s, e))
        report["orders"] = {"opened": sum(1 for d in deals if d["entry"] == 0 and d["magic"] in ASTRA_MAGICS),
                            "closed": sum(1 for d in deals if d["entry"] == 1),
                            "foreign": sum(1 for d in deals if d["magic"] not in ASTRA_MAGICS and d["entry"] == 0)}
        events = _rows(db, "SELECT kind, symbol, detail_json FROM mt5_events WHERE ts >= ? AND ts < ? ORDER BY ts", (s, e))
        kinds: dict[str, int] = {}
        for ev in events:
            kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
        report["bridge_events"] = kinds
        report["refusals"] = [{"symbol": ev["symbol"], "reason": json.loads(ev["detail_json"]).get("reason", "")[:160]}
                              for ev in events if ev["kind"] == "refused"][:10]
        report["trades"] = closed_trades(db, s, e)
        feeds = [r["session_id"] for r in _rows(db, "SELECT session_id FROM learn_managed")]
        feeds += [r["session_id"] for r in _rows(db, "SELECT session_id FROM h1_learn_demo WHERE active = 1")]
        decisions = []
        for session_id in feeds:
            for item in _rows(db, "SELECT symbol, payload_json FROM sim_items WHERE session_id = ? AND kind = 'signals' "
                                  "AND ts >= ? AND ts < ? ORDER BY seq", (session_id, s, e)):
                payload = json.loads(item["payload_json"])
                decisions.append({"symbol": item["symbol"], "ts": payload.get("ts"), "decision": payload.get("decision"),
                                  "reason": (payload.get("reason") or "")[:160]})
        report["decisions"] = decisions
        learning: dict[str, Any] = {}
        for profile, prefix in (("D1", ""), ("H1", "h1_")):
            rows = _rows(db, f"SELECT symbol, kind, detail_json FROM {prefix}learn_events WHERE ts >= ? AND ts < ?", (s, e))
            counts: dict[str, int] = {}
            notable = []
            for row in rows:
                counts[row["kind"]] = counts.get(row["kind"], 0) + 1
                if row["kind"] not in ("CANDIDATS_ENREGISTRES", "REFUS"):
                    notable.append({"symbol": row["symbol"], "kind": row["kind"],
                                    "reason": str(json.loads(row["detail_json"]).get("reason", ""))[:200]})
            learning[profile] = {"events": counts, "notable": notable}
        report["learning"] = learning
        report["data_sources"] = [{"name": r["name"], "last_success_at": r["last_success_at"], "last_data_ts": r["last_data_ts"]}
                                  for r in _rows(db, "SELECT name, last_success_at, last_data_ts FROM md_sources ORDER BY name")]
    report["summary"] = _summary(report)
    return report


def _summary(r: dict[str, Any]) -> list[str]:
    lines = []
    account = r["account"]
    if account:
        lines.append(f"Fonds propres du compte DEMO : {account['equity_open']:.2f} puis {account['equity_close']:.2f} USD "
                     f"({account['change']:+.2f} USD sur la journée).")
    else:
        lines.append("Aucun relevé des fonds propres du compte DEMO ce jour-là.")
    orders = r["orders"]
    lines.append(f"Ordres ASTRA : {orders['opened']} ouverture(s), {orders['closed']} fermeture(s) sur le compte."
                 + (f" {orders['foreign']} ouverture(s) d'une autre origine." if orders["foreign"] else ""))
    if r["trades"]:
        total = sum(t["net"] for t in r["trades"])
        lines.append(f"Transactions clôturées : {len(r['trades'])}, résultat net {total:+.2f} USD.")
    decided = [d for d in r["decisions"] if d["decision"] in ("ACHAT", "VENTE")]
    lines.append(f"Décisions des stratégies qui nourrissent le pont : {len(r['decisions'])} changement(s), "
                 f"dont {len(decided)} vers une position." if r["decisions"]
                 else "Aucun changement de décision des stratégies qui nourrissent le pont : rester en dehors du marché "
                      "est aussi une décision.")
    if r["refusals"]:
        lines.append(f"Refus du portail de risque ou du courtier : {len(r['refusals'])}.")
    for profile, data in r["learning"].items():
        events = data["events"]
        if events:
            lines.append(f"Apprentissage {profile} : {events.get('CANDIDATS_ENREGISTRES', 0)} passage(s), "
                         f"{events.get('PROMOTION_SIMULATION', 0)} promotion(s) en simulation, {events.get('REFUS', 0)} refus"
                         + (f", {events.get('SUSPENSION', 0)} suspension(s)" if events.get("SUSPENSION") else "")
                         + (f", {events.get('ECHEC', 0)} échec(s)" if events.get("ECHEC") else "") + ".")
    return lines
