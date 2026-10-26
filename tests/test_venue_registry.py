import copy
import json
import unittest
from dataclasses import replace
from importlib import resources

from _support import workspace_tempdir
from astra.venues import registry
from astra.venues.registry import (
    RegistryError,
    StatusGrant,
    VenueStatus,
    can_read_account,
    can_read_public_data,
    can_trade,
    compute_content_hash,
    decode_registry_text,
    load_registry,
    parse_registry,
    reseal_file,
)

# Registry 0.2.0 (evidence from the 2026-10-26 dossiers): the goforex placeholder became two domain records.
SEEDED = {
    "ctrader", "binance_spot", "binance_futures", "mt5", "iq_option", "interactive_brokers", "oanda", "fxcm",
    "ic_markets_global", "pepperstone", "fxpro", "deriv", "exness", "goforexfx_com", "goforextrading_com",
}


def raw_document():
    text = resources.files("astra.venues").joinpath("venue_registry.json").read_text(encoding="utf-8")
    return json.loads(text)


def resealed(document):
    document["content_sha256"] = compute_content_hash(document)
    return document


def with_statuses(record, *statuses):
    grants = tuple(StatusGrant(status, "2026-10-26", "test") for status in statuses)
    return replace(record, statuses=grants)


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_seeded_venues(self):
        self.assertEqual(set(self.registry.venue_ids), SEEDED)
        self.assertEqual(self.registry.schema_version, registry.SCHEMA_VERSION)
        self.assertRegex(self.registry.registry_version, r"^\d+\.\d+\.\d+$")

    def test_every_eligibility_is_unknown_and_nothing_can_read_or_trade(self):
        for record in self.registry.records:
            self.assertTrue(record.holds(VenueStatus.ELIGIBILITY_UNKNOWN), record.venue_id)
            self.assertTrue(record.holds(VenueStatus.DISCOVERED), record.venue_id)
            self.assertFalse(can_read_public_data(record), record.venue_id)
            self.assertFalse(can_read_account(record), record.venue_id)
            self.assertFalse(can_trade(record), record.venue_id)

    def test_binance_futures_blocked_pending_derivatives_review(self):
        record = self.registry.get("binance_futures")
        self.assertTrue(record.holds(VenueStatus.BLOCKED))
        note = next(g.note for g in record.statuses if g.status is VenueStatus.BLOCKED)
        self.assertIn("derivatives risk review", note)

    def test_ctrader_is_tested_offline_only(self):
        record = self.registry.get("ctrader")
        self.assertFalse(record.holds(VenueStatus.READ_ONLY_CONNECTED))
        self.assertIn("Adapter tested offline only; not READ_ONLY_CONNECTED.", record.limitations)
        self.assertTrue(any("test_ctrader.py" in test for test in record.tests_passed))
        self.assertTrue(any(v.startswith("READ_ONLY_CONNECTED") for v in record.validations_missing))

    def test_unknown_venue(self):
        with self.assertRaises(KeyError):
            self.registry.get("nope")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.record = load_registry().get("ctrader")

    def test_gates_need_cumulative_evidence(self):
        verified = with_statuses(self.record, VenueStatus.DISCOVERED, VenueStatus.OFFICIAL_API_VERIFIED, VenueStatus.REGION_ELIGIBILITY_VERIFIED)
        self.assertTrue(can_read_public_data(verified))
        self.assertFalse(can_read_account(verified))
        connected = with_statuses(
            self.record, VenueStatus.DISCOVERED, VenueStatus.OFFICIAL_API_VERIFIED, VenueStatus.REGION_ELIGIBILITY_VERIFIED, VenueStatus.READ_ONLY_CONNECTED
        )
        self.assertTrue(can_read_account(connected))
        self.assertFalse(can_trade(connected))
        only_api = with_statuses(self.record, VenueStatus.DISCOVERED, VenueStatus.OFFICIAL_API_VERIFIED)
        self.assertFalse(can_read_public_data(only_api))

    def test_no_venue_can_trade_even_with_every_positive_status(self):
        positive = [s for s in VenueStatus if s not in registry.NEGATIVE_STATUSES]
        self.assertFalse(can_trade(with_statuses(self.record, *positive)))
        self.assertIs(registry.TRADING_ENABLED_IN_THIS_LOT, False)

    def test_negative_status_closes_every_gate(self):
        positive = [s for s in VenueStatus if s not in registry.NEGATIVE_STATUSES]
        for negative in registry.NEGATIVE_STATUSES:
            record = with_statuses(self.record, *positive, negative)
            self.assertFalse(can_read_public_data(record), negative)
            self.assertFalse(can_read_account(record), negative)
            self.assertFalse(can_trade(record), negative)

    def test_non_record_is_refused(self):
        self.assertFalse(can_read_public_data({"statuses": []}))


