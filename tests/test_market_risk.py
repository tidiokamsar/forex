import ast
import dataclasses
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from astra.instruments import DEFAULT_SPECS, InstrumentSpec
from astra.markets import CryptoSpotSpec, ForexCfdSpec, crypto_futures, fixed_expiry_options
from astra.markets import costs as costs_module
from astra.markets import risk_policies as policies_module
from astra.markets.costs import (
    FUNDING_COST_ENABLED,
    UNCALIBRATED,
    BookTicker,
    Calibration,
    CommissionBasis,
    CryptoSpotCostModel,
    CryptoSpotFeeSchedule,
    FeeUnknown,
    FundingCostAssumption,
    SlippageAssumption,
    SpotSide,
    forex_cost_model,
)
from astra.markets.risk_policies import (
    DERIVATIVES_NOT_ENABLED_REASON,
    CryptoFuturesRiskPolicy,
    CryptoSpotOrderIntent,
    CryptoSpotRiskContext,
    CryptoSpotRiskParameters,
    CryptoSpotRiskPolicy,
    FixedExpiryOptionsRiskPolicy,
    ForexCfdPreCheck,
    MarketRiskDecision,
    SpotBuyLedger,
    SpotPosition,
    Verdict,
    canonical_parameters,
    policy_for,
)
from astra.models import CostModel, RunMode
from astra.venues.contract import Balance, MarketType

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[1] / "src"


def spot_spec(**overrides):
    fields = {
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "status": "TRADING",
        "tick_size": Decimal("0.01"),
        "step_size": Decimal("0.00001"),
        "min_qty": Decimal("0.00001"),
        "max_qty": Decimal("9000"),
        "min_notional": Decimal("5"),
        "price_precision": 2,
        "quantity_precision": 5,
        "maker_fee": Decimal("0.001"),
        "taker_fee": Decimal("0.001"),
        "source": "ILLUSTRATIVE",
        "as_of": NOW - timedelta(seconds=60),
    }
    fields.update(overrides)
    return CryptoSpotSpec(**fields)


def book(**overrides):
    fields = {
        "symbol": "BTCUSDT",
        "bid_price": Decimal("50000.00"),
        "bid_qty": Decimal("1"),
        "ask_price": Decimal("50001.00"),
        "ask_qty": Decimal("1"),
        "as_of": NOW - timedelta(seconds=1),
        "source": "TEST",
    }
    fields.update(overrides)
    return BookTicker(**fields)


def fees(**overrides):
    fields = {
        "symbol": "BTCUSDT",
        "maker_rate": Decimal("0.001"),
        "taker_rate": Decimal("0.001"),
        "source": "TEST",
        "as_of": NOW - timedelta(hours=1),
    }
    fields.update(overrides)
    return CryptoSpotFeeSchedule(**fields)


def balances(quote="100000", base="0", extra=()):
    as_of = NOW - timedelta(seconds=5)
    return (
        Balance("USDT", Decimal(quote), Decimal(0), as_of),
        Balance("BTC", Decimal(base), Decimal(0), as_of),
        *extra,
    )


def balances_at(as_of, base="0"):
    return (Balance("USDT", Decimal("100000"), Decimal(0), as_of), Balance("BTC", Decimal(base), Decimal(0), as_of))


HOLDING = {"balances": balances(base="0.5")}


def context(**overrides):
    fields = {
        "mode": RunMode.SIMULATION,
        "now": NOW,
        "venue_id": "binance_spot",
        "spec": spot_spec(),
        "book": book(),
        "balances": balances(),
        "fees": fees(),
        "day_buy_notional": Decimal(0),
        "day_realized_pnl": Decimal(0),
        "borrowing_enabled": False,
    }
    fields.update(overrides)
    return CryptoSpotRiskContext(**fields)


def buy(**overrides):
    # 0.02 BTC at 50000 = 1000 USDT, 1% of a 100000 USDT free balance.
    fields = {
        "intent_id": "spot-1",
        "symbol": "BTCUSDT",
        "side": SpotSide.BUY,
        "quantity": Decimal("0.02"),
        "limit_price": Decimal("50000.00"),
        "created_at": NOW,
        "stop_price": Decimal("49000"),
        "expected_edge_fraction": Decimal("0.01"),
    }
    fields.update(overrides)
    return CryptoSpotOrderIntent(**fields)


def sell(**overrides):
    fields = {
        "intent_id": "spot-2",
        "symbol": "BTCUSDT",
        "side": SpotSide.SELL,
        "quantity": Decimal("0.1"),
        "limit_price": Decimal("50000.00"),
        "created_at": NOW,
    }
    fields.update(overrides)
    return CryptoSpotOrderIntent(**fields)


POLICY = CryptoSpotRiskPolicy()


class CryptoSpotAllowTests(unittest.TestCase):
    def test_simulation_buy_is_allowed_with_rounded_values_and_version(self):
        decision = CryptoSpotRiskPolicy().evaluate(buy(quantity=Decimal("0.0200009"), limit_price=Decimal("50000.009")), context())
        self.assertEqual(decision.verdict, Verdict.ALLOW, decision.reasons)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.quantity, Decimal("0.02000"))
        self.assertEqual(decision.price, Decimal("50000.00"))
        self.assertEqual(decision.notional, Decimal("1000.0000000"))
        self.assertEqual(decision.policy_name, "crypto_spot")
        self.assertEqual(decision.policy_version, "crypto-spot-1")
        self.assertEqual(decision.market_type, MarketType.CRYPTO_SPOT)
        self.assertEqual(decision.estimated_fee_quote, Decimal("1.0000000000"))
        self.assertTrue(any(UNCALIBRATED in note for note in decision.notes))

    def test_simulation_sell_within_free_base_is_allowed(self):
        decision = CryptoSpotRiskPolicy().evaluate(sell(), context(**HOLDING))
        self.assertEqual(decision.verdict, Verdict.ALLOW, decision.reasons)

    def test_tightening_a_stop_is_allowed(self):
        position = SpotPosition(Decimal("0.1"), Decimal("50000"), Decimal("49000"))
        self.assertTrue(CryptoSpotRiskPolicy().evaluate_stop_change(position, Decimal("49500")).allowed)


