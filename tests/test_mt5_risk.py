"""Independent MT5 risk gateway: fail-closed defaults and one test per rule."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from _support import workspace_tempdir
from astra import mt5_risk
from astra.mt5_risk import AccountState, OrderRequest, RiskConfigError, RiskLimits

LIMITS = RiskLimits()
ACCOUNT = AccountState(environment="DEMO", login=114765880, expected_login=114765880, equity=100_000.0, margin=5_000.0,
                       day_start_equity=100_000.0, peak_equity=100_000.0, positions=(), connector_ok=True,
                       algo_trading_enabled=True, entries_allowed_by_server=True, kill_switch_active=False)
ORDER = OrderRequest(symbol="EURUSD", side="LONG", lots=0.83, loss_at_stop_usd=498.0, required_margin=900.0, bid=1.1,
                     ask=1.10002, quote_age_seconds=4.0, volume_min=0.01, volume_max=100.0, volume_step=0.01)


def failed(decision):
    return {c.name for c in decision.checks if not c.passed}


class GatewayTests(unittest.TestCase):
    def test_a_conforming_entry_is_allowed_and_every_check_is_recorded(self):
        decision = mt5_risk.evaluate_entry(ACCOUNT, ORDER, LIMITS)
        self.assertTrue(decision.allowed, decision.reasons)
        names = {c.name for c in decision.checks}
        self.assertTrue({"environnement_demo", "compte_autorise", "fraicheur_des_donnees", "spread", "instrument",
                         "volume_conforme", "taille_de_position", "marge", "perte_journaliere", "drawdown",
                         "arret_d_urgence", "connecteur", "exposition_par_instrument", "nombre_de_positions"} <= names)

    def test_every_rule_refuses_on_its_own_and_all_failures_are_listed_together(self):
        cases = {
            "environnement_demo": (replace(ACCOUNT, environment="REAL"), ORDER),
            "compte_autorise": (replace(ACCOUNT, login=999), ORDER),
            "connecteur": (replace(ACCOUNT, algo_trading_enabled=False), ORDER),
            "arret_d_urgence": (replace(ACCOUNT, entries_allowed_by_server=False), ORDER),
            "perte_journaliere": (replace(ACCOUNT, equity=96_500.0), ORDER),
            "drawdown": (replace(ACCOUNT, peak_equity=115_000.0), ORDER),
            "nombre_de_positions": (replace(ACCOUNT, positions=tuple({"symbol": f"X{i}", "volume": 0.1} for i in range(7))), ORDER),
            "instrument": (ACCOUNT, replace(ORDER, symbol="XAUUSD")),
            "volume_conforme": (ACCOUNT, replace(ORDER, lots=0.835)),
            "stop_present": (ACCOUNT, replace(ORDER, has_stop=False)),
            "taille_de_position": (ACCOUNT, replace(ORDER, loss_at_stop_usd=900.0)),
            "spread": (ACCOUNT, replace(ORDER, ask=1.1008)),
            "fraicheur_des_donnees": (ACCOUNT, replace(ORDER, quote_age_seconds=120.0)),
            "marge": (ACCOUNT, replace(ORDER, required_margin=40_000.0)),
        }
        for rule, (account, order) in cases.items():
            with self.subTest(rule=rule):
                decision = mt5_risk.evaluate_entry(account, order, LIMITS)
                self.assertFalse(decision.allowed)
                self.assertIn(rule, failed(decision))
        both = mt5_risk.evaluate_entry(replace(ACCOUNT, environment="REAL"), replace(ORDER, quote_age_seconds=999.0), LIMITS)
        self.assertGreaterEqual(len(failed(both)), 2)  # no early exit: the audit sees every failure

    def test_exposure_per_symbol_and_total(self):
        held = replace(ACCOUNT, positions=({"symbol": "EURUSD", "volume": 1.0},))
        self.assertIn("exposition_par_instrument", failed(mt5_risk.evaluate_entry(held, ORDER, LIMITS)))
        heavy = replace(ACCOUNT, positions=tuple({"symbol": s, "volume": 2.0} for s in ("AUDUSD", "GBPUSD", "USDJPY", "NZDUSD")))
        self.assertIn("exposition_totale", failed(mt5_risk.evaluate_entry(heavy, ORDER, LIMITS)))

    def test_missing_or_unreadable_data_fails_closed(self):
        blank = AccountState()
        self.assertFalse(mt5_risk.entries_state(blank, LIMITS).allowed)
        unknown = replace(ORDER, loss_at_stop_usd=None, required_margin=None, quote_age_seconds=None, bid=None, ask=None,
                          volume_min=None)
        decision = mt5_risk.evaluate_entry(ACCOUNT, unknown, LIMITS)
        self.assertFalse(decision.allowed)
        self.assertTrue({"taille_de_position", "marge", "fraicheur_des_donnees", "spread", "volume_conforme"} <= failed(decision))
        nan = replace(ACCOUNT, equity=float("nan"))
        self.assertFalse(mt5_risk.entries_state(nan, LIMITS).allowed)

    def test_an_invalid_profile_refuses_every_entry(self):
        error = RiskConfigError("profil de test")
        decision = mt5_risk.evaluate_entry(ACCOUNT, ORDER, error)
        self.assertFalse(decision.allowed)
        self.assertEqual([c.name for c in decision.checks], ["profil_de_risque"])


class UsdExposureTests(unittest.TestCase):
    def test_signs_follow_the_side_of_the_dollar(self):
        self.assertAlmostEqual(mt5_risk.usd_exposure("EURUSD", "LONG", 1, 1.1), -110_000, places=3)
        self.assertAlmostEqual(mt5_risk.usd_exposure("EURUSD", "SHORT", 1, 1.1), 110_000, places=3)
        self.assertEqual(mt5_risk.usd_exposure("USDJPY", "LONG", 2, 150), 200_000)
        self.assertEqual(mt5_risk.usd_exposure("USDJPY", "SHORT", 2, 150), -200_000)
        self.assertEqual(mt5_risk.usd_exposure("EURGBP", "LONG", 1, 0.85), 0.0)

    def held(self, *positions):
        return replace(ACCOUNT, positions=tuple({"price_open": 1.0, **p} for p in positions))

    def test_an_entry_that_piles_onto_the_same_side_of_the_dollar_is_refused(self):
        account = self.held({"symbol": "USDJPY", "side": "LONG", "volume": 2.0}, {"symbol": "USDCAD", "side": "LONG", "volume": 2.0})
        piling = replace(ORDER, symbol="USDCHF", side="LONG")
        self.assertIn("exposition_nette_usd", failed(mt5_risk.evaluate_entry(account, piling, LIMITS)))
        adding = replace(ORDER, symbol="EURUSD", side="SHORT")  # short EURUSD is long the dollar too
        self.assertIn("exposition_nette_usd", failed(mt5_risk.evaluate_entry(account, adding, LIMITS)))

    def test_an_entry_that_reduces_the_net_dollar_exposure_is_always_allowed_by_this_rule(self):
        account = self.held({"symbol": "USDJPY", "side": "LONG", "volume": 2.0}, {"symbol": "USDCAD", "side": "LONG", "volume": 2.0})
        reducing = replace(ORDER, symbol="EURUSD", side="LONG")  # long EURUSD is short the dollar
        self.assertNotIn("exposition_nette_usd", failed(mt5_risk.evaluate_entry(account, reducing, LIMITS)))

    def test_offsetting_positions_leave_room_and_the_limit_is_configurable(self):
        account = self.held({"symbol": "USDJPY", "side": "LONG", "volume": 2.0}, {"symbol": "EURUSD", "side": "LONG", "volume": 1.0})
        order = replace(ORDER, symbol="USDCAD", side="LONG")
        self.assertNotIn("exposition_nette_usd", failed(mt5_risk.evaluate_entry(account, order, LIMITS)))
        tight = replace(LIMITS, max_net_usd_exposure_x=0.5)
        self.assertIn("exposition_nette_usd", failed(mt5_risk.evaluate_entry(account, order, tight)))
        with self.assertRaises(RiskConfigError):
            replace(LIMITS, max_net_usd_exposure_x=0.0).validate()


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_tempdir()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, payload):
        path = self.dir / "risk.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_defaults_and_a_valid_file(self):
        self.assertEqual(mt5_risk.load_limits(None), RiskLimits())
        limits = mt5_risk.load_limits(self.write({"max_positions": 3, "allowed_symbols": ["EURUSD"]}))
        self.assertEqual((limits.max_positions, limits.allowed_symbols), (3, ("EURUSD",)))
        self.assertEqual(limits.public()["version"], "1")

    def test_invalid_files_are_refused(self):
        for payload in ({"risk_per_trade_pct": 50}, {"max_positions": 0}, {"unknown": 1}, {"allowed_symbols": ["XAUUSD"]},
                        {"max_drawdown_pct": float("nan")}, [1, 2]):
            with self.subTest(payload=payload), self.assertRaises(RiskConfigError):
                mt5_risk.load_limits(self.write(payload))
        with self.assertRaises(RiskConfigError):
            mt5_risk.load_limits(self.dir / "missing.json")
        (self.dir / "bad.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(RiskConfigError):
            mt5_risk.load_limits(self.dir / "bad.json")


if __name__ == "__main__":
    unittest.main()
