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

    def test_managed_account_follows_the_champion_and_can_roll_back(self):
        created = learning.ensure_managed_sessions(self.candles, self.store, self.lab)
        self.assertEqual(len(created), 1)  # only EURUSD has data here
        self.assertEqual(learning.ensure_managed_sessions(self.candles, self.store, self.lab), [])  # never twice
        session_id = self.store.managed_session("EURUSD")
        self.lab.step_session(session_id, self.candles)
        before = self.lab.session(session_id)
        new = {"strategy": {"fast": 10, "slow": 80, "atr_window": 20, "min_atr_pct": 0.0, "max_atr_pct": 0.03},
               "stop_atr_multiple": 3.0}
        self.store.promote("EURUSD", new, None, "test")
        self.assertTrue(learning.apply_champion(self.store, self.lab, "EURUSD"))
        after = self.lab.session(session_id)
        self.assertEqual(after["params"]["strategy"]["fast"], 10)
        self.assertEqual(after["params"]["risk"]["stop_atr_multiple"], 3.0)
        self.assertEqual(after["account"], before["account"])  # the paper account continues, only the strategy changes
        self.assertIn("NOUVEAU_CHAMPION", [e["kind"] for e in after["events"]])
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

    def promote_probationary(self):
        self.store.champion("EURUSD")
        self.store.promote("EURUSD", {**learning.default_params(), "tier": "PROBATOIRE"}, 5, "test")
        self.store.set_managed_session("EURUSD", 1)

    def lab_with(self, trades):
        lab = mock.Mock()
        lab.session.return_value = {"trades": trades}
        return lab

    def test_a_probationary_champion_graduates_on_a_positive_live_record(self):
        self.promote_probationary()
        live = [{"exit_ts": "2999-01-01T00:00:00+00:00", "net_pnl": 5.0}] * learning.LIVE_MIN_TRADES
        self.assertEqual(learning.graduate_or_demote(self.store, self.lab_with(live[:-1]), "EURUSD"), None)  # not enough yet
        self.assertEqual(learning.graduate_or_demote(self.store, self.lab_with(live), "EURUSD"), "GRADUATED")
        champion = self.store.champion("EURUSD")
        self.assertEqual((champion["params"]["tier"], champion["run_id"]), ("VALIDE", 5))
        self.assertIsNone(learning.graduate_or_demote(self.store, self.lab_with(live), "EURUSD"))  # nothing left to judge

    def test_a_negative_live_record_puts_the_previous_version_back(self):
        self.promote_probationary()
        live = [{"exit_ts": "2999-01-01T00:00:00+00:00", "net_pnl": -5.0}] * learning.LIVE_MIN_TRADES
        self.assertEqual(learning.graduate_or_demote(self.store, self.lab_with(live), "EURUSD"), "DEMOTED")
        champion = self.store.champion("EURUSD")
        self.assertEqual(champion["params"], learning.default_params())
        self.assertIsNone(champion["run_id"])

    def test_trades_before_the_promotion_and_validated_champions_are_never_judged(self):
        self.promote_probationary()
        old = [{"exit_ts": "2000-01-01T00:00:00+00:00", "net_pnl": 9.0}] * 50
        self.assertIsNone(learning.graduate_or_demote(self.store, self.lab_with(old), "EURUSD"))
        self.store.promote("EURUSD", {**learning.default_params(), "tier": "VALIDE"}, 5, "validé")
        live = [{"exit_ts": "2999-01-01T00:00:00+00:00", "net_pnl": -9.0}] * 50
        self.assertIsNone(learning.graduate_or_demote(self.store, self.lab_with(live), "EURUSD"))


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
