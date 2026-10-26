"""Backtest-overfitting diagnostics in pure Python (cahier des charges, section 7.2).

Two deterministic tools quantify how much a selected variant's backtest can be
trusted given how many variants were tried:

* ``probability_of_backtest_overfitting``: Combinatorially Symmetric
  Cross-Validation (CSCV) estimate of the Probability of Backtest Overfitting
  (Bailey, Borwein, Lopez de Prado, Zhu; SSRN 2326253, J. Computational
  Finance 2017), Algorithm 2.3 and section 3.1.
* ``deflated_sharpe_ratio``: Deflated Sharpe Ratio (Bailey and Lopez de Prado,
  SSRN 2460551, J. Portfolio Management 2014), eq. 1 and 2, the probability
  that the true Sharpe ratio of the selected strategy is positive after
  accounting for selection among ``n_trials`` variants, non-normal returns and
  sample length. ``resolve_trials`` chooses the trial count and the variance
  of trial Sharpes from the plan and, optionally, an external ``TrialUniverse``.

Conventions (fixed, documented, tested):

* Rows are split into contiguous blocks ``[s*T//S, (s+1)*T//S)``; all rows are
  used and block sizes differ by at most one row (the paper asks for equal
  blocks; within one half every variant is scored on the same rows, so the
  ranks are unaffected).
* IS and OOS sub-samples join their blocks in chronological order (paper,
  Algorithm 2.3, step 4a-b), so path-dependent metrics such as a drawdown are
  well defined. The same metric is used in-sample and out-of-sample.
* The default metric is ``mean / sample stdev`` of the concatenated rows. A
  window with zero sample standard deviation scores ``0.0`` (no risk-adjusted
  edge is claimed), it does not raise. ``selection_score_metric`` mirrors the
  ASTRA selection rule (net P&L minus maximum drawdown in cash).
* In-sample ties are resolved conservatively: among the variants tied for the
  best in-sample metric, the one with the worst out-of-sample metric is taken
  as selected (then the lowest index).
* Optional eligibility: with ``eligibility`` counts, only variants whose
  in-sample count reaches ``minimum_eligibility`` can be selected; a
  combination without an eligible variant is an abstention and is excluded
  from the PBO denominator (reported as ``n_abstained``).
* Out-of-sample ranks are ascending (1 = worst, N = best); tied OOS metrics
  receive their average rank. ``w = rank / (N + 1)`` and
  ``lambda = ln(w / (1 - w))``. A combination counts as overfit when
  ``lambda <= 0``, so a selected variant tied with the OOS median counts as
  overfit (conservative; the paper's ``phi`` integrates the logit density up
  to 0).
* Sharpe ratios are per period and not annualized. Skewness and kurtosis use
  biased (population) central moments; kurtosis is non-excess (normal = 3),
  as in the PSR term ``1 - skew * SR + (kurt - 1) / 4 * SR**2``.

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

OVERFITTING_VERSION = "astra-overfitting-1.1.0"
PBO_METHOD = "CSCV (Bailey, Borwein, Lopez de Prado, Zhu 2014)"
MAX_COMBINATIONS = 20_000
EULER_MASCHERONI = 0.5772156649015329
IN_SAMPLE_TIE_POLICY = "worst_out_of_sample_among_in_sample_ties_then_lowest_index"
DEFAULT_METRIC_NAME = "sharpe_like(mean / sample stdev)"
NULL_TRIAL_VARIANCE = "null_sampling_variance_1_over_T_minus_1"
SERIAL_DEPENDENCE_METHOD = (
    "sample autocorrelations rho_1..rho_m; Ljung-Box Q; T_eff = T / max(1 + 2 sum (1 - k/T) rho_k, 1) "
    "(Lo 2002, eq. 19-20, truncated at m lags)"
)
# Fewer trial Sharpe values than this give a cross-sectional variance too noisy to rely on alone.
MIN_TRIAL_SHARPES_FOR_VARIANCE = 5

KNOWN_LIMITATIONS = (
    "CSCV assumes the performance rows are comparable across blocks; strong serial dependence or regime change "
    "inside the sample weakens the estimate.",
    "PBO measures the selection process on the supplied variants only; untested or unrecorded variants are not counted.",
    "The deflated Sharpe ratio relies on asymptotic normal approximations and on an honest n_trials count; "
    "trials are counted as independent (no correlation-implied reduction of N), which is conservative.",
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
    n_evaluated: int = 0
    n_abstained: int = 0
    probability_oos_loss: float = 0.0
    metric: str = DEFAULT_METRIC_NAME
    in_sample_tie_policy: str = IN_SAMPLE_TIE_POLICY

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
    trial_sharpe_variance: float = 0.0
    trial_sharpe_variance_source: str = NULL_TRIAL_VARIANCE
    kurtosis_convention: str = "non-excess (normal = 3)"
    # Sample length used in the statistic: n_observations (i.i.d.) or the effective size T_eff.
    effective_observations: float | None = None
    serial_dependence: dict | None = None

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


def selection_score_metric(initial_equity: float | None = None) -> Callable[[Sequence[float]], float]:
    """Return the ASTRA selection rule applied to a chronological P&L path.

    ``validation._selection_score`` is ``net P&L - initial_equity * max_drawdown_pct / 100``
    where the drawdown is ``(peak - equity) / peak`` on an equity path starting at
    ``initial_equity``. The returned metric applies exactly that formula to
    ``initial_equity + cumulative(values)``. With ``initial_equity=None`` the drawdown is
    the plain cash peak-to-trough of the cumulative P&L (peak starting at 0).
    """

    if initial_equity is not None and (not _finite(initial_equity) or initial_equity <= 0):
        raise ValueError("initial_equity must be a finite number greater than 0")

    def metric(values: Sequence[float]) -> float:
        cumulative = 0.0
        if initial_equity is None:
            peak = worst = 0.0
            for value in values:
                cumulative += value
                peak = max(peak, cumulative)
                worst = max(worst, peak - cumulative)
            return cumulative - worst
        peak = float(initial_equity)
        worst_pct = 0.0
        for value in values:
            cumulative += value
            equity = initial_equity + cumulative
            peak = max(peak, equity)
            if peak > 0:
                worst_pct = max(worst_pct, (peak - equity) / peak * 100.0)
        return cumulative - initial_equity * worst_pct / 100.0

    suffix = "" if initial_equity is None else f", initial_equity={float(initial_equity)!r}"
    metric.metric_name = f"astra_selection_score(net_pnl - max_drawdown_cash{suffix})"  # type: ignore[attr-defined]
    return metric


def probability_of_backtest_overfitting(
    performance: Sequence[Sequence[float]],
    n_slices: int = 8,
    metric: Callable[[Sequence[float]], float] | None = None,
    *,
    eligibility: Sequence[Sequence[float]] | None = None,
    minimum_eligibility: float = 0,
) -> PBOResult:
    """Estimate PBO with CSCV.

    ``performance[t][v]`` is the per-period net return (or P&L) of variant ``v``
    in period ``t``, in chronological order. The ``T`` rows are split into
    ``n_slices`` contiguous blocks (``n_slices`` even, at least 2, each block at
    least 2 rows). Every combination of ``n_slices / 2`` blocks is used once as
    in-sample, its complement as out-of-sample. The variant with the best
    in-sample metric is ranked out-of-sample among all variants (ascending,
    ties: average rank), ``w = rank / (N + 1)`` and ``lambda = ln(w / (1 - w))``.
    PBO is the fraction of evaluated combinations with ``lambda <= 0``.

    In-sample ties are conservative: the tied variant with the worst
    out-of-sample metric is the one selected. With ``eligibility[t][v]``
    (non-negative counts, same shape as ``performance``), a variant can be
    selected only if its in-sample total reaches ``minimum_eligibility``;
    combinations without an eligible variant abstain and are excluded from
    the denominator. ``probability_oos_loss`` is the fraction of evaluated
    combinations whose selected variant has a negative OOS metric (paper,
    section 3.2).
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
    if metric is None:
        metric_name = DEFAULT_METRIC_NAME
    else:
        metric_name = str(getattr(metric, "metric_name", getattr(metric, "__name__", "custom")))
    count_columns: list[list[float]] | None = None
    if eligibility is not None:
        counts = _validated_matrix(eligibility, minimum_width=1)
        if len(counts) != rows or len(counts[0]) != variants:
            raise ValueError("eligibility must have the same shape as performance")
        if any(value < 0 for row in counts for value in row):
            raise ValueError("eligibility counts must be non-negative")
        count_columns = [[row[variant] for row in counts] for variant in range(variants)]
    if not _finite(minimum_eligibility) or minimum_eligibility < 0:
        raise ValueError("minimum_eligibility must be a finite non-negative number")

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

    def eligible_variants(blocks: Sequence[int]) -> list[int]:
        if count_columns is None:
            return list(range(variants))
        return [
            variant
            for variant in range(variants)
            if math.fsum(
                value for block in blocks for value in count_columns[variant][bounds[block][0] : bounds[block][1]]
            )
            >= minimum_eligibility
        ]

    logits: list[float] = []
    losses = 0
    abstained = 0
    for in_blocks in combinations(range(n_slices), half):
        out_blocks = tuple(block for block in range(n_slices) if block not in in_blocks)
        allowed = eligible_variants(in_blocks)
        if not allowed:
            abstained += 1
            continue
        in_scores = window_scores(in_blocks)
        out_scores = window_scores(out_blocks)
        top = max(in_scores[variant] for variant in allowed)
        tied_best = [variant for variant in allowed if in_scores[variant] == top]
        best = min(tied_best, key=lambda variant: (out_scores[variant], variant))
        chosen = out_scores[best]
        below = sum(1 for value in out_scores if value < chosen)
        tied = sum(1 for value in out_scores if value == chosen)
        rank = below + (tied + 1) / 2.0
        relative = rank / (variants + 1)
        logits.append(math.log(relative / (1.0 - relative)))
        losses += chosen < 0

    if not logits:
        raise ValueError("no combination had an eligible in-sample variant; PBO is undefined")
    overfit = sum(1 for value in logits if value <= 0)
    return PBOResult(
        pbo=overfit / len(logits),
        n_variants=variants,
        n_slices=n_slices,
        n_combinations=total,
        logits=tuple(logits),
        median_logit=statistics.median(logits),
        method=PBO_METHOD,
        n_evaluated=len(logits),
        n_abstained=abstained,
        probability_oos_loss=losses / len(logits),
        metric=metric_name,
    )


