"""Detect a final holdout that reuses the price path of an already sealed holdout.

An exact fingerprint (``content_sha256``) is defeated by changing one price by
1e-9, shifting the window by one bar or inverting the quote (Lot 4 review,
A-1/A-2). The store therefore keeps each sealed holdout's log returns and
refuses a new holdout that shares a run of aligned returns with any of them:
windows of up to ``WINDOW`` consecutive returns, at every alignment offset, are
compared with a robust correlation (returns clipped at ``CLIP`` scale units so
one aligned outlier cannot create a match). An absolute correlation whose
t-statistic reaches ``MIN_T_STATISTIC`` counts as reuse; the sign is ignored so
an inverted quote matches too.

Limits: a shared run shorter than about 1.5 windows, or a copy perturbed with noise
comparable to the returns themselves, is not detected; independent data could
in principle match by chance (at most about 2e-6 per window pair under a normal null).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from itertools import pairwise

WINDOW = 20
MIN_WINDOW = 8
MIN_T_STATISTIC = 12.0
CLIP = 3.0
RETURN_DECIMALS = 12


def holdout_returns(closes: Sequence[float]) -> list[float]:
    """Close-to-close log returns rounded to ``RETURN_DECIMALS``, as fingerprinted in sealed plans."""

    return [round(math.log(later / earlier), RETURN_DECIMALS) for earlier, later in pairwise(closes)]


def content_sha256(returns: Sequence[float]) -> str:
    text = json.dumps(list(returns), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _standardized(values: Sequence[float]) -> list[float] | None:
    """Scale by the median absolute deviation and clip; ``None`` when the path carries no information."""

    if not values:
        return None
    center = sorted(values)[len(values) // 2]
    deviations = sorted(abs(value - center) for value in values)
    scale = deviations[len(deviations) // 2] or (sum(deviations) / len(deviations))
    if scale <= 0.0:
        return None
    return [max(-CLIP, min(CLIP, (value - center) / scale)) for value in values]


def _correlation_threshold(window: int) -> float:
    degrees = window - 2
    return MIN_T_STATISTIC / math.sqrt(MIN_T_STATISTIC**2 + degrees)


def _window_stats(values: Sequence[float], first: int, window: int) -> tuple[list[float], float]:
    chunk = values[first:first + window]
    mean = sum(chunk) / window
    centered = [value - mean for value in chunk]
    return centered, sum(value * value for value in centered)


def shares_price_path(new: Sequence[float], old: Sequence[float]) -> bool:
    """True when a window of ``new`` is (anti-)correlated beyond chance with an aligned window of ``old``.

    Windows of ``new`` start every ``window // 2`` returns and are compared with
    every window of ``old``, so any shared run of at least 1.5 windows is found
    whatever the offset, at a cost of about ``2 * len(new) * len(old)``.
    """

    x, y = _standardized(new), _standardized(old)
    if x is None or y is None:
        return False
    window = min(WINDOW, max(MIN_WINDOW, min(len(x), len(y)) // 2))
    if min(len(x), len(y)) < window:
        return False  # too short to compare; the exact fingerprint and time overlap still apply
    threshold = _correlation_threshold(window)
    step = max(1, window // 2)
    starts = list(range(0, len(x) - window + 1, step))
    if starts[-1] != len(x) - window:
        starts.append(len(x) - window)
    old_windows = [_window_stats(y, first, window) for first in range(len(y) - window + 1)]
    for first in starts:
        xs, sxx = _window_stats(x, first, window)
        if sxx <= 1e-12:
            continue
        for ys, syy in old_windows:
            if syy <= 1e-12:
                continue
            sxy = sum(a * b for a, b in zip(xs, ys, strict=True))
            if abs(sxy) / math.sqrt(sxx * syy) >= threshold:
                return True
    return False
