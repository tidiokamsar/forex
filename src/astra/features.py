"""Versioned, causal feature library for offline research.

Every per-bar series returned here has the same length as the input bars and
value ``i`` is computed from ``bars[0..i]`` only. Warm-up positions are
``None``. Nothing in this module reads later bars, the wall clock, storage, or
any broker interface.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import math
import statistics

from .models import MarketBar
from .research import _prepare_bars


FEATURE_SET_VERSION = "astra-features-1.0.0"

FEATURE_COLUMNS = (
    "log_return",
    "volatility",
    "atr",
    "rsi",
    "zscore",
    "donchian_high",
    "donchian_low",
    "efficiency_ratio",
    "sma_slope",
)

SCALE_FREE_COLUMNS = (
    "log_return",
    "volatility",
    "rsi",
    "zscore",
    "efficiency_ratio",
    "sma_slope",
)


def log_returns(bars: Iterable[MarketBar]) -> list[float | None]:
    """Return ``ln(close_i / close_{i-1})``; index 0 is ``None``."""

    history = _prepare_bars(bars)
    return _log_returns(history)


def rolling_volatility(bars: Iterable[MarketBar], window: int) -> list[float | None]:
    """Return the sample standard deviation of the last ``window`` log returns.

    The first value appears at index ``window`` (returns 1..window).
    """

    _require_int(window, "window", minimum=2)
    history = _prepare_bars(bars)
    returns = _log_returns(history)
    output: list[float | None] = [None] * len(history)
    for index in range(window, len(history)):
        output[index] = statistics.stdev(returns[index - window + 1 : index + 1])
    return output


def average_true_range(bars: Iterable[MarketBar], window: int) -> list[float | None]:
    """Return Wilder's ATR.

    True range at bar 0 is ``high - low``; later bars also use the prior close.
    The seed at index ``window - 1`` is the simple mean of the first ``window``
    true ranges; afterwards ``ATR_i = (ATR_{i-1} * (n - 1) + TR_i) / n``.
    """

    _require_int(window, "window", minimum=1)
    history = _prepare_bars(bars)
    output: list[float | None] = [None] * len(history)
    true_ranges: list[float] = []
    atr: float | None = None
    for index, bar in enumerate(history):
        if index == 0:
            true_range = bar.high - bar.low
        else:
            previous_close = history[index - 1].close
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        true_ranges.append(true_range)
        if index == window - 1:
            atr = sum(true_ranges) / window
        elif atr is not None:
            atr = (atr * (window - 1) + true_range) / window
        output[index] = atr
    return output


def relative_strength_index(
    bars: Iterable[MarketBar], window: int
) -> list[float | None]:
    """Return Wilder's RSI in [0, 100]; the first value is at index ``window``.

    A window with no losses and some gains is 100; a window with neither gains
    nor losses is 50.
    """

    _require_int(window, "window", minimum=1)
    history = _prepare_bars(bars)
    output: list[float | None] = [None] * len(history)
    average_gain = 0.0
    average_loss = 0.0
    for index in range(1, len(history)):
        change = history[index].close - history[index - 1].close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if index <= window:
            average_gain += gain / window
            average_loss += loss / window
        else:
            average_gain = (average_gain * (window - 1) + gain) / window
            average_loss = (average_loss * (window - 1) + loss) / window
        if index >= window:
            output[index] = _rsi_value(average_gain, average_loss)
    return output


def rolling_zscore(bars: Iterable[MarketBar], window: int) -> list[float | None]:
    """Return ``(close_i - mean) / pstdev`` over the ``window`` closes ending at i.

    A flat window (zero dispersion) yields 0.0.
    """

    _require_int(window, "window", minimum=2)
    history = _prepare_bars(bars)
    closes = [bar.close for bar in history]
    output: list[float | None] = [None] * len(history)
    for index in range(window - 1, len(history)):
        sample = closes[index - window + 1 : index + 1]
        mean = sum(sample) / window
        deviation = statistics.pstdev(sample, mean)
        output[index] = 0.0 if deviation == 0 else (closes[index] - mean) / deviation
    return output


def donchian_channel(
    bars: Iterable[MarketBar], window: int
) -> tuple[list[float | None], list[float | None]]:
    """Return the highest high and lowest low of the PRIOR ``window`` bars.

    The current bar is excluded, so ``close_i > high_i`` is a causal breakout.
    The first value appears at index ``window``.
    """

    _require_int(window, "window", minimum=1)
    history = _prepare_bars(bars)
    highs: list[float | None] = [None] * len(history)
    lows: list[float | None] = [None] * len(history)
    for index in range(window, len(history)):
        prior = history[index - window : index]
        highs[index] = max(bar.high for bar in prior)
        lows[index] = min(bar.low for bar in prior)
    return highs, lows


def efficiency_ratio(bars: Iterable[MarketBar], window: int) -> list[float | None]:
    """Return Kaufman's efficiency ratio in [0, 1]; first value at index ``window``.

    ``|close_i - close_{i-window}| / sum(|close_j - close_{j-1}|)``; a path
    with no movement yields 0.0.
    """

    _require_int(window, "window", minimum=1)
    history = _prepare_bars(bars)
    closes = [bar.close for bar in history]
    output: list[float | None] = [None] * len(history)
    for index in range(window, len(history)):
        path = sum(
            abs(closes[j] - closes[j - 1]) for j in range(index - window + 1, index + 1)
        )
        net = abs(closes[index] - closes[index - window])
        output[index] = 0.0 if path == 0 else min(1.0, net / path)
    return output


def sma_slope(
    bars: Iterable[MarketBar], window: int, lookback: int = 1
) -> list[float | None]:
    """Return the fractional SMA change per bar over ``lookback`` bars.

    ``(SMA_i - SMA_{i-lookback}) / (lookback * SMA_{i-lookback})``; the first
    value appears at index ``window - 1 + lookback``.
    """

    _require_int(window, "window", minimum=1)
    _require_int(lookback, "lookback", minimum=1)
    history = _prepare_bars(bars)
    closes = [bar.close for bar in history]
    sma: list[float | None] = [None] * len(history)
    for index in range(window - 1, len(history)):
        sma[index] = sum(closes[index - window + 1 : index + 1]) / window
    output: list[float | None] = [None] * len(history)
    for index in range(window - 1 + lookback, len(history)):
        previous = sma[index - lookback]
        current = sma[index]
        assert previous is not None and current is not None
        output[index] = (current - previous) / (lookback * previous)
    return output


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """Window parameters for ``compute_feature_matrix``; hashed for provenance."""

    volatility_window: int = 20
    atr_window: int = 14
    rsi_window: int = 14
    zscore_window: int = 20
    donchian_window: int = 20
    efficiency_window: int = 20
    sma_window: int = 20
    sma_slope_lookback: int = 5
    feature_set_version: str = FEATURE_SET_VERSION

    def __post_init__(self) -> None:
        _require_int(self.volatility_window, "volatility_window", minimum=2)
        _require_int(self.atr_window, "atr_window", minimum=1)
        _require_int(self.rsi_window, "rsi_window", minimum=1)
        _require_int(self.zscore_window, "zscore_window", minimum=2)
        _require_int(self.donchian_window, "donchian_window", minimum=1)
        _require_int(self.efficiency_window, "efficiency_window", minimum=1)
        _require_int(self.sma_window, "sma_window", minimum=1)
        _require_int(self.sma_slope_lookback, "sma_slope_lookback", minimum=1)
        if self.feature_set_version != FEATURE_SET_VERSION:
            raise ValueError("feature_set_version does not match this implementation")

    def warmup_bars(self) -> dict[str, int]:
        """Return the first index at which each column is defined."""

        return {
            "log_return": 1,
            "volatility": self.volatility_window,
            "atr": self.atr_window - 1,
            "rsi": self.rsi_window,
            "zscore": self.zscore_window - 1,
            "donchian_high": self.donchian_window,
            "donchian_low": self.donchian_window,
            "efficiency_ratio": self.efficiency_window,
            "sma_slope": self.sma_window - 1 + self.sma_slope_lookback,
        }

    def sha256(self) -> str:
        return _sha256_json(asdict(self))


@dataclass(frozen=True, slots=True)
class FeatureMatrix:
    """Row-aligned causal features; ``rows[i]`` uses bars up to ``timestamps[i]``."""

    instrument: str
    timestamps: tuple[datetime, ...]
    columns: tuple[str, ...]
    rows: tuple[tuple[float | None, ...], ...]
    feature_set_version: str
    spec_sha256: str
    spec: FeatureSpec
    warmup: dict[str, int] = field(default_factory=dict)

    def column(self, name: str) -> list[float | None]:
        position = self._position(name)
        return [row[position] for row in self.rows]

    def select(
        self, columns: Sequence[str], indices: Iterable[int] | None = None
    ) -> list[tuple[float | None, ...]]:
        positions = [self._position(name) for name in columns]
        chosen = range(len(self.rows)) if indices is None else indices
        return [tuple(self.rows[i][p] for p in positions) for i in chosen]

    def slice(self, start: int, stop: int) -> "FeatureMatrix":
        """Return rows ``start:stop`` (e.g. a training window) with the same spec."""

        if not 0 <= start < stop <= len(self.rows):
            raise ValueError("slice bounds must satisfy 0 <= start < stop <= row count")
        return FeatureMatrix(
            instrument=self.instrument,
            timestamps=self.timestamps[start:stop],
            columns=self.columns,
            rows=self.rows[start:stop],
            feature_set_version=self.feature_set_version,
            spec_sha256=self.spec_sha256,
            spec=self.spec,
            warmup={name: max(0, first - start) for name, first in self.warmup.items()},
        )

    def first_complete_index(self, columns: Sequence[str] | None = None) -> int:
        names = self.columns if columns is None else tuple(columns)
        for name in names:
            self._position(name)
        return max((self.warmup[name] for name in names), default=0)

    def _position(self, name: str) -> int:
        try:
            return self.columns.index(name)
        except ValueError as exc:
            raise KeyError(f"unknown feature column: {name}") from exc


def compute_feature_matrix(
    bars: Iterable[MarketBar], spec: FeatureSpec | None = None
) -> FeatureMatrix:
    """Compute every column of ``FEATURE_COLUMNS`` for each bar (default spec when omitted)."""

    spec = FeatureSpec() if spec is None else spec
    if not isinstance(spec, FeatureSpec):
        raise TypeError("spec must be a FeatureSpec")
    history = _prepare_bars(bars)
    highs, lows = donchian_channel(history, spec.donchian_window)
    series = {
        "log_return": log_returns(history),
        "volatility": rolling_volatility(history, spec.volatility_window),
        "atr": average_true_range(history, spec.atr_window),
        "rsi": relative_strength_index(history, spec.rsi_window),
        "zscore": rolling_zscore(history, spec.zscore_window),
        "donchian_high": highs,
        "donchian_low": lows,
        "efficiency_ratio": efficiency_ratio(history, spec.efficiency_window),
        "sma_slope": sma_slope(history, spec.sma_window, spec.sma_slope_lookback),
    }
    rows = tuple(
        tuple(series[name][index] for name in FEATURE_COLUMNS)
        for index in range(len(history))
    )
    return FeatureMatrix(
        instrument=history[0].instrument if history else "",
        timestamps=tuple(bar.timestamp for bar in history),
        columns=FEATURE_COLUMNS,
        rows=rows,
        feature_set_version=FEATURE_SET_VERSION,
        spec_sha256=spec.sha256(),
        spec=spec,
        warmup=spec.warmup_bars(),
    )


class TrainOnlyScaler:
    """Standardize columns using statistics fitted on training rows only.

    ``fit`` must be called with training rows before ``transform``. Rows with
    missing or non-finite values are rejected rather than imputed. A column
    with zero training dispersion keeps scale 1.0 and is recorded in
    ``constant_columns``.
    """

    __slots__ = ("columns", "means", "stds", "fitted_rows", "constant_columns")

    def __init__(self) -> None:
        self.columns: tuple[str, ...] | None = None
        self.means: tuple[float, ...] | None = None
        self.stds: tuple[float, ...] | None = None
        self.fitted_rows = 0
        self.constant_columns: tuple[str, ...] = ()

    @property
    def is_fitted(self) -> bool:
        return self.means is not None

    def fit(
        self, rows: Sequence[Sequence[float | None]], columns: Sequence[str]
    ) -> "TrainOnlyScaler":
        if self.is_fitted:
            raise RuntimeError("scaler is already fitted; create a new scaler per fold")
        names = tuple(columns)
        if not names:
            raise ValueError("columns must not be empty")
        matrix = _validated_rows(rows, len(names))
        if len(matrix) < 2:
            raise ValueError("at least two training rows are required to fit the scaler")
        means: list[float] = []
        stds: list[float] = []
        constant: list[str] = []
        for position, name in enumerate(names):
            values = [row[position] for row in matrix]
            mean = sum(values) / len(values)
            deviation = statistics.pstdev(values, mean)
            if deviation == 0:
                constant.append(name)
                deviation = 1.0
            means.append(mean)
            stds.append(deviation)
        self.columns = names
        self.means = tuple(means)
        self.stds = tuple(stds)
        self.fitted_rows = len(matrix)
        self.constant_columns = tuple(constant)
        return self

    def transform(
        self, rows: Sequence[Sequence[float | None]], columns: Sequence[str] | None = None
    ) -> list[tuple[float, ...]]:
        if self.means is None or self.stds is None or self.columns is None:
            raise RuntimeError("scaler must be fitted on training rows before transform")
        if columns is not None and tuple(columns) != self.columns:
            raise ValueError("columns differ from the columns the scaler was fitted on")
        matrix = _validated_rows(rows, len(self.columns))
        return [
            tuple((value - mean) / std for value, mean, std in zip(row, self.means, self.stds))
            for row in matrix
        ]

    def to_dict(self) -> dict:
        return {
            "columns": list(self.columns or ()),
            "means": list(self.means or ()),
            "stds": list(self.stds or ()),
            "fitted_rows": self.fitted_rows,
            "constant_columns": list(self.constant_columns),
        }


@dataclass(frozen=True, slots=True)
class FeatureIssue:
    code: str
    column: str
    row_index: int
    timestamp: datetime
    value: float | None


_RANGES: dict[str, tuple[float | None, float | None]] = {
    "volatility": (0.0, None),
    "atr": (0.0, None),
    "rsi": (0.0, 100.0),
    "efficiency_ratio": (0.0, 1.0),
    "donchian_high": (0.0, None),
    "donchian_low": (0.0, None),
}


def detect_unexpected_values(matrix: FeatureMatrix) -> tuple[FeatureIssue, ...]:
    """Flag non-finite, out-of-range, inverted-channel, or post-warm-up missing values."""

    issues: list[FeatureIssue] = []
    for row_index, row in enumerate(matrix.rows):
        timestamp = matrix.timestamps[row_index]
        for position, name in enumerate(matrix.columns):
            value = row[position]
            if value is None:
                if row_index >= matrix.warmup.get(name, 0):
                    issues.append(FeatureIssue("MISSING_AFTER_WARMUP", name, row_index, timestamp, None))
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                issues.append(FeatureIssue("NON_FINITE", name, row_index, timestamp, value))
                continue
            lower, upper = _RANGES.get(name, (None, None))
            if (lower is not None and value < lower) or (upper is not None and value > upper):
                issues.append(FeatureIssue("OUT_OF_RANGE", name, row_index, timestamp, value))
        if "donchian_high" in matrix.columns and "donchian_low" in matrix.columns:
            high = row[matrix.columns.index("donchian_high")]
            low = row[matrix.columns.index("donchian_low")]
            if (
                isinstance(high, float)
                and isinstance(low, float)
                and math.isfinite(high)
                and math.isfinite(low)
                and low > high
            ):
                issues.append(FeatureIssue("INVERTED_CHANNEL", "donchian_low", row_index, timestamp, low))
    return tuple(issues)


def _log_returns(history: tuple[MarketBar, ...]) -> list[float | None]:
    output: list[float | None] = [None] * len(history)
    for index in range(1, len(history)):
        output[index] = math.log(history[index].close / history[index - 1].close)
    return output


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _validated_rows(
    rows: Sequence[Sequence[float | None]], width: int
) -> list[tuple[float, ...]]:
    matrix: list[tuple[float, ...]] = []
    for row in rows:
        values = tuple(row)
        if len(values) != width:
            raise ValueError("row width does not match the column count")
        for value in values:
            if (
                value is None
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("rows must contain only finite numbers (warm-up rows excluded)")
        matrix.append(tuple(float(value) for value in values))  # type: ignore[arg-type]
    return matrix


def _require_int(value: object, name: str, *, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")


def _sha256_json(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
