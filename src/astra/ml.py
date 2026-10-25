"""Baseline machine-learning engine in pure Python for offline research.

Models predict whether the forward log return over ``horizon`` bars is
positive. Outputs are probability estimates with measured calibration, not
certainties and not evidence of profitability. Evaluation uses a purged,
embargoed, expanding walk-forward split and per-fold train-only scaling.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from .features import (
    FEATURE_SET_VERSION,
    SCALE_FREE_COLUMNS,
    FeatureSpec,
    TrainOnlyScaler,
    compute_feature_matrix,
)
from .models import MarketBar
from .research import _prepare_bars

ML_ENGINE_VERSION = "1.0.0"
LOGISTIC_MODEL_NAME = "l2_logistic_regression"
BASE_RATE_MODEL_NAME = "base_rate_classifier"

KNOWN_LIMITATIONS = (
    "Probabilities are model estimates on historical/synthetic data; they are not certainties or profit forecasts.",
    "Labels ignore costs, stops, and execution; a calibrated direction estimate can still lose money after costs.",
    "A zero forward return is labelled 0 (not up).",
    "Linear model on a small hand-built feature set; no hyper-parameter search is performed or implied.",
    "Fold metrics come from a single chronological path; regime changes can invalidate them.",
)


def forward_labels(bars: Iterable[MarketBar], horizon: int) -> list[int | None]:
    """Return 1 if ``ln(close_{i+h} / close_i) > 0`` else 0; ``None`` without h future bars.

    The label of row ``i`` depends on bars ``i+1 .. i+horizon`` only.
    """

    _require_int(horizon, "horizon", 1)
    history = _prepare_bars(bars)
    labels: list[int | None] = [None] * len(history)
    for index in range(len(history) - horizon):
        labels[index] = int(history[index + horizon].close > history[index].close)
    return labels


class BaseRateClassifier:
    """Predicts the training base rate of the positive class for every row."""

    __slots__ = ("base_rate", "columns")

    def __init__(self) -> None:
        self.base_rate: float | None = None
        self.columns: tuple[str, ...] | None = None

    def fit(self, labels: Sequence[int], columns: Sequence[str] = ()) -> BaseRateClassifier:
        values = _validated_labels(labels)
        self.base_rate = sum(values) / len(values)
        self.columns = tuple(columns)
        return self

    def predict_proba(self, rows: Sequence[Sequence[float]]) -> list[float]:
        if self.base_rate is None:
            raise RuntimeError("model must be fitted before prediction")
        return [self.base_rate for _ in rows]


@dataclass(frozen=True, slots=True)
class LogisticParams:
    l2: float = 0.01
    learning_rate: float = 0.1
    iterations: int = 500

    def __post_init__(self) -> None:
        if not _finite(self.l2) or self.l2 < 0:
            raise ValueError("l2 must be a finite non-negative number")
        if not _finite(self.learning_rate) or self.learning_rate <= 0:
            raise ValueError("learning_rate must be a finite positive number")
        _require_int(self.iterations, "iterations", 1)


class LogisticRegression:
    """L2-regularized logistic regression fitted by deterministic batch gradient descent.

    Weights start at zero; the bias is not regularized. ``predict_proba``
    refuses rows described by a different column list than the training one.
    """

    __slots__ = ("bias", "columns", "params", "training_rows", "weights")

    def __init__(self, params: LogisticParams | None = None) -> None:
        params = LogisticParams() if params is None else params
        if not isinstance(params, LogisticParams):
            raise TypeError("params must be LogisticParams")
        self.params = params
        self.columns: tuple[str, ...] | None = None
        self.weights: tuple[float, ...] | None = None
        self.bias = 0.0
        self.training_rows = 0

    def fit(
        self,
        rows: Sequence[Sequence[float]],
        labels: Sequence[int],
        columns: Sequence[str],
    ) -> LogisticRegression:
        names = tuple(columns)
        matrix = _validated_matrix(rows, len(names))
        targets = _validated_labels(labels)
        if not names or len(matrix) != len(targets):
            raise ValueError("rows, labels, and columns must be non-empty and aligned")
        count = len(matrix)
        weights = [0.0] * len(names)
        bias = 0.0
        rate = self.params.learning_rate
        for _ in range(self.params.iterations):
            gradient = [0.0] * len(names)
            bias_gradient = 0.0
            for row, target in zip(matrix, targets, strict=True):
                error = _sigmoid(bias + _dot(weights, row)) - target
                bias_gradient += error
                for position, value in enumerate(row):
                    gradient[position] += error * value
            for position in range(len(weights)):
                weights[position] -= rate * (gradient[position] / count + self.params.l2 * weights[position])
            bias -= rate * bias_gradient / count
        self.columns = names
        self.weights = tuple(weights)
        self.bias = bias
        self.training_rows = count
        return self

    def predict_proba(
        self, rows: Sequence[Sequence[float]], columns: Sequence[str]
    ) -> list[float]:
        if self.weights is None or self.columns is None:
            raise RuntimeError("model must be fitted before prediction")
        if tuple(columns) != self.columns:
            raise ValueError("feature columns differ from the columns the model was trained on")
        matrix = _validated_matrix(rows, len(self.columns))
        return [_sigmoid(self.bias + _dot(self.weights, row)) for row in matrix]


@dataclass(frozen=True, slots=True)
class PurgedWalkForwardConfig:
    """Expanding-window folds over usable rows with purge and embargo.

    A training row ``j`` is kept for a validation block starting at bar index
    ``s`` only if ``j + horizon + embargo_bars < s``: its label window
    ``j+1 .. j+horizon`` ends before the block, with ``embargo_bars`` of gap.
    """

    horizon: int = 4
    fold_count: int = 3
    validation_rows: int = 40
    minimum_training_rows: int = 80
    embargo_bars: int = 2
    calibration_bins: int = 5

    def __post_init__(self) -> None:
        _require_int(self.horizon, "horizon", 1)
        _require_int(self.fold_count, "fold_count", 1)
        _require_int(self.validation_rows, "validation_rows", 2)
        _require_int(self.minimum_training_rows, "minimum_training_rows", 2)
        _require_int(self.embargo_bars, "embargo_bars", 0)
        _require_int(self.calibration_bins, "calibration_bins", 1)


@dataclass(frozen=True, slots=True)
class FoldSplit:
    fold: int
    training_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float | None
    observed_frequency: float | None


@dataclass(frozen=True, slots=True)
class FoldMetrics:
    fold: int
    training_rows: int
    validation_rows: int
    purged_rows: int
    training_start: str
    training_end: str
    validation_start: str
    validation_end: str
    training_base_rate: float
    accuracy: float
    brier: float
    log_loss: float
    base_rate_accuracy: float
    base_rate_brier: float
    base_rate_log_loss: float
    edge_vs_baseline: float
    calibration: tuple[CalibrationBin, ...]
    edge_definition: str = "base_rate_brier - model_brier on validation rows (positive means lower Brier than baseline)"


@dataclass(frozen=True, slots=True)
class ModelManifest:
    model_name: str
    model_version: str
    feature_set_version: str
    feature_spec_sha256: str
    feature_columns: tuple[str, ...]
    dataset_identity: dict[str, Any]
    params: dict[str, Any]
    code_sha256: str
    python_version: str
    train_periods: tuple[tuple[str, str], ...]
    validation_periods: tuple[tuple[str, str], ...]
    fold_metrics: tuple[FoldMetrics, ...]
    created_at: str
    known_limitations: tuple[str, ...] = KNOWN_LIMITATIONS
    probability_statement: str = "Outputs are probability estimates, not certainties; no profitability is claimed."

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        json.dumps(payload, allow_nan=False)
        return payload


@dataclass(frozen=True, slots=True)
class MLRunResult:
    """Manifest plus the most recent fold's fitted scaler and model."""

    manifest: ModelManifest
    model: LogisticRegression
    scaler: TrainOnlyScaler
    splits: tuple[FoldSplit, ...] = field(default_factory=tuple)


