from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.constants import TICK_SIZE
from core.enums import OrderSide, OrderStatus
from core.interfaces import OrderRequest, OrderResult, TradingAdapter

from .constants import (
    T0_DAILY_ROUND_TRIP_MAX,
    T0_GUI_OPS_FREEZE_THRESHOLD,
    T0_ORDER_MODIFY_MIN_DEVIATION_TICKS,
    T0_ORDER_MODIFY_MIN_INTERVAL_S,
    T0_PARTIAL_FILL_TOLERANCE_S,
)


@dataclass(frozen=True)
class ManagedOrder:
    order_id: int
    etf_code: str
    side: OrderSide
    price: float
    quantity: int
    submitted_at: datetime
    status: OrderStatus = OrderStatus.SUBMITTED


@dataclass
class OrderManager:
    _orders: dict[int, ManagedOrder]
    _gui_ops: int
    _daily_round_trip_count: int

    def __init__(self) -> None:
        self._orders = {}
        self._gui_ops = 0
        self._daily_round_trip_count = 0

    @property
    def gui_ops(self) -> int:
        return int(self._gui_ops)

    @property
    def daily_round_trip_count(self) -> int:
        return int(self._daily_round_trip_count)

    def register_order(self, *, order: ManagedOrder) -> None:
        self._orders[int(order.order_id)] = order

    def list_orders(self) -> list[ManagedOrder]:
        return list(self._orders.values())

    def reset_daily(self) -> None:
        self._orders = {}
        self._gui_ops = 0
        self._daily_round_trip_count = 0

    def mark_round_trip_closed(self) -> None:
        self._daily_round_trip_count += 1

    def assert_can_operate(self) -> None:
        if int(self._daily_round_trip_count) > int(T0_DAILY_ROUND_TRIP_MAX):
            raise AssertionError(f"日 RT 次数 {int(self._daily_round_trip_count)} 超过上限 1")
        if int(self._gui_ops) >= int(T0_GUI_OPS_FREEZE_THRESHOLD):
            raise AssertionError(f"T+0 GUI操作 {int(self._gui_ops)} 已达冻结阈值")

    def place_limit_order(self, *, trading: TradingAdapter, req: OrderRequest, now: datetime) -> OrderResult:
        self.assert_can_operate()
        r = trading.place_order(req)
        self._gui_ops += 1
        if r.order_id > 0:
            self.register_order(
                order=ManagedOrder(
                    order_id=int(r.order_id),
                    etf_code=str(req.etf_code),
                    side=req.side,
                    price=float(req.price),
                    quantity=int(req.quantity),
                    submitted_at=now,
                    status=r.status,
                )
            )
        return r

    def can_modify_order(self, *, now: datetime, current: ManagedOrder, new_price: float) -> bool:
        dt = (now - current.submitted_at).total_seconds()
        if dt < float(T0_ORDER_MODIFY_MIN_INTERVAL_S):
            return False
        ticks = abs(float(new_price) - float(current.price)) / float(TICK_SIZE)
        return ticks > float(T0_ORDER_MODIFY_MIN_DEVIATION_TICKS)

    def cancel_order(self, *, trading: TradingAdapter, order_id: int) -> bool:
        self.assert_can_operate()
        ok = bool(trading.cancel_order(int(order_id)))
        self._gui_ops += 1
        if ok:
            self._orders.pop(int(order_id), None)
        return ok

    def update_status(self, *, order_id: int, status: OrderStatus) -> None:
        oid = int(order_id)
        cur = self._orders.get(oid)
        if cur is None:
            return None
        self._orders[oid] = ManagedOrder(
            order_id=int(cur.order_id),
            etf_code=str(cur.etf_code),
            side=cur.side,
            price=float(cur.price),
            quantity=int(cur.quantity),
            submitted_at=cur.submitted_at,
            status=status,
        )

    def check_partial_fills(self, *, now: datetime, trading: TradingAdapter) -> list[int]:
        micro: list[int] = []
        for o in list(self._orders.values()):
            if o.status != OrderStatus.PARTIALLY_FILLED:
                continue
            elapsed = (now - o.submitted_at).total_seconds()
            if elapsed < float(T0_PARTIAL_FILL_TOLERANCE_S):
                continue
            _ = trading.cancel_order(int(o.order_id))
            self._gui_ops += 1
            micro.append(int(o.order_id))
        return micro
