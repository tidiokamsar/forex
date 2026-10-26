"""Lot 4 independent security review (Agent A) of the last two Lot 3 fixes (commit 8e1c897).

Scope: the holdout content fingerprint (validation._holdout_content_sha256, checked in
BacktestStore.seal_research_protocol) and the policy floor re-applied at promotion
(registry.check_promotion), plus the surrounding holdout/registry/CLI paths.

Every test asserts the SECURE behaviour. Tests decorated with ``unittest.expectedFailure``
reproduce a confirmed defect on the reviewed code (a2a1856); undecorated tests are
regression tests for protections that hold.
"""

import contextlib
import io
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import time
import types
import unittest
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import accept_unverified_evidence, workspace_tempdir
from astra.anchoring import LocalFileAnchor, verify_store_against_anchor
from astra.attempts import AttemptLog, HoldoutAttempt
from astra.backup import create_backup, restore_backup
from astra.cli import main
from astra.governance import Actor, Role
from astra.holdout_reuse import content_sha256, holdout_returns
from astra.models import MarketBar
from astra.registry import (
    ModelRegistry,
    ModelStatus,
    PromotionCriteria,
    PromotionError,
    _canonical,
    _rederivation_failures,
    _sha256,
)
from astra.research_policy import HOLDOUT_REUSE_POLICY, reuse_policy_looser_than_floor
from astra.storage import BacktestStore
from astra.strategy_validation import (
    StrategyHoldoutEvaluation,
    _store_trial_universe,
    evaluate_strategy_holdout_once,
    holdout_evidence,
    overfitting_payload,
    rederive_holdout_evidence,
    trial_universe_payload,
)
from astra.validation import _canonical_json
from astra.validation import _sha256 as _plan_sha256
from test_features import make_synthetic_bars
from test_strategy_validation import BARS, DEVELOPMENT_END, PROTOCOL, evaluate, overfitting_double

REVIEWER = Actor("reviewer", Role.HUMAN_REVIEWER)
AGENT = Actor("agent", Role.RESEARCH_AGENT)
TWO_YEARS = timedelta(days=730)
PLAIN_MANIFEST = {"dataset_identity": "d", "feature_set_version": "1", "code_sha256": "c", "params": {}}
# One value per floor field, each just looser than PromotionCriteria() defaults.
LOOSER = {
    "min_trades": 29, "min_net_pnl": -1.0, "min_profit_factor": 1.0, "max_drawdown_pct": 10.5,
    "min_edge_vs_baseline": -0.1, "require_cost_stress_non_negative": False, "require_holdout": False,
    "max_pbo": 0.31, "min_deflated_sharpe_probability": 0.94, "require_overfitting_check": False,
}


def relabel(bars, instrument="EURUSD.X"):
    return [replace(bar, instrument=instrument) for bar in bars]


def shift(bars, delta=TWO_YEARS):
    return [replace(bar, timestamp=bar.timestamp + delta) for bar in bars]


def nudge(bars, index, factor=1.0 + 1e-9):
    """Multiply one close by ``factor`` (keeping the bar valid); all other bars are untouched."""

    bars = list(bars)
    bar = bars[index]
    close = bar.close * factor
    bars[index] = replace(bar, close=close, high=max(bar.high, close), low=min(bar.low, close))
    return bars


def invert(bars, instrument="USDEUR"):
    """The same quotes expressed as the inverse pair (1/price): the same market information."""

    return [
        replace(
            bar,
            instrument=instrument,
            open=1.0 / bar.open,
            high=1.0 / bar.low,
            low=1.0 / bar.high,
            close=1.0 / bar.close,
            bid=1.0 / bar.ask if bar.ask else None,
            ask=1.0 / bar.bid if bar.bid else None,
        )
        for bar in bars
    ]


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = workspace_tempdir()
        temp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.database = f"{temp}/astra.sqlite3"
        self.store = BacktestStore(self.database)
        self.store.initialize()

    def seal_original(self):
        with overfitting_double():
            return evaluate(store=self.store)

    def assert_reuse_refused(self, bars, protocol=PROTOCOL):
        """A second plan on (essentially) the already sealed holdout must be refused before scoring."""

        with overfitting_double(), self.assertRaises(ValueError):
            evaluate(bars=bars, protocol=protocol, store=self.store)


class HoldoutFingerprintEvasionTests(_StoreCase):
    """A-1: the content fingerprint is an exact hash, and the time-overlap check is scoped by label and dates.

    Any change that alters one rounded return (a nudge far below market noise, a one-bar offset,
    one bar more or less) yields a new fingerprint; relabelling the instrument or shifting the
    timestamps then also skips the overlap check, so the same holdout can be scored again.
    """

    def setUp(self):
        super().setUp()
        self.seal_original()

    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-1): identical holdout bars (only the label differs) are accepted once the
    # bar BEFORE the holdout is nudged, because that bar's close is part of the fingerprint.
    def test_relabelled_copy_with_only_the_pre_holdout_bar_changed_is_refused(self):
        self.assert_reuse_refused(nudge(relabel(BARS), DEVELOPMENT_END - 1, factor=1.0 + 1e-6))

    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-1): a 1e-9 relative nudge of one holdout close evades the fingerprint.
    def test_relabelled_copy_with_one_holdout_close_nudged_is_refused(self):
        self.assert_reuse_refused(nudge(relabel(BARS), len(BARS) - 1))

    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-1): same instrument, dates moved two years, one close nudged by 1e-9.
    def test_time_shifted_copy_with_one_holdout_close_nudged_is_refused(self):
        self.assert_reuse_refused(nudge(shift(BARS), len(BARS) - 1))

    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-1): one bar appended and dates shifted: 39 of the 40 sealed holdout bars are rescored.
    def test_time_shifted_copy_with_holdout_offset_by_one_bar_is_refused(self):
        longer = make_synthetic_bars(401)
        self.assertEqual([bar.close for bar in longer[:400]], [bar.close for bar in BARS])
        self.assert_reuse_refused(shift(longer))

    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-1): a one-bar-longer holdout (super-range) on a relabelled copy is accepted.
    def test_relabelled_copy_with_a_longer_holdout_is_refused(self):
        self.assert_reuse_refused(relabel(BARS), protocol=replace(PROTOCOL, holdout_bars=PROTOCOL.holdout_bars + 1))

    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-1): a one-bar-shorter holdout (sub-range) on a relabelled copy is accepted.
    def test_relabelled_copy_with_a_shorter_holdout_is_refused(self):
        self.assert_reuse_refused(relabel(BARS), protocol=replace(PROTOCOL, holdout_bars=PROTOCOL.holdout_bars - 1))

    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-2): the inverse quote (1/price, e.g. USDEUR) carries the same information
    # but negated returns give another fingerprint, and another label skips the overlap check.
    def test_inverted_quote_copy_is_refused(self):
        self.assert_reuse_refused(invert(BARS))

    # --- protections that hold ------------------------------------------------

    def test_relabelled_rescaled_and_shifted_exact_copy_is_refused(self):
        scaled = [replace(b, open=b.open * 3, high=b.high * 3, low=b.low * 3, close=b.close * 3,
                          bid=b.bid * 3, ask=b.ask * 3) for b in BARS]
        self.assert_reuse_refused(shift(relabel(scaled)))

    def test_copy_with_extra_leading_history_is_refused(self):
        # Different canonical identity and start offset, identical holdout and pre-holdout bar.
        earlier = make_synthetic_bars(1, seed=5, start_index=-1)
        self.assert_reuse_refused(relabel([*earlier, *BARS]))

    def test_case_only_relabel_is_still_the_same_instrument(self):
        # _prepare_bars canonicalises the label, so the overlap check still applies.
        self.assert_reuse_refused(nudge(relabel(BARS, "eurusd "), len(BARS) - 1))

    def test_non_positive_or_nan_prices_are_refused_before_sealing(self):
        for bad in (0.0, -1.0, math.nan, math.inf):
            with self.subTest(close=bad):
                bars = list(relabel(BARS))
                bars[-1] = replace(bars[-1], close=bad)
                self.assert_reuse_refused(bars)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_protocols").fetchone()[0], 1)


