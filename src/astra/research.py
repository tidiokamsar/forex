"""Dependency-free reference strategies and event-time backtesting.

This module is for deterministic research only. It does not connect to a
broker or submit orders.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import math
from typing import Any

from .models import (
    BacktestConfig,
    BacktestResult,
    Direction,
    MarketBar,
    OrderIntent,
    RiskContext,
    RunMode,
    Signal,
    Trade,
)
from .risk import RiskEngine


STRATEGY_NAME = "sma_crossover"
STRATEGY_VERSION = "1.0.0"

_ASSUMPTIONS = (
    "Signals use only closes through the signal bar; entries and close-based reversal exits are simulated at the next bar open.",
    "Trade prices and gross P&L use unadjusted bar reference prices; configured spread, slippage, and commission are itemized simulation estimates, not broker-reported costs.",
    "The stop is fixed from the high-low range of up to stop_lookback bars ending at the signal bar; an open gap through an existing stop is processed before a reversal at that open, and intrabar stops precede close-based strategy exits.",
    "At most one position is held; daily loss uses realized net P&L grouped by each event timestamp's calendar date.",
    "Historical bars are fresh at their simulated event time; wall-clock age is not applied.",
    "The offline backtest assumes a verified simulation account, known illustrative instrument specifications, and available simulated margin; these flags do not establish real account, contract, or broker-margin facts.",
    "Quote-currency P&L is treated as account-currency P&L one-to-one; no FX conversion, financing, swap, margin constraint, or intrabar mark-to-market is modeled.",
    "Risk sizing treats one price unit times one trade unit as one quote-currency unit; the instrument specification is illustrative.",
    "Spread is charged as half the configured spread on each side; slippage and commission are charged per side.",
)


def generate_sma_crossover_signals(
    bars: Iterable[MarketBar], fast_window: int, slow_window: int
) -> list[Signal]:
    """Return close-time SMA crossover signals, without peeking at later bars.

    A signal at index ``i`` is computed from the current and immediately prior
    moving-average windows, both of which end no later than bar ``i``. The
    simulator consumes it at the next bar's open.
    """

    history = _prepare_bars(bars)
    _validate_windows(fast_window, slow_window)
    if not history:
        return []

    closes = [bar.close for bar in history]
    signals: list[Signal] = []
    # The previous slow average must exist, so the earliest comparison is at
    # index slow_window. The current averages then include the current close.
    for index in range(slow_window, len(history)):
        # ``end_exclusive=index`` makes the previous window end at bar i-1.
        fast_previous = _mean(closes, index, fast_window)
        slow_previous = _mean(closes, index, slow_window)
        fast_current = _mean(closes, index + 1, fast_window)
        slow_current = _mean(closes, index + 1, slow_window)

        direction: Direction | None = None
        if fast_previous <= slow_previous and fast_current > slow_current:
            direction = Direction.LONG
        elif fast_previous >= slow_previous and fast_current < slow_current:
            direction = Direction.SHORT

        if direction is not None:
            bar = history[index]
            signals.append(
                Signal(
                    instrument=bar.instrument,
                    timestamp=bar.timestamp,
                    direction=direction,
                    reference_price=bar.close,
                    strategy=STRATEGY_NAME,
                    strategy_version=STRATEGY_VERSION,
                    reason=(
                        f"{fast_window}-bar SMA crossed "
                        f"{'above' if direction is Direction.LONG else 'below'} "
                        f"{slow_window}-bar SMA at bar close"
                    ),
                )
            )
    return signals


def run_sma_backtest(
    bars: Iterable[MarketBar],
    config: BacktestConfig = BacktestConfig(),
    risk_engine: RiskEngine | None = None,
    provenance: dict[str, Any] | None = None,
    evaluation_start_time: datetime | None = None,
) -> BacktestResult:
    """Run the SMA reference strategy using historical event time only.

    Every pending entry is evaluated by ``RiskEngine``. The simulated account
    is synthetic and the returned costs and P&L are estimates under the
    assumptions listed in ``BacktestResult.assumptions``.
    """

    history = _prepare_bars(bars)
    _validate_config(config)
    engine = risk_engine or RiskEngine()
    metadata = dict(provenance or {})
    metadata.setdefault(
        "backtest_config",
        {
            "initial_equity": config.initial_equity,
            "fast_window": config.fast_window,
            "slow_window": config.slow_window,
            "stop_lookback": config.stop_lookback,
            "stop_range_multiple": config.stop_range_multiple,
            "cost_model": {
                "pip_size": config.cost_model.pip_size,
                "spread_pips": config.cost_model.spread_pips,
                "slippage_pips_per_side": config.cost_model.slippage_pips_per_side,
                "commission_per_unit_per_side": config.cost_model.commission_per_unit_per_side,
            },
        },
    )
    metadata.setdefault("evaluation_classification", "EXPLORATORY_NON_PROMOTABLE")

    if not history:
        return _empty_result(config, metadata)

    if evaluation_start_time is None:
        evaluation_start_index = 0
    else:
        if (
            not isinstance(evaluation_start_time, datetime)
            or evaluation_start_time.tzinfo is None
            or evaluation_start_time.utcoffset() is None
        ):
            raise ValueError("evaluation_start_time must be timezone-aware")
        try:
            evaluation_start_index = next(
                index
                for index, bar in enumerate(history)
                if bar.timestamp == evaluation_start_time
            )
        except StopIteration as exc:
            raise ValueError("evaluation_start_time must match a bar timestamp") from exc
    metadata["evaluation_start_time"] = history[evaluation_start_index].timestamp
    metadata["warmup_observations"] = evaluation_start_index

    signals = generate_sma_crossover_signals(
        history, config.fast_window, config.slow_window
    )
    signals_at = {signal.timestamp: signal for signal in signals}
    index_at = {bar.timestamp: index for index, bar in enumerate(history)}

    equity = config.initial_equity
    day_start_equity = equity
    day_net_pnl = 0.0
    event_day: date | None = None
    position: _OpenPosition | None = None
    pending_signal: Signal | None = None
    trades: list[Trade] = []
    risk_rejections: list[str] = []
    equity_curve = [equity]

    def advance_event_day(timestamp: datetime) -> None:
        nonlocal event_day, day_start_equity, day_net_pnl
        bar_day = timestamp.date()
        if bar_day != event_day:
            event_day = bar_day
            day_start_equity = equity
            day_net_pnl = 0.0

    def close_position(
        timestamp: datetime, reference_price: float, reason: str
    ) -> None:
        nonlocal position, equity, day_net_pnl
        if position is None:
            return
        trade = _close_trade(
            position=position,
            timestamp=timestamp,
            exit_price=reference_price,
            exit_reason=reason,
            config=config,
        )
        trades.append(trade)
        equity += trade.net_pnl
        day_net_pnl += trade.net_pnl
        equity_curve.append(equity)
        position = None

    # Start on the bar immediately before the evaluation window so a signal
    # formed at that close may fill at the first evaluation bar's open. Any
    # earlier account state is deliberately reset for this independent window.
    loop_start = max(0, evaluation_start_index - 1)
    for index in range(loop_start, len(history)):
        bar = history[index]
        advance_event_day(bar.timestamp)

        # A close-time signal from the preceding bar becomes eligible at this
        # bar's open. It is never filled at its own signal-bar close.
        if pending_signal is not None:
            signal = pending_signal
            pending_signal = None
            if position is not None and signal.direction is not position.signal.direction:
                # A close-time reversal signal exits at the next event's open.
                # Only an open gap can stop the old position before that open
                # exit; later intrabar prices belong to the new position.
                gap_stop = _open_gap_stop_fill_price(position, bar)
                if gap_stop is not None:
                    close_position(bar.timestamp, gap_stop, "stop_loss")
                else:
                    close_position(bar.timestamp, bar.open, "strategy_exit")
            signal_index = index_at[signal.timestamp]
            stop_price = _historical_range_stop(
                history, signal_index, signal.direction, config
            )
            intent = OrderIntent(
                intent_id=(
                    f"{signal.instrument}:{signal.timestamp.isoformat()}:"
                    f"{signal.direction.value}:{STRATEGY_VERSION}"
                ),
                instrument=signal.instrument,
                direction=signal.direction,
                entry_price=bar.open,
                stop_price=stop_price,
                created_at=signal.timestamp,
            )
            policy = engine.policy
            context = RiskContext(
                mode=RunMode.SIMULATION,
                account_mode=RunMode.SIMULATION,
                account_verified=True,
                # This is the age of the current simulated event, not the
                # number of wall-clock seconds since a historical timestamp.
                data_age_seconds=0.0,
                spread_pips=config.cost_model.spread_pips,
                equity=equity,
                day_start_equity=day_start_equity,
                day_net_pnl=day_net_pnl,
                open_positions=int(position is not None),
                instrument_specs_known=True,
                margin_available=True,
            )
            decision = engine.evaluate(intent, context)
            if decision.allowed and position is None:
                position = _OpenPosition(
                    signal=signal,
                    entry_time=bar.timestamp,
                    entry_price=bar.open,
                    stop_price=stop_price,
                    units=decision.approved_units,
                )
            elif not decision.allowed:
                risk_rejections.append(
                    f"{signal.timestamp.isoformat()}:"
                    f"{','.join(decision.reason_codes)}"
                )
            else:
                # The simulator enforces one position even if a caller
                # supplies a policy configured with a larger position limit.
                risk_rejections.append(
                    f"{signal.timestamp.isoformat()}:simulator_position_limit"
                )

        # Stops have precedence over a close-time strategy exit. If the bar
        # opens beyond a stop, the open is used to represent adverse gap fill.
        if position is not None:
            stop_fill = _stop_fill_price(position, bar)
            if stop_fill is not None:
                close_position(bar.timestamp, stop_fill, "stop_loss")

        signal = signals_at.get(bar.timestamp)
        if signal is not None:
            if position is not None and signal.direction is not position.signal.direction:
                pending_signal = signal
            elif position is None:
                pending_signal = signal

    # There is no future bar for a signal on the final close. Any position
    # that did enter is closed at the last available reference close.
    if position is not None:
        final_bar = history[-1]
        advance_event_day(final_bar.timestamp)
        close_position(final_bar.timestamp, final_bar.close, "end_of_data")

    gross_pnl = sum(trade.gross_pnl for trade in trades)
    total_costs = sum(trade.costs for trade in trades)
    net_pnl = sum(trade.net_pnl for trade in trades)
    final_equity = config.initial_equity + net_pnl
    peak = equity_curve[0]
    max_drawdown_pct = 0.0
    for curve_equity in equity_curve[1:]:
        peak = max(peak, curve_equity)
        if peak > 0:
            max_drawdown_pct = max(
                max_drawdown_pct, (peak - curve_equity) / peak * 100.0
            )

    winners = sum(trade.net_pnl > 0 for trade in trades)
    positive_net = sum(trade.net_pnl for trade in trades if trade.net_pnl > 0)
    negative_net = -sum(trade.net_pnl for trade in trades if trade.net_pnl < 0)
    profit_factor = (
        positive_net / negative_net
        if trades and negative_net > 0
        else None
    )

    return BacktestResult(
        strategy=STRATEGY_NAME,
        strategy_version=STRATEGY_VERSION,
        instrument=history[0].instrument,
        start=history[evaluation_start_index].timestamp,
        end=history[-1].timestamp,
        initial_equity=config.initial_equity,
        final_equity=final_equity,
        trade_count=len(trades),
        gross_pnl=gross_pnl,
        total_costs=total_costs,
        net_pnl=net_pnl,
        max_drawdown_pct=max_drawdown_pct,
        win_rate=(winners / len(trades) if trades else None),
        profit_factor=profit_factor,
        trades=tuple(trades),
        risk_rejections=tuple(risk_rejections),
        assumptions=_ASSUMPTIONS,
        observations=len(history) - evaluation_start_index,
        provenance=metadata,
    )


@dataclass(slots=True)
class _OpenPosition:
    signal: Signal
    entry_time: datetime
    entry_price: float
    stop_price: float
    units: float


def _prepare_bars(bars: Iterable[MarketBar]) -> tuple[MarketBar, ...]:
    supplied = tuple(bars)
    if not supplied:
        return ()

    if not isinstance(supplied[0], MarketBar):
        raise TypeError("bars must contain MarketBar values")
    history: list[MarketBar] = []
    instrument: str | None = None
    previous_timestamp: datetime | None = None
    for supplied_bar in supplied:
        bar = supplied_bar
        if not isinstance(bar, MarketBar):
            raise TypeError("bars must contain MarketBar values")
        if not isinstance(bar.timestamp, datetime):
            raise TypeError("bar timestamps must be datetime values")
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            raise ValueError("bar timestamps must be timezone-aware")
        if not isinstance(bar.instrument, str) or not bar.instrument.strip():
            raise ValueError("all bars must have the same non-empty instrument")
        canonical_instrument = bar.instrument.strip().upper()
        if instrument is None:
            instrument = canonical_instrument
        if canonical_instrument != instrument:
            raise ValueError("all bars must have the same non-empty instrument")
        bar = replace(
            bar,
            instrument=canonical_instrument,
            timestamp=bar.timestamp.astimezone(timezone.utc),
        )
        if not math.isfinite(bar.open) or not math.isfinite(bar.high):
            raise ValueError("bar OHLC prices must be finite positive numbers")
        if not math.isfinite(bar.low) or not math.isfinite(bar.close):
            raise ValueError("bar OHLC prices must be finite positive numbers")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise ValueError("bar OHLC prices must be finite positive numbers")
        if bar.high < max(bar.open, bar.low, bar.close):
            raise ValueError("bar high must be at least the open, low, and close")
        if bar.low > min(bar.open, bar.high, bar.close):
            raise ValueError("bar low must be at most the open, high, and close")
        if previous_timestamp is not None:
            try:
                ordered = bar.timestamp > previous_timestamp
            except TypeError as exc:
                raise ValueError("bar timestamps must be comparable") from exc
            if not ordered:
                raise ValueError("bar timestamps must be strictly increasing")
        previous_timestamp = bar.timestamp
        history.append(bar)
    return tuple(history)


def _validate_windows(fast_window: int, slow_window: int) -> None:
    if not isinstance(fast_window, int) or isinstance(fast_window, bool) or fast_window < 1:
        raise ValueError("fast_window must be a positive integer")
    if not isinstance(slow_window, int) or isinstance(slow_window, bool) or slow_window < 2:
        raise ValueError("slow_window must be an integer of at least 2")
    if fast_window >= slow_window:
        raise ValueError("fast_window must be smaller than slow_window")


def _validate_config(config: BacktestConfig) -> None:
    _validate_windows(config.fast_window, config.slow_window)
    if not isinstance(config.stop_lookback, int) or isinstance(config.stop_lookback, bool) or config.stop_lookback < 1:
        raise ValueError("stop_lookback must be a positive integer")
    numeric_values = (
        config.initial_equity,
        config.stop_range_multiple,
        config.cost_model.pip_size,
        config.cost_model.spread_pips,
        config.cost_model.slippage_pips_per_side,
        config.cost_model.commission_per_unit_per_side,
    )
    if any(not math.isfinite(value) for value in numeric_values):
        raise ValueError("backtest configuration values must be finite")
    if config.initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    if config.stop_range_multiple <= 0:
        raise ValueError("stop_range_multiple must be positive")
    if config.cost_model.pip_size <= 0:
        raise ValueError("cost_model.pip_size must be positive")
    if min(
        config.cost_model.spread_pips,
        config.cost_model.slippage_pips_per_side,
        config.cost_model.commission_per_unit_per_side,
    ) < 0:
        raise ValueError("spread, slippage, and commission must be non-negative")


def _mean(values: list[float], end_exclusive: int, window: int) -> float:
    return sum(values[end_exclusive - window : end_exclusive]) / window


def _historical_range_stop(
    bars: tuple[MarketBar, ...],
    signal_index: int,
    direction: Direction,
    config: BacktestConfig,
) -> float:
    first = max(0, signal_index - config.stop_lookback + 1)
    window = bars[first : signal_index + 1]
    trailing_range = max(bar.high for bar in window) - min(bar.low for bar in window)
    offset = trailing_range * config.stop_range_multiple
    signal_close = bars[signal_index].close
    return signal_close - offset if direction is Direction.LONG else signal_close + offset


def _stop_fill_price(position: _OpenPosition, bar: MarketBar) -> float | None:
    if position.signal.direction is Direction.LONG:
        if bar.open <= position.stop_price:
            return bar.open
        if bar.low <= position.stop_price:
            return position.stop_price
    else:
        if bar.open >= position.stop_price:
            return bar.open
        if bar.high >= position.stop_price:
            return position.stop_price
    return None


def _open_gap_stop_fill_price(
    position: _OpenPosition, bar: MarketBar
) -> float | None:
    """Return an adverse open-gap stop fill without inspecting later prices."""

    if position.signal.direction is Direction.LONG and bar.open <= position.stop_price:
        return bar.open
    if position.signal.direction is Direction.SHORT and bar.open >= position.stop_price:
        return bar.open
    return None


def _close_trade(
    *,
    position: _OpenPosition,
    timestamp: datetime,
    exit_price: float,
    exit_reason: str,
    config: BacktestConfig,
) -> Trade:
    costs = _round_trip_cost(position.units, config)
    gross_pnl = (
        exit_price - position.entry_price
    ) * position.signal.direction.sign * position.units
    return Trade(
        instrument=position.signal.instrument,
        direction=position.signal.direction,
        entry_time=position.entry_time,
        exit_time=timestamp,
        entry_price=position.entry_price,
        exit_price=exit_price,
        units=position.units,
        gross_pnl=gross_pnl,
        costs=costs,
        net_pnl=gross_pnl - costs,
        exit_reason=exit_reason,
    )


def _round_trip_cost(units: float, config: BacktestConfig) -> float:
    model = config.cost_model
    per_side = (
        model.spread_pips * model.pip_size / 2.0
        + model.slippage_pips_per_side * model.pip_size
        + model.commission_per_unit_per_side
    )
    return 2.0 * units * per_side


def _empty_result(config: BacktestConfig, provenance: dict[str, Any]) -> BacktestResult:
    return BacktestResult(
        strategy=STRATEGY_NAME,
        strategy_version=STRATEGY_VERSION,
        instrument="",
        start=None,
        end=None,
        initial_equity=config.initial_equity,
        final_equity=config.initial_equity,
        trade_count=0,
        gross_pnl=0.0,
        total_costs=0.0,
        net_pnl=0.0,
        max_drawdown_pct=0.0,
        win_rate=None,
        profit_factor=None,
        trades=(),
        risk_rejections=(),
        assumptions=_ASSUMPTIONS,
        observations=0,
        provenance=provenance,
    )
