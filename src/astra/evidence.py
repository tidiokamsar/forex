"""Evidence and research registry (LOT 2, master document section IV): no backtest without a sheet.

A hypothesis is registered with its sheet before any test: economic reason (who pays and why), source, universe,
horizon, study period, sealed test period after it, fixed parameters, costs, expected result, numbered abandon
criterion, planned variants, code version and data fingerprint. Then:

- ``registered_backtest`` runs one variant on the study period only (a request reaching into the sealed period is
  refused); each run is a trial and is kept with the SHA-256 of its result, negative or not;
- ``final_test`` opens the sealed period once per hypothesis; the access is recorded and counted;
- ``replay`` recomputes a stored run and says whether the result is byte for byte the same;
- the budget is 20 new hypotheses per calendar year and at most 5 variants each (PRESET values, validated);
- ``trials`` is the global, cumulative counter, earlier trials found by the LOT 0 audit included (``record_prior``);
- a closed hypothesis (``close``) takes no new run; it reopens only through a new sheet.

``require_registration`` is the guard of every other entry point (site backtests, CLI, learning jobs): a request
that does not come from this registry is refused.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .papertrade import run_backtest

HYPOTHESES_PER_YEAR = 20
VARIANTS_PER_HYPOTHESIS = 5
REQUIRED_FIELDS = ("title", "economic_reason", "source", "universe", "horizon", "study_period", "sealed_test_period",
                   "parameters", "costs", "expected_result", "abandon_criterion", "planned_variants", "code_version",
                   "data_sha256")
CLOSED = ("REJETEE", "ABANDONNEE", "RETENUE")
_TOKEN = "evidence-registry"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ev_hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, registered_at TEXT NOT NULL, year INTEGER NOT NULL, sheet_json TEXT NOT NULL,
    sheet_sha256 TEXT NOT NULL, status TEXT NOT NULL, closed_at TEXT, closed_reason TEXT);
CREATE TABLE IF NOT EXISTS ev_variants (
    hypothesis_id INTEGER NOT NULL, params_json TEXT NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (hypothesis_id, params_json));
CREATE TABLE IF NOT EXISTS ev_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, hypothesis_id INTEGER NOT NULL, kind TEXT NOT NULL, params_json TEXT NOT NULL,
    request_json TEXT NOT NULL, result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, ran_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ev_prior_trials (
    source TEXT PRIMARY KEY, count INTEGER NOT NULL, note TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS ev_runs_no_update BEFORE UPDATE ON ev_runs
BEGIN SELECT RAISE(ABORT, 'résultats immuables'); END;
CREATE TRIGGER IF NOT EXISTS ev_runs_no_delete BEFORE DELETE ON ev_runs
BEGIN SELECT RAISE(ABORT, 'résultats immuables'); END;
"""


class EvidenceError(ValueError):
    """Refused by the registry: missing sheet, budget exhausted, sealed period, closed hypothesis."""


