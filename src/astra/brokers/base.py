"""Uniform broker contract (interface version 1).

Adapters declare what they actually support through ``Capabilities``. A
missing capability raises ``CapabilityNotSupported``; it is never emulated as
if the provider supported it. The contract deliberately exposes no funds
withdrawal, transfer, or beneficiary operation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ..instruments import InstrumentSpec
from ..models import Direction


INTERFACE_VERSION = "1"


class BrokerEnvironment(StrEnum):
    SIMULATION = "SIMULATION"
    DEMO = "DEMO"
    LIVE = "LIVE"
    UNKNOWN = "UNKNOWN"


class EnvironmentSource(StrEnum):
    """Where an account environment claim came from."""

    PROVIDER_API = "PROVIDER_API"
    SIMULATOR = "SIMULATOR"
    CONFIGURATION_ONLY = "CONFIGURATION_ONLY"
    UNVERIFIED = "UNVERIFIED"


class ConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DEGRADED = "DEGRADED"


class BrokerOrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class BrokerTimeout(Exception):
    """The request outcome is unknown; callers must look it up before retrying."""


class BrokerUnavailable(Exception):
    """The adapter cannot reach the provider."""


class CapabilityNotSupported(Exception):
    """The adapter does not support the requested operation."""


@dataclass(frozen=True, slots=True)
class Capabilities:
    adapter_name: str
    adapter_version: str
    declared_environment: BrokerEnvironment
    environment_source: EnvironmentSource
    instruments: tuple[str, ...]
    order_types: tuple[str, ...]
    supports_order_lookup_by_client_id: bool
    supports_cancel: bool
    supports_modify: bool
    supports_positions: bool
    supports_event_stream: bool
    supports_quotes: bool
    supports_historical_data: bool
    interface_version: str = INTERFACE_VERSION
    official_documentation_url: str | None = None
    verified_on: str | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_ref_masked: str
    environment_reported: BrokerEnvironment
    environment_source: EnvironmentSource
    currency: str
    balance: float
    equity: float
    margin_used: float
    margin_free: float
    as_of: datetime


@dataclass(frozen=True, slots=True)
class Quote:
    instrument: str
    bid: float
    ask: float
    timestamp: datetime
    source: str


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    client_order_id: str
    instrument: str
    direction: Direction
    units: float
    order_type: str = "MARKET"
    stop_loss: float | None = None
    reduce_only: bool = False


@dataclass(frozen=True, slots=True)
class BrokerOrderReport:
    client_order_id: str
    broker_order_id: str | None
    status: BrokerOrderStatus
    requested_units: float
    filled_units: float
    average_fill_price: float | None
    sequence: int
    timestamp: datetime
    message: str = ""


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    instrument: str
    net_units: float
    average_price: float


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    sequence: int
    report: BrokerOrderReport
    received_at: datetime | None = None
    extra: dict = field(default_factory=dict)


class BrokerAdapter(ABC):
    """Interface every connector implements; strategies never see it."""

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def connection_state(self) -> ConnectionState: ...

    @abstractmethod
    def account_snapshot(self) -> AccountSnapshot: ...

    @abstractmethod
    def quote(self, instrument: str) -> Quote: ...

    @abstractmethod
    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderReport: ...

    @abstractmethod
    def lookup_order(self, client_order_id: str) -> BrokerOrderReport | None: ...

    @abstractmethod
    def open_positions(self) -> tuple[BrokerPosition, ...]: ...

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> BrokerOrderReport: ...

    @abstractmethod
    def poll_events(self, after_sequence: int) -> tuple[BrokerEvent, ...]: ...

    def instrument_specs(self) -> dict[str, "InstrumentSpec"]:
        """Provider-reported contract specs; empty when the adapter has none."""

        return {}
