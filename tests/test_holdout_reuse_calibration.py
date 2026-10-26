"""Calibration of the holdout reuse detector on documented synthetic sets (Lot 4, round 4).

Positive sets are transforms of a sealed holdout; negative sets are independent paths. Seeds are
fixed, so the measured counts are reproducible; they are asserted exactly (``DOCUMENTED``) and
reported in RESEARCH_PROTOCOL.md. ``UNDETERMINED`` is reported separately: it blocks scoring and
promotion, so it is neither "flagged" nor "not flagged". Run ``python test_holdout_reuse_calibration.py``
from ``tests/`` to print the table. All data is synthetic: these rates say nothing about real
market data. With 0 events in N independent pairs the one-sided 95% upper bound on the rate is
``1 - 0.05 ** (1 / N)`` (about 3 / N).
"""

import math
import random
import unittest
from collections import Counter

from astra.holdout_reuse import CLEAR, CONFLICT, SUSPECT, UNDETERMINED, assess_reuse, holdout_returns

POSITIVE_SAMPLES = 200
NEGATIVE_SAMPLES = 600
LONG_SAMPLES = 150
AR_LONG_SAMPLES = 60
LENGTH = 40
TICK = 1e-5


def _gauss(rng, n=LENGTH):
    return [rng.gauss(0.0, 1e-4) for _ in range(n)]


def _heavy(rng, n=LENGTH):
    return [rng.gauss(0.0, 1e-4) * (8.0 if rng.random() < 0.05 else 1.0) for _ in range(n)]


def _sparse_gauss(rng, n=LENGTH):
    return [rng.gauss(0.0, 1e-4) if rng.random() < 0.1 else 0.0 for _ in range(n)]


def _sparse_binary(rng, n=LENGTH):
    """90% flat, every move the same size (a pegged or quantized quote)."""

    return [rng.choice((-1.0, 1.0)) * 1e-4 if rng.random() < 0.1 else 0.0 for _ in range(n)]


def _ar1(rng, n=LENGTH, phi=0.7):
    out, x = [], 0.0
    for _ in range(n):
        x = phi * x + rng.gauss(0.0, 1e-4)
        out.append(x)
    return out


def _returns(closes):
    return holdout_returns(closes)


def _tick_closes(rng, n=LENGTH):
    """Random walk of 0, 1 or 2 ticks per bar, quoted to 1e-5."""

    price, closes = 1.1, [1.1]
    for _ in range(n):
        price += rng.choice((-1, 1)) * (2 if rng.random() < 0.15 else 1) * TICK * (rng.random() < 0.8)
        closes.append(round(price, 5))
    return closes


def _ticks(rng, n=LENGTH):
    return _returns(_tick_closes(rng, n))


def _sub_tick_closes(rng, n=LENGTH):
    """+2 ticks per bar with noise of 0.2 tick, quoted to 1e-5 (a smooth trend)."""

    price, closes = 1.1, [1.1]
    for _ in range(n):
        price += 2 * TICK + rng.gauss(0.0, 0.2 * TICK)
        closes.append(round(price, 5))
    return closes


def _sub_tick(rng, n=LENGTH):
    return _returns(_sub_tick_closes(rng, n))


def _aggregate(values):
    return [values[i] + values[i + 1] for i in range(0, len(values) - 1, 2)]


def _rms_of(values):
    return (sum(v * v for v in values) / len(values)) ** 0.5


def _correlated(rng, base, rho):
    return [rho * v + (1 - rho * rho) ** 0.5 * rng.gauss(0.0, 1e-4) for v in base]


def _nudged_close(closes, index):
    nudged = list(closes)
    nudged[index] = round(nudged[index] + TICK, 5)
    return _returns(nudged)