def expected_maximum_sharpe(n_trials: int, trial_sharpe_variance: float) -> float:
    """Approximate expected maximum of ``n_trials`` null Sharpe estimates (0 when ``n_trials == 1``).

    ``sqrt(V) * ((1 - gamma) * Z^-1(1 - 1/N) + gamma * Z^-1(1 - 1/(N e)))``, DSR paper eq. 1
    with a zero mean of trial Sharpes (the null).
    """

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


def deflated_sharpe_from_moments(
    *,
    sharpe: float,
    n_observations: int,
    skewness: float,
    kurtosis: float,
    n_trials: int,
    trial_sharpe_variance: float,
    effective_observations: float | None = None,
) -> tuple[float, float]:
    """Return ``(SR0, DSR)`` from summary statistics (DSR paper eq. 2; ``kurtosis`` non-excess).

    Useful when only moments are known, as in the paper's numerical example. With
    ``effective_observations`` (``T_eff <= T``, see ``serial_dependence``) the
    statistic uses ``sqrt(T_eff - 1)`` instead of ``sqrt(T - 1)``.
    """

    for value, name in ((sharpe, "sharpe"), (skewness, "skewness"), (kurtosis, "kurtosis")):
        if not _finite(value):
            raise ValueError(f"{name} must be a finite real number")
    _require_int(n_observations, "n_observations", 2)
    length = float(n_observations)
    if effective_observations is not None:
        if not _finite(effective_observations) or not 1.0 < effective_observations <= n_observations:
            raise ValueError("effective_observations must be finite, above 1 and at most n_observations")
        length = float(effective_observations)
    benchmark = expected_maximum_sharpe(n_trials, trial_sharpe_variance)
    denominator = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    if not denominator > 0:
        raise ValueError("the Sharpe ratio standard-error term is not positive for these returns")
    statistic = (sharpe - benchmark) * math.sqrt(length - 1.0) / math.sqrt(denominator)
    return benchmark, _STANDARD_NORMAL.cdf(statistic)


