"""Versioned venue registry: evidence about venues, not permission to trade.

A venue's statuses are cumulative evidence, each held since a date, not one
label. The gates below read that evidence and fail closed:

- ``can_read_public_data``: OFFICIAL_API_VERIFIED and REGION_ELIGIBILITY_VERIFIED;
- ``can_read_account``: the above plus READ_ONLY_CONNECTED (the first
  supervised read-only check is a human-run operation, like ``ctrader-check``,
  that records READ_ONLY_CONNECTED when it passes);
- ``can_trade``: False for every venue in this lot. Orders go only through
  the existing ``BrokerAdapter`` permit boundary after a reviewed change.

BLOCKED, UNVERIFIED and ELIGIBILITY_UNKNOWN each make every gate False.

The data file ``venue_registry.json`` carries a registry version and a
SHA-256 of its canonical content; the loader validates the schema strictly and
refuses a hash mismatch. After editing the file, bump ``registry_version`` and
run ``python -m astra.venues.registry rehash`` to reseal it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any

from ..markets.common import require_iso_date
from .contract import MarketType, VenueEnvironment

SCHEMA_NAME = "astra.venue_registry"
SCHEMA_VERSION = 1
REGISTRY_FILE = "venue_registry.json"
# No venue may trade in this lot, whatever its evidence. Changing this is a reviewed change.
TRADING_ENABLED_IN_THIS_LOT = False
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_VENUE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RegistryError(ValueError):
    """The registry document is malformed, inconsistent or its hash does not match."""


class VenueStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    OFFICIAL_API_VERIFIED = "OFFICIAL_API_VERIFIED"
    REGION_ELIGIBILITY_VERIFIED = "REGION_ELIGIBILITY_VERIFIED"
    READ_ONLY_CONNECTED = "READ_ONLY_CONNECTED"
    PAPER_TESTED = "PAPER_TESTED"
    DEMO_VALIDATED = "DEMO_VALIDATED"
    LIVE_REVIEW_REQUIRED = "LIVE_REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"
    ELIGIBILITY_UNKNOWN = "ELIGIBILITY_UNKNOWN"


NEGATIVE_STATUSES = frozenset({VenueStatus.BLOCKED, VenueStatus.UNVERIFIED, VenueStatus.ELIGIBILITY_UNKNOWN})
PUBLIC_DATA_REQUIRES = frozenset({VenueStatus.OFFICIAL_API_VERIFIED, VenueStatus.REGION_ELIGIBILITY_VERIFIED})
ACCOUNT_READ_REQUIRES = PUBLIC_DATA_REQUIRES | {VenueStatus.READ_ONLY_CONNECTED}
TRADE_REQUIRES = ACCOUNT_READ_REQUIRES | {VenueStatus.DEMO_VALIDATED}
# A status may only be held together with its prerequisites.
_PREREQUISITES: dict[VenueStatus, frozenset[VenueStatus]] = {
    VenueStatus.READ_ONLY_CONNECTED: PUBLIC_DATA_REQUIRES,
    VenueStatus.PAPER_TESTED: frozenset({VenueStatus.READ_ONLY_CONNECTED}),
    VenueStatus.DEMO_VALIDATED: frozenset({VenueStatus.READ_ONLY_CONNECTED}),
    VenueStatus.LIVE_REVIEW_REQUIRED: frozenset({VenueStatus.DEMO_VALIDATED}),
}
_EXCLUSIVE = (
    (VenueStatus.UNVERIFIED, VenueStatus.OFFICIAL_API_VERIFIED),
    (VenueStatus.ELIGIBILITY_UNKNOWN, VenueStatus.REGION_ELIGIBILITY_VERIFIED),
)


@dataclass(frozen=True, slots=True)
class Evidence:
    claim: str
    source_url: str
    accessed_on: str


@dataclass(frozen=True, slots=True)
class StatusGrant:
    status: VenueStatus
    since: str
    note: str


@dataclass(frozen=True, slots=True)
class VenueRecord:
    venue_id: str
    display_name: str
    legal_entity: str | None
    jurisdiction: str | None
    market_types: tuple[MarketType, ...]
    instruments: tuple[str, ...]
    api_name: str | None
    api_version: str | None
    api_url: str | None
    environments: tuple[VenueEnvironment, ...]
    permissions_required: tuple[str, ...]
    features: tuple[str, ...]
    size_rules: tuple[str, ...]
    costs_known: tuple[str, ...]
    limitations: tuple[str, ...]
    eligibility_conditions: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    statuses: tuple[StatusGrant, ...]
    tests_passed: tuple[str, ...]
    validations_missing: tuple[str, ...]

    @property
    def held_statuses(self) -> frozenset[VenueStatus]:
        return frozenset(grant.status for grant in self.statuses)

    def holds(self, status: VenueStatus) -> bool:
        return status in self.held_statuses


def _gate(record: VenueRecord, required: frozenset[VenueStatus]) -> bool:
    if not isinstance(record, VenueRecord):
        return False
    held = record.held_statuses
    return not (held & NEGATIVE_STATUSES) and required <= held


def can_read_public_data(record: VenueRecord) -> bool:
    return _gate(record, PUBLIC_DATA_REQUIRES)


def can_read_account(record: VenueRecord) -> bool:
    return _gate(record, ACCOUNT_READ_REQUIRES)


# The only venues that may receive DEMO orders, through the BrokerAdapter permit boundary and DEMO permits.
DEMO_TRADING_VENUES = frozenset({"binance_spot_demo"})


def can_trade_demo(record: VenueRecord) -> bool:
    """A controlled DEMO order (a human-requested test): account read verified, DEMO the only environment."""

    return (isinstance(record, VenueRecord) and record.venue_id in DEMO_TRADING_VENUES
            and record.environments == (VenueEnvironment.DEMO,) and _gate(record, ACCOUNT_READ_REQUIRES))


def can_trade_demo_auto(record: VenueRecord) -> bool:
    """Automatic DEMO orders from a strategy: additionally a validated controlled test (DEMO_VALIDATED)."""

    return can_trade_demo(record) and record.holds(VenueStatus.DEMO_VALIDATED)


def can_trade(record: VenueRecord) -> bool:
    if not TRADING_ENABLED_IN_THIS_LOT:
        return False
    return _gate(record, TRADE_REQUIRES)  # pragma: no cover - unreachable in this lot


@dataclass(frozen=True, slots=True)
class VenueRegistry:
    schema_version: int
    registry_version: str
    updated_on: str
    content_sha256: str
    records: tuple[VenueRecord, ...]

    @property
    def venue_ids(self) -> tuple[str, ...]:
        return tuple(record.venue_id for record in self.records)

    def get(self, venue_id: str) -> VenueRecord:
        for record in self.records:
            if record.venue_id == venue_id:
                return record
        raise KeyError(venue_id)


# -- schema ---------------------------------------------------------------

_TOP_KEYS = {"schema", "schema_version", "registry_version", "updated_on", "content_sha256", "venues"}
_STR_OR_NULL = ("legal_entity", "jurisdiction", "api_name", "api_version", "api_url")
_STR_LISTS = (
    "instruments",
    "permissions_required",
    "features",
    "size_rules",
    "costs_known",
    "limitations",
    "eligibility_conditions",
    "tests_passed",
    "validations_missing",
)
_RECORD_KEYS = {
    "venue_id",
    "display_name",
    "market_types",
    "environments",
    "evidence",
    "statuses",
    *_STR_OR_NULL,
    *_STR_LISTS,
}


def canonical_content(document: dict[str, Any]) -> bytes:
    """Canonical bytes of everything except ``content_sha256``."""

    content = {key: value for key, value in document.items() if key != "content_sha256"}
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def compute_content_hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_content(document)).hexdigest()


def _fail(where: str, message: str) -> RegistryError:
    return RegistryError(f"{where}: {message}")


def _exact_keys(value: object, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, "must be an object")
    if set(value) != keys:
        extra = sorted(set(value) - keys)
        missing = sorted(keys - set(value))
        raise _fail(where, f"unexpected keys {extra}, missing keys {missing}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _fail(where, "must be a non-empty trimmed string")
    return value


def _date(value: object, where: str) -> str:
    try:
        return require_iso_date(value, where)
    except ValueError as exc:
        raise _fail(where, "must be an ISO date YYYY-MM-DD") from exc


def _url(value: object, where: str) -> str:
    text = _text(value, where)
    if not text.startswith("https://") or any(ch.isspace() for ch in text):
        raise _fail(where, "must be an https URL")
    return text


def _text_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(where, "must be a list")
    items = tuple(_text(item, f"{where}[{index}]") for index, item in enumerate(value))
    if len(set(items)) != len(items):
        raise _fail(where, "must not repeat entries")
    return items


def _enum_list(value: object, kind: type[StrEnum], where: str) -> tuple:
    names = _text_list(value, where)
    try:
        return tuple(kind(name) for name in names)
    except ValueError as exc:
        raise _fail(where, f"unknown {kind.__name__} value") from exc


def _parse_record(raw: object, where: str) -> VenueRecord:
    data = _exact_keys(raw, _RECORD_KEYS, where)
    venue_id = _text(data["venue_id"], f"{where}.venue_id")
    if not _VENUE_ID_PATTERN.match(venue_id):
        raise _fail(f"{where}.venue_id", "must be lower-case snake_case")
    optional: dict[str, str | None] = {}
    for key in _STR_OR_NULL:
        value = data[key]
        optional[key] = None if value is None else _text(value, f"{where}.{key}")
    if optional["api_url"] is not None:
        _url(optional["api_url"], f"{where}.api_url")
    lists = {key: _text_list(data[key], f"{where}.{key}") for key in _STR_LISTS}
    market_types = _enum_list(data["market_types"], MarketType, f"{where}.market_types")
    if not market_types:
        raise _fail(f"{where}.market_types", "must not be empty")
    environments = _enum_list(data["environments"], VenueEnvironment, f"{where}.environments")

    if not isinstance(data["evidence"], list):
        raise _fail(f"{where}.evidence", "must be a list")
    evidence = []
    for index, item in enumerate(data["evidence"]):
        spot = f"{where}.evidence[{index}]"
        entry = _exact_keys(item, {"claim", "source_url", "accessed_on"}, spot)
        evidence.append(
            Evidence(
                claim=_text(entry["claim"], f"{spot}.claim"),
                source_url=_url(entry["source_url"], f"{spot}.source_url"),
                accessed_on=_date(entry["accessed_on"], f"{spot}.accessed_on"),
            )
        )

    if not isinstance(data["statuses"], list) or not data["statuses"]:
        raise _fail(f"{where}.statuses", "must be a non-empty list")
    grants = []
    for index, item in enumerate(data["statuses"]):
        spot = f"{where}.statuses[{index}]"
        entry = _exact_keys(item, {"status", "since", "note"}, spot)
        try:
            status = VenueStatus(_text(entry["status"], f"{spot}.status"))
        except ValueError as exc:
            raise _fail(f"{spot}.status", "unknown VenueStatus") from exc
        grants.append(StatusGrant(status, _date(entry["since"], f"{spot}.since"), _text(entry["note"], f"{spot}.note")))
    held = [grant.status for grant in grants]
    if len(set(held)) != len(held):
        raise _fail(f"{where}.statuses", "a status may be held only once")
    held_set = frozenset(held)
    if VenueStatus.DISCOVERED not in held_set:
        raise _fail(f"{where}.statuses", "DISCOVERED is required")
    if not held_set & {VenueStatus.ELIGIBILITY_UNKNOWN, VenueStatus.REGION_ELIGIBILITY_VERIFIED}:
        raise _fail(f"{where}.statuses", "eligibility must be explicit: ELIGIBILITY_UNKNOWN or REGION_ELIGIBILITY_VERIFIED")
    for first, second in _EXCLUSIVE:
        if first in held_set and second in held_set:
            raise _fail(f"{where}.statuses", f"{first} and {second} contradict each other")
    for status, needs in _PREREQUISITES.items():
        if status in held_set and not needs <= held_set:
            raise _fail(f"{where}.statuses", f"{status} requires {sorted(needs)}")
    if VenueStatus.OFFICIAL_API_VERIFIED in held_set and (not evidence or optional["api_url"] is None):
        raise _fail(f"{where}.statuses", "OFFICIAL_API_VERIFIED requires api_url and evidence")
    if VenueStatus.REGION_ELIGIBILITY_VERIFIED in held_set and not lists["eligibility_conditions"]:
        raise _fail(f"{where}.statuses", "REGION_ELIGIBILITY_VERIFIED requires eligibility_conditions")

    return VenueRecord(
        venue_id=venue_id,
        display_name=_text(data["display_name"], f"{where}.display_name"),
        legal_entity=optional["legal_entity"],
        jurisdiction=optional["jurisdiction"],
        market_types=market_types,
        instruments=lists["instruments"],
        api_name=optional["api_name"],
        api_version=optional["api_version"],
        api_url=optional["api_url"],
        environments=environments,
        permissions_required=lists["permissions_required"],
        features=lists["features"],
        size_rules=lists["size_rules"],
        costs_known=lists["costs_known"],
        limitations=lists["limitations"],
        eligibility_conditions=lists["eligibility_conditions"],
        evidence=tuple(evidence),
        statuses=tuple(grants),
        tests_passed=lists["tests_passed"],
        validations_missing=lists["validations_missing"],
    )


def parse_registry(document: object) -> VenueRegistry:
    """Validate a decoded registry document strictly, including its content hash."""

    data = _exact_keys(document, _TOP_KEYS, "registry")
    if data["schema"] != SCHEMA_NAME:
        raise _fail("registry.schema", f"must be {SCHEMA_NAME!r}")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise _fail("registry.schema_version", f"must be {SCHEMA_VERSION}")
    version = data["registry_version"]
    if not isinstance(version, str) or not _VERSION_PATTERN.match(version):
        raise _fail("registry.registry_version", "must be MAJOR.MINOR.PATCH")
    updated_on = _date(data["updated_on"], "registry.updated_on")
    stored_hash = data["content_sha256"]
    if not isinstance(stored_hash, str) or not _SHA256_PATTERN.match(stored_hash):
        raise _fail("registry.content_sha256", "must be 64 lower-case hex characters")
    if not isinstance(data["venues"], list) or not data["venues"]:
        raise _fail("registry.venues", "must be a non-empty list")
    records = tuple(_parse_record(raw, f"venues[{index}]") for index, raw in enumerate(data["venues"]))
    ids = [record.venue_id for record in records]
    if len(set(ids)) != len(ids):
        raise _fail("registry.venues", "venue_id values must be unique")
    actual = compute_content_hash(data)
    if actual != stored_hash:
        raise _fail("registry.content_sha256", f"does not match the content (expected {actual})")
    return VenueRegistry(SCHEMA_VERSION, version, updated_on, stored_hash, records)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> None:
    raise RegistryError(f"non-standard JSON constant {name}")


def decode_registry_text(text: str) -> dict[str, Any]:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise RegistryError(f"invalid JSON: {exc}") from exc


def _default_text() -> str:
    return resources.files(__package__).joinpath(REGISTRY_FILE).read_text(encoding="utf-8")


def load_registry(path: str | Path | None = None) -> VenueRegistry:
    """Load and validate the bundled registry, or the file at ``path``."""

    text = _default_text() if path is None else Path(path).read_text(encoding="utf-8")
    return parse_registry(decode_registry_text(text))


def reseal_file(path: str | Path) -> str:
    """Recompute ``content_sha256`` of an edited registry file, validate it, and write it back."""

    target = Path(path)
    document = decode_registry_text(target.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RegistryError("registry: must be an object")
    document["content_sha256"] = compute_content_hash(document)
    parse_registry(document)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return document["content_sha256"]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    default_path = Path(__file__).with_name(REGISTRY_FILE)
    if args[:1] == ["check"] and len(args) <= 2:
        registry = load_registry(args[1] if len(args) == 2 else default_path)
        print(f"ok {registry.registry_version} {registry.content_sha256} venues={len(registry.records)}")
        return 0
    if args[:1] == ["rehash"] and len(args) <= 2:
        print(reseal_file(args[1] if len(args) == 2 else default_path))
        return 0
    print("usage: python -m astra.venues.registry check|rehash [PATH]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
