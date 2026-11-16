"""DEMO paper accounts sized like the DEMO account: metals signals were refused by a 10 000 USD paper account
(minimum size), so the bridge never saw them (16-11-2026, D-017). Written before the change."""

import unittest

from astra import learning
from test_learning import HourlyProfileTests


class DemoCapitalTests(HourlyProfileTests):
    def _adopted(self):
        self.live_broker_candles()
        learning.ensure_managed_sessions(self.candles, self.hourly, self.lab)
        run_id = self.hourly.start_run("EURUSD")
        learning.register_observed(self.candles, self.hourly, self.lab, "EURUSD", run_id,
                                   [self.result("a", 1.0, slow=36), self.result("b", 3.0, slow=96)], None)
        learning.adopt_observed(self.hourly, self.lab, self.candles, "EURUSD", "titulaire", "essai DEMO")
        return self.hourly.demo_session("EURUSD")

    def test_a_new_demo_account_starts_with_the_demo_capital(self):
        session_id = self._adopted()
        account = self.lab.session(session_id, item_limit=0)["account"]
        self.assertEqual(account["initial_capital"], learning.DEMO_PAPER_CAPITAL)

    def test_an_existing_demo_account_is_rebased_with_the_same_strategy_and_the_old_one_stopped(self):
        old = self._adopted()
        before = self.lab.session(old, item_limit=0)
        new = learning.rebase_demo_capital(self.hourly, self.lab, self.candles, "EURUSD", "titulaire", "métaux")
        self.assertNotEqual(new, old)
        self.assertEqual(self.hourly.demo_session("EURUSD"), new)
        after = self.lab.session(new, item_limit=0)
        self.assertEqual(after["params"]["strategy"], before["params"]["strategy"])
        self.assertEqual(after["account"]["initial_capital"], learning.DEMO_PAPER_CAPITAL)
        self.assertEqual(self.lab.session(old, item_limit=0)["status"], "STOPPED")
        self.assertIn("CAPITAL_DEMO", [e["kind"] for e in self.hourly.events("EURUSD", limit=3)])
        with self.assertRaises(learning.SimulationError):  # a person must be named
            learning.rebase_demo_capital(self.hourly, self.lab, self.candles, "EURUSD", "", "x")


del HourlyProfileTests  # run only the tests above from this module

if __name__ == "__main__":
    unittest.main()
