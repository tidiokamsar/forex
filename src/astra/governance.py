"""Permissions, secret access, critical-limit changes and LLM output handling.

Models, strategies, research agents and LLMs are non-human actors. They may
propose; they cannot read secrets, change risk limits or modes, release the
kill switch, promote models, or reach the order gateway directly.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from .models import RiskPolicy
from .risk import RiskEngine


class Role(StrEnum):
    HUMAN_ADMIN = "HUMAN_ADMIN"
    HUMAN_OPERATOR = "HUMAN_OPERATOR"
    HUMAN_REVIEWER = "HUMAN_REVIEWER"
    RESEARCH_AGENT = "RESEARCH_AGENT"
    STRATEGY = "STRATEGY"
    MODEL = "MODEL"
    LLM = "LLM"
    SYSTEM = "SYSTEM"


HUMAN_ROLES = frozenset({Role.HUMAN_ADMIN, Role.HUMAN_OPERATOR, Role.HUMAN_REVIEWER})


class Action(StrEnum):
    READ_SECRET = "read_secret"
    MODIFY_RISK_LIMIT = "modify_risk_limit"
    CHANGE_MODE = "change_mode"
    RELEASE_KILL_SWITCH = "release_kill_switch"
    CLEAR_FREEZE = "clear_freeze"
    RESOLVE_ORDER = "resolve_order"
    RESTORE_BACKUP = "restore_backup"
    PROMOTE_MODEL = "promote_model"
    ROLLBACK_MODEL = "rollback_model"
    REGISTER_CANDIDATE = "register_candidate"
    PROPOSE_RESEARCH = "propose_research"
    SUBMIT_ORDER_COMMAND = "submit_order_command"
    GRANT_PERMISSION = "grant_permission"


# Static policy table. It is code, not configuration, so no actor can widen it
# at runtime; changing it requires a reviewed code change.
_POLICY: dict[Action, frozenset[Role]] = {
    Action.READ_SECRET: frozenset({Role.HUMAN_ADMIN}),
    Action.MODIFY_RISK_LIMIT: frozenset({Role.HUMAN_ADMIN}),
    Action.CHANGE_MODE: frozenset({Role.HUMAN_ADMIN}),
    Action.RELEASE_KILL_SWITCH: frozenset({Role.HUMAN_ADMIN, Role.HUMAN_OPERATOR}),
    Action.CLEAR_FREEZE: frozenset({Role.HUMAN_ADMIN, Role.HUMAN_OPERATOR}),
    Action.RESOLVE_ORDER: frozenset({Role.HUMAN_ADMIN, Role.HUMAN_OPERATOR}),
    Action.RESTORE_BACKUP: frozenset({Role.HUMAN_ADMIN, Role.HUMAN_OPERATOR}),
    Action.PROMOTE_MODEL: frozenset({Role.HUMAN_ADMIN, Role.HUMAN_REVIEWER}),
    Action.ROLLBACK_MODEL: frozenset({Role.HUMAN_ADMIN, Role.HUMAN_OPERATOR, Role.SYSTEM}),
    Action.REGISTER_CANDIDATE: frozenset(
        {Role.HUMAN_ADMIN, Role.HUMAN_REVIEWER, Role.RESEARCH_AGENT, Role.SYSTEM}
    ),
    Action.PROPOSE_RESEARCH: frozenset(
        {Role.HUMAN_ADMIN, Role.HUMAN_REVIEWER, Role.RESEARCH_AGENT, Role.LLM, Role.SYSTEM}
    ),
    # Only the deterministic pipeline submits commands; it still goes through
    # schema, mode, risk and account checks inside the gateway.
    Action.SUBMIT_ORDER_COMMAND: frozenset({Role.SYSTEM, Role.HUMAN_OPERATOR, Role.HUMAN_ADMIN}),
    Action.GRANT_PERMISSION: frozenset(),
}


class PermissionDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    role: Role

    @property
    def is_human(self) -> bool:
        return self.role in HUMAN_ROLES


class AuditSink(Protocol):
    def audit(self, event_type: str, details: dict[str, Any]) -> None: ...


class PermissionGuard:
    def __init__(self, audit_sink: AuditSink | None = None) -> None:
        self._sink = audit_sink

    def allowed(self, actor: Actor, action: Action) -> bool:
        if not isinstance(actor, Actor) or not isinstance(actor.role, Role):
            return False
        return actor.role in _POLICY.get(action, frozenset())

    def require(self, actor: Actor, action: Action, *, target: str = "") -> None:
        if self.allowed(actor, action):
            self._audit("permission.granted", actor, action, target)
            return
        self._audit("permission.denied", actor, action, target)
        role = getattr(actor, "role", "invalid")
        raise PermissionDenied(f"{role} may not {action.value}")

    def _audit(self, event_type: str, actor: Actor, action: Action, target: str) -> None:
        if self._sink is None:
            return
        self._sink.audit(
            event_type,
            {
                "actor": getattr(actor, "actor_id", "invalid"),
                "role": str(getattr(actor, "role", "invalid")),
                "action": action.value,
                "target": target,
            },
        )


class SecretValue:
    """Opaque secret wrapper whose repr and str never reveal the value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        # Anything revealed is masked in logs from now on, even inside messages.
        from .logs import register_secret

        register_secret(self._value)
        return self._value

    def __repr__(self) -> str:
        return "SecretValue(****)"

    __str__ = __repr__

    def __reduce__(self):
        # Pickling would write the plaintext (multiprocessing, caches, crash dumps); refuse it.
        raise TypeError("SecretValue cannot be pickled")

    def __reduce_ex__(self, protocol):
        raise TypeError("SecretValue cannot be pickled")

    def __copy__(self) -> SecretValue:
        # Immutable wrapper: a copy is the same object, so no new plaintext holder is created.
        return self

    def __deepcopy__(self, memo: dict) -> SecretValue:
        return self


