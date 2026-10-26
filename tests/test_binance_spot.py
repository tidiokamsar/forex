"""Binance Spot connector tests. ALL OFFLINE against tests/binance_fixture.py (127.0.0.1).

Nothing here touches a Binance host. Passing these tests does not make the
connector READ_ONLY_CONNECTED; see the module docstring of
astra.venues.binance_spot.
"""

import ast
import copy
import dataclasses
import io
import json
import logging
import pickle
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from _support import workspace_tempdir
from astra.brokers.base import (
    BrokerRequestError,
    BrokerTimeout,
    BrokerUnavailable,
    CapabilityNotSupported,
    EnvironmentSource,
    Quote,
)
from astra.governance import Actor, PermissionGuard, Role, SecretValue, SecretVault
from astra.logs import configure_logging, reset_logging
from astra.markets.crypto_spot import CryptoSpotSpec
from astra.models import MarketBar
from astra.venues import binance_spot as bs
from astra.venues import registry as venue_registry
from astra.venues.contract import (
    AccountReadConnector,
    Balance,
    Capability,
    MarketDataConnector,
    StaleDataError,
    VenueEnvironment,
)
from binance_fixture import READ_ONLY_RESTRICTIONS, FakeBinanceServer, Scripted, kline_row

KEY = "test-key-AAAAAAAAAAAAAAAA"
SECRET = "test-secret-BBBBBBBBBBBBBBBB"