# name: (sealed holdout generator, transform giving the new holdout)
POSITIVE = {
    "exact copy": (_gauss, lambda rng, b: list(b)),
    "nudged 1e-9": (_gauss, lambda rng, b: [v + rng.gauss(0, 1e-13) for v in b]),
    "offset 1 bar": (_gauss, lambda rng, b: b[1:] + _gauss(rng, 1)),
    "offset 3 bars": (_gauss, lambda rng, b: _gauss(rng, 3) + b[:-3]),
    "inverted quote": (_gauss, lambda rng, b: [-v for v in b]),
    "time reversed": (_gauss, lambda rng, b: list(reversed(b))),
    "resampled x2": (_gauss, lambda rng, b: _aggregate(b + _gauss(rng, LENGTH))),
    "noise 0.1x": (_gauss, lambda rng, b: [v + rng.gauss(0, 1e-5) for v in b]),
    "noise 0.2x": (_gauss, lambda rng, b: [v + rng.gauss(0, 2e-5) for v in b]),
    "noise 0.3x": (_gauss, lambda rng, b: [v + rng.gauss(0, 3e-5) for v in b]),
    "noise 0.5x": (_gauss, lambda rng, b: [v + rng.gauss(0, 5e-5) for v in b]),
    "distinct, correlation 0.98": (_gauss, lambda rng, b: _correlated(rng, b, 0.98)),
    "distinct, correlation 0.95": (_gauss, lambda rng, b: _correlated(rng, b, 0.95)),
    "distinct, correlation 0.90": (_gauss, lambda rng, b: _correlated(rng, b, 0.90)),
    "sparse gaussian, nudged 1e-9": (_sparse_gauss, lambda rng, b: [v + 1e-9 * (i == 5) for i, v in enumerate(b)]),
    "sparse binary, nudged 1e-9": (_sparse_binary, lambda rng, b: [v + 1e-9 * (i == 5) for i, v in enumerate(b)]),
    "sparse binary, inverted and reversed": (_sparse_binary, lambda rng, b: [-v for v in reversed(b)]),
    "tick walk, offset 1 bar": (_ticks, lambda rng, b: b[1:] + _ticks(rng, 1)),
    "AR(1) 0.7, offset 1 bar": (_ar1, lambda rng, b: b[1:] + _gauss(rng, 1)),
    "AR(1) 0.95 (60 returns), noise 0.2x": (lambda rng: _ar1(rng, 60, 0.95),
                                            lambda rng, b: [v + rng.gauss(0, 0.2 * _rms_of(b)) for v in b]),
    "sparse binary (60 returns), moves x1.5": (lambda rng: _sparse_binary(rng, 60), lambda rng, b: [1.5 * v for v in b]),
    "sparse gaussian (60 returns), 20% noise on moves": (lambda rng: _sparse_gauss(rng, 60),
                                                         lambda rng, b: [v * (1 + rng.gauss(0, 0.2)) for v in b]),
}
# Copies of near-constant paths whose closes were moved by one tick (the closes are the sealed data).
def _nudge_one(rng, closes):
    return _nudged_close(closes, rng.randrange(1, len(closes) - 1))


def _rescaled_requoted(rng, closes):
    """The same prices doubled and quoted again to 1e-5 (another rounding of the same path)."""

    return _returns([round(2 * value, 5) for value in closes])


def _sub_tick_unrounded(rng, n=120):
    price, closes = 1.1, [1.1]
    for _ in range(n):
        price += 2 * TICK + rng.gauss(0.0, 0.2 * TICK)
        closes.append(price)
    return closes


# name: (sealed closes generator, transform giving the new holdout's returns); the sealed returns are
# the closes quoted to 1e-5.
POSITIVE_CLOSES = {
    "sub-tick trend, one close nudged 1 tick": (_sub_tick_closes, _nudge_one),
    "tick walk, 16 returns, one close nudged 1 tick": (lambda rng: _tick_closes(rng, 16), _nudge_one),
    "tick walk, 16 returns, inverted": (lambda rng: _tick_closes(rng, 16), lambda rng, c: [-v for v in _returns(c)]),
    "tick walk, 20 returns, one close nudged 1 tick": (lambda rng: _tick_closes(rng, 20), _nudge_one),
    "tick walk, 20 returns, inverted": (lambda rng: _tick_closes(rng, 20), lambda rng, c: [-v for v in _returns(c)]),
    "sub-tick trend, 120 returns, prices x2 re-quoted": (_sub_tick_unrounded, _rescaled_requoted),
}