class SchemaTests(unittest.TestCase):
    def assertRejected(self, document, pattern):
        with self.assertRaisesRegex(RegistryError, pattern):
            parse_registry(document)

    def test_stored_hash_matches_content(self):
        document = raw_document()
        self.assertEqual(document["content_sha256"], compute_content_hash(document))
        self.assertEqual(load_registry().content_sha256, document["content_sha256"])

    def test_hash_ignores_formatting_but_not_content(self):
        document = raw_document()
        self.assertEqual(compute_content_hash(json.loads(json.dumps(document, indent=None))), document["content_sha256"])
        edited = copy.deepcopy(document)
        edited["venues"][0]["limitations"].append("edited")
        self.assertNotEqual(compute_content_hash(edited), document["content_sha256"])

    def test_tampered_content_is_refused(self):
        document = raw_document()
        document["venues"][1]["statuses"] = [s for s in document["venues"][1]["statuses"] if s["status"] == "DISCOVERED"] + [
            {"status": "REGION_ELIGIBILITY_VERIFIED", "since": "2026-10-26", "note": "forged"}
        ]
        document["venues"][1]["eligibility_conditions"] = ["forged"]
        self.assertRejected(document, "content_sha256")

    def test_version_bump_changes_hash(self):
        document = raw_document()
        document["registry_version"] = "0.1.1"
        self.assertRejected(document, "content_sha256")
        self.assertEqual(parse_registry(resealed(document)).registry_version, "0.1.1")

    def test_strict_top_level(self):
        document = raw_document()
        document["extra"] = 1
        self.assertRejected(document, "unexpected keys")
        for key, value, pattern in (
            ("schema", "other", "schema"),
            ("schema_version", 2, "schema_version"),
            ("schema_version", True, "schema_version"),
            ("registry_version", "1.0", "registry_version"),
            ("updated_on", "2026-13-01", "updated_on"),
            ("content_sha256", "ABC", "content_sha256"),
            ("venues", [], "venues"),
        ):
            document = raw_document()
            document[key] = value
            self.assertRejected(resealed(document) if key != "content_sha256" else document, pattern)

    def test_strict_record_fields(self):
        cases = [
            (lambda r: r.pop("features"), "missing keys"),
            (lambda r: r.update(unknown_field=1), "unexpected keys"),
            (lambda r: r.update(venue_id="Bad-Id"), "snake_case"),
            (lambda r: r.update(market_types=["SPOT"]), "MarketType"),
            (lambda r: r.update(market_types=[]), "must not be empty"),
            (lambda r: r.update(environments=["SANDBOX"]), "VenueEnvironment"),
            (lambda r: r.update(api_url="http://insecure.example"), "https"),
            (lambda r: r.update(features="one"), "must be a list"),
            (lambda r: r.update(features=["a", "a"]), "repeat"),
            (lambda r: r.update(features=[" padded"]), "trimmed"),
            (lambda r: r.update(evidence=[{"claim": "x", "source_url": "https://a.example", "accessed_on": "yesterday"}]), "ISO date"),
            (lambda r: r.update(evidence=[{"claim": "x", "source_url": "https://a.example"}]), "missing keys"),
            (lambda r: r["statuses"].append({"status": "TRUSTED", "since": "2026-10-26", "note": "x"}), "VenueStatus"),
            (lambda r: r["statuses"].append(dict(r["statuses"][0])), "only once"),
            (lambda r: r.update(statuses=[]), "non-empty"),
        ]
        for mutate, pattern in cases:
            document = raw_document()
            mutate(document["venues"][0])
            self.assertRejected(resealed(document), pattern)

    def test_status_consistency_rules(self):
        def statuses(*names):
            return [{"status": name, "since": "2026-10-26", "note": "test"} for name in names]

        cases = [
            (statuses("ELIGIBILITY_UNKNOWN", "UNVERIFIED"), "DISCOVERED is required"),
            (statuses("DISCOVERED", "UNVERIFIED"), "eligibility must be explicit"),
            (statuses("DISCOVERED", "UNVERIFIED", "OFFICIAL_API_VERIFIED", "ELIGIBILITY_UNKNOWN"), "contradict"),
            (statuses("DISCOVERED", "ELIGIBILITY_UNKNOWN", "REGION_ELIGIBILITY_VERIFIED"), "contradict"),
            (statuses("DISCOVERED", "ELIGIBILITY_UNKNOWN", "READ_ONLY_CONNECTED"), "requires"),
            (statuses("DISCOVERED", "ELIGIBILITY_UNKNOWN", "DEMO_VALIDATED"), "requires"),
        ]
        for grants, pattern in cases:
            document = raw_document()
            document["venues"][0]["statuses"] = grants
            self.assertRejected(resealed(document), pattern)

    def test_verified_statuses_need_evidence(self):
        document = raw_document()
        record = next(item for item in document["venues"] if item["venue_id"] == "binance_spot")
        record["api_url"], record["evidence"] = None, []  # strip what OFFICIAL_API_VERIFIED requires
        record["statuses"] = [
            {"status": "DISCOVERED", "since": "2026-10-26", "note": "t"},
            {"status": "OFFICIAL_API_VERIFIED", "since": "2026-10-26", "note": "t"},
            {"status": "ELIGIBILITY_UNKNOWN", "since": "2026-10-26", "note": "t"},
        ]
        self.assertRejected(resealed(document), "requires api_url and evidence")
        document = raw_document()
        document["venues"][0]["eligibility_conditions"] = []
        document["venues"][0]["statuses"] = [
            {"status": "DISCOVERED", "since": "2026-10-26", "note": "t"},
            {"status": "UNVERIFIED", "since": "2026-10-26", "note": "t"},
            {"status": "REGION_ELIGIBILITY_VERIFIED", "since": "2026-10-26", "note": "t"},
        ]
        self.assertRejected(resealed(document), "requires eligibility_conditions")

    def test_duplicate_venue_ids(self):
        document = raw_document()
        document["venues"].append(copy.deepcopy(document["venues"][0]))
        self.assertRejected(resealed(document), "unique")

    def test_json_decoding_is_strict(self):
        with self.assertRaisesRegex(RegistryError, "duplicate key"):
            decode_registry_text('{"a": 1, "a": 2}')
        with self.assertRaisesRegex(RegistryError, "NaN"):
            decode_registry_text('{"a": NaN}')
        with self.assertRaisesRegex(RegistryError, "invalid JSON"):
            decode_registry_text("{")

    def test_reseal_file_round_trip(self):
        with workspace_tempdir() as folder:
            path = f"{folder}/registry.json"
            document = raw_document()
            document["registry_version"] = "9.9.9"  # any version other than the shipped one
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            with self.assertRaises(RegistryError):
                load_registry(path)
            digest = reseal_file(path)
            loaded = load_registry(path)
            self.assertEqual(loaded.content_sha256, digest)
            self.assertEqual(loaded.registry_version, "9.9.9")


if __name__ == "__main__":
    unittest.main()
