from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from core.enums import OrderStatus
from core.interfaces import TradingAdapter

from .constants import T0_TIMEOUT_CONFIRM_S
from .types import ReconciliationResult


def _as_status(x: Any) -> Optional[OrderStatus]:
    if x is None:
        return None
    if isinstance(x, OrderStatus):
        return x
    try:
        return OrderStatus(str(x))
    except Exception:
        return None


def _broker_status_for_order(*, broker_orders: list[Any], order_id: int) -> Optional[OrderStatus]:
    oid = int(order_id)
    for o in broker_orders:
        if isinstance(o, dict):
            if int(o.get("order_id", -1)) == oid:
                return _as_status(o.get("status"))
        else:
            if int(getattr(o, "order_id", -1)) == oid:
                return _as_status(getattr(o, "status", None))
    return None


@dataclass(frozen=True)
class ReconcileInput:
    now: datetime
    order_id: int
    memory_status: OrderStatus
    trigger: str = "TIMEOUT_10S"


def confirm_or_reconcile(*, trading: TradingAdapter, inp: ReconcileInput) -> ReconciliationResult:
    oid = int(inp.order_id)
    mem = inp.memory_status if isinstance(inp.memory_status, OrderStatus) else OrderStatus(str(inp.memory_status))

    r = trading.confirm_order(oid, timeout_s=float(T0_TIMEOUT_CONFIRM_S))
    if r.status == OrderStatus.FILLED:
        return ReconciliationResult(
            timestamp=inp.now,
            trigger=str(inp.trigger),
            order_id=oid,
            case="A",
            memory_status=mem,
            broker_status=OrderStatus.FILLED,
            action="CORRECT_TO_FILLED",
            position_sync=(),
        )
    if r.status == OrderStatus.CANCELED:
        return ReconciliationResult(
            timestamp=inp.now,
            trigger=str(inp.trigger),
            order_id=oid,
            case="B",
            memory_status=mem,
            broker_status=OrderStatus.CANCELED,
            action="ALREADY_CANCELED",
            position_sync=(),
        )
    if r.status == OrderStatus.REJECTED:
        return ReconciliationResult(
            timestamp=inp.now,
            trigger=str(inp.trigger),
            order_id=oid,
            case="C",
            memory_status=mem,
            broker_status=OrderStatus.REJECTED,
            action="CORRECT_TO_REJECTED",
            position_sync=(),
        )

    trading.enter_freeze_mode(reason="T0_TIMEOUT_10S")
    broker_orders = list(trading.query_orders())
    _ = trading.query_positions()
    broker_status = _broker_status_for_order(broker_orders=broker_orders, order_id=oid)

    if broker_status == OrderStatus.FILLED and mem == OrderStatus.SUBMITTED:
        trading.exit_freeze_mode()
        return ReconciliationResult(
            timestamp=inp.now,
            trigger=str(inp.trigger),
            order_id=oid,
            case="A",
            memory_status=mem,
            broker_status=OrderStatus.FILLED,
            action="CORRECT_TO_FILLED",
            position_sync=(("locked_qty_delta", 0), ("sellable_qty_delta", 0)),
        )

    if broker_status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.SUBMITTED):
        _ = trading.cancel_order(oid)
        trading.exit_freeze_mode()
        bs = broker_status if isinstance(broker_status, OrderStatus) else OrderStatus(str(broker_status))
        return ReconciliationResult(
            timestamp=inp.now,
            trigger=str(inp.trigger),
            order_id=oid,
            case="B",
            memory_status=mem,
            broker_status=bs,
            action="CANCEL_AND_TREAT_PARTIAL_AS_MICRO",
            position_sync=(),
        )

    if broker_status is None:
        trading.exit_freeze_mode()
        return ReconciliationResult(
            timestamp=inp.now,
            trigger=str(inp.trigger),
            order_id=oid,
            case="C",
            memory_status=mem,
            broker_status=OrderStatus.REJECTED,
            action="CORRECT_TO_REJECTED",
            position_sync=(),
        )

    trading.exit_freeze_mode()
    bs = broker_status if isinstance(broker_status, OrderStatus) else OrderStatus(str(broker_status))
    return ReconciliationResult(
        timestamp=inp.now,
        trigger=str(inp.trigger),
        order_id=oid,
        case="C",
        memory_status=mem,
        broker_status=bs,
        action="UNKNOWN_BROKER_STATE",
        position_sync=(),
    )