# name: (generator, length, number of independent pairs)
NEGATIVE = {
    "independent gaussian": (_gauss, LENGTH, NEGATIVE_SAMPLES),
    "independent heavy-tailed": (_heavy, LENGTH, NEGATIVE_SAMPLES),
    "independent sparse gaussian (90% flat)": (_sparse_gauss, LENGTH, NEGATIVE_SAMPLES),
    "independent sparse binary (90% flat)": (_sparse_binary, LENGTH, NEGATIVE_SAMPLES),
    "independent AR(1) 0.7": (_ar1, LENGTH, NEGATIVE_SAMPLES),
    "independent tick-quantized walks": (_ticks, LENGTH, NEGATIVE_SAMPLES),
    "independent sub-tick trends": (_sub_tick, LENGTH, NEGATIVE_SAMPLES),
    "independent gaussian, 16 returns": (_gauss, 16, NEGATIVE_SAMPLES),
    "independent tick-quantized walks, 16 returns": (_ticks, 16, NEGATIVE_SAMPLES),
    "independent tick-quantized walks, 20 returns": (_ticks, 20, NEGATIVE_SAMPLES),
    "independent gaussian, 200 returns": (_gauss, 200, LONG_SAMPLES),
    "independent tick-quantized walks, 200 returns": (_ticks, 200, LONG_SAMPLES),
    "independent AR(1) 0.7, 400 returns": (_ar1, 400, AR_LONG_SAMPLES),
}

# Measured on the fixed seeds (CONFLICT, SUSPECT, UNDETERMINED, CLEAR). Asserted exactly: a detector
# change that moves any count must update this table and RESEARCH_PROTOCOL.md together.
DOCUMENTED = {
    'exact copy': (200, 0, 0, 0),
    'nudged 1e-9': (200, 0, 0, 0),
    'offset 1 bar': (200, 0, 0, 0),
    'offset 3 bars': (200, 0, 0, 0),
    'inverted quote': (200, 0, 0, 0),
    'time reversed': (200, 0, 0, 0),
    'resampled x2': (200, 0, 0, 0),
    'noise 0.1x': (200, 0, 0, 0),
    'noise 0.2x': (162, 38, 0, 0),
    'noise 0.3x': (30, 153, 0, 17),
    'noise 0.5x': (1, 30, 0, 169),
    'distinct, correlation 0.98': (156, 44, 0, 0),
    'distinct, correlation 0.95': (15, 135, 0, 50),
    'distinct, correlation 0.90': (0, 37, 0, 163),
    'sparse gaussian, nudged 1e-9': (148, 12, 40, 0),
    'sparse binary, nudged 1e-9': (89, 62, 49, 0),
    'sparse binary, inverted and reversed': (88, 68, 44, 0),
    'tick walk, offset 1 bar': (200, 0, 0, 0),
    'AR(1) 0.7, offset 1 bar': (200, 0, 0, 0),
    'AR(1) 0.95 (60 returns), noise 0.2x': (99, 68, 0, 33),
    'sparse binary (60 returns), moves x1.5': (167, 24, 9, 0),
    'sparse gaussian (60 returns), 20% noise on moves': (156, 30, 13, 1),
    'sub-tick trend, one close nudged 1 tick': (13, 53, 0, 134),
    'tick walk, 16 returns, one close nudged 1 tick': (98, 95, 6, 1),
    'tick walk, 16 returns, inverted': (191, 9, 0, 0),
    'tick walk, 20 returns, one close nudged 1 tick': (186, 14, 0, 0),
    'tick walk, 20 returns, inverted': (200, 0, 0, 0),
    'sub-tick trend, 120 returns, prices x2 re-quoted': (0, 0, 0, 200),
    'independent gaussian': (0, 0, 0, 600),
    'independent heavy-tailed': (0, 0, 0, 600),
    'independent sparse gaussian (90% flat)': (0, 1, 129, 470),
    'independent sparse binary (90% flat)': (0, 2, 139, 459),
    'independent AR(1) 0.7': (0, 0, 0, 600),
    'independent tick-quantized walks': (0, 0, 0, 600),
    'independent sub-tick trends': (0, 7, 47, 546),
    'independent gaussian, 16 returns': (0, 0, 0, 600),
    'independent tick-quantized walks, 16 returns': (0, 1, 6, 593),
    'independent tick-quantized walks, 20 returns': (0, 0, 0, 600),
    'independent gaussian, 200 returns': (0, 0, 0, 150),
    'independent tick-quantized walks, 200 returns': (0, 0, 0, 150),
    'independent AR(1) 0.7, 400 returns': (0, 0, 0, 60),
}


