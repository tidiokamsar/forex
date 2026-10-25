"""Continuous trading service: the supervised loop that runs without a Claude session.

One ``TradingService`` drives one account through one broker adapter:

    tick: connection check (+ reconnect when the adapter supports it)
          -> broker events -> quote -> bar builder
    bar close: strategy decision (bars 0..i only) -> stop management
               -> ExecutionCommand -> OrderGateway (all safety checks)
    always: reconcile at start, every N bars, right after a reconnect, and
            while a reconciliation-owned freeze is active (at most every
            ``reconcile_retry_seconds`` of service time); heartbeat persisted

Intent ids are derived from (account, strategy, bar, side, purpose), so a
restart that replays the last bar cannot create a second order: the gateway
returns the stored intent. Repeated tick errors put the service in degraded
mode (entries frozen until a human clears it). This module never builds a
broker itself; callers choose the adapter, and the gateway's mode checks and
the adapters' execution boundary still apply.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .brokers.base import BrokerAdapter, BrokerRequestError, BrokerTimeout, BrokerUnavailable, ConnectionState
from .brokers.simulated import SimulatedBroker
from .engine import DecisionFunction, ReplayDecision
from .execution import ExecutionCommand, ExecutionStore, FreezeReason, OrderGateway, OrderState
from .execution.controls import RECONCILIATION_OWNED
from .governance import Actor, Role
from .logs import get_logger, log_event
from .models import Direction, MarketBar, RunMode

_LOG = get_logger("service")
SERVICE_ACTOR = Actor("astra-trading-service", Role.SYSTEM)
STATE_PREFIX = "service:"
MAX_CONSECUTIVE_ERRORS = 5


class BarBuilder:
    """Aggregates mid prices into fixed-duration bars; a bar is emitted only after it closes."""

    def __init__(self, instrument: str, timeframe_seconds: int) -> None:
        if timeframe_seconds <= 0:
            raise ValueError("timeframe_seconds must be positive")
        self.instrument = instrument.upper()
        self.timeframe = timeframe_seconds
        self._current: dict | None = None

    def _bucket(self, ts: datetime) -> datetime:
        epoch = int(ts.timestamp())
        return datetime.fromtimestamp(epoch - epoch % self.timeframe, tz=UTC)

    def update(self, bid: float, ask: float, ts: datetime) -> MarketBar | None:
        mid = (bid + ask) / 2
        bucket = self._bucket(ts)
        closed = None
        current = self._current
        if current is not None and bucket > current["start"]:
            closed = MarketBar(
                instrument=self.instrument,
                timestamp=current["start"],
                open=current["open"],
                high=current["high"],
                low=current["low"],
                close=current["close"],
                bid=current["bid"],
                ask=current["ask"],
            )
            current = None
        if current is None:
            current = {"start": bucket, "open": mid, "high": mid, "low": mid, "close": mid}
        current["high"] = max(current["high"], mid)
        current["low"] = min(current["low"], mid)
        current["close"] = mid
        current["bid"], current["ask"] = bid, ask
        self._current = current
        return closed


class SyntheticQuoteFeed:
    """SYNTHETIC random-walk quotes for running the service on the simulator.

    It also owns the simulated clock. The prices are generated, not market data.
    """

    label = "SYNTHETIC"

    def __init__(
        self,
        broker: SimulatedBroker,
        *,
        instrument: str = "EURUSD",
        start: datetime | None = None,
        step_seconds: float = 5.0,
        start_price: float = 1.1000,
        volatility: float = 0.00008,
        spread_pips: float = 1.0,
        seed: int = 7,
    ) -> None:
        self.broker = broker
        self.instrument = instrument
        self.now = start or datetime(2026, 1, 5, 7, 0, tzinfo=UTC)
        self.step = timedelta(seconds=step_seconds)
        self.price = start_price
        self.volatility = volatility
        self.half_spread = spread_pips * 0.0001 / 2
        self._random = random.Random(seed)
        self._push()

    def clock(self) -> datetime:
        return self.now

    def advance(self) -> None:
        self.now += self.step
        self.price = max(0.5, self.price + self._random.gauss(0.0, self.volatility))
        self._push()

    def _push(self) -> None:
        self.broker.set_quote(self.instrument, self.price - self.half_spread, self.price + self.half_spread, timestamp=self.now)


@dataclass(slots=True)
class ServiceStats:
    ticks: int = 0
    bars: int = 0
    decisions: int = 0
    submitted: dict[str, int] = field(default_factory=dict)
    reconciliations: dict[str, int] = field(default_factory=dict)
    reconnects: int = 0
    errors: int = 0
    degraded: bool = False


class TradingService:
    def __init__(
        self,
        *,
        store: ExecutionStore,
        broker: BrokerAdapter,
        decide: DecisionFunction,
        account_ref: str,
        mode: RunMode = RunMode.SIMULATION,
        instrument: str = "EURUSD",
        timeframe_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
        reconcile_every_bars: int = 15,
        history_limit: int = 500,
        reconnect: Callable[[], object] | None = None,
        reconcile_retry_seconds: float = 30.0,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        self.reconcile_retry_seconds = reconcile_retry_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._last_reconcile_attempt: datetime | None = None
        self._last_status: str | None = None
        self._last_beat = 0.0
        self.store = store
        self.broker = broker
        self.decide = decide
        self.account_ref = account_ref
        self.mode = mode
        self.instrument = instrument.upper()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.gateway = OrderGateway(store=store, broker=broker, clock=self.clock, configured_mode=mode, account_ref=account_ref)
        self.bars = BarBuilder(self.instrument, timeframe_seconds)
        self.reconcile_every = max(1, reconcile_every_bars)
        self.history_limit = history_limit
        self.history: list[MarketBar] = []
        self.stats = ServiceStats()
        self._reconnect = reconnect or getattr(broker, "reconnect", None)
        self._state_key = f"{STATE_PREFIX}{account_ref}"
        self._consecutive_errors = 0
        self._reconnect_blocked = False
        self._stop = threading.Event()
        state = store.get_state(self._state_key, {})
        self.last_bar = datetime.fromisoformat(state["last_bar"]) if state.get("last_bar") else None
        self.stop_price: float | None = state.get("stop_price")

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> str:
        """Reconcile before anything else; entries stay frozen unless consistent."""

        status = self._reconcile("startup")
        log_event(_LOG, "service.started", account_ref=self.account_ref, mode=self.mode.value, reconciliation=status,
                  last_bar=self.last_bar.isoformat() if self.last_bar else None)
        self._heartbeat("running")
        return status

    def stop(self) -> None:
        self._stop.set()

    def run(
        self,
        *,
        advance: Callable[[], None] | None = None,
        max_ticks: int | None = None,
        tick_interval_seconds: float = 0.0,
    ) -> ServiceStats:
        """Loop until ``stop()`` or ``max_ticks``. ``advance`` moves a simulated feed."""

        self.start()
        try:
            while not self._stop.is_set() and (max_ticks is None or self.stats.ticks < max_ticks):
                if advance is not None:
                    advance()
                self.tick()
                if tick_interval_seconds > 0 and self._stop.wait(tick_interval_seconds):
                    break
        finally:
            self._heartbeat("stopped")
            log_event(_LOG, "service.stopped", account_ref=self.account_ref, ticks=self.stats.ticks, bars=self.stats.bars,
                      submitted=self.stats.submitted, errors=self.stats.errors)
        return self.stats

    # -- one iteration ---------------------------------------------------------

    def tick(self) -> None:
        self.stats.ticks += 1
        self._rearm_if_cleared()
        try:
            self._tick()
            self._consecutive_errors = 0
        except BrokerRequestError as exc:
            # A provider refusal such as a rate limit is transient: log, do not count.
            log_event(_LOG, "service.request_refused", level=logging.WARNING, error_code=exc.error_code)
        except Exception as exc:  # noqa: BLE001 - the loop must survive and report any failure
            self.stats.errors += 1
            self._consecutive_errors += 1
            _LOG.exception("service tick failed", extra={"event": "service.tick_failed", "error": type(exc).__name__})
            if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                self._enter_degraded(f"{self._consecutive_errors} consecutive tick errors")
        self._heartbeat("degraded" if self.stats.degraded else "running")

    def _enter_degraded(self, context: str) -> None:
        if self.stats.degraded:
            return
        self.stats.degraded = True
        self.gateway.controls.freeze(FreezeReason.SERVICE_DEGRADED, context=context)
        log_event(_LOG, "service.degraded", level=logging.CRITICAL, context=context)

    def _rearm_if_cleared(self) -> None:
        """After a human clears service_degraded, errors are counted again from zero."""

        if self.stats.degraded and FreezeReason.SERVICE_DEGRADED.value not in self.gateway.controls.freezes():
            self.stats.degraded = False
            self._consecutive_errors = 0
            self._reconnect_blocked = False
            log_event(_LOG, "service.rearmed", account_ref=self.account_ref)

    def _tick(self) -> None:
        if self.gateway.check_connection() is not ConnectionState.CONNECTED:
            if self._reconnect is None or self._reconnect_blocked:
                return
            try:
                self._reconnect()
            except (BrokerUnavailable, BrokerTimeout) as exc:
                if getattr(exc, "permanent", False) is True:
                    # Retrying cannot fix this (live account, configuration, token): stop and ask a human.
                    self._reconnect_blocked = True
                    self._enter_degraded(f"permanent reconnect failure: {type(exc).__name__}")
                log_event(_LOG, "service.reconnect_failed", level=logging.WARNING, error=str(exc),
                          permanent=self._reconnect_blocked)
                return
            if self.broker.connection_state() is not ConnectionState.CONNECTED:
                return
            self.stats.reconnects += 1
            self._last_reconcile_attempt = None  # reconcile right away after a reconnect
        if self._frozen_for_reconciliation() and self._reconcile_due():
            self._reconcile("recovery")
        self.gateway.process_events()
        try:
            quote = self.broker.quote(self.instrument)
        except (BrokerUnavailable, BrokerRequestError) as exc:
            log_event(_LOG, "service.quote_unavailable", level=logging.WARNING, error=str(exc))
            return
        bar = self.bars.update(quote.bid, quote.ask, quote.timestamp)
        if bar is not None:
            self._on_bar(bar)

    def _on_bar(self, bar: MarketBar) -> None:
        self.history.append(bar)
        del self.history[: -self.history_limit]
        self.stats.bars += 1
        if self.last_bar is not None and bar.timestamp <= self.last_bar:
            return  # already processed before a restart
        if self.stats.bars % self.reconcile_every == 0:
            self._reconcile("periodic")
        self._manage_stop(bar)
        decision = self.decide(tuple(self.history))
        if decision is not None:
            self.stats.decisions += 1
            self._act(decision, bar)
        self.last_bar = bar.timestamp
        self._save_state()

    # -- decisions and orders ----------------------------------------------------

    def _net_position(self) -> float:
        return sum(p.net_units for p in self.broker.open_positions() if p.instrument == self.instrument)

    def _manage_stop(self, bar: MarketBar) -> None:
        position = self._net_position()
        if not position or self.stop_price is None:
            return
        crossed = bar.low <= self.stop_price if position > 0 else bar.high >= self.stop_price
        if crossed:
            exit_side = Direction.SHORT if position > 0 else Direction.LONG
            state = self._submit(exit_side, "EXIT", None, None, bar, tag="stop")
            if state is OrderState.FILLED:
                self.stop_price = None

    def _act(self, decision: ReplayDecision, bar: MarketBar) -> None:
        position = self._net_position()
        holding = Direction.LONG if position > 0 else Direction.SHORT if position < 0 else None
        if holding is decision.direction:
            return
        if holding is not None:
            exit_side = Direction.SHORT if holding is Direction.LONG else Direction.LONG
            if self._submit(exit_side, "EXIT", None, decision, bar, tag="reverse") is not OrderState.FILLED:
                return
            self.stop_price = None
        if self._submit(decision.direction, "ENTRY", decision.stop_price, decision, bar) is OrderState.FILLED:
            self.stop_price = decision.stop_price

    def _submit(self, direction, purpose, stop, decision, bar, tag="signal") -> OrderState | None:
        strategy = decision.strategy if decision else "service_exit"
        version = decision.strategy_version if decision else "1"
        intent = f"svc:{self.account_ref}:{strategy}:{bar.timestamp.isoformat()}:{purpose}:{direction.value}:{tag}"
        now = self.clock()
        command = ExecutionCommand(
            intent_id=intent,
            decision_id=f"decision:{intent}",
            correlation_id=f"{self.account_ref}:{bar.timestamp.isoformat()}",
            mode=self.mode,
            account_ref=self.account_ref,
            instrument=self.instrument,
            direction=direction,
            created_at=now,
            expires_at=now + timedelta(minutes=2),
            strategy=strategy,
            strategy_version=version,
            checks_passed=("strategy_signal",) if purpose == "ENTRY" else (),
            purpose=purpose,
            stop_price=stop,
        )
        result = self.gateway.submit(command, SERVICE_ACTOR)
        key = result.state.value if result.state else "SCHEMA_REJECTED"
        self.stats.submitted[key] = self.stats.submitted.get(key, 0) + 1
        return result.state

    # -- persistence -------------------------------------------------------------

    def _frozen_for_reconciliation(self) -> bool:
        return bool(set(self.gateway.controls.freezes()) & {reason.value for reason in RECONCILIATION_OWNED})

    def _reconcile_due(self) -> bool:
        last = self._last_reconcile_attempt
        return last is None or (self.clock() - last).total_seconds() >= self.reconcile_retry_seconds

    def _reconcile(self, reason: str) -> str:
        self._last_reconcile_attempt = self.clock()
        try:
            status = self.gateway.reconcile().status
        except (BrokerUnavailable, BrokerTimeout, BrokerRequestError) as exc:
            log_event(_LOG, "service.reconcile_error", level=logging.WARNING, reason=reason, error=str(exc))
            status = "FAILED"
        self.stats.reconciliations[status] = self.stats.reconciliations.get(status, 0) + 1
        return status

    def _save_state(self) -> None:
        state = self.store.get_state(self._state_key, {})
        state.update(last_bar=self.last_bar.isoformat() if self.last_bar else None, stop_price=self.stop_price)
        self.store.put_state(self._state_key, state)

    def _heartbeat(self, status: str) -> None:
        # Throttled: written on status change, otherwise every heartbeat_seconds.
        now = time.monotonic()
        if status == self._last_status and now - self._last_beat < self.heartbeat_seconds:
            return
        self._last_status, self._last_beat = status, now
        state = self.store.get_state(self._state_key, {})
        state.update(
            status=status,
            heartbeat_at=datetime.now(UTC).isoformat(),
            simulated_time=self.clock().isoformat(),
            ticks=self.stats.ticks,
            bars=self.stats.bars,
            errors=self.stats.errors,
            mode=self.mode.value,
            last_bar=self.last_bar.isoformat() if self.last_bar else None,
            stop_price=self.stop_price,
        )
        self.store.put_state(self._state_key, state)


def service_status(store: ExecutionStore, *, stale_after_seconds: float = 120.0) -> list[dict]:
    """Heartbeat view for operators and the dashboard."""

    now = datetime.now(UTC)
    result = []
    for key, value, _updated in store.states_with_prefix(STATE_PREFIX):
        beat = value.get("heartbeat_at")
        age = (now - datetime.fromisoformat(beat)).total_seconds() if beat else None
        result.append({
            "account_ref": key[len(STATE_PREFIX):],
            **value,
            "heartbeat_age_seconds": round(age, 1) if age is not None else None,
            "alive": value.get("status") in ("running", "degraded") and age is not None and age <= stale_after_seconds,
        })
    return result