class SecretVault:
    """Reads secrets from the process environment for authorized humans only.

    Nothing in ASTRA currently needs a secret; this exists so connectors have
    a single audited access path. Values are never logged.
    """

    PREFIX = "ASTRA_SECRET_"

    def __init__(self, guard: PermissionGuard, environ: dict[str, str] | None = None) -> None:
        self._guard = guard
        self._environ = os.environ if environ is None else environ

    def get(self, actor: Actor, name: str) -> SecretValue | None:
        self._guard.require(actor, Action.READ_SECRET, target=name)
        value = self._environ.get(self.PREFIX + name.upper())
        return SecretValue(value) if value else None


def approve_risk_policy_change(
    *,
    guard: PermissionGuard,
    requester: Actor,
    reviewer: Actor,
    current: RiskPolicy,
    proposed: RiskPolicy,
    reason: str,
) -> RiskPolicy:
    """Two-person rule for critical risk limits.

    The requester must be an admin, the reviewer a different human, and the
    proposed policy must still pass the RiskEngine's built-in caps. Approval
    never widens those caps; it only selects values inside them.
    """

    guard.require(requester, Action.MODIFY_RISK_LIMIT, target="risk_policy")
    if not reviewer.is_human:
        guard.require(reviewer, Action.MODIFY_RISK_LIMIT, target="risk_policy:review")
    if reviewer.actor_id == requester.actor_id:
        guard._audit("permission.denied", reviewer, Action.MODIFY_RISK_LIMIT, "risk_policy:self_review")
        raise PermissionDenied("a distinct human reviewer is required")
    if not reason.strip():
        raise PermissionDenied("a documented reason is required")
    RiskEngine(proposed)  # raises if outside the hard caps
    if guard._sink is not None:
        guard._sink.audit(
            "risk_policy.change_approved",
            {
                "requester": requester.actor_id,
                "reviewer": reviewer.actor_id,
                "reason": reason,
                "from": _policy_dict(current),
                "to": _policy_dict(proposed),
            },
        )
    return replace(proposed)


def _policy_dict(policy: RiskPolicy) -> dict[str, Any]:
    return {
        "risk_per_position": policy.risk_per_position,
        "daily_loss_limit": policy.daily_loss_limit,
        "max_open_positions": policy.max_open_positions,
        "max_data_age_seconds": policy.max_data_age_seconds,
        "max_spread_pips": policy.max_spread_pips,
        "allowed_instruments": list(policy.allowed_instruments),
    }


# -- LLM output handling -------------------------------------------------------

LLM_SCHEMA_VERSION = "astra.llm_advisory.v1"
_LLM_KEYS = frozenset({"schema", "instrument", "bias", "confidence", "rationale"})
_LLM_BIASES = frozenset({"LONG", "SHORT", "NEUTRAL"})
_FORBIDDEN_LLM_KEYS = frozenset(
    {"units", "size", "lots", "order", "execute", "risk", "stop", "leverage", "mode", "account"}
)


@dataclass(frozen=True, slots=True)
class LlmAdvisory:
    instrument: str
    bias: str
    confidence: float
    rationale: str


@dataclass(frozen=True, slots=True)
class LlmHandlingResult:
    """Outcome of LLM output parsing. ``action`` is never an execution."""

    action: str  # "NO_ACTION" or "ADVISORY_ONLY"
    advisory: LlmAdvisory | None
    errors: tuple[str, ...]


def handle_llm_output(raw: object, *, allowed_instruments: tuple[str, ...] = ("EURUSD",)) -> LlmHandlingResult:
    """Strictly validate an LLM response; anything unexpected means NO_ACTION.

    A valid response is still only an annotation for humans and the research
    log. This function has no access to the gateway, risk engine or broker.
    """

    if not isinstance(raw, str) or len(raw) > 4_000:
        return LlmHandlingResult("NO_ACTION", None, ("not_a_bounded_string",))
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return LlmHandlingResult("NO_ACTION", None, ("invalid_json",))
    if not isinstance(payload, dict):
        return LlmHandlingResult("NO_ACTION", None, ("not_an_object",))
    errors: list[str] = []
    keys = set(payload)
    if keys & _FORBIDDEN_LLM_KEYS:
        errors.append("execution_fields_forbidden")
    if keys != _LLM_KEYS:
        errors.append("unexpected_or_missing_keys")
    if payload.get("schema") != LLM_SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    instrument = payload.get("instrument")
    if not isinstance(instrument, str) or instrument.upper() not in allowed_instruments:
        errors.append("instrument_not_allowed")
    bias = payload.get("bias")
    if bias not in _LLM_BIASES:
        errors.append("invalid_bias")
    confidence = payload.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        errors.append("invalid_confidence")
    rationale = payload.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2_000:
        errors.append("invalid_rationale")
    if errors:
        return LlmHandlingResult("NO_ACTION", None, tuple(errors))
    return LlmHandlingResult(
        "ADVISORY_ONLY",
        LlmAdvisory(instrument.upper(), bias, float(confidence), rationale.strip()),
        (),
    )
