"""Binance Spot read-only connector: public market data and a read-only account reader.

STATUS: TESTED OFFLINE ONLY, against ``tests/binance_fixture.py`` (a local fake
HTTP server on 127.0.0.1 that replays documented response shapes). This
connector is NOT READ_ONLY_CONNECTED. From the development machine,
api.binance.com and data-api.binance.vision were unreachable on 2026-10-26
(TCP/TLS timeouts, a local network filter is suspected, see
docs/venues/research_crypto_other.md section 0), so no live validation was
possible. Nothing here has been checked against real provider traffic.

Registry gate: every network call first checks ``astra.venues.registry``.
``BinanceSpotPublicData`` refuses unless ``can_read_public_data(record)`` is
True for the ``binance_spot`` record; ``BinanceSpotAccountReader`` refuses
unless ``can_read_account(record)`` is True. The shipped registry keeps
Binance blocked (UNVERIFIED / ELIGIBILITY_UNKNOWN) until a human records the
evidence, so both classes refuse out of the box. For a real (non-loopback)
host the record always comes from ``load_registry()`` (strict schema, evidence
rules and content hash); an in-memory ``registry_record`` is refused there.
Only with a loopback base URL, for the offline tests, may a caller pass an
in-memory ``registry_record`` or the ``test_only_skip_registry_gate`` flag.

Provider evidence: a loopback host (the offline fake) is never evidence of a
Binance account, so ``account_environment()`` reports UNVERIFIED there
whatever ``loopback_role`` says.

Stop state: the HTTP 418 ban, the HTTP 429 stop and the local request-weight
budget are shared process-wide per base URL, so a new connector instance
respects a ban or budget hit by another one. Passing ``state_path`` also
persists the ban and the 429 stop to a small JSON file, so a restarted
process respects them too; an unreadable state file is refused.

Read-only by construction: the only HTTP method in this module is GET and the
only paths are the ones in ``ALLOWED_PATHS``. There is no order, cancel,
withdrawal, transfer or user-data-stream call. Orders for any venue go only
through ``astra.brokers.base.BrokerAdapter`` and its permit, after review.

Provider facts used (read 2026-10-26 from the official GitHub mirror of the
Spot docs, https://github.com/binance/binance-spot-api-docs, files
rest-api.md and filters.md, because developers.binance.com reset the
connection from this machine; and from the official wallet client
https://github.com/binance/binance-connector-python, clients/wallet, for
``GET /sapi/v1/account/apiRestrictions``):

- weights: ping 1, time 1, exchangeInfo 20, klines 2, bookTicker 2 (single
  symbol), account 20, apiRestrictions 1 (IP weight, wallet client docstring);
- REQUEST_WEIGHT 6000 per minute per IP (documented example; the live value is
  read from ``exchangeInfo.rateLimits`` when available);
- ``X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)`` on every response;
- 429 = rate limit exceeded, 418 = automated IP ban (2 minutes to 3 days);
  ``Retry-After`` (seconds) comes with both; 403 = WAF rule violated;
  5XX = provider internal error, execution status UNKNOWN;
- SIGNED: HMAC-SHA256 of the query string with the API secret, hex encoded;
  ``timestamp`` in ms; ``recvWindow`` defaults to 5000 ms, maximum 60000;
  API key sent as header ``X-MBX-APIKEY``;
- klines: 12-element arrays (open time, O, H, L, C, volume, close time, ...);
  limit default 500, maximum 1000; startTime/endTime in UTC ms;
- filters: PRICE_FILTER (minPrice, maxPrice, tickSize; 0 disables a rule),
  LOT_SIZE (minQty, maxQty, stepSize), MIN_NOTIONAL (minNotional) and
  NOTIONAL (minNotional, maxNotional).

Key types: the docs recommend Ed25519 and mark HMAC as deprecated. Ed25519 and
RSA signing need a non-stdlib crypto library, so this stdlib-only module signs
with HMAC-SHA256 only; Ed25519 is listed as a limitation, not emulated.

Assumptions not settled by the pages read (to confirm on the first supervised
read-only check): the exact apiRestrictions field set on the live service;
that the SAPI weight is counted in the same per-IP budget; that ``canTrade`` /
``canWithdraw`` in ``/api/v3/account`` describe the account, not the key (so
the key check relies on apiRestrictions only).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..brokers.base import (
    BrokerRequestError,
    BrokerTimeout,
    BrokerUnavailable,
    EnvironmentSource,
    Quote,
)
from ..governance import SecretValue
from ..logs import get_logger, log_event
from ..markets.common import to_decimal
from ..markets.crypto_spot import CryptoSpotSpec
from ..models import MarketBar
from . import registry as venue_registry
from .contract import (
    AccountReadConnector,
    Balance,
    Capability,
    MarketDataConnector,
    MarketType,
    RateLimit,
    StaleDataError,
    VenueCapabilities,
    VenueEnvironment,
    ensure_fresh,
    validate_environment_claim,
)

_LOG = get_logger("venues.binance_spot")

VENUE_ID = "binance_spot"
CONNECTOR_NAME = "astra-binance-spot"
CONNECTOR_VERSION = "0.1.0"
DOCS_VERIFIED_ON = "2026-10-26"
SPOT_DOCS_URL = "https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md"
FILTERS_DOCS_URL = "https://github.com/binance/binance-spot-api-docs/blob/master/filters.md"
LIMITS_DOCS_URL = "https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information"
API_RESTRICTIONS_DOCS_URL = (
    "https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/account#get-api-key-permission"
)

DEFAULT_PUBLIC_BASE_URL = "https://data-api.binance.vision"
DEFAULT_ACCOUNT_BASE_URL = "https://api.binance.com"
ENV_API_KEY = "ASTRA_SECRET_BINANCE_API_KEY"
ENV_API_SECRET = "ASTRA_SECRET_BINANCE_API_SECRET"

# Hosts named in the official docs. Any other host is refused.
PUBLIC_DATA_HOSTS = frozenset({"data-api.binance.vision"})
PRODUCTION_HOSTS = frozenset(
    {"api.binance.com", "api-gcp.binance.com", "api1.binance.com", "api2.binance.com", "api3.binance.com", "api4.binance.com"}
)
TESTNET_HOSTS = frozenset({"testnet.binance.vision", "api1.testnet.binance.vision"})
DEMO_HOSTS = frozenset({"demo-api.binance.com"})
_LOOPBACK_ROLES = frozenset({"public_data", "production", "testnet", "demo"})

DOCUMENTED_REQUEST_WEIGHT_PER_MINUTE = 6000
# Stay below the provider limit: the local budget is this share of it.
WEIGHT_SAFETY_FRACTION = 0.8
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_RECV_WINDOW_MS = 5000
MAX_ALLOWED_RECV_WINDOW_MS = 10_000  # ASTRA keeps it small; the provider maximum is 60000
MIN_REVERIFY_SECONDS = 1
MAX_REVERIFY_SECONDS = 3600  # key permissions are re-read at least hourly
KLINES_MAX_LIMIT = 1000

QUOTE_SOURCE = "PROVIDER_API:binance_spot:bookTicker"
BARS_SOURCE = "PROVIDER_API:binance_spot:klines"
SPEC_SOURCE = "PROVIDER_API:binance_spot:exchangeInfo"

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,20}$")
_GET = "GET"  # the only HTTP method this module ever sends


@dataclass(frozen=True, slots=True)
class Endpoint:
    path: str
    weight: int
    signed: bool
    doc_url: str


PING = Endpoint("/api/v3/ping", 1, False, SPOT_DOCS_URL)
TIME = Endpoint("/api/v3/time", 1, False, SPOT_DOCS_URL)
EXCHANGE_INFO = Endpoint("/api/v3/exchangeInfo", 20, False, SPOT_DOCS_URL)
BOOK_TICKER = Endpoint("/api/v3/ticker/bookTicker", 2, False, SPOT_DOCS_URL)
KLINES = Endpoint("/api/v3/klines", 2, False, SPOT_DOCS_URL)
ACCOUNT = Endpoint("/api/v3/account", 20, True, SPOT_DOCS_URL)
API_RESTRICTIONS = Endpoint("/sapi/v1/account/apiRestrictions", 1, True, API_RESTRICTIONS_DOCS_URL)

PUBLIC_ENDPOINTS = (PING, TIME, EXCHANGE_INFO, BOOK_TICKER, KLINES)
ACCOUNT_ENDPOINTS = (TIME, ACCOUNT, API_RESTRICTIONS)
ALLOWED_PATHS = frozenset(endpoint.path for endpoint in (*PUBLIC_ENDPOINTS, *ACCOUNT_ENDPOINTS))

KLINE_INTERVAL_SECONDS: dict[str, int | None] = {
    "1s": 1,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14_400,
    "6h": 21_600,
    "8h": 28_800,
    "12h": 43_200,
    "1d": 86_400,
    "3d": 259_200,
    "1w": 604_800,
    "1M": None,  # calendar month: variable length, spacing is not checked
}

# apiRestrictions fields that must be present and False for a read-only key.
REQUIRED_FALSE_PERMISSIONS = (
    "enableSpotAndMarginTrading",
    "enableWithdrawals",
    "enableMargin",
    "enableFutures",
    "enableInternalTransfer",
    "permitsUniversalTransfer",
)
# Any other "enable*" / "permits*" flag must be False unless it is known to be read-only.
READ_ONLY_PERMISSION_FLAGS = frozenset({"enableReading", "enableFixReadOnly"})


# -- errors -----------------------------------------------------------------


class BinanceConfigurationError(BrokerUnavailable):
    """Configuration, registry or credential problem; retrying cannot help."""

    permanent = True


class RegistryGateRefused(BinanceConfigurationError):
    """The venue registry does not allow this kind of read for binance_spot."""


class KeyNotReadOnly(BinanceConfigurationError):
    """The API key has (or may have) a permission beyond reading; it is refused."""


class BinanceIpBanned(BrokerUnavailable):
    """HTTP 418: the provider banned this IP. No request is sent until the ban ends."""

    permanent = True

    def __init__(self, message: str, banned_until: datetime | None) -> None:
        super().__init__(message)
        self.banned_until = banned_until


class BarDataError(ValueError):
    """Kline data breaks a data quality rule (gap, duplicate, order, OHLC)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class MissingFeeSchedule(ValueError):
    """``CryptoSpotSpec`` needs fees; exchangeInfo does not report them."""


