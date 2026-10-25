"""Persistent, idempotent order state machine (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator

from ..storage import BacktestStore


class OrderState(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RISK_REJECTED = "RISK_REJECTED"
    AUTHORIZED = "AUTHORIZED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    BROKER_REJECTED = "BROKER_REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = frozenset(
    {OrderState.RISK_REJECTED, OrderState.FILLED, OrderState.CANCELLED, OrderState.BROKER_REJECTED}
)

_BROKER_OUTCOMES = frozenset(
    {
        OrderState.ACCEPTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.BROKER_REJECTED,
        OrderState.UNKNOWN,
    }
)

ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.VALIDATING}),
    OrderState.VALIDATING: frozenset({OrderState.RISK_REJECTED, OrderState.AUTHORIZED}),
    OrderState.AUTHORIZED: frozenset({OrderState.SUBMITTED, OrderState.CANCELLED}),
    OrderState.SUBMITTED: _BROKER_OUTCOMES,
    OrderState.ACCEPTED: frozenset(
        {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED, OrderState.UNKNOWN}
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED, OrderState.UNKNOWN}
    ),
    OrderState.UNKNOWN: _BROKER_OUTCOMES,
}


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OrderRecord:
    intent_id: str
    client_order_id: str
    decision_id: str
    correlation_id: str
    purpose: str
    mode: str
    account_ref: str
    instrument: str
    direction: str
    requested_units: float
    filled_units: float
    average_fill_price: float | None
    stop_price: float | None
    state: OrderState
    broker_order_id: str | None
    last_broker_sequence: int
    reason_codes: tuple[str, ...]
    strategy: str
    strategy_version: str
    created_at: str
    updated_at: str
    expires_at: str

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "client_order_id": self.client_order_id,
            "decision_id": self.decision_id,
            "correlation_id": self.correlation_id,
            "purpose": self.purpose,
            "mode": self.mode,
            "account_ref": self.account_ref,
            "instrument": self.instrument,
            "direction": self.direction,
            "requested_units": self.requested_units,
            "filled_units": self.filled_units,
            "average_fill_price": self.average_fill_price,
            "stop_price": self.stop_price,
            "state": self.state.value,
            "broker_order_id": self.broker_order_id,
            "last_broker_sequence": self.last_broker_sequence,
            "reason_codes": list(self.reason_codes),
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionStore:
    """Durable order, control, reconciliation and audit records.

    Every state change is written together with its transition row and audit
    event in one SQLite transaction. Local state is a record of what ASTRA
    sent and observed; after a failure it must be reconciled with the broker.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def initialize(self) -> None:
        BacktestStore(self.path).initialize()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    intent_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    decision_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    purpose TEXT NOT NULL CHECK (purpose IN ('ENTRY', 'EXIT')),
                    mode TEXT NOT NULL,
                    account_ref TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    requested_units REAL NOT NULL,
                    filled_units REAL NOT NULL DEFAULT 0,
                    average_fill_price REAL,
                    stop_price REAL,
                    state TEXT NOT NULL,
                    broker_order_id TEXT,
                    last_broker_sequence INTEGER NOT NULL DEFAULT 0,
                    reason_codes_json TEXT NOT NULL DEFAULT '[]',
                    strategy TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_transitions (
                    transition_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL REFERENCES orders(intent_id),
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_controls (
                    control_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliations (
                    reconciliation_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('CONSISTENT', 'DIVERGENT', 'FAILED')),
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
                CREATE INDEX IF NOT EXISTS idx_order_transitions_intent
                    ON order_transitions(intent_id, occurred_at);
                """
            )

    # -- orders -------------------------------------------------------------

    def create_order(self, values: dict[str, Any], *, correlation_id: str) -> tuple[OrderRecord, bool]:
        """Insert a CREATED order; return (record, created). Never overwrites."""

        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM orders WHERE intent_id = ?", (values["intent_id"],)
            ).fetchone()
            if existing is not None:
                return _record(existing), False
            db.execute(
                """INSERT INTO orders (
                    intent_id, client_order_id, decision_id, correlation_id, purpose, mode,
                    account_ref, instrument, direction, requested_units, stop_price, state,
                    strategy, strategy_version, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CREATED', ?, ?, ?, ?, ?)""",
                (
                    values["intent_id"],
                    values["client_order_id"],
                    values["decision_id"],
                    correlation_id,
                    values["purpose"],
                    values["mode"],
                    values["account_ref"],
                    values["instrument"],
                    values["direction"],
                    float(values.get("requested_units") or 0.0),
                    values.get("stop_price"),
                    values["strategy"],
                    values["strategy_version"],
                    now,
                    now,
                    values["expires_at"],
                ),
            )
            self._insert_transition(db, values["intent_id"], None, OrderState.CREATED, "intent_received", {}, now)
            self._insert_audit(
                db,
                "order.created",
                {"intent_id": values["intent_id"], "correlation_id": correlation_id, "mode": values["mode"]},
                now,
            )
            row = db.execute("SELECT * FROM orders WHERE intent_id = ?", (values["intent_id"],)).fetchone()
        return _record(row), True

    def transition(
        self,
        intent_id: str,
        to_state: OrderState,
        *,
        reason: str,
        updates: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> OrderRecord:
        now = _now()
        changes = dict(updates or {})
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM orders WHERE intent_id = ?", (intent_id,)).fetchone()
            if row is None:
                raise KeyError(intent_id)
            current = OrderState(row["state"])
            if to_state not in ALLOWED_TRANSITIONS.get(current, frozenset()):
                raise InvalidTransition(f"{current} -> {to_state} is not allowed")
            if "reason_codes" in changes:
                changes["reason_codes_json"] = json.dumps(list(changes.pop("reason_codes")))
            allowed_columns = {
                "requested_units",
                "filled_units",
                "average_fill_price",
                "broker_order_id",
                "last_broker_sequence",
                "reason_codes_json",
            }
            unknown = set(changes) - allowed_columns
            if unknown:
                raise ValueError(f"unsupported order update fields: {sorted(unknown)}")
            assignments = ", ".join(f"{column} = ?" for column in changes)
            params = list(changes.values())
            sql = "UPDATE orders SET state = ?, updated_at = ?"
            if assignments:
                sql += ", " + assignments
            db.execute(sql + " WHERE intent_id = ?", [to_state.value, now, *params, intent_id])
            self._insert_transition(db, intent_id, current, to_state, reason, details or {}, now)
            self._insert_audit(
                db,
                "order.transition",
                {
                    "intent_id": intent_id,
                    "correlation_id": row["correlation_id"],
                    "from": current.value,
                    "to": to_state.value,
                    "reason": reason,
                },
                now,
            )
            updated = db.execute("SELECT * FROM orders WHERE intent_id = ?", (intent_id,)).fetchone()
        return _record(updated)

    def get_order(self, intent_id: str) -> OrderRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM orders WHERE intent_id = ?", (intent_id,)).fetchone()
        return _record(row) if row else None

    def get_by_client_id(self, client_order_id: str) -> OrderRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        return _record(row) if row else None

    def list_orders(
        self, limit: int = 100, *, open_only: bool = False, account_ref: str | None = None
    ) -> list[OrderRecord]:
        safe_limit = max(1, min(int(limit), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if open_only:
            terminal = tuple(state.value for state in TERMINAL_STATES)
            clauses.append(f"state NOT IN ({','.join('?' * len(terminal))})")
            params.extend(terminal)
        if account_ref is not None:
            clauses.append("account_ref = ?")
            params.append(account_ref)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM orders {where}ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*params, safe_limit),
            ).fetchall()
        return [_record(row) for row in rows]

    def transitions(self, intent_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM order_transitions WHERE intent_id = ? ORDER BY occurred_at, rowid",
                (intent_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def expected_net_units(self, account_ref: str) -> dict[str, float]:
        """Signed filled units per instrument according to ASTRA's own records."""

        with self._connect() as db:
            rows = db.execute(
                "SELECT instrument, direction, filled_units FROM orders WHERE filled_units > 0 AND account_ref = ?",
                (account_ref,),
            ).fetchall()
        totals: dict[str, float] = {}
        for row in rows:
            sign = 1.0 if row["direction"] == "LONG" else -1.0
            totals[row["instrument"]] = totals.get(row["instrument"], 0.0) + sign * row["filled_units"]
        return {key: value for key, value in totals.items() if abs(value) > 1e-9}

    # -- controls -----------------------------------------------------------

    def get_control(self, key: str, default: Any = None) -> Any:
        with self._connect() as db:
            row = db.execute(
                "SELECT value_json FROM trading_controls WHERE control_key = ?", (key,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_control(self, key: str, value: Any, *, actor: str, audit_type: str, details: dict[str, Any]) -> None:
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO trading_controls (control_key, value_json, updated_at, updated_by)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(control_key) DO UPDATE SET
                     value_json = excluded.value_json,
                     updated_at = excluded.updated_at,
                     updated_by = excluded.updated_by""",
                (key, json.dumps(value, sort_keys=True), now, actor),
            )
            self._insert_audit(db, audit_type, {"actor": actor, **details}, now)

    # -- reconciliation and audit --------------------------------------------

    def record_reconciliation(self, status: str, details: dict[str, Any]) -> str:
        now = _now()
        reconciliation_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO reconciliations VALUES (?, ?, ?, ?)",
                (reconciliation_id, now, status, json.dumps(details, sort_keys=True, default=str)),
            )
            self._insert_audit(
                db,
                "execution.reconciliation",
                {"reconciliation_id": reconciliation_id, "status": status},
                now,
            )
        return reconciliation_id

    def reconciliations(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM reconciliations ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def audit(self, event_type: str, details: dict[str, Any]) -> None:
        with self._connect() as db:
            self._insert_audit(db, event_type, details, _now())

    @staticmethod
    def _insert_transition(db, intent_id, from_state, to_state, reason, details, now) -> None:
        db.execute(
            "INSERT INTO order_transitions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                intent_id,
                from_state.value if from_state else None,
                to_state.value,
                now,
                reason,
                json.dumps(details, sort_keys=True, default=str),
            ),
        )

    @staticmethod
    def _insert_audit(db, event_type: str, details: dict[str, Any], now: str) -> None:
        db.execute(
            """INSERT INTO audit_events (event_id, occurred_at, event_type, run_id, details_json)
               VALUES (?, ?, ?, NULL, ?)""",
            (str(uuid.uuid4()), now, event_type, json.dumps(details, sort_keys=True, default=str)),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            if db.in_transaction:
                db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()


def _record(row: sqlite3.Row) -> OrderRecord:
    return OrderRecord(
        intent_id=row["intent_id"],
        client_order_id=row["client_order_id"],
        decision_id=row["decision_id"],
        correlation_id=row["correlation_id"],
        purpose=row["purpose"],
        mode=row["mode"],
        account_ref=row["account_ref"],
        instrument=row["instrument"],
        direction=row["direction"],
        requested_units=row["requested_units"],
        filled_units=row["filled_units"],
        average_fill_price=row["average_fill_price"],
        stop_price=row["stop_price"],
        state=OrderState(row["state"]),
        broker_order_id=row["broker_order_id"],
        last_broker_sequence=row["last_broker_sequence"],
        reason_codes=tuple(json.loads(row["reason_codes_json"])),
        strategy=row["strategy"],
        strategy_version=row["strategy_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
    )
