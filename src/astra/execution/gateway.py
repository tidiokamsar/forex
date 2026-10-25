"""Order Gateway: the only path from a decision to a broker adapter.

Every command passes, in order: permission check, schema validation,
idempotency lookup, emergency controls, connection and mode verification,
quote freshness/spread, the independent RiskEngine, the account/contract
policy, and only then the adapter. State is persisted before each external
call so a crash or timeout leaves a record that must be reconciled.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..brokers.base import (
    BrokerAdapter,
    BrokerOrderReport,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerRequestError,
    BrokerTimeout,
    BrokerUnavailable,
    CapabilityNotSupported,
    ConnectionState,
)
from ..governance import Action, Actor, PermissionDenied, PermissionGuard
from ..instruments import DEFAULT_SPECS, InstrumentSpec, required_margin
from ..logs import correlation, get_logger, log_event
from ..models import Direction, OrderIntent, RiskContext, RunMode
from ..permits import _AUTHORITY as _PERMITS
from ..permits import ExecutionRefused
from ..risk import RiskEngine
from .controls import RECONCILIATION_OWNED, FreezeReason, TradingControls
from .mode import verify_mode
from .orders import TERMINAL_STATES, ExecutionStore, OrderRecord, OrderState

_LOG = get_logger("execution.gateway")
COMMAND_SCHEMA_VERSION = "astra.execution_command.v1"
_EVENT_CURSOR = "broker_event_cursor"
_DAILY_LOSS_DAY = "daily_loss_limit_day"

_REPORT_STATE = {
    BrokerOrderStatus.ACCEPTED: OrderState.ACCEPTED,
    BrokerOrderStatus.PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
    BrokerOrderStatus.FILLED: OrderState.FILLED,
    BrokerOrderStatus.CANCELLED: OrderState.CANCELLED,
    BrokerOrderStatus.REJECTED: OrderState.BROKER_REJECTED,
}


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    intent_id: str
    decision_id: str
    correlation_id: str
    mode: RunMode
    account_ref: str
    instrument: str
    direction: Direction
    created_at: datetime
    expires_at: datetime
    strategy: str
    strategy_version: str
    checks_passed: tuple[str, ...] = ()
    purpose: str = "ENTRY"
    stop_price: float | None = None
    requested_units: float | None = None
    schema_version: str = COMMAND_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class GatewayResult:
    intent_id: str | None
    client_order_id: str | None
    state: OrderState | None
    reason_codes: tuple[str, ...]
    approved_units: float = 0.0
    duplicate: bool = False

    @property
    def sent(self) -> bool:
        return self.state not in (None, OrderState.RISK_REJECTED, OrderState.CREATED, OrderState.VALIDATING)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str
    divergences: tuple[str, ...]
    resolved_orders: tuple[str, ...] = ()
    reconciliation_id: str | None = None
    freezes_after: tuple[str, ...] = field(default_factory=tuple)


def client_order_id_for(intent_id: str) -> str:
    """Stable provider-facing id; the same intent always maps to the same id."""

    return "ASTRA-" + hashlib.sha256(intent_id.encode("utf-8")).hexdigest()[:24]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OrderGateway:
    def __init__(
        self,
        *,
        store: ExecutionStore,
        broker: BrokerAdapter,
        controls: TradingControls | None = None,
        risk_engine: RiskEngine | None = None,
        guard: PermissionGuard | None = None,
        configured_mode: RunMode = RunMode.SIMULATION,
        specs: dict[str, InstrumentSpec] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        max_clock_skew_seconds: float = 5.0,
        position_tolerance_units: float = 1e-6,
        account_ref: str | None = None,
    ) -> None:
        self._store = store
        self._broker = broker
        self._guard = guard or PermissionGuard(store)
        # Freezes are scoped to the account this gateway trades; without an
        # account reference they are global and block every account.
        self._account_ref = account_ref
        self._controls = controls or TradingControls(store, self._guard, scope=account_ref)
        self._risk = risk_engine or RiskEngine()
        self._mode = configured_mode
        self._specs = dict(DEFAULT_SPECS if specs is None else specs)
        self._clock = clock
        self._skew = max_clock_skew_seconds
        self._tolerance = position_tolerance_units
        self._cursor: int | None = None

    @property
    def controls(self) -> TradingControls:
        return self._controls

    # -- submission -----------------------------------------------------------

    def submit(self, command: ExecutionCommand, actor: Actor) -> GatewayResult:
        correlation_id = getattr(command, "correlation_id", None)
        with correlation(correlation_id if isinstance(correlation_id, str) else None):
            try:
                result = self._submit(command, actor)
            except PermissionDenied:
                log_event(_LOG, "order.permission_denied", level=logging.WARNING, role=str(getattr(actor, "role", "")))
                raise
            log_event(
                _LOG,
                "order.result",
                level=logging.INFO if result.sent else logging.WARNING,
                intent_id=result.intent_id,
                state=result.state.value if result.state else None,
                reasons=list(result.reason_codes),
                units=result.approved_units,
                duplicate=result.duplicate,
                account_ref=self._account_ref,
            )
            return result

    def _submit(self, command: ExecutionCommand, actor: Actor) -> GatewayResult:
        self._guard.require(actor, Action.SUBMIT_ORDER_COMMAND, target=getattr(command, "intent_id", ""))
        schema_errors = _validate_command(command)
        if schema_errors:
            self._store.audit(
                "order.command_rejected",
                {
                    "intent_id": intent if isinstance(intent := getattr(command, "intent_id", None), str) else None,
                    "reasons": list(schema_errors),
                },
            )
            return GatewayResult(None, None, None, schema_errors)

        existing = self._store.get_order(command.intent_id)
        if existing is not None:
            return self._duplicate(existing)

        record, created = self._store.create_order(
            {
                "intent_id": command.intent_id,
                "client_order_id": client_order_id_for(command.intent_id),
                "decision_id": command.decision_id,
                "purpose": command.purpose,
                "mode": command.mode.value,
                "account_ref": command.account_ref,
                "instrument": command.instrument.upper(),
                "direction": command.direction.value,
                "requested_units": command.requested_units,
                "stop_price": command.stop_price,
                "strategy": command.strategy,
                "strategy_version": command.strategy_version,
                "expires_at": command.expires_at.isoformat(),
            },
            correlation_id=command.correlation_id,
        )
        if not created:
            return self._duplicate(record)
        self._store.transition(record.intent_id, OrderState.VALIDATING, reason="validation_started")

        reasons, units = self._pre_send_checks(command, actor)
        if reasons:
            final = self._store.transition(
                record.intent_id,
                OrderState.RISK_REJECTED,
                reason="pre_send_checks_failed",
                updates={"reason_codes": reasons},
            )
            return GatewayResult(final.intent_id, final.client_order_id, final.state, reasons)

        self._store.transition(
            record.intent_id,
            OrderState.AUTHORIZED,
            reason="all_checks_passed",
            updates={"requested_units": units},
        )
        self._store.transition(record.intent_id, OrderState.SUBMITTED, reason="sending_to_adapter")
        request = BrokerOrderRequest(
            client_order_id=record.client_order_id,
            instrument=command.instrument.upper(),
            direction=command.direction,
            units=units,
            stop_loss=command.stop_price,
            reduce_only=command.purpose == "EXIT",
        )
        permit = _PERMITS.issue(request, self._mode, self._clock())
        try:
            report = self._broker.submit_order(request, permit)
        except ExecutionRefused as exc:
            # Refused at the adapter boundary: nothing left the process.
            final = self._store.transition(
                record.intent_id,
                OrderState.BROKER_REJECTED,
                reason="refused_at_execution_boundary",
                updates={"reason_codes": (f"execution_refused:{exc}",)},
            )
            return GatewayResult(final.intent_id, final.client_order_id, final.state, final.reason_codes)
        except BrokerRequestError as exc:
            # An error response to the order request is a provider rejection.
            final = self._store.transition(
                record.intent_id,
                OrderState.BROKER_REJECTED,
                reason="provider_error_response",
                updates={"reason_codes": (f"broker_request_error:{exc.error_code}",)},
                details={"message": str(exc)},
            )
            return GatewayResult(final.intent_id, final.client_order_id, final.state, final.reason_codes)
        except BrokerTimeout:
            self._controls.freeze(FreezeReason.UNKNOWN_ORDER_STATE, context=record.intent_id)
            final = self._store.transition(
                record.intent_id,
                OrderState.UNKNOWN,
                reason="submit_timeout",
                updates={"reason_codes": ("submit_timeout_state_unknown",)},
            )
            return GatewayResult(final.intent_id, final.client_order_id, final.state, final.reason_codes, units)
        except BrokerUnavailable:
            self.on_connection_lost(context=record.intent_id)
            self._controls.freeze(FreezeReason.UNKNOWN_ORDER_STATE, context=record.intent_id)
            final = self._store.transition(
                record.intent_id,
                OrderState.UNKNOWN,
                reason="adapter_unavailable_during_submit",
                updates={"reason_codes": ("adapter_unavailable_state_unknown",)},
            )
            return GatewayResult(final.intent_id, final.client_order_id, final.state, final.reason_codes, units)
        except CapabilityNotSupported as exc:
            final = self._store.transition(
                record.intent_id,
                OrderState.BROKER_REJECTED,
                reason="capability_not_supported",
                updates={"reason_codes": ("capability_not_supported",)},
                details={"message": str(exc)},
            )
            return GatewayResult(final.intent_id, final.client_order_id, final.state, final.reason_codes)
        final = self._apply_report(record.intent_id, report)
        return GatewayResult(final.intent_id, final.client_order_id, final.state, final.reason_codes, units)

    def _duplicate(self, record: OrderRecord) -> GatewayResult:
        """Never resend a known intent; at most look up its state."""

        self._store.audit(
            "order.duplicate_intent",
            {"intent_id": record.intent_id, "state": record.state.value},
        )
        if record.state in (OrderState.UNKNOWN, OrderState.SUBMITTED):
            try:
                report = self._broker.lookup_order(record.client_order_id)
            except BrokerUnavailable:
                report = None
            if report is not None:
                record = self._apply_report(record.intent_id, report)
        return GatewayResult(
            record.intent_id,
            record.client_order_id,
            record.state,
            ("duplicate_intent", *record.reason_codes),
            record.requested_units,
            duplicate=True,
        )

    def _pre_send_checks(self, command: ExecutionCommand, actor: Actor) -> tuple[tuple[str, ...], float]:
        reasons: list[str] = []
        now = self._clock()
        if (command.created_at - now).total_seconds() > self._skew:
            reasons.append("clock_inconsistent")
            self._controls.freeze(FreezeReason.CLOCK_INCONSISTENT, context=command.intent_id)
        if command.expires_at <= now:
            reasons.append("command_expired")

        if self._controls.kill_switch_active() and not (command.purpose == "EXIT" and actor.is_human):
            reasons.append("kill_switch_active")
        if command.purpose == "ENTRY":
            self._expire_daily_loss_freeze(now)
            reasons.extend(f"entries_frozen:{code}" for code in self._controls.freezes())

        if self._broker.connection_state() is not ConnectionState.CONNECTED:
            self.on_connection_lost(context=command.intent_id)
            reasons.append("broker_disconnected")
            return tuple(dict.fromkeys(reasons)), 0.0
        try:
            capabilities = self._broker.capabilities()
            account = self._broker.account_snapshot()
            quote = self._broker.quote(command.instrument.upper())
            positions = self._broker.open_positions()
        except BrokerRequestError as exc:
            # The provider refused a read; the link is fine, so no connection freeze.
            reasons.append(f"broker_request_error:{exc.error_code}")
            return tuple(dict.fromkeys(reasons)), 0.0
        except BrokerUnavailable:
            self.on_connection_lost(context=command.intent_id)
            reasons.append("broker_unavailable")
            return tuple(dict.fromkeys(reasons)), 0.0

        verification = verify_mode(self._mode, command.mode, capabilities, account)
        if not verification.verified:
            reasons.extend(verification.reason_codes)
            self._controls.freeze(FreezeReason.MODE_MISMATCH, context=",".join(verification.reason_codes))
        if command.account_ref != account.account_ref_masked:
            reasons.append("account_ref_mismatch")

        instrument = command.instrument.upper()
        # Provider-reported specifications take precedence over illustrative ones.
        spec = {**self._specs, **self._broker.instrument_specs()}.get(instrument)
        if spec is None:
            reasons.append("instrument_specs_unknown")
        if instrument not in capabilities.instruments:
            reasons.append("instrument_not_supported_by_adapter")
        if not _finite(quote.bid) or not _finite(quote.ask) or quote.bid <= 0 or quote.ask < quote.bid:
            reasons.append("quote_invalid")
            return tuple(dict.fromkeys(reasons)), 0.0
        data_age = (now - quote.timestamp).total_seconds()
        pip_size = spec.pip_size if spec else self._risk.policy.pip_size
        spread_pips = (quote.ask - quote.bid) / pip_size
        net_position = sum(p.net_units for p in positions if p.instrument == instrument)

        if command.purpose == "EXIT":
            if abs(net_position) <= self._tolerance:
                reasons.append("no_position_to_reduce")
            elif (net_position > 0) == (command.direction is Direction.LONG):
                reasons.append("exit_direction_increases_position")
            if data_age > self._risk.policy.max_data_age_seconds or data_age < -self._skew:
                reasons.append("data_stale_or_unknown")
            if reasons or spec is None:
                return tuple(dict.fromkeys(reasons)), 0.0
            wanted = abs(net_position) if command.requested_units is None else min(command.requested_units, abs(net_position))
            units = round(wanted, 8)
            if units <= 0:
                return ("invalid_size",), 0.0
            return (), units

        entry_price = quote.ask if command.direction is Direction.LONG else quote.bid
        day_key = f"day_start_equity:{account.account_ref_masked}:{now.date().isoformat()}"
        day_start = self._store.get_control(day_key)
        if day_start is None:
            day_start = account.equity
            self._store.set_control(
                day_key,
                day_start,
                actor="system",
                audit_type="execution.trading_day_started",
                details={"equity": day_start},
            )
        open_entry_orders = sum(
            1
            for order in self._store.list_orders(open_only=True, account_ref=account.account_ref_masked)
            if order.purpose == "ENTRY" and order.intent_id != command.intent_id
        )
        open_positions = sum(1 for p in positions if abs(p.net_units) > self._tolerance) + open_entry_orders
        context = RiskContext(
            mode=self._mode,
            account_mode=self._mode if verification.verified else None,
            account_verified=verification.verified,
            data_age_seconds=data_age if data_age >= -self._skew else None,
            spread_pips=spread_pips,
            equity=account.equity,
            day_start_equity=float(day_start),
            day_net_pnl=account.equity - float(day_start),
            open_positions=open_positions,
            instrument_specs_known=spec is not None,
            margin_available=account.margin_free > 0,
            kill_switch_active=self._controls.kill_switch_active(),
        )
        intent = OrderIntent(
            intent_id=command.intent_id,
            instrument=instrument,
            direction=command.direction,
            entry_price=entry_price,
            stop_price=command.stop_price if command.stop_price is not None else math.nan,
            created_at=command.created_at,
            requested_units=command.requested_units,
        )
        decision = self._risk.evaluate(intent, context)
        if not decision.allowed:
            reasons.extend(decision.reason_codes)
            if "daily_loss_limit_reached" in decision.reason_codes:
                self._store.set_control(
                    self._daily_loss_key(),
                    now.date().isoformat(),
                    actor="system",
                    audit_type="alert.risk_daily_loss_limit",
                    details={"day": now.date().isoformat(), "account_ref": account.account_ref_masked},
                )
                self._controls.freeze(FreezeReason.DAILY_LOSS_LIMIT, context=now.date().isoformat())
        if reasons or spec is None:
            return tuple(dict.fromkeys(reasons)), 0.0

        units = spec.normalize_units(decision.approved_units)
        violations = spec.size_violations(units)
        if violations:
            return violations, 0.0
        margin = required_margin(spec, units, entry_price, account.currency)
        if margin is None:
            return ("margin_conversion_unknown",), 0.0
        if margin > account.margin_free:
            return ("margin_insufficient",), 0.0
        return (), units

    # -- broker events --------------------------------------------------------

    def _apply_report(self, intent_id: str, report: BrokerOrderReport) -> OrderRecord:
        record = self._store.get_order(intent_id)
        if record is None:
            raise KeyError(intent_id)
        target = _REPORT_STATE[report.status]
        if report.sequence <= record.last_broker_sequence:
            self._store.audit(
                "order.stale_event_ignored",
                {"intent_id": intent_id, "sequence": report.sequence, "last": record.last_broker_sequence},
            )
            return record
        if report.filled_units + 1e-9 < record.filled_units:
            self._store.audit(
                "order.inconsistent_event",
                {"intent_id": intent_id, "reported_filled": report.filled_units, "known_filled": record.filled_units},
            )
            self._controls.freeze(FreezeReason.RECONCILIATION_REQUIRED, context=intent_id)
            return record
        if record.state in TERMINAL_STATES:
            if target is not record.state:
                self._store.audit(
                    "order.terminal_conflict",
                    {"intent_id": intent_id, "known": record.state.value, "reported": target.value},
                )
                self._controls.freeze(FreezeReason.POSITION_DIVERGENCE, context=intent_id)
            return record
        if target is record.state and target is not OrderState.PARTIALLY_FILLED:
            return record
        return self._store.transition(
            intent_id,
            target,
            reason=f"broker_report:{report.status.value}",
            updates={
                "filled_units": report.filled_units,
                "average_fill_price": report.average_fill_price,
                "broker_order_id": report.broker_order_id,
                "last_broker_sequence": report.sequence,
            },
            details={"message": report.message, "sequence": report.sequence},
        )

    def process_events(self) -> int:
        """Apply provider events; out-of-order or stale events are ignored."""

        # Each adapter numbers its own events, so the cursor is per account.
        cursor_key = _EVENT_CURSOR if self._account_ref is None else f"{_EVENT_CURSOR}:{self._account_ref}"
        epoch = self._broker.event_epoch
        if self._cursor is None:
            # Sequences restart with each adapter instance; a cursor stored
            # under another epoch (or in the legacy integer form) is not reused.
            stored = self._store.get_state(cursor_key)
            same_epoch = isinstance(stored, dict) and stored.get("epoch") == epoch
            self._cursor = int(stored["cursor"]) if same_epoch else 0
        cursor = self._cursor
        try:
            events = self._broker.poll_events(cursor)
        except BrokerRequestError as exc:
            self._store.audit("execution.poll_events_refused", {"error_code": exc.error_code})
            return 0
        except BrokerUnavailable:
            self.on_connection_lost(context="poll_events")
            return 0
        applied = 0
        highest = cursor
        for event in events:
            highest = max(highest, event.sequence)
            record = self._store.get_by_client_id(event.report.client_order_id)
            if record is None:
                self._store.audit(
                    "execution.foreign_order_event",
                    {"client_order_id": event.report.client_order_id, "sequence": event.sequence},
                )
                self._controls.freeze(FreezeReason.POSITION_DIVERGENCE, context="foreign_order")
                continue
            before = record.state
            after = self._apply_report(record.intent_id, event.report)
            applied += int(after.state is not before or after.last_broker_sequence != record.last_broker_sequence)
        if highest != cursor:
            self._store.put_state(cursor_key, {"epoch": epoch, "cursor": highest})
            self._cursor = highest
        return applied

    # -- connection and reconciliation ----------------------------------------

    def on_connection_lost(self, context: str = "") -> None:
        log_event(_LOG, "execution.connection_lost", level=logging.WARNING, context=context, account_ref=self._account_ref)
        self._controls.freeze(FreezeReason.CONNECTION_LOST, context=context)
        self._controls.freeze(FreezeReason.RECONCILIATION_REQUIRED, context=context)

    def check_connection(self) -> ConnectionState:
        state = self._broker.connection_state()
        if state is not ConnectionState.CONNECTED:
            self.on_connection_lost(context=f"connection_state:{state.value}")
        return state

    def reconcile(self) -> ReconciliationResult:
        result = self._reconcile()
        log_event(
            _LOG,
            "execution.reconciliation",
            level=logging.INFO if result.status == "CONSISTENT" else logging.WARNING,
            status=result.status,
            divergences=list(result.divergences),
            resolved=list(result.resolved_orders),
            freezes=list(result.freezes_after),
            account_ref=self._account_ref,
        )
        return result

    def _reconcile(self) -> ReconciliationResult:
        """Compare local records with the adapter before any new entry."""

        if self._broker.connection_state() is not ConnectionState.CONNECTED:
            rid = self._store.record_reconciliation("FAILED", {"reason": "disconnected"})
            self.on_connection_lost(context="reconcile")
            return ReconciliationResult("FAILED", ("disconnected",), (), rid, self._controls.freezes())
        divergences: list[str] = []
        resolved: list[str] = []
        try:
            self.process_events()
            capabilities = self._broker.capabilities()
            account = self._broker.account_snapshot()
            for record in self._store.list_orders(limit=500, open_only=True, account_ref=account.account_ref_masked):
                report = self._broker.lookup_order(record.client_order_id)
                if report is None:
                    sent_or_unknown = record.state in (OrderState.SUBMITTED, OrderState.UNKNOWN)
                    if sent_or_unknown and capabilities.supports_order_lookup_by_client_id:
                        self._store.transition(
                            record.intent_id,
                            OrderState.CANCELLED,
                            reason="not_found_at_broker_after_lookup",
                            updates={"reason_codes": ("not_received_by_broker",)},
                        )
                        resolved.append(record.intent_id)
                    elif record.state in (OrderState.CREATED, OrderState.VALIDATING, OrderState.AUTHORIZED):
                        # Never sent: a crash happened before the adapter call.
                        self._abandon_unsent(record)
                        resolved.append(record.intent_id)
                    else:
                        divergences.append(f"order_missing_at_broker:{record.intent_id}")
                    continue
                updated = self._apply_report(record.intent_id, report)
                if updated.state is not record.state:
                    resolved.append(record.intent_id)
            positions = self._broker.open_positions()
        except BrokerRequestError as exc:
            # Incomplete picture: keep entries frozen but do not declare the link lost.
            rid = self._store.record_reconciliation("FAILED", {"reason": "provider_request_error", "error_code": exc.error_code})
            self._controls.freeze(FreezeReason.RECONCILIATION_REQUIRED, context=f"reconcile:{exc.error_code}")
            return ReconciliationResult(
                "FAILED", (f"provider_request_error:{exc.error_code}",), tuple(resolved), rid, self._controls.freezes()
            )
        except BrokerUnavailable:
            rid = self._store.record_reconciliation("FAILED", {"reason": "adapter_unavailable"})
            self.on_connection_lost(context="reconcile")
            return ReconciliationResult("FAILED", ("adapter_unavailable",), tuple(resolved), rid, self._controls.freezes())

        verification = verify_mode(self._mode, self._mode, capabilities, account)
        if not verification.verified:
            divergences.extend(f"mode:{code}" for code in verification.reason_codes)
        broker_net = {p.instrument: p.net_units for p in positions if abs(p.net_units) > self._tolerance}
        expected = self._store.expected_net_units(account.account_ref_masked)
        for instrument in sorted(set(broker_net) | set(expected)):
            if abs(broker_net.get(instrument, 0.0) - expected.get(instrument, 0.0)) > self._tolerance:
                divergences.append(
                    f"position_mismatch:{instrument}:broker={broker_net.get(instrument, 0.0)}"
                    f":local={expected.get(instrument, 0.0)}"
                )
        still_unknown = [
            r.intent_id
            for r in self._store.list_orders(limit=500, open_only=True, account_ref=account.account_ref_masked)
            if r.state is OrderState.UNKNOWN
        ]
        if still_unknown:
            divergences.extend(f"order_state_unknown:{intent}" for intent in still_unknown)

        details = {"divergences": divergences, "resolved": resolved, "broker_positions": broker_net, "local_positions": expected}
        if divergences:
            rid = self._store.record_reconciliation("DIVERGENT", details)
            self._controls.freeze(FreezeReason.POSITION_DIVERGENCE, context="reconciliation")
            self._controls.freeze(FreezeReason.RECONCILIATION_REQUIRED, context="reconciliation")
            return ReconciliationResult("DIVERGENT", tuple(divergences), tuple(resolved), rid, self._controls.freezes())
        rid = self._store.record_reconciliation("CONSISTENT", details)
        for reason in RECONCILIATION_OWNED:
            self._controls.clear_freeze(reason, "reconciliation", by_reconciliation=True)
        return ReconciliationResult("CONSISTENT", (), tuple(resolved), rid, self._controls.freezes())

    def resolve_unknown(
        self,
        intent_id: str,
        actor: Actor,
        *,
        outcome: str,
        evidence: str,
        filled_units: float | None = None,
        average_fill_price: float | None = None,
    ) -> OrderRecord:
        """Operator resolution after checking the adapter once more.

        Freezes are not lifted here; call ``reconcile`` afterwards.
        """

        from .resolution import resolve_unknown_order

        record = self._store.get_order(intent_id)
        report = None
        lookup = "not_performed"
        if record is not None:
            try:
                report = self._broker.lookup_order(record.client_order_id)
                lookup = "found" if report is not None else "not_found"
            except BrokerUnavailable:
                lookup = "adapter_unavailable"
        return resolve_unknown_order(
            store=self._store,
            guard=self._guard,
            actor=actor,
            intent_id=intent_id,
            outcome=outcome,
            evidence=evidence,
            filled_units=filled_units,
            average_fill_price=average_fill_price,
            broker_report=report,
            broker_lookup=lookup,
        )

    def _daily_loss_key(self) -> str:
        return _DAILY_LOSS_DAY if self._account_ref is None else f"{_DAILY_LOSS_DAY}:{self._account_ref}"

    def _expire_daily_loss_freeze(self, now: datetime) -> None:
        """The daily-loss freeze lasts for the UTC day in which it was hit."""

        if FreezeReason.DAILY_LOSS_LIMIT.value not in self._controls.freezes():
            return
        frozen_day = self._store.get_control(self._daily_loss_key())
        if frozen_day is not None and frozen_day < now.date().isoformat():
            self._controls.clear_freeze(FreezeReason.DAILY_LOSS_LIMIT, "system:new_trading_day")

    def _abandon_unsent(self, record: OrderRecord) -> None:
        if record.state is OrderState.CREATED:
            self._store.transition(record.intent_id, OrderState.VALIDATING, reason="recovery")
            record = self._store.get_order(record.intent_id)
        if record.state is OrderState.VALIDATING:
            self._store.transition(
                record.intent_id,
                OrderState.RISK_REJECTED,
                reason="abandoned_before_send_after_restart",
                updates={"reason_codes": ("abandoned_after_restart",)},
            )
        elif record.state is OrderState.AUTHORIZED:
            self._store.transition(record.intent_id, OrderState.CANCELLED, reason="abandoned_before_send_after_restart")


def _validate_command(command: object) -> tuple[str, ...]:
    if not isinstance(command, ExecutionCommand):
        return ("invalid_command_type",)
    errors: list[str] = []
    if command.schema_version != COMMAND_SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    for name in ("intent_id", "decision_id", "correlation_id", "account_ref", "strategy", "strategy_version", "instrument"):
        value = getattr(command, name)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            errors.append(f"invalid_{name}")
    if not isinstance(command.mode, RunMode):
        errors.append("invalid_mode")
    if not isinstance(command.direction, Direction):
        errors.append("invalid_direction")
    if command.purpose not in ("ENTRY", "EXIT"):
        errors.append("invalid_purpose")
    for name in ("created_at", "expires_at"):
        value = getattr(command, name)
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            errors.append(f"invalid_{name}")
    if not errors and command.expires_at <= command.created_at:
        errors.append("expiry_not_after_creation")
    if command.purpose == "ENTRY":
        if command.stop_price is None or not _finite(command.stop_price) or command.stop_price <= 0:
            errors.append("entry_requires_stop")
        if "strategy_signal" not in command.checks_passed:
            errors.append("missing_strategy_signal_check")
    if command.requested_units is not None and (not _finite(command.requested_units) or command.requested_units <= 0):
        errors.append("invalid_requested_units")
    if not isinstance(command.checks_passed, tuple) or any(not isinstance(c, str) for c in command.checks_passed):
        errors.append("invalid_checks_passed")
    return tuple(errors)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