class LegacyPlanTests(_StoreCase):
    # FIXED IN LOT 4 (was CONFIRMED DEFECT A-3): a new plan without holdout.content_sha256 and its
    # returns is refused at seal time; a plan already stored without them (pre-Lot 4 database) is
    # compared by holdout period with every instrument, so a relabelled copy is refused.
    def legacy_plan(self):
        with overfitting_double():
            report = evaluate()
        legacy = json.loads(_canonical_json(report.plan_manifest))
        del legacy["holdout"]["content_sha256"]
        return report, legacy

    def test_new_plan_without_fingerprint_is_refused_at_seal(self):
        report, legacy = self.legacy_plan()
        with self.assertRaisesRegex(ValueError, "content_sha256"):
            self.store.seal_research_protocol(
                protocol_id=_plan_sha256(_canonical_json(legacy)),
                dataset_identity_sha256=report.dataset_identity_sha256,
                protocol_version=report.protocol_version,
                plan_manifest=legacy,
            )

    def test_relabelled_copy_of_a_legacy_plan_without_fingerprint_is_refused(self):
        report, legacy = self.legacy_plan()
        with contextlib.closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT INTO research_protocols VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_plan_sha256(_canonical_json(legacy)), report.dataset_identity_sha256, report.protocol_version,
                 "2020-01-01T00:00:00+00:00", legacy["holdout"]["start"], legacy["holdout"]["end"],
                 _canonical_json(legacy)),
            )
        self.assert_reuse_refused(relabel(BARS))


class FabricatedHoldoutTests(_StoreCase):
    """A-4: public store and attempt-log helpers can still mint a COMPLETED holdout with any report."""

    # FIXED IN LOT 4 ROUND 2 (was CONFIRMED DEFECT A-4) (phase 2: mitigated, not fixed). The phase 1 version of this test stopped at
    # seal on 9eae4a6 (content_sha256 now required), which expectedFailure counts as a failure too, so it
    # was rewritten. Minimal mint now: one honest walk-forward (real plan, real fold trials in the
    # attempt log), then begin_holdout_evaluation + HoldoutAttempt.start().finish() +
    # complete_holdout_evaluation with a made-up report. No SQL, no trigger, no chain forgery.
    def test_holdout_minted_through_the_store_api_cannot_promote(self):
        registry = ModelRegistry(self.database)  # default (strict) verifier
        registry.initialize()
        report = self.seal_original()
        selected = next(item for item in report.final_candidate_selection if item.selected)
        registry.register_candidate(
            actor=AGENT, model_id="minted", version="1",
            manifest={"dataset_identity": {"canonical_sha256": report.dataset_identity_sha256},
                      "feature_set_version": "1", "code_sha256": report.implementation_sha256,
                      "params": selected.config, "plan_sha256": report.plan_sha256,
                      "candidate_id": selected.candidate_id},
            criteria=PromotionCriteria(),
        )
        attempt = self.store.begin_holdout_evaluation(protocol_id=report.plan_sha256,
                                                      dataset_identity_sha256=report.dataset_identity_sha256)
        HoldoutAttempt.start(self.store, protocol_id=report.plan_sha256, plan=report.plan_manifest,
                             candidate_id=selected.candidate_id).finish({"selected_candidate": None}, None)
        fake = {
            "protocol_id": report.plan_sha256, "plan_sha256": report.plan_sha256,
            "dataset_identity_sha256": report.dataset_identity_sha256, "status": "COMPLETED",
            "selected_candidate_id": selected.candidate_id, "selected_config": selected.config,
            "selected_candidate": {"trade_count": 500, "net_pnl": 9_000.0, "profit_factor": 4.0, "max_drawdown_pct": 1.0},
            "edge_vs_baseline": 8_000.0,
            "cost_sensitivity": [{"cost_multiplier": 2.0, "selected_candidate": {"net_pnl": 7_000.0}}],
            "overfitting": {"pbo": 0.01, "deflated_sharpe_probability": 0.999},
            "variants_tested": 1_000,
        }
        self.store.complete_holdout_evaluation(evaluation_id=attempt["evaluation_id"], report=fake)
        registry.record_evaluation(actor=AGENT, model_id="minted", version="1", kind="holdout",
                                   evidence=holdout_evidence(fake), holdout_evaluation_id=attempt["evaluation_id"])
        self.assertIn("holdout_not_rederived_from_market_data", registry.check_promotion("minted", "1"))
        # Lot 4 round 2: with the plan's real bars the re-derivation exposes the minted numbers.
        self.assertIn("holdout_report_differs_from_rederivation",
                      registry.check_promotion("minted", "1", holdout_bars=BARS))


