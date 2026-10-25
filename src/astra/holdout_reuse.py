"""Detect a final holdout that reuses the price path of an already sealed holdout.

An exact fingerprint (``content_sha256``) is defeated by changing one price by
1e-9, shifting the window by one bar or inverting the quote (Lot 4 review,
A-1/A-2). The store therefore keeps each sealed holdout's log returns and
refuses a new holdout that shares a run of aligned returns with any of them:
windows of up to ``WINDOW`` consecutive returns, at every alignment offset, are
compared with a robust correlation (returns clipped at ``CLIP`` scale units so
one aligned outlier cannot create a match). An absolute correlation whose
t-statistic reaches ``MIN_T_STATISTIC`` counts as reuse; the sign is ignored so
an inverted quote matches too, the old path is also tried reversed in time, and
both sides are also tried resampled over two bars.

This is a heuristic, not a proof of independence. Not detected (Lot 4 review,
documented): a shared run shorter than about 1.25 windows; a copy with added
noise above roughly 0.4 times the return scale (B-2); resampling by factors
other than two; windows that are mostly flat (fewer than
``MIN_INFORMATIVE_SHARE`` real moves), which are skipped to avoid false matches
on sparse data. Independent data could in principle match by chance (at most
about 2e-6 per window pair under a normal null). The cost is roughly
``12 * len(new) * len(old)`` operations per sealed plan, computed before the
store's write lock is taken. The lasting control is data provenance (holdouts
from an attested broker history), not similarity search.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from itertools import pairwise

WINDOW = 20
MIN_WINDOW = 8
MIN_HOLDOUT_RETURNS = 16
MIN_T_STATISTIC = 12.0
CLIP = 3.0
RETURN_DECIMALS = 12
# A window is compared only when this share of its standardized returns is a real move, so
# sparse (mostly flat) independent data cannot match on one or two lone moves (Lot 4, B-6).
MIN_INFORMATIVE_SHARE = 0.6
INFORMATIVE = 0.25


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


def _window_stats(values: Sequence[float], first: int, window: int) -> tuple[list[float], float] | None:
    chunk = values[first:first + window]
    if sum(1 for value in chunk if abs(value) > INFORMATIVE) < MIN_INFORMATIVE_SHARE * window:
        return None
    mean = sum(chunk) / window
    centered = [value - mean for value in chunk]
    energy = sum(value * value for value in centered)
    return (centered, energy) if energy > 1e-12 else None


def _aggregated(values: Sequence[float], phase: int) -> list[float]:
    """Log returns over two bars (the same path at half the frequency), starting at ``phase``."""

    return [values[index] + values[index + 1] for index in range(phase, len(values) - 1, 2)]


def _aligned_match(new: Sequence[float], old: Sequence[float]) -> bool:
    x, y = _standardized(new), _standardized(old)
    if x is None or y is None:
        return False
    window = min(WINDOW, max(MIN_WINDOW, min(len(x), len(y)) // 2))
    if min(len(x), len(y)) < window:
        return False
    threshold = _correlation_threshold(window)
    step = max(1, window // 4)
    starts = list(range(0, len(x) - window + 1, step))
    if starts[-1] != len(x) - window:
        starts.append(len(x) - window)
    old_windows = [stats for first in range(len(y) - window + 1) if (stats := _window_stats(y, first, window))]
    for first in starts:
        stats = _window_stats(x, first, window)
        if stats is None:
            continue
        xs, sxx = stats
        for ys, syy in old_windows:
            sxy = sum(a * b for a, b in zip(xs, ys, strict=True))
            if abs(sxy) / math.sqrt(sxx * syy) >= threshold:
                return True
    return False


def shares_price_path(new: Sequence[float], old: Sequence[float]) -> bool:
    """True when ``new`` reuses the price path of ``old``, at the same or half/double frequency or reversed.

    Windows of ``new`` start every ``window // 4`` returns and are compared with
    every window of ``old``, so any shared run of at least 1.25 windows is found
    whatever the offset. The sign is ignored (inverted quotes), ``old`` is also
    tried reversed in time, and each side is also tried aggregated over two bars
    at both phases (a copy resampled from M15 to M30, or the reverse).
    """

    pairs = [(new, old), (new, list(reversed(old)))]
    for phase in (0, 1):
        pairs.append((_aggregated(new, phase), old))
        pairs.append((new, _aggregated(old, phase)))
    return any(_aligned_match(a, b) for a, b in pairs)
