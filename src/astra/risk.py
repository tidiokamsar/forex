"""Independent fail-closed risk checks for simulated entry intents."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from threading import Lock

from .models import (
    Direction,
    OrderIntent,
    RiskContext,
    RiskDecision,
    RiskPolicy,
    RunMode,
)


class RiskEngine:
    """Validate simulation intents; this class has no broker execution methods."""

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        requested_policy = RiskPolicy() if policy is None else policy
        self._validate_policy(requested_policy)
        self._policy = replace(
            requested_policy,
            allowed_instruments=tuple(
                symbol.strip().upper()
                for symbol in requested_policy.allowed_instruments
            ),
        )
        self._seen_intents: set[str] = set()
        self._lock = Lock()

    @property
    def policy(self) -> RiskPolicy:
        """Return the validated immutable policy snapshot used by this engine."""

        return self._policy

    def evaluate(self, intent: OrderIntent, context: RiskContext) -> RiskDecision:
        if not isinstance(intent, OrderIntent):
            return RiskDecision(False, ("invalid_order_intent",))
        if not isinstance(context, RiskContext):
            return RiskDecision(False, ("invalid_risk_context",))
        with self._lock:
            intent_key = intent.intent_id if isinstance(intent.intent_id, str) else ""
            if intent_key in self._seen_intents and intent_key:
                return RiskDecision(False, ("duplicate_intent",))
            # Reserve the id before the remaining checks so retries after a
            # timeout or rejection cannot be treated as a fresh intent.
            if intent_key:
                self._seen_intents.add(intent_key)

        reasons: list[str] = []
        policy = self.policy

        if not isinstance(intent.intent_id, str) or not intent.intent_id.strip():
            reasons.append("invalid_intent_id")
        if context.mode is not RunMode.SIMULATION:
            reasons.append("mode_not_supported")
        if type(context.account_verified) is not bool:
            reasons.append("account_verification_invalid")
        elif context.account_mode is not context.mode or context.account_verified is not True:
            reasons.append("account_mode_unverified")
        if (
            not isinstance(intent.created_at, datetime)
            or intent.created_at.tzinfo is None
            or intent.created_at.utcoffset() is None
        ):
            reasons.append("invalid_intent_timestamp")
        if not _finite_number(intent.entry_price) or intent.entry_price <= 0:
            reasons.append("invalid_entry_price")
        if not _finite_number(intent.stop_price) or intent.stop_price <= 0:
            reasons.append("invalid_stop_price")
        if not isinstance(intent.direction, Direction):
            reasons.append("invalid_direction")
        if _finite_number(intent.entry_price) and _finite_number(intent.stop_price) and isinstance(
            intent.direction, Direction
        ):
            if intent.direction is Direction.LONG and intent.stop_price >= intent.entry_price:
                reasons.append("invalid_stop_direction")
            if intent.direction is Direction.SHORT and intent.stop_price <= intent.entry_price:
                reasons.append("invalid_stop_direction")

        if (
            context.data_age_seconds is None
            or not _finite_number(context.data_age_seconds)
            or context.data_age_seconds < 0
            or context.data_age_seconds > policy.max_data_age_seconds
        ):
            reasons.append("data_stale_or_unknown")
        if (
            context.spread_pips is None
            or not _finite_number(context.spread_pips)
            or context.spread_pips < 0
            or context.spread_pips > policy.max_spread_pips
        ):
            reasons.append("spread_excessive_or_unknown")
        allowed_instruments = {symbol.strip().upper() for symbol in policy.allowed_instruments}
        if (
            not isinstance(intent.instrument, str)
            or not intent.instrument.strip()
            or intent.instrument.strip().upper() not in allowed_instruments
        ):
            reasons.append("instrument_not_allowed")
        if type(context.instrument_specs_known) is not bool:
            reasons.append("instrument_specs_state_invalid")
        elif context.instrument_specs_known is not True:
            reasons.append("instrument_specs_unknown")
        if type(context.margin_available) is not bool:
            reasons.append("margin_state_invalid")
        elif context.margin_available is not True:
            reasons.append("margin_unavailable")
        if (
            not isinstance(context.open_positions, int)
            or isinstance(context.open_positions, bool)
            or context.open_positions < 0
        ):
            reasons.append("position_state_invalid")
        elif context.open_positions >= policy.max_open_positions:
            reasons.append("position_limit_reached")
        if type(context.kill_switch_active) is not bool:
            reasons.append("kill_switch_state_invalid")
        elif context.kill_switch_active is True:
            reasons.append("kill_switch_active")
        if (
            not _finite_number(context.equity)
            or context.equity <= 0
            or not _finite_number(context.day_start_equity)
            or context.day_start_equity <= 0
            or not _finite_number(context.day_net_pnl)
        ):
            reasons.append("account_equity_invalid")
        elif context.day_net_pnl <= -(context.day_start_equity * policy.daily_loss_limit):
            reasons.append("daily_loss_limit_reached")

        stop_distance = (
            abs(intent.entry_price - intent.stop_price)
            if _finite_number(intent.entry_price) and _finite_number(intent.stop_price)
            else 0.0
        )
        if not _finite_number(stop_distance) or stop_distance <= 0:
            reasons.append("invalid_stop_distance")

        if reasons:
            return RiskDecision(False, tuple(dict.fromkeys(reasons)))

        risk_amount = context.equity * policy.risk_per_position
        approved_units = risk_amount / stop_distance
        if intent.requested_units is not None:
            if not _finite_number(intent.requested_units) or intent.requested_units <= 0:
                return RiskDecision(False, ("invalid_requested_size",))
            if intent.requested_units > approved_units:
                return RiskDecision(False, ("requested_size_exceeds_risk",))
            approved_units = intent.requested_units
            risk_amount = approved_units * stop_distance

        if not _finite_number(approved_units) or approved_units <= 0:
            return RiskDecision(False, ("invalid_approved_size",))
        return RiskDecision(True, (), approved_units=approved_units, risk_amount=risk_amount)

    @staticmethod
    def _validate_policy(policy: RiskPolicy) -> None:
        if not isinstance(policy, RiskPolicy):
            raise ValueError("policy must be a RiskPolicy")
        if (
            not _finite_number(policy.risk_per_position)
            or not 0 < policy.risk_per_position <= 0.0025
        ):
            raise ValueError("risk_per_position must be within (0, 0.0025]")
        if (
            not _finite_number(policy.daily_loss_limit)
            or not 0 < policy.daily_loss_limit <= 0.01
        ):
            raise ValueError("daily_loss_limit must be within (0, 0.01]")
        if (
            not isinstance(policy.max_open_positions, int)
            or isinstance(policy.max_open_positions, bool)
            or not 1 <= policy.max_open_positions <= 1
        ):
            raise ValueError("max_open_positions must be 1")
        if (
            not _finite_number(policy.max_data_age_seconds)
            or not 0 < policy.max_data_age_seconds <= 120
        ):
            raise ValueError("max_data_age_seconds must be within (0, 120]")
        if (
            not _finite_number(policy.max_spread_pips)
            or not 0 <= policy.max_spread_pips <= 3
        ):
            raise ValueError("max_spread_pips must be within [0, 3]")
        if not _finite_number(policy.pip_size) or policy.pip_size <= 0:
            raise ValueError("pip_size must be finite and positive")
        if not isinstance(policy.allowed_instruments, (tuple, list)) or not policy.allowed_instruments or any(
            not isinstance(symbol, str) or not symbol.strip()
            for symbol in policy.allowed_instruments
        ):
            raise ValueError("allowed_instruments must contain at least one symbol")
        if any(symbol.strip().upper() != "EURUSD" for symbol in policy.allowed_instruments):
            raise ValueError("allowed_instruments is limited to EURUSD in this build")


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
