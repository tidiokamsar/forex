"""Binance Spot DEMO MODE adapter: the only connector of this build allowed to send orders, to DEMO only.

Official documentation (read 2026-10-28 on the official GitHub mirror,
https://github.com/binance/binance-spot-api-docs, demo-mode/general-info.md and rest-api.md): Demo Mode uses
the Spot API on ``https://demo-api.binance.com/api``; keys are created at demo.binance.com; prices and order
books are "similar to the live exchange" and filters and limits are "exactly the same". Realistic is not
real: Demo Mode results say nothing about live trading.

LIVE cannot be reached from here:
- the only host is ``demo-api.binance.com`` (a loopback host only for the offline tests); no production
  host is named in this module and redirects are refused;
- only the (method, path) pairs in ``ALLOWED_CALLS`` can be sent; there is no withdrawal or transfer call;
- ``submit_order`` (BrokerAdapter, not overridable) needs a gateway permit, then ``_environment_guard``
  requires a DEMO permit, the demo host, an account confirmed on that host, the venue's DEMO gate and no pause;
- a POST is never retried automatically; a timeout raises ``BrokerTimeout`` (outcome unknown) so the
  gateway looks the order up before anything else.

Spot has no positions: ASTRA's position in a symbol is the net base quantity of its own orders (client ids
starting with ``ASTRA-``), net of base-asset commissions, read from the provider's order and trade history.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..execution.spot_demo import SpotLedger, ledger_from_trades
from ..governance import SecretValue
from ..markets.crypto_spot import CryptoSpotSpec
from ..models import Direction, RunMode
from ..venues.binance_spot import BinanceSymbolRules, _parse_base_url, parse_symbol_rules
from .base import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerEnvironment,
    BrokerEvent,
    BrokerOrderReport,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerRequestError,
    BrokerTimeout,
    BrokerUnavailable,
    Capabilities,
    ConnectionState,
    EnvironmentSource,
    Quote,
)

VENUE_ID = "binance_spot_demo"
ADAPTER_NAME = "astra-binance-spot-demo"
ADAPTER_VERSION = "0.1.0"
DOCS_VERIFIED_ON = "2026-10-28"
DEMO_DOCS_URL = "https://github.com/binance/binance-spot-api-docs/blob/master/demo-mode/general-info.md"
DEMO_BASE_URL = "https://demo-api.binance.com"
CLIENT_ID_PREFIX = "ASTRA-"
RECV_WINDOW_MS = 5000
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
ALLOWED_CALLS = frozenset({
    ("GET", "/api/v3/time"), ("GET", "/api/v3/exchangeInfo"), ("GET", "/api/v3/ticker/bookTicker"),
    ("GET", "/api/v3/klines"), ("GET", "/api/v3/account"), ("GET", "/api/v3/order"), ("GET", "/api/v3/allOrders"),
    ("GET", "/api/v3/myTrades"), ("POST", "/api/v3/order"), ("DELETE", "/api/v3/order"),
})
SIGNED_PATHS = frozenset({"/api/v3/account", "/api/v3/order", "/api/v3/allOrders", "/api/v3/myTrades"})
_STATUS = {"NEW": BrokerOrderStatus.ACCEPTED, "PENDING_NEW": BrokerOrderStatus.ACCEPTED,
           "PARTIALLY_FILLED": BrokerOrderStatus.PARTIALLY_FILLED, "FILLED": BrokerOrderStatus.FILLED,
           "CANCELED": BrokerOrderStatus.CANCELLED, "PENDING_CANCEL": BrokerOrderStatus.ACCEPTED,
           "EXPIRED": BrokerOrderStatus.CANCELLED, "EXPIRED_IN_MATCH": BrokerOrderStatus.CANCELLED,
           "REJECTED": BrokerOrderStatus.REJECTED}

Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, dict[str, str], bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None,
                      timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read(MAX_RESPONSE_BYTES + 1)


@dataclass(frozen=True, slots=True)
class DemoBalances:
    as_of: datetime
    balances: dict[str, tuple[Decimal, Decimal]]  # asset: (free, locked)
    account_type: str
    can_trade: bool


class BinanceSpotDemoAdapter(BrokerAdapter):
    """Binance Spot Demo Mode. Instruments are an explicit allow-list (for example ("BTCUSDT",))."""

    def __init__(self, api_key: SecretValue, api_secret: SecretValue, instruments: tuple[str, ...], *,
                 base_url: str = DEMO_BASE_URL, loopback_role: str | None = None, transport: Transport | None = None,
                 clock: Callable[[], datetime] | None = None, paused: Callable[[], bool] | None = None,
                 demo_trading_allowed: Callable[[], bool] | None = None, test_only_trust_loopback: bool = False) -> None:
        if not isinstance(api_key, SecretValue) or not isinstance(api_secret, SecretValue):
            raise BrokerUnavailable("Binance DEMO credentials must be SecretValue instances")
        self._base = _parse_base_url(base_url, frozenset({"demo"}), loopback_role)
        if self._base.role != "demo":  # pragma: no cover - _parse_base_url already refuses
            raise BrokerUnavailable("only the Binance Demo Mode host is accepted")
        if self._base.loopback and not test_only_trust_loopback:
            raise BrokerUnavailable("a loopback host is accepted only for offline tests")
        symbols = tuple(s.strip().upper() for s in instruments)
        if not symbols or any(not s.isalnum() for s in symbols):
            raise BrokerUnavailable("instruments must be an explicit list of Binance symbols")
        self._key, self._secret = api_key, api_secret
        self._instruments = symbols
        self._transport = transport or _urllib_transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._paused = paused or (lambda: True)  # no pause source: refuse to trade
        self._demo_allowed = demo_trading_allowed or (lambda: False)
        self._trust_loopback = test_only_trust_loopback
        self._offset = timedelta(0)
        self._state = ConnectionState.DISCONNECTED
        self._account_confirmed = False
        self._blocked_until: datetime | None = None
        self._specs: dict[str, CryptoSpotSpec] = {}
        self._rules: dict[str, BinanceSymbolRules] = {}
        self._specs_at: datetime | None = None
        self._sent_at: dict[str, datetime] = {}
        self.last_error: str | None = None

    def __repr__(self) -> str:
        return f"BinanceSpotDemoAdapter(base_url={self._base.url!r}, instruments={self._instruments!r}, api_key=****)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("BinanceSpotDemoAdapter cannot be pickled")

    # -- HTTP ---------------------------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    def _boundary_now(self) -> datetime:
        return self._clock()

    def _scrub(self, text: str) -> str:
        for secret in (self._secret.reveal(), self._key.reveal()):
            text = text.replace(secret, "****")
        return text

    def _call(self, method: str, path: str, params: list[tuple[str, str]] | None = None) -> Any:
        if (method, path) not in ALLOWED_CALLS:
            raise BrokerUnavailable(f"call {method} {path} is not allowed for Binance DEMO")
        if self._blocked_until and self._now() < self._blocked_until:
            raise BrokerUnavailable(f"Binance DEMO rate limit: blocked until {self._blocked_until.isoformat()}")
        items = list(params or [])
        headers = {"Accept": "application/json", "User-Agent": f"{ADAPTER_NAME}/{ADAPTER_VERSION}"}
        if path in SIGNED_PATHS:
            items += [("recvWindow", str(RECV_WINDOW_MS)),
                      ("timestamp", str(int((self._now() + self._offset).timestamp() * 1000)))]
            query = urllib.parse.urlencode(items)
            signature = hmac.new(self._secret.reveal().encode(), query.encode(), hashlib.sha256).hexdigest()
            query = f"{query}&signature={signature}"
            headers["X-MBX-APIKEY"] = self._key.reveal()
        else:
            query = urllib.parse.urlencode(items)
        url = f"{self._base.url}{path}" + (f"?{query}" if query else "")
        try:
            status, response_headers, body = self._transport(method, url, headers, None, REQUEST_TIMEOUT_SECONDS)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            self._state = ConnectionState.DEGRADED
            self.last_error = f"{type(exc).__name__}"
            if method == "POST":
                raise BrokerTimeout(f"Binance DEMO did not answer the order request ({type(exc).__name__})") from exc
            raise BrokerUnavailable(f"Binance DEMO unreachable ({type(exc).__name__})") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise BrokerUnavailable("Binance DEMO response too large")
        if status in (418, 429):
            retry = _retry_after(response_headers)
            self._blocked_until = self._now() + timedelta(seconds=retry if retry is not None else 60)
            self._state = ConnectionState.DEGRADED
            raise BrokerRequestError(f"http_{status}", "Binance DEMO rate limit", retry, transient=True)
        if status >= 500:
            self._state = ConnectionState.DEGRADED
            if method == "POST":
                raise BrokerTimeout(f"Binance DEMO HTTP {status} on an order request: outcome unknown")
            raise BrokerUnavailable(f"Binance DEMO HTTP {status}")
        try:
            payload = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if method == "POST":
                raise BrokerTimeout("Binance DEMO order answer unreadable: outcome unknown") from exc
            raise BrokerUnavailable("Binance DEMO answer is not JSON") from exc
        if status >= 400:
            code = str(payload.get("code")) if isinstance(payload, dict) else f"http_{status}"
            message = self._scrub(str(payload.get("msg", ""))) if isinstance(payload, dict) else ""
            raise BrokerRequestError(code, message)
        return payload

    def sync_time(self) -> timedelta:
        data = self._call("GET", "/api/v3/time")
        server = datetime.fromtimestamp(int(data["serverTime"]) / 1000, UTC)
        self._offset = server - self._now()
        return self._offset

    # -- reads ----------------------------------------------------------------------------------

    def refresh_specs(self) -> dict[str, CryptoSpotSpec]:
        params = [("symbols", json.dumps(list(self._instruments), separators=(",", ":")))]
        data = self._call("GET", "/api/v3/exchangeInfo", params)
        now = self._now()
        rules = {}
        for entry in data.get("symbols", []) if isinstance(data, dict) else []:
            rule = parse_symbol_rules(entry, now)
            rules[rule.symbol] = rule
        missing = [s for s in self._instruments if s not in rules]
        if missing:
            raise BrokerUnavailable(f"exchangeInfo has no rules for {', '.join(missing)}")
        self._rules = rules
        # Demo fees are not reported by exchangeInfo; the documented standard Spot taker rate is used as an estimate.
        self._specs = {s: rules[s].to_spec(Decimal("0.001"), Decimal("0.001")) for s in self._instruments}
        self._specs_at = now
        return dict(self._specs)

    def spot_specs(self, max_age_seconds: float = 300.0) -> dict[str, CryptoSpotSpec]:
        if self._specs_at is None or (self._now() - self._specs_at).total_seconds() > max_age_seconds:
            self.refresh_specs()
        return dict(self._specs)

    def balances(self) -> DemoBalances:
        data = self._call("GET", "/api/v3/account", [("omitZeroBalances", "true")])
        if not isinstance(data, dict) or "balances" not in data:
            raise BrokerUnavailable("Binance DEMO account answer has no balances")
        balances = {b["asset"]: (Decimal(str(b["free"])), Decimal(str(b["locked"]))) for b in data["balances"]}
        self._account_confirmed = data.get("accountType") == "SPOT"
        self._state = ConnectionState.CONNECTED if self._account_confirmed else ConnectionState.DEGRADED
        self.last_error = None if self._account_confirmed else f"accountType {data.get('accountType')!r}"
        return DemoBalances(self._now(), balances, str(data.get("accountType")), bool(data.get("canTrade")))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            adapter_name=ADAPTER_NAME, adapter_version=ADAPTER_VERSION, declared_environment=BrokerEnvironment.DEMO,
            environment_source=(EnvironmentSource.PROVIDER_API if not self._base.loopback or self._trust_loopback
                                else EnvironmentSource.UNVERIFIED),
            instruments=self._instruments, order_types=("MARKET",), supports_order_lookup_by_client_id=True,
            supports_cancel=True, supports_modify=False, supports_positions=True, supports_event_stream=False,
            supports_quotes=True, supports_historical_data=True, official_documentation_url=DEMO_DOCS_URL,
            verified_on=DOCS_VERIFIED_ON,
            limitations=("Demo Mode prices are realistic, not real", "spot only: long or flat, no short",
                         "stops are managed by ASTRA, not placed at the provider", "no event stream: polling"),
        )

    def connection_state(self) -> ConnectionState:
        return self._state

    def account_snapshot(self) -> AccountSnapshot:
        snapshot = self.balances()
        free, locked = snapshot.balances.get("USDT", (Decimal(0), Decimal(0)))
        verified = self._account_confirmed and (not self._base.loopback or self._trust_loopback)
        return AccountSnapshot(
            account_ref_masked=f"{VENUE_ID}:{self._key.reveal()[:4]}****",
            environment_reported=BrokerEnvironment.DEMO if verified else BrokerEnvironment.UNKNOWN,
            environment_source=EnvironmentSource.PROVIDER_API if verified else EnvironmentSource.UNVERIFIED,
            currency="USDT", balance=float(free + locked), equity=float(free + locked), margin_used=0.0,
            margin_free=float(free), as_of=snapshot.as_of)

    def quote(self, instrument: str) -> Quote:
        symbol = self._require_instrument(instrument)
        data = self._call("GET", "/api/v3/ticker/bookTicker", [("symbol", symbol)])
        bid, ask = float(data["bidPrice"]), float(data["askPrice"])
        if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask < bid:
            raise BrokerUnavailable("Binance DEMO book ticker is not usable")
        return Quote(symbol, bid, ask, self._now(), f"PROVIDER_API:{VENUE_ID}:bookTicker")

    def klines(self, instrument: str, interval: str, limit: int = 500) -> list[list[Any]]:
        symbol = self._require_instrument(instrument)
        return self._call("GET", "/api/v3/klines", [("symbol", symbol), ("interval", interval), ("limit", str(int(limit)))])

    def _report(self, data: dict[str, Any], requested: float) -> BrokerOrderReport:
        status = _STATUS.get(str(data.get("status")))
        if status is None:
            raise BrokerUnavailable(f"unknown Binance order status {data.get('status')!r}")
        executed = Decimal(str(data.get("executedQty", "0")))
        quote_qty = Decimal(str(data.get("cummulativeQuoteQty", "0")))
        base_fee = Decimal(0)
        symbol = str(data.get("symbol", ""))
        base_asset = self._rules[symbol].base_asset if symbol in self._rules else None
        for fill in data.get("fills", []) or []:
            if base_asset and fill.get("commissionAsset") == base_asset:
                base_fee += Decimal(str(fill.get("commission", "0")))
        filled = executed - base_fee if data.get("side") == "BUY" else executed
        average = float(quote_qty / executed) if executed > 0 else None
        moment = int(data.get("updateTime") or data.get("transactTime") or data.get("time") or 0)
        return BrokerOrderReport(
            client_order_id=str(data.get("clientOrderId")), broker_order_id=str(data.get("orderId")), status=status,
            requested_units=requested, filled_units=float(filled), average_fill_price=average, sequence=moment,
            timestamp=datetime.fromtimestamp(moment / 1000, UTC) if moment else self._now(),
            message=f"binance:{data.get('status')}")

    def lookup_order(self, client_order_id: str) -> BrokerOrderReport | None:
        """The provider's state of one ASTRA order; None only once it can no longer arrive (recvWindow)."""

        for symbol in self._instruments:
            try:
                data = self._call("GET", "/api/v3/order", [("symbol", symbol), ("origClientOrderId", client_order_id)])
            except BrokerRequestError as exc:
                if exc.error_code == "-2013":  # order does not exist (for this symbol)
                    continue
                raise
            return self._report(data, float(data.get("origQty", "0")))
        sent = self._sent_at.get(client_order_id)
        if sent is not None and self._now() < sent + timedelta(milliseconds=RECV_WINDOW_MS) + timedelta(seconds=5):
            raise BrokerUnavailable("order not visible yet: it may still arrive within the receive window")
        return None

    def astra_history(self, symbol: str, limit: int = 500) -> list[dict[str, Any]]:
        orders = self._call("GET", "/api/v3/allOrders", [("symbol", self._require_instrument(symbol)),
                                                         ("limit", str(int(limit)))])
        return [o for o in orders if str(o.get("clientOrderId", "")).startswith(CLIENT_ID_PREFIX)]

    def trades(self, symbol: str, limit: int = 500) -> list[dict[str, Any]]:
        return self._call("GET", "/api/v3/myTrades", [("symbol", self._require_instrument(symbol)), ("limit", str(int(limit)))])

    def ledger(self, symbol: str) -> SpotLedger:
        """ASTRA's own fills in one symbol (client ids ASTRA-...), net of fees, from the provider's history."""

        symbol = self._require_instrument(symbol)
        rules = self._rules.get(symbol) or (self.refresh_specs() and self._rules[symbol])
        own = {o["orderId"] for o in self.astra_history(symbol)}
        return ledger_from_trades(self.trades(symbol), own, rules.base_asset, rules.quote_asset)

    def open_positions(self) -> tuple[BrokerPosition, ...]:
        positions = []
        for symbol in self._instruments:
            book = self.ledger(symbol)
            # Below one quantity step the remainder cannot be sold: it is dust, not a position.
            if book.net_base >= self._rules[symbol].step_size:
                cost = -book.cash_flow_quote
                positions.append(BrokerPosition(symbol, float(book.net_base),
                                                float(cost / book.net_base) if cost > 0 else 0.0))
        return tuple(positions)

    def cancel_order(self, client_order_id: str) -> BrokerOrderReport:
        for symbol in self._instruments:
            try:
                data = self._call("DELETE", "/api/v3/order", [("symbol", symbol), ("origClientOrderId", client_order_id)])
            except BrokerRequestError as exc:
                if exc.error_code == "-2011":  # unknown order for this symbol
                    continue
                raise
            return self._report(data, float(data.get("origQty", "0")))
        raise BrokerRequestError("-2011", "unknown order")

    def poll_events(self, after_sequence: int) -> tuple[BrokerEvent, ...]:
        return ()

    # -- the order --------------------------------------------------------------------------------

    def _require_instrument(self, instrument: str) -> str:
        symbol = (instrument or "").strip().upper()
        if symbol not in self._instruments:
            raise BrokerRequestError("instrument_not_allowed", f"{symbol} is not in the DEMO allow-list")
        return symbol

    def _environment_guard(self, request: BrokerOrderRequest, permit: object) -> str | None:
        if getattr(permit, "mode", None) is not RunMode.DEMO:
            return "demo_adapter_requires_demo_permit"
        if self._base.role != "demo" or (self._base.loopback and not self._trust_loopback):
            return "host_is_not_binance_demo"
        if not self._account_confirmed:
            return "demo_account_not_confirmed"
        if self._state is not ConnectionState.CONNECTED:
            return "demo_connection_not_healthy"
        try:
            if not self._demo_allowed():
                return "venue_demo_gate_closed"
            if self._paused():
                return "demo_execution_paused"
        except Exception:  # noqa: BLE001 - an unreadable switch means stop
            return "demo_switch_unreadable"
        if request.instrument.upper() not in self._instruments:
            return "instrument_not_allowed"
        if request.order_type != "MARKET":
            return "order_type_not_supported"
        if not request.client_order_id.startswith(CLIENT_ID_PREFIX) or len(request.client_order_id) > 36:
            return "client_order_id_invalid"
        return None

    def _send_order(self, request: BrokerOrderRequest) -> BrokerOrderReport:
        symbol = self._require_instrument(request.instrument)
        spec = self.spot_specs()[symbol]
        quantity = spec.round_quantity_down(Decimal(repr(float(request.units))))
        if quantity <= 0:
            raise BrokerRequestError("quantity_not_on_step", f"{request.units} is below the step {spec.step_size}")
        side = "BUY" if request.direction is Direction.LONG else "SELL"
        self._sent_at[request.client_order_id] = self._now()
        data = self._call("POST", "/api/v3/order", [
            ("symbol", symbol), ("side", side), ("type", "MARKET"), ("quantity", format(quantity, "f")),
            ("newClientOrderId", request.client_order_id), ("newOrderRespType", "FULL")])
        if not isinstance(data, dict) or data.get("clientOrderId") != request.client_order_id:
            raise BrokerTimeout("Binance DEMO answer does not name this order: outcome unknown")
        return self._report(data, float(quantity))


def _retry_after(headers: dict[str, str]) -> float | None:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                seconds = float(value)
            except ValueError:
                return None
            return seconds if math.isfinite(seconds) and 0 <= seconds <= 86400 else None
    return None


def load_demo_credentials(env: dict[str, str]) -> tuple[SecretValue, SecretValue]:
    """The DEMO key from the environment; any production or LIVE credential variable stops the start."""

    for name in env:
        upper = name.upper()
        if upper.startswith(("ASTRA_SECRET_BINANCE", "BINANCE_API")) or ("LIVE" in upper and "BINANCE" in upper):
            raise BrokerUnavailable(f"refusing to start: {name} looks like a non-DEMO Binance credential")
    key, secret = env.get("BINANCE_DEMO_API_KEY", ""), env.get("BINANCE_DEMO_API_SECRET", "")
    if not key or not secret:
        raise BrokerUnavailable("BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET are not configured")
    return SecretValue(key), SecretValue(secret)