class VerifierOverrideTests(_StoreCase):
    # CONFIRMED DEFECT A-5 (low, design): the production constructor accepts evidence_verifier, so any
    # in-process caller can disable provenance; promote() then accepts evidence backed by no holdout.
    @unittest.expectedFailure
    def test_promotion_with_unbacked_evidence_is_refused_even_with_a_permissive_verifier(self):
        registry = ModelRegistry(self.database, evidence_verifier=accept_unverified_evidence)
        registry.initialize()
        registry.register_candidate(actor=AGENT, model_id="m", version="1",
                                    manifest={"dataset_identity": "d", "feature_set_version": "1", "code_sha256": "c",
                                              "params": {}}, criteria=PromotionCriteria())
        registry.record_evaluation(actor=AGENT, model_id="m", version="1", kind="holdout", holdout_evaluation_id="none",
                                   evidence={"trade_count": 99, "net_pnl": 1.0, "profit_factor": 2.0,
                                             "max_drawdown_pct": 1.0, "edge_vs_baseline": 1.0, "cost_stress_net_pnl": 1.0,
                                             "holdout_used": True, "pbo": 0.0, "deflated_sharpe_probability": 1.0,
                                             "variants_tested": 2})
        with contextlib.suppress(PromotionError):
            registry.promote(actor=REVIEWER, model_id="m", version="1", reason="override")
        self.assertNotEqual(registry.get("m", "1")["status"], ModelStatus.CHAMPION)


class PolicyFloorRegressionTests(_StoreCase):
    """The floor at registration and at promotion: every field, odd types, legacy and future rows."""

    def setUp(self):
        super().setUp()
        self.registry = ModelRegistry(self.database, evidence_verifier=accept_unverified_evidence)
        self.registry.initialize()

    def rewrite_criteria(self, model_id, payload):
        with contextlib.closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE model_versions SET criteria_json = ?, criteria_sha256 = ? WHERE model_id = ?",
                       (_canonical(payload), _sha256(payload), model_id))

    def test_every_looser_field_is_refused_at_registration(self):
        odd = {"min_trades": True, "require_holdout": 1, "require_cost_stress_non_negative": None,
               "max_pbo": True, "min_net_pnl": math.nan, "max_drawdown_pct": math.inf, "min_trades_float": 30.0}
        cases = {**{name: {name: value} for name, value in LOOSER.items()},
                 **{f"odd_{name}": {name.removesuffix("_float"): value} for name, value in odd.items()}}
        for label, override in cases.items():
            with self.subTest(label), self.assertRaises((ValueError, TypeError)):
                self.registry.register_candidate(actor=AGENT, model_id=label, version="1", manifest=PLAIN_MANIFEST,
                                                 criteria=PromotionCriteria(**override))

    def test_every_looser_field_in_a_stored_row_is_refused_at_promotion(self):
        for name, value in LOOSER.items():
            with self.subTest(name):
                self.registry.register_candidate(actor=AGENT, model_id=name, version="1", manifest=PLAIN_MANIFEST,
                                                 criteria=PromotionCriteria())
                self.rewrite_criteria(name, {**asdict(PromotionCriteria()), name: value})
                self.assertIn(f"criteria_below_policy_floor:{name}", self.registry.check_promotion(name, "1"))
                with self.assertRaises(PromotionError):
                    self.registry.promote(actor=REVIEWER, model_id=name, version="1", reason="lax row")

    def test_stored_row_missing_fields_gets_the_strict_defaults(self):
        self.registry.register_candidate(actor=AGENT, model_id="old", version="1", manifest=PLAIN_MANIFEST,
                                         criteria=PromotionCriteria())
        partial = {key: value for key, value in asdict(PromotionCriteria()).items()
                   if key not in ("max_pbo", "min_deflated_sharpe_probability", "require_overfitting_check")}
        self.rewrite_criteria("old", partial)
        self.registry.record_evaluation(actor=AGENT, model_id="old", version="1", kind="holdout", holdout_evaluation_id="h",
                                        evidence={"trade_count": 99, "net_pnl": 1.0, "profit_factor": 2.0,
                                                  "max_drawdown_pct": 1.0, "edge_vs_baseline": 1.0,
                                                  "cost_stress_net_pnl": 1.0, "holdout_used": True, "pbo": 0.9,
                                                  "deflated_sharpe_probability": 0.1, "variants_tested": 2})
        failures = self.registry.check_promotion("old", "1")
        self.assertIn("pbo_above_threshold", failures)
        self.assertIn("deflated_sharpe_below_threshold", failures)

    def test_stored_row_with_an_unknown_field_never_promotes(self):
        self.registry.register_candidate(actor=AGENT, model_id="future", version="1", manifest=PLAIN_MANIFEST,
                                         criteria=PromotionCriteria())
        self.rewrite_criteria("future", {**asdict(PromotionCriteria()), "require_nothing": True})
        with self.assertRaises(Exception):  # noqa: B017 - fails closed by TypeError, not by a failure tuple
            self.registry.promote(actor=REVIEWER, model_id="future", version="1", reason="unknown field")
        self.assertEqual(self.registry.get("future", "1")["status"], ModelStatus.CANDIDATE)


class CliRegistrationRegressionTests(unittest.TestCase):
    def run_cli(self, env, *argv):
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(list(argv))
        return code, json.loads(output.getvalue() or errors.getvalue())

    def test_register_after_the_holdout_was_scored_is_refused(self):
        with workspace_tempdir() as temp:
            env = {"ASTRA_DATABASE": str(Path(temp) / "state.sqlite3")}
            code, _ = self.run_cli(env, "evaluate-strategies", "--evaluate-holdout")
            self.assertEqual(code, 0)
            code, error = self.run_cli(env, "evaluate-strategies", "--evaluate-holdout", "--register",
                                       "--model-id", "late", "--version", "1")
            self.assertEqual(code, 1)
            self.assertIn("before its holdout is scored", error["reason"])
            code, models = self.run_cli(env, "models")
            self.assertEqual(models["models"], [])


# =============================================================================================
# Phase 2: re-attack of 9eae4a6 (windowed-correlation reuse detector, append-only tables).
# =============================================================================================

OLD_RETURNS = holdout_returns([bar.close for bar in BARS[DEVELOPMENT_END - 1:]])
RETURN_SD = (sum(value * value for value in OLD_RETURNS) / len(OLD_RETURNS)) ** 0.5


def rebuild(prefix, timestamps, returns, instrument="EURUSD.X"):
    """Append bars after ``prefix`` whose close-to-close log returns are exactly ``returns``."""

    bars = list(prefix)
    for timestamp, value in zip(timestamps, returns, strict=True):
        previous = bars[-1].close
        close = previous * math.exp(value)
        bars.append(MarketBar(instrument=instrument, timestamp=timestamp, open=previous,
                              high=max(previous, close) + 1e-5, low=min(previous, close) - 1e-5, close=close,
                              volume=100.0, bid=close - 4e-5, ask=close + 4e-5))
    return bars


