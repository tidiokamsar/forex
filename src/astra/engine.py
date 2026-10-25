"""Paper-trading replay: the end-to-end path on the simulated broker.

Historical or synthetic bars are replayed through a causal decision function,
the Order Gateway (with its mode, risk and account checks) and the
``SimulatedBroker``. No external order can be produced: the only adapter this
module constructs is the in-process simulator.

Per bar ``i`` the replay (1) quotes the open of ``i`` and executes commands
decided at the close of ``i-1``, (2) exits at the stop price when bar ``i``
trades through an open position's stop, and (3) quotes the close of ``i`` and
asks the decision function, which only sees bars ``0..i``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .brokers.simulated import SimulatedBroker
from .execution import ExecutionCommand, ExecutionStore, OrderGateway, OrderState
from .governance import Actor, Role
from .models import BacktestConfig, Direction, MarketBar, RunMode
from .research import _historical_range_stop, _prepare_bars, generate_sma_crossover_signals


PIPELINE_ACTOR = Actor("astra-replay-pipeline", Role.SYSTEM)


@dataclass(frozen=True, slots=True)
class ReplayDecision:
    direction: Direction
    stop_price: float
    strategy: str
    strategy_version: str
    reason: str


DecisionFunction = Callable[[tuple[MarketBar, ...]], ReplayDecision | None]


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    run_id: str
    account_ref: str
    mode: str
    bars: int
    decisions: int
    orders_by_state: dict[str, int]
    rejections: tuple[str, ...]
    initial_balance: float
    final_balance: float
    final_positions: tuple[tuple[str, float], ...]
    reconciliation_start: str
    reconciliation_end: str
    freezes_end: tuple[str, ...]
    data_label: str
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "account_ref": self.account_ref,
            "mode": self.mode,
            "bars": self.bars,
            "decisions": self.decisions,
            "orders_by_state": self.orders_by_state,
            "rejections": list(self.rejections),
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "final_positions": [list(item) for item in self.final_positions],
            "reconciliation_start": self.reconciliation_start,
            "reconciliation_end": self.reconciliation_end,
            "freezes_end": list(self.freezes_end),
            "data_label": self.data_label,
            "limitations": list(self.limitations),
        }


LIMITATIONS = (
    "SIMULATION only: the in-process simulator is the only adapter; no provider is contacted.",
    "Quotes are built from bar prices plus a fixed simulated spread; they are not market quotes.",
    "Bar timestamps act as the clock, so quote age is zero by construction.",
    "Stops are engine-managed and exit at the stop price when the bar range crosses it; gaps exit at the open.",
    "The simulated broker models no swap, financing, margin call or rejection other than scripted faults.",
)


def sma_decisions(config: BacktestConfig = BacktestConfig()) -> DecisionFunction:
    """Reference decision function from the SMA crossover baseline."""

    def decide(history: tuple[MarketBar, ...]) -> ReplayDecision | None:
        if len(history) <= config.slow_window:
            return None
        signals = generate_sma_crossover_signals(history[-(config.slow_window + 2):], config.fast_window, config.slow_window)
        if not signals or signals[-1].timestamp != history[-1].timestamp:
            return None
        signal = signals[-1]
        stop = _historical_range_stop(history, len(history) - 1, signal.direction, config)
        return ReplayDecision(signal.direction, stop, signal.strategy, signal.strategy_version, signal.reason)

    return decide


def strategy_decisions(name: str) -> DecisionFunction:
    """Adapt a strategy family to the replay; only the last bar's decision is used.

    The strategy is re-evaluated on the history available at each close, so a
    decision can never use bars that the replay has not yet reached.
    """

    from . import strategies

    if name == "sma":
        return sma_decisions()
    evaluators = {"momentum": strategies.evaluate_momentum, "breakout": strategies.evaluate_breakout}
    if name not in evaluators:
        raise ValueError("replay strategies: sma, momentum, breakout (mean reversion needs fitted regimes)")
    evaluate = evaluators[name]

    def decide(history: tuple[MarketBar, ...]) -> ReplayDecision | None:
        decision = evaluate(history)[-1]
        if decision.action is strategies.StrategyAction.ABSTAIN or decision.invalidation_price is None:
            return None
        direction = Direction.LONG if decision.action is strategies.StrategyAction.LONG else Direction.SHORT
        return ReplayDecision(direction, decision.invalidation_price, decision.strategy, decision.strategy_version, decision.reason)

    return decide


def run_paper_replay(
    bars: Sequence[MarketBar],
    store: ExecutionStore,
    *,
    decide: DecisionFunction | None = None,
    spread_pips: float = 1.0,
    pip_size: float = 0.0001,
    initial_balance: float = 10_000.0,
    data_label: str = "SYNTHETIC",
    run_id: str | None = None,
) -> ReplaySummary:
    history = _prepare_bars(bars)
    decide = decide or sma_decisions()
    run_id = run_id or uuid.uuid4().hex[:12]
    account_ref = f"SIM-REPLAY-{run_id}"
    clock_state = {"now": history[0].timestamp if history else datetime.now().astimezone()}

    def clock() -> datetime:
        return clock_state["now"]

    broker = SimulatedBroker(initial_balance=initial_balance, account_ref=account_ref, clock=clock)
    gateway = OrderGateway(
        store=store, broker=broker, clock=clock, configured_mode=RunMode.SIMULATION, account_ref=account_ref
    )
    half_spread = spread_pips * pip_size / 2

    def quote(price: float, at: datetime) -> None:
        clock_state["now"] = at
        broker.set_quote(history[0].instrument, price - half_spread, price + half_spread, timestamp=at)

    if history:
        quote(history[0].open, history[0].timestamp)
    start = gateway.reconcile().status
    pending: ReplayDecision | None = None
    position_stop: float | None = None
    position_direction: Direction | None = None
    decisions = 0
    rejections: list[str] = []
    sequence = iter(range(1, 1_000_000_000))

    def submit(direction: Direction, purpose: str, stop: float | None, decision: ReplayDecision | None, at: datetime) -> OrderState | None:
        intent = f"replay:{run_id}:{next(sequence)}:{at.isoformat()}:{purpose}:{direction.value}"
        command = ExecutionCommand(
            intent_id=intent,
            decision_id=f"decision:{intent}",
            correlation_id=f"{run_id}:{at.isoformat()}",
            mode=RunMode.SIMULATION,
            account_ref=account_ref,
            instrument=history[0].instrument,
            direction=direction,
            created_at=at,
            expires_at=at + timedelta(minutes=5),
            strategy=decision.strategy if decision else "engine_exit",
            strategy_version=decision.strategy_version if decision else "1.0.0",
            checks_passed=("strategy_signal",) if purpose == "ENTRY" else (),
            purpose=purpose,
            stop_price=stop,
        )
        result = gateway.submit(command, PIPELINE_ACTOR)
        if result.state is not OrderState.FILLED:
            rejections.append(f"{at.isoformat()}:{purpose}:{','.join(result.reason_codes) or result.state}")
        return result.state

    for index, bar in enumerate(history):
        quote(bar.open, bar.timestamp)
        gateway.process_events()
        if pending is not None:
            decision, pending = pending, None
            if position_direction is not None and decision.direction is not position_direction:
                if submit(decision.direction, "EXIT", None, None, bar.timestamp) is OrderState.FILLED:
                    position_direction, position_stop = None, None
            if position_direction is None:
                if submit(decision.direction, "ENTRY", decision.stop_price, decision, bar.timestamp) is OrderState.FILLED:
                    position_direction, position_stop = decision.direction, decision.stop_price

        if position_direction is not None and position_stop is not None:
            crossed = bar.low <= position_stop if position_direction is Direction.LONG else bar.high >= position_stop
            if crossed:
                gapped = bar.open <= position_stop if position_direction is Direction.LONG else bar.open >= position_stop
                quote(bar.open if gapped else position_stop, bar.timestamp)
                exit_direction = Direction.SHORT if position_direction is Direction.LONG else Direction.LONG
                if submit(exit_direction, "EXIT", None, None, bar.timestamp) is OrderState.FILLED:
                    position_direction, position_stop = None, None

        quote(bar.close, bar.timestamp)
        decision = decide(history[: index + 1])
        if decision is not None:
            decisions += 1
            if position_direction is None or decision.direction is not position_direction:
                pending = decision

    if history and position_direction is not None:
        last = history[-1]
        quote(last.close, last.timestamp)
        exit_direction = Direction.SHORT if position_direction is Direction.LONG else Direction.LONG
        submit(exit_direction, "EXIT", None, None, last.timestamp)

    end = gateway.reconcile()
    orders = store.list_orders(limit=500, account_ref=account_ref)
    by_state: dict[str, int] = {}
    for order in orders:
        by_state[order.state.value] = by_state.get(order.state.value, 0) + 1
    account = broker.account_snapshot()
    store.audit(
        "paper_replay.completed",
        {"run_id": run_id, "account_ref": account_ref, "orders": by_state, "reconciliation": end.status},
    )
    return ReplaySummary(
        run_id=run_id,
        account_ref=account_ref,
        mode=RunMode.SIMULATION.value,
        bars=len(history),
        decisions=decisions,
        orders_by_state=by_state,
        rejections=tuple(rejections),
        initial_balance=initial_balance,
        final_balance=account.balance,
        final_positions=tuple((p.instrument, p.net_units) for p in broker.open_positions()),
        reconciliation_start=start,
        reconciliation_end=end.status,
        freezes_end=end.freezes_after,
        data_label=data_label,
        limitations=LIMITATIONS,
    )
