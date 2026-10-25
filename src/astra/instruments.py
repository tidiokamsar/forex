"""Instrument contract specifications used for sizing and pre-send validation.

The bundled values are illustrative simulation parameters. They are not
broker-reported contract facts; a broker adapter must supply its own verified
specification before any non-simulated use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ILLUSTRATIVE_SOURCE = "ILLUSTRATIVE_SIMULATION"


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: str
    base_currency: str
    quote_currency: str
    pip_size: float
    price_precision: int
    min_units: float
    unit_step: float
    max_units: float
    margin_rate: float
    source: str = ILLUSTRATIVE_SOURCE

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.strip().upper():
            raise ValueError("symbol must be a canonical upper-case string")
        if len(self.base_currency) != 3 or len(self.quote_currency) != 3:
            raise ValueError("currencies must be ISO-style three-letter codes")
        for name in ("pip_size", "min_units", "unit_step", "max_units", "margin_rate"):
            value = getattr(self, name)
            if not _finite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.min_units > self.max_units:
            raise ValueError("min_units must not exceed max_units")
        if self.margin_rate > 1:
            raise ValueError("margin_rate must be at most 1")

    def normalize_units(self, units: float) -> float:
        """Round a size down to the contract step; never round risk upward."""

        if not _finite(units) or units <= 0:
            return 0.0
        steps = math.floor(units / self.unit_step + 1e-9)
        return round(steps * self.unit_step, 8)

    def size_violations(self, units: float) -> tuple[str, ...]:
        if not _finite(units) or units <= 0:
            return ("invalid_size",)
        reasons: list[str] = []
        if units < self.min_units:
            reasons.append("size_below_minimum")
        if units > self.max_units:
            reasons.append("size_above_maximum")
        steps = units / self.unit_step
        if abs(steps - round(steps)) > 1e-6:
            reasons.append("size_not_on_step")
        return tuple(reasons)


DEFAULT_SPECS: dict[str, InstrumentSpec] = {
    "EURUSD": InstrumentSpec(
        symbol="EURUSD",
        base_currency="EUR",
        quote_currency="USD",
        pip_size=0.0001,
        price_precision=5,
        min_units=1_000.0,
        unit_step=1_000.0,
        max_units=10_000_000.0,
        margin_rate=1 / 30,
    ),
}


def required_margin(
    spec: InstrumentSpec, units: float, price: float, account_currency: str
) -> float | None:
    """Return margin in account currency, or None when conversion is unknown.

    Only direct base/quote conversions are supported. Any other account
    currency returns None so callers fail closed instead of guessing a rate.
    """

    if not _finite(units) or not _finite(price) or units <= 0 or price <= 0:
        return None
    currency = account_currency.strip().upper()
    if currency == spec.quote_currency:
        notional = abs(units) * price
    elif currency == spec.base_currency:
        notional = abs(units)
    else:
        return None
    return notional * spec.margin_rate