def with_holdout_returns(returns, instrument="EURUSD.X"):
    """The sealed development bars (relabelled) followed by a holdout with exactly these returns."""

    return rebuild(relabel(BARS[:DEVELOPMENT_END], instrument), [bar.timestamp for bar in BARS[DEVELOPMENT_END:]],
                   returns, instrument)


def direct_plan(returns, instrument, year, identity):
    plan = {"dataset_identity_sha256": identity, "instrument": instrument,
            "holdout": {"start": f"{year}-01-01T00:00:00+00:00", "end": f"{year}-01-02T00:00:00+00:00",
                        "content_sha256": content_sha256(returns)}}
    return _plan_sha256(_canonical_json(plan)), plan


class CorrelationDetectorEvasionTests(_StoreCase):
    """Copies of the sealed holdout path that holdout_reuse.shares_price_path does not see.

    Copies are relabelled so the period check (same label only, once returns are stored) is skipped.
    """

    def setUp(self):
        super().setUp()
        self.seal_original()

    # FIXED IN LOT 4 ROUND 2 (was CONFIRMED DEFECT B-1): the same M15 prices resampled to M30 (2-bar aggregation). The new 40-bar
    # holdout contains every sealed holdout close, but its returns are sums of pairs, so no aligned
    # window reaches the r >= 0.94 threshold.
    def test_two_bar_aggregation_of_the_sealed_prices_is_refused(self):
        aggregated = [
            MarketBar(instrument="EURUSD.M30", timestamp=first.timestamp, open=first.open,
                      high=max(first.high, second.high), low=min(first.low, second.low), close=second.close,
                      volume=first.volume + second.volume, bid=second.bid, ask=second.ask)
            for first, second in zip(BARS[0::2], BARS[1::2], strict=True)
        ]
        self.assert_reuse_refused(aggregated)

    # FIXED IN LOT 4 ROUND 2 (was CONFIRMED DEFECT B-1): up-sampling. A bar inserted before every sealed holdout bar keeps every sealed
    # close at its own time, but each sealed return is split in two and nothing correlates.
    def test_interleaved_copy_of_the_sealed_holdout_is_refused(self):
        rng = random.Random(3)
        returns, stamps = [], []
        for bar, value in zip(BARS[DEVELOPMENT_END:], OLD_RETURNS, strict=True):
            noise = rng.gauss(0.0, RETURN_SD)
            returns += [noise, value - noise]
            stamps += [bar.timestamp - timedelta(minutes=7, seconds=30), bar.timestamp]
        bars = rebuild(relabel(BARS[:DEVELOPMENT_END]), stamps, returns)
        self.assert_reuse_refused(bars, protocol=replace(PROTOCOL, holdout_bars=2 * PROTOCOL.holdout_bars))

    # CONFIRMED DEFECT B-2: noise at 0.7x the return scale (the copy still correlates about 0.8 with the
    # sealed path) is never detected; at 0.4x / 0.5x detection is already 68% / 22% (scratch probe, 50 draws).
    @unittest.expectedFailure
    def test_copy_with_moderate_noise_is_refused(self):
        rng = random.Random(7)
        self.assert_reuse_refused(with_holdout_returns([v + rng.gauss(0.0, 0.7 * RETURN_SD) for v in OLD_RETURNS]))

    # FIXED IN LOT 4 ROUND 2 (was CONFIRMED DEFECT B-3): the sealed path played backwards is not compared; only forward alignments are tried.
    def test_time_reversed_copy_is_refused(self):
        self.assert_reuse_refused(with_holdout_returns([-value for value in reversed(OLD_RETURNS)]))

    # FIXED IN LOT 4 ROUND 2 (was CONFIRMED DEFECT B-4): a holdout of 7 bars (fewer returns than MIN_WINDOW = 8) is never compared,
    # so the sealed 40-bar holdout can be re-scored in 7-bar slices under new labels.
    def test_short_holdout_slice_of_the_sealed_holdout_is_refused(self):
        self.assert_reuse_refused(relabel(BARS[:-10]), protocol=replace(PROTOCOL, holdout_bars=7))

    # FIXED IN LOT 4 ROUND 2 (was CONFIRMED DEFECT B-4): half of the sealed holdout (20 consecutive returns) spliced into fresh data at
    # offset 5 is missed; runs shorter than about 1.5 windows are missed at most offsets (probe: 16 of 21).
    def test_half_of_the_sealed_holdout_spliced_into_fresh_data_is_refused(self):
        fresh = holdout_returns([bar.close for bar in make_synthetic_bars(41, seed=33)])
        self.assert_reuse_refused(with_holdout_returns([*fresh[:5], *OLD_RETURNS[3:23], *fresh[25:40]]))

    def test_regression_rebuilt_exact_copy_is_refused(self):
        # Sanity check of the helpers: the same returns rebuilt under another label are refused.
        self.assert_reuse_refused(with_holdout_returns(OLD_RETURNS))


class LegacyShiftTests(_StoreCase):
    # CONFIRMED DEFECT B-5 (low, inherent to legacy rows): a pre-Lot 4 row has no stored returns, so only
    # the period check protects it, and shifting the dates of the same prices is enough.
    @unittest.expectedFailure
    def test_time_shifted_copy_of_a_legacy_plan_is_refused(self):
        with overfitting_double():
            report = evaluate()
        legacy = json.loads(_canonical_json(report.plan_manifest))
        del legacy["holdout"]["content_sha256"]
        with contextlib.closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT INTO research_protocols VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_plan_sha256(_canonical_json(legacy)), report.dataset_identity_sha256, report.protocol_version,
                 "2020-01-01T00:00:00+00:00", legacy["holdout"]["start"], legacy["holdout"]["end"],
                 _canonical_json(legacy)),
            )
        self.assert_reuse_refused(shift(relabel(BARS)))


