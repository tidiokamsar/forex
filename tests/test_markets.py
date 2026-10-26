import typing
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from astra import markets
from astra.instruments import DEFAULT_SPECS
from astra.markets import (
    ContractType,
    CryptoFuturesSpec,
    CryptoSpotSpec,
    FixedExpiryOptionSpec,
    ForexCfdSpec,
    FuturesMarkState,
    MarketSpec,
    OptionOutcome,
    TieRule,
    crypto_futures,
    fixed_expiry_options,
    floor_to_step,
)

AS_OF = datetime(2026, 10, 26, tzinfo=UTC)


def spot(**overrides):
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
        "as_of": AS_OF,
    }
    fields.update(overrides)
    return CryptoSpotSpec(**fields)


class ForexCfdTests(unittest.TestCase):
    def test_wraps_instrument_spec_and_is_illustrative_by_default(self):
        spec = ForexCfdSpec(DEFAULT_SPECS["EURUSD"], contract_size=100_000, margin_currency="USD")
        self.assertIs(spec.instrument, DEFAULT_SPECS["EURUSD"])
        self.assertEqual(spec.symbol, "EURUSD")
        self.assertEqual(spec.contract_size, Decimal(100_000))
        self.assertTrue(spec.illustrative)

    def test_provider_terms_on_illustrative_instrument_stay_illustrative(self):
        spec = ForexCfdSpec(DEFAULT_SPECS["EURUSD"], 100_000, "USD", source="PROVIDER_API:TEST", as_of=AS_OF)
        self.assertTrue(spec.illustrative)

    def test_validation(self):
        eurusd = DEFAULT_SPECS["EURUSD"]
        with self.assertRaises(ValueError):
            ForexCfdSpec(eurusd, 0, "USD")
        with self.assertRaises(ValueError):
            ForexCfdSpec(eurusd, 100_000, "usd")
        with self.assertRaises(ValueError):
            ForexCfdSpec(eurusd, 100_000, "USD", swap_long="-1.2")
        with self.assertRaises(ValueError):
            ForexCfdSpec(eurusd, 100_000, "USD", commission_per_lot="3")
        with self.assertRaises(ValueError):
            ForexCfdSpec(eurusd, 100_000, "USD", source="PROVIDER_API:TEST")
        with self.assertRaises(TypeError):
            ForexCfdSpec("EURUSD", 100_000, "USD")
        spec = ForexCfdSpec(eurusd, 100_000, "USD", swap_long="-1.2", swap_short="0.3", swap_unit="POINTS")
        self.assertEqual(spec.swap_long, Decimal("-1.2"))


class CryptoSpotTests(unittest.TestCase):
    def test_round_down_uses_exact_decimal_steps(self):
        spec = spot()
        self.assertEqual(spec.round_quantity_down("0.123456789"), Decimal("0.12345"))
        self.assertEqual(spec.round_price_down("67123.459"), Decimal("67123.45"))
        self.assertEqual(spec.round_price_down("67123.45"), Decimal("67123.45"))
        self.assertEqual(spec.round_quantity_down(0), Decimal("0.00000"))
        # Binary float would give 0.30000000000000004; Decimal steps stay exact.
        self.assertEqual(floor_to_step("0.3", Decimal("0.1")), Decimal("0.3"))
        self.assertEqual(floor_to_step("0.29999999", Decimal("0.1")), Decimal("0.2"))
        self.assertEqual(floor_to_step(25, Decimal("10")), Decimal("20"))

    def test_rounding_never_rounds_up(self):
        spec = spot()
        for raw in ("0.000019999", "1.999999", "0.00001", "123.4567891"):
            rounded = spec.round_quantity_down(raw)
            self.assertLessEqual(rounded, Decimal(raw))
            self.assertLess(Decimal(raw) - rounded, spec.step_size)

    def test_floats_and_negative_values_are_refused(self):
        spec = spot()
        with self.assertRaises(TypeError):
            spec.round_quantity_down(0.1)
        with self.assertRaises(ValueError):
            spec.round_quantity_down("-1")
        with self.assertRaises(ValueError):
            spec.round_price_down("NaN")
        with self.assertRaises(TypeError):
            spot(tick_size=0.01)

    def test_notional_checks(self):
        spec = spot()
        self.assertEqual(spec.notional("0.0001", "50000"), Decimal("5.0000"))
        self.assertTrue(spec.notional_ok("0.0001", "50000"))
        self.assertFalse(spec.notional_ok("0.00009", "50000"))
        self.assertEqual(spec.order_violations("0.00009", "50000"), ("notional_below_minimum",))
        self.assertEqual(spec.order_violations("0.0001", "50000"), ())

    def test_order_violations(self):
        spec = spot()
        self.assertEqual(spec.order_violations("0.000105", "50000.001"), ("quantity_not_on_step", "price_not_on_tick"))
        self.assertIn("quantity_above_maximum", spec.order_violations("9001", "50000"))
        self.assertIn("quantity_below_minimum", spot(min_qty="0.001").order_violations("0.0002", "50000"))
        self.assertEqual(spec.order_violations("0", "50000"), ("invalid_quantity",))
        self.assertEqual(spec.order_violations(0.1, "50000"), ("invalid_quantity",))
        self.assertEqual(spec.order_violations("1", "-1"), ("invalid_price",))
        self.assertIn("symbol_not_trading", spot(status="BREAK").order_violations("0.0001", "50000"))

    def test_spec_validation(self):
        with self.assertRaises(ValueError):
            spot(step_size="0")
        with self.assertRaises(ValueError):
            spot(min_qty="10", max_qty="1")
        with self.assertRaises(ValueError):
            spot(taker_fee="1")
        with self.assertRaises(ValueError):
            spot(price_precision=-1)
        with self.assertRaises(ValueError):
            spot(symbol="btcusdt")
        with self.assertRaises(ValueError):
            spot(as_of=datetime(2026, 10, 26))  # noqa: DTZ001 - naive on purpose
        self.assertEqual(spot(maker_fee="-0.0001").maker_fee, Decimal("-0.0001"))


