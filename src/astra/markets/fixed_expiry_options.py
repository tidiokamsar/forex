"""Fixed-expiry (binary-style) option data models (disabled).

Data models only. ``ENABLED`` is False: no backtest, risk or execution path
uses these classes yet, and nothing in ASTRA may simulate or send an option
from them. Fixed-expiry products have a payoff and a regulatory profile unlike
Forex CFDs (in several jurisdictions they are restricted or prohibited for
retail clients), so they need their own review before any use.

``settlement_amount`` only states the contract rule recorded in the spec; it
is pure arithmetic and is not used for sizing or for any decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .common import require_aware, require_symbol, require_text, to_decimal

ENABLED = False


class OptionOutcome(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    TIE = "TIE"


class TieRule(StrEnum):
    REFUND_PREMIUM = "REFUND_PREMIUM"
    LOSS = "LOSS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class FixedExpiryOptionSpec:
    symbol: str
    underlying: str
    premium_currency: str
    expiries_seconds: tuple[int, ...]
    payout_rate: Decimal
    loss_rate: Decimal
    min_premium: Decimal
    max_premium: Decimal
    tie_rule: TieRule
    source: str
    as_of: datetime

    def __post_init__(self) -> None:
        require_symbol(self.symbol)
        require_symbol(self.underlying, "underlying")
        require_symbol(self.premium_currency, "premium_currency")
        if not isinstance(self.expiries_seconds, tuple) or not self.expiries_seconds:
            raise ValueError("expiries_seconds must be a non-empty tuple")
        for value in self.expiries_seconds:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("every expiry must be a positive integer number of seconds")
        for name in ("payout_rate", "loss_rate", "min_premium", "max_premium"):
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        # payout_rate: profit on a win as a fraction of premium (0.8 = 80 %); loss_rate: premium share lost.
        if self.payout_rate <= 0:
            raise ValueError("payout_rate must be positive")
        if not Decimal(0) < self.loss_rate <= Decimal(1):
            raise ValueError("loss_rate must be in (0, 1]")
        if self.min_premium <= 0 or self.min_premium > self.max_premium:
            raise ValueError("premiums must satisfy 0 < min_premium <= max_premium")
        if not isinstance(self.tie_rule, TieRule):
            raise TypeError("tie_rule must be a TieRule")
        require_text(self.source, "source")
        require_aware(self.as_of, "as_of")

    def settlement_amount(self, premium: object, outcome: OptionOutcome) -> Decimal:
        """Signed profit or loss of one contract under the recorded rule."""

        amount = to_decimal(premium, "premium")
        if not self.min_premium <= amount <= self.max_premium:
            raise ValueError("premium outside the recorded limits")
        if outcome is OptionOutcome.WIN:
            return amount * self.payout_rate
        if outcome is OptionOutcome.LOSS:
            return -amount * self.loss_rate
        if outcome is OptionOutcome.TIE:
            if self.tie_rule is TieRule.REFUND_PREMIUM:
                return Decimal(0)
            if self.tie_rule is TieRule.LOSS:
                return -amount * self.loss_rate
            raise ValueError("tie rule unknown for this spec")
        raise TypeError("outcome must be an OptionOutcome")
