"""Versioned research strategy families producing structured, abstaining decisions.

Strategies only describe a proposed direction at a bar close. They do not size
positions, contact brokers, persist anything, or execute orders; simulated
fills belong to the backtester and every entry still passes the RiskEngine.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import math

from . import research
from .features import average_true_range, donchian_channel, rolling_zscore
from .models import BacktestConfig, CostModel, Direction, MarketBar, Signal
from .regimes import RegimeEstimate, TrendState
from .research import _historical_range_stop, _prepare_bars


MOMENTUM_NAME = "ts_momentum"
MOMENTUM_VERSION = "1.0.0"
BREAKOUT_NAME = "donchian_breakout"
BREAKOUT_VERSION = "1.0.0"
MEAN_REVERSION_NAME = "zscore_mean_reversion"
MEAN_REVERSION_VERSION = "1.0.0"
SMA_BASELINE_NAME = research.STRATEGY_NAME
SMA_BASELINE_VERSION = research.STRATEGY_VERSION


class StrategyAction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True, slots=True)
class StrategyInfo:
    name: str
    version: str
    family: str
    hypothesis: str
    known_limitations: tuple[str, ...]


STRATEGY_REGISTRY: dict[str, StrategyInfo] = {
    MOMENTUM_NAME: StrategyInfo(
        MOMENTUM_NAME,
        MOMENTUM_VERSION,
        "momentum",
        "If the log return over the lookback exceeds a minimum magnitude, the next "
        "horizon_bars return has the same sign more often than chance after costs.",
        ("Untested hypothesis; persistence may vanish after costs or in ranging markets.",),
    ),
    BREAKOUT_NAME: StrategyInfo(
        BREAKOUT_NAME,
        BREAKOUT_VERSION,
        "breakout",
        "A close beyond the prior Donchian channel marks a range expansion that "
        "continues in the breakout direction over horizon_bars.",
        ("False breakouts are common; bar data cannot resolve intrabar order.",),
    ),
    MEAN_REVERSION_NAME: StrategyInfo(
        MEAN_REVERSION_NAME,
        MEAN_REVERSION_VERSION,
        "mean_reversion",
        "In an estimated RANGE regime, a close-price z-score beyond the threshold "
        "tends to revert toward the rolling mean over horizon_bars.",
        ("Depends on an uncertain regime estimate; trends can extend z-scores further.",),
    ),
    SMA_BASELINE_NAME: StrategyInfo(
        SMA_BASELINE_NAME,
        SMA_BASELINE_VERSION,
        "trend_baseline",
        "Reference only: SMA crossovers are a transparent benchmark, not an "
        "established edge.",
        ("Baseline for comparison; no cost or regime gating is applied.",),
    ),
}


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """One close-time decision; ``ABSTAIN`` is a normal, expected outcome.

    ``uncertainty`` is ``1 - regime confidence`` when a regime estimate was
    supplied, otherwise ``None`` (unknown, not zero).
    """

    instrument: str
    timestamp: datetime
    action: StrategyAction
    strategy: str
    strategy_version: str
    horizon_bars: int
    reference_price: float
    invalidation_price: float | None
    estimated_cost_pips: float
    uncertainty: float | None
    reason: str
    abstain_reason: str | None = None
    regime: str | None = None


@dataclass(frozen=True, slots=True)
class MomentumConfig:
    lookback: int = 20
    min_abs_return: float = 0.001
    require_trend_regime: bool = False
    horizon_bars: int = 10
    atr_window: int = 14
    stop_lookback: int = 14
    stop_range_multiple: float = 2.0
    max_cost_to_move_ratio: float = 0.5
    min_regime_confidence: float = 0.0
    version: str = MOMENTUM_VERSION

    def __post_init__(self) -> None:
        _validate_common(self, MOMENTUM_VERSION)
        _require_int(self.lookback, "lookback", 1)
        _require_positive(self.min_abs_return, "min_abs_return", allow_zero=True)
        _require_bool(self.require_trend_regime, "require_trend_regime")


@dataclass(frozen=True, slots=True)
class BreakoutConfig:
    channel_window: int = 20
    require_trend_regime: bool = False
    horizon_bars: int = 10
    atr_window: int = 14
    stop_lookback: int = 14
    stop_range_multiple: float = 2.0
    max_cost_to_move_ratio: float = 0.5
    min_regime_confidence: float = 0.0
    version: str = BREAKOUT_VERSION

    def __post_init__(self) -> None:
        _validate_common(self, BREAKOUT_VERSION)
        _require_int(self.channel_window, "channel_window", 1)
        _require_bool(self.require_trend_regime, "require_trend_regime")


@dataclass(frozen=True, slots=True)
class MeanReversionConfig:
    zscore_window: int = 20
    entry_z: float = 2.0
    horizon_bars: int = 10
    atr_window: int = 14
    stop_lookback: int = 14
    stop_range_multiple: float = 2.0
    max_cost_to_move_ratio: float = 0.5
    min_regime_confidence: float = 0.1
    version: str = MEAN_REVERSION_VERSION

    def __post_init__(self) -> None:
        _validate_common(self, MEAN_REVERSION_VERSION)
        _require_int(self.zscore_window, "zscore_window", 2)
        _require_positive(self.entry_z, "entry_z")


@dataclass(frozen=True, slots=True)
class SmaBaselineConfig:
    fast_window: int = 10
    slow_window: int = 30
    horizon_bars: int = 10
    stop_lookback: int = 14
    stop_range_multiple: float = 2.0
    version: str = SMA_BASELINE_VERSION

    def __post_init__(self) -> None:
        if self.version != SMA_BASELINE_VERSION:
            raise ValueError("version does not match this implementation")
        research._validate_windows(self.fast_window, self.slow_window)
        _require_int(self.horizon_bars, "horizon_bars", 1)
        _require_int(self.stop_lookback, "stop_lookback", 1)
        _require_positive(self.stop_range_multiple, "stop_range_multiple")


def estimated_round_trip_cost_pips(cost_model: CostModel) -> float:
    """Return modelled spread + two-sided slippage + commission, in pips."""

    if not isinstance(cost_model, CostModel):
        raise TypeError("cost_model must be a CostModel")
    values = (
        cost_model.pip_size,
        cost_model.spread_pips,
        cost_model.slippage_pips_per_side,
        cost_model.commission_per_unit_per_side,
    )
    if any(not _finite(value) for value in values) or cost_model.pip_size <= 0:
        raise ValueError("cost model values must be finite with a positive pip size")
    if min(values[1:]) < 0:
        raise ValueError("cost model components must be non-negative")
    return (
        cost_model.spread_pips
        + 2.0 * cost_model.slippage_pips_per_side
        + 2.0 * cost_model.commission_per_unit_per_side / cost_model.pip_size
    )


def evaluate_momentum(
    bars: Iterable[MarketBar],
    config: MomentumConfig | None = None,
    cost_model: CostModel | None = None,
    regimes: Sequence[RegimeEstimate] | None = None,
) -> tuple[StrategyDecision, ...]:
    """Time-series momentum: sign of ``ln(close_i / close_{i-lookback})``."""

    config, cost_model = _defaults(config, cost_model, MomentumConfig)
    history = _prepare_bars(bars)
    aligned = _align_regimes(history, regimes)
    atr = average_true_range(history, config.atr_window)
    cost_pips = estimated_round_trip_cost_pips(cost_model)
    decisions = []
    for index, bar in enumerate(history):
        if index < config.lookback or atr[index] is None:
            decisions.append(_abstain(bar, config, MOMENTUM_NAME, cost_pips, aligned, index, "warmup"))
            continue
        lookback_return = math.log(bar.close / history[index - config.lookback].close)
        if abs(lookback_return) < config.min_abs_return or lookback_return == 0:
            decisions.append(
                _abstain(
                    bar, config, MOMENTUM_NAME, cost_pips, aligned, index,
                    f"below_threshold: |return| {abs(lookback_return):.6f} < {config.min_abs_return}",
                )
            )
            continue
        direction = Direction.LONG if lookback_return > 0 else Direction.SHORT
        reason = f"{config.lookback}-bar log return {lookback_return:+.6f} beyond {config.min_abs_return}"
        decisions.append(
            _gated(
                history, index, direction, reason, config, MOMENTUM_NAME, cost_pips,
                aligned, atr[index], cost_model.pip_size,
                required_trend=_trend_for(direction) if config.require_trend_regime else None,
            )
        )
    return tuple(decisions)


def evaluate_breakout(
    bars: Iterable[MarketBar],
    config: BreakoutConfig | None = None,
    cost_model: CostModel | None = None,
    regimes: Sequence[RegimeEstimate] | None = None,
) -> tuple[StrategyDecision, ...]:
    """Close beyond the Donchian channel of the prior ``channel_window`` bars."""

    config, cost_model = _defaults(config, cost_model, BreakoutConfig)
    history = _prepare_bars(bars)
    aligned = _align_regimes(history, regimes)
    atr = average_true_range(history, config.atr_window)
    highs, lows = donchian_channel(history, config.channel_window)
    cost_pips = estimated_round_trip_cost_pips(cost_model)
    decisions = []
    for index, bar in enumerate(history):
        high, low = highs[index], lows[index]
        if high is None or low is None or atr[index] is None:
            decisions.append(_abstain(bar, config, BREAKOUT_NAME, cost_pips, aligned, index, "warmup"))
            continue
        if bar.close > high:
            direction = Direction.LONG
            reason = f"close {bar.close} above prior {config.channel_window}-bar high {high}"
        elif bar.close < low:
            direction = Direction.SHORT
            reason = f"close {bar.close} below prior {config.channel_window}-bar low {low}"
        else:
            decisions.append(
                _abstain(bar, config, BREAKOUT_NAME, cost_pips, aligned, index, "inside_channel")
            )
            continue
        decisions.append(
            _gated(
                history, index, direction, reason, config, BREAKOUT_NAME, cost_pips,
                aligned, atr[index], cost_model.pip_size,
                required_trend=_trend_for(direction) if config.require_trend_regime else None,
            )
        )
    return tuple(decisions)


def evaluate_mean_reversion(
    bars: Iterable[MarketBar],
    config: MeanReversionConfig | None = None,
    cost_model: CostModel | None = None,
    regimes: Sequence[RegimeEstimate] | None = None,
) -> tuple[StrategyDecision, ...]:
    """Fade a z-score beyond ``entry_z``, only when the regime estimate is RANGE."""

    config, cost_model = _defaults(config, cost_model, MeanReversionConfig)
    history = _prepare_bars(bars)
    aligned = _align_regimes(history, regimes)
    atr = average_true_range(history, config.atr_window)
    zscores = rolling_zscore(history, config.zscore_window)
    cost_pips = estimated_round_trip_cost_pips(cost_model)
    decisions = []
    for index, bar in enumerate(history):
        zscore = zscores[index]
        if zscore is None or atr[index] is None:
            decisions.append(
                _abstain(bar, config, MEAN_REVERSION_NAME, cost_pips, aligned, index, "warmup")
            )
            continue
        if zscore >= config.entry_z:
            direction = Direction.SHORT
        elif zscore <= -config.entry_z:
            direction = Direction.LONG
        else:
            decisions.append(
                _abstain(
                    bar, config, MEAN_REVERSION_NAME, cost_pips, aligned, index,
                    f"zscore_within_band: {zscore:+.3f}",
                )
            )
            continue
        reason = f"{config.zscore_window}-bar z-score {zscore:+.3f} beyond +/-{config.entry_z}"
        decisions.append(
            _gated(
                history, index, direction, reason, config, MEAN_REVERSION_NAME, cost_pips,
                aligned, atr[index], cost_model.pip_size,
                required_trend=TrendState.RANGE,
            )
        )
    return tuple(decisions)


def evaluate_sma_baseline(
    bars: Iterable[MarketBar],
    config: SmaBaselineConfig | None = None,
    cost_model: CostModel | None = None,
) -> tuple[StrategyDecision, ...]:
    """Wrap ``research.generate_sma_crossover_signals`` as reference decisions."""

    config, cost_model = _defaults(config, cost_model, SmaBaselineConfig)
    history = _prepare_bars(bars)
    signals = {
        signal.timestamp: signal
        for signal in research.generate_sma_crossover_signals(
            history, config.fast_window, config.slow_window
        )
    }
    cost_pips = estimated_round_trip_cost_pips(cost_model)
    stop_config = _stop_config(config)
    decisions = []
    for index, bar in enumerate(history):
        signal = signals.get(bar.timestamp)
        if signal is None:
            reason = "warmup" if index < config.slow_window else "no_crossover"
            decisions.append(_abstain(bar, config, SMA_BASELINE_NAME, cost_pips, None, index, reason))
            continue
        decisions.append(
            StrategyDecision(
                instrument=bar.instrument,
                timestamp=bar.timestamp,
                action=StrategyAction(signal.direction.value),
                strategy=SMA_BASELINE_NAME,
                strategy_version=SMA_BASELINE_VERSION,
                horizon_bars=config.horizon_bars,
                reference_price=bar.close,
                invalidation_price=_historical_range_stop(history, index, signal.direction, stop_config),
                estimated_cost_pips=cost_pips,
                uncertainty=None,
                reason=signal.reason,
            )
        )
    return tuple(decisions)


def decisions_to_signals(decisions: Iterable[StrategyDecision]) -> list[Signal]:
    """Convert non-abstaining decisions into close-time ``models.Signal`` values."""

    signals: list[Signal] = []
    for decision in decisions:
        if not isinstance(decision, StrategyDecision):
            raise TypeError("decisions must contain StrategyDecision values")
        if decision.action is StrategyAction.ABSTAIN:
            continue
        signals.append(
            Signal(
                instrument=decision.instrument,
                timestamp=decision.timestamp,
                direction=Direction(decision.action.value),
                reference_price=decision.reference_price,
                strategy=decision.strategy,
                strategy_version=decision.strategy_version,
                reason=decision.reason,
            )
        )
    return signals


def _defaults(config: object | None, cost_model: CostModel | None, config_type: type) -> tuple:
    config = config_type() if config is None else config
    if not isinstance(config, config_type):
        raise TypeError(f"config must be a {config_type.__name__}")
    return config, CostModel() if cost_model is None else cost_model


def _gated(
    history: tuple[MarketBar, ...],
    index: int,
    direction: Direction,
    reason: str,
    config: MomentumConfig | BreakoutConfig | MeanReversionConfig,
    name: str,
    cost_pips: float,
    regimes: tuple[RegimeEstimate, ...] | None,
    atr: float | None,
    pip_size: float,
    *,
    required_trend: TrendState | None,
) -> StrategyDecision:
    bar = history[index]
    regime = None if regimes is None else regimes[index]
    if required_trend is not None:
        if regime is None:
            return _abstain(bar, config, name, cost_pips, regimes, index, "regime_unavailable")
        if regime.trend is not required_trend:
            return _abstain(
                bar, config, name, cost_pips, regimes, index,
                f"regime_mismatch: {regime.trend.value} != {required_trend.value}",
            )
    if regime is not None and regime.confidence < config.min_regime_confidence:
        return _abstain(
            bar, config, name, cost_pips, regimes, index,
            f"low_confidence: {regime.confidence:.3f} < {config.min_regime_confidence}",
        )
    assert atr is not None
    move_proxy_pips = atr / pip_size * math.sqrt(config.horizon_bars)
    if move_proxy_pips <= 0 or cost_pips > config.max_cost_to_move_ratio * move_proxy_pips:
        return _abstain(
            bar, config, name, cost_pips, regimes, index,
            f"cost_exceeds_move_proxy: {cost_pips:.2f} pips vs ATR*sqrt(h) {move_proxy_pips:.2f} pips",
        )
    stop = _historical_range_stop(history, index, direction, _stop_config(config))
    if not _finite(stop) or stop <= 0 or (stop - bar.close) * direction.sign >= 0:
        return _abstain(bar, config, name, cost_pips, regimes, index, "invalid_invalidation_level")
    return StrategyDecision(
        instrument=bar.instrument,
        timestamp=bar.timestamp,
        action=StrategyAction(direction.value),
        strategy=name,
        strategy_version=config.version,
        horizon_bars=config.horizon_bars,
        reference_price=bar.close,
        invalidation_price=stop,
        estimated_cost_pips=cost_pips,
        uncertainty=None if regime is None else 1.0 - regime.confidence,
        reason=reason,
        regime=None if regime is None else regime.trend.value,
    )


def _abstain(
    bar: MarketBar,
    config: MomentumConfig | BreakoutConfig | MeanReversionConfig | SmaBaselineConfig,
    name: str,
    cost_pips: float,
    regimes: tuple[RegimeEstimate, ...] | None,
    index: int,
    abstain_reason: str,
) -> StrategyDecision:
    regime = None if regimes is None else regimes[index]
    return StrategyDecision(
        instrument=bar.instrument,
        timestamp=bar.timestamp,
        action=StrategyAction.ABSTAIN,
        strategy=name,
        strategy_version=config.version,
        horizon_bars=config.horizon_bars,
        reference_price=bar.close,
        invalidation_price=None,
        estimated_cost_pips=cost_pips,
        uncertainty=None if regime is None else 1.0 - regime.confidence,
        reason=f"abstain: {abstain_reason}",
        abstain_reason=abstain_reason,
        regime=None if regime is None else regime.trend.value,
    )


def _align_regimes(
    history: tuple[MarketBar, ...], regimes: Sequence[RegimeEstimate] | None
) -> tuple[RegimeEstimate, ...] | None:
    if regimes is None:
        return None
    aligned = tuple(regimes)
    if any(not isinstance(item, RegimeEstimate) for item in aligned):
        raise TypeError("regimes must contain RegimeEstimate values")
    if tuple(item.timestamp for item in aligned) != tuple(bar.timestamp for bar in history):
        raise ValueError("regimes must align one-to-one with bar timestamps")
    return aligned


def _trend_for(direction: Direction) -> TrendState:
    return TrendState.TREND_UP if direction is Direction.LONG else TrendState.TREND_DOWN


def _stop_config(config: object) -> BacktestConfig:
    return BacktestConfig(
        stop_lookback=config.stop_lookback,  # type: ignore[attr-defined]
        stop_range_multiple=config.stop_range_multiple,  # type: ignore[attr-defined]
    )


def _validate_common(config: object, expected_version: str) -> None:
    if getattr(config, "version") != expected_version:
        raise ValueError("version does not match this implementation")
    _require_int(getattr(config, "horizon_bars"), "horizon_bars", 1)
    _require_int(getattr(config, "atr_window"), "atr_window", 1)
    _require_int(getattr(config, "stop_lookback"), "stop_lookback", 1)
    _require_positive(getattr(config, "stop_range_multiple"), "stop_range_multiple")
    _require_positive(getattr(config, "max_cost_to_move_ratio"), "max_cost_to_move_ratio")
    confidence = getattr(config, "min_regime_confidence")
    if not _finite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("min_regime_confidence must be within [0, 1]")


def _require_int(value: object, name: str, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")


def _require_positive(value: object, name: str, *, allow_zero: bool = False) -> None:
    if not _finite(value) or value < 0 or (value == 0 and not allow_zero):  # type: ignore[operator]
        raise ValueError(f"{name} must be a finite {'non-negative' if allow_zero else 'positive'} number")


def _require_bool(value: object, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
