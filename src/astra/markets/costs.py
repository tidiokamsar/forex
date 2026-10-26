"""Per-market cost models, kept apart so crypto costs are not forced into pip units.

- Forex/CFD reuses the existing ``astra.models.CostModel`` (pips, quote
  currency). ``forex_cost_model`` only derives a commission per unit from a
  ``ForexCfdSpec`` when the commission basis and currency are explicit.
- Crypto spot uses exact ``Decimal`` arithmetic: maker/taker fee rates with fee
  asset handling, the spread read from a best bid/ask (``bookTicker``) snapshot,
  and a slippage assumption that is explicit and labelled ``UNCALIBRATED``.
- Crypto futures funding is a data structure only (``FUNDING_COST_ENABLED`` is
  False): nothing here computes a funding cost.

Every value is an estimate under stated assumptions, not a provider-reported
cost. No module here sends orders.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum

from ..models import CostModel
from .common import require_aware, require_symbol, require_text, to_decimal
from .crypto_spot import CryptoSpotSpec
from .forex_cfd import ForexCfdSpec

UNCALIBRATED = "UNCALIBRATED"
FUNDING_COST_ENABLED = False
_PRECISION = 60


# ---------------------------------------------------------------- Forex / CFD


class CommissionBasis(StrEnum):
    """How a per-lot commission is quoted. Brokers use both; it is never guessed."""

    PER_SIDE = "PER_SIDE"
    ROUND_TURN = "ROUND_TURN"


def forex_cost_model(
    spec: ForexCfdSpec,
    *,
    base: CostModel | None = None,
    commission_basis: CommissionBasis | None = None,
) -> CostModel:
    """Return the existing ``CostModel`` for a Forex/CFD spec.

    Spread and slippage come from ``base`` (default ``CostModel()``, the
    simulation assumptions). The pip size comes from the wrapped
    ``InstrumentSpec``. A spec commission is converted to the per-unit,
    per-side figure ``CostModel`` expects only when its basis is given and its
    currency is the instrument quote currency (the simulator books costs in
    quote currency); otherwise ``ValueError`` is raised instead of guessing.
    """

    if not isinstance(spec, ForexCfdSpec):
        raise TypeError("spec must be a ForexCfdSpec")
    model = CostModel() if base is None else base
    if not isinstance(model, CostModel):
        raise TypeError("base must be an astra.models.CostModel")
    model = replace(model, pip_size=spec.instrument.pip_size)
    if spec.commission_per_lot is None:
        return model
    if not isinstance(commission_basis, CommissionBasis):
        raise ValueError("commission_basis is required to use commission_per_lot")
    if spec.commission_currency != spec.instrument.quote_currency:
        raise ValueError("commission currency differs from the quote currency; a conversion is required")
    per_unit = spec.commission_per_lot / spec.contract_size
    if commission_basis is CommissionBasis.ROUND_TURN:
        per_unit = per_unit / 2
    return replace(model, commission_per_unit_per_side=float(per_unit))


# ---------------------------------------------------------------- Crypto spot


class SpotSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Liquidity(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class FeeUnknown(ValueError):
    """A fee cannot be computed from the data at hand; callers must refuse."""


@dataclass(frozen=True, slots=True)
class BookTicker:
    """Best bid/ask snapshot, as returned by a ``bookTicker`` endpoint.

    The REST payload carries no timestamp, so ``as_of`` is the local receipt
    time supplied by the caller.
    """

    symbol: str
    bid_price: Decimal
    bid_qty: Decimal
    ask_price: Decimal
    ask_qty: Decimal
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        require_symbol(self.symbol)
        for name in ("bid_price", "bid_qty", "ask_price", "ask_qty"):
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        if self.bid_price <= 0 or self.ask_price <= 0:
            raise ValueError("bid and ask prices must be positive")
        if self.bid_qty < 0 or self.ask_qty < 0:
            raise ValueError("book quantities must not be negative")
        if self.ask_price < self.bid_price:
            raise ValueError("crossed book: ask below bid")
        require_aware(self.as_of, "as_of")
        require_text(self.source, "source")

    @classmethod
    def from_payload(cls, payload: Mapping[str, object], *, as_of: datetime, source: str) -> BookTicker:
        """Parse ``{"symbol", "bidPrice", "bidQty", "askPrice", "askQty"}`` (string numbers)."""

        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        try:
            return cls(
                symbol=payload["symbol"],
                bid_price=payload["bidPrice"],
                bid_qty=payload["bidQty"],
                ask_price=payload["askPrice"],
                ask_qty=payload["askQty"],
                as_of=as_of,
                source=source,
            )
        except KeyError as exc:
            raise ValueError(f"bookTicker payload is missing {exc.args[0]!r}") from exc

    @property
    def spread(self) -> Decimal:
        return self.ask_price - self.bid_price

    @property
    def mid(self) -> Decimal:
        with localcontext() as context:
            context.prec = _PRECISION
            return (self.ask_price + self.bid_price) / 2

    @property
    def spread_fraction(self) -> Decimal:
        """Full spread as a fraction of mid; one round trip pays it once."""

        with localcontext() as context:
            context.prec = _PRECISION
            return self.spread / self.mid


@dataclass(frozen=True, slots=True)
class SlippageAssumption:
    """Adverse slippage per side as a fraction of price. Not measured: ``UNCALIBRATED``."""

    fraction_per_side: Decimal = Decimal("0.0005")
    calibration: str = UNCALIBRATED
    note: str = "engineering placeholder; no fill data has been used to estimate it"

    def __post_init__(self) -> None:
        value = to_decimal(self.fraction_per_side, "fraction_per_side")
        if not Decimal(0) <= value < Decimal(1):
            raise ValueError("fraction_per_side must be in [0, 1)")
        object.__setattr__(self, "fraction_per_side", value)
        require_text(self.calibration, "calibration")
        require_text(self.note, "note")


@dataclass(frozen=True, slots=True)
class CryptoSpotFeeSchedule:
    """Account fee rates for one symbol.

    ``fee_asset`` None means the fee is charged in the asset received (base on
    a BUY, quote on a SELL). A named asset (for example a venue token) means the
    fee is charged in that asset at the same rate, valued at its quote price.
    """

    symbol: str
    maker_rate: Decimal
    taker_rate: Decimal
    source: str
    as_of: datetime
    fee_asset: str | None = None

    def __post_init__(self) -> None:
        require_symbol(self.symbol)
        for name in ("maker_rate", "taker_rate"):
            value = to_decimal(getattr(self, name), name)
            # A negative maker rate is a rebate; a rate at or beyond 100% is a data error.
            if not Decimal(-1) < value < Decimal(1):
                raise ValueError(f"{name} must be strictly between -1 and 1")
            object.__setattr__(self, name, value)
        if self.taker_rate < 0:
            raise ValueError("taker_rate must not be negative")
        require_text(self.source, "source")
        require_aware(self.as_of, "as_of")
        if self.fee_asset is not None:
            require_symbol(self.fee_asset, "fee_asset")

    @classmethod
    def from_spec(cls, spec: CryptoSpotSpec) -> CryptoSpotFeeSchedule:
        """Use the spec rates (often venue defaults, not the account's own rates)."""

        return cls(spec.symbol, spec.maker_fee, spec.taker_fee, spec.source, spec.as_of)

    def rate(self, liquidity: Liquidity) -> Decimal:
        if liquidity is Liquidity.MAKER:
            return self.maker_rate
        if liquidity is Liquidity.TAKER:
            return self.taker_rate
        raise TypeError("liquidity must be a Liquidity")

    @property
    def worst_rate(self) -> Decimal:
        return max(self.maker_rate, self.taker_rate)


@dataclass(frozen=True, slots=True)
class FeeEstimate:
    asset: str
    amount: Decimal
    quote_value: Decimal
    rate: Decimal


@dataclass(frozen=True, slots=True)
class CryptoSpotCostModel:
    fees: CryptoSpotFeeSchedule
    slippage: SlippageAssumption = SlippageAssumption()

    def __post_init__(self) -> None:
        if not isinstance(self.fees, CryptoSpotFeeSchedule):
            raise TypeError("fees must be a CryptoSpotFeeSchedule")
        if not isinstance(self.slippage, SlippageAssumption):
            raise TypeError("slippage must be a SlippageAssumption")

    def fee(
        self,
        spec: CryptoSpotSpec,
        side: SpotSide,
        quantity: object,
        price: object,
        *,
        rate: Decimal | None = None,
        fee_asset_price_in_quote: object = None,
    ) -> FeeEstimate:
        """Fee of one fill; raises ``FeeUnknown`` when the fee asset cannot be valued.

        ``rate`` defaults to the worst of maker and taker.
        """

        if not isinstance(side, SpotSide):
            raise TypeError("side must be a SpotSide")
        if spec.symbol != self.fees.symbol:
            raise FeeUnknown("fee schedule is for another symbol")
        fee_rate = self.fees.worst_rate if rate is None else to_decimal(rate, "rate")
        qty = to_decimal(quantity, "quantity")
        px = to_decimal(price, "price")
        with localcontext() as context:
            context.prec = _PRECISION
            notional_fee = qty * px * fee_rate
            asset = self.fees.fee_asset
            if asset is None:
                asset = spec.base_asset if side is SpotSide.BUY else spec.quote_asset
            if asset == spec.quote_asset:
                return FeeEstimate(asset, notional_fee, notional_fee, fee_rate)
            if asset == spec.base_asset:
                return FeeEstimate(asset, qty * fee_rate, notional_fee, fee_rate)
            if fee_asset_price_in_quote is None:
                raise FeeUnknown(f"no {asset}/{spec.quote_asset} price to value the fee")
            fee_price = to_decimal(fee_asset_price_in_quote, "fee_asset_price_in_quote")
            if fee_price <= 0:
                raise FeeUnknown("fee asset price must be positive")
            return FeeEstimate(asset, notional_fee / fee_price, notional_fee, fee_rate)

    def round_trip_cost_fraction(self, book: BookTicker) -> Decimal:
        """Two fills at the worst fee rate, one full spread and two slippage legs."""

        if not isinstance(book, BookTicker):
            raise TypeError("book must be a BookTicker")
        with localcontext() as context:
            context.prec = _PRECISION
            return 2 * self.fees.worst_rate + book.spread_fraction + 2 * self.slippage.fraction_per_side

    def net_expectancy_fraction(self, gross_edge_fraction: object, book: BookTicker) -> Decimal:
        """Gross expected edge per round trip minus the round-trip cost, as a fraction of notional."""

        edge = to_decimal(gross_edge_fraction, "gross_edge_fraction")
        with localcontext() as context:
            context.prec = _PRECISION
            return edge - self.round_trip_cost_fraction(book)


# ------------------------------------------------- Crypto futures (disabled)


@dataclass(frozen=True, slots=True)
class FundingCostAssumption:
    """Funding terms of a perpetual contract. Data only; nothing computes a cost from it.

    A future derivatives review must define sign convention (who pays whom),
    the notional the rate applies to (mark price times position), the interval
    and the history used before ``FUNDING_COST_ENABLED`` can change.
    """

    symbol: str
    funding_rate: Decimal
    interval_hours: int
    next_funding_time: datetime
    source: str
    as_of: datetime
    calibration: str = UNCALIBRATED

    def __post_init__(self) -> None:
        require_symbol(self.symbol)
        object.__setattr__(self, "funding_rate", to_decimal(self.funding_rate, "funding_rate"))
        if isinstance(self.interval_hours, bool) or not isinstance(self.interval_hours, int) or self.interval_hours <= 0:
            raise ValueError("interval_hours must be a positive integer")
        require_aware(self.next_funding_time, "next_funding_time")
        require_text(self.source, "source")
        require_aware(self.as_of, "as_of")
        require_text(self.calibration, "calibration")


__all__ = [
    "FUNDING_COST_ENABLED",
    "UNCALIBRATED",
    "BookTicker",
    "CommissionBasis",
    "CryptoSpotCostModel",
    "CryptoSpotFeeSchedule",
    "FeeEstimate",
    "FeeUnknown",
    "FundingCostAssumption",
    "Liquidity",
    "SlippageAssumption",
    "SpotSide",
    "forex_cost_model",
]
