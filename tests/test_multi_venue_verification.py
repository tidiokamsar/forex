"""Multi-venue lot: independent QA (agent D) against feat/multi-venue 21bbd37.

ALL OFFLINE. The Binance tests use tests/binance_fixture.py on 127.0.0.1; nothing here contacts a venue,
sends an order or uses a real key. Every test asserts the secure or honest behaviour; scenarios that fail
on 21bbd37 carry ``@unittest.expectedFailure`` and a ``# MV-D-n`` comment with a severity.
"""

import json
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from astra.brokers.base import BrokerRequestError, BrokerUnavailable, CapabilityNotSupported
from astra.markets.costs import BookTicker, SlippageAssumption, SpotSide
from astra.markets.crypto_spot import floor_to_step
from astra.markets.risk_policies import (
    DERIVATIVES_NOT_ENABLED_REASON,
    CryptoFuturesRiskPolicy,
    CryptoSpotRiskPolicy,
    FixedExpiryOptionsRiskPolicy,
    SpotPosition,
    Verdict,
)
from astra.models import RunMode
from astra.venues import binance_spot as bs
from astra.venues import registry as venue_registry
from binance_fixture import Scripted, symbol_entry
from test_binance_spot import ServerCase
from test_market_risk import NOW, balances, book, buy, context, sell

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


# --- 1. Binance connector: rate limits, adversarial responses ------------------------------------


