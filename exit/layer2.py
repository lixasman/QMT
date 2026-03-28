from __future__ import annotations

from dataclasses import dataclass

from core.enums import OrderSide, OrderType
from core.interfaces import InstrumentInfo, OrderRequest, TickSnapshot
from core.price_utils import align_order_price

from .exit_config import get_exit_layer2_threshold
from .types import LayerDecision


@dataclass(frozen=True)
class Layer2Plan:
    sell_qty: int
    sell_price: float


def _round_down_lot(qty: int, lot: int = 100) -> int:
    q = int(qty)
    l = int(lot)
    if l <= 0:
        raise AssertionError(f"invalid lot: {lot}")
    if q <= 0:
        return 0
    return (q // l) * l


def plan_layer2_reduce_50(
    *,
    instrument: InstrumentInfo,
    snapshot: TickSnapshot,
    sellable_qty: int,
) -> Layer2Plan:
    sq = int(sellable_qty)
    if sq < 0:
        raise AssertionError(f"sellable_qty negative: {sq}")
    half = int(sq // 2)
    sell_qty = _round_down_lot(half, 100)
    if sell_qty <= 0 and sq >= 100:
        sell_qty = 100
    if sell_qty > sq:
        sell_qty = _round_down_lot(sq, 100)

    sell_price = align_order_price(
        price=float(snapshot.bid1_price),
        side=OrderSide.SELL.value,
        lower_limit=float(instrument.limit_down),
        upper_limit=float(instrument.limit_up),
        tick_size=float(instrument.price_tick),
    )
    return Layer2Plan(sell_qty=int(sell_qty), sell_price=float(sell_price))


def decide_layer2(
    *,
    etf_code: str,
    instrument: InstrumentInfo,
    snapshot: TickSnapshot,
    score_soft: float,
    sellable_qty: int,
    threshold: float | None = None,
) -> LayerDecision:
    score = float(score_soft)
    if not (0.0 <= score <= 2.3):
        raise AssertionError(f"Score_soft out of range: {score}")
    gate = float(get_exit_layer2_threshold()) if threshold is None else float(threshold)
    if float(score) < float(gate):
        return LayerDecision(action="HOLD", order=None, reason="SCORE_BELOW_THRESHOLD", extra={})

    plan = plan_layer2_reduce_50(instrument=instrument, snapshot=snapshot, sellable_qty=int(sellable_qty))
    if int(plan.sell_qty) > int(sellable_qty):
        raise AssertionError(f"sell_qty > sellable_qty: {plan.sell_qty} > {sellable_qty}")
    if int(plan.sell_qty) <= 0:
        return LayerDecision(action="HOLD", order=None, reason="NO_SELLABLE_QTY", extra={"score_soft": float(score)})

    order = OrderRequest(
        etf_code=str(etf_code),
        side=OrderSide.SELL,
        quantity=int(plan.sell_qty),
        order_type=OrderType.LIMIT,
        price=float(plan.sell_price),
        strategy_name="exit",
        remark="LAYER2",
    )
    return LayerDecision(
        action="REDUCE_50",
        order=order,
        reason="SCORE_TRIGGER",
        extra={"score_soft": float(score), "sell_qty": int(plan.sell_qty), "sell_price": float(plan.sell_price)},
    )
