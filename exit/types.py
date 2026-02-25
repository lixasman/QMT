from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Optional

from core.enums import DataQuality
from core.interfaces import InstrumentInfo, OrderRequest, TickSnapshot

ExitAction = Literal["HOLD", "REDUCE_50", "FULL_EXIT", "LIFEBOAT_70_30"]


@dataclass(frozen=True)
class ExitSignals:
    s_chip: float
    s_sentiment: float
    s_diverge: float
    s_time: float


@dataclass(frozen=True)
class SoftScoreResult:
    score_soft: float
    triggered: bool
    used_signals: dict[str, float]


@dataclass(frozen=True)
class ChandelierState:
    hh: float
    atr: float
    k: float
    stop: float


@dataclass(frozen=True)
class PositionBalances:
    total_qty: int
    sellable_qty: int
    locked_qty: int


@dataclass(frozen=True)
class LifeboatSellPlan:
    sell_qty: int
    retain_qty: int
    sell_price: float
    tight_stop: float
    sell_time: datetime


@dataclass(frozen=True)
class LifeboatBuybackPlan:
    buy_qty: int
    buy_price: float
    trading_minutes_elapsed: int
    now: datetime


@dataclass(frozen=True)
class DecisionLogContext:
    score_soft: float
    data_health: Mapping[str, DataQuality]
    lifeboat_used: bool


@dataclass(frozen=True)
class LayerDecision:
    action: ExitAction
    order: Optional[OrderRequest]
    reason: str
    extra: dict[str, Any]


@dataclass(frozen=True)
class ExitContext:
    instrument: InstrumentInfo
    snapshot: TickSnapshot
    chandelier: ChandelierState
    signals: ExitSignals
    data_health: Mapping[str, DataQuality]

