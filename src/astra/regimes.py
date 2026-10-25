"""Interpretable market-regime estimates with explicit uncertainty.

A regime label is an estimate, not a fact. Volatility, slope, and spread
thresholds are fitted only on a caller-supplied training slice and then
applied unchanged to later rows. Each estimate at row ``i`` uses only the
feature row ``i`` (itself causal) and bar ``i``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .features import FeatureMatrix
from .models import MarketBar
from .research import _prepare_bars

REGIME_MODEL_VERSION = "astra-regimes-1.0.0"


class TrendState(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class VolatilityState(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class SpreadState(StrEnum):
    NORMAL = "NORMAL"
    WIDE = "WIDE"
    UNKNOWN = "UNKNOWN"


class Session(StrEnum):
    """Approximate liquidity windows by fixed UTC hour; DST shifts are ignored."""

    ASIA = "ASIA"
    LONDON = "LONDON"
    LONDON_NEW_YORK_OVERLAP = "LONDON_NEW_YORK_OVERLAP"
    NEW_YORK = "NEW_YORK"
    OFF_HOURS = "OFF_HOURS"


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    """Fixed rule parameters; quantiles define what is fitted on training rows."""

    efficiency_trend_threshold: float = 0.30
    slope_quantile: float = 0.50
    volatility_low_quantile: float = 1.0 / 3.0
    volatility_high_quantile: float = 2.0 / 3.0
    spread_wide_quantile: float = 0.90
    fallback_wide_spread_pips: float = 3.0
    pip_size: float = 0.0001
    minimum_training_rows: int = 30

    def __post_init__(self) -> None:
        for name in (
            "efficiency_trend_threshold",
            "slope_quantile",
            "volatility_low_quantile",
            "volatility_high_quantile",
            "spread_wide_quantile",
        ):
            value = getattr(self, name)
            if not _finite(value) or not 0 < value < 1:
                raise ValueError(f"{name} must be within (0, 1)")
        if self.volatility_low_quantile >= self.volatility_high_quantile:
            raise ValueError("volatility_low_quantile must be below volatility_high_quantile")
        if not _finite(self.fallback_wide_spread_pips) or self.fallback_wide_spread_pips <= 0:
            raise ValueError("fallback_wide_spread_pips must be positive")
        if not _finite(self.pip_size) or self.pip_size <= 0:
            raise ValueError("pip_size must be positive")
        if (
            not isinstance(self.minimum_training_rows, int)
            or isinstance(self.minimum_training_rows, bool)
            or self.minimum_training_rows < 2
        ):
            raise ValueError("minimum_training_rows must be an integer of at least 2")


@dataclass(frozen=True, slots=True)
class RegimeThresholds:
    """Thresholds fitted on a training slice; immutable once fitted."""

    model_version: str
    config: RegimeConfig
    efficiency_trend_threshold: float
    slope_threshold: float
    volatility_low: float
    volatility_high: float
    wide_spread_pips: float
    spread_threshold_source: str
    training_start: datetime
    training_end: datetime
    training_rows: int
    feature_set_version: str
    feature_spec_sha256: str

    def sha256(self) -> str:
        payload = asdict(self)
        payload["training_start"] = self.training_start.isoformat()
        payload["training_end"] = self.training_end.isoformat()
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RegimeEstimate:
    timestamp: datetime
    trend: TrendState
    volatility: VolatilityState
    spread: SpreadState
    session: Session
    confidence: float
    trend_confidence: float
    volatility_confidence: float
    spread_pips: float | None
    reasons: tuple[str, ...]
    model_version: str = REGIME_MODEL_VERSION


def fit_regime_thresholds(
    train_features: FeatureMatrix,
    config: RegimeConfig | None = None,
    train_bars: Iterable[MarketBar] | None = None,
) -> RegimeThresholds:
    """Fit quantile thresholds using only the supplied training rows.

    Pass a feature matrix (and optionally its bars) already truncated to the
    training window. Rows still in warm-up are skipped. The spread threshold
    is fitted only from bars with both bid and ask; otherwise the configured
    fallback is used and recorded as such.
    """

    config = RegimeConfig() if config is None else config
    if not isinstance(train_features, FeatureMatrix):
        raise TypeError("train_features must be a FeatureMatrix")
    if not isinstance(config, RegimeConfig):
        raise TypeError("config must be a RegimeConfig")
    volatility = [v for v in train_features.column("volatility") if v is not None]
    slopes = [abs(v) for v in train_features.column("sma_slope") if v is not None]
    if min(len(volatility), len(slopes)) < config.minimum_training_rows:
        raise ValueError("not enough post-warm-up training rows to fit regime thresholds")

    spread_source = "configured_fallback"
    wide_spread = config.fallback_wide_spread_pips
    if train_bars is not None:
        history = _prepare_bars(train_bars)
        if tuple(bar.timestamp for bar in history) != train_features.timestamps:
            raise ValueError("train_bars must align with train_features timestamps")
        spreads = [s for s in (_spread_pips(bar, config.pip_size) for bar in history) if s is not None]
        if len(spreads) >= config.minimum_training_rows:
            wide_spread = _quantile(spreads, config.spread_wide_quantile)
            spread_source = "training_quantile"

    return RegimeThresholds(
        model_version=REGIME_MODEL_VERSION,
        config=config,
        efficiency_trend_threshold=config.efficiency_trend_threshold,
        slope_threshold=_quantile(slopes, config.slope_quantile),
        volatility_low=_quantile(volatility, config.volatility_low_quantile),
        volatility_high=_quantile(volatility, config.volatility_high_quantile),
        wide_spread_pips=wide_spread,
        spread_threshold_source=spread_source,
        training_start=train_features.timestamps[0],
        training_end=train_features.timestamps[-1],
        training_rows=len(train_features.rows),
        feature_set_version=train_features.feature_set_version,
        feature_spec_sha256=train_features.spec_sha256,
    )


def classify(
    features: FeatureMatrix,
    thresholds: RegimeThresholds,
    bars: Iterable[MarketBar] | None = None,
) -> tuple[RegimeEstimate, ...]:
    """Return one estimate per feature row using the fitted thresholds only.

    Without ``bars`` (or when a bar lacks bid/ask) the spread state is
    ``UNKNOWN``; a spread is never invented from OHLC data.
    """

    if not isinstance(features, FeatureMatrix):
        raise TypeError("features must be a FeatureMatrix")
    if not isinstance(thresholds, RegimeThresholds):
        raise TypeError("thresholds must be RegimeThresholds")
    if thresholds.feature_spec_sha256 != features.spec_sha256:
        raise ValueError("features were computed with a different spec than the thresholds")
    history: tuple[MarketBar, ...] | None = None
    if bars is not None:
        history = _prepare_bars(bars)
        if tuple(bar.timestamp for bar in history) != features.timestamps:
            raise ValueError("bars must align with feature timestamps")
    efficiency = features.column("efficiency_ratio")
    slopes = features.column("sma_slope")
    volatility = features.column("volatility")
    estimates: list[RegimeEstimate] = []
    for index, timestamp in enumerate(features.timestamps):
        reasons: list[str] = []
        trend, trend_confidence = _trend(efficiency[index], slopes[index], thresholds, reasons)
        vol_state, vol_confidence = _volatility(volatility[index], thresholds, reasons)
        spread_value = None if history is None else _spread_pips(history[index], thresholds.config.pip_size)
        if spread_value is None:
            spread_state = SpreadState.UNKNOWN
            reasons.append("spread unknown: bid/ask not available or invalid")
        elif spread_value > thresholds.wide_spread_pips:
            spread_state = SpreadState.WIDE
            reasons.append(f"spread {spread_value:.2f} pips > {thresholds.wide_spread_pips:.2f}")
        else:
            spread_state = SpreadState.NORMAL
            reasons.append(f"spread {spread_value:.2f} pips <= {thresholds.wide_spread_pips:.2f}")
        session = session_label(timestamp)
        reasons.append(f"session {session.value} by UTC hour {timestamp.hour} (approximate)")
        estimates.append(
            RegimeEstimate(
                timestamp=timestamp,
                trend=trend,
                volatility=vol_state,
                spread=spread_state,
                session=session,
                confidence=min(trend_confidence, vol_confidence),
                trend_confidence=trend_confidence,
                volatility_confidence=vol_confidence,
                spread_pips=spread_value,
                reasons=tuple(reasons),
            )
        )
    return tuple(estimates)


def session_label(timestamp: datetime) -> Session:
    """Map a UTC hour to an approximate session.

    ASIA 00-06, LONDON 07-11, LONDON_NEW_YORK_OVERLAP 12-15, NEW_YORK 16-20,
    OFF_HOURS 21-23. Hours are fixed UTC; local DST is not modelled.
    """

    if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    hour = timestamp.astimezone(UTC).hour
    if hour < 7:
        return Session.ASIA
    if hour < 12:
        return Session.LONDON
    if hour < 16:
        return Session.LONDON_NEW_YORK_OVERLAP
    if hour < 21:
        return Session.NEW_YORK
    return Session.OFF_HOURS


def _trend(
    efficiency: float | None,
    slope: float | None,
    thresholds: RegimeThresholds,
    reasons: list[str],
) -> tuple[TrendState, float]:
    if efficiency is None or slope is None:
        reasons.append("trend unknown: features in warm-up")
        return TrendState.UNKNOWN, 0.0
    er_threshold = thresholds.efficiency_trend_threshold
    slope_threshold = thresholds.slope_threshold
    er_distance = _scaled_distance(efficiency, er_threshold, er_threshold)
    slope_distance = _scaled_distance(abs(slope), slope_threshold, slope_threshold)
    trending = efficiency >= er_threshold and abs(slope) >= slope_threshold and slope != 0
    if trending:
        state = TrendState.TREND_UP if slope > 0 else TrendState.TREND_DOWN
        reasons.append(
            f"efficiency {efficiency:.3f} >= {er_threshold:.3f} and |slope| "
            f"{abs(slope):.3g} >= {slope_threshold:.3g}"
        )
        return state, min(er_distance, slope_distance)
    failing = []
    if efficiency < er_threshold:
        failing.append(er_distance)
    if abs(slope) < slope_threshold or slope == 0:
        failing.append(slope_distance)
    reasons.append(
        f"range: efficiency {efficiency:.3f} vs {er_threshold:.3f}, |slope| "
        f"{abs(slope):.3g} vs {slope_threshold:.3g}"
    )
    return TrendState.RANGE, max(failing)


def _volatility(
    value: float | None, thresholds: RegimeThresholds, reasons: list[str]
) -> tuple[VolatilityState, float]:
    if value is None:
        reasons.append("volatility unknown: features in warm-up")
        return VolatilityState.UNKNOWN, 0.0
    low = thresholds.volatility_low
    high = thresholds.volatility_high
    if value < low:
        reasons.append(f"volatility {value:.3g} < training low {low:.3g}")
        return VolatilityState.LOW, _scaled_distance(value, low, low)
    if value > high:
        reasons.append(f"volatility {value:.3g} > training high {high:.3g}")
        return VolatilityState.HIGH, _scaled_distance(value, high, high)
    half_band = (high - low) / 2.0
    reasons.append(f"volatility {value:.3g} within training band [{low:.3g}, {high:.3g}]")
    if half_band <= 0:
        return VolatilityState.NORMAL, 0.0
    return VolatilityState.NORMAL, min(1.0, min(value - low, high - value) / half_band)


def _scaled_distance(value: float, threshold: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return max(0.0, min(1.0, abs(value - threshold) / scale))


def _spread_pips(bar: MarketBar, pip_size: float) -> float | None:
    if bar.bid is None or bar.ask is None:
        return None
    if not (_finite(bar.bid) and _finite(bar.ask)) or bar.ask < bar.bid or bar.bid <= 0:
        return None
    return (bar.ask - bar.bid) / pip_size


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot take a quantile of no values")
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
