"""MetaTrader 5 DEMO orders: market positions with a stop, on one pinned DEMO account only.

Built on ``Mt5DemoFeed`` (same terminal attachment, no password). Every order call first re-reads the account
and refuses unless it is still DEMO, on an allowed server, and the expected login (``expected_login``): an
account switched in the terminal in the meantime is never traded. The terminal's own "Algo Trading" button
must be on; ASTRA never turns it on.

Only ASTRA's positions are touched: they carry ``MAGIC`` and the comment ``ASTRA``. Positions opened by hand
in the terminal are listed but never modified or closed. There is no LIVE path in this module.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from ..marketdata import PAIRS
from .mt5_demo import ALLOWED_SERVERS, Mt5DemoError, Mt5DemoFeed

MAGIC = 20261028
COMMENT = "ASTRA"
DEVIATION_POINTS = 20
MAX_LOTS = 2.0  # hard cap per position, whatever the risk computation says


@dataclass(frozen=True, slots=True)
class Mt5Position:
    ticket: int
    symbol: str
    side: str  # LONG or SHORT
    volume: float
    price_open: float
    sl: float
    profit: float
    astra: bool
    price_current: float | None = None
    time_open: str | None = None
    swap: float = 0.0
    tp: float = 0.0
    magic: int = 0
    comment: str = ""
    identifier: int = 0
    reason: int = 0


class Mt5DemoTrader(Mt5DemoFeed):
    def __init__(self, *args: Any, expected_login: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.expected_login = int(expected_login)

    def connect(self) -> dict[str, Any]:
        account = super().connect()
        if account["login"] != self.expected_login:
            self.mt5.shutdown()
            raise Mt5DemoError(f"compte {account['login']} refusé : seul le compte DEMO {self.expected_login} est autorisé")
        return account

    def _guard(self) -> Any:
        mt5 = self.mt5
        info = mt5.account_info()
        if (info is None or info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO or info.server not in ALLOWED_SERVERS
                or info.login != self.expected_login):
            raise Mt5DemoError("compte du terminal changé ou non DEMO : aucun ordre envoyé")
        terminal = mt5.terminal_info()
        if not (terminal and terminal.trade_allowed):
            raise Mt5DemoError("« Trading Algo » est désactivé dans le terminal MT5 : aucun ordre envoyé")
        return info

    def positions(self) -> list[Mt5Position]:
        mt5 = self.mt5
        result = []
        for p in mt5.positions_get() or ():
            opened = getattr(p, "time", None)
            result.append(Mt5Position(p.ticket, p.symbol, "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT",
                                      p.volume, p.price_open, p.sl, p.profit, p.magic == MAGIC,
                                      getattr(p, "price_current", None),
                                      self._utc(opened) if opened else None, float(getattr(p, "swap", 0.0) or 0.0),
                                      float(getattr(p, "tp", 0.0) or 0.0), int(p.magic),
                                      str(getattr(p, "comment", "") or "")[:40],
                                      int(getattr(p, "identifier", p.ticket) or p.ticket), int(getattr(p, "reason", 0) or 0)))
        return result

    def _filling(self, symbol_info: Any) -> int:
        mt5 = self.mt5
        mode = symbol_info.filling_mode
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def lots_for_risk(self, symbol: str, side: str, stop_distance: float, risk_usd: float) -> tuple[float, float]:
        """Lots so that the stop loses at most ``risk_usd`` (rounded down to the volume step), and the stop price."""

        mt5 = self.mt5
        info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise Mt5DemoError(f"symbole {symbol} indisponible : {mt5.last_error()}")
        buy = side == "LONG"
        price = tick.ask if buy else tick.bid
        min_distance = (info.trade_stops_level + 1) * info.point
        distance = max(stop_distance, min_distance)
        sl = round(price - distance if buy else price + distance, info.digits)
        kind = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
        loss_per_lot = -(mt5.order_calc_profit(kind, symbol, 1.0, price, sl) or 0.0)
        if loss_per_lot <= 0:
            raise Mt5DemoError(f"perte au stop incalculable pour {symbol}")
        lots = math.floor(risk_usd / loss_per_lot / info.volume_step) * info.volume_step
        lots = round(min(lots, info.volume_max, MAX_LOTS), 2)
        return (lots if lots >= info.volume_min else 0.0), sl

    def prepare_entry(self, symbol: str, side: str, stop_distance: float, risk_usd: float) -> dict[str, Any]:
        """Everything the risk gateway needs about an entry that has not been sent: lots, stop, quote, loss at the
        stop, required margin and the broker's volume rules. A figure that cannot be computed is None."""

        mt5 = self.mt5
        info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise Mt5DemoError(f"symbole {symbol} indisponible : {mt5.last_error()}")
        buy = side == "LONG"
        price = tick.ask if buy else tick.bid
        distance = max(stop_distance, (info.trade_stops_level + 1) * info.point)
        sl = round(price - distance if buy else price + distance, info.digits)
        kind = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
        loss_per_lot = -(mt5.order_calc_profit(kind, symbol, 1.0, price, sl) or 0.0)
        lots = 0.0
        if loss_per_lot > 0:
            lots = math.floor(risk_usd / loss_per_lot / info.volume_step) * info.volume_step
            lots = round(min(lots, info.volume_max, MAX_LOTS), 2)
        margin = mt5.order_calc_margin(kind, symbol, lots, price) if lots > 0 else None
        stamp = self._utc(tick.time) if getattr(tick, "time", 0) else None
        return {"symbol": symbol, "side": side, "lots": lots, "sl": sl, "price": price, "bid": tick.bid, "ask": tick.ask,
                "tick_time": stamp, "loss_at_stop_usd": loss_per_lot * lots if loss_per_lot > 0 and lots > 0 else None,
                "required_margin": margin, "volume_min": info.volume_min, "volume_max": info.volume_max,
                "volume_step": info.volume_step, "has_stop": bool(sl)}

    def open(self, symbol: str, side: str, lots: float, sl: float) -> dict[str, Any]:
        self._guard()
        mt5 = self.mt5
        if symbol not in PAIRS or side not in ("LONG", "SHORT"):
            raise Mt5DemoError(f"ordre refusé : {symbol} {side}")
        if not 0 < lots <= MAX_LOTS or not sl:
            raise Mt5DemoError("ordre refusé : volume hors limites ou stop absent")
        info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
        buy = side == "LONG"
        request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(lots),
                   "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": tick.ask if buy else tick.bid,
                   "sl": float(sl), "deviation": DEVIATION_POINTS, "magic": MAGIC, "comment": COMMENT,
                   "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)}
        return self._send(request)

    def close(self, position: Mt5Position) -> dict[str, Any]:
        self._guard()
        mt5 = self.mt5
        if not position.astra:
            raise Mt5DemoError(f"position {position.ticket} ouverte hors ASTRA : jamais fermée par ASTRA")
        info, tick = mt5.symbol_info(position.symbol), mt5.symbol_info_tick(position.symbol)
        long = position.side == "LONG"
        request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol, "volume": position.volume,
                   "type": mt5.ORDER_TYPE_SELL if long else mt5.ORDER_TYPE_BUY, "position": position.ticket,
                   "price": tick.bid if long else tick.ask, "deviation": DEVIATION_POINTS, "magic": MAGIC,
                   "comment": COMMENT, "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)}
        return self._send(request)

    def _send(self, request: dict[str, Any]) -> dict[str, Any]:
        mt5 = self.mt5
        started = time.monotonic()
        result = mt5.order_send(request)
        latency_ms = round((time.monotonic() - started) * 1000)
        if result is None:
            raise Mt5DemoError(f"ordre non transmis : {mt5.last_error()}")
        done = result.retcode == mt5.TRADE_RETCODE_DONE
        report = {"ok": done, "retcode": result.retcode, "comment": result.comment, "order": result.order,
                  "deal": result.deal, "volume": result.volume, "price": result.price, "latency_ms": latency_ms,
                  "requested_price": request.get("price"),
                  "request": {k: request[k] for k in ("symbol", "volume", "type", "price") if k in request}
                  | {"sl": request.get("sl"), "position": request.get("position")}}
        if not done:
            raise Mt5DemoError(f"ordre refusé par MT5 (code {result.retcode}) : {result.comment}")
        return report