def require_registration(request: dict[str, Any]) -> None:
    """Every backtest entry point outside this module calls this: only a registry run carries the token."""

    if not isinstance(request, dict) or request.get("_evidence") != _TOKEN:
        raise EvidenceError("aucun backtest sans fiche au registre des preuves (LOT 2) : enregistrer une hypothèse")


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _when(text: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError as exc:
        raise EvidenceError(f"date illisible : {text!r}") from exc
    if moment.tzinfo is None:
        raise EvidenceError(f"date sans fuseau : {text!r}")
    return moment


def check_sheet(sheet: Any) -> dict[str, Any]:
    if not isinstance(sheet, dict):
        raise EvidenceError("fiche : objet attendu")
    for name in REQUIRED_FIELDS:
        value = sheet.get(name)
        if value is None or (isinstance(value, (str, list, dict)) and not value):
            raise EvidenceError(f"fiche incomplète : {name}")
    study, sealed = sheet["study_period"], sheet["sealed_test_period"]
    if not (isinstance(study, list) and isinstance(sealed, list) and len(study) == 2 and len(sealed) == 2):
        raise EvidenceError("périodes : [début, fin] attendus")
    s0, s1, t0, t1 = (_when(x) for x in (*study, *sealed))
    if not (s0 < s1 <= t0 < t1):
        raise EvidenceError("la période scellée doit suivre la période d'étude, sans recouvrement")
    variants = sheet["planned_variants"]
    if not isinstance(variants, int) or isinstance(variants, bool) or not 1 <= variants <= VARIANTS_PER_HYPOTHESIS:
        raise EvidenceError(f"variantes prévues : de 1 à {VARIANTS_PER_HYPOTHESIS}")
    return sheet


class EvidenceRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
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

    # -- sheets and budget ---------------------------------------------------------------------------------

    def register(self, sheet: dict[str, Any], now: datetime) -> int:
        check_sheet(sheet)
        with self._connect() as db:
            used = db.execute("SELECT count(*) FROM ev_hypotheses WHERE year = ?", (now.year,)).fetchone()[0]
            if used >= HYPOTHESES_PER_YEAR:
                raise EvidenceError(f"budget annuel épuisé : {HYPOTHESES_PER_YEAR} hypothèses en {now.year}")
            cursor = db.execute("INSERT INTO ev_hypotheses(registered_at, year, sheet_json, sheet_sha256, status) "
                                "VALUES (?,?,?,?, 'OUVERTE')", (now.isoformat(), now.year, json.dumps(sheet, sort_keys=True),
                                                                 _sha(sheet)))
            return int(cursor.lastrowid)

    def hypothesis(self, hid: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM ev_hypotheses WHERE id = ?", (hid,)).fetchone()
            if row is None:
                raise EvidenceError(f"hypothèse {hid} inconnue")
            finals = db.execute("SELECT count(*) FROM ev_runs WHERE hypothesis_id = ? AND kind = 'FINAL'", (hid,)).fetchone()[0]
            variants = db.execute("SELECT count(*) FROM ev_variants WHERE hypothesis_id = ?", (hid,)).fetchone()[0]
        return {"id": hid, "sheet": json.loads(row["sheet_json"]), "sheet_sha256": row["sheet_sha256"],
                "status": row["status"], "registered_at": row["registered_at"], "closed_reason": row["closed_reason"],
                "final_accesses": finals, "variants": variants}

    def add_variant(self, hid: int, params: dict[str, Any], now: datetime) -> int:
        self._open(hid)
        key = json.dumps(params, sort_keys=True)
        with self._connect() as db:
            if db.execute("SELECT 1 FROM ev_variants WHERE hypothesis_id = ? AND params_json = ?", (hid, key)).fetchone():
                return db.execute("SELECT count(*) FROM ev_variants WHERE hypothesis_id = ?", (hid,)).fetchone()[0]
            count = db.execute("SELECT count(*) FROM ev_variants WHERE hypothesis_id = ?", (hid,)).fetchone()[0]
            if count >= VARIANTS_PER_HYPOTHESIS:
                raise EvidenceError(f"budget de variantes épuisé : {VARIANTS_PER_HYPOTHESIS} par hypothèse")
            db.execute("INSERT INTO ev_variants VALUES (?,?,?)", (hid, key, now.isoformat()))
            return count + 1

    def _open(self, hid: int) -> dict[str, Any]:
        found = self.hypothesis(hid)
        if found["status"] != "OUVERTE":
            raise EvidenceError(f"hypothèse {hid} close ({found['status']}) : une nouvelle fiche est nécessaire")
        return found

    def close(self, hid: int, status: str, reason: str, now: datetime) -> None:
        if status not in CLOSED:
            raise EvidenceError(f"statut de clôture : {', '.join(CLOSED)}")
        self._open(hid)
        with self._connect() as db:
            db.execute("UPDATE ev_hypotheses SET status = ?, closed_at = ?, closed_reason = ? WHERE id = ?",
                       (status, now.isoformat(), reason[:300], hid))

    # -- runs ----------------------------------------------------------------------------------------------

    def _run(self, candles: Any, hid: int, kind: str, params: dict[str, Any], request: dict[str, Any],
             start: str, end: str, now: datetime) -> dict[str, Any]:
        prepared = {**request, "strategy_params": {**request.get("strategy_params", {}), **params},
                    "start": start, "end": end, "_evidence": _TOKEN}
        result = run_backtest(candles, prepared)
        digest = _sha(result)
        with self._connect() as db:
            cursor = db.execute("INSERT INTO ev_runs(hypothesis_id, kind, params_json, request_json, result_json, "
                                "result_sha256, ran_at) VALUES (?,?,?,?,?,?,?)",
                                (hid, kind, json.dumps(params, sort_keys=True), json.dumps(prepared, sort_keys=True),
                                 json.dumps(result, sort_keys=True, default=str), digest, now.isoformat()))
        return {"run_id": int(cursor.lastrowid), "result": result, "result_sha256": digest}

    def registered_backtest(self, candles: Any, hid: int, params: dict[str, Any], request: dict[str, Any],
                            now: datetime) -> dict[str, Any]:
        sheet = self._open(hid)["sheet"]
        study_start, study_end = sheet["study_period"]
        if request.get("end") and _when(request["end"]) > _when(study_end):
            raise EvidenceError("la demande atteint la période scellée : refusée (seul final_test l'ouvre, une fois)")
        self.add_variant(hid, params, now)
        start = request.get("start") or study_start
        if _when(start) < _when(study_start):
            start = study_start
        return self._run(candles, hid, "STUDY", params, request, start, request.get("end") or study_end, now)

    def final_test(self, candles: Any, hid: int, params: dict[str, Any], request: dict[str, Any],
                   now: datetime) -> dict[str, Any]:
        found = self._open(hid)
        if found["final_accesses"] >= 1:
            raise EvidenceError("jeu final déjà ouvert pour cette hypothèse : un seul accès")
        start, end = found["sheet"]["sealed_test_period"]
        return self._run(candles, hid, "FINAL", params, request, start, end, now)

    def replay(self, candles: Any, run_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT request_json, result_sha256 FROM ev_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise EvidenceError(f"exécution {run_id} inconnue")
        digest = _sha(run_backtest(candles, json.loads(row["request_json"])))
        return {"run_id": run_id, "identical": digest == row["result_sha256"], "result_sha256": digest,
                "recorded_sha256": row["result_sha256"]}

    def runs(self, hid: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id, kind, params_json, result_sha256, ran_at FROM ev_runs WHERE hypothesis_id = ? "
                              "ORDER BY id", (hid,)).fetchall()
        return [dict(r) for r in rows]

    # -- trial counter -------------------------------------------------------------------------------------

    def record_prior(self, source: str, count: int, note: str, now: datetime) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO ev_prior_trials VALUES (?,?,?,?) ON CONFLICT(source) DO UPDATE SET count = excluded.count,"
                       " note = excluded.note, recorded_at = excluded.recorded_at", (source, int(count), note, now.isoformat()))

    def trials(self) -> dict[str, int]:
        with self._connect() as db:
            prior = db.execute("SELECT coalesce(sum(count), 0) FROM ev_prior_trials").fetchone()[0]
            registered = db.execute("SELECT count(*) FROM ev_runs").fetchone()[0]
        return {"prior": int(prior), "registered": int(registered), "total": int(prior) + int(registered)}