class DetectorFalsePositiveAndCostTests(_StoreCase):
    def seal(self, returns, instrument, year, identity, store=None):
        protocol_id, plan = direct_plan(returns, instrument, year, identity)
        (store or self.store).seal_research_protocol(protocol_id=protocol_id, dataset_identity_sha256=identity,
                                                     protocol_version="x", plan_manifest=plan, holdout_returns=returns)

    # FIXED IN LOT 4 ROUND 2 (was CONFIRMED DEFECT B-6): false positives on independent sparse data (90% zero returns: quiet sessions,
    # quantised quotes, managed or pegged pairs). The MAD is 0, the mean-abs fallback keeps the zeros at
    # the centre, and two windows that each hold one lone move at an alignable position correlate near 1.
    # Independent plans on other instruments and periods are refused (scratch probe: 19% of pairs).
    def test_independent_sparse_holdouts_are_not_refused(self):
        refused = 0
        for seed in range(20):
            store = BacktestStore(Path(self.database).with_name(f"pair{seed}.sqlite3"))  # one independent pair per store
            store.initialize()
            old, new = ([0.0 if rng.random() < 0.9 else rng.choice((-1.0, 1.0)) * 1e-4 for _ in range(40)]
                        for rng in (random.Random(seed), random.Random(seed + 1000)))
            self.seal(old, "OLD", 2030, "a" * 64, store)
            try:
                self.seal(new, "NEW", 2130, "b" * 64, store)
            except ValueError:
                refused += 1
        self.assertEqual(refused, 0)

    # FIXED IN LOT 4 ROUND 4 (was CONFIRMED DEFECT B-7): the comparison is quadratic in pure Python, for every
    # sealed plan. Phase 2: 1500 vs 1500 returns about 5 s inside BEGIN IMMEDIATE. Phase 3 (0ef781b): moved
    # before the write lock but about 12x slower (reversed, 2-bar aggregated, negated, match-share passes):
    # 1500 vs 1500 took 65 s. Round 4: the window correlations of one new window with every old window are
    # one big-integer product, and match-share offsets are grouped by a sort (no per-offset rescan). Still
    # quadratic (about 5 s for 1500 x 1500 on the QA machine), so it grows with every sealed plan.
    def test_sealing_a_long_holdout_against_a_long_sealed_one_is_fast(self):
        rng = random.Random(1)
        self.seal([round(rng.gauss(0.0, 1e-4), 12) for _ in range(500)], "AAA", 2030, "a" * 64)
        started = time.perf_counter()
        self.seal([round(rng.gauss(0.0, 1e-4), 12) for _ in range(500)], "BBB", 2031, "b" * 64)
        self.assertLess(time.perf_counter() - started, 1.0)

    def test_regression_independent_gaussian_holdouts_are_not_refused(self):
        rng = random.Random(5)
        for index in range(10):
            self.seal([rng.gauss(0.0, 1e-4) for _ in range(40)], f"I{index}", 2040 + index, f"{index:064d}")

    def test_regression_seal_requires_returns_matching_the_fingerprint(self):
        returns = [1e-4, -2e-4, 3e-4] * 10
        protocol_id, plan = direct_plan(returns, "AAA", 2030, "a" * 64)
        with self.assertRaisesRegex(ValueError, "do not match"):
            self.store.seal_research_protocol(protocol_id=protocol_id, dataset_identity_sha256="a" * 64,
                                              protocol_version="x", plan_manifest=plan, holdout_returns=[0.0] * 30)
        with self.assertRaisesRegex(ValueError, "content_sha256"):
            self.store.seal_research_protocol(protocol_id=protocol_id, dataset_identity_sha256="a" * 64,
                                              protocol_version="x", plan_manifest=plan)


class BackupRestoreTests(_StoreCase):
    OPERATOR = Actor("operator", Role.HUMAN_OPERATOR)

    def holdout(self, report):
        with overfitting_double():
            return evaluate_strategy_holdout_once(BARS, report=report, protocol=PROTOCOL, store=self.store)

    # CONFIRMED DEFECT B-8 (medium, documented residual): restoring a backup taken before the holdout was
    # scored brings the holdout back unspent. Triggers and the hash chain live in the same file and no
    # chain head is anchored outside it, so the same sealed plan is scored a second time.
    @unittest.expectedFailure
    def test_restoring_an_older_backup_does_not_unspend_the_holdout(self):
        report = self.seal_original()
        backup = create_backup(self.database, Path(self.database).parent / "backups")
        self.assertIsInstance(self.holdout(report), StrategyHoldoutEvaluation)
        restore_backup(backup.path, self.database, actor=self.OPERATOR, reason="drill")
        self.assertNotIsInstance(self.holdout(report), StrategyHoldoutEvaluation)

    def test_regression_triggers_survive_backup_restore_and_reinitialise(self):
        report = self.seal_original()
        self.holdout(report)
        backup = create_backup(self.database, Path(self.database).parent / "backups")
        restore_backup(backup.path, self.database, actor=self.OPERATOR, reason="drill")
        BacktestStore(self.database).initialize()
        ModelRegistry(self.database).initialize()
        self.assertTrue(AttemptLog(self.database).verify_chain().ok)
        with contextlib.closing(sqlite3.connect(self.database)) as db, self.assertRaises(sqlite3.DatabaseError):
            db.execute("DELETE FROM holdout_evaluations")
        self.assertIsInstance(self.holdout(report), dict)  # still spent: the backup was taken after scoring


# =============================================================================================
# Phase 3: review of 0ef781b (re-derivation at promotion, evidence anchor, reuse verdict policy).
# =============================================================================================


class _Phase3Case(_StoreCase):
    OPERATOR = Actor("operator", Role.HUMAN_OPERATOR)

    def anchored_store(self, name="anchor.jsonl"):
        anchor = LocalFileAnchor(Path(self.database).parent / name)
        return anchor, BacktestStore(self.database, anchor=anchor)

    def score(self, report, store):
        with overfitting_double():
            return evaluate_strategy_holdout_once(BARS, report=report, protocol=PROTOCOL, store=store)


