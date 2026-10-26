"""Value types shared by the broker adapters and the read-only venue contract.

They live outside ``astra.brokers`` so read-only code (``astra.venues.contract``,
``astra.markets``) can use them without loading the broker adapter layer
(MV-D-13). ``astra.brokers.base`` re-exports them unchanged, so they are the
same classes on both paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EnvironmentSource(StrEnum):
    """Where an account environment claim came from."""

    PROVIDER_API = "PROVIDER_API"
    SIMULATOR = "SIMULATOR"
    CONFIGURATION_ONLY = "CONFIGURATION_ONLY"
    UNVERIFIED = "UNVERIFIED"


class CapabilityNotSupported(Exception):
    """The adapter does not support the requested operation."""


@dataclass(frozen=True, slots=True)
class Quote:
    instrument: str
    bid: float
    ask: float
    timestamp: datetime
    source: str
