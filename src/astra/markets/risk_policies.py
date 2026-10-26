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

The decisions are data. No connector, gateway, permit or concrete broker
adapter is imported, and no ``astra.brokers`` module is loaded: the shared value
types come from ``astra.connector_types`` (MV-D-13). The only order path in
ASTRA remains ``OrderGateway`` -> ``ExecutionPermit`` ->
``BrokerAdapter.submit_order``, and no crypto order can be sent in this lot.

Residual trust: ``position``, ``last_buy_notional`` and the intent's
``expected_edge_fraction`` are caller-supplied. The policy narrows them (an
unknown position with holdings refuses, the ledger's last BUY bounds the
caller's value) but cannot verify them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from threading import Lock

from ..models import RunMode
from ..venues.contract import Balance, MarketType, StaleDataError, ensure_fresh
from ..venues.registry import TRADING_ENABLED_IN_THIS_LOT
from . import crypto_futures, fixed_expiry_options
from .common import require_text, to_decimal
from .costs import (
    BookTicker,
    Calibration,
    CryptoSpotCostModel,
    CryptoSpotFeeSchedule,
    FeeUnknown,
    SlippageAssumption,
    SpotSide,
)
from .crypto_spot import CryptoSpotSpec
from .forex_cfd import ForexCfdSpec

DERIVATIVES_NOT_ENABLED_REASON = "market type not enabled: requires derivatives risk review"
_PRECISION = 60


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


Parameters = tuple[tuple[str, str], ...]

# Modes in which an ALLOW verdict may exist at all, per market type. Crypto spot: SIMULATION only in this
# lot. Forex/CFD: the pre-check is advisory (the RiskEngine is the authority and refuses everything but
# SIMULATION), so its ALLOW may also be reported in DEMO. Derivatives: never.
_ALLOW_MODES: dict[MarketType, frozenset[str]] = {
    MarketType.CRYPTO_SPOT: frozenset({"SIMULATION"}),
    MarketType.FOREX_CFD: frozenset({"SIMULATION", "DEMO"}),
    MarketType.CRYPTO_FUTURES: frozenset(),
    MarketType.FIXED_EXPIRY_OPTIONS: frozenset(),
}


def canonical_parameters(values: dict[str, object]) -> tuple[Parameters, str]:
    """Sorted ``(name, str(value))`` pairs and the sha256 of their canonical JSON."""

    pairs = tuple(sorted((str(key), str(value)) for key, value in values.items()))
    return pairs, _fingerprint(pairs)


def _fingerprint(pairs: Parameters) -> str:
    payload = json.dumps([list(pair) for pair in pairs], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class MarketRiskDecision:
    """Structured outcome: policy name, version, parameters and their fingerprint.

    A decision is data, not an authority token: anyone can construct one, so a
    future gate must re-evaluate rather than trust it. Construction still
    refuses impossible outcomes: ALLOW outside the modes listed in
    ``_ALLOW_MODES`` (crypto spot only in SIMULATION, derivatives never), and
    ALLOW without recorded parameters or with a fingerprint that does not
    match them.
    """

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
    parameters: Parameters = ()
    parameters_fingerprint: str = ""

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
        if self.verdict is Verdict.ALLOW and self.mode not in _ALLOW_MODES[self.market_type]:
            raise ValueError(f"{self.market_type.value} cannot be ALLOWED in mode {self.mode!r}")
        if not isinstance(self.parameters, tuple) or not all(
            isinstance(p, tuple) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in self.parameters
        ):
            raise TypeError("parameters must be a tuple of (name, value) string pairs")
        if self.parameters or self.parameters_fingerprint:
            if self.parameters_fingerprint != _fingerprint(self.parameters):
                raise ValueError("parameters_fingerprint does not match the recorded parameters")
        elif self.verdict is Verdict.ALLOW:
            raise ValueError("an ALLOW decision must record its parameters and fingerprint")
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

    @property
    def parameters(self) -> dict[str, object]:
        return {"name": self.name, "version": self.version, "authority": "astra.risk.RiskEngine"}

    def _decision(self, mode: object, reasons: list[str]) -> MarketRiskDecision:
        pairs, fingerprint = canonical_parameters(self.parameters)
        return MarketRiskDecision(
            MarketType.FOREX_CFD,
            Verdict.REFUSE if reasons else Verdict.ALLOW,
            tuple(reasons),
            self.name,
            self.version,
            _mode_label(mode),
            notes=("pre-check only (advisory); astra.risk.RiskEngine remains the authority",),
            parameters=pairs,
            parameters_fingerprint=fingerprint,
        )


# ---------------------------------------------------------------- Crypto spot

# Provisional engineering ceilings (not calibrated, not advice). A policy may be stricter, never looser.
# These names are DOCUMENTATION ONLY: validation uses literals inside CryptoSpotRiskParameters, so patching
# a module global cannot admit a looser policy.
MAX_ORDER_NOTIONAL_FRACTION = Decimal("0.02")
MAX_DAILY_NOTIONAL_FRACTION = Decimal("0.10")
MAX_QUOTE_AGE_SECONDS = 5.0
MAX_SPEC_AGE_SECONDS = 300.0
MAX_BALANCE_AGE_SECONDS = 30.0
MAX_FEE_AGE_SECONDS = 86_400.0
MIN_SLIPPAGE_FRACTION_PER_SIDE = Decimal("0.0005")
MAX_LIMIT_DEVIATION_FRACTION = Decimal("0.01")


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
    venue_id: str
    spec: CryptoSpotSpec | None
    book: BookTicker | None
    balances: tuple[Balance, ...] | None
    fees: CryptoSpotFeeSchedule | None
    day_realized_pnl: Decimal | None
    borrowing_enabled: bool | None
    kill_switch_active: bool = False
    # Caller's view of the day's BUY total, used only as an extra floor: the policy's ledger total is always
    # applied and the larger wins. None is unknown and refuses a BUY.
    day_buy_notional: Decimal | None = None
    # Residual: position, last_buy_notional and the intent's expected_edge_fraction are caller-supplied.
    position: SpotPosition | None = None
    last_buy_notional: Decimal | None = None
    fee_asset_price_in_quote: Decimal | None = None
    fee_asset_price_as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class SpotBuyRecord:
    venue_id: str
    symbol: str
    quote_asset: str
    notional: Decimal
    recorded_at: datetime
    intent_id: str
    policy_fingerprint: str

    @property
    def utc_day(self) -> date:
        return self.recorded_at.astimezone(UTC).date()


class SpotBuyLedger:
    """Append-only record of ALLOWED spot BUY notionals; the day boundary is UTC midnight.

    There is no delete or update method. Records must not go back in time, so
    a buy cannot be booked onto an earlier day. The ledger is in memory: a
    caller that wants the daily cap to hold across policy instances or process
    restarts must share (and later persist) one ledger.
    """

    __slots__ = ("_last_time", "_lock", "_records")

    def __init__(self) -> None:
        self._records: list[SpotBuyRecord] = []
        self._lock = Lock()
        self._last_time: datetime | None = None

    @property
    def records(self) -> tuple[SpotBuyRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def day_total(self, venue_id: str, quote_asset: str, day: date) -> Decimal:
        """BUY notional for one venue and quote asset on one UTC day, across symbols."""

        with self._lock:
            return self._day_total_locked(venue_id, quote_asset, day)

    def last_buy_notional(self, venue_id: str, symbol: str) -> Decimal | None:
        with self._lock:
            for record in reversed(self._records):
                if record.venue_id == venue_id and record.symbol == symbol:
                    return record.notional
            return None

    def _day_total_locked(self, venue_id: str, quote_asset: str, day: date) -> Decimal:
        total = Decimal(0)
        for record in self._records:
            if record.venue_id == venue_id and record.quote_asset == quote_asset and record.utc_day == day:
                total += record.notional
        return total

    def _record_within_cap(self, record: SpotBuyRecord, daily_cap: Decimal, floor: Decimal) -> str | None:
        """Atomically append ``record`` if the day's total stays within ``daily_cap``; else return a reason."""

        if not isinstance(record, SpotBuyRecord):
            raise TypeError("record must be a SpotBuyRecord")
        with self._lock:
            if self._last_time is not None and record.recorded_at < self._last_time:
                return "ledger_time_regression"
            used = max(self._day_total_locked(record.venue_id, record.quote_asset, record.utc_day), floor)
            if used + record.notional > daily_cap:
                return "daily_notional_exceeds_cap"
            self._records.append(record)
            self._last_time = record.recorded_at
            return None


@dataclass(frozen=True, slots=True)
class CryptoSpotRiskParameters:
    """Reviewed spot parameters. Defaults are the reviewed values; looser values are refused.

    The ceilings are literals in ``__post_init__`` on purpose: they are not
    read from module globals, so patching a constant cannot loosen them.
    """

    max_order_notional_fraction: Decimal = Decimal("0.02")
    max_daily_notional_fraction: Decimal = Decimal("0.10")
    max_quote_age_seconds: float = 5.0
    max_spec_age_seconds: float = 300.0
    max_balance_age_seconds: float = 30.0
    max_fee_age_seconds: float = 86_400.0
    slippage_fraction_per_side: Decimal = Decimal("0.0005")
    slippage_calibration: Calibration = Calibration.UNCALIBRATED
    max_limit_deviation_fraction: Decimal = Decimal("0.01")
    leverage: int = 1
    allow_short: bool = False

    def __post_init__(self) -> None:
        for attr, ceiling in (
            ("max_order_notional_fraction", Decimal("0.02")),
            ("max_daily_notional_fraction", Decimal("0.10")),
        ):
            value = getattr(self, attr)
            if not _is_decimal(value) or not Decimal(0) < value <= ceiling:
                raise ValueError(f"{attr} must be a Decimal in (0, {ceiling}]")
        if self.max_order_notional_fraction > self.max_daily_notional_fraction:
            raise ValueError("max_order_notional_fraction must not exceed max_daily_notional_fraction")
        for attr, ceiling in (
            ("max_quote_age_seconds", 5.0),
            ("max_spec_age_seconds", 300.0),
            ("max_balance_age_seconds", 30.0),
            ("max_fee_age_seconds", 86_400.0),
        ):
            value = getattr(self, attr)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= ceiling:
                raise ValueError(f"{attr} must be within (0, {ceiling}]")
        slip = self.slippage_fraction_per_side
        if not _is_decimal(slip) or not Decimal("0.0005") <= slip < Decimal(1):
            raise ValueError("slippage_fraction_per_side must be at least the reviewed 0.0005 and below 1")
        if not isinstance(self.slippage_calibration, Calibration):
            raise ValueError("slippage_calibration must be a Calibration member")
        band = self.max_limit_deviation_fraction
        if not _is_decimal(band) or not Decimal(0) < band <= Decimal("0.01"):
            raise ValueError("max_limit_deviation_fraction must be a Decimal in (0, 0.01]")
        if isinstance(self.leverage, bool) or self.leverage != 1:
            raise ValueError("spot policy has no leverage: leverage must be 1")
        if self.allow_short is not False:
            raise ValueError("spot policy does not short: allow_short must be False")

    def canonical(self) -> tuple[Parameters, str]:
        return canonical_parameters({f: getattr(self, f) for f in self.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class CryptoSpotRiskPolicy:
    """Versioned spot policy. Values are PROVISIONAL engineering parameters, not calibrated.

    Final: subclassing raises ``TypeError``, so no override can reuse the
    reviewed name and version. The flat fields are validated through
    ``CryptoSpotRiskParameters``; every decision records those parameters and
    their sha256 fingerprint.
    """

    name: str = "crypto_spot"
    version: str = "crypto-spot-1"
    max_order_notional_fraction: Decimal = Decimal("0.02")
    max_daily_notional_fraction: Decimal = Decimal("0.10")
    max_quote_age_seconds: float = 5.0
    max_spec_age_seconds: float = 300.0
    max_balance_age_seconds: float = 30.0
    max_fee_age_seconds: float = 86_400.0
    slippage: SlippageAssumption = field(default_factory=SlippageAssumption)
    max_limit_deviation_fraction: Decimal = Decimal("0.01")
    leverage: int = 1
    allow_short: bool = False
    ledger: SpotBuyLedger = field(default_factory=SpotBuyLedger, compare=False, repr=False)
    parameters: CryptoSpotRiskParameters = field(init=False, compare=False)
    parameters_fingerprint: str = field(init=False, compare=False)
    _parameter_pairs: Parameters = field(init=False, compare=False, repr=False)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("CryptoSpotRiskPolicy is final; define a new reviewed policy instead of subclassing")

    def __post_init__(self) -> None:
        require_text(self.name, "name")
        require_text(self.version, "version")
        if not isinstance(self.slippage, SlippageAssumption):
            raise TypeError("slippage must be a SlippageAssumption")
        if not isinstance(self.ledger, SpotBuyLedger):
            raise TypeError("ledger must be a SpotBuyLedger")
        parameters = CryptoSpotRiskParameters(
            max_order_notional_fraction=self.max_order_notional_fraction,
            max_daily_notional_fraction=self.max_daily_notional_fraction,
            max_quote_age_seconds=self.max_quote_age_seconds,
            max_spec_age_seconds=self.max_spec_age_seconds,
            max_balance_age_seconds=self.max_balance_age_seconds,
            max_fee_age_seconds=self.max_fee_age_seconds,
            slippage_fraction_per_side=self.slippage.fraction_per_side,
            slippage_calibration=self.slippage.calibration,
            max_limit_deviation_fraction=self.max_limit_deviation_fraction,
            leverage=self.leverage,
            allow_short=self.allow_short,
        )
        pairs, fingerprint = parameters.canonical()
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "parameters_fingerprint", fingerprint)
        object.__setattr__(self, "_parameter_pairs", pairs)

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
        if not isinstance(context.venue_id, str) or not context.venue_id.strip():
            reasons.append("venue_unknown")

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
            daily = self._buy_checks(intent, context, spec, book, cost_model, quantity, price, notional, fee_quote,
                                     balances, reasons)
            # Every refusal check runs before booking, so a refused BUY never consumes the day's budget (MV-A-12).
            reasons.extend(self._price_band(intent.side, price, book))
            if not reasons and daily is not None:
                # Check and book atomically, so concurrent or split orders cannot all pass a stale total.
                record = SpotBuyRecord(context.venue_id, spec.symbol, spec.quote_asset, notional,
                                       context.now, intent.intent_id, self.parameters_fingerprint)
                refusal = self.ledger._record_within_cap(record, *daily)
                if refusal is not None:
                    reasons.append(refusal)
        else:
            self._sell_checks(spec, quantity, balances, reasons)
            reasons.extend(self._price_band(intent.side, price, book))

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
            parameters=self._parameter_pairs,
            parameters_fingerprint=self.parameters_fingerprint,
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
            parameters=self._parameter_pairs,
            parameters_fingerprint=self.parameters_fingerprint,
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

    def _fee_checks(
        self,
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
            # The fee asset is valued at a caller-supplied price: it must be as fresh as the quote.
            reasons.extend(_freshness("fee_asset_price", context.fee_asset_price_as_of, self.max_quote_age_seconds,
                                      context.now))
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
    ) -> tuple[Decimal, Decimal] | None:
        """Append BUY refusals; return ``(daily_cap, caller_floor)`` for the atomic ledger booking, if known."""

        stop = intent.stop_price
        if stop is None:
            reasons.append("stop_required")
        elif not _is_decimal(stop) or stop <= 0 or stop >= price:
            reasons.append("invalid_stop")

        venue_known = isinstance(context.venue_id, str) and bool(context.venue_id.strip())
        daily: tuple[Decimal, Decimal] | None = None
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
            # The day total comes from the policy's ledger; a caller value can only raise it.
            floor = context.day_buy_notional
            if floor is None:
                reasons.append("daily_notional_unknown")
            elif not _is_decimal(floor) or floor < 0:
                reasons.append("daily_notional_invalid")
            elif venue_known:
                day = context.now.astimezone(UTC).date()
                used = max(self.ledger.day_total(context.venue_id, spec.quote_asset, day), floor)
                if used + notional > daily_cap:
                    reasons.append("daily_notional_exceeds_cap")
                daily = (daily_cap, floor)

        pnl = context.day_realized_pnl
        if not _is_decimal(pnl):
            reasons.append("day_realized_pnl_unknown")
        elif pnl < 0:
            # Martingale and loss-recovery sizing: size never grows after a loss. The caller must state the last
            # BUY (unknown refuses); the ledger's last booked BUY, when smaller, bounds that claim.
            caller_last = context.last_buy_notional
            if caller_last is None:
                reasons.append("last_buy_notional_unknown")
            elif not _is_decimal(caller_last) or caller_last <= 0:
                reasons.append("last_buy_notional_invalid")
            else:
                reference = caller_last
                ledger_last = self.ledger.last_buy_notional(context.venue_id, spec.symbol) if venue_known else None
                if ledger_last is not None:
                    reference = min(reference, ledger_last)
                if notional > reference:
                    reasons.append("risk_increase_after_loss")

        position = context.position
        base_balance = None if balances is None else balances.get(spec.base_asset)
        if position is None and base_balance is not None and base_balance.total > 0:
            # Holdings exist but their entry price and stop are unknown: adding to a loser cannot be ruled out.
            reasons.append("position_unknown")
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
        return daily

    def _price_band(self, side: SpotSide, price: Decimal, book: BookTicker) -> list[str]:
        """A limit may not cross the touch by more than the band: no BUY far above the ask, no SELL far below the bid."""

        with localcontext() as ctx:
            ctx.prec = _PRECISION
            band = self.max_limit_deviation_fraction
            if side is SpotSide.BUY and price > book.ask_price * (1 + band):
                return ["limit_price_outside_band"]
            if side is SpotSide.SELL and price < book.bid_price * (1 - band):
                return ["limit_price_outside_band"]
        return []

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
            MarketType.CRYPTO_SPOT, Verdict.REFUSE, tuple(reasons) or ("refused",), self.name, self.version, mode,
            parameters=self._parameter_pairs, parameters_fingerprint=self.parameters_fingerprint,
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
        pairs, fingerprint = canonical_parameters({"name": self.name, "version": self.version, "allow": "never"})
        return MarketRiskDecision(
            MarketType.CRYPTO_FUTURES, Verdict.REFUSE, tuple(reasons), self.name, self.version, _mode_label(mode),
            parameters=pairs, parameters_fingerprint=fingerprint,
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
        pairs, fingerprint = canonical_parameters({"name": self.name, "version": self.version, "allow": "never"})
        return MarketRiskDecision(
            MarketType.FIXED_EXPIRY_OPTIONS, Verdict.REFUSE, tuple(reasons), self.name, self.version, _mode_label(mode),
            parameters=pairs, parameters_fingerprint=fingerprint,
        )


MarketRiskPolicy = ForexCfdPreCheck | CryptoSpotRiskPolicy | CryptoFuturesRiskPolicy | FixedExpiryOptionsRiskPolicy


# One process-wide BUY ledger for the default spot policy, so the daily cap holds across policy_for() calls
# (MV-A-11). It is in memory: a restart starts empty until a persisted ledger exists (documented residual).
_DEFAULT_SPOT_LEDGER = SpotBuyLedger()


def reset_default_spot_ledger_for_tests() -> None:
    """TEST ONLY: replace the process-wide default spot ledger with an empty one."""

    global _DEFAULT_SPOT_LEDGER
    _DEFAULT_SPOT_LEDGER = SpotBuyLedger()


def policy_for(market_type: MarketType) -> MarketRiskPolicy:
    """Default policy instance per market type; there is no shared fallback.

    The crypto spot policy shares the process-wide BUY ledger.
    """

    if market_type is MarketType.FOREX_CFD:
        return ForexCfdPreCheck()
    if market_type is MarketType.CRYPTO_SPOT:
        return CryptoSpotRiskPolicy(ledger=_DEFAULT_SPOT_LEDGER)
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
    "MAX_LIMIT_DEVIATION_FRACTION",
    "MAX_ORDER_NOTIONAL_FRACTION",
    "MAX_QUOTE_AGE_SECONDS",
    "MAX_SPEC_AGE_SECONDS",
    "MIN_SLIPPAGE_FRACTION_PER_SIDE",
    "OPTIONS_REVIEW_REQUIREMENTS",
    "CryptoFuturesRiskPolicy",
    "CryptoSpotOrderIntent",
    "CryptoSpotRiskContext",
    "CryptoSpotRiskParameters",
    "CryptoSpotRiskPolicy",
    "FixedExpiryOptionsRiskPolicy",
    "ForexCfdPreCheck",
    "MarketRiskDecision",
    "MarketRiskPolicy",
    "Parameters",
    "SpotBuyLedger",
    "SpotBuyRecord",
    "SpotPosition",
    "Verdict",
    "canonical_parameters",
    "policy_for",
]
