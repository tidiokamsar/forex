"""Crypto spot symbol rules, kept apart from the Forex ``InstrumentSpec``.

``CryptoSpotSpec`` is a data model with exact ``Decimal`` helpers that round a
quantity or a price DOWN to the exchange step and report rule violations. It is
not wired into any backtest, risk or execution path in this lot, and no order
is sent from here: order placement for a new venue goes only through the
existing ``BrokerAdapter`` permit boundary, after review.

Price steps are taken from zero (``price = k * tick_size``). Exchanges that
anchor the tick grid on a minimum price need that offset added before use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal, localcontext

from .common import require_aware, require_symbol, require_text, to_decimal

TRADING_STATUS = "TRADING"
_PRECISION = 60


def floor_to_step(value: object, step: Decimal, name: str = "value") -> Decimal:
    """Largest multiple of ``step`` not above ``value`` (``value`` >= 0)."""

    amount = to_decimal(value, name)
    if amount < 0:
        raise ValueError(f"{name} must not be negative")
    if step <= 0:
        raise ValueError("step must be positive")
    with localcontext() as context:
        context.prec = _PRECISION
        steps = (amount / step).to_integral_value(rounding=ROUND_FLOOR)
        return (steps * step).quantize(step)


def _on_step(value: Decimal, step: Decimal) -> bool:
    with localcontext() as context:
        context.prec = _PRECISION
        return value % step == 0


@dataclass(frozen=True, slots=True)
class CryptoSpotSpec:
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int
    maker_fee: Decimal
    taker_fee: Decimal
    source: str
    as_of: datetime

    def __post_init__(self) -> None:
        require_symbol(self.symbol)
        require_symbol(self.base_asset, "base_asset")
        require_symbol(self.quote_asset, "quote_asset")
        require_symbol(self.status, "status")
        for name in ("tick_size", "step_size", "min_qty", "max_qty", "min_notional", "maker_fee", "taker_fee"):
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        for name in ("tick_size", "step_size", "max_qty"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("min_qty", "min_notional"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.min_qty > self.max_qty:
            raise ValueError("min_qty must not exceed max_qty")
        for name in ("maker_fee", "taker_fee"):
            # A negative maker fee is a rebate; a rate at or beyond 100% is a data error.
            if not Decimal(-1) < getattr(self, name) < Decimal(1):
                raise ValueError(f"{name} must be a rate strictly between -1 and 1")
        for name in ("price_precision", "quantity_precision"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        require_text(self.source, "source")
        require_aware(self.as_of, "as_of")

    @property
    def is_trading(self) -> bool:
        return self.status == TRADING_STATUS

    def round_quantity_down(self, quantity: object) -> Decimal:
        return floor_to_step(quantity, self.step_size, "quantity")

    def round_price_down(self, price: object) -> Decimal:
        return floor_to_step(price, self.tick_size, "price")

    def notional(self, quantity: object, price: object) -> Decimal:
        with localcontext() as context:
            context.prec = _PRECISION
            return to_decimal(quantity, "quantity") * to_decimal(price, "price")

    def notional_ok(self, quantity: object, price: object) -> bool:
        return self.notional(quantity, price) >= self.min_notional

    def order_violations(self, quantity: object, price: object) -> tuple[str, ...]:
        """Reason codes for a hypothetical order; empty when every rule holds."""

        reasons: list[str] = []
        try:
            qty = to_decimal(quantity, "quantity")
        except (TypeError, ValueError):
            return ("invalid_quantity",)
        try:
            px = to_decimal(price, "price")
        except (TypeError, ValueError):
            return ("invalid_price",)
        if qty <= 0:
            return ("invalid_quantity",)
        if px <= 0:
            return ("invalid_price",)
        if not self.is_trading:
            reasons.append("symbol_not_trading")
        if qty < self.min_qty:
            reasons.append("quantity_below_minimum")
        if qty > self.max_qty:
            reasons.append("quantity_above_maximum")
        if not _on_step(qty, self.step_size):
            reasons.append("quantity_not_on_step")
        if not _on_step(px, self.tick_size):
            reasons.append("price_not_on_tick")
        if not self.notional_ok(qty, px):
            reasons.append("notional_below_minimum")
        return tuple(reasons)
