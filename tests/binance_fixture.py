"""OFFLINE FIXTURE: a local fake Binance Spot REST server on 127.0.0.1.

Response shapes follow the official Spot docs (GitHub mirror
https://github.com/binance/binance-spot-api-docs, rest-api.md and filters.md)
and the official wallet client for /sapi/v1/account/apiRestrictions
(https://github.com/binance/binance-connector-python, clients/wallet), read
2026-10-26. They are NOT recorded provider traffic and prove nothing about
the real service's behaviour. Error codes used here (-1022, -2014) are
illustrative fixture values.

Only GET is served; any other method is recorded in ``write_attempts`` and
answered 405.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.parse
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEIGHTS = {
    "/api/v3/ping": 1,
    "/api/v3/time": 1,
    "/api/v3/exchangeInfo": 20,
    "/api/v3/ticker/bookTicker": 2,
    "/api/v3/klines": 2,
    "/api/v3/account": 20,
    "/sapi/v1/account/apiRestrictions": 1,
}
SIGNED = {"/api/v3/account", "/sapi/v1/account/apiRestrictions"}

READ_ONLY_RESTRICTIONS = {
    "ipRestrict": True,
    "createTime": 1623840271000,
    "enableReading": True,
    "enableWithdrawals": False,
    "enableInternalTransfer": False,
    "enableMargin": False,
    "enableFutures": False,
    "permitsUniversalTransfer": False,
    "enableVanillaOptions": False,
    "enableFixApiTrade": False,
    "enableFixReadOnly": True,
    "enableSpotAndMarginTrading": False,
    "enablePortfolioMarginTrading": False,
}


def symbol_entry(symbol, base, quote, *, status="TRADING", tick="0.01000000", step="0.00001000",
                 min_qty="0.00001000", max_qty="9000.00000000", notional=("NOTIONAL", "5.00000000")):
    filters = [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01000000", "maxPrice": "1000000.00000000", "tickSize": tick},
        {"filterType": "LOT_SIZE", "minQty": min_qty, "maxQty": max_qty, "stepSize": step},
    ]
    if notional is not None:
        kind, value = notional
        if kind == "NOTIONAL":
            filters.append({"filterType": "NOTIONAL", "minNotional": value, "applyMinToMarket": True,
                            "maxNotional": "9000000.00000000", "applyMaxToMarket": False, "avgPriceMins": 5})
        else:
            filters.append({"filterType": "MIN_NOTIONAL", "minNotional": value, "applyToMarket": True, "avgPriceMins": 5})
    return {
        "symbol": symbol, "status": status, "baseAsset": base, "baseAssetPrecision": 8, "quoteAsset": quote,
        "quotePrecision": 8, "quoteAssetPrecision": 8, "orderTypes": ["LIMIT", "MARKET"], "icebergAllowed": True,
        "ocoAllowed": True, "isSpotTradingAllowed": True, "isMarginTradingAllowed": False, "filters": filters,
        "permissions": [], "permissionSets": [["SPOT"]],
    }


DEFAULT_SYMBOLS = [
    symbol_entry("BTCUSDT", "BTC", "USDT"),
    symbol_entry("ETHBTC", "ETH", "BTC", tick="0.00001000", step="0.00010000", min_qty="0.00010000",
                 notional=("MIN_NOTIONAL", "0.00010000")),
    symbol_entry("OLDCOIN", "OLD", "USDT", status="BREAK"),
    symbol_entry("NOTICK", "NOT", "USDT", tick="0.00000000"),
]


class Scripted:
    """One scripted reply: status, JSON body, extra headers, optional delay before answering."""

    def __init__(self, status=200, body=None, headers=None, delay=0.0, raw=None, drop=False):
        self.drop = drop  # close the connection without any response
        self.status = status
        self.body = {} if body is None else body
        self.headers = headers or {}
        self.delay = delay
        self.raw = raw


class FakeBinanceServer:
    def __init__(self, *, api_key="test-key-AAAAAAAAAAAAAAAA", api_secret="test-secret-BBBBBBBBBBBBBBBB",
                 clock_ms=None, skew_ms=0):
        self.api_key = api_key
        self.api_secret = api_secret
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.skew_ms = skew_ms
        self.symbols = list(DEFAULT_SYMBOLS)
        self.book = {"BTCUSDT": ("65000.01000000", "1.50000000", "65000.02000000", "2.00000000")}
        self.restrictions = dict(READ_ONLY_RESTRICTIONS)
        self.balances = [
            {"asset": "BTC", "free": "0.50000000", "locked": "0.00000000"},
            {"asset": "USDT", "free": "1000.00000000", "locked": "25.00000000"},
        ]
        self.kline_rows = None  # None: generate from the server clock
        self.scripted = defaultdict(list)  # path -> [Scripted, ...] consumed in order
        self.requests = []  # (path, query dict, headers dict)
        self.write_attempts = []
        self.used_weight = 0
        self.rate_limits = [
            {"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1, "limit": 6000},
            {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 50},
            {"rateLimitType": "RAW_REQUESTS", "interval": "MINUTE", "intervalNum": 5, "limit": 61000},
        ]
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                server._handle(self)

            def _refuse(self):
                server.write_attempts.append((self.command, self.path))
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def base_url(self):
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def now_ms(self):
        return self.clock_ms() + self.skew_ms

    def script(self, path, *replies):
        self.scripted[path].extend(replies)

    def paths(self):
        return [path for path, _query, _headers in self.requests]

    # -- request handling ------------------------------------------------------

    def _handle(self, handler):
        parts = urllib.parse.urlsplit(handler.path)
        path = parts.path
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        with self._lock:
            self.requests.append((path, query, dict(handler.headers.items())))
            self.used_weight += WEIGHTS.get(path, 1)
            reply = self.scripted[path].pop(0) if self.scripted.get(path) else None
        if reply is None:
            reply = self._default(path, parts.query, query, handler.headers)
        if reply.delay:
            time.sleep(reply.delay)
        if reply.drop:
            handler.close_connection = True
            return
        payload = reply.raw if reply.raw is not None else json.dumps(reply.body).encode()
        try:
            handler.send_response(reply.status)
            handler.send_header("Content-Type", "application/json;charset=UTF-8")
            handler.send_header("Content-Length", str(len(payload)))
            handler.send_header("X-MBX-USED-WEIGHT-1M", str(self.used_weight))
            for name, value in reply.headers.items():
                handler.send_header(name, value)
            handler.end_headers()
            handler.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _default(self, path, raw_query, query, headers):
        if path in SIGNED:
            problem = self._check_signature(raw_query, query, headers)
            if problem is not None:
                return problem
        if path == "/api/v3/ping":
            return Scripted(body={})
        if path == "/api/v3/time":
            return Scripted(body={"serverTime": self.now_ms()})
        if path == "/api/v3/exchangeInfo":
            symbols = self.symbols
            if "symbol" in query:
                symbols = [s for s in symbols if s["symbol"] == query["symbol"]]
            elif "symbols" in query:
                wanted = set(json.loads(query["symbols"]))
                symbols = [s for s in symbols if s["symbol"] in wanted]
            return Scripted(body={"timezone": "UTC", "serverTime": self.now_ms(), "rateLimits": self.rate_limits,
                                  "exchangeFilters": [], "symbols": symbols})
        if path == "/api/v3/ticker/bookTicker":
            symbol = query.get("symbol")
            if symbol not in self.book:
                return Scripted(400, {"code": -1121, "msg": "Invalid symbol."})
            bid, bid_qty, ask, ask_qty = self.book[symbol]
            return Scripted(body={"symbol": symbol, "bidPrice": bid, "bidQty": bid_qty, "askPrice": ask, "askQty": ask_qty})
        if path == "/api/v3/klines":
            return Scripted(body=self.kline_rows if self.kline_rows is not None else self._klines(query))
        if path == "/api/v3/account":
            return Scripted(body={
                "makerCommission": 15, "takerCommission": 15, "buyerCommission": 0, "sellerCommission": 0,
                "commissionRates": {"maker": "0.00150000", "taker": "0.00150000", "buyer": "0.00000000", "seller": "0.00000000"},
                "canTrade": True, "canWithdraw": True, "canDeposit": True, "brokered": False,
                "requireSelfTradePrevention": False, "preventSor": False, "updateTime": self.now_ms(),
                "accountType": "SPOT", "balances": self.balances, "permissions": ["SPOT"], "uid": 354937868,
            })
        if path == "/sapi/v1/account/apiRestrictions":
            return Scripted(body=self.restrictions)
        return Scripted(404, {"code": -1, "msg": "not found"})

    def _check_signature(self, raw_query, query, headers):
        if headers.get("X-MBX-APIKEY") != self.api_key:
            return Scripted(401, {"code": -2014, "msg": "API-key format invalid."})
        if "&signature=" not in raw_query:
            return Scripted(400, {"code": -1022, "msg": "Signature for this request is not valid."})
        unsigned, signature = raw_query.rsplit("&signature=", 1)
        expected = hmac.new(self.api_secret.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.lower(), expected):
            return Scripted(400, {"code": -1022, "msg": "Signature for this request is not valid."})
        timestamp = int(query["timestamp"])
        window = int(query.get("recvWindow", 5000))
        if not (timestamp < self.now_ms() + 1000 and self.now_ms() - timestamp <= window):
            return Scripted(400, {"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."})
        return None

    def _klines(self, query, count=5):
        interval_ms = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000}[query["interval"]]
        now = self.now_ms()
        current_open = now - now % interval_ms
        rows = []
        for i in range(count - 1, -1, -1):  # the last row is the still-open candle
            open_ms = current_open - i * interval_ms
            price = 65000 + i
            rows.append(kline_row(open_ms, interval_ms, price))
        return rows


def kline_row(open_ms, interval_ms, price, *, high=None, low=None):
    high = price + 5 if high is None else high
    low = price - 5 if low is None else low
    return [open_ms, f"{price}.00000000", f"{high}.00000000", f"{low}.00000000", f"{price + 1}.00000000",
            "12.50000000", open_ms + interval_ms - 1, "812500.00000000", 100, "6.00000000", "390000.00000000", "0"]
