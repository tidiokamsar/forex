"""Crypto futures data models (disabled).

Data models only. ``ENABLED`` is False: no backtest, risk or execution path
uses these classes yet, and nothing in ASTRA may size, simulate or send a
futures position from them. Derivatives need their own risk review (leverage,
funding, liquidation) before any of that changes; the venue registry keeps
futures venues BLOCKED until then.

``liquidation_price`` is a placeholder for a provider-reported value. ASTRA
never computes a liquidation price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .common import optional_decimal, require_aware, require_symbol, require_text, to_decimal

ENABLED = False


class ContractType(StrEnum):
    PERPETUAL = "PERPETUAL"
    DELIVERY = "DELIVERY"


@dataclass(frozen=True, slots=True)
class CryptoFuturesSpec:
    symbol: str
    base_asset: str
    quote_asset: str
    margin_asset: str
    contract_type: ContractType
    contract_size: Decimal
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    source: str
    as_of: datetime
    delivery_date: datetime | None = None

    def __post_init__(self) -> None:
        require_symbol(self.symbol)
        for name in ("base_asset", "quote_asset", "margin_asset"):
            require_symbol(getattr(self, name), name)
        if not isinstance(self.contract_type, ContractType):
            raise TypeError("contract_type must be a ContractType")
        for name in (
            "contract_size",
            "tick_size",
            "step_size",
            "min_qty",
            "max_qty",
            "initial_margin_rate",
            "maintenance_margin_rate",
        ):
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        for name in ("contract_size", "tick_size", "step_size", "max_qty"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_qty < 0 or self.min_qty > self.max_qty:
            raise ValueError("min_qty must be between 0 and max_qty")
        for name in ("initial_margin_rate", "maintenance_margin_rate"):
            if not Decimal(0) < getattr(self, name) <= Decimal(1):
                raise ValueError(f"{name} must be in (0, 1]")
        if self.maintenance_margin_rate > self.initial_margin_rate:
            raise ValueError("maintenance_margin_rate must not exceed initial_margin_rate")
        if self.contract_type is ContractType.DELIVERY:
            if self.delivery_date is None:
                raise ValueError("a DELIVERY contract needs delivery_date")
            require_aware(self.delivery_date, "delivery_date")
        elif self.delivery_date is not None:
            raise ValueError("a PERPETUAL contract has no delivery_date")
        require_text(self.source, "source")
        require_aware(self.as_of, "as_of")


@dataclass(frozen=True, slots=True)
class FuturesMarkState:
    """Provider-reported mark, index and funding values at ``as_of``."""

    symbol: str
    mark_price: Decimal
    index_price: Decimal | None
    funding_rate: Decimal | None
    next_funding_time: datetime | None
    source: str
    as_of: datetime
    # Placeholder only: a provider-reported value for an open position, never computed by ASTRA.
    liquidation_price: Decimal | None = None

    def __post_init__(self) -> None:
        require_symbol(self.symbol)
        mark = to_decimal(self.mark_price, "mark_price")
        if mark <= 0:
            raise ValueError("mark_price must be positive")
        object.__setattr__(self, "mark_price", mark)
        for name in ("index_price", "funding_rate", "liquidation_price"):
            object.__setattr__(self, name, optional_decimal(getattr(self, name), name))
        for name in ("index_price", "liquidation_price"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when given")
        if self.next_funding_time is not None:
            require_aware(self.next_funding_time, "next_funding_time")
        require_text(self.source, "source")
        require_aware(self.as_of, "as_of")