def purged_walk_forward_splits(
    usable_indices: Sequence[int], config: PurgedWalkForwardConfig
) -> tuple[FoldSplit, ...]:
    """Split sorted bar indices into expanding folds with purge and embargo."""

    indices = tuple(usable_indices)
    if any(b <= a for a, b in pairwise(indices)):
        raise ValueError("usable_indices must be strictly increasing")
    needed = config.fold_count * config.validation_rows
    if len(indices) < needed + config.minimum_training_rows:
        raise ValueError("not enough usable rows for the requested folds")
    first_block = len(indices) - needed
    splits: list[FoldSplit] = []
    for fold in range(config.fold_count):
        block = indices[first_block + fold * config.validation_rows : first_block + (fold + 1) * config.validation_rows]
        block_start = block[0]
        candidates = [index for index in indices if index < block_start]
        kept = tuple(i for i in candidates if i + config.horizon + config.embargo_bars < block_start)
        purged = tuple(i for i in candidates if i + config.horizon + config.embargo_bars >= block_start)
        if len(kept) < config.minimum_training_rows:
            raise ValueError("purge/embargo leaves too few training rows")
        splits.append(FoldSplit(fold, kept, tuple(block), purged))
    return tuple(splits)


def run_purged_walk_forward(
    bars: Iterable[MarketBar],
    dataset_identity: dict[str, Any],
    *,
    feature_spec: FeatureSpec | None = None,
    columns: Sequence[str] = SCALE_FREE_COLUMNS,
    config: PurgedWalkForwardConfig | None = None,
    params: LogisticParams | None = None,
    created_at: datetime | None = None,
) -> MLRunResult:
    """Fit the base-rate baseline and logistic model per fold and report metrics."""

    feature_spec = FeatureSpec() if feature_spec is None else feature_spec
    config = PurgedWalkForwardConfig() if config is None else config
    params = LogisticParams() if params is None else params
    if not isinstance(dataset_identity, dict) or not dataset_identity:
        raise ValueError("dataset_identity must be a non-empty dict")
    identity = _jsonable(dataset_identity)
    json.dumps(identity, allow_nan=False)
    names = tuple(columns)
    if not names:
        raise ValueError("columns must not be empty")
    if created_at is None:
        created_at = datetime.now(UTC)
    elif created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")

    history = _prepare_bars(bars)
    matrix = compute_feature_matrix(history, feature_spec)
    feature_rows = matrix.select(names)
    labels = forward_labels(history, config.horizon)
    first = matrix.first_complete_index(names)
    usable = [
        index
        for index in range(first, len(history))
        if labels[index] is not None and all(value is not None for value in feature_rows[index])
    ]
    splits = purged_walk_forward_splits(usable, config)

    fold_metrics: list[FoldMetrics] = []
    model: LogisticRegression | None = None
    scaler: TrainOnlyScaler | None = None
    for split in splits:
        train_x = [feature_rows[i] for i in split.training_indices]
        train_y = [labels[i] for i in split.training_indices]
        valid_x = [feature_rows[i] for i in split.validation_indices]
        valid_y = [labels[i] for i in split.validation_indices]
        scaler = TrainOnlyScaler().fit(train_x, names)
        model = LogisticRegression(params).fit(scaler.transform(train_x, names), train_y, names)  # type: ignore[arg-type]
        baseline = BaseRateClassifier().fit(train_y, names)  # type: ignore[arg-type]
        predicted = model.predict_proba(scaler.transform(valid_x, names), names)
        baseline_predicted = baseline.predict_proba(valid_x)  # type: ignore[arg-type]
        observed = [int(value) for value in valid_y]  # type: ignore[arg-type]
        base_rate = baseline.base_rate or 0.0
        model_brier = _brier(predicted, observed)
        base_brier = _brier(baseline_predicted, observed)
        fold_metrics.append(
            FoldMetrics(
                fold=split.fold,
                training_rows=len(split.training_indices),
                validation_rows=len(split.validation_indices),
                purged_rows=len(split.purged_indices),
                training_start=history[split.training_indices[0]].timestamp.isoformat(),
                training_end=history[split.training_indices[-1]].timestamp.isoformat(),
                validation_start=history[split.validation_indices[0]].timestamp.isoformat(),
                validation_end=history[split.validation_indices[-1]].timestamp.isoformat(),
                training_base_rate=base_rate,
                accuracy=_accuracy(predicted, observed),
                brier=model_brier,
                log_loss=_log_loss(predicted, observed),
                base_rate_accuracy=_accuracy(baseline_predicted, observed),
                base_rate_brier=base_brier,
                base_rate_log_loss=_log_loss(baseline_predicted, observed),
                edge_vs_baseline=base_brier - model_brier,
                calibration=_calibration(predicted, observed, config.calibration_bins),
            )
        )

    assert model is not None and scaler is not None
    manifest = ModelManifest(
        model_name=LOGISTIC_MODEL_NAME,
        model_version=ML_ENGINE_VERSION,
        feature_set_version=FEATURE_SET_VERSION,
        feature_spec_sha256=matrix.spec_sha256,
        feature_columns=names,
        dataset_identity=identity,
        params={
            "logistic": asdict(params),
            "walk_forward": asdict(config),
            "feature_spec": asdict(feature_spec),
            "baseline": BASE_RATE_MODEL_NAME,
            "label": "forward log return over horizon > 0",
        },
        code_sha256=code_sha256(),
        python_version=platform.python_version(),
        train_periods=tuple((m.training_start, m.training_end) for m in fold_metrics),
        validation_periods=tuple((m.validation_start, m.validation_end) for m in fold_metrics),
        fold_metrics=tuple(fold_metrics),
        created_at=created_at.astimezone(UTC).isoformat(),
    )
    return MLRunResult(manifest=manifest, model=model, scaler=scaler, splits=splits)


