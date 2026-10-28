"""OFFLINE FIXTURE: an in-process fake of Binance Spot Demo Mode for the DEMO adapter tests.

It follows the documented REST shapes (github.com/binance/binance-spot-api-docs, rest-api.md, read 2026-10-28):
signed requests (HMAC-SHA256 of the query, X-MBX-APIKEY), MARKET orders with newOrderRespType=FULL,
GET/DELETE order by origClientOrderId (-2013 / -2011 when unknown), allOrders, myTrades, account balances.
Commission is 0.1 %: on a BUY in the base asset, on a SELL in the quote asset. It is NOT provider traffic
and proves nothing about the real service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from decimal import Decimal

from binance_fixture import symbol_entry

KEY, SECRET = "demoKEY" + "A" * 57, "demoSECRET" + "B" * 54
FEE = Decimal("0.001")


class FakeBinanceDemo:
    def __init__(self, *, bid="80000.00", ask="80000.01", usdt="5000", now_ms=1_793_000_000_000):
        self.bid, self.ask = Decimal(bid), Decimal(ask)
        self.balances = {"USDT": Decimal(usdt), "BTC": Decimal(0)}
        self.now_ms = now_ms
        self.orders: list[dict] = []
        self.trades: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.fail_next: dict[tuple[str, str], str] = {}  # (method, path) -> "timeout" | "500" | "429" | "after_fill_timeout"
        self.account_type = "SPOT"

    # transport(method, url, headers, body, timeout) -> (status, headers, body)
    def __call__(self, method, url, headers, body, timeout):
        parts = urllib.parse.urlsplit(url)
        path, query = parts.path, parts.query
        self.calls.append((method, path))
        failure = self.fail_next.pop((method, path), None)
        if failure == "timeout":
            raise TimeoutError("simulated timeout")
        if failure == "500":
            return 500, {}, b"{}"
        if failure == "429":
            return 429, {"Retry-After": "30"}, b'{"code":-1003,"msg":"Too many requests"}'
        params = dict(urllib.parse.parse_qsl(query))
        if path in ("/api/v3/account", "/api/v3/order", "/api/v3/allOrders", "/api/v3/myTrades"):
            if headers.get("X-MBX-APIKEY") != KEY:
                return 401, {}, json.dumps({"code": -2015, "msg": "Invalid API-key"}).encode()
            unsigned, signature = query.rsplit("&signature=", 1)
            expected = hmac.new(SECRET.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
            if signature != expected:
                return 400, {}, json.dumps({"code": -1022, "msg": "Signature not valid"}).encode()
        answer = self._route(method, path, params)
        if failure == "after_fill_timeout":
            raise TimeoutError("answer lost after the order was filled")
        status, payload = answer
        return status, {}, json.dumps(payload).encode()

    def _route(self, method, path, p):
        if path == "/api/v3/time":
            return 200, {"serverTime": self.now_ms}
        if path == "/api/v3/exchangeInfo":
            return 200, {"symbols": [symbol_entry("BTCUSDT", "BTC", "USDT")]}
        if path == "/api/v3/ticker/bookTicker":
            return 200, {"symbol": p["symbol"], "bidPrice": str(self.bid), "askPrice": str(self.ask)}
        if path == "/api/v3/account":
            return 200, {"accountType": self.account_type, "canTrade": True,
                         "balances": [{"asset": a, "free": str(v), "locked": "0"} for a, v in self.balances.items()]}
        if path == "/api/v3/order" and method == "POST":
            return self._market(p)
        if path == "/api/v3/order":
            found = [o for o in self.orders if o["clientOrderId"] == p.get("origClientOrderId") and o["symbol"] == p["symbol"]]
            if not found:
                return 400, {"code": -2013 if method == "GET" else -2011, "msg": "Order does not exist."}
            if method == "DELETE" and found[0]["status"] == "FILLED":
                return 400, {"code": -2011, "msg": "Unknown order sent."}
            return 200, found[0]
        if path == "/api/v3/allOrders":
            return 200, [o for o in self.orders if o["symbol"] == p["symbol"]]
        if path == "/api/v3/myTrades":
            return 200, [t for t in self.trades if t["symbol"] == p["symbol"]]
        return 404, {"code": -1, "msg": "unknown path"}

    def _market(self, p):
        if any(o["clientOrderId"] == p["newClientOrderId"] for o in self.orders):
            return 400, {"code": -2010, "msg": "Duplicate order sent."}
        qty = Decimal(p["quantity"])
        side = p["side"]
        price = self.ask if side == "BUY" else self.bid
        quote_qty = (qty * price).quantize(Decimal("0.00000001"))
        if side == "BUY":
            if self.balances["USDT"] < quote_qty:
                return 400, {"code": -2010, "msg": "Account has insufficient balance for requested action."}
            commission, asset = (qty * FEE).quantize(Decimal("0.00000001")), "BTC"
            self.balances["USDT"] -= quote_qty
            self.balances["BTC"] += qty - commission
        else:
            if self.balances["BTC"] < qty:
                return 400, {"code": -2010, "msg": "Account has insufficient balance for requested action."}
            commission, asset = (quote_qty * FEE).quantize(Decimal("0.00000001")), "USDT"
            self.balances["BTC"] -= qty
            self.balances["USDT"] += quote_qty - commission
        self.now_ms += 1000
        order_id = len(self.orders) + 1
        order = {"symbol": p["symbol"], "orderId": order_id, "clientOrderId": p["newClientOrderId"],
                 "transactTime": self.now_ms, "updateTime": self.now_ms, "price": "0.00000000",
                 "origQty": str(qty), "executedQty": str(qty), "cummulativeQuoteQty": str(quote_qty),
                 "status": "FILLED", "type": "MARKET", "side": side,
                 "fills": [{"price": str(price), "qty": str(qty), "commission": str(commission),
                            "commissionAsset": asset, "tradeId": order_id}]}
        self.orders.append(order)
        self.trades.append({"symbol": p["symbol"], "id": order_id, "orderId": order_id, "price": str(price),
                            "qty": str(qty), "quoteQty": str(quote_qty), "commission": str(commission),
                            "commissionAsset": asset, "time": self.now_ms, "isBuyer": side == "BUY"})
        return 200, order
