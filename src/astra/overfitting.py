"""Backtest-overfitting diagnostics in pure Python (cahier des charges, section 7.2).

Two deterministic tools quantify how much a selected variant's backtest can be
trusted given how many variants were tried:

* ``probability_of_backtest_overfitting``: Combinatorially Symmetric
  Cross-Validation (CSCV) estimate of the Probability of Backtest Overfitting
  (Bailey, Borwein, Lopez de Prado, Zhu, 2014).
* ``deflated_sharpe_ratio``: Deflated Sharpe Ratio (Bailey and Lopez de Prado,
  2014), the probability that the true Sharpe ratio of the selected strategy is
  positive after accounting for selection among ``n_trials`` variants,
  non-normal returns and sample length.

Conventions (fixed, documented, tested):

* Rows are split into contiguous blocks ``[s*T//S, (s+1)*T//S)``; all rows are
  used and block sizes differ by at most one row.
* The default metric is ``mean / sample stdev`` of the concatenated rows. A
  window with zero sample standard deviation scores ``0.0`` (no risk-adjusted
  edge is claimed), it does not raise.
* In-sample ties are broken by the lowest variant index.
* Out-of-sample ranks are ascending (1 = worst, N = best); tied OOS metrics
  receive their average rank. ``w = rank / (N + 1)`` and
  ``lambda = ln(w / (1 - w))``. A combination counts as overfit when
  ``lambda <= 0``, so a selected variant tied with the OOS median counts as
  overfit (conservative).
* Sharpe ratios are per period and not annualized. Skewness and kurtosis use
  biased (population) central moments; kurtosis is non-excess (normal = 3).

These statistics are evidence about the selection process, not proof of
future profitability. No randomness is used.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Any

OVERFITTING_VERSION = "astra-overfitting-1.0.0"
PBO_METHOD = "CSCV (Bailey, Borwein, Lopez de Prado, Zhu 2014)"
MAX_COMBINATIONS = 20_000
EULER_MASCHERONI = 0.5772156649015329

KNOWN_LIMITATIONS = (
    "CSCV assumes the performance rows are comparable across blocks; strong serial dependence or regime change "
    "inside the sample weakens the estimate.",
    "PBO measures the selection process on the supplied variants only; untested or unrecorded variants are not counted.",
    "The deflated Sharpe ratio relies on asymptotic normal approximations and on an honest n_trials count.",
    "Sharpe ratios are per period and not annualized; returns are assumed already net of costs.",
)

_STANDARD_NORMAL = statistics.NormalDist()


@dataclass(frozen=True, slots=True)
class PBOResult:
    pbo: float
    n_variants: int
    n_slices: int
    n_combinations: int
    logits: tuple[float, ...]
    median_logit: float
    method: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["logits"] = list(self.logits)
        payload["version"] = OVERFITTING_VERSION
        json.dumps(payload, allow_nan=False)
        return payload


@dataclass(frozen=True, slots=True)
class DeflatedSharpeResult:
    observed_sharpe: float
    expected_max_sharpe: float
    probability: float
    n_trials: int
    n_observations: int
    skewness: float
    kurtosis: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["version"] = OVERFITTING_VERSION
        json.dumps(payload, allow_nan=False)
        return payload


# Relative tolerance: a standard deviation this small against the values' scale
# is float noise, not variability (it would otherwise produce enormous ratios).
RELATIVE_ZERO_VARIANCE = 1e-9


def negligible_deviation(deviation: float, values: Sequence[float]) -> bool:
    scale = max((abs(value) for value in values), default=0.0)
    return deviation <= RELATIVE_ZERO_VARIANCE * max(scale, 1e-300)


def sharpe_like(values: Sequence[float]) -> float:
    """Per-period ``mean / sample stdev``; ``0.0`` when the sample stdev is zero."""

    if len(values) < 2:
        raise ValueError("a Sharpe-like metric needs at least 2 values")
    if min(values) == max(values):
        return 0.0
    mean = statistics.fmean(values)
    # Float two-pass variance (math.fsum) instead of statistics.stdev's exact rational arithmetic,
    # which is too slow for the thousands of windows CSCV evaluates.
    variance = math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if variance <= 0 or negligible_deviation(math.sqrt(variance), values):
        return 0.0
    return mean / math.sqrt(variance)


def probability_of_backtest_overfitting(
    performance: Sequence[Sequence[float]],
    n_slices: int = 8,
    metric: Callable[[Sequence[float]], float] | None = None,
) -> PBOResult:
    """Estimate PBO with CSCV.

    ``performance[t][v]`` is the per-period net return (or P&L) of variant ``v``
    in period ``t``, in chronological order. The ``T`` rows are split into
    ``n_slices`` contiguous blocks (``n_slices`` even, at least 2, each block at
    least 2 rows). Every combination of ``n_slices / 2`` blocks is used once as
    in-sample, its complement as out-of-sample. The variant with the best
    in-sample metric (ties: lowest index) is ranked out-of-sample among all
    variants (ascending, ties: average rank), ``w = rank / (N + 1)`` and
    ``lambda = ln(w / (1 - w))``. PBO is the fraction of combinations with
    ``lambda <= 0``.
    """

    matrix = _validated_matrix(performance)
    rows = len(matrix)
    variants = len(matrix[0])
    _require_int(n_slices, "n_slices", 2)
    if n_slices % 2:
        raise ValueError("n_slices must be even")
    if rows < 2 * n_slices:
        raise ValueError(f"n_slices={n_slices} needs at least {2 * n_slices} rows (2 per slice); got {rows}")
    half = n_slices // 2
    total = math.comb(n_slices, half)
    if total > MAX_COMBINATIONS:
        raise ValueError(f"C({n_slices}, {half}) = {total} combinations exceeds the cap of {MAX_COMBINATIONS}")
    if metric is not None and not callable(metric):
        raise TypeError("metric must be callable or None")
    score = sharpe_like if metric is None else metric

    bounds = [(index * rows // n_slices, (index + 1) * rows // n_slices) for index in range(n_slices)]
    columns = [[row[variant] for row in matrix] for variant in range(variants)]

    def window_scores(blocks: Sequence[int]) -> list[float]:
        scores = []
        for column in columns:
            window = [value for block in blocks for value in column[bounds[block][0] : bounds[block][1]]]
            value = score(window)
            if not _finite(value):
                raise ValueError("metric must return a finite real number")
            scores.append(float(value))
        return scores

    logits: list[float] = []
    for in_blocks in combinations(range(n_slices), half):
        out_blocks = tuple(block for block in range(n_slices) if block not in in_blocks)
        in_scores = window_scores(in_blocks)
        best = max(range(variants), key=lambda variant: (in_scores[variant], -variant))
        out_scores = window_scores(out_blocks)
        chosen = out_scores[best]
        below = sum(1 for value in out_scores if value < chosen)
        tied = sum(1 for value in out_scores if value == chosen)
        rank = below + (tied + 1) / 2.0
        relative = rank / (variants + 1)
        logits.append(math.log(relative / (1.0 - relative)))

    overfit = sum(1 for value in logits if value <= 0)
    return PBOResult(
        pbo=overfit / total,
        n_variants=variants,
        n_slices=n_slices,
        n_combinations=total,
        logits=tuple(logits),
        median_logit=statistics.median(logits),
        method=PBO_METHOD,
    )


def expected_maximum_sharpe(n_trials: int, trial_sharpe_variance: float) -> float:
    """Approximate expected maximum of ``n_trials`` null Sharpe estimates (0 when ``n_trials == 1``)."""

    _require_int(n_trials, "n_trials", 1)
    if not _finite(trial_sharpe_variance) or trial_sharpe_variance <= 0:
        raise ValueError("trial_sharpe_variance must be a finite number greater than 0")
    if n_trials == 1:
        return 0.0
    inverse = _STANDARD_NORMAL.inv_cdf
    return math.sqrt(trial_sharpe_variance) * (
        (1.0 - EULER_MASCHERONI) * inverse(1.0 - 1.0 / n_trials)
        + EULER_MASCHERONI * inverse(1.0 - 1.0 / (n_trials * math.e))
    )


def deflated_sharpe_ratio(
    returns: Sequence[float],
    *,
    n_trials: int,
    trial_sharpe_variance: float | None = None,
) -> DeflatedSharpeResult:
    """Deflated Sharpe ratio of the selected strategy (Bailey and Lopez de Prado, 2014).

    ``SR = mean / sample stdev`` (per period). ``SR0`` is the expected maximum
    Sharpe ratio of ``n_trials`` null trials with variance
    ``trial_sharpe_variance`` (default ``1 / (T - 1)``, the approximate variance
    of a Sharpe estimate under a zero-mean normal null). The result is
    ``Phi((SR - SR0) * sqrt(T - 1) / sqrt(1 - skew * SR + (kurt - 1) / 4 * SR**2))``.
    With ``n_trials == 1`` it reduces to the probabilistic Sharpe ratio against 0.
    """

    values = _validated_series(returns)
    _require_int(n_trials, "n_trials", 1)
    count = len(values)
    if count < 3:
        raise ValueError("deflated_sharpe_ratio needs at least 3 returns")
    deviation = statistics.stdev(values)
    if deviation == 0 or negligible_deviation(deviation, values):
        raise ValueError("returns have zero or negligible variance; the Sharpe ratio is undefined")
    mean = statistics.fmean(values)
    sharpe = mean / deviation
    m2 = sum((value - mean) ** 2 for value in values) / count
    m3 = sum((value - mean) ** 3 for value in values) / count
    m4 = sum((value - mean) ** 4 for value in values) / count
    skewness = m3 / m2**1.5
    kurtosis = m4 / m2**2
    variance = 1.0 / (count - 1) if trial_sharpe_variance is None else trial_sharpe_variance
    benchmark = expected_maximum_sharpe(n_trials, variance)
    denominator = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    if not denominator > 0:
        raise ValueError("the Sharpe ratio standard-error term is not positive for these returns")
    statistic = (sharpe - benchmark) * math.sqrt(count - 1) / math.sqrt(denominator)
    return DeflatedSharpeResult(
        observed_sharpe=sharpe,
        expected_max_sharpe=benchmark,
        probability=_STANDARD_NORMAL.cdf(statistic),
        n_trials=n_trials,
        n_observations=count,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def _validated_matrix(performance: Sequence[Sequence[float]]) -> list[list[float]]:
    if isinstance(performance, (str, bytes)) or not isinstance(performance, Sequence):
        raise TypeError("performance must be a sequence of rows")
    matrix: list[list[float]] = []
    width: int | None = None
    for index, row in enumerate(performance):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise TypeError(f"performance row {index} must be a sequence of numbers")
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise ValueError(f"performance row {index} has {len(row)} variants; expected {width} (ragged rows)")
        if not all(_finite(value) for value in row):
            raise ValueError(f"performance row {index} contains a non-finite or non-numeric value")
        matrix.append([float(value) for value in row])
    if width is None or width < 2:
        raise ValueError("performance needs at least 2 variants")
    return matrix


def _validated_series(values: Sequence[float]) -> list[float]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("returns must be a sequence of numbers")
    if not all(_finite(value) for value in values):
        raise ValueError("returns must contain finite real numbers only")
    return [float(value) for value in values]


def _require_int(value: object, name: str, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
