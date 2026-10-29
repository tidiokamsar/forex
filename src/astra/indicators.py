"""Technical indicators on closing prices: SMA, RSI (Wilder) and MACD, with their standard definitions.

Every function returns a list the same length as its input, with ``None`` while there is not enough history
(so the lists line up with the candles of a chart). Pure functions, standard library only: nothing here reads
a store, a broker or the network.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
SMA_FAST, SMA_SLOW = 20, 50


def _check(values: Sequence[float], period: int) -> None:
    if period < 1:
        raise ValueError("period must be at least 1")
    if any(v is None or not math.isfinite(v) for v in values):
        raise ValueError("closing prices must be finite numbers")


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Simple moving average of the last ``period`` values."""

    _check(values, period)
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first ``period`` values."""

    _check(values, period)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2 / (period + 1)
    previous = sum(values[:period]) / period
    out[period - 1] = previous
    for i in range(period, len(values)):
        previous = alpha * values[i] + (1 - alpha) * previous
        out[i] = previous
    return out


def rsi(values: Sequence[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Relative strength index with Wilder's smoothing: 0 to 100, 100 when nothing fell, 50 when nothing moved."""

    _check(values, period)
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100 - 100 / (1 + gain / loss)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = value(avg_gain, avg_loss)
    return out


def macd(values: Sequence[float], fast: int = MACD_FAST, slow: int = MACD_SLOW,
         signal: int = MACD_SIGNAL) -> dict[str, list[float | None]]:
    """MACD line (fast EMA minus slow EMA), its signal line (EMA of the MACD line) and the histogram."""

    if fast >= slow:
        raise ValueError("fast period must be shorter than slow period")
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(fast_line, slow_line, strict=True)]
    first = next((i for i, v in enumerate(line) if v is not None), None)
    signal_line: list[float | None] = [None] * len(values)
    if first is not None:
        tail = ema([v for v in line[first:] if v is not None], signal)
        signal_line[first:] = tail
    hist = [None if m is None or s is None else m - s for m, s in zip(line, signal_line, strict=True)]
    return {"macd": line, "signal": signal_line, "histogram": hist}


def bundle(closes: Sequence[float]) -> dict[str, list[float | None]]:
    """The indicators of the dashboard chart, aligned with ``closes``."""

    return {f"sma{SMA_FAST}": sma(closes, SMA_FAST), f"sma{SMA_SLOW}": sma(closes, SMA_SLOW),
            f"rsi{RSI_PERIOD}": rsi(closes), **macd(closes)}
