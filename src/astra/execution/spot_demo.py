"""Risk checks for crypto spot orders on a DEMO account (Binance Spot Demo Mode). SIMULATION and LIVE never use it.

ASTRA does not treat the whole demo wallet as its capital: it works on an explicit allocation (for example
1 000 USDT) and its own orders only. ASTRA equity = allocation + net cash flow of its own fills (sells minus
buys, net of quote fees) + its net base quantity valued at the bid. Every limit below is measured on that.

Spot is long or flat: an ENTRY must be a BUY with a stop below the price; an EXIT sells at most what ASTRA
holds. Limits have literal ceilings, so a configuration cannot loosen them past what this review accepted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from ..markets.crypto_spot import CryptoSpotSpec

POLICY_VERSION = "spot-demo-1"
_CEILINGS = {
    "risk_per_trade": (0.0001, 0.01),          # fraction of ASTRA equity lost if the stop is hit
    "max_exposure_fraction": (0.01, 0.5),      # notional of one instrument / ASTRA equity
    "max_positions": (1, 3),
    "daily_loss_limit": (0.001, 0.05),
    "max_drawdown": (0.01, 0.2),
    "max_data_age_seconds": (0.5, 10.0),
    "max_spread_fraction": (0.00001, 0.01),
    "allocation": (10.0, 100_000.0),
}


class SpotPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SpotDemoPolicy:
    allocation: float = 1000.0
    risk_per_trade: float = 0.005
    max_exposure_fraction: float = 0.25
    max_positions: int = 1
    daily_loss_limit: float = 0.02
    max_drawdown: float = 0.1
    max_data_age_seconds: float = 5.0
    max_spread_fraction: float = 0.002
    version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        for item in fields(self):
            if item.name == "version":
                continue
            value = getattr(self, item.name)
            low, high = _CEILINGS[item.name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                    or not low <= value <= high:
                raise SpotPolicyError(f"{item.name} must lie within [{low}, {high}]")
        if self.version != POLICY_VERSION:
            raise SpotPolicyError("unknown policy version")

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class SpotLedger:
    """ASTRA's own trading in one symbol, from the provider's history."""

    net_base: Decimal
    cash_flow_quote: Decimal      # sells minus buys, minus quote-asset fees
    fees_quote: Decimal

    def equity(self, allocation: float, bid: float) -> float:
        return float(Decimal(str(allocation)) + self.cash_flow_quote + self.net_base * Decimal(str(bid)))


def ledger_from_trades(trades: list[dict[str, Any]], own_order_ids: set[Any], base_asset: str,
                       quote_asset: str) -> SpotLedger:
    net, cash, fees = Decimal(0), Decimal(0), Decimal(0)
    for trade in trades:
        if trade.get("orderId") not in own_order_ids:
            continue
        qty, quote_qty = Decimal(str(trade["qty"])), Decimal(str(trade["quoteQty"]))
        commission, asset = Decimal(str(trade.get("commission", "0"))), trade.get("commissionAsset")
        if trade.get("isBuyer"):
            net += qty - (commission if asset == base_asset else 0)
            cash -= quote_qty
        else:
            net -= qty
            cash += quote_qty
        if asset == quote_asset:
            cash -= commission
            fees += commission
        elif asset == base_asset:
            fees += commission * (quote_qty / qty if qty else Decimal(0))
    return SpotLedger(net, cash, fees)


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def entry_checks(*, policy: SpotDemoPolicy, spec: CryptoSpotSpec, bid: float, ask: float, data_age: float,
                 ledger: SpotLedger, equity: float, day_start_equity: float, peak_equity: float,
                 open_symbols: int, free_quote: float, stop_price: float | None, direction_long: bool,
                 requested_units: float | None) -> tuple[tuple[str, ...], Decimal]:
    """Reasons to refuse a spot BUY entry, or () and the approved quantity (on the provider's step)."""

    reasons: list[str] = []
    if not direction_long:
        return ("spot_short_refused",), Decimal(0)
    if not spec.is_trading:
        reasons.append("instrument_not_trading")
    if data_age < -1 or data_age > policy.max_data_age_seconds:
        reasons.append("data_stale_or_unknown")
    spread = (ask - bid) / ask if ask > 0 else math.inf
    if spread > policy.max_spread_fraction:
        reasons.append("spread_excessive")
    if stop_price is None or not math.isfinite(stop_price) or stop_price <= 0 or stop_price >= ask:
        reasons.append("stop_must_be_below_entry")
    if day_start_equity > 0 and (day_start_equity - equity) / day_start_equity >= policy.daily_loss_limit:
        reasons.append("daily_loss_limit_reached")
    if peak_equity > 0 and (peak_equity - equity) / peak_equity >= policy.max_drawdown:
        reasons.append("max_drawdown_reached")
    if ledger.net_base > spec.step_size:
        reasons.append("position_already_open")
    if open_symbols >= policy.max_positions:
        reasons.append("position_limit_reached")
    if reasons:
        return tuple(reasons), Decimal(0)
    entry = Decimal(str(ask))
    risk_quote = Decimal(str(equity * policy.risk_per_trade))
    by_risk = risk_quote / (entry - Decimal(str(stop_price)))
    by_exposure = Decimal(str(equity * policy.max_exposure_fraction)) / entry
    deployed = max(Decimal(0), -ledger.cash_flow_quote)
    by_cash = min(Decimal(str(free_quote)), Decimal(str(policy.allocation)) - deployed) / (entry * Decimal("1.002"))
    wanted = min(by_risk, by_exposure, by_cash)
    if requested_units is not None:
        requested = Decimal(str(requested_units))
        if requested > wanted:
            return ("requested_size_exceeds_limits",), Decimal(0)
        wanted = requested
    quantity = floor_step(wanted, spec.step_size)
    violations = spec.order_violations(quantity, entry)
    if quantity <= 0 or violations:
        return (tuple(violations) or ("size_below_minimum",)), Decimal(0)
    return (), quantity


def exit_quantity(spec: CryptoSpotSpec, ledger: SpotLedger, free_base: float,
                  requested_units: float | None) -> tuple[tuple[str, ...], Decimal]:
    held = min(ledger.net_base, Decimal(str(free_base)))
    if requested_units is not None:
        held = min(held, Decimal(str(requested_units)))
    quantity = floor_step(held, spec.step_size)
    if quantity <= 0 or quantity < spec.min_qty:
        return ("no_position_to_reduce",), Decimal(0)
    return (), quantity


def day_key(account_ref: str, now: datetime) -> str:
    return f"spot_day_start_equity:{account_ref}:{now.date().isoformat()}"


def peak_key(account_ref: str) -> str:
    return f"spot_peak_equity:{account_ref}"
