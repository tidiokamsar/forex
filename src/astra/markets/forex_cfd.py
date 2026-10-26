"""Forex CFD contract data: the existing ``InstrumentSpec`` plus CFD terms.

``ForexCfdSpec`` wraps, and never replaces, ``astra.instruments.InstrumentSpec``.
The sizing, margin and pre-send checks of the order path keep reading the
wrapped ``InstrumentSpec`` unchanged (``ForexCfdSpec.instrument``).

Values are ILLUSTRATIVE unless ``source`` starts with ``PROVIDER_API:`` and the
wrapped instrument spec is itself provider-sourced. Swap and commission values
are informational: no backtest, risk or execution path reads them yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..instruments import InstrumentSpec
from .common import ILLUSTRATIVE_SOURCE, is_provider_sourced, optional_decimal, require_aware, require_text, to_decimal


@dataclass(frozen=True, slots=True)
class ForexCfdSpec:
    instrument: InstrumentSpec
    contract_size: Decimal
    margin_currency: str
    swap_long: Decimal | None = None
    swap_short: Decimal | None = None
    swap_unit: str | None = None
    commission_per_lot: Decimal | None = None
    commission_currency: str | None = None
    source: str = ILLUSTRATIVE_SOURCE
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentSpec):
            raise TypeError("instrument must be an astra.instruments.InstrumentSpec")
        contract_size = to_decimal(self.contract_size, "contract_size")
        if contract_size <= 0:
            raise ValueError("contract_size must be positive")
        object.__setattr__(self, "contract_size", contract_size)
        if not isinstance(self.margin_currency, str) or len(self.margin_currency) != 3 or not self.margin_currency.isupper():
            raise ValueError("margin_currency must be a three-letter upper-case code")
        for name in ("swap_long", "swap_short"):
            object.__setattr__(self, name, optional_decimal(getattr(self, name), name))
        if (self.swap_long is not None or self.swap_short is not None) and self.swap_unit is None:
            raise ValueError("swap_unit is required when a swap value is given")
        commission = optional_decimal(self.commission_per_lot, "commission_per_lot")
        if commission is not None and commission < 0:
            raise ValueError("commission_per_lot must not be negative")
        object.__setattr__(self, "commission_per_lot", commission)
        if commission is not None and (not isinstance(self.commission_currency, str) or len(self.commission_currency) != 3):
            raise ValueError("commission_currency is required with commission_per_lot")
        require_text(self.source, "source")
        if is_provider_sourced(self.source) and self.as_of is None:
            raise ValueError("a provider-sourced spec must carry as_of")
        if self.as_of is not None:
            require_aware(self.as_of, "as_of")

    @property
    def symbol(self) -> str:
        return self.instrument.symbol

    @property
    def illustrative(self) -> bool:
        """True unless both the CFD terms and the wrapped spec came from a provider."""

        return not (is_provider_sourced(self.source) and is_provider_sourced(self.instrument.source))
