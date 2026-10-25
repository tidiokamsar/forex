"""Versioned, chronological research evaluation for offline simulation.

Candidate selection uses explicit validation windows, test windows remain
disjoint, and the latest block is reserved without holdout metrics. Reports
are exploratory and are not a model-promotion or trading authorization.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from . import attempts, holdout_reuse, models, research, risk
from .models import BacktestConfig, BacktestResult, CostModel, MarketBar
from .research import (
    STRATEGY_NAME,
    STRATEGY_VERSION,
    _prepare_bars,
    _validate_config,
    run_sma_backtest,
)

PROTOCOL_VERSION = "1.0.0"
MAX_CANDIDATES = 100
SELECTION_METRIC = "validation_net_pnl_minus_max_drawdown_cash"
REFERENCE_NAME = f"{STRATEGY_NAME}@{STRATEGY_VERSION}"


@dataclass(frozen=True, slots=True)
class WalkForwardProtocol:
    fold_count: int = 3
    minimum_training_bars: int = 60
    validation_bars: int = 20
    holdout_bars: int = 20
    minimum_candidate_trades: int = 1
    cost_stress_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0)
    version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        for name in (
            "fold_count",
            "minimum_training_bars",
            "validation_bars",
            "holdout_bars",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            not isinstance(self.minimum_candidate_trades, int)
            or isinstance(self.minimum_candidate_trades, bool)
            or self.minimum_candidate_trades < 0
        ):
            raise ValueError("minimum_candidate_trades must be a non-negative integer")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("protocol version must be a non-empty string")
        multipliers = tuple(self.cost_stress_multipliers)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 1.0
            for value in multipliers
        ):
            raise ValueError("cost stress multipliers must be finite numbers of at least 1.0")
        if not multipliers or not math.isclose(multipliers[0], 1.0):
            raise ValueError("cost_stress_multipliers must start with 1.0")
        if any(left >= right for left, right in pairwise(multipliers)):
            raise ValueError("cost stress multipliers must be strictly increasing")
        object.__setattr__(self, "cost_stress_multipliers", multipliers)


@dataclass(frozen=True, slots=True)
class ResultMetrics:
    start: datetime
    end: datetime
    observations: int
    trade_count: int
    gross_pnl: float
    total_costs: float
    net_pnl: float
    net_return_pct: float
    max_drawdown_pct: float
    win_rate: float | None
    profit_factor: float | None


@dataclass(frozen=True, slots=True)
class CandidateTrial:
    fold: int
    candidate_id: str
    config: dict
    status: str
    selection_score: float
    validation_net_pnl: float
    validation_max_drawdown_pct: float
    validation_trade_count: int
    eligible: bool
    selected: bool


@dataclass(frozen=True, slots=True)
class FinalCandidateSelection:
    candidate_id: str
    config: dict
    mean_validation_selection_score: float
    validation_net_pnl: float
    validation_trade_count: int
    eligible: bool
    selected: bool


@dataclass(frozen=True, slots=True)
class CostSensitivity:
    cost_multiplier: float
    selected_candidate: ResultMetrics | None
    simple_reference: ResultMetrics


@dataclass(frozen=True, slots=True)
class FoldEvaluation:
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
    candidate_trials: tuple[CandidateTrial, ...]
    selected_candidate_id: str | None
    selected_candidate: ResultMetrics | None
    simple_reference: ResultMetrics
    no_position_baseline: ResultMetrics
    cost_sensitivity: tuple[CostSensitivity, ...]


@dataclass(frozen=True, slots=True)
class AggregateMetrics:
    windows: int
    windows_with_result: int
    abstained_windows: int
    observations: int
    trade_count: int
    gross_pnl: float
    total_costs: float
    net_pnl: float
    pooled_fixed_capital_net_return_pct: float
    worst_window_drawdown_pct: float
    mean_window_drawdown_pct: float


@dataclass(frozen=True, slots=True)
class StressAggregate:
    cost_multiplier: float
    selected_candidate: AggregateMetrics
    simple_reference: AggregateMetrics


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    protocol_version: str
    protocol_definition: dict
    plan_manifest: dict
    hypothesis: str
    plan_sha256: str
    dataset_sha256: str
    dataset_hash_kind: str
    dataset_identity_sha256: str
    implementation_sha256: str
    strategy: str
    strategy_version: str
    selection_metric: str
    candidate_count: int
    candidate_variant_evaluations: int
    candidate_configs: tuple[dict, ...]
    final_candidate_selection: tuple[FinalCandidateSelection, ...]
    cost_model: dict
    folds: tuple[FoldEvaluation, ...]
    selected_candidate_aggregate: AggregateMetrics
    simple_reference_aggregate: AggregateMetrics
    no_position_aggregate: AggregateMetrics
    cost_stress_aggregates: tuple[StressAggregate, ...]
    holdout_status: str
    holdout_observations: int
    holdout_start: datetime
    holdout_end: datetime
    evaluation_classification: str


@dataclass(frozen=True, slots=True)
class HoldoutEvaluation:
    protocol_id: str
    plan_sha256: str
    dataset_sha256: str
    dataset_identity_sha256: str
    status: str
    evaluated_at: datetime
    selected_candidate_id: str | None
    selected_config: dict | None
    candidate_trials: tuple[FinalCandidateSelection, ...]
    selected_candidate: ResultMetrics | None
    simple_reference: ResultMetrics
    no_position_baseline: ResultMetrics
    cost_sensitivity: tuple[CostSensitivity, ...]
    holdout_start: datetime
    holdout_end: datetime
    holdout_observations: int
    assumptions: tuple[str, ...]
    evaluation_classification: str


def evaluate_walk_forward(
    bars: Iterable[MarketBar],
    candidates: Iterable[BacktestConfig],
    *,
    hypothesis: str,
    protocol: WalkForwardProtocol = WalkForwardProtocol(),
    dataset_sha256: str | None = None,
    store=None,
    experiment_id: str | None = None,
) -> WalkForwardReport:
    """Select configurations on validation windows and score disjoint tests.

    The last ``holdout_bars`` rows are assigned to the final holdout boundary,
    but no function in this protocol computes metrics over them. A durable,
    once-only holdout lock remains a separate persistence capability. With
    ``store`` the plan is sealed before any window is scored and every variant
    of every fold (and of the run) is appended to the attempt registry.
    """

    recorder = attempts.WalkForwardAttempts.for_store(store, experiment_id=experiment_id)
    try:
        return _evaluate_walk_forward(
            bars, candidates, hypothesis=hypothesis, protocol=protocol, dataset_sha256=dataset_sha256,
            store=store, recorder=recorder,
        )
    except BaseException as exc:
        recorder.abort(exc)
        raise


def _evaluate_walk_forward(
    bars, candidates, *, hypothesis, protocol, dataset_sha256, store, recorder
) -> WalkForwardReport:
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("a falsifiable, non-empty hypothesis is required")
    history = _prepare_bars(bars)
    if not history:
        raise ValueError("walk-forward evaluation requires bars")
    candidate_configs = tuple(candidates)
    if not candidate_configs:
        raise ValueError("at least one candidate configuration is required")
    if len(candidate_configs) > MAX_CANDIDATES:
        raise ValueError(f"candidate count cannot exceed {MAX_CANDIDATES}")
    if any(not isinstance(candidate, BacktestConfig) for candidate in candidate_configs):
        raise TypeError("candidates must contain BacktestConfig values")
    for candidate in candidate_configs:
        _validate_config(candidate)

    config_payloads = tuple(_config_payload(item) for item in candidate_configs)
    candidate_ids = tuple(_sha256(_canonical_json(item)) for item in config_payloads)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidate configurations must be unique")
    common_cost_model = candidate_configs[0].cost_model
    initial_equity = candidate_configs[0].initial_equity
    if any(
        item.cost_model != common_cost_model or item.initial_equity != initial_equity
        for item in candidate_configs[1:]
    ):
        raise ValueError("candidates must share the same initial equity and cost model")
    if protocol.minimum_training_bars < max(
        31, *(item.slow_window + 1 for item in candidate_configs)
    ):
        raise ValueError("minimum_training_bars must cover the reference and candidate SMA warm-up")

    development_end = len(history) - protocol.holdout_bars
    testable_bars = (
        development_end
        - protocol.minimum_training_bars
        - protocol.fold_count * protocol.validation_bars
    )
    if testable_bars < protocol.fold_count:
        raise ValueError(
            "dataset must contain minimum_training_bars, validation_bars for every fold, "
            "at least one test bar per fold, and the reserved holdout"
        )

    fold_ranges = _fold_ranges(
        development_end,
        protocol.minimum_training_bars,
        protocol.validation_bars,
        protocol.fold_count,
    )
    resolved_dataset_hash, hash_kind = _resolve_dataset_hash(history, dataset_sha256)
    dataset_identity_hash = _dataset_identity_sha256(history)
    implementation_hash = _implementation_sha256()
    candidate_descriptors = tuple(
        {"candidate_id": candidate_ids[index], "config": config_payloads[index]}
        for index in range(len(candidate_configs))
    )
    reference_config = BacktestConfig(
        initial_equity=initial_equity,
        cost_model=common_cost_model,
    )
    boundary_payload = [
        {
            "fold": ordinal,
            "train_start": history[0].timestamp.isoformat(),
            "train_end": history[validation_start - 1].timestamp.isoformat(),
            "validation_start": history[validation_start].timestamp.isoformat(),
            "validation_end": history[validation_end - 1].timestamp.isoformat(),
            "test_start": history[test_start].timestamp.isoformat(),
            "test_end": history[test_end - 1].timestamp.isoformat(),
            "train_start_index": 0,
            "train_end_index_exclusive": validation_start,
            "validation_start_index": validation_start,
            "validation_end_index_exclusive": validation_end,
            "test_start_index": test_start,
            "test_end_index_exclusive": test_end,
        }
        for ordinal, (validation_start, validation_end, test_start, test_end)
        in enumerate(fold_ranges, start=1)
    ]
    plan = {
        "protocol": asdict(protocol),
        "protocol_use_policy": (
            "EXPLORATORY_ONLY; one sealed plan per canonical dataset per local store; "
            "confirmation requires a fresh independent dataset"
        ),
        "hypothesis": hypothesis.strip(),
        "dataset_sha256": resolved_dataset_hash,
        "dataset_hash_kind": hash_kind,
        "dataset_identity_sha256": dataset_identity_hash,
        "implementation_sha256": implementation_hash,
        "strategy": STRATEGY_NAME,
        "strategy_version": STRATEGY_VERSION,
        "selection_metric": SELECTION_METRIC,
        "final_holdout_selection": "aggregate_mean_validation_score_across_validation_windows",
        "candidates": candidate_descriptors,
        "baselines": {
            "flat": "NO_POSITION@1",
            "reference": {
                "name": REFERENCE_NAME,
                "config": _config_payload(reference_config),
            },
        },
        "cost_model": _cost_payload(common_cost_model),
        "folds": boundary_payload,
        "instrument": history[0].instrument,
        "holdout": {
            "content_sha256": _holdout_content_sha256(history, development_end),
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
            dataset_identity_sha256=dataset_identity_hash,
            protocol_version=protocol.version,
            plan_manifest=plan,
            holdout_returns=_holdout_returns(history, development_end),
        )
        recorder.begin(plan_hash, plan, candidate_descriptors)

    completed_folds: list[FoldEvaluation] = []
    for fold_number, (validation_start, validation_end, test_start, test_end) in enumerate(
        fold_ranges, start=1
    ):
        recorder.start_fold(fold_number)
        train_bars = history[:validation_start]
        validation_history = history[:validation_end]
        validation_start_time = history[validation_start].timestamp
        evaluation_bars = history[:test_end]
        test_start_time = history[test_start].timestamp
        test_bars_count = test_end - test_start
        trials: list[CandidateTrial] = []
        trial_results: list[tuple[str, BacktestConfig, BacktestResult, float, bool]] = []
        for candidate_id, config_payload, config in zip(
            candidate_ids, config_payloads, candidate_configs,
            strict=True,
        ):
            validation_result = run_sma_backtest(
                validation_history,
                config=config,
                evaluation_start_time=validation_start_time,
                provenance={"evaluation_classification": "VALIDATION_SELECTION_NOT_PROMOTABLE"},
            )
            score = _selection_score(validation_result)
            eligible = validation_result.trade_count >= protocol.minimum_candidate_trades
            trial_results.append((candidate_id, config, validation_result, score, eligible))
            trials.append(
                CandidateTrial(
                    fold=fold_number,
                    candidate_id=candidate_id,
                    config=config_payload,
                    status="COMPLETED",
                    selection_score=score,
                    validation_net_pnl=validation_result.net_pnl,
                    validation_max_drawdown_pct=validation_result.max_drawdown_pct,
                    validation_trade_count=validation_result.trade_count,
                    eligible=eligible,
                    selected=False,
                )
            )

        eligible_trials = [item for item in trial_results if item[4]]
        selected_tuple = (
            min(
                eligible_trials,
                key=lambda item: (-item[3], -item[2].net_pnl, item[0]),
            )
            if eligible_trials
            else None
        )
        selected_id = selected_tuple[0] if selected_tuple else None
        if selected_id is not None:
            trials = [
                CandidateTrial(
                    fold=trial.fold,
                    candidate_id=trial.candidate_id,
                    config=trial.config,
                    status=trial.status,
                    selection_score=trial.selection_score,
                    validation_net_pnl=trial.validation_net_pnl,
                    validation_max_drawdown_pct=trial.validation_max_drawdown_pct,
                    validation_trade_count=trial.validation_trade_count,
                    eligible=trial.eligible,
                    selected=(trial.candidate_id == selected_id),
                )
                for trial in trials
            ]
        recorder.finish_fold(
            fold_number, trials,
            {item[0]: [trade.net_pnl / initial_equity for trade in item[2].trades] for item in trial_results},
        )

        selected_config = selected_tuple[1] if selected_tuple else None
        selected_result = (
            run_sma_backtest(
                evaluation_bars,
                config=selected_config,
                evaluation_start_time=test_start_time,
                provenance={"evaluation_classification": "WALK_FORWARD_RESEARCH_NOT_PROMOTABLE"},
            )
            if selected_config is not None
            else None
        )
        reference_result = run_sma_backtest(
            evaluation_bars,
            config=reference_config,
            evaluation_start_time=test_start_time,
            provenance={"evaluation_classification": "WALK_FORWARD_REFERENCE_NOT_PROMOTABLE"},
        )

        sensitivity: list[CostSensitivity] = []
        for multiplier in protocol.cost_stress_multipliers:
            stressed_selected = (
                run_sma_backtest(
                    evaluation_bars,
                    config=_scale_costs(selected_config, multiplier),
                    evaluation_start_time=test_start_time,
                    provenance={"cost_stress_multiplier": multiplier},
                )
                if selected_config is not None
                else None
            )
            stressed_reference = run_sma_backtest(
                evaluation_bars,
                config=_scale_costs(reference_config, multiplier),
                evaluation_start_time=test_start_time,
                provenance={"cost_stress_multiplier": multiplier},
            )
            sensitivity.append(
                CostSensitivity(
                    cost_multiplier=multiplier,
                    selected_candidate=(
                        _result_metrics(stressed_selected) if stressed_selected else None
                    ),
                    simple_reference=_result_metrics(stressed_reference),
                )
            )

        completed_folds.append(
            FoldEvaluation(
                fold=fold_number,
                train_start=train_bars[0].timestamp,
                train_end=train_bars[-1].timestamp,
                train_observations=len(train_bars),
                validation_start=validation_start_time,
                validation_end=history[validation_end - 1].timestamp,
                validation_observations=validation_end - validation_start,
                test_start=test_start_time,
                test_end=history[test_end - 1].timestamp,
                test_observations=test_bars_count,
                candidate_trials=tuple(trials),
                selected_candidate_id=selected_id,
                selected_candidate=_result_metrics(selected_result) if selected_result else None,
                simple_reference=_result_metrics(reference_result),
                no_position_baseline=_flat_metrics(
                    test_start_time,
                    history[test_end - 1].timestamp,
                    test_bars_count,
                    initial_equity,
                ),
                cost_sensitivity=tuple(sensitivity),
            )
        )

    trials_by_candidate = {candidate_id: [] for candidate_id in candidate_ids}
    for fold in completed_folds:
        for trial in fold.candidate_trials:
            trials_by_candidate[trial.candidate_id].append(trial)
    aggregate_trials: list[FinalCandidateSelection] = []
    for candidate_id, config_payload in zip(candidate_ids, config_payloads, strict=True):
        candidate_trials = trials_by_candidate[candidate_id]
        aggregate_trades = sum(
            trial.validation_trade_count for trial in candidate_trials
        )
        aggregate_trials.append(
            FinalCandidateSelection(
                candidate_id=candidate_id,
                config=config_payload,
                mean_validation_selection_score=(
                    sum(trial.selection_score for trial in candidate_trials)
                    / len(candidate_trials)
                ),
                validation_net_pnl=sum(
                    trial.validation_net_pnl for trial in candidate_trials
                ),
                validation_trade_count=aggregate_trades,
                eligible=aggregate_trades >= protocol.minimum_candidate_trades,
                selected=False,
            )
        )
    eligible_aggregate = [trial for trial in aggregate_trials if trial.eligible]
    final_selected_id = (
        min(
            eligible_aggregate,
            key=lambda trial: (
                -trial.mean_validation_selection_score,
                -trial.validation_net_pnl,
                trial.candidate_id,
            ),
        ).candidate_id
        if eligible_aggregate
        else None
    )
    final_candidate_selection = tuple(
        FinalCandidateSelection(
            candidate_id=trial.candidate_id,
            config=trial.config,
            mean_validation_selection_score=trial.mean_validation_selection_score,
            validation_net_pnl=trial.validation_net_pnl,
            validation_trade_count=trial.validation_trade_count,
            eligible=trial.eligible,
            selected=(trial.candidate_id == final_selected_id),
        )
        for trial in aggregate_trials
    )
    recorder.finish_run(final_candidate_selection)

    denominator = initial_equity * len(completed_folds)
    selected_aggregate = _aggregate(
        [fold.selected_candidate for fold in completed_folds], denominator
    )
    reference_aggregate = _aggregate(
        [fold.simple_reference for fold in completed_folds], denominator
    )
    flat_aggregate = _aggregate(
        [fold.no_position_baseline for fold in completed_folds], denominator
    )
    stress_aggregates = tuple(
        StressAggregate(
            cost_multiplier=multiplier,
            selected_candidate=_aggregate(
                [
                    next(
                        scenario.selected_candidate
                        for scenario in fold.cost_sensitivity
                        if scenario.cost_multiplier == multiplier
                    )
                    for fold in completed_folds
                ],
                denominator,
            ),
            simple_reference=_aggregate(
                [
                    next(
                        scenario.simple_reference
                        for scenario in fold.cost_sensitivity
                        if scenario.cost_multiplier == multiplier
                    )
                    for fold in completed_folds
                ],
                denominator,
            ),
        )
        for multiplier in protocol.cost_stress_multipliers
    )

    return WalkForwardReport(
        protocol_version=protocol.version,
        protocol_definition=asdict(protocol),
        plan_manifest=plan,
        hypothesis=hypothesis.strip(),
        plan_sha256=plan_hash,
        dataset_sha256=resolved_dataset_hash,
        dataset_hash_kind=hash_kind,
        dataset_identity_sha256=dataset_identity_hash,
        implementation_sha256=implementation_hash,
        strategy=STRATEGY_NAME,
        strategy_version=STRATEGY_VERSION,
        selection_metric=SELECTION_METRIC,
        candidate_count=len(candidate_configs),
        candidate_variant_evaluations=len(candidate_configs) * len(completed_folds),
        candidate_configs=candidate_descriptors,
        final_candidate_selection=final_candidate_selection,
        cost_model=_cost_payload(common_cost_model),
        folds=tuple(completed_folds),
        selected_candidate_aggregate=selected_aggregate,
        simple_reference_aggregate=reference_aggregate,
        no_position_aggregate=flat_aggregate,
        cost_stress_aggregates=stress_aggregates,
        holdout_status="RESERVED_NOT_EVALUATED",
        holdout_observations=protocol.holdout_bars,
        holdout_start=history[development_end].timestamp,
        holdout_end=history[-1].timestamp,
        evaluation_classification="EXPLORATORY_WALK_FORWARD_NOT_PROMOTABLE",
    )


def evaluate_holdout_once(
    bars: Iterable[MarketBar],
    candidates: Iterable[BacktestConfig],
    *,
    walk_forward_report: WalkForwardReport,
    protocol: WalkForwardProtocol,
    store,
    dataset_sha256: str | None = None,
    experiment_id: str | None = None,
) -> HoldoutEvaluation | dict:
    """Evaluate the reserved final block once, with a durable pre-run lock.

    The database records STARTED before holdout scoring. Candidate selection is
    recomputed from validation windows without scoring the final holdout.
    Retries return a completed report or preserve a consumed failed attempt.
    """

    history = _prepare_bars(bars)
    candidate_configs = tuple(candidates)
    if not history or not candidate_configs:
        raise ValueError("holdout evaluation requires bars and candidate configurations")
    resolved_hash, _ = _resolve_dataset_hash(history, dataset_sha256)
    identity_hash = _dataset_identity_sha256(history)
    if resolved_hash != walk_forward_report.dataset_sha256:
        raise ValueError("dataset hash does not match the sealed walk-forward plan")
    if identity_hash != walk_forward_report.dataset_identity_sha256:
        raise ValueError("canonical market-bar identity does not match the sealed plan")
    if _sha256(_canonical_json(walk_forward_report.plan_manifest)) != walk_forward_report.plan_sha256:
        raise ValueError("walk-forward plan manifest does not match its hash")
    if _implementation_sha256() != walk_forward_report.implementation_sha256:
        raise ValueError("research implementation changed after the plan was sealed")
    if asdict(protocol) != walk_forward_report.protocol_definition:
        raise ValueError("protocol does not match the sealed walk-forward plan")
    expected_configs = tuple(
        {"candidate_id": _sha256(_canonical_json(_config_payload(item))),
         "config": _config_payload(item)}
        for item in candidate_configs
    )
    if expected_configs != walk_forward_report.candidate_configs:
        raise ValueError("candidate configurations do not match the sealed plan")

    holdout_size = protocol.holdout_bars
    if len(history) <= holdout_size:
        raise ValueError("dataset is too short for the reserved holdout")
    holdout_start_index = len(history) - holdout_size
    holdout_start = history[holdout_start_index].timestamp
    holdout_end = history[-1].timestamp
    if (
        holdout_start != walk_forward_report.holdout_start
        or holdout_end != walk_forward_report.holdout_end
        or holdout_size != walk_forward_report.holdout_observations
    ):
        raise ValueError("holdout boundaries do not match the sealed plan")

    # Recompute the pre-holdout partitions from the bars so a caller cannot
    # alter validation selection fields in an otherwise valid report object.
    verified_report = evaluate_walk_forward(
        history,
        candidate_configs,
        hypothesis=walk_forward_report.hypothesis,
        protocol=protocol,
        dataset_sha256=dataset_sha256,
    )
    if verified_report.plan_sha256 != walk_forward_report.plan_sha256:
        raise ValueError("recomputed protocol does not match the sealed plan")

    store.seal_research_protocol(
        protocol_id=walk_forward_report.plan_sha256,
        dataset_identity_sha256=identity_hash,
        protocol_version=walk_forward_report.protocol_version,
        plan_manifest=walk_forward_report.plan_manifest,
        holdout_returns=_holdout_returns(history, holdout_start_index),
    )
    attempt = store.begin_holdout_evaluation(
        protocol_id=walk_forward_report.plan_sha256,
        dataset_identity_sha256=identity_hash,
    )
    if attempt["status"] == "COMPLETED":
        return attempt["report"]

    selected_trial = next((trial for trial in verified_report.final_candidate_selection if trial.selected), None)
    holdout_attempt = attempts.HoldoutAttempt.start(
        store, protocol_id=walk_forward_report.plan_sha256, plan=walk_forward_report.plan_manifest,
        candidate_id=selected_trial.candidate_id if selected_trial else None, experiment_id=experiment_id,
    )
    try:
        trials = verified_report.final_candidate_selection
        selected = next((trial for trial in trials if trial.selected), None)
        selected_id = selected.candidate_id if selected else None
        config_by_id = {
            _sha256(_canonical_json(_config_payload(config))): config
            for config in candidate_configs
        }
        selected_config = config_by_id.get(selected_id) if selected_id else None

        initial_equity = candidate_configs[0].initial_equity
        common_cost_model = candidate_configs[0].cost_model
        reference_config = BacktestConfig(
            initial_equity=initial_equity,
            cost_model=common_cost_model,
        )
        holdout_bars = history[:]
        selected_result = (
            run_sma_backtest(
                holdout_bars,
                config=selected_config,
                evaluation_start_time=holdout_start,
                provenance={"evaluation_classification": "ONE_TIME_HOLDOUT_NOT_PROMOTABLE"},
            )
            if selected_config is not None
            else None
        )
        reference_result = run_sma_backtest(
            holdout_bars,
            config=reference_config,
            evaluation_start_time=holdout_start,
            provenance={"evaluation_classification": "ONE_TIME_HOLDOUT_REFERENCE"},
        )
        sensitivity: list[CostSensitivity] = []
        for multiplier in protocol.cost_stress_multipliers:
            stressed_selected = (
                run_sma_backtest(
                    holdout_bars,
                    config=_scale_costs(selected_config, multiplier),
                    evaluation_start_time=holdout_start,
                    provenance={"cost_stress_multiplier": multiplier},
                )
                if selected_config is not None
                else None
            )
            stressed_reference = run_sma_backtest(
                holdout_bars,
                config=_scale_costs(reference_config, multiplier),
                evaluation_start_time=holdout_start,
                provenance={"cost_stress_multiplier": multiplier},
            )
            sensitivity.append(
                CostSensitivity(
                    cost_multiplier=multiplier,
                    selected_candidate=(
                        _result_metrics(stressed_selected) if stressed_selected else None
                    ),
                    simple_reference=_result_metrics(stressed_reference),
                )
            )

        evaluation = HoldoutEvaluation(
            protocol_id=walk_forward_report.plan_sha256,
            plan_sha256=walk_forward_report.plan_sha256,
            dataset_sha256=resolved_hash,
            dataset_identity_sha256=identity_hash,
            status="COMPLETED",
            evaluated_at=datetime.now(UTC),
            selected_candidate_id=selected_id,
            selected_config=_config_payload(selected_config) if selected_config else None,
            candidate_trials=tuple(trials),
            selected_candidate=_result_metrics(selected_result) if selected_result else None,
            simple_reference=_result_metrics(reference_result),
            no_position_baseline=_flat_metrics(
                holdout_start,
                holdout_end,
                holdout_size,
                initial_equity,
            ),
            cost_sensitivity=tuple(sensitivity),
            holdout_start=holdout_start,
            holdout_end=holdout_end,
            holdout_observations=holdout_size,
            assumptions=(
                "The final holdout lock is scoped to this configured SQLite store and canonical market-bar identity; "
                "the local database is mutable and can be replaced.",
                "The final candidate was selected from aggregate validation-window scores before the holdout was scored.",
                "The simple reference and cost stress scenarios were declared by the sealed protocol.",
                "Metrics are historical simulation estimates, not a guarantee or promotion decision.",
            ),
            evaluation_classification="ONE_TIME_HOLDOUT_NOT_PROMOTABLE",
        )
        store.complete_holdout_evaluation(
            evaluation_id=attempt["evaluation_id"],
            report=asdict(evaluation),
        )
        holdout_attempt.finish(
            {"selected_candidate": asdict(evaluation.selected_candidate) if evaluation.selected_candidate else None},
            attempts.sharpe_like([trade.net_pnl / initial_equity for trade in selected_result.trades])
            if selected_result
            else None,
        )
        return evaluation
    except Exception as exc:
        store.fail_holdout_evaluation(
            evaluation_id=attempt["evaluation_id"],
            error_type=type(exc).__name__,
        )
        holdout_attempt.fail(exc)
        raise


def _fold_ranges(
    development_end: int,
    minimum_training_bars: int,
    validation_bars: int,
    fold_count: int,
) -> tuple[tuple[int, int, int, int], ...]:
    testable = development_end - minimum_training_bars - fold_count * validation_bars
    if testable < fold_count:
        raise ValueError("each walk-forward fold requires validation bars and at least one test bar")
    base_size, extra = divmod(testable, fold_count)
    ranges: list[tuple[int, int, int, int]] = []
    cursor = minimum_training_bars
    for ordinal in range(fold_count):
        validation_start = cursor
        validation_end = validation_start + validation_bars
        test_start = validation_end
        size = base_size + int(ordinal < extra)
        test_end = test_start + size
        ranges.append((validation_start, validation_end, test_start, test_end))
        cursor = test_end
    return tuple(ranges)


def _selection_score(result: BacktestResult) -> float:
    drawdown_cash = result.initial_equity * result.max_drawdown_pct / 100.0
    return result.net_pnl - drawdown_cash


def _result_metrics(result: BacktestResult) -> ResultMetrics:
    if result.start is None or result.end is None:
        raise ValueError("an evaluated window must have start and end timestamps")
    return ResultMetrics(
        start=result.start,
        end=result.end,
        observations=result.observations,
        trade_count=result.trade_count,
        gross_pnl=result.gross_pnl,
        total_costs=result.total_costs,
        net_pnl=result.net_pnl,
        net_return_pct=result.net_pnl / result.initial_equity * 100.0,
        max_drawdown_pct=result.max_drawdown_pct,
        win_rate=result.win_rate,
        profit_factor=result.profit_factor,
    )


def _flat_metrics(
    start: datetime, end: datetime, observations: int, initial_equity: float
) -> ResultMetrics:
    return ResultMetrics(
        start=start,
        end=end,
        observations=observations,
        trade_count=0,
        gross_pnl=0.0,
        total_costs=0.0,
        net_pnl=0.0,
        net_return_pct=0.0,
        max_drawdown_pct=0.0,
        win_rate=None,
        profit_factor=None,
    )


def _aggregate(
    metrics: Iterable[ResultMetrics | None], denominator: float
) -> AggregateMetrics:
    items = tuple(metrics)
    present = tuple(item for item in items if item is not None)
    selected = sum(item is not None for item in items)
    return AggregateMetrics(
        windows=len(items),
        windows_with_result=selected,
        abstained_windows=len(items) - selected,
        observations=sum(item.observations for item in present),
        trade_count=sum(item.trade_count for item in present),
        gross_pnl=sum(item.gross_pnl for item in present),
        total_costs=sum(item.total_costs for item in present),
        net_pnl=sum(item.net_pnl for item in present),
        pooled_fixed_capital_net_return_pct=(
            sum(item.net_pnl for item in present) / denominator * 100.0
            if denominator > 0
            else 0.0
        ),
        worst_window_drawdown_pct=max(
            (item.max_drawdown_pct for item in present), default=0.0
        ),
        mean_window_drawdown_pct=(
            sum(item.max_drawdown_pct for item in present) / len(present)
            if present
            else 0.0
        ),
    )


def _scale_costs(config: BacktestConfig, multiplier: float) -> BacktestConfig:
    return BacktestConfig(
        initial_equity=config.initial_equity,
        fast_window=config.fast_window,
        slow_window=config.slow_window,
        stop_lookback=config.stop_lookback,
        stop_range_multiple=config.stop_range_multiple,
        cost_model=CostModel(
            pip_size=config.cost_model.pip_size,
            spread_pips=config.cost_model.spread_pips * multiplier,
            slippage_pips_per_side=config.cost_model.slippage_pips_per_side * multiplier,
            commission_per_unit_per_side=(
                config.cost_model.commission_per_unit_per_side * multiplier
            ),
        ),
    )


def _config_payload(config: BacktestConfig) -> dict:
    return {
        "initial_equity": config.initial_equity,
        "fast_window": config.fast_window,
        "slow_window": config.slow_window,
        "stop_lookback": config.stop_lookback,
        "stop_range_multiple": config.stop_range_multiple,
        "cost_model": _cost_payload(config.cost_model),
    }


def _cost_payload(cost_model: CostModel) -> dict:
    return {
        "pip_size": cost_model.pip_size,
        "spread_pips": cost_model.spread_pips,
        "slippage_pips_per_side": cost_model.slippage_pips_per_side,
        "commission_per_unit_per_side": cost_model.commission_per_unit_per_side,
    }


def _resolve_dataset_hash(
    history: tuple[MarketBar, ...], provided: str | None
) -> tuple[str, str]:
    if provided is not None:
        if (
            not isinstance(provided, str)
            or len(provided) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in provided)
        ):
            raise ValueError("dataset_sha256 must be a 64-character hexadecimal digest")
        return provided.lower(), "source_bytes_sha256"
    return _dataset_identity_sha256(history), "canonical_market_bars_sha256"


def _dataset_identity_sha256(history: tuple[MarketBar, ...]) -> str:
    canonical = [
        {
            "instrument": bar.instrument.strip().upper(),
            "timestamp": bar.timestamp.astimezone(UTC).isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "bid": bar.bid,
            "ask": bar.ask,
        }
        for bar in history
    ]
    return _sha256(_canonical_json(canonical))


def _implementation_sha256() -> str:
    digest = hashlib.sha256()
    for module in (models, research, risk):
        module_path = Path(module.__file__ or "")
        digest.update(module_path.name.encode("utf-8"))
        digest.update(module_path.read_bytes())
    current_path = Path(__file__)
    digest.update(current_path.name.encode("utf-8"))
    digest.update(current_path.read_bytes())
    return digest.hexdigest()


def _holdout_content_sha256(history: tuple[MarketBar, ...], start: int) -> str:
    """Fingerprint of the holdout's price path, independent of labels, dates and price scale.

    It hashes the close-to-close log returns (rounded to 12 decimals) from the
    bar before the holdout to the end, so the same prices relabelled, moved in
    time or rescaled give the same fingerprint.
    """

    return holdout_reuse.content_sha256(_holdout_returns(history, start))


def _holdout_returns(history: tuple[MarketBar, ...], start: int) -> list[float]:
    return holdout_reuse.holdout_returns([bar.close for bar in history[max(0, start - 1):]])


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