# -- HTTP layer ---------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class _BaseUrl:
    url: str
    host: str
    role: str  # public_data | production | testnet | demo
    loopback: bool


def _parse_base_url(base_url: str, allowed_roles: frozenset[str], loopback_role: str | None) -> _BaseUrl:
    if not isinstance(base_url, str):
        raise BinanceConfigurationError("base_url must be a string")
    parts = urllib.parse.urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise BinanceConfigurationError("base_url must be scheme://host[:port] with no path, query or credentials")
    loopback = _is_loopback(host)
    if loopback:
        if parts.scheme not in ("http", "https"):
            raise BinanceConfigurationError("loopback base_url must use http or https")
        if loopback_role not in _LOOPBACK_ROLES:
            raise BinanceConfigurationError("a loopback base_url needs loopback_role (tests only)")
        role = loopback_role
    else:
        if loopback_role is not None:
            raise BinanceConfigurationError("loopback_role is accepted only with a loopback base_url")
        if parts.scheme != "https":
            raise BinanceConfigurationError("base_url must use https")
        if host in PUBLIC_DATA_HOSTS:
            role = "public_data"
        elif host in PRODUCTION_HOSTS:
            role = "production"
        elif host in TESTNET_HOSTS:
            role = "testnet"
        elif host in DEMO_HOSTS:
            role = "demo"
        else:
            raise BinanceConfigurationError(f"host {host!r} is not a documented Binance Spot host")
    if role not in allowed_roles:
        raise BinanceConfigurationError(f"a {role} host cannot serve this connector")
    port = f":{parts.port}" if parts.port else ""
    return _BaseUrl(f"{parts.scheme}://{parts.hostname}{port}", host, role, loopback)


# Marker for a 429 stop that only a human can lift (unusable Retry-After).
OPEN_ENDED = datetime.max.replace(tzinfo=UTC)
DEFAULT_429_BLOCK_SECONDS = 60.0  # 429 without any Retry-After header
STATE_SCHEMA = "astra.binance_host_state"
STATE_SCHEMA_VERSION = 1


class _HostState:
    """Process-wide stop state and weight budget for one base URL, shared by every connector instance.

    The 418 ban and the 429 stop use wall-clock times and can be persisted to a
    JSON file (``state_path``) so a restarted process also respects them. The
    weight window is in memory only (the provider's per-minute count resets it).
    """

    def __init__(self, key: str) -> None:
        self.key = key
        self.lock = threading.RLock()
        self.state_path: Path | None = None
        self.weight_limit = DOCUMENTED_REQUEST_WEIGHT_PER_MINUTE
        self.used_weight: dict[str, int] = {}
        self.window_start: float | None = None
        self.window_used = 0
        self.blocked_until: datetime | None = None  # after a 429 we did not wait out
        self.banned = False
        self.banned_until: datetime | None = None  # None while banned: open-ended, a human must acknowledge

    def attach(self, state_path: Path) -> None:
        if self.state_path is not None and self.state_path != state_path:
            raise BinanceConfigurationError(f"{self.key}: already persisted to another state file")
        self.state_path = state_path
        record = _read_state_file(state_path).get(self.key)
        if record is None:
            return
        try:
            banned = record["banned"]
            banned_until = _parse_state_time(record["banned_until"])
            blocked_until = _parse_state_time(record["blocked_until"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BinanceConfigurationError(f"state file entry for {self.key} is invalid; inspect it by hand") from exc
        if not isinstance(banned, bool):
            raise BinanceConfigurationError(f"state file entry for {self.key} is invalid; inspect it by hand")
        if banned and not self.banned:
            self.banned, self.banned_until = True, banned_until
        elif banned and self.banned_until is not None:
            self.banned_until = None if banned_until is None else max(self.banned_until, banned_until)
        if blocked_until is not None and (self.blocked_until is None or blocked_until > self.blocked_until):
            self.blocked_until = blocked_until

    def save(self) -> None:
        if self.state_path is None:
            return
        document = _read_state_file(self.state_path)
        document[self.key] = {
            "banned": self.banned,
            "banned_until": self.banned_until.isoformat() if self.banned_until else None,
            "blocked_until": self.blocked_until.isoformat() if self.blocked_until else None,
        }
        payload = {"schema": STATE_SCHEMA, "schema_version": STATE_SCHEMA_VERSION, "hosts": document}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.state_path)


def _parse_state_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("time must be an ISO string or null")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("time must be timezone-aware")
    return moment


def _read_state_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Fail closed: an unreadable stop-state file could hide an active ban.
        raise BinanceConfigurationError(f"Binance state file {path} is unreadable; inspect it by hand") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != STATE_SCHEMA
        or document.get("schema_version") != STATE_SCHEMA_VERSION
        or not isinstance(document.get("hosts"), dict)
    ):
        raise BinanceConfigurationError(f"Binance state file {path} has an unexpected format; inspect it by hand")
    return document["hosts"]


_HOST_STATES: dict[str, _HostState] = {}
_HOST_STATES_LOCK = threading.Lock()


def _host_state(key: str, state_path: str | Path | None) -> _HostState:
    with _HOST_STATES_LOCK:
        state = _HOST_STATES.get(key)
        if state is None:
            state = _HostState(key)
            _HOST_STATES[key] = state
    if state_path is not None:
        with state.lock:
            state.attach(Path(state_path))
    return state


def _state_key(base: _BaseUrl) -> str:
    """Scope of the shared ban/429/weight state (MV-A-10).

    Binance bans and weighs per IP, so every production and public-data host
    (api, api1..api4, api-gcp, data-api, with or without an explicit port)
    shares one state; testnet and demo each have their own. A loopback test
    server keeps its own state per URL.
    """

    if base.loopback:
        return f"loopback:{base.url}"
    if base.role in ("production", "public_data"):
        return "binance:production"
    return f"binance:{base.role}"


