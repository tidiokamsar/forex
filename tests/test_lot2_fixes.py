"""LOT 2, second attempt: tests for the blocking points of audit/lot2/VERIFICATION.md. Committed before the fixes."""

import unittest
from datetime import timedelta

from astra import __version__, cli, evidence
from astra.evidence import EvidenceError
from test_lot2_registry import NOW, T0, RegistryCase, sheet


def good_sheet(case, **kw):
    fingerprint = evidence.data_fingerprint(case.candles, "EURUSD", "H1", "test",
                                            T0.isoformat(), (T0 + timedelta(hours=300)).isoformat())
    base = sheet(data_sha256=fingerprint, code_version=__version__,
                 abandon_rule={"metric": "net_pnl", "op": "<", "threshold": 0.0})
    base.update(kw)
    return base


class FixTests(RegistryCase):
    def test_g1_the_token_is_not_a_public_constant(self):
        forged = dict(self.request, _evidence="evidence-registry")
        with self.assertRaises(EvidenceError):
            evidence.require_registration(forged)

    def test_g2_every_cli_research_command_refuses_without_a_sheet(self):
        for argv in (["backtest", "--csv", "x.csv", "--source-type", "SYNTHETIC", "--exploratory"],
                     ["walk-forward", "--csv", "x.csv", "--source-type", "SYNTHETIC", "--hypothesis", "h"],
                     ["evaluate-strategies"], ["demo"]):
            self.assertEqual(cli.main(argv), 2, argv)

    def test_g3_the_numeric_abandon_rule_closes_the_hypothesis_on_the_final_test(self):
        hid = self.reg.register(good_sheet(self, abandon_rule={"metric": "net_pnl", "op": "<", "threshold": 1e12}), NOW)
        self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        final = self.reg.final_test(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        self.assertTrue(final["abandon"])
        self.assertEqual(self.reg.hypothesis(hid)["status"], "REJETEE")
        with self.assertRaises(EvidenceError):
            self.reg.register(good_sheet(self, abandon_rule={"metric": "net_pnl", "op": "about", "threshold": 1}), NOW)
        with self.assertRaises(EvidenceError):
            self.reg.register(good_sheet(self, abandon_rule="Sharpe faible"), NOW)

    def test_g4_the_data_fingerprint_must_match_the_data(self):
        hid = self.reg.register(good_sheet(self, data_sha256="0" * 64), NOW)
        with self.assertRaises(EvidenceError):
            self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8}, self.request, NOW)
        ok = self.reg.register(good_sheet(self), NOW)
        self.reg.registered_backtest(self.candles, ok, {"fast": 3, "slow": 8}, self.request, NOW)

    def test_g5_code_version_and_fixed_parameters_are_checked(self):
        stale = self.reg.register(good_sheet(self, code_version="0.0.0-old"), NOW)
        with self.assertRaises(EvidenceError):
            self.reg.registered_backtest(self.candles, stale, {"fast": 3, "slow": 8}, self.request, NOW)
        hid = self.reg.register(good_sheet(self), NOW)
        with self.assertRaises(EvidenceError):  # a parameter the sheet did not fix
            self.reg.registered_backtest(self.candles, hid, {"fast": 3, "slow": 8, "atr_window": 9}, self.request, NOW)


if __name__ == "__main__":
    unittest.main()