@dataclass(frozen=True, slots=True)
class SerialDependence:
    """Serial dependence of a return series and the effective sample size it implies.

    ``autocorrelations[k - 1]`` is the sample autocorrelation at lag ``k``
    (``sum (x_t - m)(x_{t-k} - m) / sum (x_t - m)^2``, the estimator of the
    Ljung-Box statistic). ``variance_inflation`` is
    ``1 + 2 * sum_{k=1..lags} (1 - k / T) * rho_k``: by Lo (2002, eq. 19-20) the
    variance of the ``T``-period sum is ``T * sigma^2 * variance_inflation`` (with
    the sum truncated at ``lags``), so the Sharpe of the whole sample is
    ``eta(T) * SR`` with ``eta(T)^2 = T / variance_inflation``.
    ``effective_observations = T / max(variance_inflation, 1)``: negative
    autocorrelation is never allowed to raise the sample size (conservative).
    """

    n_observations: int
    lags: int
    autocorrelations: tuple[float, ...]
    ljung_box_q: float
    ljung_box_p_value: float
    variance_inflation: float
    effective_observations: float
    method: str = SERIAL_DEPENDENCE_METHOD

    @property
    def max_abs_autocorrelation(self) -> float:
        return max((abs(value) for value in self.autocorrelations), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["autocorrelations"] = list(self.autocorrelations)
        payload["max_abs_autocorrelation"] = self.max_abs_autocorrelation
        json.dumps(payload, allow_nan=False)
        return payload


def sample_autocorrelations(values: Sequence[float], lags: int) -> tuple[float, ...]:
    """Sample autocorrelations at lags ``1..lags`` (biased estimator, common denominator)."""

    series = _validated_series(values)
    _require_int(lags, "lags", 1)
    count = len(series)
    if count <= lags + 1:
        raise ValueError(f"{lags} autocorrelation lags need more than {lags + 1} observations")
    mean = math.fsum(series) / count
    centred = [value - mean for value in series]
    denominator = math.fsum(value * value for value in centred)
    if denominator <= 0 or negligible_deviation(math.sqrt(denominator / count), series):
        raise ValueError("returns have zero or negligible variance; autocorrelations are undefined")
    return tuple(
        math.fsum(centred[index] * centred[index - lag] for index in range(lag, count)) / denominator
        for lag in range(1, lags + 1)
    )


def ljung_box(autocorrelations: Sequence[float], n_observations: int) -> tuple[float, float]:
    """Ljung-Box (1978) ``Q = T (T + 2) sum_k rho_k^2 / (T - k)`` and its chi-square(h) upper-tail p-value."""

    lags = len(autocorrelations)
    _require_int(lags, "lags", 1)
    _require_int(n_observations, "n_observations", lags + 2)
    statistic = n_observations * (n_observations + 2) * math.fsum(
        value * value / (n_observations - lag) for lag, value in enumerate(autocorrelations, start=1)
    )
    return statistic, chi_square_survival(statistic, lags)


def chi_square_survival(statistic: float, degrees_of_freedom: int) -> float:
    """``P[X >= statistic]`` for ``X ~ chi-square(degrees_of_freedom)`` (regularized upper gamma)."""

    _require_int(degrees_of_freedom, "degrees_of_freedom", 1)
    if not _finite(statistic) or statistic < 0:
        raise ValueError("statistic must be a finite non-negative number")
    return _regularized_upper_gamma(degrees_of_freedom / 2.0, statistic / 2.0)


def _regularized_upper_gamma(a: float, x: float) -> float:
    # Series for x < a + 1, Lentz continued fraction otherwise (standard numerical recipe).
    if x == 0.0:
        return 1.0
    log_prefix = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:
        term = total = 1.0 / a
        denominator = a
        for _ in range(10_000):
            denominator += 1.0
            term *= x / denominator
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(log_prefix))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for index in range(1, 10_000):
        an = -index * (index - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return min(1.0, math.exp(log_prefix) * h)


def lo_scale_factor(q: int, autocorrelations: Sequence[float]) -> float:
    """Lo (2002) eq. 20: ``eta(q) = q / sqrt(q + 2 sum_{k=1..q-1} (q - k) rho_k)``.

    Autocorrelations beyond the supplied ones are taken as zero.
    """

    _require_int(q, "q", 1)
    rho = list(autocorrelations)[: q - 1]
    variance = q + 2.0 * math.fsum((q - lag) * value for lag, value in enumerate(rho, start=1))
    if not variance > 0:
        raise ValueError("autocorrelations imply a non-positive variance of the aggregated return")
    return q / math.sqrt(variance)


def effective_sample_size(n_observations: int, autocorrelations: Sequence[float]) -> tuple[float, float]:
    """Return ``(T_eff, variance_inflation)``, ``T_eff = T / max(1 + 2 sum (1 - k/T) rho_k, 1)``."""

    _require_int(n_observations, "n_observations", 2)
    inflation = 1.0 + 2.0 * math.fsum(
        (1.0 - lag / n_observations) * value for lag, value in enumerate(autocorrelations, start=1)
    )
    return n_observations / max(inflation, 1.0), inflation


def serial_dependence(values: Sequence[float], lags: int) -> SerialDependence:
    """Autocorrelations, Ljung-Box statistic and effective sample size of ``values``."""

    rho = sample_autocorrelations(values, lags)
    count = len(values)
    statistic, p_value = ljung_box(rho, count)
    effective, inflation = effective_sample_size(count, rho)
    return SerialDependence(
        n_observations=count,
        lags=lags,
        autocorrelations=rho,
        ljung_box_q=statistic,
        ljung_box_p_value=p_value,
        variance_inflation=inflation,
        effective_observations=effective,
    )


def deflated_sharpe_ratio(
    returns: Sequence[float],
    *,
    n_trials: int,
    trial_sharpe_variance: float | None = None,
    autocorrelation_lags: int | None = None,
) -> DeflatedSharpeResult:
    """Deflated Sharpe ratio of the selected strategy (Bailey and Lopez de Prado, 2014).

    ``SR = mean / sample stdev`` (per period). ``SR0`` is the expected maximum
    Sharpe ratio of ``n_trials`` null trials with variance
    ``trial_sharpe_variance`` (default ``1 / (T - 1)``, the approximate variance
    of a Sharpe estimate under a zero-mean normal null). The result is
    ``Phi((SR - SR0) * sqrt(T - 1) / sqrt(1 - skew * SR + (kurt - 1) / 4 * SR**2))``
    (DSR paper eq. 2; ``kurt`` is non-excess, normal = 3).
    With ``n_trials == 1`` it reduces to the probabilistic Sharpe ratio against 0.
    ``trial_sharpe_variance`` must be in the same per-period unit as the returns
    (never an annualized variance with per-period returns).

    With ``autocorrelation_lags`` the sample's serial dependence is measured
    (``serial_dependence``) and ``T`` is replaced by the effective sample size
    ``T_eff`` (Lo 2002, eq. 19-20) in the statistic and in the default null
    variance ``1 / (T_eff - 1)``. Without it the returns are treated as i.i.d.
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
    dependence = None if autocorrelation_lags is None else serial_dependence(values, autocorrelation_lags)
    length = float(count) if dependence is None else dependence.effective_observations
    if length <= 2.0:
        raise ValueError(f"effective sample size {length:.3f} is too small for a deflated Sharpe ratio")
    variance = 1.0 / (length - 1.0) if trial_sharpe_variance is None else trial_sharpe_variance
    source = NULL_TRIAL_VARIANCE if trial_sharpe_variance is None else "supplied"
    benchmark, probability = deflated_sharpe_from_moments(
        sharpe=sharpe,
        n_observations=count,
        skewness=skewness,
        kurtosis=kurtosis,
        n_trials=n_trials,
        trial_sharpe_variance=variance,
        effective_observations=None if dependence is None else length,
    )
    return DeflatedSharpeResult(
        observed_sharpe=sharpe,
        expected_max_sharpe=benchmark,
        probability=probability,
        n_trials=n_trials,
        n_observations=count,
        skewness=skewness,
        kurtosis=kurtosis,
        trial_sharpe_variance=float(variance),
        trial_sharpe_variance_source=source,
        effective_observations=length,
        serial_dependence=None if dependence is None else dependence.to_dict(),
    )


@dataclass(frozen=True, slots=True)
class TrialUniverse:
    """Trials known outside one plan (for example a store-wide experiment registry).

    ``n_trials`` counts every trial that could have produced the selected
    strategy, including the current plan's variants. ``trial_sharpes`` are
    per-trial Sharpe estimates in the unit named by ``sharpe_unit`` and on bars
    of ``bar_seconds``; values in another unit or bar size are ignored (the
    count still applies). ``source`` is a free-text provenance label.
    """

    n_trials: int
    trial_sharpes: tuple[float, ...] = ()
    sharpe_unit: str = ""
    bar_seconds: int | None = None
    source: str = "external"

    def __post_init__(self) -> None:
        _require_int(self.n_trials, "n_trials", 1)
        if not isinstance(self.trial_sharpes, tuple) or not all(_finite(value) for value in self.trial_sharpes):
            raise ValueError("trial_sharpes must be a tuple of finite real numbers")
        if not isinstance(self.sharpe_unit, str) or not isinstance(self.source, str):
            raise ValueError("sharpe_unit and source must be strings")
        if self.bar_seconds is not None:
            _require_int(self.bar_seconds, "bar_seconds", 1)


@dataclass(frozen=True, slots=True)
class TrialResolution:
    """Number of trials and variance of trial Sharpes actually used by the deflated Sharpe."""

    n_trials: int
    n_trials_plan: int
    n_trials_source: str
    trial_sharpe_variance: float
    trial_sharpe_variance_method: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        json.dumps(payload, allow_nan=False)
        return payload


def cross_sectional_variance(sharpes: Sequence[float]) -> float | None:
    """Sample variance (``n - 1``) of the finite trial Sharpe values; ``None`` below 2 values."""

    values = [float(value) for value in sharpes if _finite(value)]
    if len(values) < 2:
        return None
    mean = math.fsum(values) / len(values)
    return math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)


def resolve_trials(
    *,
    plan_trials: int,
    plan_trial_sharpes: Sequence[float],
    sample_length: int,
    sharpe_unit: str,
    bar_seconds: int | None = None,
    universe: TrialUniverse | None = None,
) -> TrialResolution:
    """Choose ``n_trials`` and the trial-Sharpe variance for the deflated Sharpe, conservatively.

    * ``n_trials = max(plan_trials, universe.n_trials)``: the plan's own count
      is a lower bound on the number of trials.
    * The variance is the largest of: the null sampling variance
      ``1 / (sample_length - 1)`` (a floor: estimated Sharpes of skill-less
      trials vary at least that much), the cross-sectional variance of the
      plan's trial Sharpes when at least ``MIN_TRIAL_SHARPES_FOR_VARIANCE``
      values exist, and the cross-sectional variance of the universe's trial
      Sharpes (same unit and bar size only) under the same minimum. A larger
      variance raises the expected maximum Sharpe and lowers the deflated
      Sharpe probability.
    """

    _require_int(plan_trials, "plan_trials", 1)
    _require_int(sample_length, "sample_length", 2)
    notes: list[str] = []
    n_trials = plan_trials
    source = "plan"
    if universe is not None:
        if not isinstance(universe, TrialUniverse):
            raise TypeError("universe must be a TrialUniverse")
        if universe.n_trials >= plan_trials:
            n_trials, source = universe.n_trials, f"universe:{universe.source}"
        else:
            notes.append(
                f"universe n_trials {universe.n_trials} is below the plan's {plan_trials}; the plan count is used"
            )
    candidates = [(1.0 / (sample_length - 1), NULL_TRIAL_VARIANCE)]
    plan_values = [float(value) for value in plan_trial_sharpes if _finite(value)]
    plan_variance = cross_sectional_variance(plan_values)
    if plan_variance is not None and len(plan_values) >= MIN_TRIAL_SHARPES_FOR_VARIANCE:
        candidates.append((plan_variance, f"plan_cross_sectional({len(plan_values)} trials)"))
    else:
        notes.append(
            f"{len(plan_values)} plan trial Sharpe values (fewer than {MIN_TRIAL_SHARPES_FOR_VARIANCE}); "
            "their variance is not used"
        )
    if universe is not None and universe.trial_sharpes:
        same_bars = bar_seconds is None or universe.bar_seconds == bar_seconds
        if universe.sharpe_unit != sharpe_unit or not same_bars:
            notes.append(
                f"universe trial Sharpes ignored: unit {universe.sharpe_unit!r} on {universe.bar_seconds}s bars "
                f"differs from {sharpe_unit!r} on {bar_seconds}s bars"
            )
        elif len(universe.trial_sharpes) >= MIN_TRIAL_SHARPES_FOR_VARIANCE:
            variance = cross_sectional_variance(universe.trial_sharpes)
            if variance is not None:
                label = f"universe_cross_sectional({len(universe.trial_sharpes)} trials, {universe.source})"
                candidates.append((variance, label))
        else:
            notes.append(
                f"{len(universe.trial_sharpes)} universe trial Sharpe values (fewer than "
                f"{MIN_TRIAL_SHARPES_FOR_VARIANCE}); their variance is not used"
            )
    variance, method = max(candidates, key=lambda item: item[0])
    return TrialResolution(
        n_trials=n_trials,
        n_trials_plan=plan_trials,
        n_trials_source=source,
        trial_sharpe_variance=variance,
        trial_sharpe_variance_method=f"largest available estimate: {method}",
        notes=tuple(notes),
    )


def _validated_matrix(performance: Sequence[Sequence[float]], minimum_width: int = 2) -> list[list[float]]:
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
    if width is None or width < minimum_width:
        raise ValueError(f"performance needs at least {minimum_width} variants")
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