class CryptoSpotRefusalTests(unittest.TestCase):
    def assert_refused(self, decision, reason):
        self.assertEqual(decision.verdict, Verdict.REFUSE)
        self.assertIn(reason, decision.reasons)
        self.assertEqual(decision.policy_version, POLICY.version)

    def test_only_simulation_can_allow(self):
        demo = CryptoSpotRiskPolicy().evaluate(buy(), context(mode=RunMode.DEMO))
        self.assert_refused(demo, "crypto_spot_allowed_only_in_simulation_in_this_lot")
        self.assertIn("venue_trading_not_enabled_in_this_lot", demo.reasons)
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(mode=RunMode.LIVE)), "live_mode_not_supported")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(mode="SIMULATION")), "invalid_mode")

    def test_symbol_not_trading(self):
        decision = CryptoSpotRiskPolicy().evaluate(buy(), context(spec=spot_spec(status="HALT")))
        self.assert_refused(decision, "symbol_not_trading")
        self.assertEqual(decision.reasons.count("symbol_not_trading"), 1)

    def test_stale_and_future_quote_and_spec(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(book=book(as_of=NOW - timedelta(seconds=6)))), "quote_stale")
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(), context(book=book(as_of=NOW + timedelta(seconds=1)))), "quote_future_dated"
        )
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(), context(spec=spot_spec(as_of=NOW - timedelta(seconds=301)))), "spec_stale"
        )
        stale_balance = (Balance("USDT", Decimal("100000"), Decimal(0), NOW - timedelta(minutes=5)), balances()[1])
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(balances=stale_balance)), "balance_USDT_stale")
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(), context(fees=fees(as_of=NOW - timedelta(days=2)))), "fees_stale"
        )

    def test_unknown_market_data(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(spec=None)), "spec_unknown")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(book=None)), "quote_unknown")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(book=book(symbol="ETHUSDT"))), "quote_symbol_mismatch")

    def test_unknown_balances(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(balances=None)), "balances_unknown")
        only_btc = (balances()[1],)
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(balances=only_btc)), "balance_unknown:USDT")
        only_usdt = (balances()[0],)
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(sell(), context(balances=only_usdt)), "balance_unknown:BTC")

    def test_unknown_fees_block_net_expectancy(self):
        decision = CryptoSpotRiskPolicy().evaluate(buy(), context(fees=None))
        self.assert_refused(decision, "fees_unknown")
        self.assertIsNone(decision.estimated_fee_quote)
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(fees=fees(symbol="ETHUSDT"))), "fees_symbol_mismatch")

    def test_third_asset_fee_needs_price_and_balance(self):
        bnb_fees = fees(fee_asset="BNB")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(fees=bnb_fees)), "fee_asset_conversion_unknown")
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(), context(fees=bnb_fees, fee_asset_price_in_quote=Decimal("500"), fee_asset_price_as_of=NOW)), "balance_unknown:BNB"
        )
        poor = balances(extra=(Balance("BNB", Decimal("0.001"), Decimal(0), NOW),))
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(), context(fees=bnb_fees, balances=poor, fee_asset_price_in_quote=Decimal("500"), fee_asset_price_as_of=NOW)),
            "insufficient_fee_asset_balance",
        )
        rich = balances(extra=(Balance("BNB", Decimal("1"), Decimal(0), NOW),))
        allowed = CryptoSpotRiskPolicy().evaluate(buy(), context(fees=bnb_fees, balances=rich, fee_asset_price_in_quote=Decimal("500"), fee_asset_price_as_of=NOW))
        self.assertTrue(allowed.allowed, allowed.reasons)
        no_age = CryptoSpotRiskPolicy().evaluate(buy(), context(fees=bnb_fees, balances=rich, fee_asset_price_in_quote=Decimal("500")))
        self.assertIn("fee_asset_price_timestamp_invalid", no_age.reasons)
        stale = CryptoSpotRiskPolicy().evaluate(buy(), context(fees=bnb_fees, balances=rich, fee_asset_price_in_quote=Decimal("500"),
                                                               fee_asset_price_as_of=NOW - timedelta(seconds=6)))
        self.assertIn("fee_asset_price_stale", stale.reasons)

    def test_expected_edge_required_and_net_expectancy_positive(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(expected_edge_fraction=None), context()), "expected_edge_unknown")
        # Round trip: 2 * 0.001 fee + ~0.00002 spread + 2 * 0.0005 slippage > 0.003.
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(expected_edge_fraction=Decimal("0.003")), context()), "net_expectancy_not_positive"
        )

    def test_rounding_to_zero_and_minimum_notional(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(quantity=Decimal("0.000009")), context()), "invalid_quantity")
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(quantity=Decimal("0.00001")), context()), "notional_below_minimum"
        )
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(quantity=0.02), context()), "invalid_quantity")

    def test_order_notional_cap(self):
        # 0.05 BTC = 2500 USDT > 2% of 100000.
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(quantity=Decimal("0.05")), context()), "order_notional_exceeds_cap")
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(buy(quantity=Decimal("0.04")), context()).allowed)

    def test_daily_notional_cap(self):
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(buy(), context(day_buy_notional=Decimal("9500"))), "daily_notional_exceeds_cap"
        )
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(buy(), context(day_buy_notional=Decimal("9000"))).allowed)
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(day_buy_notional=None)), "daily_notional_unknown")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(day_buy_notional=Decimal(-1))), "daily_notional_invalid")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(venue_id="")), "venue_unknown")

    def test_no_leverage(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(borrowing_enabled=True)), "borrowing_enabled_or_unknown")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(borrowing_enabled=None)), "borrowing_enabled_or_unknown")
        # Notional above free quote: cannot be financed by borrowing.
        small = balances(quote="900")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(balances=small)), "insufficient_free_quote_balance")
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(leverage=2)
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(leverage=True)

    def test_no_shorting(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(sell(quantity=Decimal("0.6")), context(**HOLDING)), "sell_exceeds_free_base_balance")
        self.assert_refused(
            CryptoSpotRiskPolicy().evaluate(sell(), context(balances=balances(base="0"))), "sell_exceeds_free_base_balance"
        )
        locked = (balances()[0], Balance("BTC", Decimal("0.01"), Decimal("1"), NOW))
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(sell(), context(balances=locked)), "sell_exceeds_free_base_balance")
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(allow_short=True)

    def test_holdings_without_position_state_are_refused(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(**HOLDING)), "position_unknown")
        locked_only = (balances()[0], Balance("BTC", Decimal(0), Decimal("0.1"), NOW))
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(balances=locked_only)), "position_unknown")
        winner = SpotPosition(Decimal("0.5"), Decimal("48000"), Decimal("47000"))
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(buy(), context(position=winner, **HOLDING)).allowed)

    def test_limit_price_band(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(sell(limit_price=Decimal("500.00")), context(**HOLDING)), "limit_price_outside_band")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(sell(limit_price=Decimal("49499.99")), context(**HOLDING)), "limit_price_outside_band")
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(sell(limit_price=Decimal("49500.00")), context(**HOLDING)).allowed)
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(sell(limit_price=Decimal("60000.00")), context(**HOLDING)).allowed)
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(limit_price=Decimal("50501.02"), quantity=Decimal("0.01")), context()), "limit_price_outside_band")
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(buy(limit_price=Decimal("50501.01"), quantity=Decimal("0.01")), context()).allowed)
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(max_limit_deviation_fraction=Decimal("0.05"))

    def test_kill_switch(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(kill_switch_active=True)), "kill_switch_active_or_unknown")

    def test_malformed_inputs(self):
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), object()), "invalid_risk_context")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(object(), context()), "invalid_order_intent")
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(created_at=datetime(2026, 1, 1)), context()), "invalid_intent_timestamp")  # noqa: DTZ001
        self.assert_refused(CryptoSpotRiskPolicy().evaluate(buy(), context(now=datetime(2026, 1, 1))), "invalid_reference_time")  # noqa: DTZ001


