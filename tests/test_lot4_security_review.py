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
import sqlite3
import sys
import unittest
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import accept_unverified_evidence, workspace_tempdir
from astra.cli import main
from astra.governance import Actor, Role
from astra.registry import (
    ModelRegistry,
    ModelStatus,
    PromotionCriteria,
    PromotionError,
    _canonical,
    _sha256,
)
from astra.storage import BacktestStore
from astra.strategy_validation import holdout_evidence
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
    """A-4: public BacktestStore methods can mint a COMPLETED holdout with any report."""

    # CONFIRMED DEFECT A-4: no SQL is written by hand. seal/begin/complete accept any plan and any
    # report without an actor, and verify_holdout_provenance trusts the stored report, so made-up
    # numbers pass every gate and a reviewer can promote them.
    @unittest.expectedFailure
    def test_holdout_minted_through_the_store_api_cannot_promote(self):
        registry = ModelRegistry(self.database)  # default (strict) verifier
        registry.initialize()
        plan = {
            "dataset_identity_sha256": "D" * 64,
            "implementation_sha256": "C" * 64,
            "instrument": "XXXYYY",
            "holdout": {"start": "2040-01-01T00:00:00+00:00", "end": "2040-02-01T00:00:00+00:00"},
        }
        protocol_id = _plan_sha256(_canonical_json(plan))
        params = {"family": "sma", "fast": 5, "slow": 13}
        registry.register_candidate(
            actor=AGENT, model_id="minted", version="1",
            manifest={"dataset_identity": {"canonical_sha256": "D" * 64}, "feature_set_version": "1",
                      "code_sha256": "C" * 64, "params": params, "plan_sha256": protocol_id, "candidate_id": "cand"},
            criteria=PromotionCriteria(),
        )
        self.store.seal_research_protocol(protocol_id=protocol_id, dataset_identity_sha256="D" * 64,
                                          protocol_version="x", plan_manifest=plan)
        attempt = self.store.begin_holdout_evaluation(protocol_id=protocol_id, dataset_identity_sha256="D" * 64)
        report = {
            "status": "COMPLETED", "selected_candidate_id": "cand", "selected_config": params,
            "selected_candidate": {"trade_count": 500, "net_pnl": 9_000.0, "profit_factor": 4.0, "max_drawdown_pct": 1.0},
            "edge_vs_baseline": 8_000.0,
            "cost_sensitivity": [{"cost_multiplier": 2.0, "selected_candidate": {"net_pnl": 7_000.0}}],
            "overfitting": {"pbo": 0.01, "deflated_sharpe_probability": 0.999},
            "variants_tested": 7,
        }
        self.store.complete_holdout_evaluation(evaluation_id=attempt["evaluation_id"], report=report)
        registry.record_evaluation(actor=AGENT, model_id="minted", version="1", kind="holdout",
                                   evidence=holdout_evidence(report), holdout_evaluation_id=attempt["evaluation_id"])
        self.assertNotEqual(registry.check_promotion("minted", "1"), ())


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


if __name__ == "__main__":
    unittest.main()
