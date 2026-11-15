"""LOT 2 acceptance tests (written and committed before the implementation).

Gate (master document, section XIV): no backtest without a sheet; the final test set is sealed; results replay
identically. Section IV: sheet fields, budget of 20 hypotheses a year and 5 variants each, one final access, a global
cumulative trial counter that includes earlier trials, negative results kept, the 120-variant daily loop stopped.
"""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _support import workspace_tempdir
from astra import evidence, jobs
from astra.evidence import EvidenceError, EvidenceRegistry
from astra.marketdata import Candle, CandleStore
from astra.papertrade import backtest_request

T0 = datetime(2026, 1, 5, tzinfo=UTC)
NOW = datetime(2026, 11, 16, tzinfo=UTC)


def sheet(**kw):
    base = {"title": "Tendance EURUSD", "economic_reason": "les couvreurs paient une prime de lenteur",
            "source": "test", "universe": ["EURUSD"], "horizon": "H1",
            "study_period": [T0.isoformat(), (T0 + timedelta(hours=200)).isoformat()],
            "sealed_test_period": [(T0 + timedelta(hours=200)).isoformat(), (T0 + timedelta(hours=300)).isoformat()],
            "parameters": {"fast": 3, "slow": 8}, "costs": "spread et glissement du modèle, doublés en test",
            "expected_result": "Sharpe net > 0", "abandon_criterion": "Sharpe net < 0,3 sur le jeu scellé",
            "planned_variants": 3, "code_version": "test", "data_sha256": "0" * 64}
    base.update(kw)
    return base


class RegistryCase(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.db = Path(self.temp.name) / "astra.sqlite3"
        self.reg = EvidenceRegistry(self.db)
        self.reg.initialize()
        self.candles = CandleStore(self.db)
        self.candles.initialize()
        bars = []
        for i in range(300):
            price = 1.10 + 0.01 * (((i // 20) % 2 and (20 - i % 20)) or i % 20) / 20
            bars.append(Candle("EURUSD", "H1", T0 + timedelta(hours=i), price, price + 0.001, price - 0.001, price, None,
                               "test", "OHLC"))
        self.candles.store(bars, "HISTORICAL", None)
        self.request = backtest_request({"symbol": "EURUSD", "timeframe": "H1", "source": "test",
                                         "strategy_params": {"fast": 3, "slow": 8, "atr_window": 3, "min_atr_pct": 0}})

    def tearDown(self):
        self.temp.cleanup()


class SheetTests(RegistryCase):
    def test_e1_a_sheet_without_every_field_is_refused(self):
        for missing in evidence.REQUIRED_FIELDS:
            bad = sheet()
            bad.pop(missing)
            with self.assertRaises(EvidenceError, msg=missing):
                self.reg.register(bad, NOW)
        with self.assertRaises(EvidenceError):
            self.reg.register(sheet(economic_reason=""), NOW)

    def test_e2_the_sealed_period_comes_after_the_study_period_without_overlap(self):
        overlap = sheet(sealed_test_period=[(T0 + timedelta(hours=150)).isoformat(), (T0 + timedelta(hours=300)).isoformat()])
        with self.assertRaises(EvidenceError):
            self.reg.register(overlap, NOW)
        with self.assertRaises(EvidenceError):
            self.reg.register(sheet(planned_variants=6), NOW)

    def test_e3_budget_of_twenty_hypotheses_a_year_and_five_variants(self):
        first = self.reg.register(sheet(), NOW)
        for _ in range(19):
            self.reg.register(sheet(), NOW)
        with self.assertRaises(EvidenceError):
            self.reg.register(sheet(), NOW)
        self.assertEqual(self.reg.register(sheet(), NOW + timedelta(days=366)) > first, True)  # a new year
        for i in range(5):
            self.reg.add_variant(first, {"fast": 3 + i, "slow": 8}, NOW)
        with self.assertRaises(EvidenceError):
            self.reg.add_variant(first, {"fast": 9, "slow": 8}, NOW)


class GateTests(RegistryCase):
    def test_e4_no_backtest_without_a_sheet_at_any_entry_point(self):
        with self.assertRaises(EvidenceError):
            evidence.require_registration(self.request)
        worker = jobs.Worker(self.db, Path(self.temp.name) / "imports", ecb_enabled=False, massive_enabled=False)
        self.assertFalse(worker.learning_enabled)  # the daily 120-variant loop is stopped (section IV)
        from astra.papertrade import LabStore

        lab = LabStore(self.db)
        lab.initialize()
        backtest_id = lab.create_backtest(self.request, "test")
        job = worker.jobs.enqueue("backtest", {"backtest_id": backtest_id}, "test")
        worker.tick()
        self.assertEqual(lab.backtest(backtest_id, item_limit=0)["status"], "FAILED")
        self.assertIsNotNone(job)

    def test_e5_a_registered_variant_runs_on_the_study_period_only(self):
        hid = self.reg.register(sheet(), NOW)
        run = self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        self.assertLessEqual(run["result"]["data"]["last"], (T0 + timedelta(hours=200)).isoformat())
        into_sealed = dict(self.request, end=(T0 + timedelta(hours=250)).isoformat())
        with self.assertRaises(EvidenceError):
            self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8}, into_sealed, NOW)

    def test_e6_the_sealed_set_is_opened_once_and_the_access_is_counted(self):
        hid = self.reg.register(sheet(), NOW)
        self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        final = self.reg.final_test(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        self.assertGreaterEqual(final["result"]["data"]["first"], (T0 + timedelta(hours=200)).isoformat())
        with self.assertRaises(EvidenceError):
            self.reg.final_test(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        self.assertEqual(self.reg.hypothesis(hid)["final_accesses"], 1)

    def test_e7_every_registered_result_replays_identically(self):
        hid = self.reg.register(sheet(), NOW)
        run = self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        replay = self.reg.replay(self.candles, run["run_id"])
        self.assertTrue(replay["identical"])
        self.assertEqual(replay["result_sha256"], run["result_sha256"])

    def test_e8_trials_are_counted_globally_with_earlier_ones_and_negative_results_are_kept(self):
        self.reg.record_prior("h1_learn_runs", 14635, "candidats H1 tracés avant le registre", NOW)
        hid = self.reg.register(sheet(), NOW)
        self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        self.reg.registered_backtest(self.candles, hid, {"fast": 4, "slow": 8}, self.request, NOW)
        self.assertEqual(self.reg.trials(), {"prior": 14635, "registered": 2, "total": 14637})
        self.reg.close(hid, "REJETEE", "Sharpe négatif", NOW)
        self.assertEqual(self.reg.hypothesis(hid)["status"], "REJETEE")
        with self.assertRaises(EvidenceError):  # a closed hypothesis reopens only through a new sheet
            self.reg.registered_backtest(self.candles, hid, {"fast": 5, "slow": 8}, self.request, NOW)
        self.assertEqual(len(self.reg.runs(hid)), 2)  # its results stay


if __name__ == "__main__":
    unittest.main()
