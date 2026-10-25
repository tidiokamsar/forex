"""OFFLINE FIXTURE: a scripted stand-in for the cTrader Open API JSON proxy.

Message shapes follow the official documentation read on 2026-10-25
(https://help.ctrader.com/open-api/). They are NOT recorded provider traffic
and prove nothing about the real service's behaviour.
"""

from __future__ import annotations

import queue

from astra.brokers.ctrader import PayloadType as P


class FakeCTraderServer:
    def __init__(self, *, is_live=False, include_is_live=True, account_id=4242):
        self.account_id = account_id
        self.is_live = is_live
        self.include_is_live = include_is_live
        self.received = []
        self.order_behavior = "fill"
        self.positions = []
        self.next_order_id = 900
        self.outbox = None
        self.fail_with = {}

    def handle(self, message):
        self.received.append(message)
        kind = message["payloadType"]
        mid = message.get("clientMsgId")
        body = message.get("payload", {})
        def reply(payload_type, payload):
            self.push({"clientMsgId": mid, "payloadType": int(payload_type), "payload": payload})

        if kind == P.HEARTBEAT_EVENT:
            return
        if kind in self.fail_with:
            reply(P.OA_ERROR_RES, {"errorCode": self.fail_with[kind], "description": "scripted error"})
            return
        if kind == P.APPLICATION_AUTH_REQ:
            reply(P.APPLICATION_AUTH_RES, {})
        elif kind == P.GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ:
            account = {"ctidTraderAccountId": self.account_id, "traderLogin": 123}
            if self.include_is_live:
                account["isLive"] = self.is_live
            reply(P.GET_ACCOUNTS_BY_ACCESS_TOKEN_RES, {"accessToken": body["accessToken"], "permissionScope": 1, "ctidTraderAccount": [account]})
        elif kind == P.ACCOUNT_AUTH_REQ:
            reply(P.ACCOUNT_AUTH_RES, {"ctidTraderAccountId": self.account_id})
        elif kind == P.TRADER_REQ:
            reply(P.TRADER_RES, {"ctidTraderAccountId": self.account_id, "trader": {
                "ctidTraderAccountId": self.account_id, "balance": 1_000_000, "moneyDigits": 2,
                "depositAssetId": 1, "leverageInCents": 3000, "accountType": 1}})
        elif kind == P.ASSET_LIST_REQ:
            reply(P.ASSET_LIST_RES, {"asset": [{"assetId": 1, "name": "USD"}, {"assetId": 2, "name": "EUR"}]})
        elif kind == P.SYMBOLS_LIST_REQ:
            reply(P.SYMBOLS_LIST_RES, {"symbol": [{"symbolId": 1, "symbolName": "EURUSD", "enabled": True}, {"symbolId": 2, "symbolName": "GBPUSD"}]})
        elif kind == P.SYMBOL_BY_ID_REQ:
            reply(P.SYMBOL_BY_ID_RES, {"symbol": [{"symbolId": 1, "digits": 5, "pipPosition": 4,
                "minVolume": 100_000, "maxVolume": 1_000_000_000, "stepVolume": 100_000, "lotSize": 10_000_000}]})
        elif kind == P.SUBSCRIBE_SPOTS_REQ:
            reply(P.SUBSCRIBE_SPOTS_RES, {"ctidTraderAccountId": self.account_id})
            self.spot(110_000, 110_010)
        elif kind == P.RECONCILE_REQ:
            reply(P.RECONCILE_RES, {"position": list(self.positions), "order": []})
        elif kind == P.GET_POSITION_UNREALIZED_PNL_REQ:
            reply(P.GET_POSITION_UNREALIZED_PNL_RES, {"moneyDigits": 2, "positionUnrealizedPnL": [
                {"positionId": p["positionId"], "grossUnrealizedPnL": -250, "netUnrealizedPnL": -300} for p in self.positions]})
        elif kind == P.NEW_ORDER_REQ:
            self._new_order(mid, body)
        elif kind == P.CLOSE_POSITION_REQ:
            position = next(p for p in self.positions if p["positionId"] == body["positionId"])
            self.positions.remove(position)
            side = 2 if position["tradeData"]["tradeSide"] == 1 else 1
            order = self._order({"symbolId": 1, "tradeSide": side, "volume": body["volume"]}, executed=body["volume"], price=1.1)
            reply(P.EXECUTION_EVENT, {"executionType": 3, "order": order})

    def _order(self, trade, *, executed, price, client_order_id=None):
        self.next_order_id += 1
        order = {"orderId": self.next_order_id, "tradeData": trade, "orderType": 1, "executedVolume": executed}
        if price is not None:
            order["executionPrice"] = price
        if client_order_id:
            order["clientOrderId"] = client_order_id
        return order

    def _new_order(self, mid, body):
        trade = {"symbolId": body["symbolId"], "tradeSide": body["tradeSide"], "volume": body["volume"]}
        behavior = self.order_behavior
        if behavior == "silent":
            return
        if behavior == "error":
            self.push({"clientMsgId": mid, "payloadType": int(P.ORDER_ERROR_EVENT), "payload": {"errorCode": "NOT_ENOUGH_MONEY", "description": "test"}})
            return
        accepted = self._order(trade, executed=0, price=None, client_order_id=body.get("clientOrderId"))
        self.push({"clientMsgId": mid, "payloadType": int(P.EXECUTION_EVENT), "payload": {"executionType": 2, "order": accepted}})
        if behavior == "accept_only":
            return
        filled = dict(accepted, executedVolume=body["volume"], executionPrice=1.1001 if body["tradeSide"] == 1 else 1.1)
        self.positions.append({"positionId": 77, "tradeData": trade, "positionStatus": 1, "price": filled["executionPrice"], "usedMargin": 36_670, "moneyDigits": 2, "swap": 0})
        self.push({"payloadType": int(P.EXECUTION_EVENT), "payload": {"executionType": 3, "order": filled}})

    def spot(self, bid, ask):
        self.push({"payloadType": int(P.SPOT_EVENT), "payload": {"symbolId": 1, "bid": bid, "ask": ask}})

    def push(self, message):
        self.outbox.put(message)

    def foreign_fill(self):
        self.push({"payloadType": int(P.EXECUTION_EVENT), "payload": {"executionType": 3, "order": {
            "orderId": 5555, "tradeData": {"symbolId": 1, "tradeSide": 1, "volume": 100_000}, "executedVolume": 100_000, "executionPrice": 1.1}}})


class FakeTransport:
    def __init__(self, server):
        self.server = server
        self.inbox = queue.Queue()
        server.outbox = self.inbox
        self.connected = False
        self.broken = False

    def connect(self):
        self.connected = True

    def send(self, message):
        if self.broken:
            raise OSError("link down")
        self.server.handle(message)

    def recv(self, timeout=None):
        if self.broken:
            raise OSError("link down")
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self.connected = False

    def break_link(self):
        self.broken = True
