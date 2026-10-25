import hashlib
import json
import platform
import random
import unittest
from datetime import UTC, datetime
from pathlib import Path

from astra import ml
from astra.features import SCALE_FREE_COLUMNS, compute_feature_matrix
from astra.ml import (
    BaseRateClassifier,
    LogisticParams,
    LogisticRegression,
    PurgedWalkForwardConfig,
    forward_labels,
    purged_walk_forward_splits,
    run_purged_walk_forward,
)
from test_features import make_synthetic_bars, mutate_after

CREATED = datetime(2026, 10, 25, tzinfo=UTC)
IDENTITY = {"dataset": "synthetic_generator", "seed": 7, "classification": "SYNTHETIC"}
FAST = LogisticParams(iterations=150)


def _run(bars=None, config=None, **kwargs):
    return run_purged_walk_forward(
        make_synthetic_bars() if bars is None else bars,
        IDENTITY,
        config=config or PurgedWalkForwardConfig(horizon=4, embargo_bars=3),
        params=kwargs.pop("params", FAST),
        created_at=CREATED,
        **kwargs,
    )


class LabelTests(unittest.TestCase):
    def test_label_uses_only_bars_after_row_within_horizon(self):
        bars = make_synthetic_bars(100)
        labels = forward_labels(bars, 3)
        self.assertEqual(labels[-3:], [None, None, None])
        self.assertEqual(labels[10], int(bars[13].close > bars[10].close))
        for index in (5, 40, 90):
            mutated = forward_labels(mutate_after(bars, index + 3), 3)
            self.assertEqual(mutated[: index + 1], labels[: index + 1])


class PurgeEmbargoTests(unittest.TestCase):
    def test_no_training_label_window_overlaps_validation_rows(self):
        config = PurgedWalkForwardConfig(horizon=5, embargo_bars=4, fold_count=3, validation_rows=30, minimum_training_rows=50)
        splits = purged_walk_forward_splits(list(range(10, 300)), config)
        self.assertEqual(len(splits), 3)
        for split in splits:
            start, end = split.validation_indices[0], split.validation_indices[-1]
            validation = set(split.validation_indices)
            self.assertTrue(split.purged_indices)
            for row in split.training_indices:
                label_window = set(range(row + 1, row + config.horizon + 1))
                self.assertFalse(label_window & validation)
                self.assertLess(row + config.horizon + config.embargo_bars, start)
                self.assertLess(row, start)
            for row in split.purged_indices:
                self.assertGreaterEqual(row + config.horizon + config.embargo_bars, start)
            self.assertEqual(end - start + 1, config.validation_rows)
        blocks = [split.validation_indices for split in splits]
        self.assertEqual(sorted(set().union(*blocks)), [i for b in blocks for i in b])

    def test_run_respects_purge_in_reported_folds(self):
        config = PurgedWalkForwardConfig(horizon=4, embargo_bars=3)
        result = _run(config=config)
        for split, metrics in zip(result.splits, result.manifest.fold_metrics, strict=True):
            self.assertLess(split.training_indices[-1] + 4 + 3, split.validation_indices[0])
            self.assertEqual(metrics.purged_rows, len(split.purged_indices))
            self.assertEqual(metrics.training_rows, len(split.training_indices))

    def test_insufficient_rows_fail_closed(self):
        with self.assertRaises(ValueError):
            purged_walk_forward_splits(list(range(50)), PurgedWalkForwardConfig())
        with self.assertRaises(ValueError):
            purged_walk_forward_splits([3, 2, 1], PurgedWalkForwardConfig())


class ScalerLeakageTests(unittest.TestCase):
    def test_last_fold_scaler_fitted_on_training_rows_only(self):
        bars = make_synthetic_bars()
        result = _run(bars)
        last = result.splits[-1]
        matrix = compute_feature_matrix(bars)
        training = matrix.select(SCALE_FREE_COLUMNS, last.training_indices)
        self.assertEqual(result.scaler.fitted_rows, len(last.training_indices))
        for position in range(len(SCALE_FREE_COLUMNS)):
            mean = sum(row[position] for row in training) / len(training)
            self.assertAlmostEqual(result.scaler.means[position], mean)

    def test_fold_metrics_do_not_depend_on_later_rows(self):
        bars = make_synthetic_bars()
        config = PurgedWalkForwardConfig(horizon=4, embargo_bars=3)
        result = _run(bars, config=config)
        first = result.splits[0]
        cut = first.validation_indices[-1] + config.horizon
        mutated = _run(mutate_after(bars, cut), config=config)
        self.assertEqual(mutated.splits[0], first)
        self.assertEqual(mutated.manifest.fold_metrics[0], result.manifest.fold_metrics[0])