class AntiMartingaleTests(unittest.TestCase):
    def test_martingale_doubling_after_loss_is_refused(self):
        ctx = context(day_realized_pnl=Decimal("-50"), last_buy_notional=Decimal("500"))
        decision = CryptoSpotRiskPolicy().evaluate(buy(), ctx)  # 1000 after a 500 loser: doubling.
        self.assertIn("risk_increase_after_loss", decision.reasons)

    def test_same_or_smaller_size_after_loss_is_not_flagged(self):
        ctx = context(day_realized_pnl=Decimal("-50"), last_buy_notional=Decimal("1000"))
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(buy(), ctx).allowed)

    def test_unknown_last_buy_after_loss_is_refused(self):
        decision = CryptoSpotRiskPolicy().evaluate(buy(), context(day_realized_pnl=Decimal("-50")))
        self.assertIn("last_buy_notional_unknown", decision.reasons)
        self.assertIn("last_buy_notional_invalid",
                      CryptoSpotRiskPolicy().evaluate(buy(), context(day_realized_pnl=Decimal("-50"), last_buy_notional=Decimal(0))).reasons)

    def test_ledger_bounds_the_callers_last_buy(self):
        policy = CryptoSpotRiskPolicy()
        self.assertTrue(policy.evaluate(buy(quantity=Decimal("0.01")), context()).allowed)  # books 500
        omitted = policy.evaluate(buy(intent_id="b2", quantity=Decimal("0.01")), context(day_realized_pnl=Decimal("-50")))
        self.assertIn("last_buy_notional_unknown", omitted.reasons)
        # A caller cannot raise the reference above what the ledger booked.
        lied = policy.evaluate(buy(intent_id="b3"), context(day_realized_pnl=Decimal("-50"), last_buy_notional=Decimal("5000")))
        self.assertIn("risk_increase_after_loss", lied.reasons)
        same = policy.evaluate(buy(intent_id="b4", quantity=Decimal("0.01")),
                               context(day_realized_pnl=Decimal("-50"), last_buy_notional=Decimal("5000")))
        self.assertTrue(same.allowed, same.reasons)

    def test_unknown_pnl_is_refused(self):
        self.assertIn("day_realized_pnl_unknown", CryptoSpotRiskPolicy().evaluate(buy(), context(day_realized_pnl=None)).reasons)

    def test_adding_to_loser_is_refused(self):
        position = SpotPosition(Decimal("0.1"), Decimal("52000"), Decimal("49000"))
        decision = CryptoSpotRiskPolicy().evaluate(buy(), context(position=position))
        self.assertIn("adding_to_losing_position", decision.reasons)
        winner = SpotPosition(Decimal("0.1"), Decimal("48000"), Decimal("47000"))
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(buy(), context(position=winner)).allowed)

    def test_buy_without_stop_is_refused(self):
        self.assertIn("stop_required", CryptoSpotRiskPolicy().evaluate(buy(stop_price=None), context()).reasons)
        self.assertIn("invalid_stop", CryptoSpotRiskPolicy().evaluate(buy(stop_price=Decimal("51000")), context()).reasons)
        unprotected = SpotPosition(Decimal("0.1"), Decimal("48000"), None)
        self.assertIn("open_position_without_stop", CryptoSpotRiskPolicy().evaluate(buy(), context(position=unprotected)).reasons)

    def test_stop_removal_and_widening_are_refused(self):
        position = SpotPosition(Decimal("0.1"), Decimal("50000"), Decimal("49000"))
        self.assertIn("stop_removal_refused", CryptoSpotRiskPolicy().evaluate_stop_change(position, None).reasons)
        self.assertIn("stop_widening_refused", CryptoSpotRiskPolicy().evaluate_stop_change(position, Decimal("48000")).reasons)
        self.assertIn("live_mode_not_supported", CryptoSpotRiskPolicy().evaluate_stop_change(position, Decimal("49500"), mode=RunMode.LIVE).reasons)

    def test_policy_has_no_loss_scaling_parameter(self):
        names = {f.name for f in dataclasses.fields(CryptoSpotRiskPolicy)}
        for forbidden in ("multiplier", "martingale", "recovery", "scale_after_loss", "grid"):
            self.assertFalse(any(forbidden in name for name in names), forbidden)


