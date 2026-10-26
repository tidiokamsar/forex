import inspect
import unittest
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from astra.brokers.base import BrokerAdapter, CapabilityNotSupported, EnvironmentSource, Quote
from astra.venues import contract
from astra.venues.contract import (
    AccountReadConnector,
    Balance,
    Capability,
    MarketDataConnector,
    MarketType,
    RateLimit,
    StaleDataError,
    VenueCapabilities,
    VenueConnector,
    VenueEnvironment,
    ensure_fresh,
    validate_environment_claim,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
READ_SET = frozenset({Capability.SERVER_TIME, Capability.QUOTES, Capability.BALANCES})


def capabilities(supported=READ_SET, unsupported=None, **overrides):
    if unsupported is None:
        unsupported = {c: f"{c.value} not offered by the offline fake" for c in Capability if c not in supported}
    fields = {
        "venue_id": "fake_venue",
        "connector_name": "astra-fake-venue",
        "connector_version": "0.0.1",
        "market_types": (MarketType.CRYPTO_SPOT,),
        "environments": (VenueEnvironment.TESTNET,),
        "supported": supported,
        "unsupported": unsupported,
        "rate_limits": (RateLimit("requests", 10, 1.0, False, "OFFLINE_FIXTURE"),),
        "official_api_url": "https://example.invalid/docs",
        "verified_on": None,
        "limitations": ("offline fake, no provider",),
    }
    fields.update(overrides)
    return VenueCapabilities(**fields)


class FakeConnector(MarketDataConnector, AccountReadConnector):
    """Offline fake: no network, fixed values."""

    def __init__(self, caps=None, environment=(VenueEnvironment.TESTNET, EnvironmentSource.PROVIDER_API)):
        self._caps = caps or capabilities()
        self._environment = environment

    def capabilities(self):
        return self._caps

    def server_time(self):
        self.require(Capability.SERVER_TIME)
        return NOW

    def instrument_specs(self, symbols=None):
        self.require(Capability.INSTRUMENT_SPECS)
        return {}

    def quote(self, symbol):
        self.require(Capability.QUOTES)
        return Quote(symbol, 1.0, 1.1, NOW, "OFFLINE_FIXTURE")

    def bars(self, symbol, interval, *, start=None, end=None, limit=500):
        self.require(Capability.HISTORICAL_BARS)
        return ()

    def balances(self):
        self.require(Capability.BALANCES)
        return (Balance("USDT", Decimal("10"), Decimal("0"), NOW),)

    def account_environment(self):
        return validate_environment_claim(*self._environment)


class CapabilityTests(unittest.TestCase):
    def test_public_names_exist(self):
        for name in (
            "MarketType", "VenueEnvironment", "Capability", "RateLimit", "VenueCapabilities", "VenueConnector",
            "MarketDataConnector", "Balance", "AccountReadConnector", "ensure_fresh", "StaleDataError",
        ):
            self.assertTrue(hasattr(contract, name), name)
        self.assertEqual([m.value for m in MarketType], ["FOREX_CFD", "CRYPTO_SPOT", "CRYPTO_FUTURES", "FIXED_EXPIRY_OPTIONS"])
        self.assertEqual([e.value for e in VenueEnvironment], ["PUBLIC_DATA", "READ_ONLY", "DEMO", "PAPER", "TESTNET", "LIVE"])
        self.assertEqual(len(Capability), 14)
        self.assertTrue(issubclass(StaleDataError, ValueError))

    def test_every_unsupported_capability_needs_a_reason(self):
        reasons = {c: "not offered" for c in Capability if c not in READ_SET}
        reasons.pop(Capability.ORDER_HISTORY)
        with self.assertRaisesRegex(ValueError, "ORDER_HISTORY"):
            capabilities(unsupported=reasons)

    def test_blank_reason_is_refused(self):
        reasons = {c: "not offered" for c in Capability if c not in READ_SET}
        reasons[Capability.POSITIONS] = "   "
        with self.assertRaisesRegex(ValueError, "explicit reason"):
            capabilities(unsupported=reasons)

    def test_supported_and_unsupported_cannot_overlap(self):
        reasons = {c: "not offered" for c in Capability}
        with self.assertRaisesRegex(ValueError, "both supported and unsupported"):
            capabilities(unsupported=reasons)

    def test_order_capabilities_cannot_be_supported(self):
        for capability in (Capability.ORDER_PLACEMENT, Capability.ORDER_CANCEL, Capability.ORDER_MODIFY):
            with self.assertRaisesRegex(ValueError, "order capabilities"):
                capabilities(supported=READ_SET | {capability})

    def test_unsupported_mapping_is_read_only_copy(self):
        reasons = {c: "not offered" for c in Capability if c not in READ_SET}
        caps = capabilities(unsupported=reasons)
        reasons[Capability.POSITIONS] = "changed"
        self.assertEqual(caps.unsupported[Capability.POSITIONS], "not offered")
        with self.assertRaises(TypeError):
            caps.unsupported[Capability.POSITIONS] = "x"

    def test_field_validation(self):
        with self.assertRaises(ValueError):
            capabilities(venue_id="Fake")
        with self.assertRaises(ValueError):
            capabilities(official_api_url="http://example.invalid")
        with self.assertRaises(ValueError):
            capabilities(verified_on="26/10/2026")
        with self.assertRaises(ValueError):
            capabilities(market_types=())
        with self.assertRaises(TypeError):
            capabilities(environments=("TESTNET",))
        with self.assertRaises(ValueError):
            RateLimit("requests", 0, 1.0, False, "doc")
        with self.assertRaises(ValueError):
            RateLimit("requests", 10, float("inf"), False, "doc")
        self.assertEqual(capabilities(verified_on="2026-10-26").verified_on, "2026-10-26")


class RequireTests(unittest.TestCase):
    def test_require_refuses_with_recorded_reason(self):
        connector = FakeConnector()
        with self.assertRaises(CapabilityNotSupported) as caught:
            connector.bars("BTCUSDT", "1m")
        self.assertIn("HISTORICAL_BARS not offered by the offline fake", str(caught.exception))
        self.assertIs(caught.exception.capability, Capability.HISTORICAL_BARS)
        with self.assertRaises(CapabilityNotSupported):
            connector.require(Capability.ORDER_PLACEMENT)

    def test_supported_capability_passes(self):
        connector = FakeConnector()
        self.assertIsNone(connector.require(Capability.QUOTES))
        self.assertEqual(connector.quote("BTCUSDT").source, "OFFLINE_FIXTURE")
        self.assertEqual(connector.venue_id, "fake_venue")
        self.assertEqual(connector.balances()[0].total, Decimal("10"))

    def test_require_rejects_non_capability(self):
        with self.assertRaises(TypeError):
            FakeConnector().require("QUOTES")

    def test_contract_has_no_order_or_funds_methods(self):
        names = set()
        for cls in (VenueConnector, MarketDataConnector, AccountReadConnector):
            names |= {name.lower() for name in dir(cls)}
        for forbidden in ("order", "submit", "cancel", "withdraw", "transfer", "deposit", "beneficiary", "payout"):
            self.assertFalse(any(forbidden in name for name in names), forbidden)
        self.assertFalse(issubclass(FakeConnector, BrokerAdapter))

    def test_bars_signature(self):
        params = inspect.signature(MarketDataConnector.bars).parameters
        self.assertEqual(params["start"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(params["limit"].default, 500)


class EnvironmentTests(unittest.TestCase):
    def test_provider_and_unverified_claims_are_accepted(self):
        self.assertEqual(FakeConnector().account_environment(), (VenueEnvironment.TESTNET, EnvironmentSource.PROVIDER_API))
        claim = (VenueEnvironment.DEMO, EnvironmentSource.UNVERIFIED)
        self.assertEqual(FakeConnector(environment=claim).account_environment(), claim)

    def test_configuration_is_not_evidence(self):
        for source in (EnvironmentSource.CONFIGURATION_ONLY, EnvironmentSource.SIMULATOR):
            with self.assertRaisesRegex(ValueError, "UNVERIFIED"):
                FakeConnector(environment=(VenueEnvironment.DEMO, source)).account_environment()
        with self.assertRaises(TypeError):
            validate_environment_claim("DEMO", EnvironmentSource.PROVIDER_API)


class FreshnessTests(unittest.TestCase):
    def test_fresh_data_returns_age(self):
        self.assertEqual(ensure_fresh(NOW - timedelta(seconds=5), max_age_seconds=10, now=NOW), 5.0)
        self.assertEqual(ensure_fresh(NOW, max_age_seconds=10, now=NOW), 0.0)

    def test_stale_data_is_refused(self):
        with self.assertRaises(StaleDataError) as caught:
            ensure_fresh(NOW - timedelta(seconds=11), max_age_seconds=10, now=NOW)
        self.assertEqual(caught.exception.reason, "stale")

    def test_future_dated_data_is_refused(self):
        with self.assertRaises(StaleDataError) as caught:
            ensure_fresh(NOW + timedelta(milliseconds=1), max_age_seconds=10, now=NOW)
        self.assertEqual(caught.exception.reason, "future_dated")

    def test_other_timezones_compare_by_instant(self):
        paris = timezone(timedelta(hours=1))
        self.assertEqual(ensure_fresh(datetime(2026, 10, 26, 12, 59, 58, tzinfo=paris), max_age_seconds=10, now=NOW), 2.0)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            ensure_fresh(datetime(2026, 10, 26, 12, 0), max_age_seconds=10, now=NOW)  # noqa: DTZ001 - naive on purpose
        with self.assertRaises(ValueError):
            ensure_fresh(NOW, max_age_seconds=0, now=NOW)
        with self.assertRaises(TypeError):
            ensure_fresh("2026-10-26T12:00:00Z", max_age_seconds=10, now=NOW)
        with self.assertRaises(TypeError):
            ensure_fresh(NOW, max_age_seconds=True, now=NOW)

    def test_default_now_is_utc_clock(self):
        self.assertLess(ensure_fresh(datetime.now(UTC), max_age_seconds=60), 60)


class BalanceTests(unittest.TestCase):
    def test_balance_validation(self):
        self.assertEqual(Balance("BTC", "0.5", 0, NOW).free, Decimal("0.5"))
        with self.assertRaises(TypeError):
            Balance("BTC", 0.5, Decimal(0), NOW)
        with self.assertRaises(ValueError):
            Balance("BTC", Decimal("-1"), Decimal(0), NOW)
        with self.assertRaises(ValueError):
            Balance("btc", Decimal(1), Decimal(0), NOW)
        with self.assertRaises(ValueError):
            Balance("BTC", Decimal(1), Decimal(0), datetime(2026, 10, 26))  # noqa: DTZ001 - naive on purpose


if __name__ == "__main__":
    unittest.main()