class RateLimitAdversarialTests(ServerCase):
    # FIXED (was MV-D-1) (Medium): a 429 whose Retry-After exceeds 3 days + 60 s (or is not a number) is treated as absent
    # and blocks locally for only 60 s, although the provider asked for longer; resuming early is exactly what
    # escalates a 429 into a 418 IP ban. A 418 with the same header is open-ended (safe); a 429 is not.
    def test_429_with_retry_after_beyond_three_days_keeps_blocking(self):
        self.server.script("/api/v3/ping", Scripted(429, {"code": -1003, "msg": "Too many requests"},
                                                    {"Retry-After": "400000"}))
        connector = self.public()
        with self.assertRaises(BrokerRequestError):
            connector.ping()
        self.fake_time.now += 61
        with self.assertRaises(BrokerRequestError):
            connector.ping()  # must still be blocked locally: the provider asked for 400000 s
        self.assertEqual(len(self.server.requests), 1)

    def test_418_with_unparsable_retry_after_needs_a_human(self):
        self.server.script("/api/v3/ping", Scripted(418, {"code": -1003, "msg": "banned"},
                                                    {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))
        connector = self.public()
        with self.assertRaises(bs.BinanceIpBanned):
            connector.ping()
        self.fake_time.now += 10 * 86_400
        self.clock.advance(10 * 86_400)
        with self.assertRaises(bs.BinanceIpBanned):
            connector.ping()
        self.assertEqual(len(self.server.requests), 1)

    def test_html_error_pages_and_partial_json_fail_closed(self):
        html = b"<html><body>502 Bad Gateway</body></html>"
        self.server.script("/api/v3/ping", Scripted(200, raw=html))
        with self.assertRaises(BrokerRequestError) as caught:
            self.public().ping()
        self.assertEqual(caught.exception.error_code, "INVALID_JSON")
        self.server.script("/api/v3/time", Scripted(200, raw=b'{"serverTime": 17'))
        with self.assertRaises(BrokerRequestError):
            self.public().server_time()
        self.server.script("/api/v3/ping", *(Scripted(502, raw=html) for _ in range(3)))
        with self.assertRaises(BrokerUnavailable):
            self.public(max_attempts=3).ping()

    def test_gate_closed_by_the_shipped_registry(self):
        record = venue_registry.load_registry().get("binance_spot")
        self.assertFalse(venue_registry.can_read_public_data(record))
        self.assertFalse(venue_registry.can_read_account(record))
        self.assertFalse(venue_registry.can_trade(record))
        with self.assertRaises((bs.RegistryGateRefused, bs.BinanceConfigurationError, CapabilityNotSupported)):
            bs.BinanceSpotPublicData(self.server.base_url, loopback_role="public_data").ping()
        self.assertEqual(len(self.server.requests), 0)


class ExchangeInfoAdversarialTests(ServerCase):
    def rules_with(self, entries):
        self.server.symbols = entries
        connector = self.public()
        return connector.symbol_rules(), connector.rejected_symbols

    # FIXED (was MV-D-2) (Low): duplicate detection drops the symbol on its second entry and then accepts the third
    # entry of the same symbol as if it were unique.
    def test_a_symbol_listed_three_times_is_rejected(self):
        entry = symbol_entry("BTCUSDT", "BTC", "USDT")
        rules, rejected = self.rules_with([entry, entry, entry])
        self.assertNotIn("BTCUSDT", rules)
        self.assertIn("BTCUSDT", rejected)

    def test_malformed_entries_are_rejected_not_fatal(self):
        good = symbol_entry("BTCUSDT", "BTC", "USDT")
        bad = [
            "not an object",
            {**good, "symbol": "btcusdt"},
            {**good, "symbol": "ETHUSDT", "filters": "none"},
            {**good, "symbol": "XRPUSDT", "filters": [f for f in good["filters"] if f["filterType"] != "LOT_SIZE"]},
            {**good, "symbol": "ADAUSDT", "filters": [
                {**f, "tickSize": "NaN"} if f["filterType"] == "PRICE_FILTER" else f for f in good["filters"]]},
            {**good, "symbol": "DOTUSDT", "filters": [
                {**f, "stepSize": "-1"} if f["filterType"] == "LOT_SIZE" else f for f in good["filters"]]},
            {**good, "symbol": "SOLUSDT", "quoteAsset": " usdt"},
        ]
        rules, rejected = self.rules_with([good, *bad])
        self.assertEqual(set(rules), {"BTCUSDT"})
        self.assertGreaterEqual(len(rejected), len(bad) - 1)  # the non-object entry is keyed "None"

    # FIXED (was MV-D-3) (Low; extreme Decimals): an absurd tick or step (1E-99) is accepted as a rule; rounding a
    # price on it then raises decimal.InvalidOperation instead of a ValueError or a refusal.
    def test_absurd_tick_is_rejected_at_parse_time(self):
        entry = symbol_entry("BTCUSDT", "BTC", "USDT", tick="1E-99")
        rules, _rejected = self.rules_with([entry])
        self.assertNotIn("BTCUSDT", rules)

    # FIXED (was MV-D-5) (Low; no order path yet): the PRICE_FILTER bounds (minPrice, maxPrice) and NOTIONAL maxNotional
    # are parsed into BinanceSymbolRules but dropped by to_spec(); CryptoSpotSpec.order_violations therefore
    # accepts a price above maxPrice. (The tick grid from zero is correct: filters.md states price % tickSize.)
    def test_spec_enforces_price_bounds_and_max_notional(self):
        rules, _ = self.rules_with([symbol_entry("BTCUSDT", "BTC", "USDT")])
        spec = rules["BTCUSDT"].to_spec(Decimal("0.001"), Decimal("0.001"))
        self.assertTrue(spec.order_violations(Decimal("1"), Decimal("2000000.00")))  # maxPrice is 1000000


class ExtremeDecimalTests(unittest.TestCase):
    # FIXED (was MV-D-4) (Low; extreme Decimals): floor_to_step works at a fixed 60-digit precision; when value/step needs
    # more digits, quantize() raises decimal.InvalidOperation (an ArithmeticError, not a ValueError), so
    # CryptoSpotRiskPolicy.evaluate raises on a 1E+70 quantity instead of returning REFUSE, and callers that
    # catch (TypeError, ValueError) are bypassed.
    def test_floor_to_step_never_rounds_up_and_never_raises_arithmetic_errors(self):
        value = Decimal("0." + "9" * 65)
        self.assertLessEqual(floor_to_step(value, Decimal("1E-70")), value)
        decision = CryptoSpotRiskPolicy().evaluate(buy(quantity=Decimal("1E+70")), context())
        self.assertIs(decision.verdict, Verdict.REFUSE)

    def test_realistic_steps_round_down_exactly(self):
        for value, step, expected in (("0.123456789", "0.00001", "0.12345"), ("65000.019", "0.01", "65000.01"),
                                      ("9000.00000001", "0.00000001", "9000.00000001"), ("0.00000999", "0.00001", "0")):
            with self.subTest(value=value):
                self.assertEqual(floor_to_step(Decimal(value), Decimal(step)), Decimal(expected))


# --- 2. Crypto spot risk policy: loopholes ------------------------------------------------------


class SpotPolicyLoopholeTests(unittest.TestCase):
    policy = CryptoSpotRiskPolicy()

    def test_baseline_buy_is_allowed_only_in_simulation(self):
        self.assertIs(self.policy.evaluate(buy(), context()).verdict, Verdict.ALLOW)
        for mode in (RunMode.DEMO, RunMode.LIVE):
            with self.subTest(mode=mode):
                self.assertIs(self.policy.evaluate(buy(), context(mode=mode)).verdict, Verdict.REFUSE)

    def test_splitting_cannot_exceed_the_daily_cap(self):
        # 2% orders are allowed one by one, but the reported day total stops them at 10%.
        self.assertIs(self.policy.evaluate(buy(quantity=Decimal("0.04")), context()).verdict, Verdict.ALLOW)
        late = context(day_buy_notional=Decimal("9000"))
        decision = self.policy.evaluate(buy(quantity=Decimal("0.04")), late)
        self.assertIn("daily_notional_exceeds_cap", decision.reasons)
        self.assertIn("daily_notional_unknown",
                      self.policy.evaluate(buy(), context(day_buy_notional=None)).reasons)

    # MV-D-8 (Medium): after a losing day the size-increase check runs only when last_buy_notional is known.
    # With it left None (unknown), a BUY far larger than any earlier one is allowed: an unknown value is
    # treated as "no constraint", contrary to "None means unknown and is refused".
    @unittest.expectedFailure
    def test_unknown_last_buy_after_a_loss_is_refused(self):
        losing = context(day_realized_pnl=Decimal("-500"), last_buy_notional=None)
        decision = self.policy.evaluate(buy(quantity=Decimal("0.04")), losing)
        self.assertIs(decision.verdict, Verdict.REFUSE)

    def test_larger_buy_after_a_loss_is_refused_when_known(self):
        losing = context(day_realized_pnl=Decimal("-500"), last_buy_notional=Decimal("500"))
        self.assertIn("risk_increase_after_loss", self.policy.evaluate(buy(), losing).reasons)

    # MV-D-9 (Low-Medium): adding to a losing position is detected only through the optional ``position``.
    # With base holdings in the balances (0.5 BTC) and position=None, the policy cannot know the entry price
    # and still allows the BUY instead of refusing an unknown position.
    @unittest.expectedFailure
    def test_holdings_without_position_state_are_refused(self):
        decision = self.policy.evaluate(buy(), context(balances=balances(base="0.5"), position=None))
        self.assertIs(decision.verdict, Verdict.REFUSE)

    def test_adding_to_a_losing_position_and_stop_changes(self):
        under = SpotPosition(Decimal("0.5"), Decimal("60000"), Decimal("55000"))
        self.assertIn("adding_to_losing_position", self.policy.evaluate(buy(), context(position=under)).reasons)
        for new_stop, code in ((None, "stop_removal_refused"), (Decimal("54000"), "stop_widening_refused")):
            with self.subTest(new_stop=new_stop):
                self.assertIn(code, self.policy.evaluate_stop_change(under, new_stop).reasons)
        self.assertIs(self.policy.evaluate_stop_change(under, Decimal("56000")).verdict, Verdict.ALLOW)

    # MV-D-10 (Low): caps can only be tightened, but the slippage assumption can be set to 0 and relabelled
    # (calibration is free text), which loosens the net-expectancy check without any recorded evidence.
    @unittest.expectedFailure
    def test_slippage_cannot_be_loosened_below_the_provisional_value(self):
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(slippage=SlippageAssumption(Decimal(0), calibration="CALIBRATED"))

    # MV-D-11 (Low): BookTicker accepts a locked book (bid == ask) and zero quantities; the cost model then
    # charges no spread. The Binance connector refuses such books, but the policy input type does not.
    @unittest.expectedFailure
    def test_locked_or_empty_book_is_refused_by_the_policy_input(self):
        with self.assertRaises(ValueError):
            book(ask_price=Decimal("50000.00"))
        with self.assertRaises(ValueError):
            book(bid_qty=Decimal("0"))

    # MV-D-12 (Low): a SELL is checked only against the free base balance; a SELL limit far below the bid
    # (here 1% of it) is allowed, so the "reduce only" path can dump holdings at any price.
    @unittest.expectedFailure
    def test_sell_far_below_the_market_is_refused(self):
        decision = self.policy.evaluate(sell(limit_price=Decimal("500.00")), context())
        self.assertIs(decision.verdict, Verdict.REFUSE)

    def test_sell_cannot_short_and_unknown_values_refuse(self):
        self.assertIn("sell_exceeds_free_base_balance",
                      self.policy.evaluate(sell(quantity=Decimal("1")), context()).reasons)
        for field, code in (("balances", "balances_unknown"), ("fees", "fees_unknown"), ("book", "quote_unknown"),
                            ("borrowing_enabled", "borrowing_enabled_or_unknown")):
            with self.subTest(field=field):
                self.assertIn(code, self.policy.evaluate(buy(), context(**{field: None})).reasons)
        stale = context(book=book(as_of=NOW - timedelta(seconds=6)))
        self.assertIn("quote_stale", self.policy.evaluate(buy(), stale).reasons)

    def test_derivatives_refused_in_every_mode(self):
        for policy in (CryptoFuturesRiskPolicy(), FixedExpiryOptionsRiskPolicy()):
            for mode in RunMode:
                with self.subTest(policy=policy.name, mode=mode):
                    decision = policy.evaluate(mode=mode)
                    self.assertIs(decision.verdict, Verdict.REFUSE)
                    self.assertIn(DERIVATIVES_NOT_ENABLED_REASON, decision.reasons)

    def test_policy_values_cannot_be_loosened(self):
        for kwargs in ({"max_order_notional_fraction": Decimal("0.03")}, {"max_daily_notional_fraction": Decimal("0.2")},
                       {"max_quote_age_seconds": 10.0}, {"leverage": 2}, {"allow_short": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CryptoSpotRiskPolicy(**kwargs)
        frozen = CryptoSpotRiskPolicy()
        with self.assertRaises((AttributeError, TypeError)):
            frozen.leverage = 2
        self.assertIs(replace(frozen, version="crypto-spot-1b").leverage, 1)


# --- 3. Import isolation and documentation honesty -------------------------------------------------


class ImportAndDocHonestyTests(unittest.TestCase):
    # MV-D-13 (Low; wording): RISK_POLICY.md says the policy modules "import no ... broker adapter", but
    # importing astra.markets.risk_policies loads astra.brokers.base (through astra.venues.contract), which
    # defines the BrokerAdapter ABC with submit_order. No order can be sent from it (abstract, permit-guarded),
    # but the statement is stronger than the code; the existing test does not list astra.brokers.base.
    @unittest.expectedFailure
    def test_policy_modules_do_not_load_the_broker_adapter_module(self):
        code = "import sys, astra.markets.risk_policies; print('astra.brokers.base' in sys.modules)"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                                env={"PYTHONPATH": str(SRC), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
        self.assertEqual(result.stdout.strip(), "False")

    def test_no_venue_claims_a_connection_or_trading(self):
        data = json.loads((SRC / "astra" / "venues" / "venue_registry.json").read_text(encoding="utf-8"))
        for venue in data["venues"]:
            held = {grant["status"] for grant in venue["statuses"]}
            with self.subTest(venue=venue["venue_id"]):
                self.assertFalse(held & {"READ_ONLY_CONNECTED", "PAPER_TESTED", "DEMO_VALIDATED",
                                         "LIVE_REVIEW_REQUIRED"})
                self.assertTrue(held & {"UNVERIFIED", "ELIGIBILITY_UNKNOWN", "BLOCKED"})
        self.assertFalse(venue_registry.TRADING_ENABLED_IN_THIS_LOT)

    def test_docs_never_call_a_venue_integrated(self):
        for name in ("VENUE_REGISTRY.md", "BROKERS.md", "ARCHITECTURE.md", "docs/venues/research_forex.md",
                     "docs/venues/research_crypto_other.md"):
            text = (ROOT / name).read_text(encoding="utf-8").lower()
            for phrase in ("is integrated", "are integrated", "now integrated", "fully integrated",
                           "integration complete", "read_only_connected since"):
                with self.subTest(doc=name, phrase=phrase):
                    for line in text.splitlines():
                        if phrase in line:
                            self.assertRegex(line, r"\bnot\b|\bno\b|never|does not", line)

    # MV-D-7 (Low; documentation drift): the Binance Spot connector (a236996) is not mentioned in
    # VENUE_REGISTRY.md, BROKERS.md or the "Multi-venue contract" section of ARCHITECTURE.md; BROKERS.md still
    # recommends a read-only Ed25519 key although the connector signs with HMAC-SHA256 only; ARCHITECTURE.md
    # still calls the seeded records placeholders; the registry's binance_spot record lists no offline tests
    # (tests_passed is empty) while the ctrader record lists its offline tests.
    @unittest.expectedFailure
    def test_docs_describe_the_binance_connector_as_offline_only(self):
        combined = "\n".join((ROOT / name).read_text(encoding="utf-8")
                             for name in ("VENUE_REGISTRY.md", "BROKERS.md", "ARCHITECTURE.md"))
        self.assertIn("binance_spot.py", combined.replace("astra.venues.binance_spot", "binance_spot.py"))
        data = json.loads((SRC / "astra" / "venues" / "venue_registry.json").read_text(encoding="utf-8"))
        record = next(venue for venue in data["venues"] if venue["venue_id"] == "binance_spot")
        self.assertTrue(record["tests_passed"])

    def test_fixture_shapes_match_the_official_docs(self):
        # Checked against binance/binance-spot-api-docs rest-api.md and filters.md (read 2026-10-27):
        # 12-element kline rows (index 6 close time, 11 "Ignore"), bookTicker without a timestamp,
        # documented weights (ping 1, time 1, exchangeInfo 20, bookTicker 2, klines 2, account 20),
        # MIN_NOTIONAL uses applyToMarket while NOTIONAL uses applyMinToMarket/applyMaxToMarket.
        from binance_fixture import WEIGHTS, kline_row

        self.assertEqual(len(kline_row(0, 60_000, 1)), 12)
        self.assertEqual({path: WEIGHTS[path] for path in ("/api/v3/ping", "/api/v3/time", "/api/v3/exchangeInfo",
                                                           "/api/v3/ticker/bookTicker", "/api/v3/klines",
                                                           "/api/v3/account")},
                         {"/api/v3/ping": 1, "/api/v3/time": 1, "/api/v3/exchangeInfo": 20,
                          "/api/v3/ticker/bookTicker": 2, "/api/v3/klines": 2, "/api/v3/account": 20})
        self.assertEqual({bs.PING.weight, bs.TIME.weight, bs.EXCHANGE_INFO.weight, bs.BOOK_TICKER.weight,
                          bs.KLINES.weight, bs.ACCOUNT.weight}, {1, 20, 2})
        entry = symbol_entry("ETHBTC", "ETH", "BTC", notional=("MIN_NOTIONAL", "0.0001"))
        (min_notional,) = [f for f in entry["filters"] if f["filterType"] == "MIN_NOTIONAL"]
        self.assertIn("applyToMarket", min_notional)
        self.assertEqual(SpotSide.BUY.value, "BUY")
        self.assertIsInstance(BookTicker, type)


if __name__ == "__main__":
    unittest.main()
