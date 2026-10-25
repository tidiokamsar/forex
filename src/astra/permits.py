"""Execution permits: proof, checked by every adapter, that the gateway approved an order.

``OrderGateway`` issues a permit only after permission, schema, idempotency,
emergency-control, mode, quote, RiskEngine and contract checks pass. Adapters
verify it in ``BrokerAdapter.submit_order`` before anything leaves the
process, so calling an adapter directly cannot send an order.

A permit binds every field of the approved request (client id, instrument,
side, size, order type, stop-loss, purpose) and the mode, expires after
``PERMIT_TTL`` and is accepted once. The signing key is random per process
and never persisted, so permits cannot be replayed across restarts. This
guards against accidental and strategy/LLM-originated bypasses; it is not a
defence against code that deliberately imports private members of this
module.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
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
    order_type: str
    stop_loss: float | None
    mode: RunMode
    expires_at: datetime
    nonce: str
    signature: str


def _stop_text(stop_loss: float | None) -> str:
    return "none" if stop_loss is None else f"{float(stop_loss):.10f}"


class _PermitAuthority:
    def __init__(self) -> None:
        self._key = os.urandom(32)
        self._used: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def _digest(
        self, client_order_id, instrument, direction, units, reduce_only, order_type, stop_loss, mode, expires_at, nonce
    ) -> str:
        message = "|".join(
            (
                client_order_id,
                instrument.upper(),
                direction.value,
                f"{float(units):.8f}",
                "1" if reduce_only else "0",
                str(order_type),
                _stop_text(stop_loss),
                mode.value,
                expires_at.isoformat(),
                nonce,
            )
        )
        return hmac.new(self._key, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, request, mode: RunMode, now: datetime) -> ExecutionPermit:
        expires_at = now + PERMIT_TTL
        # A random nonce makes every permit unique, even for identical requests.
        nonce = os.urandom(16).hex()
        return ExecutionPermit(
            client_order_id=request.client_order_id,
            instrument=request.instrument.upper(),
            direction=request.direction,
            units=float(request.units),
            reduce_only=bool(request.reduce_only),
            order_type=str(request.order_type),
            stop_loss=request.stop_loss,
            mode=mode,
            expires_at=expires_at,
            nonce=nonce,
            signature=self._digest(
                request.client_order_id, request.instrument, request.direction, request.units,
                request.reduce_only, request.order_type, request.stop_loss, mode, expires_at, nonce,
            ),
        )

    def verify(self, permit: object, request, now: datetime) -> str | None:
        """Return None and consume the permit if valid, else a refusal reason."""

        if not isinstance(permit, ExecutionPermit):
            return "missing_execution_permit"
        expected = self._digest(
            permit.client_order_id, permit.instrument, permit.direction, permit.units,
            permit.reduce_only, permit.order_type, permit.stop_loss, permit.mode, permit.expires_at, permit.nonce,
        )
        if not hmac.compare_digest(expected, permit.signature):
            return "invalid_permit_signature"
        if (
            permit.client_order_id != request.client_order_id
            or permit.instrument != request.instrument.upper()
            or permit.direction is not request.direction
            or abs(permit.units - float(request.units)) > 1e-9
            or permit.reduce_only != bool(request.reduce_only)
            or permit.order_type != str(request.order_type)
            or _stop_text(permit.stop_loss) != _stop_text(request.stop_loss)
        ):
            return "permit_does_not_match_request"
        if now >= permit.expires_at:
            return "permit_expired"
        with self._lock:
            for signature, expires in list(self._used.items()):
                if expires <= now:
                    del self._used[signature]
            if permit.signature in self._used:
                return "permit_already_used"
            self._used[permit.signature] = permit.expires_at
        return None


# Process-local authority. Only the gateway issues permits.
_AUTHORITY = _PermitAuthority()


def verify_permit(permit: object, request, now: datetime) -> str | None:
    return _AUTHORITY.verify(permit, request, now)