class ModelTests(unittest.TestCase):
    def test_logistic_regression_is_deterministic(self):
        first = _run()
        second = _run()
        self.assertEqual(first.model.weights, second.model.weights)
        self.assertEqual(first.model.bias, second.model.bias)
        self.assertEqual(first.manifest.to_dict(), second.manifest.to_dict())

    def test_logistic_learns_separable_signal_better_than_base_rate(self):
        rng = random.Random(3)
        rows = [(rng.uniform(-2, 2), rng.uniform(-2, 2)) for _ in range(200)]
        labels = [int(x > 0) for x, _ in rows]
        model = LogisticRegression(LogisticParams(iterations=300, learning_rate=0.5)).fit(rows, labels, ("a", "b"))
        probabilities = model.predict_proba(rows, ("a", "b"))
        accuracy = sum(int(p >= 0.5) == y for p, y in zip(probabilities, labels, strict=True)) / len(labels)
        self.assertGreater(accuracy, 0.9)
        self.assertTrue(all(0.0 < p < 1.0 for p in probabilities))
        self.assertGreater(abs(model.weights[0]), abs(model.weights[1]))
        baseline = BaseRateClassifier().fit(labels)
        self.assertEqual(set(baseline.predict_proba(rows)), {sum(labels) / len(labels)})

    def test_predict_proba_refuses_different_feature_columns(self):
        model = LogisticRegression(FAST).fit([(0.0, 1.0), (1.0, 0.0)], [0, 1], ("a", "b"))
        with self.assertRaises(ValueError):
            model.predict_proba([(0.0, 1.0)], ("b", "a"))
        with self.assertRaises(ValueError):
            model.predict_proba([(0.0,)], ("a",))
        with self.assertRaises(RuntimeError):
            LogisticRegression().predict_proba([(0.0,)], ("a",))
        with self.assertRaises(RuntimeError):
            BaseRateClassifier().predict_proba([(0.0,)])
        with self.assertRaises(ValueError):
            LogisticRegression().fit([(0.0,)], [2], ("a",))
        with self.assertRaises(ValueError):
            LogisticParams(l2=-1.0)


class ManifestTests(unittest.TestCase):
    def test_manifest_is_complete_and_json_serializable(self):
        result = _run()
        manifest = result.manifest
        payload = manifest.to_dict()
        text = json.dumps(payload, allow_nan=False)
        self.assertIn("feature_columns", json.loads(text))
        self.assertEqual(manifest.model_name, ml.LOGISTIC_MODEL_NAME)
        self.assertEqual(manifest.feature_columns, SCALE_FREE_COLUMNS)
        self.assertEqual(manifest.dataset_identity, IDENTITY)
        self.assertEqual(manifest.python_version, platform.python_version())
        self.assertEqual(manifest.created_at, CREATED.isoformat())
        self.assertEqual(
            manifest.code_sha256,
            hashlib.sha256(Path(ml.__file__).read_bytes()).hexdigest(),
        )
        self.assertEqual(len(manifest.train_periods), len(manifest.fold_metrics))
        self.assertTrue(manifest.known_limitations)
        self.assertIn("not certainties", manifest.probability_statement)
        for fold in manifest.fold_metrics:
            self.assertEqual(sum(b.count for b in fold.calibration), fold.validation_rows)
            self.assertAlmostEqual(fold.edge_vs_baseline, fold.base_rate_brier - fold.brier)
            for value in (fold.accuracy, fold.brier, fold.base_rate_brier, fold.base_rate_accuracy):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)
            self.assertGreater(fold.log_loss, 0.0)
            self.assertLess(fold.training_end, fold.validation_start)

    def test_dataset_identity_and_created_at_are_validated(self):
        bars = make_synthetic_bars()
        with self.assertRaises(ValueError):
            run_purged_walk_forward(bars, {}, created_at=CREATED)
        with self.assertRaises(ValueError):
            run_purged_walk_forward(bars, {"x": float("nan")}, created_at=CREATED)
        with self.assertRaises(ValueError):
            run_purged_walk_forward(bars, IDENTITY, created_at=datetime(2026, 1, 1))  # noqa: DTZ001 - naive on purpose: must be rejected


if __name__ == "__main__":
    unittest.main()
