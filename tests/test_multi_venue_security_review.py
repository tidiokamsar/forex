"""Independent security review (Agent A) of the multi-venue lot at 5f9af94: Binance Spot read-only connector,
venue contract and venue registry.

ALL OFFLINE: the only server is tests/binance_fixture.py on 127.0.0.1, and calls aimed at real Binance hosts
go to a patched ``_BinanceHttp._send`` that records them and never opens a socket. No key, no order.

Every test asserts the SECURE behaviour. ``unittest.expectedFailure`` + "CONFIRMED DEFECT MV-A-n" marks a
defect reproduced on 5f9af94; undecorated tests are regression tests for protections that hold.
"""

import dataclasses
import json
import math
import pickle
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from astra.brokers.base import BrokerRequestError, EnvironmentSource
from astra.governance import SecretValue
from astra.venues import binance_spot as bs
from astra.venues import registry as venue_registry
from astra.venues.contract import Capability, MarketType, VenueCapabilities, VenueEnvironment
from binance_fixture import Scripted
from test_binance_spot import KEY, SECRET, ServerCase, bs_restrictions

S = venue_registry.VenueStatus


def _forged_account_record():
    """An in-memory binance_spot record claiming every read status, with no evidence at all."""

    shipped = venue_registry.load_registry().get("binance_spot")
    grants = tuple(
        venue_registry.StatusGrant(status, "2026-10-26", "forged in memory")
        for status in (S.DISCOVERED, S.OFFICIAL_API_VERIFIED, S.REGION_ELIGIBILITY_VERIFIED, S.READ_ONLY_CONNECTED)
    )
    return dataclasses.replace(shipped, statuses=grants, evidence=(), eligibility_conditions=())


