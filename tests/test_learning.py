"""Continuous improvement: windows, candidates, promotion criteria, managed paper accounts, rollback, worker."""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from _support import workspace_tempdir
from astra import learning
from astra.jobs import Worker
from astra.marketdata import KIND_REFERENCE, Candle, CandleStore
from astra.papertrade import BUY, FLAT, SELL, LabStore


def daily_series(symbol="EURUSD", years=6, start=datetime(2019, 1, 1, 13, 15, tzinfo=UTC)):
    candles, price, day = [], 1.10, start
    for i in range(int(years * 365)):
        day = start + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        price *= 1 + 0.004 * ((i // 37) % 2 * 2 - 1) + 0.001 * ((i * 7919) % 13 - 6) / 6
        candles.append(Candle(symbol, "D1", day, None, None, None, round(price, 6), None, "ECB_EXR", KIND_REFERENCE))
    return candles


class LearningCase(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.candles, self.lab, self.store = CandleStore(self.db), LabStore(self.db), learning.LearningStore(self.db)
        for s in (self.candles, self.lab, self.store):
            s.initialize()
        self.candles.register_source("ECB_EXR", "ECB_SDMX_API", "REFERENCE", "test")
        self.candles.store(daily_series(), "REFERENCE", None)

    def tearDown(self):
        self.temp.cleanup()


class UnitTests(LearningCase):
    def test_selection_windows_never_touch_the_final_check(self):
        first, last = datetime(2015, 1, 2, tzinfo=UTC), datetime(2026, 10, 27, tzinfo=UTC)
        selection, check = learning.windows_for(first, last)
        self.assertEqual(selection[0][0], datetime(2018, 1, 1, tzinfo=UTC))
        self.assertTrue(all(end < check[0] for _, end in selection))
        self.assertEqual((check[1] - check[0]).days, learning.FINAL_CHECK_DAYS)

    def test_candidates_are_deterministic_valid_and_never_the_champion(self):
        champion = learning.default_params()
        first, again = learning.candidates(42, champion), learning.candidates(42, champion)
        self.assertEqual(first, again)
        self.assertEqual(len(first), learning.CANDIDATES_PER_RUN)
        self.assertTrue(all(c["strategy"]["fast"] < c["strategy"]["slow"] for c in first))
        self.assertNotIn(champion, first)
        self.assertNotEqual(first, learning.candidates(43, champion))

    def test_a_run_records_every_candidate_and_keeps_the_champion_without_evidence(self):
        result = learning.learn(self.candles, self.store, "EURUSD")
        run = self.store.runs(1)[0]
        self.assertEqual((run["status"], run["decision"]), ("DONE", result["decision"]))
        self.assertEqual(len(run["candidates"]), learning.CANDIDATES_PER_RUN + 1)
        self.assertEqual(self.store.trials("EURUSD"), learning.CANDIDATES_PER_RUN + 1)
        self.assertIn("criteria", run["evidence"])
        if result["decision"] == "KEPT":
            self.assertEqual(self.store.champion("EURUSD")["version"], 1)

    def test_promotion_needs_every_criterion(self):
        def fake(scores):
            def evaluate(candles, symbol, params, windows, cost_factor=1.0, purpose="selection", profile=None, **_):
                key = {"selection": "selection", "final_check": "check", "cost_stress": "stress"}[purpose]
                return dict(scores[key](params))
            return evaluate

        good = {"windows": 1, "score": 3.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 500.0}
        champ = {"windows": 1, "score": 1.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 100.0}
        pick = lambda p: champ if p == self.store.champion("EURUSD")["params"] else good  # noqa: E731
        with mock.patch.object(learning, "evaluate", fake({"selection": pick, "check": pick, "stress": pick})):
            self.assertEqual(learning.learn(self.candles, self.store, "EURUSD")["decision"], "PROMOTED")
        self.assertEqual(self.store.champion("EURUSD")["version"], 2)
        promoted = self.store.champion("EURUSD")
        self.assertEqual(promoted["params"]["tier"], "PROBATOIRE")  # no deflated Sharpe here: never full size at once
        self.assertIn("probatoire", promoted["reason"])
        losing = {**good, "net_pnl": -1.0}
        with mock.patch.object(learning, "evaluate", fake({"selection": pick, "check": pick, "stress": lambda p: losing})):
            result = learning.learn(self.candles, self.store, "EURUSD")
        self.assertEqual(result["decision"], "KEPT")
        self.assertIn("coûts doublés", result["reason"])
        deep = {**good, "worst_drawdown_pct": 40.0}
        with mock.patch.object(learning, "evaluate", fake({"selection": lambda p: champ if p == self.store.champion("EURUSD")["params"] else deep,
                                                          "check": pick, "stress": pick})):
            self.assertIn("drawdown", learning.learn(self.candles, self.store, "EURUSD")["reason"])

    def test_a_stress_test_that_cannot_be_evaluated_refuses_the_promotion_instead_of_failing(self):
        good = {"windows": 1, "score": 3.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 500.0}
        champ = {"windows": 1, "score": 1.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 100.0}
        empty = {"windows": 0, "score": None, "trades": 0, "window_errors": 3, "first_error": "spread hors limites"}

        def evaluate(candles, symbol, params, windows, cost_factor=1.0, purpose="selection", profile=None, **_):
            if purpose == "cost_stress":
                return dict(empty)
            return dict(champ if params == self.store.champion("EURUSD")["params"] else good)

        with mock.patch.object(learning, "evaluate", evaluate):
            result = learning.learn(self.candles, self.store, "EURUSD")
        self.assertEqual(result["decision"], "KEPT")
        self.assertIn("aucune fenêtre évaluable", result["reason"])
        self.assertIn("spread hors limites", result["reason"])

    def test_windows_that_cannot_be_simulated_are_reported(self):
        params = learning.default_params()
        series = self.candles.candles("EURUSD", "D1", "ECB_EXR")
        window = [(series[-300].timestamp, series[-50].timestamp)]
        result = learning.evaluate(self.candles, "EURUSD", params, window, cost_factor=10_000.0)  # absurd costs
        self.assertEqual((result["windows"], result["window_errors"]), (0, 1))
        self.assertIn("spread", result["first_error"])
        normal = learning.evaluate(self.candles, "EURUSD", params, window)
        self.assertEqual(normal["windows"], 1)
        self.assertNotIn("window_errors", normal)

    def test_a_promotion_reaches_the_observation_account_and_never_the_demo_feed_until_a_person_authorizes_it(self):
        created = learning.ensure_managed_sessions(self.candles, self.store, self.lab)
        self.assertEqual(len(created), 1)  # only EURUSD has data here
        self.assertEqual(learning.ensure_managed_sessions(self.candles, self.store, self.lab), [])  # never twice
        shadow = learning.ensure_shadow_sessions(self.candles, self.store, self.lab)
        self.assertEqual(len(shadow), 1)
        self.assertEqual(learning.ensure_shadow_sessions(self.candles, self.store, self.lab), [])
        feed_id, shadow_id = self.store.managed_session("EURUSD"), self.store.shadow_session("EURUSD")
        self.assertNotEqual(feed_id, shadow_id)
        self.lab.step_session(feed_id, self.candles)
        before = self.lab.session(feed_id)
        new = {"strategy": {"fast": 10, "slow": 80, "atr_window": 20, "min_atr_pct": 0.0, "max_atr_pct": 0.03},
               "stop_atr_multiple": 3.0}
        self.store.promote("EURUSD", new, None, "test")
        self.assertTrue(learning.apply_champion(self.store, self.lab, "EURUSD"))
        observed, feed = self.lab.session(shadow_id), self.lab.session(feed_id)
        self.assertEqual(observed["params"]["strategy"]["fast"], 10)  # the observation account follows the champion
        self.assertEqual(observed["params"]["risk"]["stop_atr_multiple"], 3.0)
        self.assertIn("NOUVEAU_CHAMPION", [e["kind"] for e in observed["events"]])
        self.assertEqual(feed["params"], before["params"])  # the account that feeds the DEMO bridge did not move
        self.assertEqual(feed["account"], before["account"])
        with self.assertRaises(learning.SimulationError):
            learning.authorize_demo(self.store, self.lab, "EURUSD", "", "raison")  # a person and a reason are required
        self.assertTrue(learning.authorize_demo(self.store, self.lab, "EURUSD", "test-op", "essai"))
        self.assertEqual(self.lab.session(feed_id)["params"]["strategy"]["fast"], 10)
        audit = [e for e in self.store.events("EURUSD") if e["kind"] == "AUTORISATION_DEMO"]
        self.assertEqual((audit[0]["detail"]["operator"], audit[0]["detail"]["champion_version"]), ("test-op", 2))
        self.assertEqual(self.store.rollback("EURUSD"), 3)
        self.assertEqual(self.store.champion("EURUSD")["params"], learning.default_params())


def hourly_series(symbol="EURUSD", source="MASSIVE_FX_QUOTES", start=datetime(2024, 10, 29, tzinfo=UTC), bars=12000, end=None):
    """Weekday hourly bars with trending and ranging stretches; ``end`` fixes the last bar instead of the first."""

    from astra.marketdata import KIND_OHLC

    out, price, moment, i = [], 1.10, start, 0
    while len(out) < bars:
        moment = start + timedelta(hours=i)
        i += 1
        if moment.weekday() >= 5:
            continue
        price *= 1 + 0.0004 * ((i // 90) % 2 * 2 - 1) + 0.0003 * ((i * 7919) % 13 - 6) / 6
        out.append(Candle(symbol, "H1", moment, price, price + 0.0004, price - 0.0004, round(price, 6), 100.0, source, KIND_OHLC))
    if end is not None:
        shift = end - out[-1].timestamp
        out = [Candle(c.symbol, c.timeframe, c.timestamp + shift, c.open, c.high, c.low, c.close, c.volume, c.source, c.kind)
               for c in out]
    return out


class DeflationAndGraduationTests(LearningCase):
    STRONG = [0.02, 0.018, 0.022, 0.019, 0.021, -0.001] * 10
    WEAK = [0.004, -0.003, 0.002, -0.004, 0.003, -0.001] * 10

    def test_the_deflated_sharpe_probability_falls_as_the_number_of_trials_grows(self):
        results = [{"selection": {"returns": self.STRONG}}, {"selection": {"returns": self.WEAK}},
                   {"selection": {"returns": list(reversed(self.WEAK))[::2]}}]
        best = results[0]
        few = learning._deflated(results, best, 1)
        many = learning._deflated(results, best, 10_000)
        self.assertGreater(few["probability"], many["probability"])
        self.assertTrue(0 <= many["probability"] <= few["probability"] <= 1)
        self.assertEqual(few["n_trades"], len(self.STRONG))

    def test_too_few_returns_give_no_deflated_sharpe(self):
        self.assertIsNone(learning._deflated([{"selection": {"returns": [0.01]}}], {"selection": {"returns": [0.01]}}, 5))

    def test_the_review_criteria_are_derived_from_the_candidate_own_selection_trades(self):
        import math
        import statistics

        review = learning.preregistered_review(self.STRONG)
        self.assertEqual((review["min_obs"], review["z_confirm"], review["z_suspend"]),
                         (learning.REVIEW_MIN_OBS, learning.Z_CONFIRM, learning.Z_SUSPEND))
        mu, sigma = statistics.fmean(self.STRONG), statistics.stdev(self.STRONG)
        self.assertEqual(review["n_required"], max(learning.REVIEW_MIN_OBS, math.ceil((1.645 * sigma / (0.5 * mu)) ** 2)))
        self.assertIsNone(learning.preregistered_review([-0.01, -0.02, -0.01, 0.0])["n_required"])  # no edge to confirm
        self.assertIsNone(learning.preregistered_review([0.01])["n_required"])

    def promote_with_review(self, returns):
        self.store.champion("EURUSD")
        review = learning.preregistered_review(returns)
        self.store.promote("EURUSD", {**learning.default_params(), "tier": "PROBATOIRE", "review": review}, 5, "test")
        self.store.set_shadow_session("EURUSD", 1)
        return review

    def lab_with(self, trades):
        lab = mock.Mock()
        lab.session.return_value = {"trades": trades, "account": {"initial_capital": 10_000.0}}
        return lab

    @staticmethod
    def trades(values):
        return [{"exit_ts": "2999-01-01T00:00:00+00:00", "net_pnl": v} for v in values]

    def test_a_clearly_negative_prospective_record_suspends_the_champion_and_is_audited(self):
        self.promote_with_review(self.STRONG)
        lab = self.lab_with(self.trades([-50.0, -40.0, -60.0, -45.0, -55.0, -42.0, -58.0, -47.0, -52.0]))
        self.assertEqual(learning.review_live(self.store, lab, "EURUSD"), "SUSPENDED")
        self.assertEqual(self.store.champion("EURUSD")["params"], learning.default_params())
        kinds = [e["kind"] for e in self.store.events("EURUSD")]
        self.assertIn("SUSPENSION", kinds)
        self.assertIn("RETOUR_ARRIERE", kinds)

    def test_nothing_is_decided_below_the_minimum_observations_or_without_a_registered_review(self):
        self.promote_with_review(self.STRONG)
        few = self.lab_with(self.trades([-50.0, -40.0, -60.0]))
        self.assertIsNone(learning.review_live(self.store, few, "EURUSD"))
        self.assertEqual(self.store.champion("EURUSD")["version"], 2)  # untouched
        self.store.promote("EURUSD", {**learning.default_params(), "tier": "PROBATOIRE"}, 6, "sans revue")
        many = self.lab_with(self.trades([-50.0, -40.0, -60.0, -45.0, -55.0, -42.0, -58.0, -47.0, -52.0]))
        self.assertIsNone(learning.review_live(self.store, many, "EURUSD"))  # no pre-registered criteria: no verdict

    def test_enough_good_prospective_trades_confirm_the_champion_once_without_touching_the_demo_feed(self):
        review = self.promote_with_review(self.STRONG)
        needed = max(review["n_required"], learning.REVIEW_MIN_OBS)
        lab = self.lab_with(self.trades([100.0 + (i % 5) for i in range(needed)]))
        self.assertEqual(learning.review_live(self.store, lab, "EURUSD"), "CONFIRMED")
        champion = self.store.champion("EURUSD")
        self.assertEqual((champion["params"]["review_status"], champion["run_id"]), ("CONFIRME", 5))
        self.assertIsNone(learning.review_live(self.store, lab, "EURUSD"))  # nothing left to confirm
        self.assertIn("CONFIRMATION_PROSPECTIVE", [e["kind"] for e in self.store.events("EURUSD")])
        lab.update_session_strategy.assert_not_called()  # no account changed

    def test_trades_before_the_promotion_are_not_counted(self):
        self.promote_with_review(self.STRONG)
        old = [{"exit_ts": "2000-01-01T00:00:00+00:00", "net_pnl": -50.0}] * 20
        self.assertIsNone(learning.review_live(self.store, self.lab_with(old), "EURUSD"))
        self.assertEqual(self.store.champion("EURUSD")["version"], 2)

    def test_a_run_registers_its_candidates_and_data_identity_before_evaluating_anything(self):
        seen = []

        def evaluate(candles, symbol, params, windows, cost_factor=1.0, purpose="selection", profile=None, **_):
            if not seen:
                seen.append([e["kind"] for e in self.store.events("EURUSD")])
            return {"windows": 1, "score": 1.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 100.0}

        with mock.patch.object(learning, "evaluate", evaluate):
            learning.learn(self.candles, self.store, "EURUSD")
        self.assertEqual(seen[0], ["CANDIDATS_ENREGISTRES"])
        registered = next(e for e in self.store.events("EURUSD") if e["kind"] == "CANDIDATS_ENREGISTRES")
        detail = registered["detail"]
        self.assertEqual(len(detail["data"]["sha256"]), 64)
        self.assertEqual((detail["data"]["nature"], detail["data"]["source"]), ("REFERENCE", "ECB_EXR"))
        self.assertEqual(detail["criteria"]["dsr_valid"], learning.DSR_VALID)
        self.assertEqual(len(detail["pool_sha256"]), 64)
        run = self.store.runs(1)[0]
        self.assertEqual(run["data"]["sha256"], detail["data"]["sha256"])  # the run stores the same identity

    def test_every_verdict_and_failure_leaves_an_audit_line(self):
        def evaluate(candles, symbol, params, windows, cost_factor=1.0, purpose="selection", profile=None, **_):
            return {"windows": 1, "score": 1.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 100.0}

        with mock.patch.object(learning, "evaluate", evaluate):
            learning.learn(self.candles, self.store, "EURUSD")
        self.assertIn("REFUS", [e["kind"] for e in self.store.events("EURUSD")])  # no candidate beat the champion by enough
        short = CandleStore(Path(self.temp.name) / "short.sqlite3")
        short.initialize()
        other = learning.LearningStore(Path(self.temp.name) / "short.sqlite3")
        other.initialize()
        with self.assertRaises(learning.SimulationError):
            learning.learn(short, other, "EURUSD")
        self.assertEqual(other.events("EURUSD")[0]["kind"], "ECHEC")


class HourlyProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.candles, self.lab = CandleStore(self.db), LabStore(self.db)
        self.daily, self.hourly = learning.LearningStore(self.db), learning.LearningStore(self.db, learning.H1_PROFILE)
        for s in (self.candles, self.lab, self.daily, self.hourly):
            s.initialize()
        self.candles.register_source("MASSIVE_FX_QUOTES", "MASSIVE_REST", "HISTORICAL", "test")

    def tearDown(self):
        self.temp.cleanup()

    def test_hourly_windows_walk_back_from_the_final_check_and_never_touch_it(self):
        first, last = datetime(2024, 10, 29, tzinfo=UTC), datetime(2026, 11, 8, 23, tzinfo=UTC)
        selection, check = learning.windows_for(first, last, learning.H1_PROFILE)
        self.assertGreaterEqual(len(selection), learning.H1_MIN_WINDOWS)
        self.assertEqual((check[1] - check[0]).days, learning.H1_FINAL_CHECK_DAYS)
        self.assertTrue(all(end < check[0] for _, end in selection))
        self.assertEqual(selection, sorted(selection))
        self.assertTrue(all((end - start).days == learning.H1_WINDOW_DAYS - 1 for start, end in selection))
        self.assertGreaterEqual(selection[0][0], first + timedelta(days=14))  # warm-up history before the first block

    def test_hourly_candidates_use_the_hourly_space_and_are_deterministic(self):
        pool = learning.candidates(7, learning.default_params(), profile=learning.H1_PROFILE)
        self.assertEqual(pool, learning.candidates(7, learning.default_params(), profile=learning.H1_PROFILE))
        self.assertTrue(all(c["strategy"]["fast"] in learning.SPACE_H1["fast"] for c in pool))
        self.assertTrue(all(c["strategy"]["slow"] in learning.SPACE_H1["slow"] for c in pool))
        self.assertIn("breakout", {c["strategy"]["family"] for c in learning.candidates(
            1, learning.default_params(), count=200, profile=learning.H1_PROFILE)})

    def test_each_profile_keeps_its_own_champions_and_runs(self):
        self.assertEqual(self.daily.champion("EURUSD")["version"], 1)  # the reference strategy is created on first read
        version = self.daily.promote("EURUSD", {**learning.default_params(), "stop_atr_multiple": 3.0}, None, "test")
        self.assertEqual(version, 2)
        self.assertEqual(self.hourly.champion("EURUSD")["version"], 1)
        self.assertEqual(self.daily.champion("EURUSD")["version"], 2)
        self.hourly.start_run("EURUSD")
        self.assertEqual(len(self.hourly.runs()), 1)
        self.assertEqual(self.daily.runs(), [])

    def test_an_hourly_run_learns_on_the_massive_history_and_leaves_the_daily_pipeline_alone(self):
        self.candles.store(hourly_series(), "HISTORICAL", None)
        original = learning.candidates
        with mock.patch.object(learning, "candidates",
                               lambda seed, champ, count=3, profile=learning.D1_PROFILE: original(seed, champ, count, profile)):
            result = learning.learn(self.candles, self.hourly, "EURUSD")
        run = self.hourly.runs(1)[0]
        self.assertEqual((run["status"], run["data"]["profile"], run["data"]["timeframe"]), ("DONE", "H1", "H1"))
        self.assertEqual(len(run["candidates"]), 4)
        self.assertEqual(run["evidence"]["criteria"]["min_trades"], learning.H1_PROFILE.min_trades)
        self.assertIn(result["decision"], ("KEPT", "PROMOTED"))
        self.assertEqual(self.daily.runs(), [])

    def test_a_too_short_hourly_history_fails_the_run_instead_of_learning_from_nothing(self):
        from astra.papertrade import SimulationError

        self.candles.store(hourly_series(bars=500), "HISTORICAL", None)
        with self.assertRaises(SimulationError):
            learning.learn(self.candles, self.hourly, "EURUSD")
        self.assertEqual(self.hourly.runs(1)[0]["status"], "FAILED")

    def test_the_hourly_paper_account_follows_the_broker_candles_and_starts_now(self):
        self.candles.register_source("MT5_METAQUOTES_DEMO", "MT5", "BROKER", "test")
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        self.candles.store(hourly_series(source="MT5_METAQUOTES_DEMO", bars=600, end=now - timedelta(hours=1)), "BROKER", None)
        created = learning.ensure_managed_sessions(self.candles, self.hourly, self.lab)
        self.assertEqual(len(created), 1)
        session = self.lab.session(created[0], item_limit=0)
        self.assertEqual((session["timeframe"], session["source"]), ("H1", "MT5_METAQUOTES_DEMO"))
        self.assertTrue(session["name"].startswith("ASTRA H1"))
        self.assertGreaterEqual(datetime.fromisoformat(session["start_at"]), now)  # no old bar is traded after the fact
        self.assertEqual(learning.ensure_managed_sessions(self.candles, self.hourly, self.lab), [])

    # -- hourly DEMO authorization (a person, never a process) -------------------------------------------------

    def live_broker_candles(self):
        self.candles.register_source("MT5_METAQUOTES_DEMO", "MT5", "BROKER", "test")
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        self.candles.store(hourly_series(source="MT5_METAQUOTES_DEMO", bars=600, end=now - timedelta(hours=1)), "BROKER", None)

    def promote_hourly(self, status=None):
        self.hourly.champion("EURUSD")
        params = {**learning.default_params(), "tier": "PROBATOIRE",
                  "review": learning.preregistered_review([0.01, 0.02, 0.015, 0.012, 0.018])}
        if status:
            params["review_status"] = status
        self.hourly.promote("EURUSD", params, 5, "test")

    def authorize(self, **kw):
        return learning.authorize_demo(self.hourly, self.lab, "EURUSD", "test-op", "essai", candles=self.candles, **kw)

    def test_hourly_authorization_needs_a_promoted_champion_and_a_confirmation_or_an_explicit_acceptance(self):
        self.live_broker_candles()
        with self.assertRaises(learning.SimulationError):
            self.authorize()  # only the reference strategy: nothing was promoted
        self.promote_hourly()
        with self.assertRaises(learning.SimulationError) as caught:
            self.authorize()  # promoted but not confirmed by the prospective review
        self.assertIn("--accept-unconfirmed", str(caught.exception))
        self.assertIsNone(self.hourly.demo_session("EURUSD"))
        with self.assertRaises(learning.SimulationError):
            learning.authorize_demo(self.hourly, self.lab, "EURUSD", "", "essai", candles=self.candles, accept_unconfirmed=True)
        self.assertTrue(self.authorize(accept_unconfirmed=True))
        session_id = self.hourly.demo_session("EURUSD")
        self.assertIsNotNone(session_id)
        session = self.lab.session(session_id, item_limit=0)
        self.assertEqual((session["timeframe"], session["source"], session["status"]),
                         ("H1", "MT5_METAQUOTES_DEMO", "RUNNING"))
        self.assertIn("DEMO", session["name"])
        audit = next(e for e in self.hourly.events("EURUSD") if e["kind"] == "AUTORISATION_DEMO")
        self.assertEqual((audit["detail"]["unconfirmed"], audit["detail"]["operator"]), (True, "test-op"))
        again = self.authorize(accept_unconfirmed=True)  # same champion: same account, nothing changes
        self.assertFalse(again)
        self.assertEqual(self.hourly.demo_session("EURUSD"), session_id)
        pair = next(p for p in learning.overview(self.hourly, self.lab)["pairs"] if p["symbol"] == "EURUSD")
        self.assertEqual(pair["demo_session"]["id"], session_id)

    def test_the_standing_authorization_feeds_the_bridge_only_after_a_confirmation_and_only_for_tradable_pairs(self):
        self.live_broker_candles()
        self.promote_hourly(status="CONFIRME")
        args = (self.hourly, self.lab, self.candles, "EURUSD")
        for verdict in (None, "SUSPENDED"):
            self.assertIsNone(learning.apply_standing_authorization(*args, verdict))  # nothing but a confirmation counts
        self.assertIsNone(self.hourly.demo_session("EURUSD"))
        granted = learning.apply_standing_authorization(*args, "CONFIRMED")
        self.assertEqual(granted, {"authorized": True, "changed": True})
        self.assertIsNotNone(self.hourly.demo_session("EURUSD"))
        audit = next(e for e in self.hourly.events("EURUSD") if e["kind"] == "AUTORISATION_DEMO")
        self.assertEqual((audit["detail"]["operator"], audit["detail"]["unconfirmed"]),
                         (learning.STANDING_OPERATOR, False))
        gold = learning.apply_standing_authorization(self.hourly, self.lab, self.candles, "XAUUSD", "CONFIRMED")
        self.assertFalse(gold["authorized"])  # an instrument the bridge may not trade
        self.assertIsNone(self.hourly.demo_session("XAUUSD"))

    def test_the_standing_authorization_reports_a_refusal_instead_of_failing_when_a_prerequisite_is_missing(self):
        self.hourly.champion("EURUSD")  # only the reference strategy: nothing was promoted by a run
        refused = learning.apply_standing_authorization(self.hourly, self.lab, self.candles, "EURUSD", "CONFIRMED")
        self.assertFalse(refused["authorized"])
        self.assertIn("promu", refused["reason"])

    def test_a_champion_confirmed_by_the_prospective_review_needs_no_acceptance_flag(self):
        self.live_broker_candles()
        self.promote_hourly(status="CONFIRME")
        self.assertTrue(self.authorize())
        audit = next(e for e in self.hourly.events("EURUSD") if e["kind"] == "AUTORISATION_DEMO")
        self.assertFalse(audit["detail"]["unconfirmed"])

    def test_a_person_can_revoke_the_hourly_feed_and_the_daily_feed_cannot_be_revoked(self):
        self.live_broker_candles()
        self.promote_hourly(status="CONFIRME")
        self.authorize()
        self.assertTrue(learning.revoke_demo(self.hourly, "EURUSD", "test-op", "essai"))
        self.assertIsNone(self.hourly.demo_session("EURUSD"))
        self.assertIn("REVOCATION_DEMO", [e["kind"] for e in self.hourly.events("EURUSD")])
        with self.assertRaises(learning.SimulationError):
            learning.revoke_demo(self.daily, "EURUSD", "test-op", "essai")
        with self.assertRaises(learning.SimulationError):
            learning.revoke_demo(self.hourly, "EURUSD", "", "essai")

    def test_a_negative_prospective_review_suspends_the_authorization_without_a_person(self):
        self.live_broker_candles()
        self.promote_hourly()
        self.authorize(accept_unconfirmed=True)
        self.hourly.set_managed_session("EURUSD", 99)  # the observation account of the pair
        lab = mock.Mock()
        lab.session.return_value = {"account": {"initial_capital": 10_000.0}, "trades": [
            {"exit_ts": "2999-01-01T00:00:00+00:00", "net_pnl": v}
            for v in (-50.0, -40.0, -60.0, -45.0, -55.0, -42.0, -58.0, -47.0, -52.0)]}
        self.assertEqual(learning.review_live(self.hourly, lab, "EURUSD"), "SUSPENDED")
        self.assertIsNone(self.hourly.demo_session("EURUSD"))  # the bridge goes back to the daily feed
        kinds = [e["kind"] for e in self.hourly.events("EURUSD")]
        self.assertIn("SUSPENSION_DEMO", kinds)
        self.assertIn("SUSPENSION", kinds)

    def test_the_worker_schedules_hourly_learning_next_to_the_daily_one(self):
        self.candles.store(hourly_series(bars=3000), "HISTORICAL", None)
        worker = Worker(self.db, Path(self.temp.name) / "imports", ecb_enabled=False, massive_enabled=False)
        worker.initialize()
        worker.maybe_schedule_learning()
        learn_jobs = [j for j in worker.jobs.jobs() if j["kind"] == "learn"]
        self.assertEqual([j["params"] for j in learn_jobs], [{"symbol": "EURUSD", "profile": "H1"}])


class FamilyTests(unittest.TestCase):
    @staticmethod
    def series(closes):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        return [Candle("EURUSD", "D1", start + timedelta(days=i), None, None, None, c, None, "ECB_EXR", KIND_REFERENCE)
                for i, c in enumerate(closes)]

    def decide(self, closes, **strategy):
        from astra.papertrade import StrategyParams, _build, decide

        params = _build(StrategyParams, {"fast": 3, "slow": 10, "atr_window": 3, "min_atr_pct": 0.0, **strategy})
        return decide(self.series(closes), params)

    def test_momentum_follows_the_move_over_the_slow_window(self):
        rising = [1.10 + 0.001 * i for i in range(12)]
        self.assertEqual(self.decide(rising, family="momentum").action, BUY)
        self.assertEqual(self.decide(rising[::-1], family="momentum").action, SELL)

    def test_mean_reversion_fades_a_stretched_price_and_stays_flat_near_the_mean(self):
        flat = [1.10, 1.101] * 5
        self.assertEqual(self.decide([*flat, 1.13], family="mean_reversion", z_entry=1.5).action, SELL)
        self.assertEqual(self.decide([*flat, 1.07], family="mean_reversion", z_entry=1.5).action, BUY)
        self.assertEqual(self.decide([*flat, 1.1005], family="mean_reversion").action, FLAT)

    def test_breakout_buys_above_the_channel_sells_below_and_ignores_the_current_bar_in_it(self):
        base = [1.10, 1.101] * 6
        self.assertEqual(self.decide([*base, 1.12], family="breakout").action, BUY)
        self.assertEqual(self.decide([*base, 1.08], family="breakout").action, SELL)
        self.assertEqual(self.decide([*base, 1.1005], family="breakout").action, FLAT)
        decision = self.decide([*base, 1.12], family="breakout")
        self.assertEqual(decision.indicators["canal_haut"], 1.101)  # the 1.12 close is not part of its own channel

    def test_breakout_needs_one_more_bar_than_the_window_before_deciding(self):
        self.assertIsNone(self.decide([1.10, 1.101] * 5, family="breakout"))  # 10 bars: slow window only
        self.assertIsNotNone(self.decide([1.10, 1.101] * 5 + [1.1], family="breakout"))

    def test_the_regime_filter_keeps_trend_strategies_out_of_a_range_and_mean_reversion_out_of_a_trend(self):
        climb = [1.10 + 0.002 * i for i in range(30)]
        zigzag = [1.10 + (0.002 if i % 2 else 0.0) for i in range(30)]
        self.assertEqual(self.decide(climb, family="sma_trend", regime="match").action, BUY)
        blocked = self.decide(zigzag, family="sma_trend", regime="match")
        self.assertEqual(blocked.action, FLAT)
        self.assertIn("sans tendance", blocked.reason)
        self.assertEqual(self.decide(climb, family="mean_reversion", regime="match").action, FLAT)
        self.assertEqual(self.decide(zigzag, family="sma_trend").action in (BUY, SELL, FLAT), True)  # filter off: unchanged

    def test_the_regime_filter_does_not_block_when_the_history_is_too_short_to_judge(self):
        short = [1.10 + 0.002 * i for i in range(12)]
        self.assertEqual(self.decide(short, family="momentum", regime="match", slow=10).action, BUY)

    def test_old_parameters_keep_the_sma_trend_and_unknown_families_are_refused(self):
        from astra.papertrade import SimulationError, StrategyParams, _build

        self.assertEqual(_build(StrategyParams, {"fast": 20, "slow": 50}).family, "sma_trend")
        for bad in ({"family": "martingale"}, {"family": 3}, {"z_entry": 9}):
            with self.assertRaises(SimulationError):
                _build(StrategyParams, bad)

    def test_candidates_cover_every_family(self):
        pool = learning.candidates(7, learning.default_params())
        self.assertEqual({c["strategy"]["family"] for c in pool}, set(learning.SPACE["family"]))
        legacy = {"strategy": {"fast": 20, "slow": 50, "atr_window": 14, "min_atr_pct": 0.0002, "max_atr_pct": 0.03},
                  "stop_atr_multiple": 2.0}  # a champion stored before the families
        self.assertNotIn(learning.default_params(), learning.candidates(7, legacy))


class WorkerTests(LearningCase):
    def test_worker_schedules_one_learning_run_per_pair_and_day(self):
        now = {"t": datetime(2026, 10, 28, 8, tzinfo=UTC)}
        worker = Worker(self.db, Path(self.temp.name) / "imports", ecb_enabled=False, clock=lambda: now["t"])
        worker.initialize()
        worker.tick()
        jobs = [j for j in worker.jobs.jobs() if j["kind"] == "learn"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "DONE")
        self.assertIsNotNone(self.store.managed_session("EURUSD"))
        worker.tick()
        self.assertEqual(len([j for j in worker.jobs.jobs() if j["kind"] == "learn"]), 1)  # not again the same day


if __name__ == "__main__":
    unittest.main()