def reset_host_states_for_tests() -> None:
    """TEST ONLY: forget the in-memory per-host state (persisted files are left untouched)."""

    with _HOST_STATES_LOCK:
        _HOST_STATES.clear()


class _BinanceHttp:
    """GET-only HTTP client with registry gate, weight budget, 429/418 handling and bounded retries."""

    def __init__(
        self,
        base: _BaseUrl,
        gate: Callable[[], None],
        *,
        timeout: float,
        max_attempts: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
        max_retry_after_seconds: float,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        scrub: Callable[[str], str],
        state_path: str | Path | None = None,
    ) -> None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise BinanceConfigurationError("max_attempts must be an integer from 1 to 5")
        if not 0 < timeout <= 60:
            raise BinanceConfigurationError("timeout must be in (0, 60] seconds")
        self._base = base
        self._gate = gate
        self._timeout = float(timeout)
        self._max_attempts = max_attempts
        self._backoff_base = float(backoff_base_seconds)
        self._backoff_max = float(backoff_max_seconds)
        self._max_retry_after = float(max_retry_after_seconds)
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._scrub = scrub
        handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
        if base.loopback:
            handlers.append(urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)
        # Ban, 429 stop and weight budget are shared by every instance for this provider scope (per IP).
        self._state = _host_state(_state_key(base), state_path)
        self._lock = self._state.lock
        self.requests_sent = 0

    # -- state ---------------------------------------------------------------

    @property
    def banned_until(self) -> datetime | None:
        return self._state.banned_until

    @property
    def is_banned(self) -> bool:
        return self._state.banned

    @property
    def weight_limit(self) -> int:
        return self._state.weight_limit

    @property
    def used_weight(self) -> dict[str, int]:
        return self._state.used_weight

    def acknowledge_ban_cleared(self) -> None:
        """Human action: lift an open-ended 418 ban or 429 stop after checking it has ended."""

        with self._lock:
            self._state.banned = False
            self._state.banned_until = None
            self._state.blocked_until = None
            self._state.save()
        log_event(_LOG, "binance.ban_acknowledged", level=logging.WARNING)

    def _check_blocks(self, path: str) -> None:
        state = self._state
        if state.banned:
            if state.banned_until is not None and self._clock() >= state.banned_until:
                state.banned = False
                state.banned_until = None
                state.save()
            else:
                until = state.banned_until.isoformat() if state.banned_until else "unknown (a human must acknowledge)"
                raise BinanceIpBanned(f"{path}: IP banned by the provider (HTTP 418) until {until}; no request sent",
                                      state.banned_until)
        if state.blocked_until == OPEN_ENDED:
            raise BrokerRequestError(
                "HTTP_429",
                f"{path}: rate limited with an unusable Retry-After; stopped until a human calls acknowledge_ban_cleared()",
            )
        if state.blocked_until is not None:
            remaining = (state.blocked_until - self._clock()).total_seconds()
            if remaining > 0:
                raise BrokerRequestError("HTTP_429", f"{path}: rate limited; wait {remaining:.1f}s before retrying",
                                         remaining, transient=True)
            state.blocked_until = None
            state.save()

    def _reserve_weight(self, path: str, weight: int) -> None:
        state = self._state
        now = self._monotonic()
        if state.window_start is None or not 0 <= now - state.window_start < 60.0:
            state.window_start = now
            state.window_used = 0
        budget = int(state.weight_limit * WEIGHT_SAFETY_FRACTION)
        if state.window_used + weight > budget:
            wait = max(0.0, 60.0 - (now - state.window_start))
            raise BrokerRequestError(
                "LOCAL_WEIGHT_BUDGET",
                f"{path}: local request-weight budget {budget}/min reached ({state.window_used} used); no request sent",
                wait,
                transient=True,
            )
        state.window_used += weight

    def _record_headers(self, headers: Mapping[str, str] | Any) -> None:
        if headers is None:
            return
        for name in list(headers.keys()):
            lower = name.lower()
            if not lower.startswith("x-mbx-used-weight-"):
                continue
            try:
                value = int(str(headers[name]).strip())
            except ValueError:
                continue
            if value < 0:
                continue
            interval = name[len("x-mbx-used-weight-"):].upper()
            self._state.used_weight[interval] = value
            if interval == "1M":
                # The provider's count is authoritative for the current minute.
                self._state.window_used = value

    def update_weight_limit(self, rate_limits: object) -> None:
        if not isinstance(rate_limits, list):
            return
        for entry in rate_limits:
            if (
                isinstance(entry, dict)
                and entry.get("rateLimitType") == "REQUEST_WEIGHT"
                and entry.get("interval") == "MINUTE"
                and entry.get("intervalNum") == 1
                and type(entry.get("limit")) is int
                and entry["limit"] > 0
            ):
                with self._lock:
                    # A provider value may lower the local budget, never raise it above the documented limit.
                    self._state.weight_limit = min(entry["limit"], DOCUMENTED_REQUEST_WEIGHT_PER_MINUTE)

    # -- request -------------------------------------------------------------

    def get_json(
        self,
        endpoint: Endpoint,
        params: Iterable[tuple[str, str]] = (),
        *,
        signer: Callable[[list[tuple[str, str]]], tuple[str, dict[str, str]]] | None = None,
    ) -> Any:
        if endpoint.path not in ALLOWED_PATHS:
            raise BinanceConfigurationError(f"{endpoint.path} is not an allowed read-only path")
        if endpoint.signed != (signer is not None):
            raise BinanceConfigurationError(f"{endpoint.path}: signing mismatch")
        self._gate()
        items = list(params)
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            with self._lock:
                self._check_blocks(endpoint.path)
                self._reserve_weight(endpoint.path, endpoint.weight)
            if signer is not None:
                query, headers = signer(items)  # fresh timestamp on every attempt
            else:
                query, headers = urllib.parse.urlencode(items), {}
            url = self._base.url + endpoint.path + (f"?{query}" if query else "")
            try:
                status, response_headers, body = self._send(url, headers)
            except TimeoutError:
                last_error = BrokerTimeout(f"{endpoint.path}: no response within {self._timeout:g}s")
            except (OSError, http.client.HTTPException) as exc:
                last_error = BrokerUnavailable(f"{endpoint.path}: connection failed ({type(exc).__name__})")
            else:
                with self._lock:
                    self._record_headers(response_headers)
                if 200 <= status < 300:
                    return self._decode(endpoint.path, body)
                retry_after, retry_after_invalid = _retry_after_seconds(response_headers)
                if status == 418:
                    self._ban(endpoint.path, retry_after)  # None (absent or invalid): open-ended, a human must acknowledge
                if status == 429:
                    if retry_after is not None and retry_after <= self._max_retry_after and attempt < self._max_attempts:
                        log_event(_LOG, "binance.rate_limited", level=logging.WARNING, path=endpoint.path,
                                  retry_after=retry_after, attempt=attempt)
                        self._sleep(retry_after)
                        continue
                    if retry_after_invalid:
                        # Fail closed: an unparseable or out-of-range Retry-After never resumes on its own.
                        until, wait = OPEN_ENDED, None
                    else:
                        wait = retry_after if retry_after is not None else DEFAULT_429_BLOCK_SECONDS
                        until = self._clock() + timedelta(seconds=wait)
                    with self._lock:
                        self._state.blocked_until = until
                        self._state.save()
                    log_event(_LOG, "binance.rate_limited_stop", level=logging.WARNING, path=endpoint.path,
                              retry_after=wait, open_ended=wait is None)
                    raise BrokerRequestError(
                        "HTTP_429", f"{endpoint.path}: rate limit exceeded", wait, transient=wait is not None
                    )
                if status >= 500:
                    last_error = BrokerUnavailable(f"{endpoint.path}: HTTP {status} from provider (status unknown)")
                elif status == 403:
                    raise BrokerRequestError("HTTP_403", f"{endpoint.path}: WAF rule violated; not retried")
                elif 300 <= status < 400:
                    raise BinanceConfigurationError(f"{endpoint.path}: HTTP {status} redirect refused")
                else:
                    code, message = self._error_body(body)
                    raise BrokerRequestError(code or f"HTTP_{status}", self._scrub(f"{endpoint.path}: HTTP {status} {message}"))
            if attempt < self._max_attempts:
                delay = min(self._backoff_max, self._backoff_base * 2 ** (attempt - 1))
                log_event(_LOG, "binance.retry_wait", level=logging.WARNING, path=endpoint.path, attempt=attempt,
                          delay_seconds=delay, error=type(last_error).__name__)
                self._sleep(delay)
        assert last_error is not None
        if isinstance(last_error, BrokerTimeout):
            raise BrokerTimeout(f"{last_error} (after {self._max_attempts} attempts)")
        raise BrokerUnavailable(f"{last_error} (after {self._max_attempts} attempts)")

    def _ban(self, path: str, retry_after: float | None) -> None:
        until = None if retry_after is None else self._clock() + timedelta(seconds=retry_after)
        with self._lock:
            self._state.banned = True
            self._state.banned_until = until
            self._state.save()
        log_event(_LOG, "binance.ip_banned", level=logging.ERROR, path=path,
                  banned_until=until.isoformat() if until else None)
        raise BinanceIpBanned(f"{path}: IP banned by the provider (HTTP 418); all requests stopped", until)

    def _send(self, url: str, headers: dict[str, str]) -> tuple[int, Any, bytes]:
        request = urllib.request.Request(url, headers={"Accept": "application/json", **headers}, method=_GET)
        if request.get_method() != _GET:  # defensive: this module never sends anything but GET
            raise BinanceConfigurationError("only GET is allowed")
        self.requests_sent += 1
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, response.headers, _read_capped(response)
        except urllib.error.HTTPError as exc:
            try:
                body = _read_capped(exc)
            finally:
                exc.close()
            return exc.code, exc.headers, body
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError from None
            raise ConnectionError(type(exc.reason).__name__) from None

    def _decode(self, path: str, body: bytes) -> Any:
        try:
            return json.loads(body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrokerRequestError("INVALID_JSON", f"{path}: response is not valid JSON") from exc

    @staticmethod
    def _error_body(body: bytes) -> tuple[str | None, str]:
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, ""
        if not isinstance(data, dict):
            return None, ""
        code = data.get("code")
        message = data.get("msg")
        return (str(code) if isinstance(code, int) else None), (message[:200] if isinstance(message, str) else "")


def _read_capped(response: Any) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ConnectionError("response too large")
    return body


MAX_DOCUMENTED_BAN_SECONDS = 3 * 86_400  # bans "scale from 2 minutes to 3 days"


def _retry_after_seconds(headers: Any) -> tuple[float | None, bool]:
    """Return ``(seconds, invalid)``. ``(None, False)``: header absent; ``(None, True)``: unparseable or out of range."""

    if headers is None:
        return None, False
    value = headers.get("Retry-After")
    if value is None:
        return None, False
    try:
        seconds = float(str(value).strip())
    except ValueError:
        return None, True
    if not math.isfinite(seconds) or seconds < 0 or seconds > MAX_DOCUMENTED_BAN_SECONDS + 60:
        return None, True
    return seconds, False


# -- shared clock handling ----------------------------------------------------


def _ms_to_datetime(value: object, name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BrokerRequestError("INVALID_RESPONSE", f"{name} must be an integer of milliseconds")
    # 2017-01-01 .. 2100-01-01: anything else is a data error.
    if not 1_483_228_800_000 <= value <= 4_102_444_800_000:
        raise BrokerRequestError("INVALID_RESPONSE", f"{name} is outside the plausible range")
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _require_symbol(symbol: object) -> str:
    if not isinstance(symbol, str) or not _SYMBOL_PATTERN.match(symbol):
        raise ValueError("symbol must be an upper-case Binance symbol such as BTCUSDT")
    return symbol


class _BinanceClient:
    """Shared construction, registry gate and server-clock offset."""

    _allowed_roles: frozenset[str] = frozenset()

    def _init_client(
        self,
        *,
        base_url: str,
        registry_record: venue_registry.VenueRecord | None,
        test_only_skip_registry_gate: bool,
        loopback_role: str | None,
        timeout: float,
        max_attempts: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
        max_retry_after_seconds: float,
        max_clock_skew_seconds: float,
        offset_max_age_seconds: float,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        state_path: str | Path | None = None,
    ) -> None:
        self._base = _parse_base_url(base_url, self._allowed_roles, loopback_role)
        if test_only_skip_registry_gate is not False and (
            test_only_skip_registry_gate is not True or not self._base.loopback
        ):
            raise BinanceConfigurationError("test_only_skip_registry_gate is accepted only with a loopback base_url")
        self._skip_gate = test_only_skip_registry_gate
        self._record = registry_record
        if not 0 < max_clock_skew_seconds <= 30:
            raise BinanceConfigurationError("max_clock_skew_seconds must be in (0, 30]")
        self._max_skew = float(max_clock_skew_seconds)
        self._offset_max_age = float(offset_max_age_seconds)
        self._clock = clock
        self._offset: timedelta | None = None
        self._offset_measured_at: datetime | None = None
        self._http = _BinanceHttp(
            self._base,
            self._check_gate,
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
            clock=clock,
            monotonic=monotonic,
            sleep=sleep,
            scrub=self._scrub,
            state_path=state_path,
        )

    def _scrub(self, text: str) -> str:
        return text

    def _gate_allows(self, record: venue_registry.VenueRecord) -> bool:
        raise NotImplementedError

    def _check_gate(self) -> None:
        if self._skip_gate:
            return
        record = self._record
        if record is not None and not self._base.loopback:
            # An in-memory record skips the strict loader (schema, evidence rules, content hash).
            # Real hosts are gated only by the bundled registry file, read through load_registry().
            raise RegistryGateRefused(
                f"{VENUE_ID}: an in-memory registry_record is accepted only with a loopback base_url (tests); "
                "real hosts use the bundled registry"
            )
        if record is None:
            try:
                record = venue_registry.load_registry().get(VENUE_ID)
            except (KeyError, venue_registry.RegistryError) as exc:
                raise RegistryGateRefused(f"{VENUE_ID}: registry record unavailable ({type(exc).__name__})") from None
        if not isinstance(record, venue_registry.VenueRecord) or record.venue_id != VENUE_ID:
            raise RegistryGateRefused(f"registry record is not the {VENUE_ID} record")
        if not self._gate_allows(record):
            held = sorted(status.value for status in record.held_statuses)
            raise RegistryGateRefused(f"{VENUE_ID}: the venue registry does not allow this read (statuses {held})")

    # -- weight / ban state --------------------------------------------------

    @property
    def used_weight(self) -> dict[str, int]:
        """Last ``X-MBX-USED-WEIGHT-*`` values reported by the provider, by interval."""

        return dict(self._http.used_weight)

    @property
    def weight_limit(self) -> int:
        return self._http.weight_limit

    @property
    def banned_until(self) -> datetime | None:
        return self._http.banned_until

    @property
    def is_banned(self) -> bool:
        return self._http.is_banned

    @property
    def requests_sent(self) -> int:
        return self._http.requests_sent

    def acknowledge_ban_cleared(self) -> None:
        self._http.acknowledge_ban_cleared()

    # -- clock ---------------------------------------------------------------

    def server_time(self) -> datetime:
        self.require(Capability.SERVER_TIME)  # type: ignore[attr-defined]
        before = self._clock()
        data = self._http.get_json(TIME)
        after = self._clock()
        if not isinstance(data, dict):
            raise BrokerRequestError("INVALID_RESPONSE", f"{TIME.path}: expected an object")
        server = _ms_to_datetime(data.get("serverTime"), "serverTime")
        midpoint = before + (after - before) / 2
        self._offset = server - midpoint
        self._offset_measured_at = after
        return server

    @property
    def clock_offset(self) -> timedelta | None:
        """Provider clock minus local clock, measured at the last ``server_time`` call."""

        return self._offset

    def _server_now(self) -> datetime:
        """Current time on the provider clock: local clock corrected by a recent measured offset."""

        local = self._clock()
        if (
            self._offset is None
            or self._offset_measured_at is None
            or (local - self._offset_measured_at).total_seconds() > self._offset_max_age
            or local < self._offset_measured_at
        ):
            self.server_time()
            local = self._clock()
        offset = self._offset.total_seconds()  # type: ignore[union-attr]
        if offset > self._max_skew:
            raise StaleDataError("future_dated", -offset)
        if offset < -self._max_skew:
            raise StaleDataError("stale", -offset)
        return local + self._offset  # type: ignore[operator]


def _rate_limits() -> tuple[RateLimit, ...]:
    return (
        RateLimit("REQUEST_WEIGHT", DOCUMENTED_REQUEST_WEIGHT_PER_MINUTE, 60.0, True, f"PROVIDER_DOCS:{LIMITS_DOCS_URL}"),
    )


_COMMON_UNSUPPORTED = {
    Capability.POSITIONS: "spot has balances, not positions",
    Capability.ORDER_HISTORY: "not implemented: no order endpoint is called by this read-only connector",
    Capability.ORDER_PLACEMENT: "orders are outside the venue contract; only the BrokerAdapter permit boundary may send them",
    Capability.ORDER_CANCEL: "orders are outside the venue contract; only the BrokerAdapter permit boundary may send them",
    Capability.ORDER_MODIFY: "orders are outside the venue contract; only the BrokerAdapter permit boundary may send them",
    Capability.EVENT_STREAM: "WebSocket streams are not implemented in this lot",
    Capability.RECONCILIATION: "no orders or positions to reconcile in a read-only connector",
}

_LIMITATIONS = (
    "TESTED OFFLINE ONLY against a local fake server; NOT READ_ONLY_CONNECTED",
    "api.binance.com and data-api.binance.vision were unreachable from the development machine on 2026-10-26; "
    "no live validation was possible",
    "the shipped venue registry keeps binance_spot blocked until a human records evidence",
    "HMAC-SHA256 signing only (stdlib); Ed25519 and RSA keys are not supported",
    "GET only; no order, cancel, withdrawal, transfer or user-data-stream endpoint",
)


# -- public market data ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BinanceSymbolRules:
    """Exchange rules for one symbol, as reported by exchangeInfo. Fees are not reported there."""

    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    tick_size: Decimal
    min_price: Decimal
    max_price: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    max_notional: Decimal | None
    notional_filter: str
    source: str
    as_of: datetime
    maker_fee: Decimal | None = None
    taker_fee: Decimal | None = None

    def to_spec(self, maker_fee: Decimal, taker_fee: Decimal) -> CryptoSpotSpec:
        return CryptoSpotSpec(
            symbol=self.symbol,
            base_asset=self.base_asset,
            quote_asset=self.quote_asset,
            status=self.status,
            tick_size=self.tick_size,
            step_size=self.step_size,
            min_qty=self.min_qty,
            max_qty=self.max_qty,
            min_notional=self.min_notional,
            price_precision=_decimals(self.tick_size),
            quantity_precision=_decimals(self.step_size),
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            source=self.source,
            as_of=self.as_of,
            min_price=self.min_price,
            max_price=self.max_price,
            max_notional=self.max_notional,
        )


# Sanity bounds for exchangeInfo values (ASTRA choice, not a Binance rule). Real Spot ticks and steps
# are within 1E-8 .. 1E+3 in the documented examples; anything outside 1E-18 .. 1E+9 is treated as a
# data error rather than a rule, and no filter value may exceed 1E+18.
MIN_STEP = Decimal("1E-18")
MAX_STEP = Decimal("1E+9")
MAX_FILTER_VALUE = Decimal("1E+18")


def _decimals(step: Decimal) -> int:
    exponent = step.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _filter_decimal(entry: Mapping[str, Any], key: str, symbol: str) -> Decimal:
    if key not in entry:
        raise ValueError(f"{symbol}: filter {entry.get('filterType')} lacks {key}")
    value = to_decimal(entry[key], f"{symbol}.{key}")
    if value < 0 or value > MAX_FILTER_VALUE:
        raise ValueError(f"{symbol}: {key} {value} is outside the sanity range [0, {MAX_FILTER_VALUE}]")
    return value


def parse_symbol_rules(raw: object, as_of: datetime) -> BinanceSymbolRules:
    """Parse one exchangeInfo ``symbols`` entry; raise ``ValueError`` when a rule is missing or disabled."""

    if not isinstance(raw, dict):
        raise ValueError("symbol entry must be an object")
    symbol = _require_symbol(raw.get("symbol"))
    filters = raw.get("filters")
    if not isinstance(filters, list):
        raise ValueError(f"{symbol}: filters missing")
    by_type: dict[str, Mapping[str, Any]] = {}
    for entry in filters:
        if isinstance(entry, dict) and isinstance(entry.get("filterType"), str):
            if entry["filterType"] in by_type:
                raise ValueError(f"{symbol}: duplicate {entry['filterType']} filter")
            by_type[entry["filterType"]] = entry
    price = by_type.get("PRICE_FILTER")
    lot = by_type.get("LOT_SIZE")
    if price is None or lot is None:
        raise ValueError(f"{symbol}: PRICE_FILTER and LOT_SIZE are required")
    tick = _filter_decimal(price, "tickSize", symbol)
    if tick <= 0:
        raise ValueError(f"{symbol}: tick size rule disabled (tickSize 0); refusing to guess a tick")
    step = _filter_decimal(lot, "stepSize", symbol)
    if step <= 0:
        raise ValueError(f"{symbol}: stepSize must be positive")
    notional = by_type.get("NOTIONAL")
    if notional is not None:
        min_notional = _filter_decimal(notional, "minNotional", symbol)
        max_notional = _filter_decimal(notional, "maxNotional", symbol) if "maxNotional" in notional else None
        notional_kind = "NOTIONAL"
    elif "MIN_NOTIONAL" in by_type:
        min_notional = _filter_decimal(by_type["MIN_NOTIONAL"], "minNotional", symbol)
        max_notional = None
        notional_kind = "MIN_NOTIONAL"
    else:
        raise ValueError(f"{symbol}: no NOTIONAL or MIN_NOTIONAL filter; minimum notional unknown")
    status = raw.get("status")
    base_asset = raw.get("baseAsset")
    quote_asset = raw.get("quoteAsset")
    for name, value in (("status", status), ("baseAsset", base_asset), ("quoteAsset", quote_asset)):
        if not isinstance(value, str) or not value or value != value.strip().upper():
            raise ValueError(f"{symbol}: {name} missing or not canonical")
    rules = BinanceSymbolRules(
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        status=status,
        tick_size=tick,
        min_price=_filter_decimal(price, "minPrice", symbol),
        max_price=_filter_decimal(price, "maxPrice", symbol),
        step_size=step,
        min_qty=_filter_decimal(lot, "minQty", symbol),
        max_qty=_filter_decimal(lot, "maxQty", symbol),
        min_notional=min_notional,
        max_notional=max_notional,
        notional_filter=notional_kind,
        source=SPEC_SOURCE,
        as_of=as_of,
    )
    # Validate through the spec model with neutral fees so bad rules fail here, not later.
    for label, increment in (("tickSize", tick), ("stepSize", step)):
        if not MIN_STEP <= increment <= MAX_STEP:
            raise ValueError(f"{symbol}: {label} {increment} is outside the sanity range [{MIN_STEP}, {MAX_STEP}]")
    rules.to_spec(Decimal(0), Decimal(0))
    return rules


class BinanceSpotPublicData(_BinanceClient, MarketDataConnector):
    """Keyless Binance Spot market data (default host data-api.binance.vision).

    TESTED OFFLINE ONLY; NOT READ_ONLY_CONNECTED; no live validation was
    possible from the development machine (Binance hosts unreachable). Every
    network call is refused unless the venue registry allows public data reads
    for binance_spot, which the shipped registry does not.
    """

    _allowed_roles = frozenset({"public_data", "production", "testnet", "demo"})

    def __init__(
        self,
        base_url: str = DEFAULT_PUBLIC_BASE_URL,
        *,
        registry_record: venue_registry.VenueRecord | None = None,
        test_only_skip_registry_gate: bool = False,
        loopback_role: str | None = None,
        fees: tuple[Decimal, Decimal] | None = None,
        max_quote_age_seconds: float = 5.0,
        timeout: float = 10.0,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 8.0,
        max_retry_after_seconds: float = 30.0,
        max_clock_skew_seconds: float = 2.0,
        offset_max_age_seconds: float = 60.0,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        state_path: str | Path | None = None,
    ) -> None:
        self._init_client(
            base_url=base_url,
            registry_record=registry_record,
            test_only_skip_registry_gate=test_only_skip_registry_gate,
            loopback_role=loopback_role,
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
            max_clock_skew_seconds=max_clock_skew_seconds,
            offset_max_age_seconds=offset_max_age_seconds,
            clock=clock,
            monotonic=monotonic,
            sleep=sleep,
            state_path=state_path,
        )
        if fees is not None:
            maker, taker = (to_decimal(fees[0], "maker_fee"), to_decimal(fees[1], "taker_fee"))
            fees = (maker, taker)
        self._fees = fees
        self._max_quote_age = float(max_quote_age_seconds)
        self.rejected_symbols: dict[str, str] = {}
        self._capabilities = VenueCapabilities(
            venue_id=VENUE_ID,
            connector_name=f"{CONNECTOR_NAME}-public",
            connector_version=CONNECTOR_VERSION,
            market_types=(MarketType.CRYPTO_SPOT,),
            environments=(VenueEnvironment.PUBLIC_DATA,),
            supported=frozenset(
                {
                    Capability.SERVER_TIME,
                    Capability.PUBLIC_MARKET_DATA,
                    Capability.QUOTES,
                    Capability.HISTORICAL_BARS,
                    Capability.INSTRUMENT_SPECS,
                }
            ),
            unsupported={
                Capability.ACCOUNT_READ: "public data connector; use BinanceSpotAccountReader",
                Capability.BALANCES: "public data connector; use BinanceSpotAccountReader",
                **_COMMON_UNSUPPORTED,
            },
            rate_limits=_rate_limits(),
            official_api_url=SPOT_DOCS_URL,
            verified_on=None,
            limitations=(*_LIMITATIONS, "CryptoSpotSpec needs fees: pass fees=(maker, taker); exchangeInfo has none"),
        )

    def __repr__(self) -> str:
        return f"BinanceSpotPublicData(base_url={self._base.url!r})"

    def _gate_allows(self, record: venue_registry.VenueRecord) -> bool:
        return venue_registry.can_read_public_data(record)

    def capabilities(self) -> VenueCapabilities:
        return self._capabilities

    @property
    def bars_source(self) -> str:
        """Source label for every bar returned by ``bars`` (``MarketBar`` has no source field)."""

        return BARS_SOURCE

    def ping(self) -> None:
        self.require(Capability.PUBLIC_MARKET_DATA)
        data = self._http.get_json(PING)
        if data != {}:
            raise BrokerRequestError("INVALID_RESPONSE", f"{PING.path}: expected an empty object")

    # -- exchangeInfo --------------------------------------------------------

    def symbol_rules(self, symbols: tuple[str, ...] | None = None) -> dict[str, BinanceSymbolRules]:
        """Parsed exchange rules (fees None). Symbols that fail parsing go to ``rejected_symbols``."""

        self.require(Capability.INSTRUMENT_SPECS)
        params: list[tuple[str, str]] = []
        if symbols is not None:
            if not isinstance(symbols, tuple) or not symbols:
                raise ValueError("symbols must be a non-empty tuple or None")
            for symbol in symbols:
                _require_symbol(symbol)
            if len(symbols) == 1:
                params.append(("symbol", symbols[0]))
            else:
                params.append(("symbols", json.dumps(list(symbols), separators=(",", ":"))))
        data = self._http.get_json(EXCHANGE_INFO, params)
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise BrokerRequestError("INVALID_RESPONSE", f"{EXCHANGE_INFO.path}: symbols missing")
        self._http.update_weight_limit(data.get("rateLimits"))
        as_of = _ms_to_datetime(data["serverTime"], "serverTime") if "serverTime" in data else self._server_now()
        rules: dict[str, BinanceSymbolRules] = {}
        rejected: dict[str, str] = {}
        names = [raw.get("symbol") if isinstance(raw, dict) else None for raw in data["symbols"]]
        # A symbol listed more than once is ambiguous: every copy is refused.
        duplicated = {name for name in names if isinstance(name, str) and names.count(name) > 1}
        for name, raw in zip(names, data["symbols"], strict=True):
            if name in duplicated:
                rejected[name] = "duplicate symbol entry; every copy refused"
                continue
            try:
                parsed = parse_symbol_rules(raw, as_of)
            except (TypeError, ValueError) as exc:
                rejected[str(name)] = str(exc)
                continue
            rules[parsed.symbol] = parsed
        self.rejected_symbols = rejected
        if symbols is not None:
            missing = [s for s in symbols if s not in rules]
            if missing:
                reasons = {s: rejected.get(s, "not returned by the provider") for s in missing}
                raise ValueError(f"symbol rules unavailable: {reasons}")
        return rules

    def instrument_specs(self, symbols: tuple[str, ...] | None = None) -> dict[str, CryptoSpotSpec]:
        if self._fees is None:
            raise MissingFeeSchedule(
                "CryptoSpotSpec requires maker and taker fees and exchangeInfo does not report them; "
                "construct with fees=(maker, taker) from a documented source, or use symbol_rules()"
            )
        maker, taker = self._fees
        return {symbol: rules.to_spec(maker, taker) for symbol, rules in self.symbol_rules(symbols).items()}

    # -- quotes --------------------------------------------------------------

    def quote(self, symbol: str, *, max_age_seconds: float | None = None) -> Quote:
        """Best bid/ask. The timestamp is on the provider clock (local receipt time plus the measured offset)."""

        self.require(Capability.QUOTES)
        _require_symbol(symbol)
        self._server_now()  # refresh the offset first if needed, and refuse a skewed clock
        data = self._http.get_json(BOOK_TICKER, [("symbol", symbol)])
        received = self._server_now()
        if not isinstance(data, dict) or data.get("symbol") != symbol:
            raise BrokerRequestError("INVALID_RESPONSE", f"{BOOK_TICKER.path}: response is not for {symbol}")
        try:
            bid = to_decimal(data.get("bidPrice"), "bidPrice")
            ask = to_decimal(data.get("askPrice"), "askPrice")
            bid_qty = to_decimal(data.get("bidQty"), "bidQty")
            ask_qty = to_decimal(data.get("askQty"), "askQty")
        except (TypeError, ValueError) as exc:
            raise BrokerRequestError("INVALID_RESPONSE", f"{BOOK_TICKER.path}: {exc}") from None
        if bid <= 0 or ask <= 0 or bid_qty <= 0 or ask_qty <= 0:
            raise BrokerRequestError("INVALID_BOOK", f"{symbol}: non-positive price or quantity in the book")
        if bid >= ask:
            raise BrokerRequestError("INVALID_BOOK", f"{symbol}: crossed or locked book (bid {bid} >= ask {ask})")
        limit = self._max_quote_age if max_age_seconds is None else max_age_seconds
        ensure_fresh(received, max_age_seconds=limit, now=self._server_now())
        return Quote(instrument=symbol, bid=float(bid), ask=float(ask), timestamp=received, source=QUOTE_SOURCE)

    # -- klines --------------------------------------------------------------

    def bars(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> tuple[MarketBar, ...]:
        """Closed candles only. ``MarketBar.timestamp`` is the candle OPEN time; source is ``bars_source``.

        Raises ``BarDataError`` on duplicates, out-of-order rows, gaps or
        spacing mismatches (codes as in ``astra.data``), or inconsistent OHLC.
        """

        self.require(Capability.HISTORICAL_BARS)
        _require_symbol(symbol)
        if interval not in KLINE_INTERVAL_SECONDS:
            raise ValueError(f"unsupported kline interval {interval!r}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= KLINES_MAX_LIMIT:
            raise ValueError(f"limit must be an integer from 1 to {KLINES_MAX_LIMIT}")
        params = [("symbol", symbol), ("interval", interval)]
        for name, moment in (("startTime", start), ("endTime", end)):
            if moment is not None:
                if not isinstance(moment, datetime) or moment.tzinfo is None:
                    raise ValueError(f"{name} must be a timezone-aware datetime")
                params.append((name, str(_to_ms(moment))))
        if start is not None and end is not None and start > end:
            raise ValueError("start must not be after end")
        params.append(("limit", str(limit)))
        self._server_now()  # measure the offset first if needed, and refuse a skewed clock
        data = self._http.get_json(KLINES, params)
        now_ms = _to_ms(self._server_now())
        if not isinstance(data, list):
            raise BrokerRequestError("INVALID_RESPONSE", f"{KLINES.path}: expected an array")
        return _parse_klines(symbol, interval, data, now_ms)


def _parse_klines(symbol: str, interval: str, rows: list, now_ms: int) -> tuple[MarketBar, ...]:
    spacing = KLINE_INTERVAL_SECONDS[interval]
    bars: list[MarketBar] = []
    previous_open: int | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            raise BarDataError("MALFORMED_ROW", f"row {index} is not a kline array")
        open_ms, close_ms = row[0], row[6]
        for name, value in (("open time", open_ms), ("close time", close_ms)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise BarDataError("INVALID_TIMESTAMP", f"row {index}: {name} is not integer milliseconds")
        if close_ms <= open_ms:
            raise BarDataError("INVALID_TIMESTAMP", f"row {index}: close time not after open time")
        if previous_open is not None:
            delta = (open_ms - previous_open) / 1000
            if delta == 0:
                raise BarDataError("DUPLICATE_TIMESTAMP", f"row {index} duplicates the prior open time")
            if delta < 0:
                raise BarDataError("OUT_OF_ORDER_TIMESTAMP", f"row {index} precedes the prior row")
            if spacing is not None and delta > spacing:
                raise BarDataError("INTERVAL_GAP", f"row {index}: gap of {delta:g}s, expected {spacing}s cadence")
            if spacing is not None and delta < spacing:
                raise BarDataError("INTERVAL_MISMATCH", f"row {index}: spacing {delta:g}s, expected {spacing}s")
        if spacing is not None and close_ms != open_ms + spacing * 1000 - 1:
            raise BarDataError("CLOSE_TIME_MISMATCH", f"row {index}: close time does not match the {interval} interval")
        previous_open = open_ms
        if close_ms >= now_ms:
            # Still open on the provider clock: never returned. Only the last row may be open.
            if index != len(rows) - 1:
                raise BarDataError("OPEN_CANDLE_NOT_LAST", f"row {index} is still open but is not the last row")
            continue
        try:
            o, h, low, c, v = (to_decimal(row[i], name) for i, name in enumerate(("open", "high", "low", "close", "volume"), 1))
        except (TypeError, ValueError) as exc:
            raise BarDataError("INVALID_PRICE", f"row {index}: {exc}") from None
        if min(o, h, low, c) <= 0 or v < 0:
            raise BarDataError("INVALID_PRICE", f"row {index}: non-positive price or negative volume")
        if h < max(o, c, low) or low > min(o, c, h):
            raise BarDataError("INCONSISTENT_OHLC", f"row {index}: high/low do not bound open/close")
        bars.append(
            MarketBar(
                instrument=symbol,
                timestamp=datetime.fromtimestamp(open_ms / 1000, tz=UTC),
                open=float(o),
                high=float(h),
                low=float(low),
                close=float(c),
                volume=float(v),
            )
        )
    return tuple(bars)


# -- account reader -------------------------------------------------------------


class BinanceSpotAccountReader(_BinanceClient, AccountReadConnector):
    """Signed, read-only Binance Spot account reads (GET /api/v3/account only).

    TESTED OFFLINE ONLY; NOT READ_ONLY_CONNECTED; no live validation was
    possible from the development machine. Refuses unless the venue registry
    allows account reads for binance_spot (the shipped registry does not), and
    refuses any key whose apiRestrictions show a permission beyond reading or
    cannot be read at all (the Spot testnet has no /sapi, so it is refused).
    Credentials come only from ASTRA_SECRET_BINANCE_API_KEY and
    ASTRA_SECRET_BINANCE_API_SECRET, are held as ``SecretValue`` and registered
    for log masking; they never appear in logs, exceptions or reprs.
    """

    _allowed_roles = frozenset({"production", "testnet", "demo"})

    def __init__(
        self,
        api_key: SecretValue,
        api_secret: SecretValue,
        base_url: str = DEFAULT_ACCOUNT_BASE_URL,
        *,
        registry_record: venue_registry.VenueRecord | None = None,
        test_only_skip_registry_gate: bool = False,
        loopback_role: str | None = None,
        recv_window_ms: int = DEFAULT_RECV_WINDOW_MS,
        reverify_after_seconds: float = 300.0,
        timeout: float = 10.0,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 8.0,
        max_retry_after_seconds: float = 30.0,
        max_clock_skew_seconds: float = 2.0,
        offset_max_age_seconds: float = 60.0,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        state_path: str | Path | None = None,
    ) -> None:
        if not isinstance(api_key, SecretValue) or not isinstance(api_secret, SecretValue):
            raise BinanceConfigurationError("api_key and api_secret must be SecretValue instances")
        key_text, secret_text = api_key.reveal(), api_secret.reveal()  # reveal() registers both for log masking
        if not key_text or not secret_text or any(ch.isspace() for ch in key_text + secret_text):
            raise BinanceConfigurationError("Binance API key or secret is empty or malformed")
        self._api_key = api_key
        self._api_secret = api_secret
        if (
            isinstance(recv_window_ms, bool)
            or not isinstance(recv_window_ms, int)
            or not 1 <= recv_window_ms <= MAX_ALLOWED_RECV_WINDOW_MS
        ):
            raise BinanceConfigurationError(f"recv_window_ms must be an integer from 1 to {MAX_ALLOWED_RECV_WINDOW_MS}")
        self._recv_window = recv_window_ms
        if (
            isinstance(reverify_after_seconds, bool)
            or not isinstance(reverify_after_seconds, (int, float))
            or not math.isfinite(reverify_after_seconds)
            or not MIN_REVERIFY_SECONDS <= reverify_after_seconds <= MAX_REVERIFY_SECONDS
        ):
            raise BinanceConfigurationError(
                f"reverify_after_seconds must be a finite number from {MIN_REVERIFY_SECONDS} to {MAX_REVERIFY_SECONDS}"
            )
        self._reverify_after = float(reverify_after_seconds)
        self._verified_at: datetime | None = None
        self._provider_evidence = False
        self.last_restrictions: dict[str, bool] | None = None
        self.last_account_flags: dict[str, object] = {}
        self._init_client(
            base_url=base_url,
            registry_record=registry_record,
            test_only_skip_registry_gate=test_only_skip_registry_gate,
            loopback_role=loopback_role,
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
            max_clock_skew_seconds=max_clock_skew_seconds,
            offset_max_age_seconds=offset_max_age_seconds,
            clock=clock,
            monotonic=monotonic,
            sleep=sleep,
            state_path=state_path,
        )
        self._capabilities = VenueCapabilities(
            venue_id=VENUE_ID,
            connector_name=f"{CONNECTOR_NAME}-account",
            connector_version=CONNECTOR_VERSION,
            market_types=(MarketType.CRYPTO_SPOT,),
            environments=(VenueEnvironment.READ_ONLY, VenueEnvironment.LIVE, VenueEnvironment.TESTNET),
            supported=frozenset({Capability.SERVER_TIME, Capability.ACCOUNT_READ, Capability.BALANCES}),
            unsupported={
                Capability.PUBLIC_MARKET_DATA: "account reader; use BinanceSpotPublicData",
                Capability.QUOTES: "account reader; use BinanceSpotPublicData",
                Capability.HISTORICAL_BARS: "account reader; use BinanceSpotPublicData",
                Capability.INSTRUMENT_SPECS: "account reader; use BinanceSpotPublicData",
                **_COMMON_UNSUPPORTED,
            },
            rate_limits=_rate_limits(),
            official_api_url=SPOT_DOCS_URL,
            verified_on=None,
            limitations=_LIMITATIONS,
        )

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None, **kwargs: Any
    ) -> BinanceSpotAccountReader:
        """Build from ASTRA_SECRET_BINANCE_API_KEY / ASTRA_SECRET_BINANCE_API_SECRET only."""

        source = os.environ if environ is None else environ
        key, secret = source.get(ENV_API_KEY), source.get(ENV_API_SECRET)
        if not key or not secret:
            raise BinanceConfigurationError(f"{ENV_API_KEY} and {ENV_API_SECRET} must both be set")
        return cls(SecretValue(key), SecretValue(secret), **kwargs)

    @classmethod
    def from_vault(cls, vault: Any, actor: Any, **kwargs: Any) -> BinanceSpotAccountReader:
        """Build through ``astra.governance.SecretVault`` (audited; HUMAN_ADMIN only)."""

        key = vault.get(actor, "BINANCE_API_KEY")
        secret = vault.get(actor, "BINANCE_API_SECRET")
        if key is None or secret is None:
            raise BinanceConfigurationError(f"{ENV_API_KEY} and {ENV_API_SECRET} must both be set")
        return cls(key, secret, **kwargs)

    def __repr__(self) -> str:
        return f"BinanceSpotAccountReader(base_url={self._base.url!r}, api_key=****)"

    __str__ = __repr__

    def __reduce__(self):  # never pickle credentials
        raise TypeError("BinanceSpotAccountReader cannot be pickled")

    def _scrub(self, text: str) -> str:
        for secret in (self._api_secret.reveal(), self._api_key.reveal()):
            text = text.replace(secret, "****")
        return text

    def _gate_allows(self, record: venue_registry.VenueRecord) -> bool:
        return venue_registry.can_read_account(record)

    def capabilities(self) -> VenueCapabilities:
        return self._capabilities

    def _sign(self, params: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
        timestamp = _to_ms(self._server_now())
        items = [*params, ("recvWindow", str(self._recv_window)), ("timestamp", str(timestamp))]
        query = urllib.parse.urlencode(items)
        signature = hmac.new(self._api_secret.reveal().encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{query}&signature={signature}", {"X-MBX-APIKEY": self._api_key.reveal()}

    # -- read-only verification ------------------------------------------------

    def verify_read_only(self) -> dict[str, bool]:
        """Read the key's apiRestrictions and refuse (permanently) unless it is read-only."""

        self.require(Capability.ACCOUNT_READ)
        if self._base.role != "production":
            raise KeyNotReadOnly(
                f"key permissions cannot be verified on a {self._base.role} host (apiRestrictions is a production "
                "/sapi endpoint); refused"
            )
        data = self._http.get_json(API_RESTRICTIONS, signer=self._sign)
        restrictions = evaluate_api_restrictions(data)
        self.last_restrictions = restrictions
        self._verified_at = self._clock()
        log_event(_LOG, "binance.key_read_only_verified", ip_restrict=restrictions.get("ipRestrict"))
        return restrictions

    def _ensure_verified(self) -> None:
        now = self._clock()
        verified_at = self._verified_at
        if verified_at is None or now < verified_at or (now - verified_at).total_seconds() > self._reverify_after:
            self._verified_at = None
            self.verify_read_only()

    # -- reads -----------------------------------------------------------------

    def balances(self) -> tuple[Balance, ...]:
        self.require(Capability.BALANCES)
        self._ensure_verified()
        data = self._http.get_json(ACCOUNT, [("omitZeroBalances", "true")], signer=self._sign)
        as_of = self._server_now()
        if not isinstance(data, dict) or not isinstance(data.get("balances"), list):
            raise BrokerRequestError("INVALID_RESPONSE", f"{ACCOUNT.path}: balances missing")
        if data.get("accountType") != "SPOT":
            raise BrokerRequestError("INVALID_RESPONSE", f"{ACCOUNT.path}: accountType is not SPOT")
        self.last_account_flags = {
            key: data.get(key) for key in ("accountType", "canTrade", "canWithdraw", "canDeposit", "permissions")
        }
        balances: list[Balance] = []
        seen: set[str] = set()
        for entry in data["balances"]:
            if not isinstance(entry, dict):
                raise BrokerRequestError("INVALID_RESPONSE", f"{ACCOUNT.path}: balance entry is not an object")
            try:
                balance = Balance(asset=entry.get("asset"), free=entry.get("free"), locked=entry.get("locked"), as_of=as_of)
            except (TypeError, ValueError) as exc:
                raise BrokerRequestError("INVALID_RESPONSE", f"{ACCOUNT.path}: {exc}") from None
            if balance.asset in seen:
                raise BrokerRequestError("INVALID_RESPONSE", f"{ACCOUNT.path}: duplicate asset {balance.asset}")
            seen.add(balance.asset)
            balances.append(balance)
        # A signed response from a production host is provider evidence of a live account.
        # A loopback host (the offline fake) is never evidence, whatever loopback_role says.
        self._provider_evidence = self._base.role == "production" and not self._base.loopback
        return tuple(balances)

    def account_environment(self) -> tuple[VenueEnvironment, EnvironmentSource]:
        """LIVE + PROVIDER_API only after a signed account response from a real (non-loopback) production host.

        Binance production has no demo flag in its account response; the
        evidence is that the production host authenticated the key and served
        the account. Testnet hosts report TESTNET and demo hosts DEMO, both
        UNVERIFIED (a base URL is configuration, not evidence). Anything else is
        LIVE + UNVERIFIED, so callers treat it as live by default.
        """

        role = self._base.role
        if role == "testnet":
            return validate_environment_claim(VenueEnvironment.TESTNET, EnvironmentSource.UNVERIFIED)
        if role == "demo":
            return validate_environment_claim(VenueEnvironment.DEMO, EnvironmentSource.UNVERIFIED)
        if role == "production" and self._provider_evidence and not self._base.loopback:
            return validate_environment_claim(VenueEnvironment.LIVE, EnvironmentSource.PROVIDER_API)
        return validate_environment_claim(VenueEnvironment.LIVE, EnvironmentSource.UNVERIFIED)


def evaluate_api_restrictions(data: object) -> dict[str, bool]:
    """Return the restriction flags, or raise ``KeyNotReadOnly`` unless the key is provably read-only."""

    if not isinstance(data, dict):
        raise KeyNotReadOnly("apiRestrictions response is not an object; permissions cannot be verified")
    if data.get("enableReading") is not True:
        raise KeyNotReadOnly("key does not report enableReading=true; permissions cannot be verified")
    enabled: list[str] = []
    for name in REQUIRED_FALSE_PERMISSIONS:
        value = data.get(name)
        if value is True:
            enabled.append(name)
        elif value is not False:
            raise KeyNotReadOnly(f"apiRestrictions lacks a boolean {name}; permissions cannot be verified")
    for name, value in data.items():
        if not isinstance(name, str) or name in READ_ONLY_PERMISSION_FLAGS or name in REQUIRED_FALSE_PERMISSIONS:
            continue
        if name.startswith(("enable", "permits")):
            if value is True:
                enabled.append(name)
            elif value is not False:
                raise KeyNotReadOnly(f"apiRestrictions {name} is not a boolean; permissions cannot be verified")
    if enabled:
        raise KeyNotReadOnly(f"API key is not read-only: {sorted(enabled)} enabled; refused")
    return {name: value for name, value in data.items() if isinstance(value, bool)}
