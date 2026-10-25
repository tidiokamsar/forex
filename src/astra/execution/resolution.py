"""Human resolution of orders whose outcome ASTRA could not determine.

An order in ``UNKNOWN`` (or stuck in ``SUBMITTED``) freezes entries. When the
adapter cannot settle it (for example cTrader has no lookup by client order
id), an operator records what the provider's own records show, with
evidence. Resolution does not lift any freeze: only a consistent
reconciliation of positions does that afterwards.
"""

from __future__ import annotations

import math

from ..brokers.base import BrokerOrderReport
from ..governance import Action, Actor, PermissionGuard
from .orders import ExecutionStore, OrderRecord, OrderState


OUTCOMES = ("FILLED", "NOT_EXECUTED")
RESOLVABLE_STATES = frozenset({OrderState.UNKNOWN, OrderState.SUBMITTED})


class ResolutionRefused(ValueError):
    pass


def resolve_unknown_order(
    *,
    store: ExecutionStore,
    guard: PermissionGuard,
    actor: Actor,
    intent_id: str,
    outcome: str,
    evidence: str,
    filled_units: float | None = None,
    average_fill_price: float | None = None,
    broker_report: BrokerOrderReport | None = None,
    broker_lookup: str = "not_performed",
) -> OrderRecord:
    guard.require(actor, Action.RESOLVE_ORDER, target=intent_id)
    record = store.get_order(intent_id)
    if record is None:
        raise ResolutionRefused("unknown intent id")
    if record.state not in RESOLVABLE_STATES:
        raise ResolutionRefused(f"only UNKNOWN or SUBMITTED orders can be resolved, not {record.state.value}")
    if outcome not in OUTCOMES:
        raise ResolutionRefused(f"outcome must be one of {OUTCOMES}")
    if not isinstance(evidence, str) or len(evidence.strip()) < 8:
        raise ResolutionRefused("evidence is required (provider deal/order id, statement reference)")
    if broker_report is not None:
        raise ResolutionRefused(
            f"the adapter reports {broker_report.status.value} for this order; reconcile instead of resolving by hand"
        )
    details = {"actor": actor.actor_id, "evidence": evidence.strip(), "outcome": outcome, "broker_lookup": broker_lookup}
    if outcome == "FILLED":
        if not _positive(filled_units) or filled_units > record.requested_units + 1e-9:
            raise ResolutionRefused("filled_units must be positive and at most the requested units")
        if not _positive(average_fill_price):
            raise ResolutionRefused("average_fill_price must be positive")
        target = OrderState.FILLED if abs(filled_units - record.requested_units) < 1e-9 else OrderState.PARTIALLY_FILLED
        updates = {
            "filled_units": filled_units,
            "average_fill_price": average_fill_price,
            "reason_codes": ("resolved_by_operator",),
        }
        if target is OrderState.PARTIALLY_FILLED:
            # A partial fill with no live order left is terminal from ASTRA's side.
            store.transition(intent_id, OrderState.PARTIALLY_FILLED, reason="resolved_by_operator", updates=updates, details=details)
            resolved = store.transition(
                intent_id, OrderState.CANCELLED, reason="remainder_cancelled_by_operator_resolution", details=details
            )
        else:
            resolved = store.transition(intent_id, target, reason="resolved_by_operator", updates=updates, details=details)
    else:
        resolved = store.transition(
            intent_id,
            OrderState.CANCELLED,
            reason="resolved_not_executed_by_operator",
            updates={"reason_codes": ("resolved_not_executed_by_operator",)},
            details=details,
        )
    store.audit("order.resolved_by_operator", {"intent_id": intent_id, **details})
    return resolved


def _positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
