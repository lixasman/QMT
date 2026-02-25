from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

from core.enums import DataQuality, OrderStatus

TrendState = Literal["UP", "DOWN", "RANGE"]
T0Action = Literal["HOLD", "PLACE_LIMIT_BUY", "PLACE_LIMIT_SELL", "CANCEL_BUY_ORDERS", "CANCEL_ALL_ORDERS"]
SignalType = Literal["VWAP_BUY", "VWAP_SELL"]
Confidence = Literal["HIGH", "NORMAL"]
BreakerAction = Literal["ALLOW", "FREEZE_TODAY", "FREEZE_UNTIL_WINDOW_OUT", "FREEZE_30D", "FREEZE_UNTIL_NEXT_DAY"]
ReconcileCase = Literal["A", "B", "C"]


@dataclass(frozen=True)
class RegimeResult:
    regime_active: bool
    reason: str
    auction_vol_ratio: float
    atr5_percentile: float
    computed_at: datetime


@dataclass(frozen=True)
class DenseZone:
    upper: float
    lower: float
    strength: float


@dataclass(frozen=True)
class VwapSnapshot:
    timestamp: datetime
    price: float
    vwap: float
    sigma: float
    delta_volume: int
    delta_amount: float
    data_quality: DataQuality


@dataclass(frozen=True)
class VwapBands:
    vwap: float
    sigma: float
    k_buy: float
    k_sell: float
    buy_trigger: float
    sell_trigger: float
    trend_state: TrendState


@dataclass(frozen=True)
class T0Signal:
    etf_code: str
    timestamp: datetime
    signal_type: SignalType
    vwap: float
    sigma: float
    k_value: float
    trend_state: TrendState
    target_price: float
    amount: float
    confidence: Confidence
    kde_support: bool
    kde_zone_price: Optional[float]
    action: T0Action
    quantity: Optional[int] = None


@dataclass(frozen=True)
class RoundTripResult:
    timestamp: datetime
    etf_code: str
    direction: str
    buy_price: float
    sell_price: float
    quantity: int
    commission: float
    net_pnl_cny: float
    net_pnl_bps: float
    actual_be_bps: float
    daily_round_trip_count: int
    consecutive_loss_count: int
    t0_daily_pnl: float


@dataclass(frozen=True)
class BreakerDecision:
    timestamp: datetime
    etf_code: str
    breaker_layer: str
    trigger_value: float
    threshold: float
    action: BreakerAction
    note: str = ""


@dataclass(frozen=True)
class ReconciliationResult:
    timestamp: datetime
    trigger: str
    order_id: int
    case: ReconcileCase
    memory_status: OrderStatus
    broker_status: OrderStatus
    action: str
    position_sync: tuple[tuple[str, Any], ...]
