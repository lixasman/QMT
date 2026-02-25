from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from core.enums import OrderSide
from core.interfaces import TradingAdapter

from .constants import T0_SWEEPER_CANCEL_ALL_AT, T0_SWEEPER_CANCEL_BUY_AT
from .order_manager import OrderManager
from .types import T0Action


@dataclass(frozen=True)
class SweepPlan:
    action: T0Action
    reason: str


def plan_sweep(*, now: datetime) -> SweepPlan:
    t = now.time()
    if t >= T0_SWEEPER_CANCEL_ALL_AT:
        return SweepPlan(action="CANCEL_ALL_ORDERS", reason="SWEEPER_1455")
    if t >= T0_SWEEPER_CANCEL_BUY_AT:
        return SweepPlan(action="CANCEL_BUY_ORDERS", reason="SWEEPER_1415")
    return SweepPlan(action="HOLD", reason="")


def execute_sweep(*, now: datetime, trading: TradingAdapter, om: OrderManager) -> int:
    plan = plan_sweep(now=now)
    if plan.action == "HOLD":
        return 0
    canceled = 0
    orders = list(om.list_orders())
    if plan.action == "CANCEL_BUY_ORDERS":
        for o in orders:
            if o.side == OrderSide.BUY:
                if om.cancel_order(trading=trading, order_id=int(o.order_id)):
                    canceled += 1
        return int(canceled)
    for o in orders:
        if om.cancel_order(trading=trading, order_id=int(o.order_id)):
            canceled += 1
    return int(canceled)