class SpotBuyLedgerTests(unittest.TestCase):
    def test_allowed_buys_are_booked_and_split_orders_hit_the_daily_cap(self):
        policy = CryptoSpotRiskPolicy()
        results = [policy.evaluate(buy(intent_id=f"s{i}", quantity=Decimal("0.038")), context()) for i in range(8)]
        self.assertEqual([r.allowed for r in results], [True] * 5 + [False] * 3)
        self.assertIn("daily_notional_exceeds_cap", results[5].reasons)
        records = policy.ledger.records
        self.assertEqual(len(records), 5)
        self.assertEqual({r.policy_fingerprint for r in records}, {policy.parameters_fingerprint})
        self.assertEqual(policy.ledger.day_total("binance_spot", "USDT", NOW.date()), Decimal("9500.0000"))

    def test_caller_floor_can_only_raise_the_day_total(self):
        policy = CryptoSpotRiskPolicy()
        for i in range(5):
            policy.evaluate(buy(intent_id=f"f{i}", quantity=Decimal("0.038")), context())
        # Claiming nothing was bought today does not reset the ledger total.
        self.assertIn("daily_notional_exceeds_cap",
                      policy.evaluate(buy(intent_id="f5", quantity=Decimal("0.038")), context(day_buy_notional=Decimal(0))).reasons)

    def test_refused_decisions_are_not_booked(self):
        policy = CryptoSpotRiskPolicy()
        policy.evaluate(buy(), context(mode=RunMode.DEMO))
        policy.evaluate(buy(quantity=Decimal("0.05")), context())
        self.assertEqual(policy.ledger.records, ())

    def test_day_boundary_is_utc_and_time_cannot_go_back(self):
        policy = CryptoSpotRiskPolicy()

        def at(moment):
            return context(now=moment, book=book(as_of=moment), spec=spot_spec(as_of=moment), balances=balances_at(moment),
                           fees=fees(as_of=moment))

        late = NOW.replace(hour=23, minute=59, second=50)
        for i in range(5):
            self.assertTrue(policy.evaluate(buy(intent_id=f"d{i}", quantity=Decimal("0.038")), at(late)).allowed)
        self.assertFalse(policy.evaluate(buy(intent_id="d5", quantity=Decimal("0.038")), at(late)).allowed)
        nxt = late + timedelta(seconds=20)
        self.assertTrue(policy.evaluate(buy(intent_id="d6", quantity=Decimal("0.038")), at(nxt)).allowed)
        backdated = policy.evaluate(buy(intent_id="d7", quantity=Decimal("0.038")), at(late - timedelta(days=1)))
        self.assertIn("ledger_time_regression", backdated.reasons)

    def test_cap_is_shared_across_symbols_with_the_same_quote_asset(self):
        policy = CryptoSpotRiskPolicy()
        for i in range(5):
            policy.evaluate(buy(intent_id=f"x{i}", quantity=Decimal("0.038")), context())
        eth = spot_spec(symbol="ETHUSDT", base_asset="ETH")
        eth_ctx = context(spec=eth, book=book(symbol="ETHUSDT"), fees=fees(symbol="ETHUSDT"),
                          balances=(*balances(), Balance("ETH", Decimal(0), Decimal(0), NOW)))
        decision = policy.evaluate(buy(intent_id="e1", symbol="ETHUSDT", quantity=Decimal("0.038")), eth_ctx)
        self.assertIn("daily_notional_exceeds_cap", decision.reasons)
        # An unregistered venue cannot open a fresh cap on the same balance (MV-A-16): it is refused outright.
        other_venue = policy.evaluate(buy(intent_id="o1", quantity=Decimal("0.038")), context(venue_id="other_venue"))
        self.assertIn("venue_not_in_registry", other_venue.reasons)

    def test_ledger_has_no_delete_or_update_api(self):
        public = {n for n in dir(SpotBuyLedger) if not n.startswith("_")}
        self.assertEqual(public, {"records", "day_total", "last_buy_notional"})
        self.assertIsInstance(CryptoSpotRiskPolicy().ledger.records, tuple)

    def test_replace_keeps_the_same_ledger(self):
        policy = CryptoSpotRiskPolicy()
        stricter = replace(policy, version="crypto-spot-1-strict", max_order_notional_fraction=Decimal("0.01"))
        self.assertIs(stricter.ledger, policy.ledger)


