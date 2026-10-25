"""Generic event-time backtesting of precomputed close-time signals.

The simulation rules mirror ``research.run_sma_backtest`` exactly and reuse
its private helpers: next-bar-open fills, a RiskEngine check per entry with a
simulated context, a trailing range stop, gap-stop precedence, itemized cost
estimates, at most one position, and an ``end_of_data`` close. No broker,
storage, or execution code is imported.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
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
from .research import (
    _ASSUMPTIONS as _SMA_ASSUMPTIONS,
)
from .research import (
    _close_trade,
    _historical_range_stop,
    _open_gap_stop_fill_price,
    _OpenPosition,
    _prepare_bars,
    _stop_fill_price,
    _validate_config,
)
from .risk import RiskEngine

SIGNAL_BACKTEST_VERSION = "1.0.0"

_ASSUMPTIONS = (
    "Signals are supplied precomputed at bar close; their causality is the caller's responsibility. "
    "Entries and close-based reversal exits are simulated at the next bar open.",
    *_SMA_ASSUMPTIONS[1:],
)


def run_signal_backtest(
    bars: Iterable[MarketBar],
    signals: Iterable[Signal],
    config: BacktestConfig,
    strategy_name: str,
    strategy_version: str,
    risk_engine: RiskEngine | None = None,
    provenance: dict[str, Any] | None = None,
    evaluation_start_time: datetime | None = None,
) -> BacktestResult:
    """Simulate close-time ``signals`` over ``bars`` with SMA-backtest semantics.

    ``config.fast_window`` and ``config.slow_window`` are validated but unused.
    Every signal must name ``strategy_name``/``strategy_version``, match the
    bar instrument, fall on a bar timestamp, and be unique per timestamp.
    """

    history = _prepare_bars(bars)
    _validate_config(config)
    for value, label in ((strategy_name, "strategy_name"), (strategy_version, "strategy_version")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
    engine = risk_engine or RiskEngine()
    signals_at = _index_signals(history, signals, strategy_name, strategy_version)
    metadata = dict(provenance or {})
    metadata.setdefault(
        "backtest_config",
        {
            "initial_equity": config.initial_equity,
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
    metadata["signal_backtest_version"] = SIGNAL_BACKTEST_VERSION
    metadata["signal_count"] = len(signals_at)

    if not history:
        return _result(strategy_name, strategy_version, config, history, 0, [], [], [config.initial_equity], metadata)

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
                index for index, bar in enumerate(history) if bar.timestamp == evaluation_start_time
            )
        except StopIteration as exc:
            raise ValueError("evaluation_start_time must match a bar timestamp") from exc
    metadata["evaluation_start_time"] = history[evaluation_start_index].timestamp
    metadata["warmup_observations"] = evaluation_start_index

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
        if timestamp.date() != event_day:
            event_day = timestamp.date()
            day_start_equity = equity
            day_net_pnl = 0.0

    def close_position(timestamp: datetime, reference_price: float, reason: str) -> None:
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

    for index in range(max(0, evaluation_start_index - 1), len(history)):
        bar = history[index]
        advance_event_day(bar.timestamp)

        if pending_signal is not None:
            signal = pending_signal
            pending_signal = None
            if position is not None and signal.direction is not position.signal.direction:
                gap_stop = _open_gap_stop_fill_price(position, bar)
                if gap_stop is not None:
                    close_position(bar.timestamp, gap_stop, "stop_loss")
                else:
                    close_position(bar.timestamp, bar.open, "strategy_exit")
            stop_price = _historical_range_stop(
                history, index_at[signal.timestamp], signal.direction, config
            )
            intent = OrderIntent(
                intent_id=(
                    f"{signal.instrument}:{signal.timestamp.isoformat()}:"
                    f"{signal.direction.value}:{strategy_version}"
                ),
                instrument=signal.instrument,
                direction=signal.direction,
                entry_price=bar.open,
                stop_price=stop_price,
                created_at=signal.timestamp,
            )
            context = RiskContext(
                mode=RunMode.SIMULATION,
                account_mode=RunMode.SIMULATION,
                account_verified=True,
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
                    f"{signal.timestamp.isoformat()}:{','.join(decision.reason_codes)}"
                )
            else:
                risk_rejections.append(f"{signal.timestamp.isoformat()}:simulator_position_limit")

        if position is not None:
            stop_fill = _stop_fill_price(position, bar)
            if stop_fill is not None:
                close_position(bar.timestamp, stop_fill, "stop_loss")

        signal = signals_at.get(bar.timestamp)
        if signal is not None and (
            position is None or signal.direction is not position.signal.direction
        ):
            pending_signal = signal

    if position is not None:
        final_bar = history[-1]
        advance_event_day(final_bar.timestamp)
        close_position(final_bar.timestamp, final_bar.close, "end_of_data")

    return _result(
        strategy_name, strategy_version, config, history, evaluation_start_index,
        trades, risk_rejections, equity_curve, metadata,
    )


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    lower: float
    upper: float
    confidence: float
    resamples: int
    seed: int
    method: str = "percentile bootstrap of mean net P&L per trade"


@dataclass(frozen=True, slots=True)
class ExtendedMetrics:
    """Per-trade statistics; ratios are per trade and NOT annualized."""

    trade_count: int
    expectancy: float | None
    average_win: float | None
    average_loss: float | None
    median_trade: float | None
    per_trade_sharpe_like: float | None
    per_trade_sortino_like: float | None
    max_consecutive_losses: int
    mean_net_pnl_ci: BootstrapInterval | None
    convention: str = "per-trade net P&L in account currency; ratios not annualized"


def extended_metrics(
    result: BacktestResult,
    *,
    confidence: float = 0.95,
    resamples: int = 2000,
    seed: int = 0,
) -> ExtendedMetrics:
    """Summarize net trade P&L; ``None`` marks statistics the sample cannot support."""

    if not isinstance(result, BacktestResult):
        raise TypeError("result must be a BacktestResult")
    if not (isinstance(confidence, float) and 0 < confidence < 1):
        raise ValueError("confidence must be a float within (0, 1)")
    if not isinstance(resamples, int) or isinstance(resamples, bool) or resamples < 100:
        raise ValueError("resamples must be an integer of at least 100")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    pnl = [trade.net_pnl for trade in result.trades]
    count = len(pnl)
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    longest = current = 0
    for value in pnl:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)

    sharpe = None
    sortino = None
    interval = None
    if count >= 2:
        mean = statistics.fmean(pnl)
        deviation = statistics.stdev(pnl)
        sharpe = mean / deviation if deviation > 0 else None
        downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in pnl) / count)
        sortino = mean / downside if downside > 0 else None
        rng = random.Random(seed)
        means = sorted(
            statistics.fmean(rng.choices(pnl, k=count)) for _ in range(resamples)
        )
        tail = (1.0 - confidence) / 2.0
        interval = BootstrapInterval(
            lower=_percentile(means, tail),
            upper=_percentile(means, 1.0 - tail),
            confidence=confidence,
            resamples=resamples,
            seed=seed,
        )
    return ExtendedMetrics(
        trade_count=count,
        expectancy=statistics.fmean(pnl) if pnl else None,
        average_win=statistics.fmean(wins) if wins else None,
        average_loss=statistics.fmean(losses) if losses else None,
        median_trade=statistics.median(pnl) if pnl else None,
        per_trade_sharpe_like=sharpe,
        per_trade_sortino_like=sortino,
        max_consecutive_losses=longest,
        mean_net_pnl_ci=interval,
    )


def _index_signals(
    history: tuple[MarketBar, ...],
    signals: Iterable[Signal],
    strategy_name: str,
    strategy_version: str,
) -> dict[datetime, Signal]:
    timestamps = {bar.timestamp for bar in history}
    instrument = history[0].instrument if history else None
    indexed: dict[datetime, Signal] = {}
    for signal in signals:
        if not isinstance(signal, Signal):
            raise TypeError("signals must contain Signal values")
        if not isinstance(signal.direction, Direction):
            raise ValueError("signal direction must be a Direction")
        if signal.strategy != strategy_name or signal.strategy_version != strategy_version:
            raise ValueError("signal strategy/version does not match the backtest label")
        if (
            not isinstance(signal.timestamp, datetime)
            or signal.timestamp.tzinfo is None
            or signal.timestamp.utcoffset() is None
        ):
            raise ValueError("signal timestamps must be timezone-aware")
        if not isinstance(signal.instrument, str) or signal.instrument.strip().upper() != instrument:
            raise ValueError("signal instrument does not match the bars")
        if signal.timestamp not in timestamps:
            raise ValueError("signal timestamp does not match any bar timestamp")
        if signal.timestamp in indexed:
            raise ValueError("at most one signal per bar timestamp is allowed")
        indexed[signal.timestamp] = signal
    return indexed


def _result(
    strategy_name: str,
    strategy_version: str,
    config: BacktestConfig,
    history: tuple[MarketBar, ...],
    evaluation_start_index: int,
    trades: Sequence[Trade],
    risk_rejections: Sequence[str],
    equity_curve: Sequence[float],
    metadata: dict[str, Any],
) -> BacktestResult:
    net_pnl = sum(trade.net_pnl for trade in trades)
    peak = equity_curve[0]
    max_drawdown_pct = 0.0
    for curve_equity in equity_curve[1:]:
        peak = max(peak, curve_equity)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - curve_equity) / peak * 100.0)
    winners = sum(trade.net_pnl > 0 for trade in trades)
    positive_net = sum(trade.net_pnl for trade in trades if trade.net_pnl > 0)
    negative_net = -sum(trade.net_pnl for trade in trades if trade.net_pnl < 0)
    return BacktestResult(
        strategy=strategy_name,
        strategy_version=strategy_version,
        instrument=history[0].instrument if history else "",
        start=history[evaluation_start_index].timestamp if history else None,
        end=history[-1].timestamp if history else None,
        initial_equity=config.initial_equity,
        final_equity=config.initial_equity + net_pnl,
        trade_count=len(trades),
        gross_pnl=sum(trade.gross_pnl for trade in trades),
        total_costs=sum(trade.costs for trade in trades),
        net_pnl=net_pnl,
        max_drawdown_pct=max_drawdown_pct,
        win_rate=(winners / len(trades) if trades else None),
        profit_factor=(positive_net / negative_net if trades and negative_net > 0 else None),
        trades=tuple(trades),
        risk_rejections=tuple(risk_rejections),
        assumptions=_ASSUMPTIONS,
        observations=len(history) - evaluation_start_index,
        provenance=metadata,
    )


def _percentile(ordered: Sequence[float], q: float) -> float:
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
