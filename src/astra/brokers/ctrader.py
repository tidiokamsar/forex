"""cTrader Open API adapter (JSON over WebSocket), DEMO host only.

Protocol facts were read from https://help.ctrader.com/open-api/ on
2026-10-25 (messages, model-messages, sending-receiving-json, account
authentication, proxies-endpoints, symbol-data, profit-loss-calculation).
Assumptions not settled by those pages, to confirm on the first supervised
demo connection:

- JSON is sent in WebSocket text frames (the docs do not state text or binary;
  the transport accepts either on receive).
- Enum fields are sent as their numeric values and int64 fields as JSON numbers.
- Equity is derived as balance + sum(netUnrealizedPnL) and free margin as
  equity - sum(position.usedMargin); the docs give no equity formula.
- The margin rate in the instrument specification is derived from account
  leverage (``100 / leverageInCents``); dynamic leverage is not modelled.

The live host is never used: this module only builds a transport to
``demo.ctraderapi.com``, and an account reported with ``isLive: true`` is
refused at connection time.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Protocol

from ..governance import SecretValue
from ..instruments import InstrumentSpec
from ..models import Direction
from .base import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerEnvironment,
    BrokerEvent,
    BrokerOrderReport,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerTimeout,
    BrokerUnavailable,
    Capabilities,
    CapabilityNotSupported,
    ConnectionState,
    EnvironmentSource,
    Quote,
)


DEMO_HOST = "demo.ctraderapi.com"
JSON_PORT = 5036
DOCS_URL = "https://help.ctrader.com/open-api/"
DOCS_VERIFIED_ON = "2026-10-25"
HEARTBEAT_SECONDS = 10.0
PRICE_SCALE = 100_000
VOLUME_SCALE = 100


class PayloadType(IntEnum):
    # ProtoPayloadType (common)
    ERROR_RES = 50
    HEARTBEAT_EVENT = 51
    # ProtoOAPayloadType
    APPLICATION_AUTH_REQ = 2100
    APPLICATION_AUTH_RES = 2101
    ACCOUNT_AUTH_REQ = 2102
    ACCOUNT_AUTH_RES = 2103
    NEW_ORDER_REQ = 2106
    CLOSE_POSITION_REQ = 2111
    ASSET_LIST_REQ = 2112
    ASSET_LIST_RES = 2113
    SYMBOLS_LIST_REQ = 2114
    SYMBOLS_LIST_RES = 2115
    SYMBOL_BY_ID_REQ = 2116
    SYMBOL_BY_ID_RES = 2117
    TRADER_REQ = 2121
    TRADER_RES = 2122
    TRADER_UPDATE_EVENT = 2123
    RECONCILE_REQ = 2124
    RECONCILE_RES = 2125
    EXECUTION_EVENT = 2126
    SUBSCRIBE_SPOTS_REQ = 2127
    SUBSCRIBE_SPOTS_RES = 2128
    SPOT_EVENT = 2131
    ORDER_ERROR_EVENT = 2132
    MARGIN_CHANGED_EVENT = 2141
    OA_ERROR_RES = 2142
    ACCOUNTS_TOKEN_INVALIDATED_EVENT = 2147
    CLIENT_DISCONNECT_EVENT = 2148
    GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ = 2149
    GET_ACCOUNTS_BY_ACCESS_TOKEN_RES = 2150
    GET_POSITION_UNREALIZED_PNL_REQ = 2187
    GET_POSITION_UNREALIZED_PNL_RES = 2188


class ExecutionType(IntEnum):
    ORDER_ACCEPTED = 2
    ORDER_FILLED = 3
    ORDER_REPLACED = 4
    ORDER_CANCELLED = 5
    ORDER_EXPIRED = 6
    ORDER_REJECTED = 7
    ORDER_CANCEL_REJECTED = 8
    SWAP = 9
    DEPOSIT_WITHDRAW = 10
    ORDER_PARTIAL_FILL = 11
    BONUS_DEPOSIT_WITHDRAW = 12


ORDER_TYPE_MARKET = 1
TRADE_SIDE_BUY = 1
TRADE_SIDE_SELL = 2
POSITION_STATUS_OPEN = 1

_EXECUTION_STATUS = {
    ExecutionType.ORDER_ACCEPTED: BrokerOrderStatus.ACCEPTED,
    ExecutionType.ORDER_FILLED: BrokerOrderStatus.FILLED,
    ExecutionType.ORDER_PARTIAL_FILL: BrokerOrderStatus.PARTIALLY_FILLED,
    ExecutionType.ORDER_CANCELLED: BrokerOrderStatus.CANCELLED,
    ExecutionType.ORDER_EXPIRED: BrokerOrderStatus.CANCELLED,
    ExecutionType.ORDER_REJECTED: BrokerOrderStatus.REJECTED,
}
_ERROR_TYPES = frozenset({PayloadType.ERROR_RES, PayloadType.OA_ERROR_RES})
NEVER_SENT_BY_CHECK = frozenset({int(PayloadType.NEW_ORDER_REQ), int(PayloadType.CLOSE_POSITION_REQ)})


class JsonTransport(Protocol):
    def connect(self) -> None: ...
    def send(self, message: dict[str, Any]) -> None: ...
    def recv(self, timeout: float | None = None) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


class CTraderError(BrokerUnavailable):
    def __init__(self, error_code: str, description: str = "") -> None:
        super().__init__(f"{error_code}: {description}".strip(": "))
        self.error_code = error_code
        self.description = description


class LiveAccountRefused(BrokerUnavailable):
    """The provider reports a live account; this build never trades live."""


def create_demo_transport(timeout: float = 10.0):
    """The only network transport this module creates: the documented demo host."""

    from .websocket import WebSocketJsonTransport

    return WebSocketJsonTransport(f"wss://{DEMO_HOST}:{JSON_PORT}", timeout=timeout)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CTraderSession:
    """Request/response correlation by clientMsgId, event fan-out, heartbeat."""

    def __init__(self, transport: JsonTransport, *, heartbeat_seconds: float = HEARTBEAT_SECONDS) -> None:
        self._transport = transport
        self._heartbeat = heartbeat_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._handlers: list[Callable[[dict[str, Any]], None]] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.alive = False
        self.failure: str | None = None

    def add_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handlers.append(handler)

    def start(self) -> None:
        self._transport.connect()
        self.alive = True
        for target in (self._read_loop, self._heartbeat_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self.alive = False
        try:
            self._transport.close()
        except OSError:
            pass
        with self._lock:
            for slot in self._pending.values():
                slot["event"].set()

    def send(self, payload_type: int, payload: dict[str, Any], client_msg_id: str | None = None) -> str:
        message_id = client_msg_id or uuid.uuid4().hex
        try:
            self._transport.send({"clientMsgId": message_id, "payloadType": int(payload_type), "payload": payload})
        except OSError as exc:
            self._fail(f"send failed: {exc}")
            raise BrokerUnavailable("cTrader connection lost while sending") from exc
        return message_id

    def request(
        self,
        payload_type: int,
        payload: dict[str, Any],
        expect: set[int],
        timeout: float,
        client_msg_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.alive:
            raise BrokerUnavailable("cTrader session is not connected")
        message_id = client_msg_id or uuid.uuid4().hex
        slot = {"event": threading.Event(), "expect": set(expect), "message": None}
        with self._lock:
            self._pending[message_id] = slot
        try:
            self.send(payload_type, payload, message_id)
            if not slot["event"].wait(timeout):
                raise BrokerTimeout(f"no response to payloadType {int(payload_type)} within {timeout}s")
            message = slot["message"]
            if message is None:
                raise BrokerUnavailable(self.failure or "cTrader session closed")
            if message.get("payloadType") in _ERROR_TYPES:
                body = message.get("payload") or {}
                raise CTraderError(str(body.get("errorCode", "UNKNOWN")), str(body.get("description", "")))
            return message
        finally:
            with self._lock:
                self._pending.pop(message_id, None)

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._transport.recv(timeout=1.0)
            except (OSError, ValueError) as exc:
                self._fail(f"receive failed: {exc}")
                return
            if message is None:
                continue
            self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        payload_type = message.get("payloadType")
        if payload_type == PayloadType.HEARTBEAT_EVENT:
            return
        # Handlers run first so state (e.g. an execution report) is recorded
        # before a waiting request is woken.
        for handler in self._handlers:
            handler(message)
        message_id = message.get("clientMsgId")
        if message_id:
            with self._lock:
                slot = self._pending.get(message_id)
            if slot is not None and (payload_type in slot["expect"] or payload_type in _ERROR_TYPES):
                slot["message"] = message
                slot["event"].set()
        if payload_type in (PayloadType.CLIENT_DISCONNECT_EVENT, PayloadType.ACCOUNTS_TOKEN_INVALIDATED_EVENT):
            self._fail(f"provider event {payload_type}")

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat):
            try:
                self.send(PayloadType.HEARTBEAT_EVENT, {})
            except BrokerUnavailable:
                return

    def _fail(self, reason: str) -> None:
        if not self.alive:
            return
        self.failure = reason
        self.alive = False
        self._stop.set()
        with self._lock:
            for slot in self._pending.values():
                slot["event"].set()


@dataclass(frozen=True, slots=True)
class CTraderCredentials:
    client_id: SecretValue
    client_secret: SecretValue
    access_token: SecretValue
    ctid_trader_account_id: int


class CTraderAdapter(BrokerAdapter):
    ADAPTER_NAME = "astra-ctrader-open-api-json"
    ADAPTER_VERSION = "0.1.0"

    def __init__(
        self,
        credentials: CTraderCredentials,
        *,
        transport: JsonTransport | None = None,
        instruments: tuple[str, ...] = ("EURUSD",),
        request_timeout: float = 10.0,
        clock: Callable[[], datetime] = _utc_now,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self._credentials = credentials
        self._transport = transport if transport is not None else create_demo_transport(request_timeout)
        self._instruments = tuple(name.upper() for name in instruments)
        self._timeout = request_timeout
        self._clock = clock
        self._heartbeat = heartbeat_seconds
        self._session: CTraderSession | None = None
        self._lock = threading.RLock()
        self._is_live: bool | None = None
        self._authorized = False
        self._trader: dict[str, Any] = {}
        self._assets: dict[int, str] = {}
        self._symbols: dict[str, dict[str, Any]] = {}
        self._symbol_names: dict[int, str] = {}
        self._quotes: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, BrokerOrderReport] = {}
        self._order_clients: dict[int, str] = {}
        self._message_clients: dict[str, str] = {}
        self._events: list[BrokerEvent] = []
        self._sequence = itertools.count(1)
        self.sent_payload_types: list[int] = []

    # -- connection -------------------------------------------------------

    def connect(self) -> None:
        """Authorize app and account, load account, assets, symbols and spots."""

        account_id = self._credentials.ctid_trader_account_id
        session = CTraderSession(self._transport, heartbeat_seconds=self._heartbeat)
        session.add_handler(self._on_message)
        session.start()
        self._session = session
        try:
            self._request(
                PayloadType.APPLICATION_AUTH_REQ,
                {
                    "clientId": self._credentials.client_id.reveal(),
                    "clientSecret": self._credentials.client_secret.reveal(),
                },
                {PayloadType.APPLICATION_AUTH_RES},
            )
            accounts = self._request(
                PayloadType.GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ,
                {"accessToken": self._credentials.access_token.reveal()},
                {PayloadType.GET_ACCOUNTS_BY_ACCESS_TOKEN_RES},
            )["payload"].get("ctidTraderAccount", [])
            match = [a for a in accounts if int(a.get("ctidTraderAccountId", -1)) == account_id]
            if not match:
                raise BrokerUnavailable("configured ctidTraderAccountId is not granted to this access token")
            is_live = match[0].get("isLive")
            self._is_live = is_live if isinstance(is_live, bool) else None
            if self._is_live is True:
                raise LiveAccountRefused("provider reports isLive=true; LIVE is locked in this build")
            self._request(
                PayloadType.ACCOUNT_AUTH_REQ,
                {"ctidTraderAccountId": account_id, "accessToken": self._credentials.access_token.reveal()},
                {PayloadType.ACCOUNT_AUTH_RES},
            )
            self._authorized = True
            self._refresh_trader()
            assets = self._request(
                PayloadType.ASSET_LIST_REQ, {"ctidTraderAccountId": account_id}, {PayloadType.ASSET_LIST_RES}
            )["payload"].get("asset", [])
            self._assets = {int(a["assetId"]): str(a["name"]) for a in assets}
            light = self._request(
                PayloadType.SYMBOLS_LIST_REQ, {"ctidTraderAccountId": account_id}, {PayloadType.SYMBOLS_LIST_RES}
            )["payload"].get("symbol", [])
            wanted = {s["symbolName"].upper(): int(s["symbolId"]) for s in light if str(s.get("symbolName", "")).upper() in self._instruments}
            missing = set(self._instruments) - set(wanted)
            if missing:
                raise BrokerUnavailable(f"symbols not offered on this account: {sorted(missing)}")
            details = self._request(
                PayloadType.SYMBOL_BY_ID_REQ,
                {"ctidTraderAccountId": account_id, "symbolId": sorted(wanted.values())},
                {PayloadType.SYMBOL_BY_ID_RES},
            )["payload"].get("symbol", [])
            by_id = {int(s["symbolId"]): s for s in details}
            for name, symbol_id in wanted.items():
                if symbol_id not in by_id:
                    raise BrokerUnavailable(f"no specification returned for {name}")
                self._symbols[name] = by_id[symbol_id]
                self._symbol_names[symbol_id] = name
            self._request(
                PayloadType.SUBSCRIBE_SPOTS_REQ,
                {"ctidTraderAccountId": account_id, "symbolId": sorted(wanted.values()), "subscribeToSpotTimestamp": True},
                {PayloadType.SUBSCRIBE_SPOTS_RES},
            )
        except BaseException:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        if self._session is not None:
            self._session.stop()
        self._authorized = False

    # -- BrokerAdapter ----------------------------------------------------

    def capabilities(self) -> Capabilities:
        environment = BrokerEnvironment.DEMO if self._is_live is False else BrokerEnvironment.UNKNOWN
        return Capabilities(
            adapter_name=self.ADAPTER_NAME,
            adapter_version=self.ADAPTER_VERSION,
            declared_environment=environment,
            environment_source=EnvironmentSource.PROVIDER_API if self._is_live is False else EnvironmentSource.UNVERIFIED,
            instruments=tuple(sorted(self._symbols)),
            order_types=("MARKET",),
            supports_order_lookup_by_client_id=False,
            supports_cancel=False,
            supports_modify=False,
            supports_positions=True,
            supports_event_stream=True,
            supports_quotes=True,
            supports_historical_data=False,
            official_documentation_url=DOCS_URL,
            verified_on=DOCS_VERIFIED_ON,
            limitations=(
                "Demo host only; isLive=true accounts are refused.",
                "No documented lookup by clientOrderId: unresolved orders require human review.",
                "Equity and free margin are derived (balance + net unrealized P&L - used margin).",
                "Margin rate derived from account leverage; dynamic leverage not modelled.",
                "JSON frame type (text) and numeric enum encoding not yet confirmed against the provider.",
                "Netted single-position-per-symbol use only; hedged accounts with several positions refuse exits.",
            ),
        )

    def connection_state(self) -> ConnectionState:
        session = self._session
        if session is None or not session.alive or not self._authorized:
            return ConnectionState.DISCONNECTED
        return ConnectionState.CONNECTED

    def account_snapshot(self) -> AccountSnapshot:
        self._require_connection()
        trader = self._refresh_trader()
        digits = int(trader.get("moneyDigits", 2))
        balance = int(trader["balance"]) / 10**digits
        pnl_payload = self._request(
            PayloadType.GET_POSITION_UNREALIZED_PNL_REQ,
            {"ctidTraderAccountId": self._credentials.ctid_trader_account_id},
            {PayloadType.GET_POSITION_UNREALIZED_PNL_RES},
        )["payload"]
        pnl_digits = int(pnl_payload.get("moneyDigits", digits))
        unrealized = sum(int(p.get("netUnrealizedPnL", 0)) for p in pnl_payload.get("positionUnrealizedPnL", [])) / 10**pnl_digits
        used_margin = 0.0
        for position in self._reconcile()["position"]:
            position_digits = int(position.get("moneyDigits", digits))
            used_margin += int(position.get("usedMargin", 0)) / 10**position_digits
        equity = balance + unrealized
        currency = self._assets.get(int(trader.get("depositAssetId", -1)), "UNKNOWN")
        account_id = str(self._credentials.ctid_trader_account_id)
        return AccountSnapshot(
            account_ref_masked=f"CTRADER-****{account_id[-4:]}",
            environment_reported=BrokerEnvironment.DEMO if self._is_live is False else BrokerEnvironment.UNKNOWN,
            environment_source=EnvironmentSource.PROVIDER_API if self._is_live is False else EnvironmentSource.UNVERIFIED,
            currency=currency,
            balance=balance,
            equity=equity,
            margin_used=used_margin,
            margin_free=equity - used_margin,
            as_of=self._clock(),
        )

    def instrument_specs(self) -> dict[str, InstrumentSpec]:
        leverage = int(self._trader.get("leverageInCents", 0) or 0)
        specs: dict[str, InstrumentSpec] = {}
        for name, symbol in self._symbols.items():
            if leverage <= 0 or len(name) != 6:
                continue
            specs[name] = InstrumentSpec(
                symbol=name,
                base_currency=name[:3],
                quote_currency=name[3:],
                pip_size=10 ** -int(symbol["pipPosition"]),
                price_precision=int(symbol["digits"]),
                min_units=int(symbol["minVolume"]) / VOLUME_SCALE,
                unit_step=int(symbol["stepVolume"]) / VOLUME_SCALE,
                max_units=int(symbol["maxVolume"]) / VOLUME_SCALE,
                margin_rate=min(1.0, 100 / leverage),
                source="PROVIDER_API:CTRADER;margin_rate=derived_from_leverage",
            )
        return specs

    def quote(self, instrument: str) -> Quote:
        self._require_connection()
        with self._lock:
            state = self._quotes.get(instrument.upper())
        if not state or state.get("bid") is None or state.get("ask") is None:
            raise BrokerUnavailable(f"no complete cTrader quote yet for {instrument}")
        return Quote(instrument.upper(), state["bid"], state["ask"], state["timestamp"], "PROVIDER_API:CTRADER_SPOT")

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderReport:
        self._require_connection()
        if request.order_type != "MARKET":
            raise CapabilityNotSupported("only MARKET orders are implemented")
        symbol = self._symbols.get(request.instrument.upper())
        if symbol is None:
            raise CapabilityNotSupported(f"{request.instrument} is not loaded on this account")
        with self._lock:
            existing = self._orders.get(request.client_order_id)
        if existing is not None:
            return existing
        volume = int(round(request.units * VOLUME_SCALE))
        account_id = self._credentials.ctid_trader_account_id
        message_id = uuid.uuid4().hex
        with self._lock:
            self._message_clients[message_id] = request.client_order_id
        if request.reduce_only:
            position_id = self._single_position_to_reduce(int(symbol["symbolId"]), request.direction)
            payload_type = PayloadType.CLOSE_POSITION_REQ
            payload = {"ctidTraderAccountId": account_id, "positionId": position_id, "volume": volume}
        else:
            quote = self.quote(request.instrument)
            reference = quote.ask if request.direction is Direction.LONG else quote.bid
            payload_type = PayloadType.NEW_ORDER_REQ
            payload = {
                "ctidTraderAccountId": account_id,
                "symbolId": int(symbol["symbolId"]),
                "orderType": ORDER_TYPE_MARKET,
                "tradeSide": TRADE_SIDE_BUY if request.direction is Direction.LONG else TRADE_SIDE_SELL,
                "volume": volume,
                "clientOrderId": request.client_order_id[:50],
                "label": request.client_order_id[:100],
            }
            if request.stop_loss is not None:
                payload["relativeStopLoss"] = int(round(abs(reference - request.stop_loss) * PRICE_SCALE))
        try:
            response = self._request(
                payload_type,
                payload,
                {PayloadType.EXECUTION_EVENT, PayloadType.ORDER_ERROR_EVENT},
                client_msg_id=message_id,
            )
        except CTraderError as exc:
            return self._record(request.client_order_id, BrokerOrderStatus.REJECTED, request.units, 0.0, None, None, str(exc))
        if response["payloadType"] == PayloadType.ORDER_ERROR_EVENT:
            body = response.get("payload", {})
            return self._record(
                request.client_order_id, BrokerOrderStatus.REJECTED, request.units, 0.0, None, None,
                f"{body.get('errorCode', 'ORDER_ERROR')}: {body.get('description', '')}",
            )
        with self._lock:
            report = self._orders.get(request.client_order_id)
        if report is None:
            raise BrokerTimeout("execution event received but could not be attributed")
        return report

    def lookup_order(self, client_order_id: str) -> BrokerOrderReport | None:
        """Known reports or pending orders only; None is NOT proof of absence."""

        self._require_connection()
        with self._lock:
            known = self._orders.get(client_order_id)
        if known is not None:
            return known
        for order in self._reconcile()["order"]:
            if order.get("clientOrderId") == client_order_id[:50]:
                return self._record(
                    client_order_id, BrokerOrderStatus.ACCEPTED,
                    int(order.get("tradeData", {}).get("volume", 0)) / VOLUME_SCALE, 0.0, None,
                    str(order.get("orderId")), "pending order found by reconcile",
                )
        return None

    def open_positions(self) -> tuple[BrokerPosition, ...]:
        self._require_connection()
        totals: dict[str, list[float]] = {}
        for position in self._reconcile()["position"]:
            if int(position.get("positionStatus", POSITION_STATUS_OPEN)) != POSITION_STATUS_OPEN:
                continue
            trade = position.get("tradeData", {})
            name = self._symbol_names.get(int(trade.get("symbolId", -1)), f"SYMBOL-{trade.get('symbolId')}")
            units = int(trade.get("volume", 0)) / VOLUME_SCALE
            signed = units if int(trade.get("tradeSide", 0)) == TRADE_SIDE_BUY else -units
            entry = totals.setdefault(name, [0.0, 0.0])
            entry[0] += signed
            entry[1] += float(position.get("price", 0.0)) * abs(signed)
        return tuple(
            BrokerPosition(name, net, (weighted / abs(net)) if net else 0.0)
            for name, (net, weighted) in sorted(totals.items())
            if abs(net) > 1e-9
        )

    def cancel_order(self, client_order_id: str) -> BrokerOrderReport:
        raise CapabilityNotSupported("cancel is not implemented for the cTrader adapter")

    def poll_events(self, after_sequence: int) -> tuple[BrokerEvent, ...]:
        self._require_connection()
        with self._lock:
            return tuple(event for event in self._events if event.sequence > after_sequence)

    # -- internals ----------------------------------------------------------

    def _request(self, payload_type, payload, expect, client_msg_id=None) -> dict[str, Any]:
        if self._session is None:
            raise BrokerUnavailable("not connected")
        self.sent_payload_types.append(int(payload_type))
        return self._session.request(payload_type, payload, {int(e) for e in expect}, self._timeout, client_msg_id)

    def _require_connection(self) -> None:
        if self.connection_state() is not ConnectionState.CONNECTED:
            raise BrokerUnavailable("cTrader adapter is not connected and authorized")

    def _refresh_trader(self) -> dict[str, Any]:
        trader = self._request(
            PayloadType.TRADER_REQ,
            {"ctidTraderAccountId": self._credentials.ctid_trader_account_id},
            {PayloadType.TRADER_RES},
        )["payload"]["trader"]
        self._trader = trader
        return trader

    def _reconcile(self) -> dict[str, list[dict[str, Any]]]:
        payload = self._request(
            PayloadType.RECONCILE_REQ,
            {"ctidTraderAccountId": self._credentials.ctid_trader_account_id},
            {PayloadType.RECONCILE_RES},
        )["payload"]
        return {"position": list(payload.get("position", [])), "order": list(payload.get("order", []))}

    def _single_position_to_reduce(self, symbol_id: int, direction: Direction) -> int:
        reducing_side = TRADE_SIDE_SELL if direction is Direction.LONG else TRADE_SIDE_BUY
        candidates = [
            p for p in self._reconcile()["position"]
            if int(p.get("tradeData", {}).get("symbolId", -1)) == symbol_id
            and int(p.get("positionStatus", POSITION_STATUS_OPEN)) == POSITION_STATUS_OPEN
        ]
        if len(candidates) != 1:
            raise CapabilityNotSupported(f"exit needs exactly one open position for the symbol, found {len(candidates)}")
        if int(candidates[0]["tradeData"]["tradeSide"]) != reducing_side:
            raise CapabilityNotSupported("exit direction would not reduce the open position")
        return int(candidates[0]["positionId"])

    def _on_message(self, message: dict[str, Any]) -> None:
        payload_type = message.get("payloadType")
        payload = message.get("payload") or {}
        if payload_type == PayloadType.SPOT_EVENT:
            name = self._symbol_names.get(int(payload.get("symbolId", -1)))
            if name is None:
                return
            digits = int(self._symbols[name].get("digits", 5))
            with self._lock:
                state = self._quotes.setdefault(name, {"bid": None, "ask": None, "timestamp": self._clock()})
                if payload.get("bid") is not None:
                    state["bid"] = round(int(payload["bid"]) / PRICE_SCALE, digits)
                if payload.get("ask") is not None:
                    state["ask"] = round(int(payload["ask"]) / PRICE_SCALE, digits)
                timestamp = payload.get("timestamp")
                state["timestamp"] = (
                    datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc) if timestamp else self._clock()
                )
        elif payload_type == PayloadType.EXECUTION_EVENT:
            self._on_execution(message.get("clientMsgId"), payload)
        elif payload_type == PayloadType.TRADER_UPDATE_EVENT and "trader" in payload:
            self._trader = payload["trader"]

    def _on_execution(self, message_id: str | None, payload: dict[str, Any]) -> None:
        try:
            execution_type = ExecutionType(int(payload.get("executionType", 0)))
        except ValueError:
            return
        status = _EXECUTION_STATUS.get(execution_type)
        order = payload.get("order") or {}
        if status is None or not order:
            return
        order_id = order.get("orderId")
        with self._lock:
            client = (
                (self._message_clients.get(message_id) if message_id else None)
                or order.get("clientOrderId")
                or (self._order_clients.get(int(order_id)) if order_id is not None else None)
            )
            if client is None:
                # Not ours: manual or third-party activity on the account.
                client = f"FOREIGN-{order_id}"
            if order_id is not None:
                self._order_clients[int(order_id)] = client
        trade = order.get("tradeData", {})
        requested = int(trade.get("volume", 0)) / VOLUME_SCALE
        filled = int(order.get("executedVolume", 0) or 0) / VOLUME_SCALE
        deal = payload.get("deal") or {}
        price = order.get("executionPrice", deal.get("executionPrice"))
        self._record(client, status, requested, filled, price, str(order_id) if order_id is not None else None, execution_type.name)

    def _record(self, client, status, requested, filled, price, broker_id, message) -> BrokerOrderReport:
        with self._lock:
            sequence = next(self._sequence)
            report = BrokerOrderReport(
                client_order_id=client,
                broker_order_id=broker_id,
                status=status,
                requested_units=requested,
                filled_units=filled,
                average_fill_price=float(price) if price is not None else None,
                sequence=sequence,
                timestamp=self._clock(),
                message=message,
            )
            self._orders[client] = report
            self._events.append(BrokerEvent(sequence=sequence, report=report))
            return report
