"""Execution permits: proof, checked by every adapter, that the gateway approved an order.

``OrderGateway`` issues a permit only after permission, schema, idempotency,
emergency-control, mode, quote, RiskEngine and contract checks pass. Adapters
verify it in ``BrokerAdapter.submit_order`` before anything leaves the
process, so calling an adapter directly cannot send an order.

The signing key is random per process and never persisted, so permits cannot
be replayed across restarts. This guards against accidental and
strategy/LLM-originated bypasses; it is not a defence against code that
deliberately imports private members of this module.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Direction, RunMode


PERMIT_TTL = timedelta(seconds=30)


class ExecutionRefused(PermissionError):
    """An adapter refused to send: missing/invalid permit or unsafe environment."""


@dataclass(frozen=True, slots=True)
class ExecutionPermit:
    client_order_id: str
    instrument: str
    direction: Direction
    units: float
    reduce_only: bool
    mode: RunMode
    expires_at: datetime
    signature: str


class _PermitAuthority:
    def __init__(self) -> None:
        self._key = os.urandom(32)

    def _digest(self, client_order_id, instrument, direction, units, reduce_only, mode, expires_at) -> str:
        message = "|".join(
            (
                client_order_id,
                instrument.upper(),
                direction.value,
                f"{float(units):.8f}",
                "1" if reduce_only else "0",
                mode.value,
                expires_at.isoformat(),
            )
        )
        return hmac.new(self._key, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, request, mode: RunMode, now: datetime) -> ExecutionPermit:
        expires_at = now + PERMIT_TTL
        return ExecutionPermit(
            client_order_id=request.client_order_id,
            instrument=request.instrument.upper(),
            direction=request.direction,
            units=float(request.units),
            reduce_only=bool(request.reduce_only),
            mode=mode,
            expires_at=expires_at,
            signature=self._digest(
                request.client_order_id, request.instrument, request.direction,
                request.units, request.reduce_only, mode, expires_at,
            ),
        )

    def verify(self, permit: object, request, now: datetime) -> str | None:
        """Return None if valid, else a refusal reason."""

        if not isinstance(permit, ExecutionPermit):
            return "missing_execution_permit"
        expected = self._digest(
            permit.client_order_id, permit.instrument, permit.direction,
            permit.units, permit.reduce_only, permit.mode, permit.expires_at,
        )
        if not hmac.compare_digest(expected, permit.signature):
            return "invalid_permit_signature"
        if (
            permit.client_order_id != request.client_order_id
            or permit.instrument != request.instrument.upper()
            or permit.direction is not request.direction
            or abs(permit.units - float(request.units)) > 1e-9
            or permit.reduce_only != bool(request.reduce_only)
        ):
            return "permit_does_not_match_request"
        if now >= permit.expires_at:
            return "permit_expired"
        return None


# Process-local authority. Only the gateway issues permits.
_AUTHORITY = _PermitAuthority()


def verify_permit(permit: object, request, now: datetime) -> str | None:
    return _AUTHORITY.verify(permit, request, now)
