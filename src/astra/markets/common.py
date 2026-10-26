"""Shared helpers for the per-market data models.

Exchange quantities and prices are handled as ``Decimal``. Binary floats are
refused on input so a step such as 0.1 is never silently stored as
0.1000000000000000055.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

ILLUSTRATIVE_SOURCE = "ILLUSTRATIVE"
PROVIDER_SOURCE_PREFIX = "PROVIDER_API:"


def to_decimal(value: object, name: str) -> Decimal:
    """Convert ``Decimal``, ``int`` or a numeric string to a finite ``Decimal``.

    ``float`` and ``bool`` are refused: callers parse provider strings directly.
    """

    if isinstance(value, (bool, float)):
        raise TypeError(f"{name} must be Decimal, int or a numeric string, not {type(value).__name__}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{name} is not a decimal number: {value!r}") from exc
    else:
        raise TypeError(f"{name} must be Decimal, int or a numeric string, not {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def optional_decimal(value: object, name: str) -> Decimal | None:
    return None if value is None else to_decimal(value, name)


def require_aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


# ASCII only: a Unicode look-alike (full-width letters, a Cyrillic capital that looks like S, a zero-width
# space) is upper-case and stripped but would be a different ledger key for the same asset.
_SYMBOL_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_./:-]*")


def require_symbol(value: object, name: str = "symbol") -> str:
    if not isinstance(value, str) or not _SYMBOL_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty canonical upper-case ASCII string")
    return value


def require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def require_iso_date(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date string YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date string YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an ISO date string YYYY-MM-DD")
    return value


def is_provider_sourced(source: str) -> bool:
    return source.startswith(PROVIDER_SOURCE_PREFIX)