class DisabledMarketTests(unittest.TestCase):
    def test_futures_and_options_are_disabled(self):
        self.assertIs(crypto_futures.ENABLED, False)
        self.assertIs(fixed_expiry_options.ENABLED, False)
        for module in (crypto_futures, fixed_expiry_options):
            self.assertIn("no backtest, risk or execution path", module.__doc__)

    def test_disabled_markets_are_not_imported_by_order_or_risk_paths(self):
        import astra.engine
        import astra.execution.gateway
        import astra.risk

        for module in (astra.engine, astra.execution.gateway, astra.risk):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("astra.markets", source)
            self.assertNotIn("from ..markets", source)
            self.assertNotIn("from .markets", source)

    def test_futures_model(self):
        spec = CryptoFuturesSpec(
            "BTCUSDT", "BTC", "USDT", "USDT", ContractType.PERPETUAL, "1", "0.1", "0.001", "0.001", "1000", "0.05", "0.004", "ILLUSTRATIVE", AS_OF
        )
        self.assertEqual(spec.maintenance_margin_rate, Decimal("0.004"))
        with self.assertRaises(ValueError):
            CryptoFuturesSpec("BTCUSDT", "BTC", "USDT", "USDT", ContractType.PERPETUAL, "1", "0.1", "0.001", "0.001", "1000", "0.004", "0.05", "ILLUSTRATIVE", AS_OF)
        with self.assertRaises(ValueError):
            CryptoFuturesSpec("BTCUSDT", "BTC", "USDT", "USDT", ContractType.DELIVERY, "1", "0.1", "0.001", "0.001", "1000", "0.05", "0.004", "ILLUSTRATIVE", AS_OF)
        mark = FuturesMarkState("BTCUSDT", "50000", None, "0.0001", None, "ILLUSTRATIVE", AS_OF)
        self.assertIsNone(mark.liquidation_price)

    def test_options_model(self):
        spec = FixedExpiryOptionSpec("EURUSD-60S", "EURUSD", "USD", (60, 300), "0.8", "1", "1", "1000", TieRule.REFUND_PREMIUM, "ILLUSTRATIVE", AS_OF)
        self.assertEqual(spec.settlement_amount("10", OptionOutcome.WIN), Decimal("8.0"))
        self.assertEqual(spec.settlement_amount("10", OptionOutcome.LOSS), Decimal("-10"))
        self.assertEqual(spec.settlement_amount("10", OptionOutcome.TIE), Decimal(0))
        with self.assertRaises(ValueError):
            spec.settlement_amount("0.5", OptionOutcome.WIN)
        with self.assertRaises(ValueError):
            FixedExpiryOptionSpec("EURUSD-60S", "EURUSD", "USD", (), "0.8", "1", "1", "1000", TieRule.LOSS, "ILLUSTRATIVE", AS_OF)
        unknown = FixedExpiryOptionSpec("EURUSD-60S", "EURUSD", "USD", (60,), "0.8", "1", "1", "1000", TieRule.UNKNOWN, "ILLUSTRATIVE", AS_OF)
        with self.assertRaises(ValueError):
            unknown.settlement_amount("10", OptionOutcome.TIE)


class MarketSpecUnionTests(unittest.TestCase):
    def test_market_spec_is_the_union_of_spec_classes(self):
        self.assertEqual(set(typing.get_args(MarketSpec)), {ForexCfdSpec, CryptoSpotSpec, CryptoFuturesSpec, FixedExpiryOptionSpec})
        self.assertIsInstance(spot(), MarketSpec)
        self.assertIn("MarketSpec", markets.__all__)


if __name__ == "__main__":
    unittest.main()
