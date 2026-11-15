"""LOT 4 acceptance tests (written and committed before the implementation).

Gate (master document, section XIV): non-compliant orders are refused; the emergency stop is tested from a separate
process. PRESET values of section VIII, applied as decided in audit/DECISIONS.md D-003.
"""

import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from _support import workspace_tempdir
from astra import mt5_emergency, mt5_risk
from astra.brokers.mt5_demo_orders import MAGIC, MAGIC_MANUAL, Mt5DemoTrader
from astra.mt5_risk import AccountState, OrderRequest, RiskConfigError
from test_mt5_bridge import LOGIN, TradingMt5

PRESET = {"risk_per_trade_pct": 0.1, "unconfirmed_risk_scale": 1.0, "max_quote_age_seconds": 5.0,
          "max_net_currency_exposure_x": 1.0, "max_gross_leverage_x": 3.0, "monthly_halving_pct": 4.0,
          "emergency_drawdown_pct": 12.0}
RATES = {"USD": 1.0, "EUR": 1.10, "GBP": 1.30, "JPY": 1 / 150, "CHF": 1.12, "CAD": 0.71, "AUD": 0.66, "NZD": 0.60,
         "XAU": 2000.0, "XAG": 25.0}


def account(**kw):
    base = dict(environment="DEMO", login=1, expected_login=1, equity=100_000.0, margin=0.0, day_start_equity=100_000.0,
                week_start_equity=100_000.0, month_start_equity=100_000.0, peak_equity=100_000.0, positions=(),
                connector_ok=True, algo_trading_enabled=True, currency="USD", reconciliation_ok=True, unknown_orders=0,
                entries_allowed_by_server=True, kill_switch_active=False, usd_rates=RATES)
    base.update(kw)
    return AccountState(**base)


def order(symbol="EURUSD", side="LONG", lots=0.1, loss=90.0, bid=1.1, ask=1.10002, age=1.0, vmin=0.01, size=100000.0):
    return OrderRequest(symbol, side, lots, loss, 100.0, bid, ask, age, vmin, 100.0, 0.01, True, size)


class LimitsFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.path = Path(self.temp.name) / "mt5-risk.json"
        self.path.write_text(json.dumps(PRESET), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_r1_without_a_limits_file_every_entry_is_refused(self):
        with self.assertRaises(RiskConfigError):
            mt5_risk.load_required_limits(None)
        with self.assertRaises(RiskConfigError):
            mt5_risk.load_required_limits(str(self.path) + ".absent")
        decision = mt5_risk.evaluate_entry(account(), order(), RiskConfigError("aucun fichier"))
        self.assertFalse(decision.allowed)

    def test_r11_the_file_is_strict_and_its_fingerprint_is_published(self):
        limits = mt5_risk.load_required_limits(str(self.path))
        self.assertEqual(limits.risk_per_trade_pct, 0.1)
        public = limits.public()
        self.assertEqual(len(public["file_sha256"]), 64)
        self.path.write_text(json.dumps({**PRESET, "risk_per_trade_pct": 0.2}), encoding="utf-8")
        self.assertNotEqual(mt5_risk.load_required_limits(str(self.path)).public()["file_sha256"], public["file_sha256"])
        for bad in ({**PRESET, "unknown_key": 1}, {**PRESET, "risk_per_trade_pct": -1},
                    {**PRESET, "max_gross_leverage_x": 0}, {**PRESET, "emergency_drawdown_pct": 50}):
            self.path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(RiskConfigError):
                mt5_risk.load_required_limits(str(self.path))


class PresetChecksTests(unittest.TestCase):
    def setUp(self):
        self.limits = replace(mt5_risk.RiskLimits(), **PRESET)

    def failed(self, acct, req):
        decision = mt5_risk.evaluate_entry(acct, req, self.limits)
        return {c.name for c in decision.checks if not c.passed}

    def test_r2_loss_at_stop_is_capped_at_a_tenth_of_a_percent(self):
        self.assertEqual(self.failed(account(), order(loss=100.0)), set())
        self.assertIn("taille_de_position", self.failed(account(), order(loss=150.0)))

    def test_r3_net_exposure_per_currency_is_capped_at_one_times_equity(self):
        # 1 lot EURUSD = 100 000 EUR, about 110 000 USD long EUR: more than the equity
        self.assertIn("exposition_par_devise", self.failed(account(), order(lots=1.0, loss=95.0)))
        self.assertNotIn("exposition_par_devise", self.failed(account(), order(lots=0.5, loss=95.0)))
        # a cross counts on both of its currencies: long 0.5 EURGBP with 0.6 lot EURUSD long already open
        held = ({"symbol": "EURUSD", "side": "LONG", "volume": 0.6, "price_open": 1.1, "price_current": 1.1},)
        cross = order("EURGBP", lots=0.5, loss=95.0, bid=0.846, ask=0.84602)
        self.assertIn("exposition_par_devise", self.failed(account(positions=held), cross))
        # a currency without a known USD rate cannot be measured: refused
        self.assertIn("exposition_par_devise", self.failed(account(usd_rates={"USD": 1.0}), order()))

    def test_r4_gross_leverage_is_capped_at_three(self):
        held = tuple({"symbol": s, "side": "LONG", "volume": 0.9, "price_open": p, "price_current": p}
                     for s, p in (("EURUSD", 1.1), ("GBPUSD", 1.3), ("AUDUSD", 0.66), ("NZDUSD", 0.6)))
        held += ({"symbol": "USDJPY", "side": "SHORT", "volume": 0.9, "price_open": 150.0, "price_current": 150.0},)
        relaxed = replace(self.limits, max_net_currency_exposure_x=20.0, max_positions=50, max_total_lots=50)
        decision = mt5_risk.evaluate_entry(account(positions=held), order("USDCHF", lots=0.6, loss=95.0, bid=0.89,
                                                                          ask=0.89002), relaxed)
        self.assertIn("levier_brut", {c.name for c in decision.checks if not c.passed})

    def test_r5_a_losing_month_halves_the_size_and_nothing_ever_scales_up(self):
        self.assertEqual(mt5_risk.size_scale(account(equity=96_100.0), self.limits), 1.0)
        self.assertEqual(mt5_risk.size_scale(account(equity=95_900.0), self.limits), 0.5)
        self.assertEqual(mt5_risk.size_scale(account(equity=130_000.0), self.limits), 1.0)  # no increase after gains
        self.assertEqual(mt5_risk.size_scale(account(month_start_equity=None), self.limits), 0.5)  # unknown: prudent

    def test_r6_a_quote_older_than_five_seconds_is_refused(self):
        self.assertIn("fraicheur_des_donnees", self.failed(account(), order(age=6.0)))
        self.assertNotIn("fraicheur_des_donnees", self.failed(account(), order(age=4.0)))

    def test_r7_a_minimum_volume_above_the_risk_budget_is_refused(self):
        # XAUUSD: 0.01 lot is the minimum and, with a wide stop, loses more than 0.1 % at the stop
        gold = order("XAUUSD", lots=0.01, loss=400.0, bid=2000.0, ask=2000.3, size=100.0)
        self.assertIn("taille_de_position", self.failed(account(), gold))


class EmergencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.dir = Path(self.temp.name)
        self.limits = replace(mt5_risk.RiskLimits(), **PRESET)

    def tearDown(self):
        self.temp.cleanup()

    def test_r8_twelve_percent_below_the_peak_calls_the_emergency_stop(self):
        self.assertIsNone(mt5_emergency.trigger(account(equity=88_100.0), self.limits))
        self.assertIn("12", mt5_emergency.trigger(account(equity=87_900.0), self.limits))
        self.assertIsNotNone(mt5_emergency.trigger(account(peak_equity=None), self.limits))  # unknown peak: stop

    def _fake_with_positions(self):
        fake = TradingMt5()
        fake.open_positions = [
            SimpleNamespace(ticket=1, symbol="EURUSD", type=0, volume=0.1, price_open=1.1, sl=1.09, profit=0.0,
                            magic=MAGIC, comment="ASTRA:x"),
            SimpleNamespace(ticket=2, symbol="GBPUSD", type=1, volume=0.1, price_open=1.3, sl=1.31, profit=0.0,
                            magic=MAGIC_MANUAL, comment="ASTRA-SITE:y"),
            SimpleNamespace(ticket=3, symbol="USDJPY", type=0, volume=0.1, price_open=150, sl=149, profit=0.0,
                            magic=0, comment="by hand")]
        return fake

    def test_r9_the_stop_file_comes_first_then_every_astra_position_is_closed(self):
        fake = self._fake_with_positions()
        trader = Mt5DemoTrader(module=fake, expected_login=LOGIN)
        with trader:
            report = mt5_emergency.run(trader, self.dir, "test")
        self.assertTrue((self.dir / "STOP").exists())
        self.assertIn("test", (self.dir / "STOP").read_text(encoding="utf-8"))
        self.assertEqual(sorted(report["closed"]), [1, 2])
        self.assertEqual([p.ticket for p in fake.open_positions], [3])  # a position opened by hand is never touched
        with trader:
            self.assertEqual(mt5_emergency.run(trader, self.dir, "again")["closed"], [])  # idempotent

    def test_r9b_a_close_that_fails_is_reported_and_the_stop_stays(self):
        fake = self._fake_with_positions()
        fake.mode = "market_closed"
        trader = Mt5DemoTrader(module=fake, expected_login=LOGIN)
        with trader:
            report = mt5_emergency.run(trader, self.dir, "test")
        self.assertEqual(report["closed"], [])
        self.assertEqual(sorted(f["ticket"] for f in report["failed"]), [1, 2])
        self.assertTrue((self.dir / "STOP").exists())

    def test_r10_the_emergency_command_runs_alone_without_the_bridge(self):
        fake = self._fake_with_positions()
        code = mt5_emergency.main(["--workdir", str(self.dir), "--reason", "manuel"], module=fake, login=LOGIN)
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "STOP").exists())
        journal = (self.dir / "emergency.jsonl").read_text(encoding="utf-8")
        self.assertIn("\"closed\": [1, 2]", journal)


if __name__ == "__main__":
    unittest.main()
