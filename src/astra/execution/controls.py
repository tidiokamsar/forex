"""Emergency stop and entry-freeze controls, persisted and audited."""

from __future__ import annotations

from enum import StrEnum

from ..governance import Action, Actor, PermissionDenied, PermissionGuard
from .orders import ExecutionStore


class FreezeReason(StrEnum):
    CONNECTION_LOST = "connection_lost"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    POSITION_DIVERGENCE = "position_divergence"
    MODE_MISMATCH = "mode_mismatch"
    UNKNOWN_ORDER_STATE = "unknown_order_state"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    CLOCK_INCONSISTENT = "clock_inconsistent"
    OPERATOR = "operator"


# Reasons that only a successful reconciliation may clear.
RECONCILIATION_OWNED = frozenset(
    {
        FreezeReason.CONNECTION_LOST,
        FreezeReason.RECONCILIATION_REQUIRED,
        FreezeReason.POSITION_DIVERGENCE,
        FreezeReason.UNKNOWN_ORDER_STATE,
    }
)

_KILL_SWITCH = "kill_switch"
_FREEZES = "entry_freezes"


class TradingControls:
    """Kill switch and entry freezes.

    The kill switch is global: it blocks every new order on every account,
    including automated exits, and only an authorized human can release it.
    Entry freezes block new entries but leave risk-reducing exits possible.
    With ``scope`` (an account reference) freezes are raised and cleared for
    that account only; reads return global freezes plus the scope's own.
    """

    def __init__(self, store: ExecutionStore, guard: PermissionGuard | None = None, scope: str | None = None) -> None:
        self._store = store
        self._guard = guard or PermissionGuard(store)
        self.scope = scope
        self._key = _FREEZES if scope is None else f"{_FREEZES}:{scope}"

    def kill_switch_active(self) -> bool:
        state = self._store.get_control(_KILL_SWITCH, {"active": False})
        return bool(state.get("active", False))

    def activate_kill_switch(self, actor: Actor | str, reason: str) -> None:
        # Anyone, including automated monitors, may stop trading.
        actor_id = actor.actor_id if isinstance(actor, Actor) else str(actor)
        self._store.set_control(
            _KILL_SWITCH,
            {"active": True, "reason": reason},
            actor=actor_id,
            audit_type="control.kill_switch_activated",
            details={"reason": reason},
        )

    def release_kill_switch(self, actor: Actor, reason: str) -> None:
        self._guard.require(actor, Action.RELEASE_KILL_SWITCH, target="kill_switch")
        if not reason.strip():
            raise PermissionDenied("a release reason is required")
        self._store.set_control(
            _KILL_SWITCH,
            {"active": False, "reason": reason},
            actor=actor.actor_id,
            audit_type="control.kill_switch_released",
            details={"reason": reason},
        )

    def freezes(self) -> tuple[str, ...]:
        """Global freezes plus this scope's own."""

        codes = set(self._store.get_control(_FREEZES, []))
        if self.scope is not None:
            codes |= set(self._store.get_control(self._key, []))
        return tuple(sorted(codes))

    def own_freezes(self) -> tuple[str, ...]:
        return tuple(sorted(self._store.get_control(self._key, [])))

    def all_freezes(self) -> dict[str, list[str]]:
        """Active freezes per scope; the key "*" holds global freezes."""

        result = {}
        for key, value in self._store.controls_with_prefix(_FREEZES):
            if value:
                result["*" if key == _FREEZES else key.split(":", 1)[1]] = sorted(value)
        return result

    def freeze(self, reason: FreezeReason | str, actor: str = "system", context: str = "") -> None:
        current = set(self.own_freezes())
        code = str(reason)
        if code in current:
            return
        current.add(code)
        self._store.set_control(
            self._key,
            sorted(current),
            actor=actor,
            audit_type="control.entries_frozen",
            details={"reason": code, "context": context, "scope": self.scope or "*"},
        )

    def clear_freeze(self, reason: FreezeReason | str, actor: Actor | str, *, by_reconciliation: bool = False) -> None:
        code = str(reason)
        if code in {item.value for item in RECONCILIATION_OWNED} and not by_reconciliation:
            raise PermissionDenied(f"{code} can only be cleared by a consistent reconciliation")
        if isinstance(actor, Actor) and not by_reconciliation:
            self._guard.require(actor, Action.CLEAR_FREEZE, target=code)
        actor_id = actor.actor_id if isinstance(actor, Actor) else str(actor)
        current = set(self.own_freezes())
        if code not in current:
            return
        current.discard(code)
        self._store.set_control(
            self._key,
            sorted(current),
            actor=actor_id,
            audit_type="control.freeze_cleared",
            details={"reason": code, "scope": self.scope or "*"},
        )

    def entry_blockers(self) -> tuple[str, ...]:
        blockers = list(self.freezes())
        if self.kill_switch_active():
            blockers.insert(0, "kill_switch_active")
        return tuple(blockers)