class RederivationScopeTests(_Phase3Case):
    # FIXED IN LOT 4 ROUND 4 (was CONFIRMED DEFECT C-1, high): re-derivation covered only REDERIVED_FIELDS;
    # pbo, deflated_sharpe_probability and variants_tested were taken from the stored report. The holdout
    # attempt now records the report hash, the trial universe and the overfitting payload, and promotion
    # re-derives PBO and DSR from the bars with that universe; a report minted through the store API has no
    # such record, so the overfitting check counts as missing. Adapted for C-2: the real performance numbers
    # come from an honest scoring of a copy of the store, since rederive_holdout_evidence is no longer a
    # store-free oracle (the minted numbers are the same; the attack is unchanged).
    def test_minted_overfitting_statistics_are_detected(self):
        registry = ModelRegistry(self.database)
        registry.initialize()
        report = self.seal_original()
        selected = next(item for item in report.final_candidate_selection if item.selected)
        registry.register_candidate(
            actor=AGENT, model_id="m", version="1",
            manifest={"dataset_identity": {"canonical_sha256": report.dataset_identity_sha256},
                      "feature_set_version": "1", "code_sha256": report.implementation_sha256,
                      "params": selected.config, "plan_sha256": report.plan_sha256, "candidate_id": selected.candidate_id},
            criteria=PromotionCriteria(),
        )
        scratch = Path(self.database).parent / "scratch.sqlite3"
        shutil.copyfile(self.database, scratch)
        with overfitting_double():
            honest = evaluate_strategy_holdout_once(BARS, report=report, protocol=PROTOCOL,
                                                    store=BacktestStore(scratch))
        real = {"selected_candidate_id": honest.selected_candidate_id, **holdout_evidence(honest)}
        attempt = self.store.begin_holdout_evaluation(protocol_id=report.plan_sha256,
                                                      dataset_identity_sha256=report.dataset_identity_sha256)
        HoldoutAttempt.start(self.store, protocol_id=report.plan_sha256, plan=report.plan_manifest,
                             candidate_id=selected.candidate_id).finish({"selected_candidate": None}, None)
        minted = {
            "protocol_id": report.plan_sha256, "plan_sha256": report.plan_sha256,
            "dataset_identity_sha256": report.dataset_identity_sha256, "status": "COMPLETED",
            "selected_candidate_id": real["selected_candidate_id"], "selected_config": selected.config,
            "selected_candidate": {key: real[key] for key in ("trade_count", "net_pnl", "profit_factor", "max_drawdown_pct")},
            "edge_vs_baseline": real["edge_vs_baseline"],
            "cost_sensitivity": [{"cost_multiplier": 2.0, "selected_candidate": {"net_pnl": real["cost_stress_net_pnl"]}}],
            "overfitting": {"pbo": 0.0, "deflated_sharpe_probability": 1.0},
            "variants_tested": 1_000,
        }
        self.store.complete_holdout_evaluation(evaluation_id=attempt["evaluation_id"], report=minted)
        registry.record_evaluation(actor=AGENT, model_id="m", version="1", kind="holdout",
                                   evidence=holdout_evidence(minted), holdout_evaluation_id=attempt["evaluation_id"])
        failures = set(registry.check_promotion("m", "1", holdout_bars=BARS))
        self.assertNotIn("holdout_report_differs_from_rederivation", failures)  # the performance numbers are real
        detected = {"holdout_report_differs_from_rederivation", "deflated_sharpe_below_threshold",
                    "pbo_above_threshold", "overfitting_check_missing"}
        self.assertTrue(failures & detected, failures)

    # FIXED IN LOT 4 ROUND 4 (was CONFIRMED DEFECT C-2, medium): rederive_holdout_evidence was a public,
    # store-free holdout oracle for a sealed, never-scored plan. It now needs the store and a COMPLETED or
    # FAILED holdout evaluation of the plan, and refuses otherwise. The assertion is unchanged in substance
    # ("scored implies consumed"), applied to every way of calling it: a refusal scores nothing.
    def test_holdout_cannot_be_scored_without_consuming_it(self):
        report = self.seal_original()
        for kwargs in ({}, {"store": self.store}):
            with self.subTest(store="store" in kwargs):
                scored = None
                with contextlib.suppress(ValueError):
                    scored = rederive_holdout_evidence(BARS, report.plan_manifest, **kwargs)
                self.assertTrue(scored is None or self.store.holdout_for_protocol(report.plan_sha256) is not None)
                self.assertIsNone(scored)

    def test_regression_sma_plans_fail_closed_and_bars_are_required(self):
        self.assertEqual(_rederivation_failures({"protocol": {}}, {}, BARS), ["holdout_rederivation_unsupported_for_this_plan"])
        self.assertEqual(_rederivation_failures({}, {}, None), ["holdout_not_rederived_from_market_data"])

    def test_regression_other_bars_do_not_reproduce_the_plan(self):
        with overfitting_double():
            report = evaluate()
        with self.assertRaisesRegex(ValueError, "do not reproduce"):
            rederive_holdout_evidence(nudge(BARS, len(BARS) - 1), report.plan_manifest)


class AnchorTests(_Phase3Case):
    def test_regression_restore_is_refused_at_scoring_with_the_same_anchor(self):
        _anchor, store = self.anchored_store()
        with overfitting_double():
            report = evaluate(store=store)
        backup = create_backup(self.database, Path(self.database).parent / "backups")
        self.assertIsInstance(self.score(report, store), StrategyHoldoutEvaluation)
        restore_backup(backup.path, self.database, actor=self.OPERATOR, reason="drill")
        with self.assertRaisesRegex(ValueError, "inconsistent with the evidence anchor"):
            self.score(report, store)

    # FIXED IN LOT 4 ROUND 4 (was CONFIRMED DEFECT C-3, high with LocalFileAnchor): a store is bound to the
    # identity of the anchor it was first anchored to (evidence_anchor_bindings); an empty or other anchor is
    # refused for a used store (anchor_empty_for_used_store, anchor_identity_mismatch), so the restored store
    # cannot be re-scored under a fresh anchor. Promotion with a non-production anchor fails unless waived.
    def test_restore_then_fresh_anchor_does_not_unspend_the_holdout(self):
        _anchor, store = self.anchored_store()
        with overfitting_double():
            report = evaluate(store=store)
        backup = create_backup(self.database, Path(self.database).parent / "backups")
        self.score(report, store)
        restore_backup(backup.path, self.database, actor=self.OPERATOR, reason="drill")
        fresh, store = self.anchored_store("fresh-anchor.jsonl")
        second = None
        with contextlib.suppress(ValueError):
            second = self.score(report, store)
        self.assertNotIsInstance(second, StrategyHoldoutEvaluation)
        self.assertFalse(verify_store_against_anchor(self.database, fresh).ok and second is not None)

    # CONFIRMED DEFECT C-4 (medium): scoring does not require an anchor (require_anchor_consistency returns
    # when none is configured), so a holdout scored without one leaves no external trace; after a restore it
    # is scored again under an anchor and looks first-time (B-8 is only closed when an anchor was present).
    # LOT 4 ROUND 4: MITIGATED, KEPT AS EXPECTED FAILURE BY DESIGN. Scoring without an anchor stays allowed
    # (exploration, tests, P-10 relies on it) but is fail-closed for promotion: the report and the hash-chained
    # holdout attempt record anchored_before_scoring=False (holdout_scored_without_anchor, never promotable),
    # and a store that already holds sealed plans or holdout evaluations can never be anchored afterwards
    # (anchor_empty_for_used_store), so the "scored again under an anchor, looks first-time" path is closed.
    # With an anchor, the STARTED attempt is anchored before scoring (holdout_not_anchored_before_scoring).
    @unittest.expectedFailure
    def test_holdout_cannot_be_scored_without_an_anchor(self):
        with overfitting_double():
            report = evaluate(store=self.store)
        with self.assertRaises(ValueError):
            self.score(report, self.store)


