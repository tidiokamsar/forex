"""In-process simulated broker with explicit fault injection.

This adapter never contacts a provider. Every quote it serves is supplied by
the caller and labelled ``SIMULATED``; it is a test double for the gateway,
not market data and not evidence about any real broker.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import RLock

from ..instruments import DEFAULT_SPECS, InstrumentSpec
from ..models import Direction, RunMode
from .base import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerEnvironment,
    BrokerEvent,
    BrokerOrderReport,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerTimeout,
    BrokerUnavailable,
    Capabilities,
    CapabilityNotSupported,
    ConnectionState,
    EnvironmentSource,
    Quote,
)


SUBMIT_BEHAVIORS = frozenset(
    {"fill", "reject", "partial", "accept_only", "timeout_after_accept", "timeout_before_accept"}
)


@dataclass(slots=True)
class _Position:
    units: float = 0.0
    average_price: float = 0.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SimulatedBroker(BrokerAdapter):
    ADAPTER_NAME = "astra-simulated-broker"
    ADAPTER_VERSION = "1.0.0"

    def __init__(
        self,
        *,
        initial_balance: float = 10_000.0,
        account_ref: str = "SIM-****0001",
        currency: str = "USD",
        specs: dict[str, InstrumentSpec] | None = None,
        commission_per_unit: float = 0.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._specs = dict(DEFAULT_SPECS if specs is None else specs)
        self._balance = float(initial_balance)
        self._account_ref = account_ref
        self._currency = currency
        self._commission = float(commission_per_unit)
        self._clock = clock
        self._lock = RLock()
        self._connected = True
        self._environment = BrokerEnvironment.SIMULATION
        self._environment_source = EnvironmentSource.SIMULATOR
        self._declared_environment = BrokerEnvironment.SIMULATION
        self._quotes: dict[str, Quote] = {}
        self._orders: dict[str, BrokerOrderReport] = {}
        self._requests: dict[str, BrokerOrderRequest] = {}
        self._positions: dict[str, _Position] = {}
        self._events: list[BrokerEvent] = []
        self._sequence = itertools.count(1)
        self._broker_ids = itertools.count(1)
        self._next_behaviors: list[tuple[str, float]] = []
        self._reverse_delivery = False
        self.submit_calls = 0

    # -- fault injection and test controls -------------------------------

    def set_quote(self, instrument: str, bid: float, ask: float, timestamp: datetime | None = None) -> None:
        with self._lock:
            self._quotes[instrument.upper()] = Quote(
                instrument=instrument.upper(),
                bid=bid,
                ask=ask,
                timestamp=timestamp or self._clock(),
                source="SIMULATED",
            )

    def script_next_submit(self, behavior: str, fill_fraction: float = 0.5) -> None:
        if behavior not in SUBMIT_BEHAVIORS:
            raise ValueError(f"unknown behavior {behavior!r}")
        with self._lock:
            self._next_behaviors.append((behavior, fill_fraction))

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def reconnect(self) -> None:
        with self._lock:
            self._connected = True

    def report_environment(
        self,
        environment: BrokerEnvironment,
        source: EnvironmentSource = EnvironmentSource.SIMULATOR,
        *,
        declared: BrokerEnvironment | None = None,
    ) -> None:
        """Make the simulator claim another environment, to test mode guards."""

        with self._lock:
            self._environment = environment
            self._environment_source = source
            if declared is not None:
                self._declared_environment = declared

    def deliver_events_in_reverse(self, enabled: bool = True) -> None:
        with self._lock:
            self._reverse_delivery = enabled

    def inject_position(self, instrument: str, units: float, price: float) -> None:
        """Create a position the gateway did not order (external divergence)."""

        with self._lock:
            self._apply_fill(instrument.upper(), units, price)

    def complete_partial(self, client_order_id: str) -> BrokerOrderReport:
        with self._lock:
            report = self._orders[client_order_id]
            if report.status is not BrokerOrderStatus.PARTIALLY_FILLED:
                raise ValueError("order is not partially filled")
            remaining = report.requested_units - report.filled_units
            price = report.average_fill_price or 0.0
            signed = remaining if self._direction_of(client_order_id) is Direction.LONG else -remaining
            self._apply_fill(self._instrument_of(client_order_id), signed, price)
            return self._record(
                replace(
                    report,
                    status=BrokerOrderStatus.FILLED,
                    filled_units=report.requested_units,
                    message="remaining quantity filled",
                )
            )

    def fill_accepted(self, client_order_id: str) -> BrokerOrderReport:
        with self._lock:
            report = self._orders[client_order_id]
            if report.status is not BrokerOrderStatus.ACCEPTED:
                raise ValueError("order is not in ACCEPTED state")
            request = self._requests[client_order_id]
            price = self._fill_price(request)
            signed = request.units if request.direction is Direction.LONG else -request.units
            self._apply_fill(request.instrument, signed, price)
            return self._record(
                replace(
                    report,
                    status=BrokerOrderStatus.FILLED,
                    filled_units=request.units,
                    average_fill_price=price,
                    message="filled",
                )
            )

    # -- BrokerAdapter ----------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            adapter_name=self.ADAPTER_NAME,
            adapter_version=self.ADAPTER_VERSION,
            declared_environment=self._declared_environment,
            environment_source=EnvironmentSource.SIMULATOR,
            instruments=tuple(sorted(self._specs)),
            order_types=("MARKET",),
            supports_order_lookup_by_client_id=True,
            supports_cancel=True,
            supports_modify=False,
            supports_positions=True,
            supports_event_stream=True,
            supports_quotes=True,
            supports_historical_data=False,
            limitations=(
                "In-process simulator; no provider connection.",
                "Quotes are caller-supplied SIMULATED values.",
                "Market orders only; no swaps, financing, or margin call model.",
            ),
        )

    def connection_state(self) -> ConnectionState:
        return ConnectionState.CONNECTED if self._connected else ConnectionState.DISCONNECTED

    def account_snapshot(self) -> AccountSnapshot:
        with self._lock:
            self._require_connection()
            unrealized = 0.0
            margin_used = 0.0
            for instrument, position in self._positions.items():
                if position.units == 0:
                    continue
                quote = self._quotes.get(instrument)
                mark = (quote.bid + quote.ask) / 2 if quote else position.average_price
                unrealized += (mark - position.average_price) * position.units
                spec = self._specs.get(instrument)
                if spec is not None:
                    margin_used += abs(position.units) * mark * spec.margin_rate
            equity = self._balance + unrealized
            return AccountSnapshot(
                account_ref_masked=self._account_ref,
                environment_reported=self._environment,
                environment_source=self._environment_source,
                currency=self._currency,
                balance=self._balance,
                equity=equity,
                margin_used=margin_used,
                margin_free=equity - margin_used,
                as_of=self._clock(),
            )

    def quote(self, instrument: str) -> Quote:
        with self._lock:
            self._require_connection()
            quote = self._quotes.get(instrument.upper())
            if quote is None:
                raise BrokerUnavailable(f"no simulated quote for {instrument}")
            return quote

    def _boundary_now(self) -> datetime:
        return self._clock()

    def _environment_guard(self, request: BrokerOrderRequest, permit) -> str | None:
        # The simulator only executes permits issued for SIMULATION, and only
        # while it declares itself a simulator.
        if permit.mode is not RunMode.SIMULATION:
            return "simulator_requires_simulation_permit"
        if self._declared_environment is not BrokerEnvironment.SIMULATION:
            return "simulator_not_in_simulation_environment"
        return None

    def _send_order(self, request: BrokerOrderRequest) -> BrokerOrderReport:
        with self._lock:
            self.submit_calls += 1
            self._require_connection()
            existing = self._orders.get(request.client_order_id)
            if existing is not None:
                # Providers with client-id uniqueness return the original order.
                return existing
            if request.order_type != "MARKET":
                raise CapabilityNotSupported(f"order type {request.order_type} is not supported")
            behavior, fraction = (
                self._next_behaviors.pop(0) if self._next_behaviors else ("fill", 0.0)
            )
            if behavior == "timeout_before_accept":
                raise BrokerTimeout("simulated timeout before the order reached the broker")
            self._requests[request.client_order_id] = request
            broker_id = f"SIM-{next(self._broker_ids)}"
            base = BrokerOrderReport(
                client_order_id=request.client_order_id,
                broker_order_id=broker_id,
                status=BrokerOrderStatus.ACCEPTED,
                requested_units=request.units,
                filled_units=0.0,
                average_fill_price=None,
                sequence=0,
                timestamp=self._clock(),
                message="accepted",
            )
            if behavior == "reject" or request.instrument not in self._specs:
                return self._record(replace(base, status=BrokerOrderStatus.REJECTED, message="rejected by simulator"))
            if request.instrument not in self._quotes:
                return self._record(replace(base, status=BrokerOrderStatus.REJECTED, message="no quote"))
            accepted = self._record(base)
            if behavior == "accept_only":
                return accepted
            price = self._fill_price(request)
            if behavior == "partial":
                filled = round(request.units * fraction, 8)
                signed = filled if request.direction is Direction.LONG else -filled
                self._apply_fill(request.instrument, signed, price)
                return self._record(
                    replace(
                        base,
                        status=BrokerOrderStatus.PARTIALLY_FILLED,
                        filled_units=filled,
                        average_fill_price=price,
                        message="partially filled",
                    )
                )
            signed = request.units if request.direction is Direction.LONG else -request.units
            self._apply_fill(request.instrument, signed, price)
            filled_report = self._record(
                replace(
                    base,
                    status=BrokerOrderStatus.FILLED,
                    filled_units=request.units,
                    average_fill_price=price,
                    message="filled",
                )
            )
            if behavior == "timeout_after_accept":
                raise BrokerTimeout("simulated timeout after the broker accepted the order")
            return filled_report

    def lookup_order(self, client_order_id: str) -> BrokerOrderReport | None:
        with self._lock:
            self._require_connection()
            return self._orders.get(client_order_id)

    def open_positions(self) -> tuple[BrokerPosition, ...]:
        with self._lock:
            self._require_connection()
            return tuple(
                BrokerPosition(instrument, position.units, position.average_price)
                for instrument, position in sorted(self._positions.items())
                if abs(position.units) > 1e-9
            )

    def cancel_order(self, client_order_id: str) -> BrokerOrderReport:
        with self._lock:
            self._require_connection()
            report = self._orders.get(client_order_id)
            if report is None:
                raise ValueError("unknown order")
            if report.status in (BrokerOrderStatus.FILLED, BrokerOrderStatus.REJECTED, BrokerOrderStatus.CANCELLED):
                return report
            return self._record(replace(report, status=BrokerOrderStatus.CANCELLED, message="cancelled"))

    def poll_events(self, after_sequence: int) -> tuple[BrokerEvent, ...]:
        with self._lock:
            self._require_connection()
            events = [event for event in self._events if event.sequence > after_sequence]
            if self._reverse_delivery:
                events.reverse()
            return tuple(events)

    # -- internals ----------------------------------------------------------

    def _require_connection(self) -> None:
        if not self._connected:
            raise BrokerUnavailable("simulated connection is down")

    def _record(self, report: BrokerOrderReport) -> BrokerOrderReport:
        sequence = next(self._sequence)
        stamped = replace(report, sequence=sequence, timestamp=self._clock())
        self._orders[report.client_order_id] = stamped
        self._events.append(BrokerEvent(sequence=sequence, report=stamped))
        return stamped

    def _fill_price(self, request: BrokerOrderRequest) -> float:
        quote = self._quotes[request.instrument]
        return quote.ask if request.direction is Direction.LONG else quote.bid

    def _direction_of(self, client_order_id: str) -> Direction:
        return self._requests[client_order_id].direction

    def _instrument_of(self, client_order_id: str) -> str:
        return self._requests[client_order_id].instrument

    def _apply_fill(self, instrument: str, signed_units: float, price: float) -> None:
        position = self._positions.setdefault(instrument, _Position())
        self._balance -= abs(signed_units) * self._commission
        if position.units == 0 or (position.units > 0) == (signed_units > 0):
            total = position.units + signed_units
            position.average_price = (
                (position.average_price * position.units + price * signed_units) / total
                if total
                else 0.0
            )
            position.units = total
            return
        closing = min(abs(signed_units), abs(position.units))
        direction = 1 if position.units > 0 else -1
        self._balance += (price - position.average_price) * closing * direction
        remainder = abs(signed_units) - closing
        if remainder > 1e-9:
            position.units = remainder * (1 if signed_units > 0 else -1)
            position.average_price = price
        else:
            position.units -= closing * direction
            if abs(position.units) < 1e-9:
                position.units = 0.0
                position.average_price = 0.0
