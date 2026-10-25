"""Sealed walk-forward evaluation shared by every implemented strategy family.

Candidates are ``(family, config)`` pairs for the SMA baseline, time-series
momentum and Donchian breakout families. The fold layout is the one of
``validation.WalkForwardProtocol``: an expanding training prefix, a fixed
validation block, a disjoint test block per fold, and a reserved final
holdout of ``holdout_bars``. Signals for a block are computed from bars up to
that block's end only, then simulated with ``run_signal_backtest`` from the
block start. Selection uses validation metrics only. Every candidate is also
scored on the test blocks, solely to build the overfitting inputs (PBO matrix);
those numbers never feed back into selection.

Mean reversion is excluded: it needs regime thresholds fitted on each fold's
training slice, which this protocol version does not implement.

Reports are simulation evidence. They are not a promotion decision and do not
authorize any order.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import features, models, regimes, research, risk, signal_backtest, strategies, validation
from .models import BacktestConfig, BacktestResult, CostModel, MarketBar
from .registry import EVIDENCE_FIELDS
from .research import _prepare_bars
from .signal_backtest import run_signal_backtest
from .strategies import (
    BREAKOUT_NAME,
    BREAKOUT_VERSION,
    MOMENTUM_NAME,
    MOMENTUM_VERSION,
    SMA_BASELINE_NAME,
    SMA_BASELINE_VERSION,
    BreakoutConfig,
    MomentumConfig,
    SmaBaselineConfig,
    decisions_to_signals,
    evaluate_breakout,
    evaluate_momentum,
    evaluate_sma_baseline,
)
from .validation import (
    AggregateMetrics,
    ResultMetrics,
    WalkForwardProtocol,
    _aggregate,
    _canonical_json,
    _cost_payload,
    _dataset_identity_sha256,
    _flat_metrics,
    _fold_ranges,
    _resolve_dataset_hash,
    _result_metrics,
    _scale_costs,
    _selection_score,
    _sha256,
)

STRATEGY_VALIDATION_VERSION = "1.0.0"
SELECTION_METRIC = validation.SELECTION_METRIC
MAX_CANDIDATES = validation.MAX_CANDIDATES
MAX_PBO_SLICES = 8
# CSCV needs per-period returns, not one total per window: each development
# window is split into periods of this many bars and each candidate's trade
# P&L is attributed to the period in which the trade exits.
PBO_PERIOD_BARS = 5
# Minimum information before the statistics are trusted (otherwise the gate stays unmet):
# the deflated Sharpe needs a real sample, and the PBO matrix must not be mostly empty or repetitive.
MIN_DSR_RETURNS = 30
MIN_PBO_NONZERO_ROW_FRACTION = 0.5
FLAT_BASELINE_NAME = "NO_POSITION@1"
FAMILIES = ("sma", "momentum", "breakout")
EXCLUDED_FAMILIES = {
    "mean_reversion": (
        "excluded: requires regime thresholds fitted on each fold's training slice, "
        "not implemented in this protocol version"
    ),
}
DSR_SAMPLE = (
    "per-trade net return (net P&L / initial equity) of the final selected candidate on "
    "development test windows (never used for selection), plus the holdout trades once evaluated"
)
DSR_TRIAL_VARIANCE = (
    "sample variance across candidates of the per-trade Sharpe-like ratio on validation windows "
    "(candidates with fewer than 2 validation trades or zero dispersion are omitted)"
)

_FAMILY_SPECS: dict[str, tuple[type, str, str, Any]] = {
    "sma": (SmaBaselineConfig, SMA_BASELINE_NAME, SMA_BASELINE_VERSION, evaluate_sma_baseline),
    "momentum": (MomentumConfig, MOMENTUM_NAME, MOMENTUM_VERSION, evaluate_momentum),
    "breakout": (BreakoutConfig, BREAKOUT_NAME, BREAKOUT_VERSION, evaluate_breakout),
}


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    """One pre-declared variant: a strategy family and its frozen configuration."""

    family: str
    config: SmaBaselineConfig | MomentumConfig | BreakoutConfig

    def __post_init__(self) -> None:
        if self.family not in _FAMILY_SPECS:
            raise ValueError(f"unsupported strategy family {self.family!r}; supported: {', '.join(FAMILIES)}")
        config_type = _FAMILY_SPECS[self.family][0]
        if type(self.config) is not config_type:
            raise TypeError(f"{self.family} candidates require a {config_type.__name__}")

    @property
    def strategy_name(self) -> str:
        return _FAMILY_SPECS[self.family][1]

    @property
    def strategy_version(self) -> str:
        return _FAMILY_SPECS[self.family][2]

    def payload(self) -> dict:
        return {
            "family": self.family,
            "strategy": self.strategy_name,
            "strategy_version": self.strategy_version,
            "config": asdict(self.config),
        }

    @property
    def candidate_id(self) -> str:
        return _sha256(_canonical_json(self.payload()))

    def descriptor(self) -> dict:
        return {"candidate_id": self.candidate_id, **self.payload()}

    def warmup_bars(self) -> int:
        config = self.config
        if isinstance(config, SmaBaselineConfig):
            core = config.slow_window + 1
        elif isinstance(config, MomentumConfig):
            core = max(config.lookback, config.atr_window) + 1
        else:
            core = max(config.channel_window, config.atr_window) + 1
        return max(core, config.stop_lookback + 1)


SMA_REFERENCE = StrategyCandidate("sma", SmaBaselineConfig())


def default_candidates() -> tuple[StrategyCandidate, ...]:
    """Illustrative, untuned grid across families; not a recommendation."""

    return (
        StrategyCandidate("sma", SmaBaselineConfig(fast_window=5, slow_window=13)),
        StrategyCandidate("sma", SmaBaselineConfig(fast_window=8, slow_window=21)),
        StrategyCandidate("sma", SmaBaselineConfig(fast_window=10, slow_window=30)),
        StrategyCandidate("momentum", MomentumConfig(lookback=10)),
        StrategyCandidate("momentum", MomentumConfig(lookback=20)),
        StrategyCandidate("breakout", BreakoutConfig(channel_window=10)),
        StrategyCandidate("breakout", BreakoutConfig(channel_window=20)),
    )


@dataclass(frozen=True, slots=True)
class StrategyTrial:
    """Validation outcome of one candidate in one fold.

    ``test`` is recorded only as an input to the overfitting matrix; it is
    computed after the fold's selection and never influences it.
    """

    fold: int
    candidate_id: str
    family: str
    config: dict
    status: str
    selection_score: float
    validation: ResultMetrics
    eligible: bool
    selected: bool
    test: ResultMetrics


@dataclass(frozen=True, slots=True)
class StrategyFold:
    fold: int
    train_start: datetime
    train_end: datetime
    train_observations: int
    validation_start: datetime
    validation_end: datetime
    validation_observations: int
    test_start: datetime
    test_end: datetime
    test_observations: int
    trials: tuple[StrategyTrial, ...]
    selected_candidate_id: str | None
    selected_candidate: ResultMetrics | None
    sma_reference: ResultMetrics
    no_position_baseline: ResultMetrics
    edge_vs_baseline: float | None


@dataclass(frozen=True, slots=True)
class StrategyFinalSelection:
    candidate_id: str
    family: str
    config: dict
    mean_validation_selection_score: float
    validation_net_pnl: float
    validation_trade_count: int
    eligible: bool
    selected: bool


@dataclass(frozen=True, slots=True)
class OverfittingAssessment:
    """Inputs and outputs of the PBO and deflated Sharpe checks.

    ``status`` is ``COMPLETED`` when both statistics were computed,
    ``INCOMPLETE`` when one raised, and ``UNAVAILABLE`` when the statistics
    module is not installed; missing values keep the registry gates unmet.
    """

    status: str
    variants_tested: int
    slice_labels: tuple[str, ...]
    performance_matrix: tuple[tuple[float, ...], ...]
    n_slices: int
    pbo: float | None
    pbo_result: dict | None
    deflated_sharpe_probability: float | None
    deflated_sharpe_result: dict | None
    dsr_sample: str
    dsr_returns: tuple[float, ...]
    trial_sharpe_variance: float | None
    trial_sharpe_variance_method: str
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyWalkForwardReport:
    protocol_version: str
    strategy_validation_version: str
    protocol_definition: dict
    plan_manifest: dict
    plan_sha256: str
    hypothesis: str
    dataset_sha256: str
    dataset_hash_kind: str
    dataset_identity_sha256: str
    implementation_sha256: str
    selection_metric: str
    variants_tested: int
    families: tuple[str, ...]
    excluded_families: dict
    candidates: tuple[dict, ...]
    folds: tuple[StrategyFold, ...]
    final_candidate_selection: tuple[StrategyFinalSelection, ...]
    selected_candidate_id: str | None
    selected_candidate_test_aggregate: AggregateMetrics
    sma_reference_test_aggregate: AggregateMetrics
    no_position_test_aggregate: AggregateMetrics
    overfitting: OverfittingAssessment
    initial_equity: float
    cost_model: dict
    holdout_status: str
    holdout_observations: int
    holdout_start: datetime
    holdout_end: datetime
    evaluation_classification: str


@dataclass(frozen=True, slots=True)
class StrategyCostSensitivity:
    cost_multiplier: float
    selected_candidate: ResultMetrics | None
    sma_reference: ResultMetrics


@dataclass(frozen=True, slots=True)
class StrategyHoldoutEvaluation:
    protocol_id: str
    plan_sha256: str
    dataset_sha256: str
    dataset_identity_sha256: str
    status: str
    evaluated_at: datetime
    selected_candidate_id: str | None
    selected_family: str | None
    selected_config: dict | None
    final_candidate_selection: tuple[StrategyFinalSelection, ...]
    selected_candidate: ResultMetrics | None
    sma_reference: ResultMetrics
    no_position_baseline: ResultMetrics
    edge_vs_baseline: float | None
    cost_sensitivity: tuple[StrategyCostSensitivity, ...]
    overfitting: OverfittingAssessment
    variants_tested: int
    holdout_start: datetime
    holdout_end: datetime
    holdout_observations: int
    assumptions: tuple[str, ...]
    evaluation_classification: str


def evaluate_strategy_walk_forward(
    bars: Iterable[MarketBar],
    candidates: Iterable[StrategyCandidate] | None = None,
    *,
    hypothesis: str,
    protocol: WalkForwardProtocol = WalkForwardProtocol(),
    initial_equity: float = 10_000.0,
    cost_model: CostModel | None = None,
    dataset_sha256: str | None = None,
    store=None,
) -> StrategyWalkForwardReport:
    """Select on validation windows across families and score disjoint tests.

    With ``store`` the plan is sealed before any window is scored; a different
    plan for the same canonical dataset identity is rejected by the store. No
    metric is computed over the reserved final holdout here.
    """

    history, chosen, cost_model = _prepare(bars, candidates, protocol, initial_equity, cost_model)
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("a falsifiable, non-empty hypothesis is required")
    development_end = len(history) - protocol.holdout_bars
    testable = development_end - protocol.minimum_training_bars - protocol.fold_count * protocol.validation_bars
    if testable < protocol.fold_count:
        raise ValueError(
            "dataset must contain minimum_training_bars, validation_bars for every fold, "
            "at least one test bar per fold, and the reserved holdout"
        )
    ranges = _fold_ranges(
        development_end, protocol.minimum_training_bars, protocol.validation_bars, protocol.fold_count
    )
    resolved_hash, hash_kind = _resolve_dataset_hash(history, dataset_sha256)
    identity_hash = _dataset_identity_sha256(history)
    implementation_hash = _implementation_sha256()
    descriptors = tuple(candidate.descriptor() for candidate in chosen)
    families = tuple(family for family in FAMILIES if any(c.family == family for c in chosen))
    n_slices = pbo_slice_count(_period_count(ranges) // 2)
    plan = {
        "protocol": asdict(protocol),
        "strategy_validation_version": STRATEGY_VALIDATION_VERSION,
        "protocol_use_policy": (
            "EXPLORATORY_ONLY; one sealed plan per canonical dataset per local store; "
            "confirmation requires a fresh independent dataset"
        ),
        "hypothesis": hypothesis.strip(),
        "dataset_sha256": resolved_hash,
        "dataset_hash_kind": hash_kind,
        "dataset_identity_sha256": identity_hash,
        "implementation_sha256": implementation_hash,
        "selection_metric": SELECTION_METRIC,
        "selection_scope": "validation windows only; test and holdout metrics never influence selection",
        "final_holdout_selection": "aggregate_mean_validation_score_across_validation_windows",
        "families": list(families),
        "excluded_families": EXCLUDED_FAMILIES,
        "candidates": list(descriptors),
        "variants_tested": len(chosen),
        "baselines": {"flat": FLAT_BASELINE_NAME, "reference": SMA_REFERENCE.descriptor()},
        "initial_equity": initial_equity,
        "cost_model": _cost_payload(cost_model),
        "cost_stress": "signals use base costs; simulated costs scaled by each protocol multiplier",
        "overfitting": {
            "pbo_matrix": (
                "net trade P&L per PBO_PERIOD_BARS-bar period of each development window "
                "(by trade exit time) and variant"
            ),
            "pbo_period_bars": PBO_PERIOD_BARS,
            "n_slices": n_slices,
            "max_slices": MAX_PBO_SLICES,
            "dsr_sample": DSR_SAMPLE,
            "dsr_trial_variance": DSR_TRIAL_VARIANCE,
        },
        "folds": [
            {
                "fold": ordinal,
                "train_start": history[0].timestamp.isoformat(),
                "train_end": history[vs - 1].timestamp.isoformat(),
                "validation_start": history[vs].timestamp.isoformat(),
                "validation_end": history[ve - 1].timestamp.isoformat(),
                "test_start": history[ts].timestamp.isoformat(),
                "test_end": history[te - 1].timestamp.isoformat(),
                "train_end_index_exclusive": vs,
                "validation_start_index": vs,
                "validation_end_index_exclusive": ve,
                "test_start_index": ts,
                "test_end_index_exclusive": te,
            }
            for ordinal, (vs, ve, ts, te) in enumerate(ranges, start=1)
        ],
        "instrument": history[0].instrument,
        "holdout": {
            "status": "RESERVED_NOT_EVALUATED",
            "observations": protocol.holdout_bars,
            "start": history[development_end].timestamp.isoformat(),
            "end": history[-1].timestamp.isoformat(),
        },
    }
    plan_hash = _sha256(_canonical_json(plan))
    if store is not None:
        store.seal_research_protocol(
            protocol_id=plan_hash,
            dataset_identity_sha256=identity_hash,
            protocol_version=protocol.version,
            plan_manifest=plan,
        )

    ids = tuple(candidate.candidate_id for candidate in chosen)
    folds: list[StrategyFold] = []
    matrix: list[tuple[float, ...]] = []
    labels: list[str] = []
    validation_trades: dict[str, list[float]] = {cid: [] for cid in ids}
    test_trades: dict[str, list[float]] = {cid: [] for cid in ids}
    for ordinal, (vs, ve, ts, te) in enumerate(ranges, start=1):
        # 1) Validation only, then selection. Test results do not exist yet.
        validation_results = {
            candidate.candidate_id: _simulate(
                candidate, history, vs, ve, initial_equity, cost_model, 1.0, "VALIDATION_SELECTION_NOT_PROMOTABLE"
            )
            for candidate in chosen
        }
        scores = {cid: _selection_score(result) for cid, result in validation_results.items()}
        eligible = {
            cid: result.trade_count >= protocol.minimum_candidate_trades
            for cid, result in validation_results.items()
        }
        eligible_ids = [cid for cid in ids if eligible[cid]]
        selected_id = (
            min(eligible_ids, key=lambda cid: (-scores[cid], -validation_results[cid].net_pnl, cid))
            if eligible_ids
            else None
        )
        # 2) Test blocks: scored for every candidate (overfitting inputs only).
        test_results = {
            candidate.candidate_id: _simulate(
                candidate, history, ts, te, initial_equity, cost_model, 1.0, "WALK_FORWARD_TEST_NOT_PROMOTABLE"
            )
            for candidate in chosen
        }
        reference = _simulate(
            SMA_REFERENCE, history, ts, te, initial_equity, cost_model, 1.0, "WALK_FORWARD_REFERENCE_NOT_PROMOTABLE"
        )
        flat = _flat_metrics(history[ts].timestamp, history[te - 1].timestamp, te - ts, initial_equity)
        trials = tuple(
            StrategyTrial(
                fold=ordinal,
                candidate_id=candidate.candidate_id,
                family=candidate.family,
                config=candidate.payload(),
                status="COMPLETED",
                selection_score=scores[candidate.candidate_id],
                validation=_result_metrics(validation_results[candidate.candidate_id]),
                eligible=eligible[candidate.candidate_id],
                selected=candidate.candidate_id == selected_id,
                test=_result_metrics(test_results[candidate.candidate_id]),
            )
            for candidate in chosen
        )
        selected_metrics = _result_metrics(test_results[selected_id]) if selected_id else None
        folds.append(
            StrategyFold(
                fold=ordinal,
                train_start=history[0].timestamp,
                train_end=history[vs - 1].timestamp,
                train_observations=vs,
                validation_start=history[vs].timestamp,
                validation_end=history[ve - 1].timestamp,
                validation_observations=ve - vs,
                test_start=history[ts].timestamp,
                test_end=history[te - 1].timestamp,
                test_observations=te - ts,
                trials=trials,
                selected_candidate_id=selected_id,
                selected_candidate=selected_metrics,
                sma_reference=_result_metrics(reference),
                no_position_baseline=flat,
                edge_vs_baseline=(
                    selected_metrics.net_pnl - max(flat.net_pnl, reference.net_pnl) if selected_metrics else None
                ),
            )
        )
        for kind, (start, stop), results in (
            ("validation", (vs, ve), validation_results),
            ("test", (ts, te), test_results),
        ):
            period_labels, period_rows = _period_rows(history, start, stop, ids, results, f"fold{ordinal}_{kind}")
            labels.extend(period_labels)
            matrix.extend(period_rows)
        for cid in ids:
            validation_trades[cid].extend(_trade_returns(validation_results[cid], initial_equity))
            test_trades[cid].extend(_trade_returns(test_results[cid], initial_equity))

    final_selection = _final_selection(chosen, folds, protocol.minimum_candidate_trades)
    final_id = next((item.candidate_id for item in final_selection if item.selected), None)
    trial_sharpes = [value for value in (_sharpe(validation_trades[cid]) for cid in ids) if value is not None]
    overfitting = assess_overfitting(
        matrix,
        labels,
        variants_tested=len(chosen),
        dsr_returns=test_trades[final_id] if final_id else (),
        trial_sharpe_variance=statistics.variance(trial_sharpes) if len(trial_sharpes) >= 2 else None,
    )
    denominator = initial_equity * len(folds)
    return StrategyWalkForwardReport(
        protocol_version=protocol.version,
        strategy_validation_version=STRATEGY_VALIDATION_VERSION,
        protocol_definition=asdict(protocol),
        plan_manifest=plan,
        plan_sha256=plan_hash,
        hypothesis=hypothesis.strip(),
        dataset_sha256=resolved_hash,
        dataset_hash_kind=hash_kind,
        dataset_identity_sha256=identity_hash,
        implementation_sha256=implementation_hash,
        selection_metric=SELECTION_METRIC,
        variants_tested=len(chosen),
        families=families,
        excluded_families=dict(EXCLUDED_FAMILIES),
        candidates=descriptors,
        folds=tuple(folds),
        final_candidate_selection=final_selection,
        selected_candidate_id=final_id,
        selected_candidate_test_aggregate=_aggregate([fold.selected_candidate for fold in folds], denominator),
        sma_reference_test_aggregate=_aggregate([fold.sma_reference for fold in folds], denominator),
        no_position_test_aggregate=_aggregate([fold.no_position_baseline for fold in folds], denominator),
        overfitting=overfitting,
        initial_equity=initial_equity,
        cost_model=_cost_payload(cost_model),
        holdout_status="RESERVED_NOT_EVALUATED",
        holdout_observations=protocol.holdout_bars,
        holdout_start=history[development_end].timestamp,
        holdout_end=history[-1].timestamp,
        evaluation_classification="EXPLORATORY_STRATEGY_WALK_FORWARD_NOT_PROMOTABLE",
    )


def evaluate_strategy_holdout_once(
    bars: Iterable[MarketBar],
    candidates: Iterable[StrategyCandidate] | None = None,
    *,
    report: StrategyWalkForwardReport,
    protocol: WalkForwardProtocol,
    store,
    initial_equity: float = 10_000.0,
    cost_model: CostModel | None = None,
    dataset_sha256: str | None = None,
) -> StrategyHoldoutEvaluation | dict:
    """Score the reserved final block once, after a durable STARTED record.

    Selection is recomputed from validation windows and must reproduce the
    sealed plan. A completed retry returns the stored report (a ``dict``);
    a STARTED or FAILED attempt stays consumed.
    """

    history, chosen, cost_model = _prepare(bars, candidates, protocol, initial_equity, cost_model)
    resolved_hash, _ = _resolve_dataset_hash(history, dataset_sha256)
    identity_hash = _dataset_identity_sha256(history)
    if resolved_hash != report.dataset_sha256:
        raise ValueError("dataset hash does not match the sealed plan")
    if identity_hash != report.dataset_identity_sha256:
        raise ValueError("canonical market-bar identity does not match the sealed plan")
    if _sha256(_canonical_json(report.plan_manifest)) != report.plan_sha256:
        raise ValueError("plan manifest does not match its hash")
    if _implementation_sha256() != report.implementation_sha256:
        raise ValueError("research implementation changed after the plan was sealed")
    if asdict(protocol) != report.protocol_definition:
        raise ValueError("protocol does not match the sealed plan")
    if tuple(candidate.descriptor() for candidate in chosen) != tuple(report.candidates):
        raise ValueError("candidates do not match the sealed plan")
    if len(history) <= protocol.holdout_bars:
        raise ValueError("dataset is too short for the reserved holdout")
    holdout_start_index = len(history) - protocol.holdout_bars
    holdout_start = history[holdout_start_index].timestamp
    holdout_end = history[-1].timestamp
    if (holdout_start, holdout_end) != (report.holdout_start, report.holdout_end):
        raise ValueError("holdout boundaries do not match the sealed plan")

    # Recompute from bars so an edited report object cannot change selection.
    verified = evaluate_strategy_walk_forward(
        history,
        chosen,
        hypothesis=report.hypothesis,
        protocol=protocol,
        initial_equity=initial_equity,
        cost_model=cost_model,
        dataset_sha256=dataset_sha256,
    )
    if verified.plan_sha256 != report.plan_sha256:
        raise ValueError("recomputed plan does not match the sealed plan")

    store.seal_research_protocol(
        protocol_id=report.plan_sha256,
        dataset_identity_sha256=identity_hash,
        protocol_version=report.protocol_version,
        plan_manifest=report.plan_manifest,
    )
    attempt = store.begin_holdout_evaluation(protocol_id=report.plan_sha256, dataset_identity_sha256=identity_hash)
    if attempt["status"] == "COMPLETED":
        return attempt["report"]

    try:
        by_id = {candidate.candidate_id: candidate for candidate in chosen}
        selected = by_id.get(verified.selected_candidate_id) if verified.selected_candidate_id else None
        end = len(history)
        selected_result = (
            _simulate(selected, history, holdout_start_index, end, initial_equity, cost_model, 1.0,
                      "ONE_TIME_HOLDOUT_NOT_PROMOTABLE")
            if selected
            else None
        )
        reference = _simulate(
            SMA_REFERENCE, history, holdout_start_index, end, initial_equity, cost_model, 1.0,
            "ONE_TIME_HOLDOUT_REFERENCE",
        )
        flat = _flat_metrics(holdout_start, holdout_end, protocol.holdout_bars, initial_equity)
        sensitivity = tuple(
            StrategyCostSensitivity(
                cost_multiplier=multiplier,
                selected_candidate=(
                    _result_metrics(
                        _simulate(selected, history, holdout_start_index, end, initial_equity, cost_model,
                                  multiplier, "ONE_TIME_HOLDOUT_COST_STRESS")
                    )
                    if selected
                    else None
                ),
                sma_reference=_result_metrics(
                    _simulate(SMA_REFERENCE, history, holdout_start_index, end, initial_equity, cost_model,
                              multiplier, "ONE_TIME_HOLDOUT_COST_STRESS")
                ),
            )
            for multiplier in protocol.cost_stress_multipliers
        )
        dev = verified.overfitting
        overfitting = assess_overfitting(
            dev.performance_matrix,
            dev.slice_labels,
            variants_tested=verified.variants_tested,
            dsr_returns=(
                (*dev.dsr_returns, *_trade_returns(selected_result, initial_equity)) if selected_result else ()
            ),
            trial_sharpe_variance=dev.trial_sharpe_variance,
        )
        selected_metrics = _result_metrics(selected_result) if selected_result else None
        evaluation = StrategyHoldoutEvaluation(
            protocol_id=report.plan_sha256,
            plan_sha256=report.plan_sha256,
            dataset_sha256=resolved_hash,
            dataset_identity_sha256=identity_hash,
            status="COMPLETED",
            evaluated_at=datetime.now(UTC),
            selected_candidate_id=selected.candidate_id if selected else None,
            selected_family=selected.family if selected else None,
            selected_config=selected.payload() if selected else None,
            final_candidate_selection=verified.final_candidate_selection,
            selected_candidate=selected_metrics,
            sma_reference=_result_metrics(reference),
            no_position_baseline=flat,
            edge_vs_baseline=(
                selected_metrics.net_pnl - max(flat.net_pnl, reference.net_pnl) if selected_metrics else None
            ),
            cost_sensitivity=sensitivity,
            overfitting=overfitting,
            variants_tested=verified.variants_tested,
            holdout_start=holdout_start,
            holdout_end=holdout_end,
            holdout_observations=protocol.holdout_bars,
            assumptions=(
                "The holdout lock is scoped to this configured SQLite store and canonical market-bar identity; "
                "the local database is mutable and can be replaced.",
                "The final candidate was selected from aggregate validation-window scores before the holdout was scored.",
                "edge_vs_baseline is the selected candidate's holdout net P&L minus the larger of the flat and "
                "SMA reference holdout net P&L.",
                "Cost stress keeps signals computed with base costs and scales simulated spread, slippage and commission.",
                "Metrics are historical simulation estimates, not a guarantee or a promotion decision.",
            ),
            evaluation_classification="ONE_TIME_HOLDOUT_NOT_PROMOTABLE",
        )
        store.complete_holdout_evaluation(evaluation_id=attempt["evaluation_id"], report=asdict(evaluation))
        return evaluation
    except Exception as exc:
        store.fail_holdout_evaluation(evaluation_id=attempt["evaluation_id"], error_type=type(exc).__name__)
        raise


def holdout_evidence(report: StrategyHoldoutEvaluation | Mapping[str, Any]) -> dict[str, Any]:
    """Return exactly the registry evidence fields for a holdout evaluation.

    Accepts the dataclass or the stored ``dict`` a completed retry returns.
    The cost-stress figure is the worst selected net P&L among multipliers
    above 1.0. Missing values stay ``None`` so registry gates fail closed.
    """

    data = asdict(report) if is_dataclass(report) else dict(report)
    selected = data.get("selected_candidate")
    stressed = [
        scenario["selected_candidate"]["net_pnl"]
        for scenario in data.get("cost_sensitivity", ())
        if scenario["cost_multiplier"] > 1.0 and scenario.get("selected_candidate")
    ]
    overfitting = data.get("overfitting") or {}
    values = {
        "trade_count": selected["trade_count"] if selected else 0,
        "net_pnl": selected["net_pnl"] if selected else None,
        "profit_factor": selected["profit_factor"] if selected else None,
        "max_drawdown_pct": selected["max_drawdown_pct"] if selected else None,
        "edge_vs_baseline": data.get("edge_vs_baseline"),
        "cost_stress_net_pnl": min(stressed) if stressed else None,
        "holdout_used": data.get("status") == "COMPLETED",
        "pbo": overfitting.get("pbo"),
        "deflated_sharpe_probability": overfitting.get("deflated_sharpe_probability"),
        "variants_tested": data.get("variants_tested"),
    }
    return {key: values[key] for key in EVIDENCE_FIELDS}


def _period_bounds(start: int, stop: int) -> list[tuple[int, int]]:
    return [(first, min(first + PBO_PERIOD_BARS, stop)) for first in range(start, stop, PBO_PERIOD_BARS)]


def _period_count(ranges) -> int:
    return sum(len(_period_bounds(vs, ve)) + len(_period_bounds(ts, te)) for vs, ve, ts, te in ranges)


def _period_rows(history, start, stop, ids, results, label_prefix):
    """Net trade P&L per candidate for each PBO_PERIOD_BARS period of one window, by trade exit time."""

    bounds = _period_bounds(start, stop)
    labels = [f"{label_prefix}_p{number}" for number in range(1, len(bounds) + 1)]
    ends = [history[last - 1].timestamp for _first, last in bounds]
    rows = [[0.0] * len(ids) for _ in bounds]
    for column, cid in enumerate(ids):
        for trade in results[cid].trades:
            # Trades of a window exit inside it; the first period ending at or after the exit owns it.
            row = next((index for index, end in enumerate(ends) if trade.exit_time <= end), len(ends) - 1)
            rows[row][column] += trade.net_pnl
    return labels, [tuple(row) for row in rows]


def pbo_slice_count(available_slices: int) -> int:
    """Largest even number not above ``min(MAX_PBO_SLICES, available_slices)``."""

    count = min(MAX_PBO_SLICES, int(available_slices))
    count -= count % 2
    if count < 2:
        raise ValueError("at least two development slices are required for PBO")
    return count


def assess_overfitting(
    performance: Iterable[Iterable[float]],
    slice_labels: Iterable[str],
    *,
    variants_tested: int,
    dsr_returns: Iterable[float],
    trial_sharpe_variance: float | None,
) -> OverfittingAssessment:
    """Call the statistics module on ``performance[t][v]`` and the DSR sample.

    The import is lazy so this module works before ``astra.overfitting`` is
    integrated; without it the assessment is ``UNAVAILABLE`` and the
    registry's overfitting gate stays unmet.
    """

    matrix = tuple(tuple(float(value) for value in row) for row in performance)
    labels = tuple(slice_labels)
    returns = tuple(float(value) for value in dsr_returns)
    # At least two rows per slice, as the statistics module requires.
    n_slices = pbo_slice_count(len(matrix) // 2)
    common = {
        "variants_tested": variants_tested,
        "slice_labels": labels,
        "performance_matrix": matrix,
        "n_slices": n_slices,
        "dsr_sample": DSR_SAMPLE,
        "dsr_returns": returns,
        "trial_sharpe_variance": trial_sharpe_variance,
        "trial_sharpe_variance_method": DSR_TRIAL_VARIANCE,
    }
    try:
        from .overfitting import deflated_sharpe_ratio, probability_of_backtest_overfitting
    except ImportError:
        return OverfittingAssessment(
            status="UNAVAILABLE",
            pbo=None,
            pbo_result=None,
            deflated_sharpe_probability=None,
            deflated_sharpe_result=None,
            errors=("astra.overfitting is not available; overfitting gates remain unmet",),
            **common,
        )
    errors: list[str] = []
    low_information: list[str] = []
    pbo = pbo_payload = dsr = dsr_payload = None
    nonzero_rows = sum(1 for row in matrix if any(value != 0.0 for value in row))
    distinct_rows = len(set(matrix))
    columns = len(matrix[0]) if matrix else 0
    min_active = max(2, n_slices // 2)
    inactive_variants = sum(
        1 for column in range(columns) if sum(1 for row in matrix if row[column] != 0.0) < min_active
    )
    if inactive_variants:
        # A variant that barely trades makes its rank, and therefore the PBO, arbitrary.
        low_information.append(
            f"pbo: {inactive_variants} of {columns} variants have fewer than {min_active} non-zero periods"
        )
    elif nonzero_rows < MIN_PBO_NONZERO_ROW_FRACTION * len(matrix) or distinct_rows < n_slices:
        # A mostly empty or repetitive matrix gives a PBO that looks precise but says nothing.
        low_information.append(
            f"pbo: {nonzero_rows}/{len(matrix)} non-zero rows and {distinct_rows} distinct rows "
            f"(need at least {MIN_PBO_NONZERO_ROW_FRACTION:.0%} non-zero and {n_slices} distinct)"
        )
    else:
        try:
            result = probability_of_backtest_overfitting([list(row) for row in matrix], n_slices=n_slices)
            pbo, pbo_payload = _finite_or_none(result.pbo), _json_safe(result.to_dict())
        except (ValueError, ArithmeticError) as exc:
            errors.append(f"pbo: {type(exc).__name__}: {exc}")
    if len(returns) < MIN_DSR_RETURNS:
        low_information.append(f"deflated_sharpe: {len(returns)} returns (need at least {MIN_DSR_RETURNS})")
    else:
        try:
            result = deflated_sharpe_ratio(
                list(returns), n_trials=variants_tested, trial_sharpe_variance=trial_sharpe_variance
            )
            dsr, dsr_payload = _finite_or_none(result.probability), _json_safe(result.to_dict())
        except (ValueError, ArithmeticError) as exc:
            errors.append(f"deflated_sharpe: {type(exc).__name__}: {exc}")
    if pbo is not None and dsr is not None:
        status = "COMPLETED"
    elif errors:
        status = "INCOMPLETE"
    else:
        status = "LOW_INFORMATION"
    errors.extend(low_information)
    return OverfittingAssessment(
        status=status,
        pbo=pbo,
        pbo_result=pbo_payload,
        deflated_sharpe_probability=dsr,
        deflated_sharpe_result=dsr_payload,
        errors=tuple(errors),
        **common,
    )


def _prepare(
    bars: Iterable[MarketBar],
    candidates: Iterable[StrategyCandidate] | None,
    protocol: WalkForwardProtocol,
    initial_equity: float,
    cost_model: CostModel | None,
) -> tuple[tuple[MarketBar, ...], tuple[StrategyCandidate, ...], CostModel]:
    if not isinstance(protocol, WalkForwardProtocol):
        raise TypeError("protocol must be a WalkForwardProtocol")
    history = _prepare_bars(bars)
    if not history:
        raise ValueError("strategy walk-forward evaluation requires bars")
    chosen = default_candidates() if candidates is None else tuple(candidates)
    if any(not isinstance(candidate, StrategyCandidate) for candidate in chosen):
        raise TypeError("candidates must contain StrategyCandidate values")
    if len(chosen) < 2:
        raise ValueError("at least two candidates are required for a selection and an overfitting check")
    if len(chosen) > MAX_CANDIDATES:
        raise ValueError(f"candidate count cannot exceed {MAX_CANDIDATES}")
    if len({candidate.candidate_id for candidate in chosen}) != len(chosen):
        raise ValueError("candidates must be unique")
    cost_model = CostModel() if cost_model is None else cost_model
    # Validates equity and costs with the shared backtest rules.
    research._validate_config(BacktestConfig(initial_equity=initial_equity, cost_model=cost_model))
    warmup = max(candidate.warmup_bars() for candidate in (*chosen, SMA_REFERENCE))
    if protocol.minimum_training_bars < warmup:
        raise ValueError(f"minimum_training_bars must cover the candidate and reference warm-up ({warmup} bars)")
    return history, chosen, cost_model


def _simulate(
    candidate: StrategyCandidate,
    history: tuple[MarketBar, ...],
    start: int,
    end: int,
    initial_equity: float,
    cost_model: CostModel,
    cost_multiplier: float,
    classification: str,
) -> BacktestResult:
    """Decide on ``history[:end]`` only, then simulate from ``history[start]``."""

    window = history[:end]
    evaluate = _FAMILY_SPECS[candidate.family][3]
    signals = decisions_to_signals(evaluate(window, candidate.config, cost_model))
    config = BacktestConfig(
        initial_equity=initial_equity,
        stop_lookback=candidate.config.stop_lookback,
        stop_range_multiple=candidate.config.stop_range_multiple,
        cost_model=cost_model,
    )
    if cost_multiplier != 1.0:
        config = _scale_costs(config, cost_multiplier)
    return run_signal_backtest(
        window,
        signals,
        config,
        candidate.strategy_name,
        candidate.strategy_version,
        provenance={
            "evaluation_classification": classification,
            "candidate_id": candidate.candidate_id,
            "cost_stress_multiplier": cost_multiplier,
        },
        evaluation_start_time=history[start].timestamp,
    )


def _final_selection(
    chosen: tuple[StrategyCandidate, ...], folds: list[StrategyFold], minimum_trades: int
) -> tuple[StrategyFinalSelection, ...]:
    rows = []
    for candidate in chosen:
        trials = [trial for fold in folds for trial in fold.trials if trial.candidate_id == candidate.candidate_id]
        trades = sum(trial.validation.trade_count for trial in trials)
        rows.append(
            (
                candidate,
                sum(trial.selection_score for trial in trials) / len(trials),
                sum(trial.validation.net_pnl for trial in trials),
                trades,
                trades >= minimum_trades,
            )
        )
    eligible = [row for row in rows if row[4]]
    winner = (
        min(eligible, key=lambda row: (-row[1], -row[2], row[0].candidate_id))[0].candidate_id if eligible else None
    )
    return tuple(
        StrategyFinalSelection(
            candidate_id=candidate.candidate_id,
            family=candidate.family,
            config=candidate.payload(),
            mean_validation_selection_score=score,
            validation_net_pnl=net,
            validation_trade_count=trades,
            eligible=ok,
            selected=candidate.candidate_id == winner,
        )
        for candidate, score, net, trades, ok in rows
    )


def _trade_returns(result: BacktestResult, initial_equity: float) -> list[float]:
    return [trade.net_pnl / initial_equity for trade in result.trades]


def _sharpe(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    # Same relative tolerance as astra.overfitting (kept local: that module may be absent).
    scale = max(abs(value) for value in values)
    if deviation <= 0 or deviation <= 1e-9 * max(scale, 1e-300):
        return None
    return statistics.fmean(values) / deviation


def _finite_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _json_safe(value: Any) -> Any:
    """Replace non-finite floats so reports serialize with ``allow_nan=False``."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _implementation_sha256() -> str:
    digest = hashlib.sha256()
    paths = [
        Path(module.__file__ or "")
        for module in (models, research, risk, features, regimes, strategies, signal_backtest, validation)
    ]
    paths.append(Path(__file__))
    statistics_module = Path(__file__).with_name("overfitting.py")
    if statistics_module.exists():
        paths.append(statistics_module)
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
