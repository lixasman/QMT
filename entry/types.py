from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from core.interfaces import OrderRequest


@dataclass(frozen=True)
class WatchlistItem:
    etf_code: str
    sentiment_score: int
    profit_ratio: float
    nearest_resistance: Optional[float] = None
    micro_caution: bool = False
    vpin_rank: Optional[float] = None
    ofi_daily: Optional[float] = None
    vs_max: Optional[float] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalFired:
    etf_code: str
    score: float
    is_strong: bool
    h_signal: float
    l_signal: float
    close_signal_day: float
    atr_20: float
    signal_date: date
    expire_date: date
    signals: dict[str, float]
    watchlist: WatchlistItem


class ConfirmActionType(str, Enum):
    NOOP = "NOOP"
    CONFIRM_ENTRY = "CONFIRM_ENTRY"
    REJECT = "REJECT"
    INVALIDATE = "INVALIDATE"


@dataclass(frozen=True)
class ConfirmAction:
    action: ConfirmActionType
    reason: str = ""
    conditions: dict[str, Any] = field(default_factory=dict)
    order: Optional[OrderRequest] = None
    used_vwap_slope: bool = False
