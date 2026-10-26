"""Versioned, provisional research-integrity thresholds (Lot 4).

These values are technical floors for running diagnostics, not validated
financial recommendations and not evidence that a strategy is profitable.
They live in code on purpose: an agent cannot lower them at run time through
an API, a CLI flag or a database row. Changing one is a reviewed code change
that must bump ``RESEARCH_POLICY_VERSION`` and be justified in
RESEARCH_PROTOCOL.md; because this module is part of the research
implementation hash, a change also invalidates every holdout scored under the
previous policy (``code_changed_since_holdout``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

RESEARCH_POLICY_VERSION = "astra-research-policy-0.4.0-provisional"


@dataclass(frozen=True, slots=True)
class HoldoutReusePolicy:
    """Thresholds of the holdout reuse detector (``astra.holdout_reuse``)."""

    # Absolute correlation of robust-standardized log returns over an aligned window.
    conflict_abs_correlation: float = 0.98  # the plan is refused: the holdout is already spent
    suspect_abs_correlation: float = 0.95  # sealed, but promotion is refused until a new holdout is used
    window: int = 20
    min_window: int = 12
    min_holdout_returns: int = 16  # technical floor to run the diagnostic; not enough to promote
    min_informative_share: float = 0.6  # share of real moves a window needs to be compared
    informative_threshold: float = 0.25  # |standardized return| counted as a real move
    clip: float = 3.0  # robust z-scores are clipped so one aligned outlier cannot create a match
    # Second statistic, independent of correlation: share of aligned informative returns that are equal
    # within match_tolerance times the new holdout's return scale. When a path of the pair drifts (drift_share
    # its returns at the median), each path's median is removed first (a shared drift is not a match);
    # a return is informative when it is further than the tolerance from that centre.
    match_tolerance: float = 0.05
    conflict_match_share: float = 0.8
    # The match share must also be improbable by chance: binomial tail of the matches given the chance
    # match rate measured over every alignment of the pair (quantization grids, discrete moves),
    # Bonferroni-corrected by the number of spans of the same kind tested (per transform and sign).
    conflict_match_chance_probability: float = 1e-4  # the plan is refused
    suspect_match_chance_probability: float = 1e-2  # whole-overlap spans only: sealed, not scoreable
    # Fewest informative returns a NEW holdout needs to be compared at all (else UNDETERMINED), and
    # fewest matched informative returns a span needs to be a match.
    min_informative_points: int = 3
    # Returns smaller than this fraction of the largest absolute return do not set the return scale
    # (a nudged zero is not a move).
    scale_floor_fraction: float = 0.01
    # Share of returns at the median that makes a path a drift (its median is removed before matching).
    drift_share: float = 0.75

    def __post_init__(self) -> None:
        if not 0.0 < self.suspect_abs_correlation <= self.conflict_abs_correlation < 1.0:
            raise ValueError("require 0 < suspect_abs_correlation <= conflict_abs_correlation < 1")
        if not 4 <= self.min_window <= self.window:
            raise ValueError("require 4 <= min_window <= window")
        if self.min_holdout_returns < self.min_window:
            raise ValueError("min_holdout_returns must be at least min_window")
        if not 0.0 < self.min_informative_share <= 1.0 or self.informative_threshold <= 0.0 or self.clip <= 0.0:
            raise ValueError("invalid informative share, threshold or clip")
        if not 0.0 < self.match_tolerance < 1.0 or not 0.5 <= self.conflict_match_share <= 1.0:
            raise ValueError("invalid match tolerance or share")
        if not 0.0 < self.conflict_match_chance_probability <= self.suspect_match_chance_probability < 1.0:
            raise ValueError("require 0 < conflict_match_chance_probability <= suspect_match_chance_probability < 1")
        if self.min_informative_points < 2:
            raise ValueError("min_informative_points must be at least 2")
        if not 0.0 <= self.scale_floor_fraction < 1.0 or not 0.5 <= self.drift_share <= 1.0:
            raise ValueError("invalid scale floor fraction or drift share")

    def payload(self) -> dict:
        return {"version": RESEARCH_POLICY_VERSION, **asdict(self)}


@dataclass(frozen=True, slots=True)
class StatisticalPolicy:
    """Information floors of the overfitting diagnostics (``astra.strategy_validation``)."""

    min_dsr_returns: int = 30
    min_dsr_trades: int = 30  # technical floor for a preliminary DSR; not enough to show profitability
    min_pbo_nonzero_row_fraction: float = 0.5
    min_pbo_slices: int = 4
    min_pbo_trades_per_slice: int = 1  # each PBO variant needs this many trades per slice on average
    # Serial dependence of the DSR sample (policy 0.3.0, provisional engineering values that need a
    # statistical review; not tuned on any candidate). Autocorrelations rho_1..rho_m of the per-bar
    # returns are measured with m = dsr_autocorrelation_lags; the DSR uses the effective sample size
    # T_eff = T / max(1 + 2 sum (1 - k/T) rho_k, 1) (Lo 2002) and is not published when
    # T_eff < min_dsr_effective_returns or when any |rho_k| > max_dsr_abs_autocorrelation (the
    # truncated correction is then too weak an approximation).
    dsr_autocorrelation_lags: int = 5
    min_dsr_effective_returns: int = 30
    max_dsr_abs_autocorrelation: float = 0.3

    def __post_init__(self) -> None:
        if not 1 <= self.dsr_autocorrelation_lags < self.min_dsr_returns:
            raise ValueError("require 1 <= dsr_autocorrelation_lags < min_dsr_returns")
        if not 0.0 < self.max_dsr_abs_autocorrelation < 1.0:
            raise ValueError("require 0 < max_dsr_abs_autocorrelation < 1")

    def payload(self) -> dict:
        return {"version": RESEARCH_POLICY_VERSION, **asdict(self)}


# Direction in which each statistical value may move without weakening the check; "equal" values
# change what is measured and must match the reviewed policy exactly.
_STATISTICAL_STRICTER_WHEN = {
    "min_dsr_returns": "higher",
    "min_dsr_trades": "higher",
    "min_pbo_nonzero_row_fraction": "higher",
    "min_pbo_slices": "higher",
    "min_pbo_trades_per_slice": "higher",
    "dsr_autocorrelation_lags": "equal",
    "min_dsr_effective_returns": "higher",
    "max_dsr_abs_autocorrelation": "lower",
}


def statistical_policy_below_floor(applied: object) -> tuple[str, ...]:
    """Names of statistical values recorded as applied that are looser than ``StatisticalPolicy()`` (fail closed).

    Floors are stricter when higher, ``max_dsr_abs_autocorrelation`` when lower,
    and ``dsr_autocorrelation_lags`` must equal the policy. A missing, non-numeric
    or boolean value is reported too, so a report without the record (or minted
    without it) cannot pass.
    """

    floor = asdict(StatisticalPolicy())
    if not isinstance(applied, dict):
        return tuple(floor)
    below = []
    for name, reference in floor.items():
        value = applied.get(name)
        direction = _STATISTICAL_STRICTER_WHEN[name]
        invalid = isinstance(value, bool) or not isinstance(value, (int, float)) or value != value
        if invalid or (
            (direction == "higher" and value < reference)
            or (direction == "lower" and value > reference)
            or (direction == "equal" and value != reference)
        ):
            below.append(name)
    return tuple(below)


HOLDOUT_REUSE_POLICY = HoldoutReusePolicy()
STATISTICAL_POLICY = StatisticalPolicy()

# Direction in which each reuse threshold may move without weakening the check.
_STRICTER_WHEN = {
    "conflict_abs_correlation": "lower",
    "suspect_abs_correlation": "lower",
    "window": "any",
    "min_window": "any",
    "min_holdout_returns": "higher",
    "min_informative_share": "lower",
    "informative_threshold": "lower",
    "clip": "any",
    "match_tolerance": "higher",
    "conflict_match_share": "lower",
    "conflict_match_chance_probability": "higher",
    "suspect_match_chance_probability": "higher",
    "min_informative_points": "any",
    "scale_floor_fraction": "any",
    "drift_share": "any",
}


def reuse_policy_looser_than_floor(payload: dict) -> tuple[str, ...]:
    """Names of recorded reuse thresholds that are looser than the current policy (fail closed)."""

    floor = asdict(HOLDOUT_REUSE_POLICY)
    looser = []
    for item in fields(HoldoutReusePolicy):
        name, value = item.name, payload.get(item.name)
        direction = _STRICTER_WHEN[name]
        invalid = not isinstance(value, (int, float)) or isinstance(value, bool)
        if invalid or (direction == "lower" and value > floor[name]) or (direction == "higher" and value < floor[name]):
            looser.append(name)
    return tuple(looser)
