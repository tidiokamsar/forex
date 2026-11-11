"""Independent MT5 risk gateway: fail-closed defaults and one test per rule."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from _support import workspace_tempdir
from astra import mt5_risk
from astra.mt5_risk import AccountState, OrderRequest, RiskConfigError, RiskLimits

LIMITS = RiskLimits()
ACCOUNT = AccountState(environment="DEMO", currency="USD", reconciliation_ok=True, unknown_orders=0,
                       week_start_equity=100_000.0, month_start_equity=100_000.0, login=114765880,
                       expected_login=114765880, equity=100_000.0, margin=5_000.0,
                       day_start_equity=100_000.0, peak_equity=100_000.0, positions=(), connector_ok=True,
                       algo_trading_enabled=True, entries_allowed_by_server=True, kill_switch_active=False)
ORDER = OrderRequest(symbol="EURUSD", side="LONG", lots=0.83, loss_at_stop_usd=498.0, required_margin=900.0, bid=1.1,
                     ask=1.10002, quote_age_seconds=4.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
                     contract_size=100_000.0)


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
            "instrument": (ACCOUNT, replace(ORDER, symbol="BTCUSD")),
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


class PeriodLossTests(unittest.TestCase):
    def test_a_bad_week_or_month_blocks_new_entries_even_when_today_is_fine(self):
        week = replace(ACCOUNT, equity=94_800.0, day_start_equity=94_900.0, week_start_equity=100_000.0,
                       month_start_equity=100_000.0, peak_equity=100_000.0)
        small = replace(ORDER, loss_at_stop_usd=400.0)  # within the size allowed on the lower equity
        self.assertEqual(failed(mt5_risk.evaluate_entry(week, small, LIMITS)), {"perte_hebdomadaire"})  # -5.2 % this week
        month = replace(week, week_start_equity=95_000.0, month_start_equity=103_500.0, peak_equity=103_500.0)
        self.assertEqual(failed(mt5_risk.evaluate_entry(month, small, LIMITS)), {"perte_mensuelle"})  # -8.4 % this month
        self.assertIn("perte_mensuelle", failed(mt5_risk.entries_state(replace(month, month_start_equity=None), LIMITS)))

    def test_the_period_limits_must_stay_ordered(self):
        with self.assertRaises(RiskConfigError):
            replace(LIMITS, weekly_loss_limit_pct=2.0).validate()  # below the daily limit
        with self.assertRaises(RiskConfigError):
            replace(LIMITS, monthly_loss_limit_pct=4.0).validate()  # below the weekly limit


class FreezeTests(unittest.TestCase):
    def test_a_blocking_reconciliation_alert_or_an_unknown_state_freezes_new_entries(self):
        for account in (replace(ACCOUNT, reconciliation_ok=False), replace(ACCOUNT, reconciliation_ok=None)):
            decision = mt5_risk.evaluate_entry(account, ORDER, LIMITS)
            self.assertIn("rapprochement", failed(decision))
            self.assertFalse(decision.allowed)
        self.assertTrue(mt5_risk.evaluate_entry(ACCOUNT, ORDER, LIMITS).allowed)

    def test_an_order_with_an_unknown_outcome_freezes_new_entries_everywhere(self):
        for count in (1, 3, None):
            decision = mt5_risk.evaluate_entry(replace(ACCOUNT, unknown_orders=count), ORDER, LIMITS)
            self.assertIn("ordres_inconnus", failed(decision))
            self.assertFalse(mt5_risk.entries_state(replace(ACCOUNT, unknown_orders=count), LIMITS).allowed)  # the panel too

    def test_the_contract_size_must_be_the_one_the_calculations_assume(self):
        for size in (None, 1_000.0, 10_000.0):
            self.assertIn("taille_de_contrat", failed(mt5_risk.evaluate_entry(ACCOUNT, replace(ORDER, contract_size=size), LIMITS)))


class WiderInstrumentTests(unittest.TestCase):
    GOLD = replace(ORDER, symbol="XAUUSD", lots=0.1, loss_at_stop_usd=300.0, required_margin=400.0, bid=2650.0, ask=2650.2,
                   contract_size=100.0)

    def test_gold_and_silver_use_their_own_contract_size_and_a_wrong_one_is_refused(self):
        self.assertEqual(failed(mt5_risk.evaluate_entry(ACCOUNT, self.GOLD, LIMITS)), set())
        self.assertIn("taille_de_contrat", failed(mt5_risk.evaluate_entry(ACCOUNT, replace(self.GOLD, contract_size=100_000.0), LIMITS)))
        silver = replace(self.GOLD, symbol="XAGUSD", bid=31.0, ask=31.01, contract_size=5000.0)
        self.assertEqual(failed(mt5_risk.evaluate_entry(ACCOUNT, silver, LIMITS)), set())
        self.assertIn("taille_de_contrat", failed(mt5_risk.evaluate_entry(ACCOUNT, replace(silver, contract_size=100.0), LIMITS)))

    def test_the_exposure_of_metals_is_valued_with_their_contract(self):
        self.assertAlmostEqual(mt5_risk.usd_exposure("XAUUSD", "LONG", 2, 2650.0), -530_000.0, places=3)
        self.assertAlmostEqual(mt5_risk.usd_exposure("XAGUSD", "SHORT", 1, 31.0), 155_000.0, places=3)

    def test_the_spread_cap_outside_the_seven_pairs_follows_the_instrument(self):
        wide = replace(self.GOLD, ask=2652.5)  # 250 pips at 0.01: far above 3 times the usual 30
        self.assertIn("spread", failed(mt5_risk.evaluate_entry(ACCOUNT, wide, LIMITS)))
        cross = replace(ORDER, symbol="EURGBP", bid=0.85, ask=0.85015, loss_at_stop_usd=400.0, required_margin=500.0)
        self.assertEqual(failed(mt5_risk.evaluate_entry(ACCOUNT, cross, LIMITS)), set())  # 1.5 pips, cap 3
        self.assertIn("spread", failed(mt5_risk.evaluate_entry(ACCOUNT, replace(cross, ask=0.8504), LIMITS)))  # 4 pips

    def test_the_number_of_crosses_without_a_dollar_leg_is_bounded(self):
        cross = replace(ORDER, symbol="EURGBP", bid=0.85, ask=0.85015, loss_at_stop_usd=400.0, required_margin=500.0)
        held = tuple({"symbol": s, "volume": 0.1, "side": "LONG", "price_open": 1.0, "price_current": 1.0}
                     for s in ("EURJPY", "AUDCAD", "CHFJPY"))
        self.assertIn("croisees_sans_dollar", failed(mt5_risk.evaluate_entry(replace(ACCOUNT, positions=held), cross, LIMITS)))
        self.assertNotIn("croisees_sans_dollar", failed(mt5_risk.evaluate_entry(replace(ACCOUNT, positions=held[:2]), cross, LIMITS)))
        self.assertNotIn("croisees_sans_dollar", failed(mt5_risk.evaluate_entry(replace(ACCOUNT, positions=held), ORDER, LIMITS)))

    def test_every_instrument_of_the_registry_is_allowed_by_default_and_nothing_else(self):
        from astra.marketdata import INSTRUMENTS

        self.assertEqual(set(LIMITS.allowed_symbols), set(INSTRUMENTS))


class UsdExposureTests(unittest.TestCase):
    def test_signs_follow_the_side_of_the_dollar(self):
        self.assertAlmostEqual(mt5_risk.usd_exposure("EURUSD", "LONG", 1, 1.1), -110_000, places=3)
        self.assertAlmostEqual(mt5_risk.usd_exposure("EURUSD", "SHORT", 1, 1.1), 110_000, places=3)
        self.assertEqual(mt5_risk.usd_exposure("USDJPY", "LONG", 2, 150), 200_000)
        self.assertEqual(mt5_risk.usd_exposure("USDJPY", "SHORT", 2, 150), -200_000)
        self.assertEqual(mt5_risk.usd_exposure("EURGBP", "LONG", 1, 0.85), 0.0)

    def held(self, *positions):
        return replace(ACCOUNT, positions=tuple({"price_open": 1.0, **p} for p in positions))

    def test_a_position_is_valued_at_its_own_price_not_at_the_price_of_the_order(self):
        # A EURUSD position at 1.10 next to a USDJPY order at 150: valuing it at 150 would multiply its weight by 136.
        account = self.held({"symbol": "EURUSD", "side": "LONG", "volume": 1.0, "price_open": 1.10, "price_current": 1.10})
        order = replace(ORDER, symbol="USDJPY", side="SHORT", bid=150.0, ask=150.02)
        self.assertNotIn("exposition_nette_usd", failed(mt5_risk.evaluate_entry(account, order, LIMITS)))

    def test_a_position_without_a_known_price_or_side_fails_closed(self):
        for bad in ({"price_open": None, "price_current": None}, {"price_open": 0.0}, {"side": "?"}):
            account = self.held({"symbol": "USDJPY", "side": "LONG", "volume": 1.0, **bad})
            decision = mt5_risk.evaluate_entry(account, replace(ORDER, symbol="EURUSD"), LIMITS)
            self.assertIn("exposition_nette_usd", failed(decision), bad)
            self.assertFalse(decision.allowed)

    def test_an_account_that_is_not_in_usd_is_refused(self):
        self.assertIn("devise_du_compte", failed(mt5_risk.evaluate_entry(replace(ACCOUNT, currency="EUR"), ORDER, LIMITS)))
        self.assertIn("devise_du_compte", failed(mt5_risk.evaluate_entry(replace(ACCOUNT, currency=None), ORDER, LIMITS)))

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
        for payload in ({"risk_per_trade_pct": 50}, {"max_positions": 0}, {"unknown": 1}, {"allowed_symbols": ["BTCUSD"]},
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
