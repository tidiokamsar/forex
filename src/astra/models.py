"""Shared, dependency-free contracts used across the ASTRA simulation path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RunMode(StrEnum):
    SIMULATION = "SIMULATION"
    DEMO = "DEMO"
    LIVE = "LIVE"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


@dataclass(frozen=True, slots=True)
class MarketBar:
    instrument: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    instrument: str
    timestamp: datetime
    direction: Direction
    reference_price: float
    strategy: str
    strategy_version: str
    reason: str


@dataclass(frozen=True, slots=True)
class CostModel:
    """Explicit simulation assumptions; these are not broker-reported costs."""

    pip_size: float = 0.0001
    spread_pips: float = 1.2
    slippage_pips_per_side: float = 0.2
    commission_per_unit_per_side: float = 0.0


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: float = 10_000.0
    fast_window: int = 10
    slow_window: int = 30
    stop_lookback: int = 14
    stop_range_multiple: float = 2.0
    cost_model: CostModel = field(default_factory=CostModel)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    instrument: str
    direction: Direction
    entry_price: float
    stop_price: float
    created_at: datetime
    requested_units: float | None = None


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Indicative simulation-only defaults, not financial advice or a guarantee."""

    risk_per_position: float = 0.0025
    daily_loss_limit: float = 0.01
    max_open_positions: int = 1
    max_data_age_seconds: float = 120.0
    max_spread_pips: float = 3.0
    pip_size: float = 0.0001
    allowed_instruments: tuple[str, ...] = ("EURUSD",)


@dataclass(frozen=True, slots=True)
class RiskContext:
    mode: RunMode
    account_mode: RunMode | None
    account_verified: bool
    data_age_seconds: float | None
    spread_pips: float | None
    equity: float
    day_start_equity: float
    day_net_pnl: float
    open_positions: int
    instrument_specs_known: bool
    margin_available: bool
    kill_switch_active: bool = False


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    approved_units: float = 0.0
    risk_amount: float = 0.0


@dataclass(frozen=True, slots=True)
class Trade:
    instrument: str
    direction: Direction
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    units: float
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    strategy: str
    strategy_version: str
    instrument: str
    start: datetime | None
    end: datetime | None
    initial_equity: float
    final_equity: float
    trade_count: int
    gross_pnl: float
    total_costs: float
    net_pnl: float
    max_drawdown_pct: float
    win_rate: float | None
    profit_factor: float | None
    trades: tuple[Trade, ...]
    risk_rejections: tuple[str, ...]
    assumptions: tuple[str, ...]
    observations: int
    provenance: dict[str, Any] = field(default_factory=dict)
