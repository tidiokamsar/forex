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
            def evaluate(candles, symbol, params, windows, cost_factor=1.0, purpose="selection"):
                key = {"selection": "selection", "final_check": "check", "cost_stress": "stress"}[purpose]
                return dict(scores[key](params))
            return evaluate

        good = {"windows": 1, "score": 3.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 500.0}
        champ = {"windows": 1, "score": 1.0, "trades": 50, "worst_drawdown_pct": 5.0, "net_pnl": 100.0}
        pick = lambda p: champ if p == self.store.champion("EURUSD")["params"] else good  # noqa: E731
        with mock.patch.object(learning, "evaluate", fake({"selection": pick, "check": pick, "stress": pick})):
            self.assertEqual(learning.learn(self.candles, self.store, "EURUSD")["decision"], "PROMOTED")
        self.assertEqual(self.store.champion("EURUSD")["version"], 2)
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