class ReuseVerdictPolicyTests(_Phase3Case):
    def sparse(self, seed):
        rng = random.Random(seed)
        return [0.0 if rng.random() < 0.9 else rng.choice((-1.0, 1.0)) * 1e-4 for _ in range(40)]

    def seal_direct(self, returns, instrument, year, identity):
        protocol_id, plan = direct_plan(returns, instrument, year, identity)
        self.store.seal_research_protocol(protocol_id=protocol_id, dataset_identity_sha256=identity,
                                          protocol_version="x", plan_manifest=plan, holdout_returns=returns)
        return protocol_id

    # FIXED IN LOT 4 ROUND 4 (was CONFIRMED DEFECT C-5, high, permanent denial of service): one sealed plan
    # whose holdout is mostly flat made every later, unrelated plan UNDETERMINED (no comparable window on the
    # old side), and UNDETERMINED blocks scoring and promotion. UNDETERMINED now depends on the new holdout
    # only; an uninformative old holdout is recorded as old_holdout_uninformative and compared on its moves.
    def test_one_flat_sealed_holdout_does_not_block_unrelated_plans(self):
        self.seal_direct(self.sparse(1), "FLAT", 2030, "a" * 64)
        rng = random.Random(9)
        normal = self.seal_direct([rng.gauss(0.0, 1e-4) for _ in range(40)], "LIVELY", 2031, "b" * 64)
        self.assertEqual(self.store.reuse_check(normal)["verdict"], "CLEAR")

    # FIXED IN LOT 4 ROUND 4 (was CONFIRMED DEFECT C-6, medium, false positive moved, not removed): independent
    # sparse holdouts were no longer refused at seal (B-6 test passes) but were sealed UNDETERMINED, so they
    # could never be scored. Sparse paths are now compared on their moves and positions (match share over the
    # whole overlap, judged against the chance match rate).
    def test_independent_sparse_holdouts_can_be_scored(self):
        self.seal_direct(self.sparse(2), "OLD", 2030, "a" * 64)
        new = self.seal_direct(self.sparse(1002), "NEW", 2130, "b" * 64)
        self.assertEqual(self.store.reuse_check(new)["verdict"], "CLEAR")

    def test_regression_undetermined_plan_cannot_be_scored_and_reseal_keeps_the_verdict(self):
        self.seal_direct(self.sparse(1), "FLAT", 2030, "a" * 64)
        rng = random.Random(9)
        returns = [rng.gauss(0.0, 1e-4) for _ in range(40)]
        normal = self.seal_direct(returns, "LIVELY", 2031, "b" * 64)
        verdict = self.store.reuse_check(normal)["verdict"]
        self.assertIn(verdict, ("UNDETERMINED", "SUSPECT", "CLEAR"))
        if verdict != "CLEAR":
            with self.assertRaisesRegex(ValueError, "independent holdout"):
                self.store.begin_holdout_evaluation(protocol_id=normal, dataset_identity_sha256="b" * 64)
        self.seal_direct(returns, "LIVELY", 2031, "b" * 64)  # idempotent re-seal
        self.assertEqual(self.store.reuse_check(normal)["verdict"], verdict)

    def test_regression_recorded_policy_looser_than_code_is_flagged(self):
        payload = asdict(HOLDOUT_REUSE_POLICY)
        self.assertEqual(reuse_policy_looser_than_floor(payload), ())
        for name, value in (("conflict_abs_correlation", 0.999), ("min_holdout_returns", 8), ("match_tolerance", 0.01),
                            ("suspect_abs_correlation", True), ("clip", None)):
            with self.subTest(name):
                self.assertIn(name, reuse_policy_looser_than_floor({**payload, name: value}))


# =============================================================================================
# Phase 4: re-review of f1e0f76 (round 4 fixes). Ids A4-D-n.
# =============================================================================================


def _ar1(seed, n=60, phi=0.0, sd=1e-4):
    rng, value, out = random.Random(seed), 0.0, []
    for _ in range(n):
        value = phi * value + rng.gauss(0.0, sd)
        out.append(value)
    return out


