"""Detect a final holdout that reuses the price path of an already sealed holdout.

An exact fingerprint (``content_sha256``) is defeated by changing one price by
1e-9, shifting the window by one bar or inverting the quote (Lot 4 review,
A-1/A-2). The store therefore keeps each sealed holdout's log returns and
compares a new holdout with every one of them (``assess_reuse``), under
``research_policy.HOLDOUT_REUSE_POLICY``. Each pair is tried as is, with the
old path reversed in time, with either side aggregated over two bars at both
phases (M15 resampled to M30, or the reverse), and with the sign ignored
(inverted quote). Two statistics decide:

- window correlation: robust-standardized (median, median absolute deviation
  floored at the match tolerance, clipped) returns over aligned windows at
  every offset, windows of the new holdout starting every ``window // 4``
  returns, then over the whole overlap of every offset (at least two windows).
  A window needs ``min_informative_share`` real moves to be compared. A
  correlation counts only once confirmed (``_exact_correlation``): on windows
  prewhitened by the mean lag-1 autocorrelation of the two paths, or because the
  fit residual is not serially correlated (a noisy copy of a persistent path).
  ``CONFLICT`` from ``conflict_abs_correlation``, ``SUSPECT`` from
  ``suspect_abs_correlation``.
- match share: returns are compared as they are, or minus each path's median
  when one path of the pair drifts (``drift_share`` of its returns at the
  median: a shared constant drift is not a match). A return is informative when it is further
  than ``match_tolerance`` times the new holdout's return scale from that
  centre. At every offset, the share of aligned informative returns equal
  within that tolerance is measured over dense windows
  (``min_informative_share`` informative points) and over the whole overlap
  (at least ``min_informative_points``), so mostly flat (sparse) holdouts are
  compared on their moves and their positions. A span reaching
  ``conflict_match_share`` is judged by the binomial probability of its
  matches at the chance match rate of the pair (measured over every alignment,
  which absorbs quantization grids and discrete moves), multiplied by the
  number of spans of its kind tested: ``CONFLICT`` at most
  ``conflict_match_chance_probability`` (a sparse span then counts runs of
  consecutive moves, not points); a whole-overlap span is ``SUSPECT`` at most
  ``suspect_match_chance_probability``. A sparse new holdout is also compared on
  the timing and direction of its moves only (a copy with rescaled or perturbed
  moves). When even a copy with two changed returns could not be told from
  chance, the pair fails closed (``UNDETERMINED``, ``match_not_discriminative``).
- an old holdout with fewer than ``min_informative_points`` informative
  returns is recorded as ``old_holdout_uninformative`` and does not taint
  unrelated plans; if it is entirely constant (a constant drift, the bundled
  fixture's ramp) it still matches a new holdout that repeats the constant,
  returns and price level, over ``conflict_match_share`` of its length.

``UNDETERMINED`` depends on the new holdout only: fewer than
``min_informative_points`` informative returns (flat or constant-drift data
cannot be compared with anything). ``CLEAR`` otherwise.

This is a heuristic, not a proof of independence (see the calibration in
tests/test_holdout_reuse_calibration.py and RESEARCH_PROTOCOL.md). Not
detected: a shared run shorter than about one window; a copy with enough added
noise (detection falls as the noise approaches the return scale, and an
arbitrarily noisy copy cannot be told apart from independent data); a noisy
copy of a sparse path beyond the direction of its moves; prices rescaled and
re-quoted to the same tick; a copy of an old holdout with one or two moves; a
nudged copy of a near-constant path whose old holdout has a few moves;
resampling factors other than two. Distinct but strongly correlated data (two
highly correlated currency pairs) is flagged like a copy, two independent
paths that repeat the same constant return are a conflict, and a few
independent sparse or near-constant pairs are ``SUSPECT``. The cost is
quadratic in the holdout lengths (computed before the store's write lock is
taken). The lasting control is data provenance (holdouts from an attested
broker history).
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from array import array
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import accumulate, pairwise, repeat
from operator import add, floordiv, mul, sub

from .research_policy import HOLDOUT_REUSE_POLICY, HoldoutReusePolicy

RETURN_DECIMALS = 12
MIN_HOLDOUT_RETURNS = HOLDOUT_REUSE_POLICY.min_holdout_returns
CONFLICT, SUSPECT, UNDETERMINED, CLEAR = "CONFLICT", "SUSPECT", "UNDETERMINED", "CLEAR"
_SEVERITY = {CLEAR: 0, UNDETERMINED: 1, SUSPECT: 2, CONFLICT: 3}
OLD_HOLDOUT_UNINFORMATIVE = "old_holdout_uninformative"
NEW_HOLDOUT_UNINFORMATIVE = "new_holdout_uninformative"
MATCH_NOT_DISCRIMINATIVE = "match_not_discriminative"


@dataclass(frozen=True, slots=True)
class ReuseAssessment:
    verdict: str
    max_abs_correlation: float | None
    transform: str | None
    comparable: bool
    notes: tuple[str, ...] = field(default=())
    max_match_share: float | None = None

    def payload(self) -> dict:
        return {
            "verdict": self.verdict,
            "max_abs_correlation": self.max_abs_correlation,
            "transform": self.transform,
            "comparable": self.comparable,
            "notes": list(self.notes),
            "max_match_share": self.max_match_share,
        }


def holdout_returns(closes: Sequence[float]) -> list[float]:
    """Close-to-close log returns rounded to ``RETURN_DECIMALS``, as fingerprinted in sealed plans."""

    return [round(math.log(later / earlier), RETURN_DECIMALS) for earlier, later in pairwise(closes)]


def content_sha256(returns: Sequence[float]) -> str:
    text = json.dumps(list(returns), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def worst(verdicts: Sequence[str]) -> str:
    return max(verdicts, key=_SEVERITY.__getitem__, default=CLEAR)


def _median(values: Sequence[float]) -> float:
    """Median (mean of the two middle values for an even count), so the median of -x is -median(x)."""

    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _tolerance(values: Sequence[float], policy: HoldoutReusePolicy) -> float:
    """``match_tolerance`` times the median size of the returns that are not negligible."""

    magnitudes = [abs(value) for value in values]
    floor = policy.scale_floor_fraction * max(magnitudes, default=0.0)
    kept = [magnitude for magnitude in magnitudes if magnitude > floor]
    return policy.match_tolerance * _median(kept) if kept else 0.0


@dataclass(slots=True)
class _Profile:
    """A path relative to its median (or to zero): residuals and which of them are informative."""

    values: list[float]
    center: float
    residuals: list[float]
    informative: list[bool]
    count: int

    def sorted_informative(self) -> tuple[list[float], list[int]]:
        """Informative residuals in increasing order, with their positions."""

        ordered = sorted((value, index) for index, value in enumerate(self.residuals) if self.informative[index])
        return [value for value, _ in ordered], [index for _, index in ordered]

    def signs(self) -> _Profile:
        """The same moves reduced to their direction: +1, -1, or 0 for a flat point."""

        residuals = [math.copysign(1.0, value) if live else 0.0 for value, live in zip(self.residuals, self.informative,
                                                                                         strict=True)]
        return _Profile(residuals, 0.0, residuals, list(self.informative), self.count)

    @staticmethod
    def drifting(values: Sequence[float], tolerance: float, policy: HoldoutReusePolicy) -> bool:
        """At least ``drift_share`` of the returns sit at the median: a near-constant (drift) path.

        A discrete walk (moves of 0, 1 or 2 ticks) can have half its returns at one value; it is not a
        drift, and centring it would hide most of its moves.
        """

        median = _median(values)
        return sum(abs(value - median) <= tolerance for value in values) >= policy.drift_share * len(values)

    @classmethod
    def of(cls, values: Sequence[float], tolerance: float, centred: bool) -> _Profile:
        """Residuals from the median when ``centred`` (a pair with a drifting path), else the returns.

        Centring only near-constant paths keeps partial copies comparable: the median of a path and
        of a copy spliced into other data differ, which would shift every residual. The decision is
        taken for the pair, so a copy and its original are always centred alike.
        """

        values = list(values)
        center = _median(values) if centred else 0.0
        residuals = [value - center for value in values]
        informative = [abs(value) > tolerance for value in residuals]
        return cls(values, center, residuals, informative, sum(informative))


# ---------------------------------------------------------------------------------------------------
# Window correlation
# ---------------------------------------------------------------------------------------------------


def _standardized(values: Sequence[float], policy: HoldoutReusePolicy) -> list[float] | None:
    """Scale by the median absolute deviation and clip; ``None`` when the path carries no information.

    The scale is at least the match tolerance: variations below it (the slow drift of a constant
    tick move as the price level changes, a nudge) are not amplified into a correlated pattern.
    """

    if not values:
        return None
    center = _median(values)
    scale = max(_median([abs(value - center) for value in values]), _tolerance(values, policy))
    if scale <= 0.0:
        return None
    return [max(-policy.clip, min(policy.clip, (value - center) / scale)) for value in values]


def _lag1(values: list[float]) -> float:
    """Lag-1 autocorrelation of the path, clipped to [-0.95, 0.95]."""

    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    energy = math.sumprod(centered, centered)
    phi = math.sumprod(centered[1:], centered[:-1]) / energy if energy > 0.0 else 0.0
    return max(-0.95, min(0.95, phi))


def _whitened(chunk: Sequence[float], phi: float) -> list[float]:
    """In-window residuals of a lag-1 autoregression (one value shorter than the window)."""

    return [later - phi * earlier for earlier, later in pairwise(chunk)]


def _unit(chunk: Sequence[float], window: int) -> list[float] | None:
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


def _window_length(n: int, m: int, policy: HoldoutReusePolicy) -> int:
    return min(policy.window, max(policy.min_window, min(n, m) * 3 // 4))


# Window dot products of one new window with every old window are computed at once as one
# big-integer product (Kronecker substitution): the old path is quantized to non-negative integers
# packed as 64-bit digits, the new unit window to integers summing to zero, and digit j of the
# product is the dot product with the old window starting at j (minus at most one borrowed unit).
# With _UNIT_SCALE = 2**16 and an old-path scale of at least 2**30, the error on a correlation is
# below 3e-4, under _QUANTIZATION_MARGIN; every pair the estimate puts within the margin of
# suspect_abs_correlation is recomputed exactly in floating point.
_UNIT_SCALE = 1 << 16
_MIN_PATH_SCALE = 1 << 30
_QUANTIZATION_MARGIN = 1e-3
_WHITE_RESIDUAL = 0.5  # |lag-1 autocorrelation| of a fit residual still treated as noise
_DIGIT = 8  # bytes per packed digit


class _CorrelationSide:
    """Standardized path, cached across transforms."""

    def __init__(self, values: Sequence[float], policy: HoldoutReusePolicy) -> None:
        self.policy = policy
        self.standard = x = _standardized(values, policy)
        self.phi = _lag1(x) if x is not None else 0.0
        if x is not None:
            self.moves = list(accumulate((abs(value) > policy.informative_threshold for value in x), initial=0))
            self.sums = list(accumulate(x, initial=0.0))
            self.squares = list(accumulate(map(mul, x, x), initial=0.0))
        self._packed: dict[int, tuple] = {}

    def comparable(self, first: int, window: int) -> bool:
        """The window has ``min_informative_share`` real moves and is not constant."""

        if self.moves[first + window] - self.moves[first] < self.policy.min_informative_share * window:
            return False
        total = self.sums[first + window] - self.sums[first]
        return window * (self.squares[first + window] - self.squares[first]) - total * total > window * 1e-9

    def unit(self, first: int, window: int) -> list[float] | None:
        return _unit(self.standard[first:first + window], window) if self.comparable(first, window) else None

    def packed(self, window: int) -> tuple:
        """(packed path, weight per window start); the weight is 0 for a window that is not compared.

        The weight converts an integer dot product into a correlation. For a window too long for
        64-bit digits the path is ``None`` and the starts and unit vectors of the comparable windows
        are returned instead (floating-point scan).
        """

        if window not in self._packed:
            self._packed[window] = self._pack(window)
        return self._packed[window]

    def _pack(self, window: int) -> tuple:
        x, clip = self.standard, self.policy.clip
        # |digit| <= window * _UNIT_SCALE * (clip * scale + offset) stays below 2**62.
        scale = (1 << 61) // (window * _UNIT_SCALE * (2 * math.ceil(clip) + 2))
        starts = range(len(x) - window + 1)
        if scale < _MIN_PATH_SCALE:
            units = [(first, unit) for first in starts if (unit := self.unit(first, window))]
            return None, ([first for first, _ in units], [unit for _, unit in units])
        offset = math.ceil(clip * scale) + 1
        path = int.from_bytes(array("Q", [round(value * scale) + offset for value in x]).tobytes(), sys.byteorder)
        weights = []
        for first in starts:
            if self.comparable(first, window):
                chunk = x[first:first + window]
                mean = sum(chunk) / window
                centered = [value - mean for value in chunk]
                weights.append(1.0 / (math.sqrt(math.sumprod(centered, centered)) * _UNIT_SCALE * scale))
            else:
                weights.append(0.0)
        return path, weights


def _packed_unit(unit: list[float]) -> int:
    """Reversed unit vector scaled to integers that sum exactly to zero, as one signed big integer."""

    values = [round(value * _UNIT_SCALE) for value in reversed(unit)]
    largest = max(range(len(values)), key=lambda k: abs(values[k]))
    values[largest] -= sum(values)  # at most window / 2 units on one entry
    positive = array("Q", [max(value, 0) for value in values]).tobytes()
    negative = array("Q", [max(-value, 0) for value in values]).tobytes()
    return int.from_bytes(positive, sys.byteorder) - int.from_bytes(negative, sys.byteorder)


def _lag1_of_residual(x: list[float], y: list[float]) -> float:
    """Lag-1 autocorrelation of the residual of the least-squares fit of ``y`` on ``x`` (both centred)."""

    mx, my = sum(x) / len(x), sum(y) / len(y)
    xc, yc = [value - mx for value in x], [value - my for value in y]
    energy = math.sumprod(xc, xc)
    beta = math.sumprod(xc, yc) / energy if energy > 0.0 else 0.0
    residual = [b - beta * a for a, b in zip(xc, yc, strict=True)]
    total = math.sumprod(residual, residual)
    return math.sumprod(residual[1:], residual[:-1]) / total if total > 0.0 else 0.0


def _exact_correlation(new: _CorrelationSide, old: _CorrelationSide, first: int, other: int, length: int,
                       policy: HoldoutReusePolicy, long_span: bool = False) -> float:
    """|correlation| of two aligned spans if confirmed, else 0; never above the raw correlation.

    Serially correlated independent paths correlate by chance over short spans. A raw correlation is
    confirmed when the spans are also correlated after prewhitening (both whitened with the mean
    lag-1 coefficient of the two paths, in-window values only), or when what the fit leaves is not
    serially correlated (|lag-1| at most _WHITE_RESIDUAL): a noisy copy of a persistent path leaves
    the added noise, two independent persistent paths leave a persistent difference. Prewhitening
    alone would miss the noisy copy (the noise swamps its innovations). The residual test is used
    over a ``long_span`` (the whole overlap of an offset, at least two windows) and, over a single
    window, only for a raw correlation at ``conflict_abs_correlation`` or above: in one window the
    lag-1 estimate is too noisy, and independent AR(1) 0.7 windows pass it near 0.95 by chance.
    """

    x, y = new.standard[first:first + length], old.standard[other:other + length]
    u, v = _unit(x, length), _unit(y, length)
    if u is None or v is None:
        return 0.0
    raw = abs(math.sumprod(u, v))
    if (long_span or raw >= policy.conflict_abs_correlation) and abs(_lag1_of_residual(x, y)) <= _WHITE_RESIDUAL:
        return raw
    phi = (new.phi + old.phi) / 2
    u, v = _unit(_whitened(x, phi), length - 1), _unit(_whitened(y, phi), length - 1)
    return 0.0 if u is None or v is None else min(raw, abs(math.sumprod(u, v)))


def _max_overlap_correlation(new: _CorrelationSide, old: _CorrelationSide, window: int,
                             policy: HoldoutReusePolicy, best: float) -> float:
    """Largest confirmed |correlation| over the whole overlap of each offset (at least two windows).

    Window correlations of a persistent path are noisy (little variance within one window); a
    noisy copy of it still correlates over its whole length.
    """

    x, y = new.standard, old.standard
    n, m = len(x), len(y)
    for offset in range(2 * window - n, m - 2 * window + 1):
        low, high = max(0, -offset), min(n, m - offset)
        length = high - low
        sx, sy = new.sums[high] - new.sums[low], old.sums[high + offset] - old.sums[low + offset]
        vx = new.squares[high] - new.squares[low] - sx * sx / length
        vy = old.squares[high + offset] - old.squares[low + offset] - sy * sy / length
        if vx <= 1e-12 or vy <= 1e-12:
            continue
        value = abs(math.sumprod(x[low:high], y[low + offset:high + offset]) - sx * sy / length) / math.sqrt(vx * vy)
        if value >= policy.suspect_abs_correlation and value > best:
            best = max(best, _exact_correlation(new, old, low, low + offset, length, policy, long_span=True))
            if best >= policy.conflict_abs_correlation:
                return best
    return best


def _max_window_correlation(new: _CorrelationSide, old: _CorrelationSide, policy: HoldoutReusePolicy):
    """Largest aligned-window |correlation|, and whether each side had a window that could be compared.

    Windows of the new path start every ``window // 4`` returns and are compared with every window
    of the old path (every offset), then the whole overlap of every offset is compared too. A pair
    at or above ``suspect_abs_correlation`` counts only once confirmed (``_exact_correlation``).
    """

    x, y = new.standard, old.standard
    if x is None or y is None:
        return None, x is not None, y is not None
    window = _window_length(len(x), len(y), policy)
    if min(len(x), len(y)) < window:
        return None, False, False
    step, last = max(1, window // 4), len(x) - window
    new_windows = [(first, unit) for first in range(last + 1)
                   if (first % step == 0 or first == last) and (unit := new.unit(first, window))]
    path, weights = old.packed(window)
    has_old = any(weights) if path is not None else bool(weights[0])
    if not new_windows or not has_old:
        return None, bool(new_windows), has_old
    suspect, conflict = policy.suspect_abs_correlation, policy.conflict_abs_correlation
    margin = _QUANTIZATION_MARGIN if path is not None else 0.0
    size = (len(y) + window + 1) * _DIGIT
    best = 0.0
    for first, unit in new_windows:
        if path is None:
            starts, units = weights
            scores = list(map(math.sumprod, repeat(unit), units))
            top = max(map(abs, scores))
        else:
            digits = memoryview((_packed_unit(unit) * path).to_bytes(size, sys.byteorder, signed=True)).cast("q")
            top = max(map(abs, map(mul, digits[window - 1:len(y)], weights)))
        if top <= best:
            continue
        if top < suspect - margin:
            best = top  # an estimate within the margin; every value near a threshold is computed exactly
            continue
        if path is not None:
            starts, scores = range(len(weights)), list(map(mul, digits[window - 1:len(y)], weights))
        for index, score in enumerate(scores):
            value = abs(score)
            if value <= best:
                continue
            if value >= suspect - margin:
                value = _exact_correlation(new, old, first, starts[index], window, policy)
            if value > best:
                best = value
                if best >= conflict:
                    return best, True, True
    return _max_overlap_correlation(new, old, window, policy, best), True, True


# ---------------------------------------------------------------------------------------------------
# Match share
# ---------------------------------------------------------------------------------------------------


def _binomial_tail(successes: int, trials: int, probability: float) -> float:
    """P(X >= successes) for X ~ Binomial(trials, probability)."""

    if successes <= 0:
        return 1.0
    if probability <= 0.0:
        return 0.0
    if probability >= 1.0 or successes > trials:
        return 1.0 if probability >= 1.0 else 0.0
    log_p, log_q = math.log(probability), math.log1p(-probability)
    log_n = math.lgamma(trials + 1)
    terms = [log_n - math.lgamma(x + 1) - math.lgamma(trials - x + 1) + x * log_p + (trials - x) * log_q
             for x in range(successes, trials + 1)]
    top = max(terms)
    return min(1.0, math.exp(top) * sum(math.exp(term - top) for term in terms))


@dataclass(slots=True)
class _Match:
    share: float | None = None  # share of the most significant span that reached conflict_match_share
    chance: float | None = None  # its chance probability, times the number of spans of its kind tested
    verdict: str = CLEAR
    weak: bool = False  # a copy with two changed returns would still look like chance (see _match_share)


def _match_share(a: _Profile, b: _Profile, sign: int, tolerance: float, policy: HoldoutReusePolicy) -> _Match:
    """Share of aligned informative residuals of ``a`` and ``sign * b`` equal within ``tolerance``.

    A match is two informative residuals equal within the tolerance; a flat point is never a match.
    Spans are the whole overlap of an offset (at least ``min_informative_points`` informative
    points) and every window of the overlap (at least ``min_informative_share`` of the window). A
    span reaching ``conflict_match_share`` is judged by the binomial probability of its matches at
    the chance match rate of the pair, multiplied by the number of spans of its kind tested
    (Bonferroni). Only a whole-overlap span can be SUSPECT: a dense window is a CONFLICT or nothing
    (a dense copy is far below the threshold, and noisy dense copies are the correlation's job).
    Each match is encoded as one integer (offset, position), so grouping by offset is a sort.
    """

    result = _Match()
    n, m = len(a.residuals), len(b.residuals)
    window = _window_length(n, m, policy)
    if min(n, m) < window or tolerance <= 0.0:
        return result
    keys, positions = b.sorted_informative()
    stride = n + 1
    scaled = [(position + n) * stride for position in positions]
    codes: list[int] = []
    for index, value in enumerate(a.residuals):
        if a.informative[index]:
            target = value if sign > 0 else -value
            low, high = bisect_left(keys, target - tolerance), bisect_right(keys, target + tolerance)
            if low < high:
                # (old - new + n) * stride + new, for every old position matching this new one
                codes.extend(map(add, scaled[low:high], repeat(-index * n)))
    live_pairs = n * m - (n - a.count) * (m - b.count)
    if live_pairs <= 0 or not codes:
        return result
    chance_rate = len(codes) / live_pairs  # match rate of a live aligned pair over every alignment
    overlaps = [min(n, m - offset) - max(0, -offset) for offset in range(window - n, m - window + 1)]
    tested = {True: len(overlaps), False: sum(overlap - window + 1 for overlap in overlaps)}  # whole, windows
    if a.count >= policy.min_informative_share * n or (abs(a.center) > tolerance and n < 2 * policy.window):
        # A nudged close changes two returns. If even a copy with two changed returns would not reach
        # suspect_match_chance_probability, the match share cannot tell a copy from chance here (short
        # discrete paths that mostly repeat one move; drifts shorter than two windows with a few
        # deviations): the pair fails closed instead of passing. Flat-centred sparse paths are exempt
        # (a nudge adds moves to them, and their independent pairs must stay comparable).
        nudged = _binomial_tail(max(0, a.count - 2), a.count, chance_rate) * tested[True]
        result.weak = nudged > policy.suspect_match_chance_probability
    codes.sort()
    per_offset = Counter(map(floordiv, codes, repeat(stride)))
    needed = policy.min_informative_share * window
    share_floor, minimum = policy.conflict_match_share, policy.min_informative_points
    dense = max(minimum, math.ceil(share_floor * needed))
    prefix_a = list(accumulate(a.informative, initial=0))
    prefix_b = list(accumulate(b.informative, initial=0))
    ia, ib = a.informative, b.informative
    for code, count in per_offset.items():
        if count < minimum:
            continue
        offset = code - n
        start, stop = max(0, -offset), min(n, m - offset)
        if stop - start < window:
            continue
        live_floor = max(prefix_a[stop] - prefix_a[start], prefix_b[stop + offset] - prefix_b[start + offset])
        whole = count >= share_floor * live_floor
        if not whole and count < dense:
            continue
        first_code = bisect_left(codes, code * stride)
        matched_at = list(map(sub, codes[first_code:first_code + count], repeat(code * stride)))
        if not whole and min(map(sub, matched_at[dense - 1:], matched_at[:count - dense + 1])) >= window:
            continue
        matched = set(matched_at)
        live_at = [ia[index] or ib[index + offset] for index in range(start, stop)]
        live_sum = list(accumulate(live_at, initial=0))
        match_sum = list(accumulate((index in matched for index in range(start, stop)), initial=0))
        # Moves of a sparse path come in runs (a nudged close, a quote flickering between two ticks);
        # the matches of one run are not independent, so for a CONFLICT a sparse span counts runs, not
        # points. SUSPECT uses the points, so a copy of a sparse path is never cleared.
        runs = sum(1 for earlier, later in pairwise([False, *live_at]) if later and not earlier)
        spans = [(0, stop - start, minimum, True)]  # the whole overlap: sparse paths are compared on their moves
        spans += [(low, low + window, needed, False) for low in range(0, stop - start - window + 1)]
        for low, high, required, is_whole in spans:
            live = live_sum[high] - live_sum[low]
            hits = match_sum[high] - match_sum[low]
            if live < required or hits < minimum or hits < share_floor * live:
                continue
            chance = min(1.0, _binomial_tail(hits, live, chance_rate) * tested[is_whole])
            if is_whole and runs and live < needed * (high - low) / window:
                length = live / runs
                runs_chance = _binomial_tail(math.floor(hits / length), math.ceil(live / length), chance_rate)
                conflict_chance = min(1.0, runs_chance * tested[is_whole])
            else:
                conflict_chance = chance
            if result.chance is None or chance < result.chance:
                result.share, result.chance = hits / live, chance
            if conflict_chance <= policy.conflict_match_chance_probability:
                result.share, result.chance, result.verdict = hits / live, conflict_chance, CONFLICT
                return result
            if is_whole and chance <= policy.suspect_match_chance_probability:
                result.verdict = SUSPECT
    return result


def _constant_path_share(a: _Profile, constant: float, tolerance: float) -> float:
    """Share of the new holdout that repeats ``constant``: returns and price level both within ``tolerance``.

    The price level is compared over the whole new holdout, so a nudged close (the level comes back)
    is still a repeat, while a path whose level drifts away by one tick is not.
    """

    level, hits = 0.0, 0
    for value in a.values:
        level += value - constant
        hits += abs(value - constant) <= tolerance and abs(level) <= tolerance
    return hits / len(a.values) if a.values else 0.0


# ---------------------------------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------------------------------


def _pairs(new: Sequence[float], old: Sequence[float]):
    yield "same", new, old
    yield "reversed", new, list(reversed(old))
    for phase in (0, 1):
        yield f"new_aggregated_x2_phase{phase}", _aggregated(new, phase), old
        yield f"old_aggregated_x2_phase{phase}", new, _aggregated(old, phase)


def assess_reuse(
    new: Sequence[float], old: Sequence[float], policy: HoldoutReusePolicy = HOLDOUT_REUSE_POLICY
) -> ReuseAssessment:
    """Verdict on whether the holdout returns ``new`` reuse the sealed holdout returns ``old``."""

    new, old = [float(value) for value in new], [float(value) for value in old]
    new_tolerance = _tolerance(new, policy)
    comparable = new_tolerance > 0.0 and _Profile.of(
        new, new_tolerance, _Profile.drifting(new, new_tolerance, policy)).count >= policy.min_informative_points
    notes: set[str] = set()
    best, best_transform, best_share, match_suspect, weak = None, None, None, None, False
    sides: dict[int, tuple[list[float], _CorrelationSide]] = {}  # keeps each list alive, so ids stay unique

    def side(values: list[float]) -> _CorrelationSide:
        if id(values) not in sides:
            sides[id(values)] = (values, _CorrelationSide(values, policy))
        return sides[id(values)][1]

    for transform, a, b in _pairs(new, old):
        tolerance = _tolerance(a, policy)
        if tolerance <= 0.0:
            continue
        centred = _Profile.drifting(a, tolerance, policy) or _Profile.drifting(b, tolerance, policy)
        profile_a = _Profile.of(a, tolerance, centred)
        profile_b = _Profile.of(b, tolerance, centred)
        window = _window_length(len(a), len(b), policy)
        for sign, factor in (("", 1), ("negated_", -1)):  # factor -1: the old path with its sign flipped
            if profile_b.count < policy.min_informative_points:
                # Too few moves to compare. An entirely constant (drift) path matches a new path that
                # repeats that constant, returns and price level (the bundled fixture's constant ramp).
                notes.add(OLD_HOLDOUT_UNINFORMATIVE)
                if profile_b.count == 0 and abs(profile_b.center) > tolerance and min(len(a), len(b)) >= window:
                    share = _constant_path_share(profile_a, factor * profile_b.center, tolerance)
                    if share >= policy.conflict_match_share:
                        return ReuseAssessment(CONFLICT, None, f"{sign}{transform}:constant_path_share={round(share, 4)}",
                                               True, tuple(sorted(notes)), round(share, 6))
                continue
            if profile_a.count < policy.min_informative_points:
                continue
            matches = [("", _match_share(profile_a, profile_b, factor, tolerance, policy))]
            if profile_a.count < policy.min_informative_share * len(a):
                # A sparse path is also compared on the timing and direction of its moves only, so a
                # copy with every move rescaled or perturbed is still matched (correlation cannot be).
                matches.append(("signs:", _match_share(profile_a.signs(), profile_b.signs(), factor, 0.5, policy)))
            weak = weak or (matches[0][1].weak and transform in ("same", "reversed"))  # at the holdout's own length
            for kind, match in matches:
                if match.share is not None and (best_share is None or match.share > best_share):
                    best_share = match.share
                if match.verdict != CLEAR:
                    label = f"{kind}{sign}{transform}:match_share={round(match.share, 4)},chance={match.chance:.3g}"
                    if match.verdict == CONFLICT:
                        return ReuseAssessment(CONFLICT, None, label, True, tuple(sorted(notes)), round(match.share, 6))
                    match_suspect = match_suspect or label
        value, new_windows, old_windows = _max_window_correlation(side(a), side(b), policy)
        if new_windows and not old_windows:
            notes.add(OLD_HOLDOUT_UNINFORMATIVE)
        if value is not None and (best is None or value > best):
            best, best_transform = value, transform
        if best is not None and best >= policy.conflict_abs_correlation:
            break
    if not comparable:
        notes.add(NEW_HOLDOUT_UNINFORMATIVE)
    if best is not None and best >= policy.conflict_abs_correlation:
        verdict = CONFLICT
    elif best is not None and best >= policy.suspect_abs_correlation:
        verdict = SUSPECT
    elif match_suspect:
        verdict, best_transform = SUSPECT, match_suspect
    elif not comparable:
        verdict = UNDETERMINED
    elif weak:
        verdict = UNDETERMINED
        notes.add(MATCH_NOT_DISCRIMINATIVE)
    else:
        verdict = CLEAR
    return ReuseAssessment(verdict, None if best is None else round(best, 6), best_transform, comparable,
                           tuple(sorted(notes)), None if best_share is None else round(best_share, 6))


def shares_price_path(new: Sequence[float], old: Sequence[float]) -> bool:
    """True when the detector flags ``new`` as reusing ``old`` (``CONFLICT`` or ``SUSPECT``)."""

    return assess_reuse(new, old).verdict in (CONFLICT, SUSPECT)
