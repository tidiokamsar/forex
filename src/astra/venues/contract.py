"""Common read-oriented contract for multi-venue connectors (contract version 1).

Scope of this lot: server time, public market data, quotes, historical bars,
instrument specifications and account reads. **Orders are not part of this
contract.** Order placement, cancellation and modification for any new venue
go only through the existing ``astra.brokers.base.BrokerAdapter`` permit
boundary (``OrderGateway`` -> single-use ``ExecutionPermit`` ->
``BrokerAdapter.submit_order``), later, after a reviewed change. A
``VenueCapabilities`` record therefore refuses to declare an order capability
as supported, and every connector here is read-only by construction.

Rules shared by every connector:
- A capability is either supported or listed in ``unsupported`` with an
  explicit reason. ``require`` raises ``CapabilityNotSupported`` with that
  reason; an unsupported operation is never emulated.
- An account environment comes from provider evidence
  (``EnvironmentSource.PROVIDER_API``); otherwise it is reported with
  ``EnvironmentSource.UNVERIFIED``. A configuration value is not evidence.
- Timestamps are timezone-aware; ``ensure_fresh`` refuses stale and
  future-dated data instead of using it.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from ..connector_types import CapabilityNotSupported, EnvironmentSource, Quote
from ..markets import MarketSpec
from ..markets.common import require_aware, require_iso_date, require_symbol, require_text, to_decimal
from ..models import MarketBar

CONTRACT_VERSION = "1"


class MarketType(StrEnum):
    FOREX_CFD = "FOREX_CFD"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    CRYPTO_FUTURES = "CRYPTO_FUTURES"
    FIXED_EXPIRY_OPTIONS = "FIXED_EXPIRY_OPTIONS"


class VenueEnvironment(StrEnum):
    PUBLIC_DATA = "PUBLIC_DATA"
    READ_ONLY = "READ_ONLY"
    DEMO = "DEMO"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class Capability(StrEnum):
    SERVER_TIME = "SERVER_TIME"
    PUBLIC_MARKET_DATA = "PUBLIC_MARKET_DATA"
    QUOTES = "QUOTES"
    HISTORICAL_BARS = "HISTORICAL_BARS"
    INSTRUMENT_SPECS = "INSTRUMENT_SPECS"
    ACCOUNT_READ = "ACCOUNT_READ"
    BALANCES = "BALANCES"
    POSITIONS = "POSITIONS"
    ORDER_HISTORY = "ORDER_HISTORY"
    ORDER_PLACEMENT = "ORDER_PLACEMENT"
    ORDER_CANCEL = "ORDER_CANCEL"
    ORDER_MODIFY = "ORDER_MODIFY"
    EVENT_STREAM = "EVENT_STREAM"
    RECONCILIATION = "RECONCILIATION"


# Order actions stay behind the BrokerAdapter permit boundary; this contract never supports them.
ORDER_CAPABILITIES = frozenset({Capability.ORDER_PLACEMENT, Capability.ORDER_CANCEL, Capability.ORDER_MODIFY})
ORDERS_OUTSIDE_CONTRACT_REASON = (
    "orders are outside the venue contract; only the BrokerAdapter permit boundary may send them, after review"
)
_TRUSTED_ENVIRONMENT_SOURCES = frozenset({EnvironmentSource.PROVIDER_API, EnvironmentSource.UNVERIFIED})


class StaleDataError(ValueError):
    """Data is older than allowed or dated in the future; it must not be used."""

    def __init__(self, reason: str, age_seconds: float) -> None:
        super().__init__(f"{reason}: age {age_seconds:.3f} s")
        self.reason = reason
        self.age_seconds = age_seconds


def ensure_fresh(timestamp: datetime, *, max_age_seconds: float, now: datetime | None = None) -> float:
    """Return the age in seconds, or raise ``StaleDataError``.

    There is no tolerance for future timestamps: a venue clock ahead of the
    local clock fails closed until the skew is understood.
    """

    require_aware(timestamp, "timestamp")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)):
        raise TypeError("max_age_seconds must be a number")
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be finite and positive")
    reference = datetime.now(UTC) if now is None else require_aware(now, "now")
    age = (reference - timestamp).total_seconds()
    if age < 0:
        raise StaleDataError("future_dated", age)
    if age > max_age_seconds:
        raise StaleDataError("stale", age)
    return age


@dataclass(frozen=True, slots=True)
class RateLimit:
    name: str
    limit: int
    interval_seconds: float
    weight_based: bool
    source: str

    def __post_init__(self) -> None:
        require_text(self.name, "name")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError("limit must be a positive integer")
        interval = self.interval_seconds
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("interval_seconds must be finite and positive")
        if not isinstance(self.weight_based, bool):
            raise TypeError("weight_based must be a bool")
        require_text(self.source, "source")


@dataclass(frozen=True, slots=True)
class VenueCapabilities:
    venue_id: str
    connector_name: str
    connector_version: str
    market_types: tuple[MarketType, ...]
    environments: tuple[VenueEnvironment, ...]
    supported: frozenset[Capability]
    # Stored as a read-only mapping; excluded from hashing because mappings are unhashable.
    unsupported: Mapping[Capability, str] = field(hash=False)
    rate_limits: tuple[RateLimit, ...]
    official_api_url: str
    verified_on: str | None
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.venue_id, str) or not self.venue_id or self.venue_id != self.venue_id.strip().lower():
            raise ValueError("venue_id must be a non-empty lower-case identifier")
        require_text(self.connector_name, "connector_name")
        require_text(self.connector_version, "connector_version")
        _typed_tuple(self.market_types, MarketType, "market_types")
        _typed_tuple(self.environments, VenueEnvironment, "environments")
        if not isinstance(self.supported, frozenset) or not all(isinstance(c, Capability) for c in self.supported):
            raise TypeError("supported must be a frozenset of Capability")
        if not isinstance(self.unsupported, Mapping):
            raise TypeError("unsupported must map Capability to a reason")
        reasons: dict[Capability, str] = {}
        for capability, reason in self.unsupported.items():
            if not isinstance(capability, Capability):
                raise TypeError("unsupported keys must be Capability values")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"unsupported capability {capability} needs an explicit reason")
            reasons[capability] = reason
        overlap = self.supported & reasons.keys()
        if overlap:
            raise ValueError(f"capabilities both supported and unsupported: {sorted(overlap)}")
        missing = set(Capability) - self.supported - reasons.keys()
        if missing:
            raise ValueError(f"every capability needs a decision; no reason recorded for {sorted(missing)}")
        ordering = self.supported & ORDER_CAPABILITIES
        if ordering:
            raise ValueError(f"order capabilities cannot be supported through the venue contract: {sorted(ordering)}")
        object.__setattr__(self, "unsupported", MappingProxyType(reasons))
        if not isinstance(self.rate_limits, tuple) or not all(isinstance(r, RateLimit) for r in self.rate_limits):
            raise TypeError("rate_limits must be a tuple of RateLimit")
        if not isinstance(self.official_api_url, str) or not self.official_api_url.startswith("https://"):
            raise ValueError("official_api_url must be an https URL")
        if self.verified_on is not None:
            require_iso_date(self.verified_on, "verified_on")
        if not isinstance(self.limitations, tuple) or not all(isinstance(x, str) and x.strip() for x in self.limitations):
            raise TypeError("limitations must be a tuple of non-empty strings")

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported


def _typed_tuple(value: object, kind: type, name: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{name} must be a non-empty tuple")
    if not all(isinstance(item, kind) for item in value):
        raise TypeError(f"{name} must contain only {kind.__name__} values")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not repeat values")


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    free: Decimal
    locked: Decimal
    as_of: datetime

    def __post_init__(self) -> None:
        require_symbol(self.asset, "asset")
        for name in ("free", "locked"):
            value = to_decimal(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} must not be negative")
            object.__setattr__(self, name, value)
        require_aware(self.as_of, "as_of")

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


def validate_environment_claim(
    environment: VenueEnvironment, source: EnvironmentSource
) -> tuple[VenueEnvironment, EnvironmentSource]:
    """Accept only provider evidence or an explicit UNVERIFIED claim."""

    if not isinstance(environment, VenueEnvironment):
        raise TypeError("environment must be a VenueEnvironment")
    if not isinstance(source, EnvironmentSource):
        raise TypeError("source must be an EnvironmentSource")
    if source not in _TRUSTED_ENVIRONMENT_SOURCES:
        raise ValueError(f"{source} is not evidence of an account environment; report UNVERIFIED instead")
    return environment, source


class VenueConnector(ABC):
    """Base of every venue connector. Read-only: it has no order or funds method."""

    @abstractmethod
    def capabilities(self) -> VenueCapabilities: ...

    @abstractmethod
    def server_time(self) -> datetime:
        """Venue clock (timezone-aware), from the provider."""

    @property
    def venue_id(self) -> str:
        return self.capabilities().venue_id

    def require(self, capability: Capability) -> None:
        """Raise ``CapabilityNotSupported`` with the recorded reason unless supported."""

        if not isinstance(capability, Capability):
            raise TypeError("capability must be a Capability")
        capabilities = self.capabilities()
        if capability in capabilities.supported:
            return
        reason = capabilities.unsupported.get(capability, "no decision recorded")
        error = CapabilityNotSupported(f"{capabilities.venue_id}: {capability.value} not supported: {reason}")
        error.capability = capability
        error.reason = reason
        raise error


class MarketDataConnector(VenueConnector):
    """Public and quote data. Implementations call ``self.require(...)`` first."""

    @abstractmethod
    def instrument_specs(self, symbols: tuple[str, ...] | None = None) -> dict[str, MarketSpec]: ...

    @abstractmethod
    def quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    def bars(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> tuple[MarketBar, ...]: ...


class AccountReadConnector(VenueConnector):
    """Account reads only; never orders, transfers or withdrawals."""

    @abstractmethod
    def balances(self) -> tuple[Balance, ...]: ...

    @abstractmethod
    def account_environment(self) -> tuple[VenueEnvironment, EnvironmentSource]:
        """Environment reported by the provider, else ``EnvironmentSource.UNVERIFIED``.

        Implementations return ``validate_environment_claim(environment, source)``.
        """