class CryptoSpotPolicyValidationTests(unittest.TestCase):
    def test_policy_is_final(self):
        with self.assertRaises(TypeError):
            class Sub(CryptoSpotRiskPolicy):
                pass

    def test_ceilings_ignore_patched_module_constants(self):
        from unittest.mock import patch

        with patch.object(policies_module, "MAX_ORDER_NOTIONAL_FRACTION", Decimal("1")), \
                patch.object(policies_module, "MAX_DAILY_NOTIONAL_FRACTION", Decimal("1")):
            with self.assertRaises(ValueError):
                CryptoSpotRiskPolicy(max_order_notional_fraction=Decimal("0.5"), max_daily_notional_fraction=Decimal("1"))
        with self.assertRaises(ValueError):
            CryptoSpotRiskParameters(max_daily_notional_fraction=Decimal("0.2"))

    def test_slippage_floor_and_label(self):
        for value in ("0", "0.0001", "0.0004999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CryptoSpotRiskPolicy(slippage=SlippageAssumption(Decimal(value)))
        with self.assertRaises(ValueError):
            SlippageAssumption(Decimal("0.001"), calibration="CALIBRATED")
        self.assertIs(SlippageAssumption(calibration="UNCALIBRATED").calibration, Calibration.UNCALIBRATED)
        self.assertTrue(CryptoSpotRiskPolicy(version="v-slip", slippage=SlippageAssumption(Decimal("0.001"))).evaluate(
            buy(expected_edge_fraction=Decimal("0.02")), context()).allowed)

    def test_every_decision_records_parameters_and_fingerprint(self):
        policy = CryptoSpotRiskPolicy()
        decisions = (policy.evaluate(buy(), context()), policy.evaluate(object(), object()),
                     policy.evaluate_stop_change(None, None))
        for decision in decisions:
            self.assertEqual(decision.parameters_fingerprint, policy.parameters_fingerprint)
            self.assertIn(("max_order_notional_fraction", "0.02"), decision.parameters)
            self.assertIn(("slippage_calibration", "UNCALIBRATED"), decision.parameters)
        self.assertEqual(len(policy.parameters_fingerprint), 64)
        stricter = CryptoSpotRiskPolicy(max_order_notional_fraction=Decimal("0.01"))
        self.assertNotEqual(stricter.parameters_fingerprint, policy.parameters_fingerprint)
        self.assertEqual(CryptoSpotRiskPolicy().parameters_fingerprint, policy.parameters_fingerprint)

    def test_frozen_and_versioned(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            POLICY.version = "x"
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(version="")

    def test_cannot_loosen_provisional_ceilings(self):
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(max_order_notional_fraction=Decimal("0.05"))
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(max_daily_notional_fraction=Decimal("0.5"))
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(max_order_notional_fraction=0.01)
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(max_quote_age_seconds=60)
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(max_order_notional_fraction=Decimal("0.02"), max_daily_notional_fraction=Decimal("0.01"))

    def test_stricter_policy_is_accepted_and_recorded(self):
        strict = CryptoSpotRiskPolicy(version="crypto-spot-1-strict", max_order_notional_fraction=Decimal("0.005"))
        decision = strict.evaluate(buy(), context())
        self.assertIn("order_notional_exceeds_cap", decision.reasons)
        self.assertEqual(decision.policy_version, "crypto-spot-1-strict")


class SpotVenueIdentityTests(unittest.TestCase):
    """Pins the reason codes of the MV-A-15/MV-A-16 venue checks (MV-D-17) and the ASCII asset rule (A-17)."""

    def test_non_canonical_venue_id_is_refused_with_its_reason(self):
        for venue_id in ("binance_spot ", "BINANCE_SPOT", "binance-spot", "1binance", "binance_spot\n", "binance_\u017fpot"):
            with self.subTest(venue_id=venue_id):
                decision = CryptoSpotRiskPolicy().evaluate(buy(), context(venue_id=venue_id))
                self.assertFalse(decision.allowed)
                self.assertIn("venue_id_not_canonical", decision.reasons)

    def test_canonical_venue_outside_the_registry_or_without_crypto_spot_is_refused(self):
        for venue_id in ("binance_spot_a", "x", "ctrader"):
            with self.subTest(venue_id=venue_id):
                decision = CryptoSpotRiskPolicy().evaluate(buy(), context(venue_id=venue_id))
                self.assertFalse(decision.allowed)
                self.assertIn("venue_not_in_registry", decision.reasons)
                self.assertNotIn("venue_id_not_canonical", decision.reasons)

    def test_registered_spot_venue_is_exactly_binance_spot_and_still_allowed(self):
        self.assertEqual(policies_module._registered_spot_venues(), frozenset({"binance_spot"}))
        self.assertTrue(CryptoSpotRiskPolicy().evaluate(buy(), context()).allowed)

    def test_unreadable_registry_refuses_every_venue(self):
        from unittest import mock

        from astra.venues.registry import RegistryError

        policies_module._registered_spot_venues.cache_clear()
        self.addCleanup(policies_module._registered_spot_venues.cache_clear)
        for error in (RegistryError("broken"), OSError("missing"), ValueError("bad json")):
            with self.subTest(error=type(error).__name__):
                policies_module._registered_spot_venues.cache_clear()
                with mock.patch.object(policies_module, "load_registry", side_effect=error):
                    decision = CryptoSpotRiskPolicy().evaluate(buy(), context())
                self.assertFalse(decision.allowed)
                self.assertIn("venue_not_in_registry", decision.reasons)

    def test_two_registered_venues_keep_separate_daily_caps(self):
        from unittest import mock

        with mock.patch.object(policies_module, "_registered_spot_venues",
                               return_value=frozenset({"binance_spot", "second_spot"})):
            policy = CryptoSpotRiskPolicy()
            for i in range(5):
                self.assertTrue(policy.evaluate(buy(intent_id=f"a{i}", quantity=Decimal("0.038")), context()).allowed)
            full = policy.evaluate(buy(intent_id="a5", quantity=Decimal("0.038")), context())
            self.assertIn("daily_notional_exceeds_cap", full.reasons)
            other = policy.evaluate(buy(intent_id="b0", quantity=Decimal("0.038")), context(venue_id="second_spot"))
            self.assertTrue(other.allowed, other.reasons)

    def test_unicode_look_alike_assets_are_refused(self):
        for asset in ("USDT\u200b", "\uff35\uff33\uff24\uff34", "U\u0405DT"):
            with self.subTest(asset=asset.encode("unicode_escape")):
                with self.assertRaises(ValueError):
                    spot_spec(quote_asset=asset, symbol="BTC" + asset)
                with self.assertRaises(ValueError):
                    Balance(asset, Decimal(1), Decimal(0), NOW)


class ForexPreCheckTests(unittest.TestCase):
    precheck = ForexCfdPreCheck()

    def provider_spec(self, **overrides):
        instrument = replace(DEFAULT_SPECS["EURUSD"], source="PROVIDER_API:TEST")
        fields = {
            "instrument": instrument,
            "contract_size": 100_000,
            "margin_currency": "EUR",
            "swap_long": "-1.2",
            "swap_short": "0.3",
            "swap_unit": "POINTS",
            "commission_per_lot": "3.5",
            "commission_currency": "USD",
            "source": "PROVIDER_API:TEST",
            "as_of": NOW,
        }
        fields.update(overrides)
        return ForexCfdSpec(**fields)

    def test_illustrative_spec_passes_in_simulation_only(self):
        spec = ForexCfdSpec(DEFAULT_SPECS["EURUSD"], 100_000, "USD")
        decision = self.precheck.evaluate(spec, mode=RunMode.SIMULATION, account_currency=None)
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.policy_version, "forex-cfd-precheck-1")
        self.assertIn("RiskEngine remains the authority", decision.notes[0])
        demo = self.precheck.evaluate(spec, mode=RunMode.DEMO, account_currency="USD")
        self.assertIn("forex_spec_illustrative_outside_simulation", demo.reasons)
        self.assertIn("swap_unknown", demo.reasons)
        self.assertIn("commission_unknown", demo.reasons)

    def test_live_is_refused(self):
        decision = self.precheck.evaluate(self.provider_spec(), mode=RunMode.LIVE, account_currency="USD",
                                          conversion_rates={"EUR": Decimal("1.08")})
        self.assertEqual(decision.reasons, ("live_mode_not_supported",))

    def test_conversion_rates_required_outside_simulation(self):
        spec = self.provider_spec()
        missing = self.precheck.evaluate(spec, mode=RunMode.DEMO, account_currency="USD")
        self.assertEqual(missing.reasons, ("conversion_rate_unknown:EUR",))
        ok = self.precheck.evaluate(spec, mode=RunMode.DEMO, account_currency="USD", conversion_rates={"EUR": Decimal("1.08")})
        self.assertTrue(ok.allowed, ok.reasons)
        gbp = self.precheck.evaluate(spec, mode=RunMode.DEMO, account_currency="GBP", conversion_rates={"EUR": Decimal("0.87")})
        self.assertIn("conversion_rate_unknown:USD", gbp.reasons)
        no_ccy = self.precheck.evaluate(spec, mode=RunMode.DEMO, account_currency=None)
        self.assertIn("account_currency_unknown", no_ccy.reasons)
        swap_ccy = self.precheck.evaluate(self.provider_spec(swap_unit="JPY"), mode=RunMode.DEMO, account_currency="USD",
                                          conversion_rates={"EUR": Decimal("1.08")})
        self.assertIn("conversion_rate_unknown:JPY", swap_ccy.reasons)

    def test_lot_conversion_ambiguity(self):
        odd_step = InstrumentSpec("EURUSD", "EUR", "USD", 0.0001, 5, 1_000.0, 3_000.0, 10_000_000.0, 1 / 30)
        spec = ForexCfdSpec(odd_step, 100_000, "USD")
        self.assertIn("lot_conversion_ambiguous", self.precheck.evaluate(spec, mode=RunMode.SIMULATION, account_currency="USD").reasons)
        default = ForexCfdSpec(DEFAULT_SPECS["EURUSD"], 100_000, "USD")
        self.assertTrue(self.precheck.evaluate(default, mode=RunMode.SIMULATION, account_currency="USD", requested_lots="0.01").allowed)
        self.assertIn("lot_conversion_ambiguous",
                      self.precheck.evaluate(default, mode=RunMode.SIMULATION, account_currency="USD", requested_lots="0.015").reasons)
        self.assertIn("requested_lots_outside_contract_limits",
                      self.precheck.evaluate(default, mode=RunMode.SIMULATION, account_currency="USD", requested_lots="101").reasons)
        self.assertIn("requested_lots_invalid",
                      self.precheck.evaluate(default, mode=RunMode.SIMULATION, account_currency="USD", requested_lots=0.01).reasons)

    def test_missing_spec(self):
        decision = self.precheck.evaluate(None, mode=RunMode.SIMULATION, account_currency="USD")
        self.assertEqual(decision.reasons, ("forex_spec_missing",))


class DerivativesTests(unittest.TestCase):
    def test_flags_stay_disabled(self):
        self.assertIs(crypto_futures.ENABLED, False)
        self.assertIs(fixed_expiry_options.ENABLED, False)
        self.assertIs(FUNDING_COST_ENABLED, False)

    def test_always_refused_in_every_mode(self):
        for policy, market in (
            (CryptoFuturesRiskPolicy(), MarketType.CRYPTO_FUTURES),
            (FixedExpiryOptionsRiskPolicy(), MarketType.FIXED_EXPIRY_OPTIONS),
        ):
            for mode in (*RunMode, None):
                decision = policy.evaluate(object(), mode=mode, anything=1)
                self.assertEqual(decision.verdict, Verdict.REFUSE)
                self.assertEqual(decision.reasons, (DERIVATIVES_NOT_ENABLED_REASON,))
                self.assertEqual(decision.market_type, market)
                self.assertTrue(decision.policy_version)
            self.assertGreaterEqual(len(policy.review_requirements), 5)

    def test_review_requirements_cover_the_listed_topics(self):
        futures = " ".join(CryptoFuturesRiskPolicy().review_requirements)
        for topic in ("initial and maintenance margin", "mark price", "funding", "liquidation", "margin mode", "cross-contract"):
            self.assertIn(topic, futures)
        options = " ".join(FixedExpiryOptionsRiskPolicy().review_requirements)
        for topic in ("expiry", "payout", "premium", "schedule", "restrictions"):
            self.assertIn(topic, options)

    def test_enabled_flag_without_review_is_still_refused(self):
        original = crypto_futures.ENABLED
        crypto_futures.ENABLED = True
        try:
            decision = CryptoFuturesRiskPolicy().evaluate(mode=RunMode.SIMULATION)
        finally:
            crypto_futures.ENABLED = original
        self.assertFalse(decision.allowed)
        self.assertIn("enabled_flag_set_without_reviewed_policy", decision.reasons)

    def test_funding_cost_is_data_only(self):
        funding = FundingCostAssumption("BTCUSDT", "0.0001", 8, NOW + timedelta(hours=1), "TEST", NOW)
        self.assertEqual(funding.calibration, UNCALIBRATED)
        public = [n for n in dir(FundingCostAssumption) if not n.startswith("_") and callable(getattr(FundingCostAssumption, n))]
        self.assertEqual(public, [])


class DecisionAndRegistryTests(unittest.TestCase):
    def test_one_policy_per_market_type(self):
        kinds = {market: type(policy_for(market)) for market in MarketType}
        self.assertEqual(len(set(kinds.values())), len(MarketType))
        self.assertIs(kinds[MarketType.CRYPTO_SPOT], CryptoSpotRiskPolicy)

    def test_allow_is_impossible_outside_its_modes(self):
        pairs, fp = canonical_parameters({"a": 1})
        for market, mode in ((MarketType.CRYPTO_SPOT, "LIVE"), (MarketType.CRYPTO_SPOT, "DEMO"),
                             (MarketType.CRYPTO_FUTURES, "SIMULATION"), (MarketType.FIXED_EXPIRY_OPTIONS, "SIMULATION"),
                             (MarketType.FOREX_CFD, "LIVE")):
            with self.subTest(market=market, mode=mode), self.assertRaises(ValueError):
                MarketRiskDecision(market, Verdict.ALLOW, (), "p", "1", mode, parameters=pairs, parameters_fingerprint=fp)
        # Forex pre-check ALLOW stays advisory in DEMO: the RiskEngine still refuses DEMO.
        MarketRiskDecision(MarketType.FOREX_CFD, Verdict.ALLOW, (), "p", "1", "DEMO", parameters=pairs, parameters_fingerprint=fp)
        MarketRiskDecision(MarketType.CRYPTO_SPOT, Verdict.ALLOW, (), "p", "1", "SIMULATION", parameters=pairs, parameters_fingerprint=fp)

    def test_allow_needs_matching_parameters(self):
        pairs, _fp = canonical_parameters({"a": 1})
        with self.assertRaises(ValueError):
            MarketRiskDecision(MarketType.CRYPTO_SPOT, Verdict.ALLOW, (), "p", "1", "SIMULATION")
        with self.assertRaises(ValueError):
            MarketRiskDecision(MarketType.CRYPTO_SPOT, Verdict.ALLOW, (), "p", "1", "SIMULATION", parameters=pairs, parameters_fingerprint="0" * 64)

    def test_decision_invariants(self):
        with self.assertRaises(ValueError):
            MarketRiskDecision(MarketType.CRYPTO_SPOT, Verdict.ALLOW, ("x",), "p", "1", "SIMULATION")
        with self.assertRaises(ValueError):
            MarketRiskDecision(MarketType.CRYPTO_SPOT, Verdict.REFUSE, (), "p", "1", "SIMULATION")
        with self.assertRaises(ValueError):
            MarketRiskDecision(MarketType.CRYPTO_SPOT, Verdict.REFUSE, ("x",), "p", "", "SIMULATION")

    def test_every_decision_records_a_version(self):
        decisions = [
            CryptoSpotRiskPolicy().evaluate(buy(), context()),
            CryptoSpotRiskPolicy().evaluate(buy(), context(mode=RunMode.LIVE)),
            CryptoSpotRiskPolicy().evaluate(object(), object()),
            ForexCfdPreCheck().evaluate(None, mode=RunMode.DEMO, account_currency="USD"),
            CryptoFuturesRiskPolicy().evaluate(),
            FixedExpiryOptionsRiskPolicy().evaluate(),
        ]
        for decision in decisions:
            self.assertTrue(decision.policy_name)
            self.assertTrue(decision.policy_version)
            self.assertEqual(len(decision.parameters_fingerprint), 64)


class CostModelTests(unittest.TestCase):
    def test_book_ticker_payload_and_spread(self):
        ticker = BookTicker.from_payload(
            {"symbol": "BTCUSDT", "bidPrice": "50000.00", "bidQty": "1.5", "askPrice": "50010.00", "askQty": "2"},
            as_of=NOW, source="TEST",
        )
        self.assertEqual(ticker.spread, Decimal("10.00"))
        self.assertEqual(ticker.mid, Decimal("50005.00"))
        self.assertLess(abs(ticker.spread_fraction - Decimal(10) / Decimal("50005")), Decimal("1e-25"))
        with self.assertRaises(ValueError):
            BookTicker.from_payload({"symbol": "BTCUSDT"}, as_of=NOW, source="TEST")
        with self.assertRaises(ValueError):
            book(bid_price=Decimal("50002"))
        with self.assertRaises(ValueError):
            book(ask_price=Decimal("50000.00"))  # locked
        for side in ("bid_qty", "ask_qty"):
            with self.assertRaises(ValueError):
                book(**{side: Decimal(0)})
        with self.assertRaises(TypeError):
            book(bid_price=50000.0)

    def test_fee_asset_handling(self):
        model = CryptoSpotCostModel(fees())
        spec = spot_spec()
        buy_fee = model.fee(spec, SpotSide.BUY, Decimal("0.02"), Decimal("50000"))
        self.assertEqual((buy_fee.asset, buy_fee.amount, buy_fee.quote_value), ("BTC", Decimal("0.00002"), Decimal("1.000")))
        sell_fee = model.fee(spec, SpotSide.SELL, Decimal("0.02"), Decimal("50000"))
        self.assertEqual((sell_fee.asset, sell_fee.amount), ("USDT", Decimal("1.000")))
        bnb = CryptoSpotCostModel(fees(fee_asset="BNB"))
        with self.assertRaises(FeeUnknown):
            bnb.fee(spec, SpotSide.BUY, Decimal("0.02"), Decimal("50000"))
        self.assertEqual(bnb.fee(spec, SpotSide.BUY, Decimal("0.02"), Decimal("50000"), fee_asset_price_in_quote="500").amount,
                         Decimal("0.002"))

    def test_worst_rate_and_rebate(self):
        schedule = fees(maker_rate=Decimal("-0.0001"), taker_rate=Decimal("0.0004"))
        self.assertEqual(schedule.worst_rate, Decimal("0.0004"))
        with self.assertRaises(ValueError):
            fees(taker_rate=Decimal("-0.0001"))

    def test_slippage_is_explicit_and_uncalibrated(self):
        slip = SlippageAssumption()
        self.assertEqual(slip.calibration, "UNCALIBRATED")
        self.assertEqual(POLICY.slippage.calibration, "UNCALIBRATED")
        model = CryptoSpotCostModel(fees(), slip)
        ticker = book(bid_price=Decimal("100"), ask_price=Decimal("100.1"))
        expected = Decimal("0.002") + Decimal("0.1") / Decimal("100.05") + Decimal("0.001")
        self.assertLess(abs(model.round_trip_cost_fraction(ticker) - expected), Decimal("1e-25"))
        with self.assertRaises(ValueError):
            SlippageAssumption(Decimal("-0.1"))

    def test_fee_schedule_from_spec(self):
        schedule = CryptoSpotFeeSchedule.from_spec(spot_spec())
        self.assertEqual(schedule.taker_rate, Decimal("0.001"))
        self.assertIsNone(schedule.fee_asset)

    def test_forex_reuses_existing_cost_model(self):
        plain = ForexCfdSpec(DEFAULT_SPECS["EURUSD"], 100_000, "USD")
        model = forex_cost_model(plain)
        self.assertIsInstance(model, CostModel)
        self.assertEqual(model, CostModel())
        with_commission = ForexCfdSpec(DEFAULT_SPECS["EURUSD"], 100_000, "USD", commission_per_lot="7", commission_currency="USD")
        with self.assertRaises(ValueError):
            forex_cost_model(with_commission)
        round_turn = forex_cost_model(with_commission, commission_basis=CommissionBasis.ROUND_TURN)
        self.assertAlmostEqual(round_turn.commission_per_unit_per_side, 0.000035)
        per_side = forex_cost_model(with_commission, commission_basis=CommissionBasis.PER_SIDE)
        self.assertAlmostEqual(per_side.commission_per_unit_per_side, 0.00007)
        eur_commission = ForexCfdSpec(DEFAULT_SPECS["EURUSD"], 100_000, "USD", commission_per_lot="7", commission_currency="EUR")
        with self.assertRaises(ValueError):
            forex_cost_model(eur_commission, commission_basis=CommissionBasis.PER_SIDE)


class NoOrderPathTests(unittest.TestCase):
    FORBIDDEN_MODULES = (
        "astra.execution",
        "astra.permits",
        "astra.service",
        "astra.engine",
        "astra.web",
        "astra.cli",
        "astra.brokers.ctrader",
        "astra.brokers.simulated",
        "astra.brokers.websocket",
    )

    def test_only_the_abstract_broker_contract_is_loaded(self):
        # MV-D-13: the shared value types live in astra.connector_types, so no astra.brokers module is loaded.
        code = ("import sys, astra.markets.risk_policies, astra.markets.costs\n"
                "print(','.join(sorted(m for m in sys.modules if m.startswith('astra.brokers'))))\n")
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True,
            env={"PYTHONPATH": str(SRC), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
        )
        self.assertEqual(result.stdout.strip(), "")

    def test_importing_the_modules_loads_no_order_sending_code(self):
        code = (
            "import sys\n"
            "import astra.markets.risk_policies, astra.markets.costs\n"
            f"bad = [m for m in sys.modules if m.startswith({self.FORBIDDEN_MODULES!r})]\n"
            "print(','.join(bad))\n"
        )
        env_path = str(SRC)
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True,
            env={"PYTHONPATH": env_path, "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
        )
        self.assertEqual(result.stdout.strip(), "", result.stdout)

    def test_sources_reference_no_order_api(self):
        for module in (policies_module, costs_module):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            imported = {
                alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
            }
            modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
            for forbidden in ("submit_order", "_send_order", "cancel_order", "BrokerAdapter", "OrderGateway",
                              "ExecutionPermit", "BrokerOrderRequest"):
                self.assertNotIn(forbidden, names | imported, f"{module.__name__} references {forbidden}")
            self.assertFalse(any(m and ("execution" in m or "permits" in m or "brokers" in m) for m in modules), modules)


if __name__ == "__main__":
    unittest.main()