def code_sha256() -> str:
    """Return the SHA-256 of this module's source bytes."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _calibration(
    predicted: Sequence[float], observed: Sequence[int], bins: int
) -> tuple[CalibrationBin, ...]:
    output = []
    for position in range(bins):
        lower = position / bins
        upper = (position + 1) / bins
        members = [
            (p, y)
            for p, y in zip(predicted, observed, strict=True)
            if lower <= p < upper or (position == bins - 1 and p == 1.0)
        ]
        output.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=len(members),
                mean_predicted=(sum(p for p, _ in members) / len(members)) if members else None,
                observed_frequency=(sum(y for _, y in members) / len(members)) if members else None,
            )
        )
    return tuple(output)


def _accuracy(predicted: Sequence[float], observed: Sequence[int]) -> float:
    return sum(int(p >= 0.5) == y for p, y in zip(predicted, observed, strict=True)) / len(observed)


def _brier(predicted: Sequence[float], observed: Sequence[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(predicted, observed, strict=True)) / len(observed)


def _log_loss(predicted: Sequence[float], observed: Sequence[int]) -> float:
    epsilon = 1e-15
    total = 0.0
    for p, y in zip(predicted, observed, strict=True):
        clipped = min(max(p, epsilon), 1.0 - epsilon)
        total -= y * math.log(clipped) + (1 - y) * math.log(1.0 - clipped)
    return total / len(observed)


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _dot(weights: Sequence[float], row: Sequence[float]) -> float:
    return sum(w * x for w, x in zip(weights, row, strict=True))


def _validated_matrix(rows: Sequence[Sequence[float]], width: int) -> list[tuple[float, ...]]:
    matrix = []
    for row in rows:
        values = tuple(row)
        if len(values) != width or any(not _finite(value) for value in values):
            raise ValueError("rows must be finite numbers matching the column count")
        matrix.append(tuple(float(value) for value in values))
    if not matrix:
        raise ValueError("at least one row is required")
    return matrix


def _validated_labels(labels: Sequence[int]) -> list[int]:
    values = list(labels)
    if not values or any(type(value) is not int or value not in (0, 1) for value in values):
        raise ValueError("labels must be a non-empty sequence of 0/1 integers")
    return values


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("manifest values must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON-serializable")


def _require_int(value: object, name: str, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