class _RecordingSend:
    """Stands in for ``_BinanceHttp._send``: records the URL, answers like an empty provider, opens no socket."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, headers):  # a patched class attribute that is an instance is not bound
        self.urls.append(url)
        return 200, {}, b"{}"


class RegistryGateBypassTests(unittest.TestCase):
    # FIXED (was CONFIRMED DEFECT MV-A-1) (medium): the module claims "the only bypass is test_only_skip_registry_gate,
    # accepted only with a loopback base URL", but ``registry_record=`` accepts any in-memory VenueRecord. A
    # record built with dataclasses.replace (or the VenueRecord constructor) skips every rule of the strict
    # loader (READ_ONLY_CONNECTED with no evidence, no eligibility conditions, no content hash) and opens the
    # gate for the real production hosts. Patched _send: no request leaves the machine.
    def test_forged_in_memory_record_cannot_open_the_gate_for_a_real_host(self):
        send = _RecordingSend()
        with patch.object(bs._BinanceHttp, "_send", send):
            connector = bs.BinanceSpotPublicData(registry_record=_forged_account_record())  # data-api.binance.vision
            with self.assertRaises(bs.RegistryGateRefused):
                connector.ping()
        self.assertEqual(send.urls, [])

    def test_regression_the_strict_loader_refuses_the_same_forgery_in_a_file(self):
        document = json.loads((Path(venue_registry.__file__).with_name("venue_registry.json")).read_text(encoding="utf-8"))
        for venue in document["venues"]:
            if venue["venue_id"] == "binance_spot":
                venue["statuses"] = [{"status": s.value, "since": "2026-10-26", "note": "forged"}
                                     for s in (S.DISCOVERED, S.OFFICIAL_API_VERIFIED, S.REGION_ELIGIBILITY_VERIFIED,
                                               S.READ_ONLY_CONNECTED)]
        with self.assertRaises(venue_registry.RegistryError):  # hash mismatch (and evidence rules once resealed)
            venue_registry.parse_registry(document)
        document["content_sha256"] = venue_registry.compute_content_hash(document)
        shipped = venue_registry.load_registry().get("binance_spot")
        if not shipped.evidence or not shipped.eligibility_conditions:
            with self.assertRaises(venue_registry.RegistryError):
                venue_registry.parse_registry(document)

    def test_regression_shipped_registry_blocks_both_gates_and_trading(self):
        record = venue_registry.load_registry().get("binance_spot")
        self.assertFalse(venue_registry.can_read_public_data(record))
        self.assertFalse(venue_registry.can_read_account(record))
        self.assertFalse(venue_registry.can_trade(_forged_account_record()))

    def test_regression_test_only_flag_refused_for_real_hosts(self):
        for value in (True, 1, "yes"):
            with self.subTest(value=value), self.assertRaises(bs.BinanceConfigurationError):
                bs.BinanceSpotPublicData("https://data-api.binance.vision", test_only_skip_registry_gate=value)


class EnvironmentClaimTests(ServerCase):
    # FIXED (was CONFIRMED DEFECT MV-A-2) (medium): configuration alone yields LIVE + PROVIDER_API. With the test-only
    # gate bypass and loopback_role="production", a signed answer from the local fake server makes
    # account_environment() report PROVIDER_API, the one source the contract reserves for evidence from the
    # provider. A loopback host can never be evidence of a live Binance account.
    def test_loopback_fake_server_never_yields_provider_api(self):
        reader = self.reader()
        reader.balances()
        self.assertNotEqual(reader.account_environment()[1], EnvironmentSource.PROVIDER_API)

    def test_regression_testnet_and_demo_hosts_are_unverified_and_cannot_read_balances(self):
        for role, environment in (("testnet", VenueEnvironment.TESTNET), ("demo", VenueEnvironment.DEMO)):
            with self.subTest(role=role):
                reader = self.reader(loopback_role=role)
                self.assertEqual(reader.account_environment(), (environment, EnvironmentSource.UNVERIFIED))
                with self.assertRaises(bs.KeyNotReadOnly):
                    reader.balances()


class BanStateTests(ServerCase):
    # FIXED (was CONFIRMED DEFECT MV-A-3) (medium): the 418 ban, the 429 stop and the local weight budget live in each
    # _BinanceHttp instance. After an HTTP 418 (IP ban, escalating from 2 minutes to 3 days), constructing a
    # new connector, as a restarted service or a retrying caller does, sends the next request at once.
    def test_a_new_connector_respects_an_active_ip_ban(self):
        self.server.script("/api/v3/ping", Scripted(418, {"code": -1003, "msg": "banned"}, {"Retry-After": "300"}))
        with self.assertRaises(bs.BinanceIpBanned):
            self.public().ping()
        self.assertEqual(len(self.server.requests), 1)
        with self_suppress():
            self.public().ping()
        self.assertEqual(len(self.server.requests), 1)

    def test_regression_429_retries_are_bounded_and_then_stop(self):
        for _ in range(6):
            self.server.script("/api/v3/ping", Scripted(429, {"code": -1003, "msg": "slow"}, {"Retry-After": "1"}))
        connector = self.public(max_attempts=5)
        with self.assertRaises(BrokerRequestError):
            connector.ping()
        self.assertEqual(len(self.server.requests), 5)
        with self.assertRaises(BrokerRequestError):
            connector.ping()
        self.assertEqual(len(self.server.requests), 5)  # blocked locally, nothing sent
        with self.assertRaises(bs.BinanceConfigurationError):
            self.public(max_attempts=50)


def self_suppress():
    import contextlib

    return contextlib.suppress(bs.BinanceIpBanned, BrokerRequestError)


class ReadOnlyKeyTests(ServerCase):
    # FIXED (was CONFIRMED DEFECT MV-A-4) (low): reverify_after_seconds has no upper bound and no validation. With
    # math.inf (or NaN, or 1e12) the key's apiRestrictions are read once; a key later switched to trading and
    # withdrawals keeps serving signed reads forever.
    def test_key_permissions_are_rechecked_even_with_an_unbounded_interval(self):
        try:
            reader = self.reader(reverify_after_seconds=math.inf)
        except bs.BinanceConfigurationError:
            return  # refusing the interval is the secure outcome
        reader.balances()
        self.server.restrictions = {**bs_restrictions(), "enableSpotAndMarginTrading": True, "enableWithdrawals": True}
        self.clock.advance(86_400)
        with self.assertRaises(bs.KeyNotReadOnly):
            reader.balances()

    def test_regression_default_interval_rechecks_and_refuses_a_key_turned_trading(self):
        reader = self.reader()
        reader.balances()
        self.server.restrictions = {**bs_restrictions(), "enableSpotAndMarginTrading": True}
        self.clock.advance(301)
        with self.assertRaises(bs.KeyNotReadOnly):
            reader.balances()
        self.assertEqual(self.server.paths().count("/api/v3/account"), 1)

    def test_regression_type_confusion_and_unknown_flags_are_refused(self):
        for restrictions in ({**bs_restrictions(), "enableWithdrawals": 0}, {**bs_restrictions(), "enableReading": 1},
                             {**bs_restrictions(), "permitsSomethingNew": "true"}, {**bs_restrictions(), "enableX": None},
                             {"enableReading": True}):
            with self.subTest(restrictions=restrictions), self.assertRaises(bs.KeyNotReadOnly):
                bs.evaluate_api_restrictions(restrictions)


class SecretHandlingTests(ServerCase):
    # FIXED (was CONFIRMED DEFECT MV-A-5) (low): SecretValue (slots, no __reduce__) pickles and deep-copies with the
    # plaintext. The reader refuses pickling, but the SecretValue objects it holds, or the ones
    # from_environment builds, serialize the API secret in clear (multiprocessing, caches, crash dumps).
    def test_secret_value_cannot_be_pickled_in_clear(self):
        try:
            data = pickle.dumps(SecretValue(SECRET))
        except (TypeError, pickle.PicklingError):
            return
        self.assertNotIn(SECRET.encode(), data)

    def test_regression_reader_repr_pickle_and_provider_echo_hide_credentials(self):
        reader = self.reader()
        self.assertNotIn(KEY, repr(reader))
        with self.assertRaises(TypeError):
            pickle.dumps(reader)
        self.server.script("/api/v3/time", Scripted(400, {"code": -1022, "msg": f"bad {KEY} {SECRET}"}))
        with self.assertRaises(BrokerRequestError) as caught:
            reader.balances()
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

    def test_regression_signature_covers_the_exact_query_sent(self):
        import hashlib
        import hmac
        import urllib.parse

        reader = self.reader()
        reader.balances()
        path, query, headers = self.server.requests[-1]
        self.assertEqual(path, "/api/v3/account")
        self.assertEqual({name.lower(): value for name, value in headers.items()}.get("x-mbx-apikey"), KEY)
        unsigned = urllib.parse.urlencode([(k, v) for k, v in query.items() if k != "signature"])
        expected = hmac.new(SECRET.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(query["signature"], expected)
        self.assertEqual(list(query)[-3:], ["recvWindow", "timestamp", "signature"])


class WritePathTests(ServerCase):
    def test_regression_redirect_to_another_host_is_refused_not_followed(self):
        self.server.script("/api/v3/ping", Scripted(302, {}, {"Location": "https://evil.example/api/v3/order"}))
        with self.assertRaises(bs.BinanceConfigurationError):
            self.public().ping()
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.write_attempts, [])

    def test_regression_non_binance_and_disguised_hosts_are_refused(self):
        for url in ("https://evil.example", "https://api.binance.com@evil.example", "https://api.binance.com.evil.example",
                    "http://api.binance.com", "https://api.binance.com/api/v3/order", "https://api.binance.com?x=1",
                    "https://api.binance.com.", "https://evil.example#@api.binance.com"):
            with self.subTest(url=url), self.assertRaises(bs.BinanceConfigurationError):
                bs.BinanceSpotPublicData(url)

    def test_regression_unlisted_paths_and_order_capabilities_are_refused(self):
        connector = self.public()
        with self.assertRaises(bs.BinanceConfigurationError):
            connector._http.get_json(bs.Endpoint("/api/v3/order", 1, False, bs.SPOT_DOCS_URL))
        self.assertEqual(self.server.requests, [])
        for capability in (Capability.ORDER_PLACEMENT, Capability.ORDER_CANCEL, Capability.ORDER_MODIFY):
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                VenueCapabilities(
                    venue_id="x", connector_name="x", connector_version="0", market_types=(MarketType.CRYPTO_SPOT,),
                    environments=(VenueEnvironment.PUBLIC_DATA,),
                    supported=frozenset(Capability),
                    unsupported={}, rate_limits=(), official_api_url="https://example.org", verified_on=None,
                    limitations=("x",),
                )


# =============================================================================================
# Addendum (b98a759): markets/risk_policies.py and markets/costs.py, security angle only. MV-A-6+.
# =============================================================================================

from decimal import Decimal  # noqa: E402

from astra.markets import crypto_futures, fixed_expiry_options  # noqa: E402
from astra.markets import risk_policies as policies_module  # noqa: E402
from astra.markets.costs import SlippageAssumption  # noqa: E402
from astra.markets.risk_policies import (  # noqa: E402
    CryptoFuturesRiskPolicy,
    CryptoSpotRiskPolicy,
    FixedExpiryOptionsRiskPolicy,
    MarketRiskDecision,
    Verdict,
)
from astra.models import RunMode  # noqa: E402
from test_market_risk import buy, context  # noqa: E402


class MarketRiskDecisionTests(unittest.TestCase):
    # FIXED (was CONFIRMED DEFECT MV-A-6, low): MarketRiskDecision refuses ALLOW outside the modes allowed per
    # market type (crypto spot: SIMULATION only; derivatives: never; the Forex pre-check ALLOW stays advisory)
    # and an ALLOW without matching parameters and fingerprint. A decision is still data, not an authority token.
    def test_allow_decision_outside_simulation_cannot_be_constructed(self):
        for market_type, mode in ((MarketType.CRYPTO_SPOT, "LIVE"), (MarketType.CRYPTO_SPOT, "DEMO"),
                                  (MarketType.CRYPTO_FUTURES, "SIMULATION")):
            with self.subTest(market_type=market_type, mode=mode), self.assertRaises(ValueError):
                MarketRiskDecision(market_type, Verdict.ALLOW, (), "crypto_spot", "crypto-spot-1", mode)

    def test_regression_policy_never_allows_outside_simulation(self):
        policy = CryptoSpotRiskPolicy()
        self.assertTrue(policy.evaluate(buy(), context()).allowed)  # the same intent is allowed in SIMULATION
        for mode in (RunMode.DEMO, RunMode.LIVE, "SIMULATION", None):
            with self.subTest(mode=mode):
                self.assertFalse(policy.evaluate(buy(), context(mode=mode)).allowed)
                self.assertFalse(policy.evaluate_stop_change(None, Decimal(1), mode=mode).allowed)


class LooserPolicyTests(unittest.TestCase):
    # FIXED (was CONFIRMED DEFECT MV-A-7, low): (a) CryptoSpotRiskPolicy is final (__init_subclass__ raises
    # TypeError, so the subclass below cannot be defined); (b) ceilings are literals in
    # CryptoSpotRiskParameters, not patchable module globals; (c) slippage is floored at the reviewed 0.0005.
    # Every decision records the parameters and their sha256 fingerprint.
    def test_subclass_cannot_allow_live_under_the_reviewed_version(self):
        try:
            class Loose(CryptoSpotRiskPolicy):
                __slots__ = ()

                @staticmethod
                def _mode_reasons(mode):
                    return []
        except TypeError:
            return  # the policy is final

        decision = Loose().evaluate(buy(), context(mode=RunMode.LIVE))
        self.assertFalse(decision.allowed and decision.policy_version == "crypto-spot-1")

    def test_patched_ceiling_cannot_build_a_looser_policy_under_the_same_version(self):
        with patch.object(policies_module, "MAX_ORDER_NOTIONAL_FRACTION", Decimal("1")), \
                patch.object(policies_module, "MAX_DAILY_NOTIONAL_FRACTION", Decimal("1")):
            try:
                loose = CryptoSpotRiskPolicy(max_order_notional_fraction=Decimal("1"),
                                             max_daily_notional_fraction=Decimal("1"))
            except ValueError:
                return
        self.assertNotEqual(loose.version, CryptoSpotRiskPolicy().version)

    def test_zero_slippage_is_not_accepted_under_the_reviewed_version(self):
        with self.assertRaises(ValueError):
            CryptoSpotRiskPolicy(slippage=SlippageAssumption(Decimal(0)))

    def test_regression_replace_with_looser_values_is_refused(self):
        policy = CryptoSpotRiskPolicy()
        for changes in ({"max_order_notional_fraction": Decimal("0.5")}, {"max_quote_age_seconds": 60.0},
                        {"leverage": 3}, {"allow_short": True}, {"max_daily_notional_fraction": Decimal("Infinity")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                dataclasses.replace(policy, **changes)


class BuyCapTests(unittest.TestCase):
    # FIXED (was CONFIRMED DEFECT MV-A-8, low-medium): after a negative day P&L an unknown last_buy_notional
    # refuses (last_buy_notional_unknown); the ledger's last booked BUY also bounds the caller's value.
    def test_unknown_last_buy_after_a_loss_is_refused(self):
        decision = CryptoSpotRiskPolicy().evaluate(
            buy(quantity=Decimal("0.03")), context(day_realized_pnl=Decimal("-50"), last_buy_notional=None)
        )
        self.assertFalse(decision.allowed)

    # FIXED (was CONFIRMED DEFECT MV-A-9, low, design): the policy books every ALLOWED BUY in an append-only
    # SpotBuyLedger (UTC day, per venue and quote asset) and checks the cap atomically against it; the caller's
    # day_buy_notional is only an extra floor. Residual: position, last_buy_notional and expected_edge_fraction
    # remain caller-supplied, and the ledger is in memory (shared per policy instance).
    def test_order_splitting_cannot_exceed_the_daily_cap(self):
        policy = CryptoSpotRiskPolicy()
        allowed = [policy.evaluate(buy(intent_id=f"split-{i}", quantity=Decimal("0.038")), context()).allowed
                   for i in range(10)]
        self.assertLess(sum(allowed) * Decimal("0.038") * Decimal("50000"), Decimal("100000") * Decimal("0.10"))


class DerivativesAndImportsTests(unittest.TestCase):
    def test_regression_flipping_enabled_does_not_enable_derivatives(self):
        with patch.object(crypto_futures, "ENABLED", True), patch.object(fixed_expiry_options, "ENABLED", True):
            for policy in (CryptoFuturesRiskPolicy(), FixedExpiryOptionsRiskPolicy()):
                with self.subTest(policy=policy.name):
                    decision = policy.evaluate(mode=RunMode.SIMULATION)
                    self.assertFalse(decision.allowed)
                    self.assertIn("enabled_flag_set_without_reviewed_policy", decision.reasons)

    def test_regression_no_import_path_to_order_sending_code(self):
        import subprocess

        code = ("import sys, astra.markets.risk_policies, astra.markets.costs, astra.venues.binance_spot, "
                "astra.venues.registry, astra.venues.contract; "
                "print(','.join(sorted(m for m in sys.modules if m.startswith('astra'))))")
        src = str(Path(__file__).resolve().parents[1] / "src")
        loaded = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {src!r}); {code}"],
                                capture_output=True, text=True, timeout=120, check=True).stdout.strip().split(",")
        for module in loaded:
            self.assertFalse(module.startswith(("astra.execution", "astra.permits", "astra.service")), module)
            self.assertNotIn(module, {"astra.brokers.ctrader", "astra.brokers.simulated"})


# =============================================================================================
# Re-check of the fixes (8831d88). MV-A-10+.
# =============================================================================================

from astra.markets.risk_policies import policy_for  # noqa: E402


class SharedBanKeyTests(unittest.TestCase):
    def setUp(self):
        bs.reset_host_states_for_tests()
        self.addCleanup(bs.reset_host_states_for_tests)

    # FIXED (was CONFIRMED DEFECT MV-A-10) (medium): the process-wide stop state (418 ban, 429 stop, weight budget) is
    # keyed by the normalised base-URL string. "https://api.binance.com" and "https://api.binance.com:443" are
    # the same endpoint but get two states, so a ban hit through one is ignored through the other. Binance bans
    # and weighs per IP, so the documented alternates (api1..api4, api-gcp) should share it too. No network:
    # the connectors are only constructed and the shared state inspected.
    def test_equivalent_base_urls_share_one_ban_state(self):
        first = bs.BinanceSpotPublicData("https://api.binance.com")
        first._http._state.banned = True  # as after an HTTP 418 without Retry-After
        for url in ("https://api.binance.com:443", "https://api1.binance.com"):
            with self.subTest(url=url):
                self.assertTrue(bs.BinanceSpotPublicData(url).is_banned)


class SpotLedgerTests(unittest.TestCase):
    def setUp(self):  # the default ledger is process-wide: isolate each test
        from astra.markets.risk_policies import reset_default_spot_ledger_for_tests

        reset_default_spot_ledger_for_tests()
        self.addCleanup(reset_default_spot_ledger_for_tests)

    # FIXED (was CONFIRMED DEFECT MV-A-11) (low, documented residual made the default): the daily-cap ledger belongs to one
    # policy instance (default_factory), and policy_for(CRYPTO_SPOT) returns a new instance, hence a new empty
    # ledger, on every call. On the default path the 10% daily cap holds only per call site: ten 1.9% buys
    # through ten policy_for() instances are all allowed.
    def test_daily_cap_holds_across_default_policy_instances(self):
        allowed = [policy_for(MarketType.CRYPTO_SPOT).evaluate(buy(intent_id=f"p{i}", quantity=Decimal("0.038")),
                                                               context()).allowed for i in range(10)]
        self.assertLessEqual(sum(allowed) * Decimal("0.038") * Decimal("50000"), Decimal("100000") * Decimal("0.10"))

    # FIXED (was CONFIRMED DEFECT MV-A-12) (low, fail-closed): the BUY is booked in the ledger before the price-band check
    # runs, so a BUY refused for limit_price_outside_band still consumes the day's budget (and becomes the
    # "last BUY" for the anti-martingale bound). Ledger state then disagrees with the decisions.
    def test_refused_buy_is_not_booked(self):
        policy = CryptoSpotRiskPolicy()
        decision = policy.evaluate(buy(quantity=Decimal("0.019"), limit_price=Decimal("51000.00")), context())
        self.assertFalse(decision.allowed)
        self.assertIn("limit_price_outside_band", decision.reasons)
        self.assertEqual(policy.ledger.records, ())


class PersistedStateMigrationTests(unittest.TestCase):
    def setUp(self):
        bs.reset_host_states_for_tests()
        self.addCleanup(bs.reset_host_states_for_tests)

    # (was) CONFIRMED DEFECT MV-A-13 (low, fail-open on upgrade): the persisted stop-state file is now read under the new
    # scope key ("binance:production"). A file written before this change keys the ban by base URL
    # ("https://api.binance.com"); attach() looks up only the new key and silently ignores the old entry, so an
    # active, open-ended 418 ban recorded before the upgrade is forgotten. Entries it cannot map should refuse
    # (as an unreadable file already does) or be migrated. No network: the connector is only constructed.
    # FIXED (was CONFIRMED DEFECT MV-A-13): legacy base-URL keys are mapped to their scope; unknown keys refuse.
    def test_ban_persisted_under_the_previous_key_format_is_still_honoured(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "binance_state.json"
            path.write_text(json.dumps({
                "schema": bs.STATE_SCHEMA, "schema_version": bs.STATE_SCHEMA_VERSION,
                "hosts": {"https://api.binance.com": {"banned": True, "banned_until": None, "blocked_until": None}},
            }), encoding="utf-8")
            try:
                connector = bs.BinanceSpotPublicData("https://api.binance.com", state_path=path)
            except bs.BinanceConfigurationError:
                return  # refusing an unmapped entry is the secure outcome
            self.assertTrue(connector.is_banned)

    # FIXED (was CONFIRMED DEFECT MV-A-14) (low, fail-closed lock-out): save() rewrites only the scope key and leaves the legacy
    # base-URL entry in the file. After a human acknowledge_ban_cleared(), the new key says "not banned" but the
    # legacy entry still says banned (open-ended); on the next restart attach() ORs them and the ban is back, so
    # an acknowledgement can never stick until someone edits the file by hand. No network.
    def test_acknowledged_ban_stays_cleared_after_restart_with_a_legacy_entry(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "binance_state.json"
            path.write_text(json.dumps({
                "schema": bs.STATE_SCHEMA, "schema_version": bs.STATE_SCHEMA_VERSION,
                "hosts": {"https://api.binance.com": {"banned": True, "banned_until": None, "blocked_until": None}},
            }), encoding="utf-8")
            connector = bs.BinanceSpotPublicData("https://api.binance.com", state_path=path)
            self.assertTrue(connector.is_banned)
            connector.acknowledge_ban_cleared()
            self.assertFalse(connector.is_banned)
            bs.reset_host_states_for_tests()  # simulates a restarted process
            restarted = bs.BinanceSpotPublicData("https://api.binance.com", state_path=path)
            self.assertFalse(restarted.is_banned)


if __name__ == "__main__":
    unittest.main()
