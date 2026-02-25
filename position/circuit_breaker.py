from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.models import PortfolioState

from .types import CircuitBreakerDecision
from .constants import CIRCUIT_COOLDOWN_DAYS, CIRCUIT_INTRADAY_HARD_DD, CIRCUIT_INTRADAY_SOFT_DD


def can_unlock_cooldown(*, cooldown_days: int, market_above_ma20: bool, manual_ack: bool) -> bool:
    if int(cooldown_days) < int(CIRCUIT_COOLDOWN_DAYS):
        return False
    if not bool(market_above_ma20):
        return False
    if not bool(manual_ack):
        return False
    return True


def evaluate_intraday_breaker(
    *,
    now: datetime,
    state: PortfolioState,
    nav_estimate: float,
) -> Optional[CircuitBreakerDecision]:
    hwm = float(state.hwm)
    nav = float(nav_estimate)
    if hwm <= 0:
        hwm = float(nav)
    if float(nav) <= float(hwm) * float(CIRCUIT_INTRADAY_HARD_DD):
        return CircuitBreakerDecision(
            timestamp=now,
            trigger_type="INTRADAY_HARD",
            hwm=float(hwm),
            nav=float(nav),
            action="CLEAR_ALL",
            frozen_operations=["trial_entry", "scale_buy", "t0_buy"],
            allowed_operations=["stop_loss", "layer2_reduce", "t0_sell"],
        )
    if float(nav) <= float(hwm) * float(CIRCUIT_INTRADAY_SOFT_DD):
        return CircuitBreakerDecision(
            timestamp=now,
            trigger_type="INTRADAY_SOFT",
            hwm=float(hwm),
            nav=float(nav),
            action="FREEZE_NEW_OPEN",
            frozen_operations=["trial_entry", "scale_buy", "t0_buy"],
            allowed_operations=["stop_loss", "layer2_reduce", "t0_sell"],
        )
    return None


def evaluate_post_close_breaker(*, now: datetime, state: PortfolioState, current_nav: float) -> Optional[CircuitBreakerDecision]:
    nav = float(current_nav)
    hwm = float(state.hwm)
    if hwm <= 0:
        hwm = float(nav)
    if float(nav) <= float(hwm) * float(CIRCUIT_INTRADAY_HARD_DD):
        return CircuitBreakerDecision(
            timestamp=now,
            trigger_type="POST_CLOSE",
            hwm=float(hwm),
            nav=float(nav),
            action="CLEAR_ALL_AND_COOLDOWN",
            frozen_operations=["trial_entry", "scale_buy", "t0_buy"],
            allowed_operations=["stop_loss", "layer2_reduce", "t0_sell"],
        )
    return None


def update_hwm_post_close(*, prev_hwm: float, current_nav: float) -> float:
    prev = float(prev_hwm)
    nav = float(current_nav)
    hwm = float(max(prev, nav))
    if hwm < prev:
        raise AssertionError(f"HWM 被下调: {hwm} < {prev}，违反单调递增")
    return float(hwm)
