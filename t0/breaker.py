from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .constants import (
    BREAKER_CONSECUTIVE_LOSS_MAX,
    BREAKER_DAILY_LOSS_PCT,
    BREAKER_MONTHLY_LOSS_PCT,
    BREAKER_WEEKLY_LOSS_PCT,
    EXTREME_DOWN_PCT,
    EXTREME_UP_PCT,
)
from .types import BreakerDecision


def should_freeze_daily(*, t0_daily_pnl: float, nav: float) -> bool:
    n = float(nav)
    if n <= 0:
        return False
    pct = float(t0_daily_pnl) / n
    return pct <= -float(BREAKER_DAILY_LOSS_PCT)


def should_freeze_rolling(*, pnl_history: list[float], nav: float, threshold_pct: float) -> bool:
    n = float(nav)
    if n <= 0:
        return False
    total = float(sum(float(x) for x in pnl_history))
    pct = total / n
    return pct <= -float(threshold_pct)


def update_consecutive_loss_count(*, prev_count: int, net_pnl: float) -> int:
    c = int(prev_count)
    if float(net_pnl) > 0:
        return 0
    if float(net_pnl) < 0:
        return int(c + 1)
    return int(c)


def should_freeze_consecutive(*, consecutive_loss_count: int) -> bool:
    return int(consecutive_loss_count) >= int(BREAKER_CONSECUTIVE_LOSS_MAX)


def forbid_reverse_sell_by_extreme(*, daily_change: float) -> bool:
    return float(daily_change) > float(EXTREME_UP_PCT)


def forbid_forward_buy_by_extreme(*, daily_change: float) -> bool:
    return float(daily_change) < float(EXTREME_DOWN_PCT)


@dataclass(frozen=True)
class BreakerInputs:
    now: datetime
    etf_code: str
    nav: float
    t0_daily_pnl: float
    pnl_5d: list[float]
    pnl_30d: list[float]
    consecutive_loss_count: int


def evaluate_breakers(*, inp: BreakerInputs) -> Optional[BreakerDecision]:
    if should_freeze_daily(t0_daily_pnl=float(inp.t0_daily_pnl), nav=float(inp.nav)):
        return BreakerDecision(
            timestamp=inp.now,
            etf_code=str(inp.etf_code),
            breaker_layer="LAYER_5_DAILY",
            trigger_value=float(inp.t0_daily_pnl) / float(inp.nav) if float(inp.nav) > 0 else 0.0,
            threshold=float(BREAKER_DAILY_LOSS_PCT),
            action="FREEZE_TODAY",
            note="日内亏损触发",
        )

    if should_freeze_rolling(pnl_history=list(inp.pnl_5d), nav=float(inp.nav), threshold_pct=float(BREAKER_WEEKLY_LOSS_PCT)):
        return BreakerDecision(
            timestamp=inp.now,
            etf_code=str(inp.etf_code),
            breaker_layer="LAYER_7_WEEKLY",
            trigger_value=float(sum(inp.pnl_5d)) / float(inp.nav) if float(inp.nav) > 0 else 0.0,
            threshold=float(BREAKER_WEEKLY_LOSS_PCT),
            action="FREEZE_UNTIL_WINDOW_OUT",
            note="5日滚动亏损触发",
        )

    if should_freeze_rolling(pnl_history=list(inp.pnl_30d), nav=float(inp.nav), threshold_pct=float(BREAKER_MONTHLY_LOSS_PCT)):
        return BreakerDecision(
            timestamp=inp.now,
            etf_code=str(inp.etf_code),
            breaker_layer="LAYER_8_MONTHLY",
            trigger_value=float(sum(inp.pnl_30d)) / float(inp.nav) if float(inp.nav) > 0 else 0.0,
            threshold=float(BREAKER_MONTHLY_LOSS_PCT),
            action="FREEZE_30D",
            note="30日滚动亏损触发",
        )

    if should_freeze_consecutive(consecutive_loss_count=int(inp.consecutive_loss_count)):
        return BreakerDecision(
            timestamp=inp.now,
            etf_code=str(inp.etf_code),
            breaker_layer="LAYER_9_CONSECUTIVE",
            trigger_value=float(inp.consecutive_loss_count),
            threshold=float(BREAKER_CONSECUTIVE_LOSS_MAX),
            action="FREEZE_UNTIL_NEXT_DAY",
            note="连续亏损触发",
        )

    return None

