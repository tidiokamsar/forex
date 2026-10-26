"""One risk policy per market type. Pure functions over data; nothing here sends an order.

- Forex/CFD: the existing ``astra.risk.RiskEngine`` stays the authority for
  sizing and every limit. ``ForexCfdPreCheck`` only adds contract-data checks
  that the engine cannot see (illustrative spec outside SIMULATION, missing
  swap, commission or conversion data, ambiguous lot conversion). Passing it
  is necessary, never sufficient.
- Crypto spot: ``CryptoSpotRiskPolicy``, a versioned frozen policy. No
  leverage, no shorting, notional caps from the free quote balance, rounding
  down through ``CryptoSpotSpec`` and fail-closed refusals on stale or unknown
  data. ALLOW is reachable only in ``RunMode.SIMULATION`` in this lot.
- Crypto futures / perpetuals and fixed-expiry options: policies that always
  refuse, with the requirements a future derivatives review must settle.

Martingale sizing, adding to a losing position, removing or widening a stop
and raising risk after losses are refused by construction: no input can scale
size up after a loss, and the checks below reject the patterns explicitly.

The decisions are data. No connector, gateway, permit or adapter is imported:
the only order path in ASTRA remains ``OrderGateway`` -> ``ExecutionPermit`` ->
``BrokerAdapter.submit_order``, and no crypto order can be sent in this lot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum

from ..models import RunMode
from ..venues.contract import Balance, MarketType, StaleDataError, ensure_fresh
from ..venues.registry import TRADING_ENABLED_IN_THIS_LOT
from . import crypto_futures, fixed_expiry_options
from .common import require_text, to_decimal
from .costs import BookTicker, CryptoSpotCostModel, CryptoSpotFeeSchedule, FeeUnknown, SlippageAssumption, SpotSide
from .crypto_spot import CryptoSpotSpec
from .forex_cfd import ForexCfdSpec

DERIVATIVES_NOT_ENABLED_REASON = "market type not enabled: requires derivatives risk review"
_PRECISION = 60


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


@dataclass(frozen=True, slots=True)
class MarketRiskDecision:
    """Structured outcome. Every decision records the policy name and version."""

    market_type: MarketType
    verdict: Verdict
    reasons: tuple[str, ...]
    policy_name: str
    policy_version: str
    mode: str
    quantity: Decimal | None = None
    price: Decimal | None = None
    notional: Decimal | None = None
    estimated_fee_quote: Decimal | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, MarketType):
            raise TypeError("market_type must be a MarketType")
        if not isinstance(self.verdict, Verdict):
            raise TypeError("verdict must be a Verdict")
        require_text(self.policy_name, "policy_name")
        require_text(self.policy_version, "policy_version")
        reasons = tuple(dict.fromkeys(self.reasons))
        if self.verdict is Verdict.ALLOW and reasons:
            raise ValueError("an ALLOW decision carries no refusal reason")
        if self.verdict is Verdict.REFUSE and not reasons:
            raise ValueError("a REFUSE decision needs at least one reason")
        object.__setattr__(self, "reasons", reasons)

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


def _mode_label(mode: object) -> str:
    return mode.value if isinstance(mode, RunMode) else "INVALID"


def _is_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


# ---------------------------------------------------------------- Forex / CFD


@dataclass(frozen=True, slots=True)
class ForexCfdPreCheck:
    """Contract-data pre-check run before the RiskEngine; it never sizes or allows on its own."""

    name: str = "forex_cfd_precheck"
    version: str = "forex-cfd-precheck-1"

    def __post_init__(self) -> None:
        require_text(self.name, "name")
        require_text(self.version, "version")

    def evaluate(
        self,
        spec: object,
        *,
        mode: object,
        account_currency: str | None,
        conversion_rates: dict[str, Decimal] | None = None,
        requested_lots: object = None,
    ) -> MarketRiskDecision:
        """``conversion_rates`` maps a currency to its rate into ``account_currency``.

        Outside SIMULATION the spec must be provider-sourced and complete, and
        every currency the position touches (quote for P&L, margin, commission,
        a currency-denominated swap) needs a rate unless it is the account
        currency. Lot conversion is checked in every mode.
        """

        reasons: list[str] = []
        if not isinstance(mode, RunMode):
            reasons.append("invalid_mode")
        elif mode is RunMode.LIVE:
            reasons.append("live_mode_not_supported")
        if not isinstance(spec, ForexCfdSpec):
            reasons.append("forex_spec_missing")
            return self._decision(mode, reasons)
        simulated = mode is RunMode.SIMULATION
        if spec.illustrative and not simulated:
            reasons.append("forex_spec_illustrative_outside_simulation")
        if not simulated:
            reasons.extend(self._completeness(spec, account_currency, conversion_rates))
        reasons.extend(self._lot_conversion(spec, requested_lots))
        return self._decision(mode, reasons)

    @staticmethod
    def _completeness(
        spec: ForexCfdSpec, account_currency: str | None, rates: dict[str, Decimal] | None
    ) -> list[str]:
        reasons: list[str] = []
        if spec.swap_long is None or spec.swap_short is None or spec.swap_unit is None:
            reasons.append("swap_unknown")
        if spec.commission_per_lot is None or spec.commission_currency is None:
            reasons.append("commission_unknown")
        if not isinstance(account_currency, str) or len(account_currency) != 3 or not account_currency.isupper():
            reasons.append("account_currency_unknown")
            return reasons
        needed = {spec.instrument.quote_currency, spec.margin_currency}
        if spec.commission_currency is not None:
            needed.add(spec.commission_currency)
        swap_unit = spec.swap_unit
        if isinstance(swap_unit, str) and len(swap_unit) == 3 and swap_unit.isalpha() and swap_unit.isupper():
            needed.add(swap_unit)
        rates = rates or {}
        for currency in sorted(needed - {account_currency}):
            rate = rates.get(currency)
            if not _is_decimal(rate) or rate <= 0:
                reasons.append(f"conversion_rate_unknown:{currency}")
        return reasons

    @staticmethod
    def _lot_conversion(spec: ForexCfdSpec, requested_lots: object) -> list[str]:
        instrument = spec.instrument
        # InstrumentSpec carries floats; their shortest repr is the intended decimal value.
        step = Decimal(repr(instrument.unit_step))
        minimum = Decimal(repr(instrument.min_units))
        maximum = Decimal(repr(instrument.max_units))
        with localcontext() as context:
            context.prec = _PRECISION
            if spec.contract_size % step != 0:
                return ["lot_conversion_ambiguous"]
            if requested_lots is None:
                return []
            try:
                lots = to_decimal(requested_lots, "requested_lots")
            except (TypeError, ValueError):
                return ["requested_lots_invalid"]
            if lots <= 0:
                return ["requested_lots_invalid"]
            units = lots * spec.contract_size
            if units % step != 0:
                return ["lot_conversion_ambiguous"]
            if units < minimum or units > maximum:
                return ["requested_lots_outside_contract_limits"]
        return []

    def _decision(self, mode: object, reasons: list[str]) -> MarketRiskDecision:
        return MarketRiskDecision(
            MarketType.FOREX_CFD,
            Verdict.REFUSE if reasons else Verdict.ALLOW,
            tuple(reasons),
            self.name,
            self.version,
            _mode_label(mode),
            notes=("pre-check only; astra.risk.RiskEngine remains the authority",),
        )


# ---------------------------------------------------------------- Crypto spot

# Provisional engineering ceilings (not calibrated, not advice). A policy may be stricter, never looser.
MAX_ORDER_NOTIONAL_FRACTION = Decimal("0.02")
MAX_DAILY_NOTIONAL_FRACTION = Decimal("0.10")
MAX_QUOTE_AGE_SECONDS = 5.0
MAX_SPEC_AGE_SECONDS = 300.0
MAX_BALANCE_AGE_SECONDS = 30.0
MAX_FEE_AGE_SECONDS = 86_400.0


@dataclass(frozen=True, slots=True)
class CryptoSpotOrderIntent:
    """A hypothetical spot order. BUY opens or adds; SELL only reduces holdings."""

    intent_id: str
    symbol: str
    side: SpotSide
    quantity: Decimal
    limit_price: Decimal
    created_at: datetime
    stop_price: Decimal | None = None
    # Gross expected edge per round trip as a fraction of notional, from the strategy.
    expected_edge_fraction: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SpotPosition:
    """Strategy holdings of the base asset with the protective stop in force."""

    quantity: Decimal
    average_entry_price: Decimal
    stop_price: Decimal | None


@dataclass(frozen=True, slots=True)
class CryptoSpotRiskContext:
    """Everything the policy reads. ``None`` means unknown and is refused, not defaulted."""

    mode: RunMode
    now: datetime
    spec: CryptoSpotSpec | None
    book: BookTicker | None
    balances: tuple[Balance, ...] | None
    fees: CryptoSpotFeeSchedule | None
    day_buy_notional: Decimal | None
    day_realized_pnl: Decimal | None
    borrowing_enabled: bool | None
    kill_switch_active: bool = False
    position: SpotPosition | None = None
    last_buy_notional: Decimal | None = None
    fee_asset_price_in_quote: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CryptoSpotRiskPolicy:
    """Versioned spot policy. Values are PROVISIONAL engineering parameters, not calibrated."""

    name: str = "crypto_spot"
    version: str = "crypto-spot-1"
    max_order_notional_fraction: Decimal = MAX_ORDER_NOTIONAL_FRACTION
    max_daily_notional_fraction: Decimal = MAX_DAILY_NOTIONAL_FRACTION
    max_quote_age_seconds: float = MAX_QUOTE_AGE_SECONDS
    max_spec_age_seconds: float = MAX_SPEC_AGE_SECONDS
    max_balance_age_seconds: float = MAX_BALANCE_AGE_SECONDS
    max_fee_age_seconds: float = MAX_FEE_AGE_SECONDS
    slippage: SlippageAssumption = field(default_factory=SlippageAssumption)
    leverage: int = 1
    allow_short: bool = False

    def __post_init__(self) -> None:
        require_text(self.name, "name")
        require_text(self.version, "version")
        for attr, ceiling in (
            ("max_order_notional_fraction", MAX_ORDER_NOTIONAL_FRACTION),
            ("max_daily_notional_fraction", MAX_DAILY_NOTIONAL_FRACTION),
        ):
            value = getattr(self, attr)
            if not _is_decimal(value) or not Decimal(0) < value <= ceiling:
                raise ValueError(f"{attr} must be a Decimal in (0, {ceiling}]")
        if self.max_order_notional_fraction > self.max_daily_notional_fraction:
            raise ValueError("max_order_notional_fraction must not exceed max_daily_notional_fraction")
        for attr, ceiling in (
            ("max_quote_age_seconds", MAX_QUOTE_AGE_SECONDS),
            ("max_spec_age_seconds", MAX_SPEC_AGE_SECONDS),
            ("max_balance_age_seconds", MAX_BALANCE_AGE_SECONDS),
            ("max_fee_age_seconds", MAX_FEE_AGE_SECONDS),
        ):
            value = getattr(self, attr)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= ceiling:
                raise ValueError(f"{attr} must be within (0, {ceiling}]")
        if not isinstance(self.slippage, SlippageAssumption):
            raise TypeError("slippage must be a SlippageAssumption")
        if self.leverage != 1 or isinstance(self.leverage, bool):
            raise ValueError("spot policy has no leverage: leverage must be 1")
        if self.allow_short is not False:
            raise ValueError("spot policy does not short: allow_short must be False")

    # -- public API

    def evaluate(self, intent: object, context: object) -> MarketRiskDecision:
        if not isinstance(context, CryptoSpotRiskContext):
            return self._refuse("INVALID", ["invalid_risk_context"])
        mode = context.mode
        if not isinstance(intent, CryptoSpotOrderIntent):
            return self._refuse(_mode_label(mode), ["invalid_order_intent"])
        reasons: list[str] = []
        reasons.extend(self._mode_reasons(mode))
        if context.kill_switch_active is not False:
            reasons.append("kill_switch_active_or_unknown")
        if context.borrowing_enabled is not False:
            reasons.append("borrowing_enabled_or_unknown")
        reasons.extend(self._intent_shape(intent))

        spec, book = context.spec, context.book
        reasons.extend(self._market_data(intent, context))
        if not isinstance(spec, CryptoSpotSpec) or not isinstance(book, BookTicker) or reasons_block_pricing(reasons):
            return self._refuse(_mode_label(mode), reasons)

        quantity = spec.round_quantity_down(intent.quantity)
        price = spec.round_price_down(intent.limit_price)
        reasons.extend(spec.order_violations(quantity, price))
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            notional = quantity * price

        balances = self._balances(context, spec, reasons)
        cost_model = self._cost_model(context, reasons)
        fee_quote: Decimal | None = None
        if cost_model is not None and quantity > 0 and price > 0:
            fee_quote = self._fee_checks(intent, context, cost_model, spec, quantity, price, balances, reasons)

        if intent.side is SpotSide.BUY:
            self._buy_checks(intent, context, spec, book, cost_model, quantity, price, notional, fee_quote,
                             balances, reasons)
        else:
            self._sell_checks(spec, quantity, balances, reasons)

        verdict = Verdict.REFUSE if reasons else Verdict.ALLOW
        return MarketRiskDecision(
            MarketType.CRYPTO_SPOT,
            verdict,
            tuple(reasons),
            self.name,
            self.version,
            _mode_label(mode),
            quantity=quantity,
            price=price,
            notional=notional,
            estimated_fee_quote=fee_quote,
            notes=(
                "decision is data only; no crypto order can be sent in this lot",
                f"slippage assumption {self.slippage.fraction_per_side} per side is {self.slippage.calibration}",
            ),
        )

    def evaluate_stop_change(
        self, position: object, new_stop: object, *, mode: object = RunMode.SIMULATION
    ) -> MarketRiskDecision:
        """A long spot stop may only be tightened (raised); removal or widening is refused."""

        reasons: list[str] = list(self._mode_reasons(mode))
        if not isinstance(position, SpotPosition) or not _is_decimal(position.stop_price):
            reasons.append("current_stop_unknown")
        elif new_stop is None:
            reasons.append("stop_removal_refused")
        elif not _is_decimal(new_stop) or new_stop <= 0:
            reasons.append("invalid_stop")
        elif new_stop < position.stop_price:
            reasons.append("stop_widening_refused")
        return MarketRiskDecision(
            MarketType.CRYPTO_SPOT,
            Verdict.REFUSE if reasons else Verdict.ALLOW,
            tuple(reasons),
            self.name,
            self.version,
            _mode_label(mode),
        )

    # -- checks

    @staticmethod
    def _mode_reasons(mode: object) -> list[str]:
        if not isinstance(mode, RunMode):
            return ["invalid_mode"]
        if mode is RunMode.LIVE:
            return ["live_mode_not_supported"]
        if mode is not RunMode.SIMULATION:
            reasons = ["crypto_spot_allowed_only_in_simulation_in_this_lot"]
            if not TRADING_ENABLED_IN_THIS_LOT:
                reasons.append("venue_trading_not_enabled_in_this_lot")
            return reasons
        return []

    @staticmethod
    def _intent_shape(intent: CryptoSpotOrderIntent) -> list[str]:
        reasons: list[str] = []
        if not isinstance(intent.intent_id, str) or not intent.intent_id.strip():
            reasons.append("invalid_intent_id")
        if not isinstance(intent.side, SpotSide):
            reasons.append("invalid_side")
        if not _is_decimal(intent.quantity) or intent.quantity <= 0:
            reasons.append("invalid_quantity")
        if not _is_decimal(intent.limit_price) or intent.limit_price <= 0:
            reasons.append("invalid_price")
        created = intent.created_at
        if not isinstance(created, datetime) or created.tzinfo is None or created.utcoffset() is None:
            reasons.append("invalid_intent_timestamp")
        return reasons

    def _market_data(self, intent: CryptoSpotOrderIntent, context: CryptoSpotRiskContext) -> list[str]:
        reasons: list[str] = []
        now = context.now
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return ["invalid_reference_time"]
        spec = context.spec
        if not isinstance(spec, CryptoSpotSpec):
            reasons.append("spec_unknown")
        else:
            if spec.symbol != intent.symbol:
                reasons.append("spec_symbol_mismatch")
            if not spec.is_trading:
                reasons.append("symbol_not_trading")
            reasons.extend(_freshness("spec", spec.as_of, self.max_spec_age_seconds, now))
        book = context.book
        if not isinstance(book, BookTicker):
            reasons.append("quote_unknown")
        else:
            if book.symbol != intent.symbol:
                reasons.append("quote_symbol_mismatch")
            reasons.extend(_freshness("quote", book.as_of, self.max_quote_age_seconds, now))
        return reasons

    def _balances(
        self, context: CryptoSpotRiskContext, spec: CryptoSpotSpec, reasons: list[str]
    ) -> dict[str, Balance] | None:
        balances = context.balances
        if not isinstance(balances, tuple) or not all(isinstance(b, Balance) for b in balances):
            reasons.append("balances_unknown")
            return None
        by_asset: dict[str, Balance] = {}
        for balance in balances:
            if balance.asset in by_asset:
                reasons.append(f"balance_duplicated:{balance.asset}")
            by_asset[balance.asset] = balance
            reasons.extend(_freshness(f"balance_{balance.asset}", balance.as_of, self.max_balance_age_seconds, context.now))
        # A missing asset is unknown, not zero: some venues omit zero balances on request.
        for asset in (spec.base_asset, spec.quote_asset):
            if asset not in by_asset:
                reasons.append(f"balance_unknown:{asset}")
        return by_asset

    def _cost_model(self, context: CryptoSpotRiskContext, reasons: list[str]) -> CryptoSpotCostModel | None:
        fees = context.fees
        if not isinstance(fees, CryptoSpotFeeSchedule):
            reasons.append("fees_unknown")
            return None
        spec = context.spec
        if isinstance(spec, CryptoSpotSpec) and fees.symbol != spec.symbol:
            reasons.append("fees_symbol_mismatch")
            return None
        reasons.extend(_freshness("fees", fees.as_of, self.max_fee_age_seconds, context.now))
        return CryptoSpotCostModel(fees, self.slippage)

    @staticmethod
    def _fee_checks(
        intent: CryptoSpotOrderIntent,
        context: CryptoSpotRiskContext,
        cost_model: CryptoSpotCostModel,
        spec: CryptoSpotSpec,
        quantity: Decimal,
        price: Decimal,
        balances: dict[str, Balance] | None,
        reasons: list[str],
    ) -> Decimal | None:
        try:
            fee = cost_model.fee(
                spec, intent.side, quantity, price, fee_asset_price_in_quote=context.fee_asset_price_in_quote
            )
        except (FeeUnknown, TypeError, ValueError):
            reasons.append("fee_asset_conversion_unknown")
            return None
        if fee.asset not in (spec.base_asset, spec.quote_asset):
            held = None if balances is None else balances.get(fee.asset)
            if held is None:
                reasons.append(f"balance_unknown:{fee.asset}")
            elif held.free < fee.amount:
                reasons.append("insufficient_fee_asset_balance")
        return fee.quote_value

    def _buy_checks(
        self,
        intent: CryptoSpotOrderIntent,
        context: CryptoSpotRiskContext,
        spec: CryptoSpotSpec,
        book: BookTicker,
        cost_model: CryptoSpotCostModel | None,
        quantity: Decimal,
        price: Decimal,
        notional: Decimal,
        fee_quote: Decimal | None,
        balances: dict[str, Balance] | None,
        reasons: list[str],
    ) -> None:
        stop = intent.stop_price
        if stop is None:
            reasons.append("stop_required")
        elif not _is_decimal(stop) or stop <= 0 or stop >= price:
            reasons.append("invalid_stop")

        quote_balance = None if balances is None else balances.get(spec.quote_asset)
        if quote_balance is not None:
            free_quote = quote_balance.free
            # No borrowing: the whole cost, plus a quote-denominated fee, must be covered by free quote.
            fee_charged_in_quote = cost_model is not None and cost_model.fees.fee_asset == spec.quote_asset
            fee_in_quote_asset = fee_quote if fee_charged_in_quote and fee_quote is not None else Decimal(0)
            if notional + fee_in_quote_asset > free_quote:
                reasons.append("insufficient_free_quote_balance")
            with localcontext() as ctx:
                ctx.prec = _PRECISION
                order_cap = free_quote * self.max_order_notional_fraction
                daily_cap = free_quote * self.max_daily_notional_fraction
            if notional > order_cap:
                reasons.append("order_notional_exceeds_cap")
            day_used = context.day_buy_notional
            if not _is_decimal(day_used) or day_used < 0:
                reasons.append("daily_notional_unknown")
            elif day_used + notional > daily_cap:
                reasons.append("daily_notional_exceeds_cap")

        pnl = context.day_realized_pnl
        if not _is_decimal(pnl):
            reasons.append("day_realized_pnl_unknown")
        elif pnl < 0:
            last = context.last_buy_notional
            if last is not None and (not _is_decimal(last) or notional > last):
                # Martingale and loss-recovery sizing: size never grows after a loss.
                reasons.append("risk_increase_after_loss")

        position = context.position
        if position is not None:
            if not isinstance(position, SpotPosition) or not _is_decimal(position.average_entry_price):
                reasons.append("position_state_invalid")
            elif book.bid_price < position.average_entry_price:
                reasons.append("adding_to_losing_position")
            if isinstance(position, SpotPosition) and position.stop_price is None:
                reasons.append("open_position_without_stop")

        if cost_model is not None:
            edge = intent.expected_edge_fraction
            if edge is None:
                reasons.append("expected_edge_unknown")
            elif not _is_decimal(edge):
                reasons.append("expected_edge_invalid")
            elif cost_model.net_expectancy_fraction(edge, book) <= 0:
                reasons.append("net_expectancy_not_positive")

    @staticmethod
    def _sell_checks(
        spec: CryptoSpotSpec, quantity: Decimal, balances: dict[str, Balance] | None, reasons: list[str]
    ) -> None:
        base_balance = None if balances is None else balances.get(spec.base_asset)
        if base_balance is not None and quantity > base_balance.free:
            # No shorting: a spot sell is bounded by the free base balance.
            reasons.append("sell_exceeds_free_base_balance")

    def _refuse(self, mode: str, reasons: list[str]) -> MarketRiskDecision:
        return MarketRiskDecision(
            MarketType.CRYPTO_SPOT, Verdict.REFUSE, tuple(reasons) or ("refused",), self.name, self.version, mode
        )


def reasons_block_pricing(reasons: list[str]) -> bool:
    """Rounding needs a valid quantity and price; skip it when either is malformed."""

    return any(code in ("invalid_quantity", "invalid_price", "invalid_side", "invalid_reference_time") for code in reasons)


def _freshness(label: str, timestamp: object, max_age: float, now: datetime) -> list[str]:
    try:
        ensure_fresh(timestamp, max_age_seconds=max_age, now=now)
    except StaleDataError as exc:
        return [f"{label}_{exc.reason}"]
    except (TypeError, ValueError):
        return [f"{label}_timestamp_invalid"]
    return []


# ------------------------------------------- Derivatives (disabled in this lot)

FUTURES_REVIEW_REQUIREMENTS: tuple[str, ...] = (
    "initial and maintenance margin per contract and per tier, from the provider",
    "mark price and index price source, update rate and staleness limit",
    "funding rate sign convention, interval, notional basis and cost in expectancy",
    "liquidation distance from mark price, with a minimum buffer; ASTRA never computes a liquidation price",
    "margin mode (isolated or cross) and the account-level consequence of each",
    "cross-contract and cross-venue exposure, including spot holdings of the same asset",
    "leverage schedule and maximum effective leverage, auto-deleverage and insurance fund rules",
    "position-mode (one-way or hedge) and reduce-only semantics",
)

OPTIONS_REVIEW_REQUIREMENTS: tuple[str, ...] = (
    "expiry schedule, settlement time and the settlement price source",
    "payout and premium terms, including the tie rule, from the provider",
    "maximum loss per contract equals premium; premium budget per day and per account",
    "product restrictions or prohibitions for retail clients in the user's jurisdiction",
    "pricing of the expected value net of the payout asymmetry",
    "early close, cancellation and requote rules",
)


@dataclass(frozen=True, slots=True)
class CryptoFuturesRiskPolicy:
    """Always refuses (``crypto_futures.ENABLED`` is False). Covers perpetual and delivery contracts."""

    name: str = "crypto_futures"
    version: str = "crypto-futures-disabled-1"
    review_requirements: tuple[str, ...] = FUTURES_REVIEW_REQUIREMENTS

    def evaluate(self, *_args: object, mode: object = None, **_kwargs: object) -> MarketRiskDecision:
        reasons = [DERIVATIVES_NOT_ENABLED_REASON]
        if crypto_futures.ENABLED is not False:
            reasons.append("enabled_flag_set_without_reviewed_policy")
        return MarketRiskDecision(
            MarketType.CRYPTO_FUTURES, Verdict.REFUSE, tuple(reasons), self.name, self.version, _mode_label(mode)
        )


@dataclass(frozen=True, slots=True)
class FixedExpiryOptionsRiskPolicy:
    """Always refuses (``fixed_expiry_options.ENABLED`` is False)."""

    name: str = "fixed_expiry_options"
    version: str = "fixed-expiry-options-disabled-1"
    review_requirements: tuple[str, ...] = OPTIONS_REVIEW_REQUIREMENTS

    def evaluate(self, *_args: object, mode: object = None, **_kwargs: object) -> MarketRiskDecision:
        reasons = [DERIVATIVES_NOT_ENABLED_REASON]
        if fixed_expiry_options.ENABLED is not False:
            reasons.append("enabled_flag_set_without_reviewed_policy")
        return MarketRiskDecision(
            MarketType.FIXED_EXPIRY_OPTIONS, Verdict.REFUSE, tuple(reasons), self.name, self.version, _mode_label(mode)
        )


MarketRiskPolicy = ForexCfdPreCheck | CryptoSpotRiskPolicy | CryptoFuturesRiskPolicy | FixedExpiryOptionsRiskPolicy


def policy_for(market_type: MarketType) -> MarketRiskPolicy:
    """Default policy instance per market type; there is no shared fallback."""

    if market_type is MarketType.FOREX_CFD:
        return ForexCfdPreCheck()
    if market_type is MarketType.CRYPTO_SPOT:
        return CryptoSpotRiskPolicy()
    if market_type is MarketType.CRYPTO_FUTURES:
        return CryptoFuturesRiskPolicy()
    if market_type is MarketType.FIXED_EXPIRY_OPTIONS:
        return FixedExpiryOptionsRiskPolicy()
    raise ValueError(f"no risk policy for {market_type!r}")


__all__ = [
    "DERIVATIVES_NOT_ENABLED_REASON",
    "FUTURES_REVIEW_REQUIREMENTS",
    "MAX_BALANCE_AGE_SECONDS",
    "MAX_DAILY_NOTIONAL_FRACTION",
    "MAX_FEE_AGE_SECONDS",
    "MAX_ORDER_NOTIONAL_FRACTION",
    "MAX_QUOTE_AGE_SECONDS",
    "MAX_SPEC_AGE_SECONDS",
    "OPTIONS_REVIEW_REQUIREMENTS",
    "CryptoFuturesRiskPolicy",
    "CryptoSpotOrderIntent",
    "CryptoSpotRiskContext",
    "CryptoSpotRiskPolicy",
    "FixedExpiryOptionsRiskPolicy",
    "ForexCfdPreCheck",
    "MarketRiskDecision",
    "MarketRiskPolicy",
    "SpotPosition",
    "Verdict",
    "policy_for",
]
