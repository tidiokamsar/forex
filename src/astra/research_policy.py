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

RESEARCH_POLICY_VERSION = "astra-research-policy-0.2.0-provisional"


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
    # within match_tolerance times the new holdout's return scale. When a path of the pair drifts (half
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
        if not 0.0 <= self.scale_floor_fraction < 1.0:
            raise ValueError("invalid scale floor fraction")

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

    def payload(self) -> dict:
        return {"version": RESEARCH_POLICY_VERSION, **asdict(self)}


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
