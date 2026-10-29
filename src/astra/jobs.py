"""Job queue and worker: the web interface only records requests, the worker (a separate service) runs them.

The worker imports CSV files, fetches the ECB reference rates on a schedule, runs backtests and advances the
running simulations. Every job and every step is persisted, so a restart loses nothing: a job interrupted
by a restart is marked FAILED (never silently replayed), candles and simulation steps are idempotent.
SIMULATION only: no job sends anything to a broker.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import learning, marketdata
from .marketdata import CandleStore, MarketDataError
from .papertrade import LabStore, SimulationError, run_backtest

LOG = logging.getLogger("astra.worker")
JOB_KINDS = ("import_csv", "ingest_ecb", "backtest", "learn")
LEARN_INTERVAL = timedelta(hours=20)  # one learning run per pair and day at most
ECB_INTERVAL = timedelta(hours=6)
# The ECB publishes the day's reference rates in the afternoon (about 14:15 CET). From 14:00 UTC on a weekday, until the day's
# candle is stored, the worker asks every 30 minutes instead of every 6 hours, so the strategies see the new bar quickly.
ECB_WINDOW_INTERVAL = timedelta(minutes=30)
ECB_WINDOW_FROM_HOUR = 14
POLL_SECONDS = 5.0


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, params_json TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, actor TEXT, started_at TEXT, finished_at TEXT, result_json TEXT, error TEXT,
    dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, id);
CREATE TABLE IF NOT EXISTS worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


class JobStore:
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

    def enqueue(self, kind: str, params: dict[str, Any], actor: str, dedupe_key: str | None = None) -> dict[str, Any]:
        """Queue a job; an identical job still waiting or running is returned instead (no double run)."""

        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind {kind}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if dedupe_key:
                row = db.execute("SELECT id, status FROM jobs WHERE dedupe_key = ? AND status IN ('QUEUED','RUNNING')",
                                 (dedupe_key,)).fetchone()
                if row:
                    return {"id": row["id"], "status": row["status"], "duplicate": True}
            cursor = db.execute("INSERT INTO jobs(kind, params_json, status, created_at, actor, dedupe_key)"
                                " VALUES (?,?,?,?,?,?)", (kind, json.dumps(params, ensure_ascii=False), "QUEUED",
                                                          _iso(_now()), actor, dedupe_key))
            return {"id": int(cursor.lastrowid), "status": "QUEUED", "duplicate": False}

    def active(self, dedupe_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT id, status FROM jobs WHERE dedupe_key = ? AND status IN ('QUEUED','RUNNING')",
                             (dedupe_key,)).fetchone()
        return {"id": row["id"], "status": row["status"], "duplicate": True} if row else None

    def claim(self) -> dict[str, Any] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status = 'QUEUED' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status = 'RUNNING', started_at = ? WHERE id = ?", (_iso(_now()), row["id"]))
            job = dict(row)
            job["params"] = json.loads(job.pop("params_json"))
            return job

    def finish(self, job_id: int, ok: bool, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._connect() as db:
            db.execute("UPDATE jobs SET status = ?, finished_at = ?, result_json = ?, error = ? WHERE id = ?",
                       ("DONE" if ok else "FAILED", _iso(_now()),
                        json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                        (error or "")[:1000] or None, job_id))

    def recover(self) -> int:
        """Jobs left RUNNING by a stopped worker are failed explicitly (an operator may queue them again)."""

        with self._connect() as db:
            cursor = db.execute("UPDATE jobs SET status = 'FAILED', finished_at = ?, error = ? WHERE status = 'RUNNING'",
                                (_iso(_now()), "interrompue par un redémarrage du worker : relancez-la si besoin"))
            return cursor.rowcount

    def jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            params = json.loads(item.pop("params_json"))
            params.pop("path", None)  # internal file path, not useful to the reader
            item["params"] = params
            item["result"] = json.loads(item.pop("result_json") or "null")
            out.append(item)
        return out

    def set_state(self, key: str, value: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO worker_state(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET"
                       " value = excluded.value, updated_at = excluded.updated_at",
                       (key, json.dumps(value, default=str), _iso(_now())))

    def state(self) -> dict[str, Any]:
        with self._connect() as db:
            return {row["key"]: {"value": json.loads(row["value"]), "updated_at": row["updated_at"]}
                    for row in db.execute("SELECT * FROM worker_state")}


def worker_health(jobs: JobStore, now: datetime | None = None) -> dict[str, Any]:
    beat = jobs.state().get("heartbeat")
    if not beat:
        return {"state": "NEVER_SEEN", "label": "worker jamais démarré", "last_heartbeat": None}
    age = ((now or _now()) - datetime.fromisoformat(beat["updated_at"])).total_seconds()
    state = "OK" if age <= 60 else "STALE"
    return {"state": state, "label": "actif" if state == "OK" else f"silencieux depuis {int(age)} s",
            "last_heartbeat": beat["updated_at"], "detail": beat["value"]}


class Worker:
    def __init__(self, database: str | Path, import_dir: str | Path, *,
                 ecb_fetch: marketdata.Fetcher | None = None, ecb_enabled: bool = True,
                 clock: Callable[[], datetime] = _now) -> None:
        self.database = Path(database)
        self.import_dir = Path(import_dir)
        self.candles = CandleStore(self.database)
        self.lab = LabStore(self.database)
        self.jobs = JobStore(self.database)
        self.learning = learning.LearningStore(self.database)
        self.learning_enabled = True
        self.ecb_fetch = ecb_fetch
        self.ecb_enabled = ecb_enabled
        self.clock = clock
        self.stop = threading.Event()

    def initialize(self) -> None:
        self.candles.initialize()
        self.lab.initialize()
        self.jobs.initialize()
        self.learning.initialize()
        recovered = self.jobs.recover()
        self.lab.fail_interrupted_backtests()
        if recovered:
            LOG.warning("%d job(s) interrupted by a restart marked FAILED", recovered)

    def run_job(self, job: dict[str, Any]) -> dict[str, Any]:
        params = job["params"]
        if job["kind"] == "import_csv":
            path = Path(params["path"])
            if path.parent.resolve() != self.import_dir.resolve():
                raise MarketDataError("fichier d'import hors du répertoire prévu")
            try:
                raw = path.read_bytes()
                return self.candles.import_csv(raw, params["symbol"], params["timeframe"], params["source"],
                                               timezone=params.get("timezone") or "UTC",
                                               nature=params.get("nature") or "HISTORICAL", actor=job.get("actor") or "web")
            finally:
                path.unlink(missing_ok=True)
        if job["kind"] == "ingest_ecb":
            kwargs = {"fetch": self.ecb_fetch} if self.ecb_fetch else {}
            result = marketdata.ingest_ecb(self.candles, actor=job.get("actor") or "worker", force=bool(params.get("force")),
                                           **kwargs)
            if result["status"] == "FAILED":
                raise MarketDataError(result["error"] or "BCE indisponible")
            return result
        if job["kind"] == "learn":
            symbol = str(params["symbol"])
            result = learning.learn(self.candles, self.learning, symbol)
            if result["decision"] == "PROMOTED":
                result["applied_to_simulation"] = learning.apply_champion(self.learning, self.lab, symbol)
            return result
        if job["kind"] == "backtest":
            backtest_id = int(params["backtest_id"])
            try:
                self.lab.set_backtest_status(backtest_id, "RUNNING")
                stored = self.lab.backtest(backtest_id, item_limit=0)
                if stored is None:
                    raise SimulationError(f"backtest {backtest_id} introuvable")
                result = run_backtest(self.candles, stored["params"])
                self.lab.save_backtest_result(backtest_id, result)
            except (SimulationError, MarketDataError) as exc:
                self.lab.set_backtest_status(backtest_id, "FAILED", str(exc))
                raise
            except Exception:
                self.lab.set_backtest_status(backtest_id, "FAILED", "erreur interne du calcul (voir le journal du worker)")
                raise
            return {"backtest_id": backtest_id, **result["metrics"]}
        raise ValueError(f"unknown job kind {job['kind']}")

    def maybe_schedule_ecb(self) -> None:
        if not self.ecb_enabled:
            return
        source = self.candles.source(marketdata.ECB_SOURCE) or {}
        now = self.clock()
        success, attempt = source.get("last_success_at"), source.get("last_attempt_at")
        waiting_for_today = (now.weekday() < 5 and now.hour >= ECB_WINDOW_FROM_HOUR
                             and (source.get("last_data_ts") or "")[:10] < now.date().isoformat())
        interval = ECB_WINDOW_INTERVAL if waiting_for_today else ECB_INTERVAL
        if success and now - datetime.fromisoformat(success) < interval:
            return  # fresh enough: one request every 6 hours, every 30 minutes while today's candle is awaited
        if source.get("next_allowed_at") and datetime.fromisoformat(source["next_allowed_at"]) > now:
            return  # back-off after failures (5 min, 10 min, ... up to 6 h)
        if attempt and now - datetime.fromisoformat(attempt) < timedelta(minutes=5):
            return
        self.jobs.enqueue("ingest_ecb", {"scheduled": True}, "worker:planification", dedupe_key="ingest_ecb")

    def maybe_schedule_learning(self) -> None:
        """Continuous improvement: managed paper accounts exist, and each pair is re-evaluated once a day."""

        if not self.learning_enabled:
            return
        learning.ensure_managed_sessions(self.candles, self.learning, self.lab)
        available = {c["symbol"] for c in self.candles.coverage()
                     if c["timeframe"] == learning.TIMEFRAME and c["source"] == learning.SOURCE}
        for symbol in sorted(available):
            last = self.learning.last_run_at(symbol)
            if last is None or self.clock() - last >= LEARN_INTERVAL:
                self.jobs.enqueue("learn", {"symbol": symbol}, "worker:apprentissage", dedupe_key=f"learn:{symbol}")

    def tick(self) -> dict[str, Any]:
        """One loop: schedule, run at most one job, advance every running simulation."""

        summary: dict[str, Any] = {"job": None, "sessions": {}}
        self.maybe_schedule_ecb()
        try:
            self.maybe_schedule_learning()
        except Exception as exc:  # noqa: BLE001 - learning must never stop data, backtests or simulations
            LOG.error("learning schedule failed: %s", exc)
        job = self.jobs.claim()
        if job:
            self.jobs.set_state("heartbeat", {"pid_time": _iso(self.clock()), "running_job": job["id"]})
            try:
                result = self.run_job(job)
                self.jobs.finish(job["id"], True, result)
                summary["job"] = {"id": job["id"], "kind": job["kind"], "status": "DONE"}
                LOG.info("job %s %s done", job["id"], job["kind"])
            except Exception as exc:  # noqa: BLE001 - the job fails, the worker keeps running
                self.jobs.finish(job["id"], False, error=f"{type(exc).__name__}: {exc}")
                summary["job"] = {"id": job["id"], "kind": job["kind"], "status": "FAILED", "error": str(exc)}
                LOG.error("job %s %s failed: %s\n%s", job["id"], job["kind"], exc, traceback.format_exc(limit=3))
        for session_id in self.lab.running_sessions():
            try:
                summary["sessions"][session_id] = self.lab.step_session(session_id, self.candles)
            except Exception as exc:  # noqa: BLE001 - one broken simulation must not stop the others
                self.lab.session_error(session_id, f"{type(exc).__name__}: {exc}")
                LOG.error("simulation %s step failed: %s", session_id, exc)
        self.jobs.set_state("heartbeat", {"pid_time": _iso(self.clock()), "last_job": summary["job"],
                                          "running_sessions": len(summary["sessions"])})
        return summary

    def run(self, once: bool = False) -> None:
        self.initialize()
        LOG.warning("ASTRA worker started (SIMULATION only; no broker connection)")
        while not self.stop.is_set():
            try:
                summary = self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive; systemd restarts a dead process
                LOG.error("worker loop error: %s\n%s", exc, traceback.format_exc(limit=3))
                summary = {"job": None}
            if once:
                return
            if summary.get("job") is None:
                self.stop.wait(POLL_SECONDS)
            else:
                time.sleep(0.2)
