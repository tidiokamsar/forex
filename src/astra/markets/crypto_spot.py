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
# Upper bound on the digits a step computation may need. Beyond it the input is refused with
# ValueError instead of being rounded (which could round up) or raising decimal.InvalidOperation.
MAX_STEP_PRECISION = 120


def _digits_needed(value: Decimal, step: Decimal) -> int:
    """Significant digits needed to represent ``value / step`` and the result on the step grid exactly."""

    exponents = [d.as_tuple().exponent for d in (value, step)]
    if not all(isinstance(e, int) for e in exponents):
        raise ValueError("value and step must be finite")
    top = max(value.adjusted() if value else 0, step.adjusted())
    return top - min(exponents) + 2


def floor_to_step(value: object, step: Decimal, name: str = "value") -> Decimal:
    """Largest multiple of ``step`` not above ``value`` (``value`` >= 0).

    Exact up to ``MAX_STEP_PRECISION`` digits; inputs that would need more
    raise ``ValueError`` so callers fail closed. The result is never above
    ``value``.
    """

    amount = to_decimal(value, name)
    if amount < 0:
        raise ValueError(f"{name} must not be negative")
    if step <= 0:
        raise ValueError("step must be positive")
    digits = _digits_needed(amount, step)
    if digits > MAX_STEP_PRECISION:
        raise ValueError(f"{name}/step ratio needs {digits} digits, above the {MAX_STEP_PRECISION}-digit bound")
    try:
        with localcontext() as context:
            context.prec = max(_PRECISION, digits)
            context.rounding = ROUND_FLOOR
            steps = (amount / step).to_integral_value(rounding=ROUND_FLOOR)
            result = (steps * step).quantize(step)
    except ArithmeticError as exc:  # decimal.InvalidOperation and friends
        raise ValueError(f"{name} cannot be rounded to step {step}") from exc
    if result > amount or amount - result >= step:
        raise ValueError(f"{name} cannot be rounded exactly to step {step}")
    return result


def _on_step(value: Decimal, step: Decimal) -> bool:
    """True when ``value`` is an exact multiple of ``step``; False when that cannot be decided exactly."""

    try:
        digits = _digits_needed(value, step)
        if digits > MAX_STEP_PRECISION:
            return False
        with localcontext() as context:
            context.prec = max(_PRECISION, digits)
            return value % step == 0
    except (ArithmeticError, ValueError):
        return False


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
    # Optional exchange bounds (None: not reported). Zero disables a bound, as in Binance PRICE_FILTER.
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    max_notional: Decimal | None = None

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
        for name in ("min_price", "max_price", "max_notional"):
            value = getattr(self, name)
            if value is not None:
                value = to_decimal(value, name)
                if value < 0:
                    raise ValueError(f"{name} must not be negative")
                object.__setattr__(self, name, value)
        if self.min_price and self.max_price and self.min_price > self.max_price:
            raise ValueError("min_price must not exceed max_price")
        if self.max_notional and self.max_notional < self.min_notional:
            raise ValueError("max_notional must not be below min_notional")

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
        if self.min_price and px < self.min_price:
            reasons.append("price_below_minimum")
        if self.max_price and px > self.max_price:
            reasons.append("price_above_maximum")
        if not self.notional_ok(qty, px):
            reasons.append("notional_below_minimum")
        if self.max_notional and self.notional(qty, px) > self.max_notional:
            reasons.append("notional_above_maximum")
        return tuple(reasons)
