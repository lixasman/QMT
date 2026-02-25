from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.cash_manager import CashManager
from core.enums import OrderSide, OrderStatus, OrderTimeInForce, OrderType
from core.interfaces import DataAdapter, OrderRequest, TradingAdapter
from core.price_utils import tick_ceil

from .constants import SCALE_CANCEL_AT
from .types import ScaleSignalEval


def execute_scale_if_needed(
    *,
    now: datetime,
    etf_code: str,
    cash_manager: CashManager,
    data: DataAdapter,
    trading: TradingAdapter,
    eval_result: ScaleSignalEval,
    log_path: str,
) -> Optional[int]:
    _ = str(log_path)
    code = str(etf_code)
    if str(eval_result.decision) != "SCALE_BUY":
        return None
    if not bool(eval_result.prerequisites.passed):
        raise AssertionError("加仓前提不满足但尝试执行加仓")
    if not bool(eval_result.conditions.passed):
        raise AssertionError("加仓信号条件不满足但尝试执行加仓")

    snap = data.get_snapshot(code)
    bid1 = float(snap.bid1_price)
    order_price = float(tick_ceil(float(bid1)))
    if eval_result.order is not None:
        if float(eval_result.order.price) != float(order_price):
            raise AssertionError(f"加仓挂单价应为 tick_ceil(Bid1)={order_price}，实际={eval_result.order.price}")
        if str(eval_result.order.tif.value) != str(OrderTimeInForce.DAY.value):
            raise AssertionError(f"加仓挂单必须当日有效，实际 TIF={eval_result.order.tif}")

    qty_raw = float(eval_result.target_amount) / float(order_price)
    qty = int(qty_raw)
    qty = (int(qty) // 100) * 100
    if qty <= 0:
        return None
    amount = float(order_price) * int(qty)
    if float(amount) > float(cash_manager.available_cash()):
        return None

    req = OrderRequest(
        etf_code=code,
        side=OrderSide.BUY,
        quantity=int(qty),
        order_type=OrderType.LIMIT,
        price=float(order_price),
        tif=OrderTimeInForce.DAY,
        strategy_name="position",
        remark="SCALE_BUY",
    )
    res = trading.place_order(req)
    oid = int(res.order_id)
    if oid <= 0:
        trading.enter_freeze_mode(res.error or "SCALE_PLACE_ORDER_FAILED")
        return None
    try:
        cash_manager.lock_cash(order_id=int(oid), etf_code=code, side="BUY", amount=float(amount), priority=2, strategy_name="position")
    except AssertionError:
        _ = trading.cancel_order(int(oid))
        trading.enter_freeze_mode("SCALE_LOCK_CASH_FAILED")
        return None
    final = trading.confirm_order(int(oid), timeout_s=10.0)
    if final.status == OrderStatus.SUBMITTED and now.time() >= SCALE_CANCEL_AT:
        _ = trading.cancel_order(int(oid))
        final = trading.confirm_order(int(oid), timeout_s=2.0)
    if final.status in (OrderStatus.CANCELED, OrderStatus.REJECTED):
        _ = cash_manager.release_cash(int(oid))
        return None
    return int(oid)
