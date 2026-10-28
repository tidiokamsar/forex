"""DEMO account state shared by the dashboard and the astra-demo service, with no execution code.

The web reads the state (connection, balances, latest quotes, controlled tests) and records requests
(pause, resume, controlled test); only astra-demo acts on them. DEMO execution starts PAUSED and stays paused
until an operator resumes it; an unreadable switch counts as paused.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUEST_KINDS = ("pause", "resume", "controlled_test")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_state (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demo_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    status TEXT NOT NULL, processed_at TEXT, result_json TEXT
);
CREATE TABLE IF NOT EXISTS demo_quotes (
    ts TEXT NOT NULL, symbol TEXT NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS demo_test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, actor TEXT NOT NULL,
    status TEXT NOT NULL, details_json TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DemoStore:
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

    def set(self, key: str, value: Any) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO demo_state(key, value_json, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE"
                       " SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                       (key, json.dumps(value, default=str), _now()))

    def get(self, key: str, default: Any = None) -> Any:
        with self._connect() as db:
            row = db.execute("SELECT value_json FROM demo_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def state(self) -> dict[str, Any]:
        with self._connect() as db:
            return {r["key"]: {"value": json.loads(r["value_json"]), "updated_at": r["updated_at"]}
                    for r in db.execute("SELECT * FROM demo_state")}

    def paused(self) -> bool:
        try:
            return self.get("paused", True) is not False
        except (sqlite3.Error, ValueError):
            return True

    def request(self, kind: str, actor: str) -> dict[str, Any]:
        if kind not in REQUEST_KINDS:
            raise ValueError("unknown request")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT id FROM demo_requests WHERE kind = ? AND status = 'QUEUED'", (kind,)).fetchone()
            if pending:
                return {"id": pending["id"], "status": "QUEUED", "duplicate": True}
            cursor = db.execute("INSERT INTO demo_requests(kind, actor, created_at, status) VALUES (?,?,?,?)",
                                (kind, actor, _now(), "QUEUED"))
            return {"id": int(cursor.lastrowid), "status": "QUEUED", "duplicate": False}

    def next_request(self) -> dict[str, Any] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM demo_requests WHERE status = 'QUEUED' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE demo_requests SET status = 'RUNNING' WHERE id = ?", (row["id"],))
            return dict(row)

    def finish_request(self, request_id: int, ok: bool, result: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("UPDATE demo_requests SET status = ?, processed_at = ?, result_json = ? WHERE id = ?",
                       ("DONE" if ok else "FAILED", _now(), json.dumps(result, default=str), request_id))

    def recover(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE demo_requests SET status = 'FAILED', processed_at = ?, result_json = ? WHERE status = 'RUNNING'",
                       (_now(), json.dumps({"error": "interrompue par un redémarrage d'astra-demo"})))

    def requests(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM demo_requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "result": json.loads(r["result_json"] or "null")} for r in rows]

    def add_quote(self, ts: str, symbol: str, bid: float, ask: float, source: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO demo_quotes(ts, symbol, bid, ask, source) VALUES (?,?,?,?,?)",
                       (ts, symbol, bid, ask, source))
            db.execute("DELETE FROM demo_quotes WHERE symbol = ? AND ts NOT IN (SELECT ts FROM demo_quotes WHERE symbol = ?"
                       " ORDER BY ts DESC LIMIT 2880)", (symbol, symbol))

    def quotes(self, symbol: str, limit: int = 300) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM demo_quotes WHERE symbol = ? ORDER BY ts DESC LIMIT ?", (symbol, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def start_test(self, actor: str) -> int:
        with self._connect() as db:
            return int(db.execute("INSERT INTO demo_test_runs(started_at, actor, status, details_json) VALUES (?,?,?,?)",
                                  (_now(), actor, "RUNNING", "{}")).lastrowid)

    def finish_test(self, run_id: int, status: str, details: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("UPDATE demo_test_runs SET finished_at = ?, status = ?, details_json = ? WHERE id = ?",
                       (_now(), status, json.dumps(details, default=str), run_id))

    def tests(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM demo_test_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            out.append(item)
        return out