class Phase4EvidenceTests(_Phase3Case):
    def scratch_copy(self, name="scratch.sqlite3"):
        scratch = Path(self.database).parent / name
        shutil.copyfile(self.database, scratch)
        return BacktestStore(scratch)  # no anchor: ASTRA_EVIDENCE_ANCHOR is not set in tests

    # CONFIRMED DEFECT A4-D-1 (medium): the C-2 fix duck-types the store: any object with a
    # holdout_for_protocol() answering {"status": "COMPLETED"} turns rederive_holdout_evidence back into a
    # store-free oracle for a sealed, never-scored plan.
    @unittest.expectedFailure
    def test_rederivation_cannot_be_fooled_by_a_stub_store(self):
        report = self.seal_original()
        stub = types.SimpleNamespace(holdout_for_protocol=lambda _protocol_id: {"status": "COMPLETED"})
        scored = None
        with contextlib.suppress(ValueError):
            scored = rederive_holdout_evidence(BARS, report.plan_manifest, store=stub)
        self.assertIsNone(scored)
        self.assertIsNone(self.store.holdout_for_protocol(report.plan_sha256))

    # CONFIRMED DEFECT A4-D-2 (medium): peek by file copy. The holdout of an anchored store is scored in an
    # unanchored copy of the file (allowed: C-4 keeps unanchored scoring for exploration), the result is seen,
    # and the original store then scores the same holdout as if for the first time, anchored before scoring
    # and therefore promotable. The integrator's own C-1 test adaptation uses exactly this copy. Neither the
    # anchor nor the original attempt log can know about the copy.
    @unittest.expectedFailure
    def test_holdout_peeked_in_a_copy_of_the_store_cannot_be_scored_again(self):
        _anchor, store = self.anchored_store()
        with overfitting_double():
            report = evaluate(store=store)
        self.assertIsInstance(self.score(report, self.scratch_copy()), StrategyHoldoutEvaluation)  # the peek
        second = None
        with contextlib.suppress(ValueError):
            second = self.score(report, store)
        self.assertNotIsInstance(second, StrategyHoldoutEvaluation)

    # CONFIRMED DEFECT A4-D-3 (medium): new store + new anchor. The binding stops swapping the anchor of a used
    # store, but nothing links a fresh database file to the anchors that already recorded the holdout as
    # consumed: the same bars and plan are sealed, anchored before scoring and scored again. The non-production
    # waiver at promotion is the only remaining trace, and a production adapter would need a global registry.
    @unittest.expectedFailure
    def test_same_holdout_cannot_be_rescored_in_a_new_store_with_a_new_anchor(self):
        _anchor, store = self.anchored_store()
        with overfitting_double():
            report = evaluate(store=store)
        self.score(report, store)
        other_anchor = LocalFileAnchor(Path(self.database).parent / "other-anchor.jsonl")
        other = BacktestStore(Path(self.database).parent / "other.sqlite3", anchor=other_anchor)
        other.initialize()
        second = None
        with contextlib.suppress(ValueError):
            with overfitting_double():
                again = evaluate(store=other)
            second = self.score(again, other)
        self.assertNotIsInstance(second, StrategyHoldoutEvaluation)

    # CONFIRMED DEFECT A4-D-6 (low, fail-closed lock-out): a crash between anchor.append() and
    # _record_binding() on the first anchoring leaves an anchor with a checkpoint and an unbound store; every
    # later seal or scoring is refused for good (store_not_bound_to_anchor) and nothing can repair it.
    @unittest.expectedFailure
    def test_crash_between_anchor_append_and_binding_is_recoverable(self):
        _anchor, store = self.anchored_store()
        with patch("astra.anchoring._record_binding", side_effect=OSError("crash")), contextlib.suppress(OSError):
            with overfitting_double():
                evaluate(store=store)
        try:
            with overfitting_double():
                report = evaluate(store=store)
        except ValueError as exc:
            self.fail(f"store locked out after the crash: {exc}")
        self.assertIsNotNone(store.reuse_check(report.plan_sha256))

    def test_regression_full_record_mint_with_forged_overfitting_is_detected(self):
        """C-1 holds even when the minted attempt carries a complete record (report hash, logged universe)."""

        registry = ModelRegistry(self.database)
        registry.initialize()
        report = self.seal_original()
        selected = next(item for item in report.final_candidate_selection if item.selected)
        registry.register_candidate(
            actor=AGENT, model_id="m", version="1",
            manifest={"dataset_identity": {"canonical_sha256": report.dataset_identity_sha256},
                      "feature_set_version": "1", "code_sha256": report.implementation_sha256,
                      "params": selected.config, "plan_sha256": report.plan_sha256, "candidate_id": selected.candidate_id},
            criteria=PromotionCriteria(),
        )
        honest = asdict(self.score(report, self.scratch_copy()))
        attempt = self.store.begin_holdout_evaluation(protocol_id=report.plan_sha256,
                                                      dataset_identity_sha256=report.dataset_identity_sha256)
        universe = _store_trial_universe(self.store, report.plan_sha256, report.overfitting.bar_seconds, None)
        recorder = HoldoutAttempt.start(self.store, protocol_id=report.plan_sha256, plan=report.plan_manifest,
                                        candidate_id=selected.candidate_id,
                                        holdout_returns=OLD_RETURNS)
        forged = {**honest, "overfitting": {**honest["overfitting"], "pbo": 0.0, "deflated_sharpe_probability": 1.0},
                  "evidence_anchor": {"anchored_before_scoring": True}}
        digest = self.store.complete_holdout_evaluation(evaluation_id=attempt["evaluation_id"], report=forged)
        recorder.finish({**holdout_evidence(forged), "evaluation_id": attempt["evaluation_id"], "report_sha256": digest,
                         "trial_universe": trial_universe_payload(universe),
                         "overfitting": overfitting_payload(forged["overfitting"])}, None)
        registry.record_evaluation(actor=AGENT, model_id="m", version="1", kind="holdout",
                                   evidence=holdout_evidence(forged), holdout_evaluation_id=attempt["evaluation_id"])
        failures = registry.check_promotion("m", "1", holdout_bars=BARS)
        self.assertIn("holdout_overfitting_differs_from_rederivation", failures)
        self.assertIn("overfitting_check_missing", failures)


class Phase4DetectorTests(ReuseVerdictPolicyTests):
    def assert_not_cleared(self, returns):
        # Not CLEAR: refused at seal (CONFLICT) or sealed with a blocking verdict (SUSPECT, UNDETERMINED).
        # Round 5: a refusal is the stronger outcome, so it counts as not cleared (the round 4 version of
        # these tests treated the refusal's ValueError as a failure).
        try:
            protocol_id = self.seal_direct(returns, "NEW", 2130, "b" * 64)
        except ValueError as error:
            self.assertIn("price path matches", str(error))
            return
        self.assertNotEqual(self.store.reuse_check(protocol_id)["verdict"], "CLEAR")

    # FIXED IN LOT 4 ROUND 5 (was CONFIRMED DEFECT A4-D-4, medium): prewhitening created false negatives on
    # persistent paths. For an AR(1) holdout with phi = 0.95, a copy with noise at 0.2x the return scale was
    # CLEAR (20/20 in a probe), while the same noise on an i.i.d. path is CONFLICT (16/20). Prewhitening no
    # longer decides alone: a raw correlation is also confirmed when the fit residual is not serially
    # correlated (a noisy copy leaves white noise), and the whole overlap of each offset is compared too.
    def test_noisy_copy_of_a_persistent_holdout_is_not_clear(self):
        old = _ar1(3, phi=0.95)
        scale = (sum(value * value for value in old) / len(old)) ** 0.5
        rng = random.Random(0)
        self.seal_direct(old, "OLD", 2030, "a" * 64)
        self.assert_not_cleared([value + rng.gauss(0.0, 0.2 * scale) for value in old])

    # FIXED IN LOT 4 ROUND 5 (was CONFIRMED DEFECT A4-D-5, medium): sparse paths were compared only by match
    # share with an absolute tolerance, so a sparse holdout whose moves are all amplified 1.5x (same bars, same
    # timing) was CLEAR. A sparse new holdout is now also compared on the timing and direction of its moves.
    def test_amplified_copy_of_a_sparse_holdout_is_not_clear(self):
        rng = random.Random(5)
        old = [0.0 if rng.random() < 0.9 else rng.choice((-1.0, 1.0)) * 1e-4 for _ in range(60)]
        self.seal_direct(old, "OLD", 2030, "a" * 64)
        self.assert_not_cleared([1.5 * value for value in old])

    def test_regression_dense_copies_and_sparse_exact_copies_are_refused(self):
        old = _ar1(4)
        self.seal_direct(old, "OLD", 2030, "a" * 64)
        for index, new in enumerate(([2.0 * v for v in old], [v + 1e-4 for v in old], [-v for v in reversed(old)])):
            with self.subTest(index), self.assertRaises(ValueError):
                self.seal_direct(new, f"N{index}", 2100 + index, f"{index:064d}")


if __name__ == "__main__":
    unittest.main()
