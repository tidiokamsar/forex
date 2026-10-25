"""Calibration of the holdout reuse detector on documented synthetic sets (Lot 4).

Positive sets are transforms of a sealed holdout; negative sets are independent
paths. Seeds are fixed, so the measured rates are reproducible; they are
reported in RESEARCH_PROTOCOL.md. Run ``python test_holdout_reuse_calibration.py``
from ``tests/`` to print the table. All data is synthetic: these rates say
nothing about real market data.
"""

import random
import unittest
from collections import Counter

from astra.holdout_reuse import CLEAR, CONFLICT, SUSPECT, UNDETERMINED, assess_reuse

SAMPLES = 40
LENGTH = 40


def _gauss(rng, n):
    return [rng.gauss(0.0, 1e-4) for _ in range(n)]


def _heavy(rng, n):
    return [rng.gauss(0.0, 1e-4) * (8.0 if rng.random() < 0.05 else 1.0) for _ in range(n)]


def _sparse(rng, n):
    return [rng.gauss(0.0, 1e-4) if rng.random() < 0.1 else 0.0 for _ in range(n)]


def _aggregate(values):
    return [values[i] + values[i + 1] for i in range(0, len(values) - 1, 2)]


def _correlated(rng, base, rho):
    return [rho * v + (1 - rho * rho) ** 0.5 * rng.gauss(0.0, 1e-4) for v in base]


POSITIVE = {
    "exact copy": lambda rng, b: list(b),
    "nudged 1e-9": lambda rng, b: [v + rng.gauss(0, 1e-13) for v in b],
    "offset 1 bar": lambda rng, b: b[1:] + _gauss(rng, 1),
    "offset 3 bars": lambda rng, b: _gauss(rng, 3) + b[:-3],
    "inverted quote": lambda rng, b: [-v for v in b],
    "time reversed": lambda rng, b: list(reversed(b)),
    "resampled x2": lambda rng, b: _aggregate(b + _gauss(rng, LENGTH)),
    "noise 0.1x": lambda rng, b: [v + rng.gauss(0, 1e-5) for v in b],
    "noise 0.2x": lambda rng, b: [v + rng.gauss(0, 2e-5) for v in b],
    "noise 0.3x": lambda rng, b: [v + rng.gauss(0, 3e-5) for v in b],
    "noise 0.5x": lambda rng, b: [v + rng.gauss(0, 5e-5) for v in b],
    "distinct, correlation 0.98": lambda rng, b: _correlated(rng, b, 0.98),
    "distinct, correlation 0.95": lambda rng, b: _correlated(rng, b, 0.95),
    "distinct, correlation 0.90": lambda rng, b: _correlated(rng, b, 0.90),
}
NEGATIVE = {
    "independent gaussian": _gauss,
    "independent heavy-tailed": _heavy,
    "independent sparse (90% flat)": _sparse,
}


def calibration_table(samples=SAMPLES):
    table = {}
    for name, transform in POSITIVE.items():
        rng = random.Random(f"positive:{name}")
        verdicts = Counter()
        for _ in range(samples):
            base = _gauss(rng, LENGTH)
            verdicts[assess_reuse(transform(rng, base), base).verdict] += 1
        table[name] = verdicts
    for name, generator in NEGATIVE.items():
        rng = random.Random(f"negative:{name}")
        table[name] = Counter(
            assess_reuse(generator(rng, LENGTH), generator(rng, LENGTH)).verdict for _ in range(samples)
        )
    return table


def flagged(counter):
    return (counter[CONFLICT] + counter[SUSPECT]) / sum(counter.values())


class DetectorCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = calibration_table()

    def test_copies_and_simple_transforms_are_conflicts(self):
        for name in ("exact copy", "nudged 1e-9", "offset 1 bar", "offset 3 bars", "inverted quote", "time reversed",
                     "resampled x2", "noise 0.1x"):
            with self.subTest(name):
                self.assertGreaterEqual(self.table[name][CONFLICT] / SAMPLES, 0.95, self.table[name])

    def test_moderate_noise_and_strong_correlation_are_flagged(self):
        for name, floor in (("noise 0.2x", 0.9), ("distinct, correlation 0.98", 0.9)):
            with self.subTest(name):
                self.assertGreaterEqual(flagged(self.table[name]), floor, self.table[name])

    def test_independent_paths_are_not_flagged(self):
        for name in NEGATIVE:
            with self.subTest(name):
                self.assertEqual(flagged(self.table[name]), 0.0, self.table[name])

    def test_documented_limit_heavy_noise_is_mostly_missed(self):
        # Not a goal: it documents that a copy with noise half the return scale is usually not detected.
        self.assertLess(flagged(self.table["noise 0.5x"]), 0.5, self.table["noise 0.5x"])


if __name__ == "__main__":
    for name, counter in calibration_table().items():
        print(f"{name:32s} conflict {counter[CONFLICT]:3d} suspect {counter[SUSPECT]:3d} "
              f"undetermined {counter[UNDETERMINED]:3d} clear {counter[CLEAR]:3d}")
