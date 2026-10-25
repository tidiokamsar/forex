"""Detect a final holdout that reuses the price path of an already sealed holdout.

An exact fingerprint (``content_sha256``) is defeated by changing one price by
1e-9, shifting the window by one bar or inverting the quote (Lot 4 review,
A-1/A-2). The store therefore keeps each sealed holdout's log returns and
compares a new holdout with every one of them (``assess_reuse``):

- returns, not raw prices, are compared, after robust standardization (median
  and median absolute deviation) and clipping, so one aligned outlier cannot
  create a match and the price scale does not matter;
- windows of the new holdout start every ``window // 4`` returns and are
  compared with every window of the old one, at every alignment offset;
- the sign is ignored (inverted quote), the old path is also tried reversed in
  time, and each side is also tried aggregated over two bars at both phases
  (a copy resampled from M15 to M30, or the reverse);
- the largest absolute window correlation decides the verdict under
  ``research_policy.HOLDOUT_REUSE_POLICY``: ``CONFLICT`` (at least
  ``conflict_abs_correlation``, the plan is refused), ``SUSPECT`` (at least
  ``suspect_abs_correlation``, promotion is refused until a new independent
  holdout is used), ``UNDETERMINED`` (no window of one side carries enough
  real moves to be compared, promotion is refused) or ``CLEAR``.

This is a heuristic, not a proof of independence (see the calibration in
tests/test_holdout_reuse_calibration.py and RESEARCH_PROTOCOL.md). Not detected:
a shared run shorter than about 1.25 windows; a copy with enough added noise
(detection falls as the noise approaches the return scale, and an arbitrarily
noisy copy cannot be told apart from independent data); resampling factors
other than two; mostly flat windows, which are skipped. Distinct but strongly
correlated data (for example two highly correlated currency pairs) is flagged
like a copy. The cost is roughly ``12 * len(new) * len(old)`` operations per
sealed plan, computed before the store's write lock is taken. The lasting
control is data provenance (holdouts from an attested broker history).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from .research_policy import HOLDOUT_REUSE_POLICY, HoldoutReusePolicy

RETURN_DECIMALS = 12
MIN_HOLDOUT_RETURNS = HOLDOUT_REUSE_POLICY.min_holdout_returns
CONFLICT, SUSPECT, UNDETERMINED, CLEAR = "CONFLICT", "SUSPECT", "UNDETERMINED", "CLEAR"
_SEVERITY = {CLEAR: 0, UNDETERMINED: 1, SUSPECT: 2, CONFLICT: 3}


@dataclass(frozen=True, slots=True)
class ReuseAssessment:
    verdict: str
    max_abs_correlation: float | None
    transform: str | None
    comparable: bool

    def payload(self) -> dict:
        return {
            "verdict": self.verdict,
            "max_abs_correlation": self.max_abs_correlation,
            "transform": self.transform,
            "comparable": self.comparable,
        }


def holdout_returns(closes: Sequence[float]) -> list[float]:
    """Close-to-close log returns rounded to ``RETURN_DECIMALS``, as fingerprinted in sealed plans."""

    return [round(math.log(later / earlier), RETURN_DECIMALS) for earlier, later in pairwise(closes)]


def content_sha256(returns: Sequence[float]) -> str:
    text = json.dumps(list(returns), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def worst(verdicts: Sequence[str]) -> str:
    return max(verdicts, key=_SEVERITY.__getitem__, default=CLEAR)


def _standardized(values: Sequence[float], policy: HoldoutReusePolicy) -> list[float] | None:
    """Scale by the median absolute deviation and clip; ``None`` when the path carries no information."""

    if not values:
        return None
    center = sorted(values)[len(values) // 2]
    deviations = sorted(abs(value - center) for value in values)
    scale = deviations[len(deviations) // 2] or (sum(deviations) / len(deviations))
    if scale <= 0.0:
        return None
    return [max(-policy.clip, min(policy.clip, (value - center) / scale)) for value in values]


def _window_stats(values, first: int, window: int, policy: HoldoutReusePolicy):
    chunk = values[first:first + window]
    real_moves = sum(1 for value in chunk if abs(value) > policy.informative_threshold)
    if real_moves < policy.min_informative_share * window:
        return None
    mean = sum(chunk) / window
    centered = [value - mean for value in chunk]
    energy = math.sumprod(centered, centered)
    if energy <= 1e-12:
        return None
    norm = math.sqrt(energy)
    return [value / norm for value in centered]  # unit vector: a dot product is the correlation


def _aggregated(values: Sequence[float], phase: int) -> list[float]:
    """Log returns over two bars (the same path at half the frequency), starting at ``phase``."""

    return [values[index] + values[index + 1] for index in range(phase, len(values) - 1, 2)]


def _max_window_correlation(new, old, policy: HoldoutReusePolicy) -> tuple[float | None, bool]:
    """Largest absolute aligned-window correlation, and whether any pair of windows could be compared."""

    x, y = _standardized(new, policy), _standardized(old, policy)
    if x is None or y is None:
        return None, False
    shorter = min(len(x), len(y))
    window = min(policy.window, max(policy.min_window, shorter * 3 // 4))
    if shorter < window:
        return None, False
    step = max(1, window // 4)
    starts = list(range(0, len(x) - window + 1, step))
    if starts[-1] != len(x) - window:
        starts.append(len(x) - window)
    old_windows = [stats for first in range(len(y) - window + 1) if (stats := _window_stats(y, first, window, policy))]
    best: float | None = None
    for first in starts:
        stats = _window_stats(x, first, window, policy)
        if stats is None or not old_windows:
            continue
        for ys in old_windows:
            value = abs(math.sumprod(stats, ys))
            if best is None or value > best:
                best = value
                if best >= policy.conflict_abs_correlation:
                    return best, True
    return best, best is not None


def _max_match_share(new, old, policy: HoldoutReusePolicy) -> float | None:
    """Largest share of aligned non-flat returns equal within tolerance, over any window and offset.

    Independent of correlation: a near-constant path (a smooth trend) keeps
    little signal once centred, but a copy still matches it return by return.
    Points where both returns are flat are ignored, so sparse data cannot match
    on zeros; a window needs ``min_informative_share`` non-flat points. Returns
    are bucketed by the tolerance, so only offsets with enough candidate
    matches are scanned.
    """

    magnitudes = sorted(abs(value) for value in (*new, *old) if value != 0.0)
    if not magnitudes:
        return None
    tolerance = policy.match_tolerance * magnitudes[len(magnitudes) // 2]
    shorter = min(len(new), len(old))
    window = min(policy.window, max(policy.min_window, shorter * 3 // 4))
    if shorter < window or tolerance <= 0.0:
        return None
    needed = policy.min_informative_share * window
    buckets: dict[int, list[int]] = {}
    for index, value in enumerate(old):
        if abs(value) > tolerance:
            buckets.setdefault(math.floor(value / tolerance), []).append(index)
    per_offset: dict[int, int] = {}
    for index, value in enumerate(new):
        if abs(value) <= tolerance:
            continue
        key = math.floor(value / tolerance)
        for neighbour in (key - 1, key, key + 1):
            for other in buckets.get(neighbour, ()):
                if abs(value - old[other]) <= tolerance:
                    per_offset[other - index] = per_offset.get(other - index, 0) + 1
    best: float | None = None
    minimum = policy.conflict_match_share * needed
    for offset, count in per_offset.items():
        if count < minimum:
            continue
        start, stop = max(0, -offset), min(len(new), len(old) - offset)
        if stop - start < window:
            continue
        informative, matched = [0], [0]
        for index in range(start, stop):
            a, b = new[index], old[index + offset]
            live = abs(a) > tolerance or abs(b) > tolerance
            informative.append(informative[-1] + live)
            matched.append(matched[-1] + (live and abs(a - b) <= tolerance))
        for first in range(0, stop - start - window + 1):
            live = informative[first + window] - informative[first]
            if live >= needed:
                share = (matched[first + window] - matched[first]) / live
                if best is None or share > best:
                    best = share
                    if best >= policy.conflict_match_share:
                        return best
    return best


def assess_reuse(
    new: Sequence[float], old: Sequence[float], policy: HoldoutReusePolicy = HOLDOUT_REUSE_POLICY
) -> ReuseAssessment:
    """Verdict on whether the holdout returns ``new`` reuse the sealed holdout returns ``old``."""

    pairs = [("same", new, old), ("reversed", new, list(reversed(old)))]
    for phase in (0, 1):
        pairs.append((f"new_aggregated_x2_phase{phase}", _aggregated(new, phase), old))
        pairs.append((f"old_aggregated_x2_phase{phase}", new, _aggregated(old, phase)))
    best, best_transform, comparable = None, None, False
    for transform, a, b in pairs:
        for sign, other in (("", b), ("negated_", [-value for value in b])):
            share = _max_match_share(a, other, policy)
            if share is not None and share >= policy.conflict_match_share:
                return ReuseAssessment(CONFLICT, None, f"{sign}{transform}:match_share={round(share, 4)}", True)
        value, compared = _max_window_correlation(a, b, policy)
        comparable = comparable or compared
        if value is not None and (best is None or value > best):
            best, best_transform = value, transform
        if best is not None and best >= policy.conflict_abs_correlation:
            break
    if best is not None and best >= policy.conflict_abs_correlation:
        verdict = CONFLICT
    elif best is not None and best >= policy.suspect_abs_correlation:
        verdict = SUSPECT
    elif not comparable:
        verdict = UNDETERMINED
    else:
        verdict = CLEAR
    return ReuseAssessment(verdict, None if best is None else round(best, 6), best_transform, comparable)


def shares_price_path(new: Sequence[float], old: Sequence[float]) -> bool:
    """True when the detector flags ``new`` as reusing ``old`` (``CONFLICT`` or ``SUSPECT``)."""

    return assess_reuse(new, old).verdict in (CONFLICT, SUSPECT)