class FakeClock:
    def __init__(self, start=None, step=0.0):
        self.t = start or datetime.now(UTC).replace(microsecond=0)
        self.step = timedelta(seconds=step)

    def __call__(self):
        value = self.t
        self.t += self.step
        return value

    def peek_ms(self):
        return int(self.t.timestamp() * 1000)

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class FakeTime:
    """Monotonic clock and sleep that advance together without real waiting."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ServerCase(unittest.TestCase):
    def setUp(self):
        # Stop state is process-wide per base URL; a reused ephemeral port must not inherit it.
        bs.reset_host_states_for_tests()
        self.addCleanup(bs.reset_host_states_for_tests)
        self.clock = FakeClock()
        self.server = FakeBinanceServer(api_key=KEY, api_secret=SECRET, clock_ms=self.clock.peek_ms).start()
        self.addCleanup(self.server.stop)
        self.fake_time = FakeTime()

    def public(self, **kwargs):
        options = {
            "test_only_skip_registry_gate": True,
            "loopback_role": "public_data",
            "clock": self.clock,
            "monotonic": self.fake_time.monotonic,
            "sleep": self.fake_time.sleep,
            "timeout": 2.0,
        }
        options.update(kwargs)
        return bs.BinanceSpotPublicData(self.server.base_url, **options)

    def reader(self, key=KEY, secret=SECRET, **kwargs):
        options = {
            "test_only_skip_registry_gate": True,
            "loopback_role": "production",
            "clock": self.clock,
            "monotonic": self.fake_time.monotonic,
            "sleep": self.fake_time.sleep,
            "timeout": 2.0,
        }
        options.update(kwargs)
        return bs.BinanceSpotAccountReader(SecretValue(key), SecretValue(secret), self.server.base_url, **options)


class ContractConformanceTests(ServerCase):
    def test_public_connector_declares_exactly_what_it_supports(self):
        connector = self.public()
        self.assertIsInstance(connector, MarketDataConnector)
        caps = connector.capabilities()
        self.assertEqual(caps.venue_id, "binance_spot")
        self.assertEqual(connector.venue_id, "binance_spot")
        self.assertEqual(
            caps.supported,
            {Capability.SERVER_TIME, Capability.PUBLIC_MARKET_DATA, Capability.QUOTES,
             Capability.HISTORICAL_BARS, Capability.INSTRUMENT_SPECS},
        )
        self.assertEqual(set(caps.supported) | set(caps.unsupported), set(Capability))
        for capability in (Capability.ORDER_PLACEMENT, Capability.ORDER_CANCEL, Capability.ORDER_MODIFY, Capability.BALANCES):
            with self.assertRaises(CapabilityNotSupported) as ctx:
                connector.require(capability)
            self.assertTrue(ctx.exception.reason)
        weight = caps.rate_limits[0]
        self.assertEqual((weight.name, weight.limit, weight.interval_seconds, weight.weight_based),
                         ("REQUEST_WEIGHT", 6000, 60.0, True))
        self.assertTrue(any("OFFLINE" in line for line in caps.limitations))
        self.assertIsNone(caps.verified_on)

    def test_account_reader_declares_account_reads_only(self):
        reader = self.reader()
        self.assertIsInstance(reader, AccountReadConnector)
        caps = reader.capabilities()
        self.assertEqual(caps.supported, {Capability.SERVER_TIME, Capability.ACCOUNT_READ, Capability.BALANCES})
        with self.assertRaises(CapabilityNotSupported):
            reader.require(Capability.ORDER_PLACEMENT)

    def test_no_order_or_funds_methods(self):
        for cls in (bs.BinanceSpotPublicData, bs.BinanceSpotAccountReader):
            names = [name.lower() for name in dir(cls)]
            for word in ("order", "cancel", "withdraw", "transfer", "submit"):
                self.assertFalse([n for n in names if word in n], (cls.__name__, word))

    def test_server_time_measures_offset(self):
        self.server.skew_ms = 1500
        connector = self.public()
        server = connector.server_time()
        self.assertEqual(server.tzinfo, UTC)
        self.assertAlmostEqual(connector.clock_offset.total_seconds(), 1.5, places=2)

    def test_ping(self):
        self.public().ping()
        self.assertEqual(self.server.paths(), ["/api/v3/ping"])


class ExchangeInfoTests(ServerCase):
    def test_rules_parsed_as_decimal_with_provider_source(self):
        connector = self.public()
        rules = connector.symbol_rules()
        btc = rules["BTCUSDT"]
        self.assertEqual(btc.tick_size, Decimal("0.01"))
        self.assertIsInstance(btc.step_size, Decimal)
        self.assertEqual(btc.min_notional, Decimal("5"))
        self.assertEqual(btc.notional_filter, "NOTIONAL")
        self.assertEqual(btc.source, "PROVIDER_API:binance_spot:exchangeInfo")
        self.assertIsNone(btc.maker_fee)
        self.assertIsNone(btc.taker_fee)
        self.assertEqual(rules["ETHBTC"].notional_filter, "MIN_NOTIONAL")
        self.assertEqual(rules["ETHBTC"].min_notional, Decimal("0.0001"))
        self.assertEqual(rules["OLDCOIN"].status, "BREAK")
        # tickSize 0 disables the tick rule: refused rather than guessed.
        self.assertNotIn("NOTICK", rules)
        self.assertIn("tick size rule disabled", connector.rejected_symbols["NOTICK"])
        self.assertEqual(abs((btc.as_of - self.clock.t).total_seconds()) < 1, True)

    def test_specs_need_fees_and_apply_step_and_notional_rules(self):
        with self.assertRaises(bs.MissingFeeSchedule):
            self.public().instrument_specs()
        connector = self.public(fees=(Decimal("0.001"), Decimal("0.001")))
        specs = connector.instrument_specs(("BTCUSDT", "OLDCOIN"))
        spec = specs["BTCUSDT"]
        self.assertIsInstance(spec, CryptoSpotSpec)
        self.assertEqual(spec.price_precision, 2)
        self.assertEqual(spec.quantity_precision, 5)
        self.assertEqual(spec.round_quantity_down("0.123456789"), Decimal("0.12345"))
        self.assertEqual(spec.round_price_down("65000.019"), Decimal("65000.01"))
        self.assertEqual(spec.order_violations("0.00001", "65000.00"), ("notional_below_minimum",))
        self.assertEqual(spec.order_violations("0.001", "65000.00"), ())
        self.assertIn("quantity_not_on_step", spec.order_violations("0.000015", "65000.00"))
        self.assertIn("symbol_not_trading", specs["OLDCOIN"].order_violations("0.001", "65000.00"))
        query = self.server.requests[-1][1]
        self.assertEqual(json.loads(query["symbols"]), ["BTCUSDT", "OLDCOIN"])

    def test_requested_symbol_missing_or_rejected_raises(self):
        connector = self.public()
        with self.assertRaises(ValueError):
            connector.symbol_rules(("NOTICK",))
        with self.assertRaises(ValueError):
            connector.symbol_rules(("ZZZUSDT",))
        with self.assertRaises(ValueError):
            connector.symbol_rules(("btcusdt",))

    def test_weight_limit_read_from_rate_limits(self):
        self.server.rate_limits[0]["limit"] = 3000
        connector = self.public()
        connector.symbol_rules(("BTCUSDT",))
        self.assertEqual(connector.weight_limit, 3000)

    def test_missing_notional_filter_rejected(self):
        self.server.symbols = [dict(self.server.symbols[0], filters=self.server.symbols[0]["filters"][:2])]
        connector = self.public()
        self.assertEqual(connector.symbol_rules(), {})
        self.assertIn("minimum notional unknown", connector.rejected_symbols["BTCUSDT"])


class QuoteTests(ServerCase):
    def test_quote_uses_provider_clock(self):
        self.server.skew_ms = 800
        connector = self.public()
        quote = connector.quote("BTCUSDT")
        self.assertIsInstance(quote, Quote)
        self.assertEqual((quote.bid, quote.ask), (65000.01, 65000.02))
        self.assertEqual(quote.source, "PROVIDER_API:binance_spot:bookTicker")
        # Timestamp is local clock + measured offset, i.e. the provider clock.
        self.assertAlmostEqual((quote.timestamp - self.clock.t).total_seconds(), 0.8, places=2)
        self.assertEqual(self.server.paths(), ["/api/v3/time", "/api/v3/ticker/bookTicker"])

    def test_crossed_locked_and_empty_books_refused(self):
        connector = self.public()
        for book in (
            ("65000.03000000", "1.0", "65000.02000000", "1.0"),
            ("65000.02000000", "1.0", "65000.02000000", "1.0"),
            ("0.00000000", "1.0", "65000.02000000", "1.0"),
            ("65000.01000000", "0.00000000", "65000.02000000", "1.0"),
        ):
            self.server.book["BTCUSDT"] = book
            with self.assertRaises(BrokerRequestError) as ctx:
                connector.quote("BTCUSDT")
            self.assertEqual(ctx.exception.error_code, "INVALID_BOOK")

    def test_stale_quote_refused(self):
        connector = self.public(max_quote_age_seconds=5.0)
        connector.server_time()
        self.clock.step = timedelta(seconds=6)
        with self.assertRaises(StaleDataError) as ctx:
            connector.quote("BTCUSDT")
        self.assertEqual(ctx.exception.reason, "stale")

    def test_future_dated_provider_clock_refused(self):
        self.server.skew_ms = 10_000
        with self.assertRaises(StaleDataError) as ctx:
            self.public().quote("BTCUSDT")
        self.assertEqual(ctx.exception.reason, "future_dated")
        self.assertNotIn("/api/v3/ticker/bookTicker", self.server.paths())

    def test_provider_clock_far_behind_refused(self):
        self.server.skew_ms = -10_000
        with self.assertRaises(StaleDataError):
            self.public().quote("BTCUSDT")

    def test_unknown_symbol_is_a_request_error(self):
        with self.assertRaises(BrokerRequestError) as ctx:
            self.public().quote("ETHBTC")
        self.assertEqual(ctx.exception.error_code, "-1121")


class KlineTests(ServerCase):
    def test_closed_candles_only(self):
        connector = self.public()
        bars = connector.bars("BTCUSDT", "1m", limit=5)
        self.assertEqual(len(bars), 4)  # the still-open candle is dropped
        self.assertTrue(all(isinstance(bar, MarketBar) for bar in bars))
        self.assertEqual(connector.bars_source, "PROVIDER_API:binance_spot:klines")
        now = self.clock.t
        for bar in bars:
            self.assertLessEqual(bar.timestamp + timedelta(minutes=1), now)
            self.assertEqual(bar.timestamp.tzinfo, UTC)
        self.assertEqual(bars[-1].timestamp - bars[-2].timestamp, timedelta(minutes=1))
        self.assertEqual(bars[0].open, 65004.0)

    def test_parameters_sent(self):
        start = datetime(2026, 10, 1, tzinfo=UTC)
        self.public().bars("BTCUSDT", "5m", start=start, limit=10)
        query = self.server.requests[-1][1]
        self.assertEqual(query["startTime"], str(int(start.timestamp() * 1000)))
        self.assertEqual((query["interval"], query["limit"]), ("5m", "10"))

    def test_invalid_arguments(self):
        connector = self.public()
        with self.assertRaises(ValueError):
            connector.bars("BTCUSDT", "7m")
        with self.assertRaises(ValueError):
            connector.bars("BTCUSDT", "1m", limit=1001)
        with self.assertRaises(ValueError):
            connector.bars("BTCUSDT", "1m", start=datetime(2026, 1, 1))  # naive  # noqa: DTZ001

    def _rows(self, opens, interval=60_000):
        return [kline_row(o, interval, 65000) for o in opens]

    def test_data_quality_rules(self):
        base = 1_790_000_040_000 - 1_790_000_040_000 % 60_000
        cases = {
            "DUPLICATE_TIMESTAMP": self._rows([base, base]),
            "OUT_OF_ORDER_TIMESTAMP": self._rows([base, base - 60_000]),
            "INTERVAL_GAP": self._rows([base, base + 180_000]),
            "INTERVAL_MISMATCH": self._rows([base, base + 30_000]),
        }
        connector = self.public()
        for code, rows in cases.items():
            self.server.kline_rows = rows
            with self.assertRaises(bs.BarDataError) as ctx:
                connector.bars("BTCUSDT", "1m")
            self.assertEqual(ctx.exception.code, code)
        self.server.kline_rows = [kline_row(base, 60_000, 65000, high=64000)]
        with self.assertRaises(bs.BarDataError) as ctx:
            connector.bars("BTCUSDT", "1m")
        self.assertEqual(ctx.exception.code, "INCONSISTENT_OHLC")
        bad_close = kline_row(base, 60_000, 65000)
        bad_close[6] = base + 30_000
        self.server.kline_rows = [bad_close]
        with self.assertRaises(bs.BarDataError) as ctx:
            connector.bars("BTCUSDT", "1m")
        self.assertEqual(ctx.exception.code, "CLOSE_TIME_MISMATCH")


class RateLimitTests(ServerCase):
    def test_used_weight_header_tracked(self):
        connector = self.public()
        connector.symbol_rules(("BTCUSDT",))
        self.assertEqual(connector.used_weight["1M"], self.server.used_weight)
        self.assertEqual(self.server.used_weight, 20)

    def test_local_budget_refuses_without_sending(self):
        connector = self.public()
        self.server.used_weight = 4790
        connector.ping()  # provider now reports 4791
        sent = len(self.server.requests)
        with self.assertRaises(BrokerRequestError) as ctx:
            connector.symbol_rules()
        self.assertEqual(ctx.exception.error_code, "LOCAL_WEIGHT_BUDGET")
        self.assertTrue(ctx.exception.transient)
        self.assertEqual(len(self.server.requests), sent)

    def test_429_honours_retry_after_then_succeeds(self):
        self.server.script("/api/v3/ping", Scripted(429, {"code": -1003, "msg": "Too many requests"}, {"Retry-After": "2"}))
        self.public().ping()
        self.assertEqual(self.fake_time.sleeps, [2.0])
        self.assertEqual(self.server.paths(), ["/api/v3/ping", "/api/v3/ping"])

    def test_429_with_long_retry_after_stops_and_blocks_locally(self):
        self.server.script("/api/v3/ping", Scripted(429, {"code": -1003, "msg": "Too many requests"}, {"Retry-After": "120"}))
        connector = self.public()
        with self.assertRaises(BrokerRequestError) as ctx:
            connector.ping()
        self.assertEqual((ctx.exception.error_code, ctx.exception.retry_after_seconds), ("HTTP_429", 120.0))
        self.assertTrue(ctx.exception.transient)
        with self.assertRaises(BrokerRequestError):
            connector.ping()
        self.assertEqual(len(self.server.requests), 1)  # no hammering
        self.clock.advance(121)
        connector.ping()
        self.assertEqual(len(self.server.requests), 2)

    def test_418_stops_until_ban_ends(self):
        self.server.script("/api/v3/ping", Scripted(418, {"code": -1003, "msg": "banned"}, {"Retry-After": "300"}))
        connector = self.public()
        with self.assertRaises(bs.BinanceIpBanned) as ctx:
            connector.ping()
        self.assertTrue(ctx.exception.permanent)
        self.assertIsNotNone(ctx.exception.banned_until)
        self.assertTrue(connector.is_banned)
        for _ in range(3):
            with self.assertRaises(bs.BinanceIpBanned):
                connector.ping()
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.fake_time.sleeps, [])
        self.clock.advance(301)
        connector.ping()
        self.assertFalse(connector.is_banned)

    def test_418_without_retry_after_needs_a_human(self):
        self.server.script("/api/v3/ping", Scripted(418, {"code": -1003, "msg": "banned"}))
        connector = self.public()
        with self.assertRaises(bs.BinanceIpBanned):
            connector.ping()
        self.clock.advance(4 * 86_400)
        with self.assertRaises(bs.BinanceIpBanned):
            connector.ping()
        connector.acknowledge_ban_cleared()
        connector.ping()
        self.assertEqual(len(self.server.requests), 2)


class FailureMappingTests(ServerCase):
    def test_timeout_maps_to_broker_timeout_with_bounded_retries(self):
        self.server.script("/api/v3/ping", Scripted(body={}, delay=0.6), Scripted(body={}, delay=0.6))
        connector = self.public(timeout=0.2, max_attempts=2)
        with self.assertRaises(BrokerTimeout):
            connector.ping()
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.fake_time.sleeps, [0.5])

    def test_dropped_connection_maps_to_unavailable(self):
        self.server.script("/api/v3/ping", *[Scripted(drop=True) for _ in range(3)])
        with self.assertRaises(BrokerUnavailable) as ctx:
            self.public(max_attempts=3).ping()
        self.assertIn("after 3 attempts", str(ctx.exception))
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(self.fake_time.sleeps, [0.5, 1.0])

    def test_dropped_then_reconnects(self):
        self.server.script("/api/v3/ping", Scripted(drop=True))
        self.public().ping()
        self.assertEqual(len(self.server.requests), 2)

    def test_5xx_retried_then_recovers(self):
        self.server.script("/api/v3/ping", Scripted(503, {}), Scripted(502, {}))
        self.public().ping()
        self.assertEqual(len(self.server.requests), 3)

    def test_5xx_exhausts_bounded_attempts(self):
        self.server.script("/api/v3/ping", *[Scripted(500, {}) for _ in range(5)])
        with self.assertRaises(BrokerUnavailable):
            self.public(max_attempts=3).ping()
        self.assertEqual(len(self.server.requests), 3)

    def test_4xx_and_waf_not_retried(self):
        self.server.script("/api/v3/ping", Scripted(403, {}))
        with self.assertRaises(BrokerRequestError) as ctx:
            self.public().ping()
        self.assertEqual(ctx.exception.error_code, "HTTP_403")
        self.assertEqual(len(self.server.requests), 1)

    def test_retry_bounds_validated(self):
        with self.assertRaises(bs.BinanceConfigurationError):
            self.public(max_attempts=6)
        with self.assertRaises(bs.BinanceConfigurationError):
            self.public(max_attempts=0)

    def test_invalid_json_refused(self):
        self.server.script("/api/v3/time", Scripted(raw=b"<html>"))
        with self.assertRaises(BrokerRequestError) as ctx:
            self.public().server_time()
        self.assertEqual(ctx.exception.error_code, "INVALID_JSON")


class BaseUrlTests(unittest.TestCase):
    def test_base_url_rules(self):
        bs.BinanceSpotPublicData()  # default data-api.binance.vision, no network at construction
        bs.BinanceSpotPublicData("https://api.binance.com")
        for url in ("http://data-api.binance.vision", "https://example.com", "https://data-api.binance.vision/api",
                    "https://user:pw@api.binance.com", "http://127.0.0.1:1"):
            with self.assertRaises(bs.BinanceConfigurationError, msg=url):
                bs.BinanceSpotPublicData(url)
        with self.assertRaises(bs.BinanceConfigurationError):
            bs.BinanceSpotPublicData("https://api.binance.com", loopback_role="production")
        with self.assertRaises(bs.BinanceConfigurationError):
            bs.BinanceSpotAccountReader(SecretValue(KEY), SecretValue(SECRET), "https://data-api.binance.vision")


class ReadOnlyKeyTests(ServerCase):
    def test_read_only_key_reads_balances(self):
        reader = self.reader()
        self.assertEqual(reader.account_environment(), (VenueEnvironment.LIVE, EnvironmentSource.UNVERIFIED))
        balances = reader.balances()
        self.assertEqual([b.asset for b in balances], ["BTC", "USDT"])
        self.assertIsInstance(balances[1], Balance)
        self.assertEqual(balances[1].locked, Decimal("25"))
        self.assertEqual(self.server.paths(), ["/api/v3/time", "/sapi/v1/account/apiRestrictions", "/api/v3/account"])
        account_query = self.server.requests[-1][1]
        self.assertEqual(account_query["recvWindow"], "5000")
        self.assertEqual(account_query["omitZeroBalances"], "true")
        self.assertIn("signature", account_query)
        # A loopback fake is never provider evidence, even with loopback_role="production" (MV-A-2).
        self.assertEqual(reader.account_environment(), (VenueEnvironment.LIVE, EnvironmentSource.UNVERIFIED))
        # Verified once; not re-read within the re-verification window.
        reader.balances()
        self.assertEqual(self.server.paths().count("/sapi/v1/account/apiRestrictions"), 1)

    def test_dangerous_permissions_refused_before_account_read(self):
        for flag in ("enableSpotAndMarginTrading", "enableWithdrawals", "enableMargin", "enableFutures",
                     "enableInternalTransfer", "permitsUniversalTransfer", "enableVanillaOptions",
                     "enableFixApiTrade", "enablePortfolioMarginTrading", "enableSomethingNew"):
            with self.subTest(flag=flag):
                self.server.requests.clear()
                self.server.restrictions = {**bs_restrictions(), flag: True}
                with self.assertRaises(bs.KeyNotReadOnly) as ctx:
                    self.reader().balances()
                self.assertTrue(ctx.exception.permanent)
                self.assertNotIn("/api/v3/account", self.server.paths())

    def test_unverifiable_permissions_refused(self):
        for restrictions in (
            {k: v for k, v in bs_restrictions().items() if k != "enableWithdrawals"},
            {**bs_restrictions(), "enableReading": False},
            {**bs_restrictions(), "enableFutures": "false"},
            [],
        ):
            with self.subTest(restrictions=restrictions):
                self.server.restrictions = restrictions
                with self.assertRaises(bs.KeyNotReadOnly):
                    self.reader().balances()

    def test_testnet_cannot_verify_permissions(self):
        reader = self.reader(loopback_role="testnet")
        self.assertEqual(reader.account_environment(), (VenueEnvironment.TESTNET, EnvironmentSource.UNVERIFIED))
        with self.assertRaises(bs.KeyNotReadOnly):
            reader.balances()
        self.assertEqual(self.server.requests, [])
        demo = self.reader(loopback_role="demo")
        self.assertEqual(demo.account_environment(), (VenueEnvironment.DEMO, EnvironmentSource.UNVERIFIED))

    def test_bad_signature_is_a_request_error(self):
        with self.assertRaises(BrokerRequestError) as ctx:
            self.reader(secret="wrong-secret-CCCCCCCCCCCC").verify_read_only()
        self.assertEqual(ctx.exception.error_code, "-1022")

    def test_recv_window_kept_small(self):
        with self.assertRaises(bs.BinanceConfigurationError):
            self.reader(recv_window_ms=60_000)


def bs_restrictions():
    return dict(READ_ONLY_RESTRICTIONS)


class SecretHandlingTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.stream = io.StringIO()
        configure_logging(level="DEBUG", stream=self.stream)
        self.addCleanup(reset_logging)

    def test_secrets_never_in_logs_reprs_or_errors(self):
        self.server.script("/api/v3/account",
                           Scripted(400, {"code": -1100, "msg": f"Illegal characters found in {KEY} {SECRET}"}))
        self.server.script("/api/v3/time", Scripted(500, {}))
        reader = self.reader()
        texts = [repr(reader), str(reader)]
        with self.assertRaises(BrokerRequestError) as ctx:
            reader.balances()
        texts.append(str(ctx.exception))
        texts.append(repr(ctx.exception))
        self.server.restrictions = {**bs_restrictions(), "enableWithdrawals": True}
        with self.assertRaises(bs.KeyNotReadOnly) as ctx2:
            self.reader().verify_read_only()
        texts.append(str(ctx2.exception))
        logging.getLogger("astra.test").warning("leak attempt %s %s", KEY, SECRET)
        texts.append(self.stream.getvalue())
        self.assertIn("binance.retry_wait", self.stream.getvalue())
        for text in texts:
            self.assertNotIn(KEY, text)
            self.assertNotIn(SECRET, text)
        with self.assertRaises(TypeError):
            pickle.dumps(reader)

    def test_credentials_only_from_named_environment_variables(self):
        environ = {"ASTRA_SECRET_BINANCE_API_KEY": KEY, "ASTRA_SECRET_BINANCE_API_SECRET": SECRET}
        reader = bs.BinanceSpotAccountReader.from_environment(
            environ, base_url=self.server.base_url, loopback_role="production", test_only_skip_registry_gate=True,
            clock=self.clock,
        )
        reader.verify_read_only()
        for missing in ({"ASTRA_SECRET_BINANCE_API_KEY": KEY}, {"BINANCE_API_KEY": KEY, "BINANCE_API_SECRET": SECRET}):
            with self.assertRaises(bs.BinanceConfigurationError) as ctx:
                bs.BinanceSpotAccountReader.from_environment(missing)
            self.assertNotIn(KEY, str(ctx.exception))
        with self.assertRaises(bs.BinanceConfigurationError):
            bs.BinanceSpotAccountReader(KEY, SECRET)  # plain strings refused

    def test_vault_path_requires_human_admin(self):
        environ = {"ASTRA_SECRET_BINANCE_API_KEY": KEY, "ASTRA_SECRET_BINANCE_API_SECRET": SECRET}
        vault = SecretVault(PermissionGuard(), environ)
        reader = bs.BinanceSpotAccountReader.from_vault(
            vault, Actor("ops", Role.HUMAN_ADMIN), base_url=self.server.base_url, loopback_role="production",
            test_only_skip_registry_gate=True,
        )
        self.assertNotIn(KEY, repr(reader))
        with self.assertRaises(PermissionError):
            bs.BinanceSpotAccountReader.from_vault(vault, Actor("bot", Role.LLM))


class RegistryGateTests(ServerCase):
    def test_shipped_registry_blocks_public_data_without_network(self):
        connector = bs.BinanceSpotPublicData(self.server.base_url, loopback_role="public_data")
        with self.assertRaises(bs.RegistryGateRefused):
            connector.ping()
        with self.assertRaises(bs.RegistryGateRefused):
            connector.quote("BTCUSDT")
        self.assertEqual(self.server.requests, [])

    def test_shipped_registry_blocks_account_reads_without_network(self):
        reader = bs.BinanceSpotAccountReader(SecretValue(KEY), SecretValue(SECRET), self.server.base_url,
                                             loopback_role="production")
        with self.assertRaises(bs.RegistryGateRefused):
            reader.balances()
        self.assertEqual(self.server.requests, [])

    def test_override_only_with_loopback(self):
        with self.assertRaises(bs.BinanceConfigurationError):
            bs.BinanceSpotPublicData(test_only_skip_registry_gate=True)
        with self.assertRaises(bs.BinanceConfigurationError):
            bs.BinanceSpotPublicData(self.server.base_url, loopback_role="public_data", test_only_skip_registry_gate=1)

    def test_gate_reads_record_statuses(self):
        shipped = venue_registry.load_registry().get("binance_spot")
        self.assertFalse(venue_registry.can_read_public_data(shipped))
        S = venue_registry.VenueStatus
        grants = tuple(venue_registry.StatusGrant(s, "2026-10-26", "test")
                       for s in (S.DISCOVERED, S.OFFICIAL_API_VERIFIED, S.REGION_ELIGIBILITY_VERIFIED))
        public_ok = dataclasses.replace(shipped, statuses=grants)
        connector = bs.BinanceSpotPublicData(self.server.base_url, loopback_role="public_data",
                                             registry_record=public_ok, clock=self.clock)
        connector.ping()
        reader = bs.BinanceSpotAccountReader(SecretValue(KEY), SecretValue(SECRET), self.server.base_url,
                                             loopback_role="production", registry_record=public_ok)
        with self.assertRaises(bs.RegistryGateRefused):
            reader.verify_read_only()
        account_ok = dataclasses.replace(
            shipped, statuses=(*grants, venue_registry.StatusGrant(S.READ_ONLY_CONNECTED, "2026-10-26", "test"))
        )
        reader = bs.BinanceSpotAccountReader(SecretValue(KEY), SecretValue(SECRET), self.server.base_url,
                                             loopback_role="production", registry_record=account_ok, clock=self.clock)
        reader.verify_read_only()
        wrong = dataclasses.replace(public_ok, venue_id="other_venue")
        with self.assertRaises(bs.RegistryGateRefused):
            bs.BinanceSpotPublicData(self.server.base_url, loopback_role="public_data", registry_record=wrong).ping()


class SharedStopStateTests(ServerCase):
    """MV-A-3: ban, 429 stop and weight budget are process-wide per base URL, optionally persisted."""

    def test_ban_and_429_stop_shared_by_new_instances(self):
        self.server.script("/api/v3/ping", Scripted(418, {"code": -1003, "msg": "banned"}, {"Retry-After": "300"}))
        with self.assertRaises(bs.BinanceIpBanned):
            self.public().ping()
        with self.assertRaises(bs.BinanceIpBanned):
            self.public().ping()
        with self.assertRaises(bs.BinanceIpBanned):
            self.reader().verify_read_only()  # same host, other class
        self.assertEqual(len(self.server.requests), 1)
        self.clock.advance(301)
        self.public().ping()
        self.server.script("/api/v3/ping", Scripted(429, {}, {"Retry-After": "120"}))
        with self.assertRaises(BrokerRequestError):
            self.public().ping()
        with self.assertRaises(BrokerRequestError):
            self.public().ping()
        self.assertEqual(len(self.server.requests), 3)

    def test_weight_budget_shared_by_instances(self):
        self.server.used_weight = 4790
        self.public().ping()
        sent = len(self.server.requests)
        with self.assertRaises(BrokerRequestError) as ctx:
            self.public().symbol_rules()
        self.assertEqual(ctx.exception.error_code, "LOCAL_WEIGHT_BUDGET")
        self.assertEqual(len(self.server.requests), sent)

    def test_ban_persisted_across_processes(self):
        with workspace_tempdir() as directory:
            state = Path(directory) / "binance_state.json"
            self.server.script("/api/v3/ping", Scripted(418, {"code": -1003, "msg": "banned"}))
            with self.assertRaises(bs.BinanceIpBanned):
                self.public(state_path=state).ping()
            document = json.loads(state.read_text(encoding="utf-8"))
            self.assertTrue(document["hosts"][f"loopback:{self.server.base_url}"]["banned"])  # state key per provider scope (MV-A-10)
            bs.reset_host_states_for_tests()  # simulates a restarted process
            with self.assertRaises(bs.BinanceIpBanned):
                self.public(state_path=state).ping()
            self.assertEqual(len(self.server.requests), 1)
            self.public(state_path=state).acknowledge_ban_cleared()
            bs.reset_host_states_for_tests()
            self.public(state_path=state).ping()
            self.assertEqual(len(self.server.requests), 2)

    def test_unreadable_state_file_refused(self):
        with workspace_tempdir() as directory:
            state = Path(directory) / "binance_state.json"
            state.write_text("{not json", encoding="utf-8")
            with self.assertRaises(bs.BinanceConfigurationError):
                self.public(state_path=state)
            state.write_text(json.dumps({"schema": "other", "hosts": {}}), encoding="utf-8")
            with self.assertRaises(bs.BinanceConfigurationError):
                self.public(state_path=state)


class QaFixTests(ServerCase):
    def test_429_with_unusable_retry_after_stops_until_acknowledged(self):
        # MV-D-1
        for header in ("400000", "soon", "-5", "nan"):
            with self.subTest(header=header):
                bs.reset_host_states_for_tests()
                self.server.requests.clear()
                self.server.script("/api/v3/ping", Scripted(429, {}, {"Retry-After": header}))
                connector = self.public()
                with self.assertRaises(BrokerRequestError) as ctx:
                    connector.ping()
                self.assertIsNone(ctx.exception.retry_after_seconds)
                self.clock.advance(4 * 86_400)
                self.fake_time.now += 4 * 86_400
                with self.assertRaises(BrokerRequestError):
                    self.public().ping()
                self.assertEqual(len(self.server.requests), 1)
                connector.acknowledge_ban_cleared()
                connector.ping()

    def test_429_without_retry_after_blocks_sixty_seconds(self):
        self.server.script("/api/v3/ping", Scripted(429, {}))
        connector = self.public()
        with self.assertRaises(BrokerRequestError) as ctx:
            connector.ping()
        self.assertEqual(ctx.exception.retry_after_seconds, 60.0)
        self.clock.advance(61)
        connector.ping()

    def test_provider_weight_limit_never_raises_the_budget(self):
        self.server.rate_limits[0]["limit"] = 12_000
        connector = self.public()
        connector.symbol_rules(("BTCUSDT",))
        self.assertEqual(connector.weight_limit, 6000)

    def test_duplicated_symbol_refused_in_every_copy(self):
        # MV-D-2
        entry = self.server.symbols[0]
        self.server.symbols = [entry, entry, entry, self.server.symbols[1]]
        connector = self.public()
        rules = connector.symbol_rules()
        self.assertEqual(set(rules), {"ETHBTC"})
        self.assertIn("duplicate", connector.rejected_symbols["BTCUSDT"])

    def test_absurd_filter_values_rejected(self):
        # MV-D-3
        from binance_fixture import symbol_entry

        self.server.symbols = [
            symbol_entry("AAAUSDT", "AAA", "USDT", tick="1E-99"),
            symbol_entry("BBBUSDT", "BBB", "USDT", step="1E+12"),
            symbol_entry("CCCUSDT", "CCC", "USDT", max_qty="1E+30"),
            symbol_entry("BTCUSDT", "BTC", "USDT"),
        ]
        connector = self.public()
        self.assertEqual(set(connector.symbol_rules()), {"BTCUSDT"})
        self.assertIn("sanity range", connector.rejected_symbols["AAAUSDT"])

    def test_spec_carries_and_enforces_price_and_notional_bounds(self):
        # MV-D-5
        rules = self.public().symbol_rules(("BTCUSDT",))["BTCUSDT"]
        spec = rules.to_spec(Decimal("0.001"), Decimal("0.001"))
        self.assertEqual((spec.min_price, spec.max_price, spec.max_notional),
                         (Decimal("0.01"), Decimal("1000000"), Decimal("9000000")))
        self.assertIn("price_above_maximum", spec.order_violations("0.001", "2000000.00"))
        self.assertIn("price_below_minimum", spec.order_violations("1000", "0.001"))
        self.assertIn("notional_above_maximum", spec.order_violations("9000", "1000.01"))
        self.assertEqual(spec.order_violations("0.001", "65000.00"), ())


class ReviewFixTests(ServerCase):
    def test_in_memory_record_refused_for_real_host(self):
        # MV-A-1: no socket is opened; the gate refuses before _send.
        shipped = venue_registry.load_registry().get("binance_spot")
        S = venue_registry.VenueStatus
        forged = dataclasses.replace(shipped, statuses=tuple(
            venue_registry.StatusGrant(s, "2026-10-26", "forged")
            for s in (S.DISCOVERED, S.OFFICIAL_API_VERIFIED, S.REGION_ELIGIBILITY_VERIFIED, S.READ_ONLY_CONNECTED)))
        with patch.object(bs._BinanceHttp, "_send", side_effect=AssertionError("network used")):
            with self.assertRaises(bs.RegistryGateRefused):
                bs.BinanceSpotPublicData(registry_record=forged).ping()
            reader = bs.BinanceSpotAccountReader(SecretValue(KEY), SecretValue(SECRET), registry_record=forged)
            with self.assertRaises(bs.RegistryGateRefused):
                reader.balances()

    def test_real_production_host_yields_provider_api_after_signed_read(self):
        # MV-A-2 counterpart: a real production host (patched transport, gate patched open) is evidence.
        def send(_self, url, _headers):
            path = url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            bodies = {
                "api/v3/time": {"serverTime": self.clock.peek_ms()},
                "sapi/v1/account/apiRestrictions": bs_restrictions(),
                "api/v3/account": {"accountType": "SPOT", "balances": [], "canTrade": False},
            }
            return 200, {}, json.dumps(bodies[path]).encode()

        with patch.object(bs._BinanceHttp, "_send", send), patch.object(bs.venue_registry, "can_read_account", return_value=True):
            reader = bs.BinanceSpotAccountReader(SecretValue(KEY), SecretValue(SECRET), clock=self.clock)
            self.assertEqual(reader.account_environment(), (VenueEnvironment.LIVE, EnvironmentSource.UNVERIFIED))
            self.assertEqual(reader.balances(), ())
            self.assertEqual(reader.account_environment(), (VenueEnvironment.LIVE, EnvironmentSource.PROVIDER_API))

    def test_reverify_interval_bounded(self):
        # MV-A-4
        for value in (float("inf"), float("nan"), 0, 0.5, 3601, 1e12, True, "300", None):
            with self.subTest(value=value), self.assertRaises(bs.BinanceConfigurationError):
                self.reader(reverify_after_seconds=value)
        self.reader(reverify_after_seconds=1)
        self.reader(reverify_after_seconds=3600)

    def test_secret_value_not_pickled_or_copied_in_clear(self):
        # MV-A-5
        secret = SecretValue(SECRET)
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with self.assertRaises(TypeError):
                pickle.dumps(secret, protocol=protocol)
        self.assertIs(copy.copy(secret), secret)
        self.assertIs(copy.deepcopy(secret), secret)
        self.assertIs(copy.deepcopy({"k": secret})["k"], secret)
        self.assertEqual(secret.reveal(), SECRET)


class NoWriteEndpointTests(unittest.TestCase):
    SOURCE = Path(bs.__file__)

    def test_source_has_only_read_paths_and_get(self):
        tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
        strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        paths = {s for s in strings if s.startswith(("/api/", "/sapi/", "/wapi/", "/fapi/", "/dapi/"))}
        self.assertEqual(paths, set(bs.ALLOWED_PATHS))
        self.assertEqual(
            bs.ALLOWED_PATHS,
            {"/api/v3/ping", "/api/v3/time", "/api/v3/exchangeInfo", "/api/v3/ticker/bookTicker", "/api/v3/klines",
             "/api/v3/account", "/sapi/v1/account/apiRestrictions"},
        )
        for path in paths:
            for word in ("order", "withdraw", "transfer", "userdatastream", "capital", "margin", "loan"):
                self.assertNotIn(word, path.lower())
        methods = {s for s in strings if s.upper() in {"POST", "PUT", "DELETE", "PATCH"}}
        self.assertEqual(methods, set())
        self.assertEqual(bs._GET, "GET")
        # Every urllib Request is built with method=_GET.
        requests = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "Request"]
        self.assertEqual(len(requests), 1)
        method = {kw.arg: kw.value for kw in requests[0].keywords}["method"]
        self.assertEqual(getattr(method, "id", None), "_GET")

    def test_runtime_refuses_unlisted_path(self):
        connector = bs.BinanceSpotPublicData(
            "http://127.0.0.1:1", test_only_skip_registry_gate=True, loopback_role="public_data"
        )
        with self.assertRaises(bs.BinanceConfigurationError):
            connector._http.get_json(bs.Endpoint("/api/v3/order", 1, False, bs.SPOT_DOCS_URL))

    def test_fake_server_saw_no_write_attempts(self):
        server = FakeBinanceServer(api_key=KEY, api_secret=SECRET).start()
        self.addCleanup(server.stop)
        connector = bs.BinanceSpotPublicData(server.base_url, test_only_skip_registry_gate=True, loopback_role="public_data")
        connector.symbol_rules(("BTCUSDT",))
        connector.quote("BTCUSDT")
        connector.bars("BTCUSDT", "1m")
        reader = bs.BinanceSpotAccountReader(SecretValue(KEY), SecretValue(SECRET), server.base_url,
                                             test_only_skip_registry_gate=True, loopback_role="production")
        reader.balances()
        self.assertEqual(server.write_attempts, [])


if __name__ == "__main__":
    unittest.main()