def calibration_table():
    table = {}
    for name, (generate, transform) in POSITIVE.items():
        rng = random.Random(f"positive:{name}")
        verdicts = Counter()
        for _ in range(POSITIVE_SAMPLES):
            base = generate(rng)
            verdicts[assess_reuse(transform(rng, base), base).verdict] += 1
        table[name] = verdicts
    for name, (generate, transform) in POSITIVE_CLOSES.items():
        rng = random.Random(f"positive:{name}")
        verdicts = Counter()
        for _ in range(POSITIVE_SAMPLES):
            closes = generate(rng)
            sealed = _returns([round(value, 5) for value in closes])
            verdicts[assess_reuse(transform(rng, closes), sealed).verdict] += 1
        table[name] = verdicts
    for name, (generate, length, samples) in NEGATIVE.items():
        rng = random.Random(f"negative:{name}")
        table[name] = Counter(
            assess_reuse(generate(rng, length), generate(rng, length)).verdict for _ in range(samples)
        )
    return table


def counts(counter):
    return tuple(counter[verdict] for verdict in (CONFLICT, SUSPECT, UNDETERMINED, CLEAR))


def upper_bound_95(events, trials):
    """One-sided 95% upper bound on a rate (exact for 0 events, Clopper-Pearson otherwise, by bisection)."""

    if events == 0:
        return 1.0 - 0.05 ** (1.0 / trials)
    low, high = events / trials, 1.0
    for _ in range(60):
        rate = (low + high) / 2
        below = sum(math.comb(trials, k) * rate ** k * (1 - rate) ** (trials - k) for k in range(events + 1))
        low, high = (rate, high) if below > 0.05 else (low, rate)
    return high


class DetectorCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = calibration_table()

    def share(self, name, *verdicts):
        counter = self.table[name]
        return sum(counter[verdict] for verdict in verdicts) / sum(counter.values())

    def test_measured_counts_match_the_documented_table(self):
        self.assertEqual({name: counts(counter) for name, counter in self.table.items()}, DOCUMENTED)

    def test_copies_and_simple_transforms_are_conflicts(self):
        for name in ("exact copy", "nudged 1e-9", "offset 1 bar", "offset 3 bars", "inverted quote", "time reversed",
                     "resampled x2", "noise 0.1x", "tick walk, offset 1 bar", "AR(1) 0.7, offset 1 bar",
                     "tick walk, 16 returns, inverted", "tick walk, 20 returns, inverted"):
            with self.subTest(name):
                self.assertGreaterEqual(self.share(name, CONFLICT), 0.95, self.table[name])

    def test_moderate_noise_and_strong_correlation_are_flagged(self):
        for name, floor in (("noise 0.2x", 0.9), ("distinct, correlation 0.98", 0.9), ("noise 0.3x", 0.75),
                            ("distinct, correlation 0.95", 0.75)):
            with self.subTest(name):
                self.assertGreaterEqual(self.share(name, CONFLICT, SUSPECT), floor, self.table[name])

    def test_sparse_copies_are_never_cleared(self):
        # A sparse copy is CONFLICT when its moves are improbable by chance, SUSPECT otherwise (it then
        # cannot be scored), UNDETERMINED with fewer than three moves; it is never CLEAR.
        for name in ("sparse gaussian, nudged 1e-9", "sparse binary, nudged 1e-9", "sparse binary, inverted and reversed",
                     "sparse binary (60 returns), moves x1.5"):
            with self.subTest(name):
                self.assertEqual(self.table[name][CLEAR], 0, self.table[name])

    def test_independent_paths_are_never_conflicts(self):
        for name in NEGATIVE:
            with self.subTest(name):
                self.assertEqual(self.table[name][CONFLICT], 0, self.table[name])

    def test_independent_dense_paths_are_comparable_and_rarely_flagged(self):
        for name in ("independent gaussian", "independent heavy-tailed", "independent AR(1) 0.7",
                     "independent tick-quantized walks", "independent gaussian, 200 returns",
                     "independent tick-quantized walks, 200 returns", "independent AR(1) 0.7, 400 returns"):
            with self.subTest(name):
                self.assertEqual(self.table[name][UNDETERMINED], 0, self.table[name])
                self.assertLessEqual(self.share(name, SUSPECT), 0.01, self.table[name])

    def test_independent_sparse_and_trend_paths_are_rarely_flagged(self):
        # Documented availability cost: sparse paths with fewer than three moves are UNDETERMINED, and
        # a few independent sparse or sub-tick-trend pairs are SUSPECT (they cannot be scored).
        for name in ("independent sparse gaussian (90% flat)", "independent sparse binary (90% flat)",
                     "independent sub-tick trends"):
            with self.subTest(name):
                self.assertLessEqual(self.share(name, SUSPECT), 0.02, self.table[name])

    def test_short_tick_copies_are_rarely_or_never_cleared(self):
        # Round 5 (Q-1, Q-2): at the CLI default of 20 returns no tick copy is CLEAR; at the 16-return floor a
        # copy nudged by one tick still is in 1 of 200 (documented), and an inverted copy never is.
        for name in ("tick walk, 20 returns, one close nudged 1 tick", "tick walk, 20 returns, inverted",
                     "tick walk, 16 returns, inverted"):
            with self.subTest(name):
                self.assertEqual(self.table[name][CLEAR], 0, self.table[name])
        self.assertLessEqual(self.table["tick walk, 16 returns, one close nudged 1 tick"][CLEAR], 1)

    def test_rescaled_and_noisy_persistent_copies_are_mostly_flagged(self):
        # A4-D-4 / A4-D-5: noisy copies of a persistent path and perturbed sparse copies (documented residue).
        for name, floor in (("AR(1) 0.95 (60 returns), noise 0.2x", 0.8),
                            ("sparse gaussian (60 returns), 20% noise on moves", 0.9)):
            with self.subTest(name):
                self.assertGreaterEqual(self.share(name, CONFLICT, SUSPECT), floor, self.table[name])

    def test_documented_limit_requoted_rescaled_sub_tick_trend_is_missed(self):
        # Q-3, documented: re-quoting doubled prices to 1e-5 changes every rounding; not detected.
        self.assertEqual(self.share("sub-tick trend, 120 returns, prices x2 re-quoted", CLEAR), 1.0)

    def test_documented_limit_moderate_noise_and_correlation_090_are_mostly_missed(self):
        # Not goals: they document that a copy with noise half the return scale, or a distinct path
        # correlated 0.90, is usually not detected.
        self.assertLess(self.share("noise 0.5x", CONFLICT, SUSPECT), 0.5, self.table["noise 0.5x"])
        self.assertLess(self.share("distinct, correlation 0.90", CONFLICT, SUSPECT), 0.5,
                        self.table["distinct, correlation 0.90"])


if __name__ == "__main__":
    measured = calibration_table()
    print(f"{'set':48s} {'n':>4s} conflict suspect undetermined clear  95% upper bound (flagged, if 0)")
    for name, counter in measured.items():
        total = sum(counter.values())
        flagged = counter[CONFLICT] + counter[SUSPECT]
        bound = f"{upper_bound_95(flagged, total):.4f}" if name in NEGATIVE else ""
        print(f"{name:48s} {total:4d} {counter[CONFLICT]:8d} {counter[SUSPECT]:7d} {counter[UNDETERMINED]:12d} "
              f"{counter[CLEAR]:5d}  {bound}")
    print("DOCUMENTED =", {name: counts(counter) for name, counter in measured.items()})
