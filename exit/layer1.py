from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Mapping, Optional

from core.enums import DataQuality, OrderSide, OrderType
from core.interfaces import InstrumentInfo, OrderRequest, TickSnapshot
from core.price_utils import align_order_price

from .constants import (
    DEADWATER_MAX_ABS_RETURN,
    DEADWATER_MIN_DAYS_HELD,
    GAP_CHECK_TIMES,
    GAP_STOP_MULTIPLIER,
    LAYER1_SELL_DISCOUNT,
    T0_DAILY_LOSS_CIRCUIT_BREAKER_PCT,
)
from .lifeboat import plan_lifeboat_sell
from .types import LayerDecision


@dataclass(frozen=True)
class Layer1Trigger:
    triggered: bool
    reason: str


def _layer1_sell_price(*, instrument: InstrumentInfo, bid1: float) -> float:
    return align_order_price(
        price=float(bid1) * float(LAYER1_SELL_DISCOUNT),
        side=OrderSide.SELL.value,
        lower_limit=float(instrument.limit_down),
        upper_limit=float(instrument.limit_up),
        tick_size=float(instrument.price_tick),
    )


def decide_full_exit(
    *,
    etf_code: str,
    instrument: InstrumentInfo,
    snapshot: TickSnapshot,
    reason: str,
    sellable_qty: int,
    total_qty: int,
    locked_qty: int,
    extra: Optional[dict[str, Any]] = None,
) -> LayerDecision:
    sq = int(sellable_qty)
    tq = int(total_qty)
    lq = int(locked_qty)
    if sq < 0 or tq < 0 or lq < 0:
        raise AssertionError(f"invalid qty: total={tq} sellable={sq} locked={lq}")
    if sq > tq and tq > 0:
        raise AssertionError(f"sellable_qty > total_qty: {sq} > {tq}")
    sell_price = _layer1_sell_price(instrument=instrument, bid1=float(snapshot.bid1_price))
    order = None
    if int(sq) > 0:
        order = OrderRequest(
            etf_code=str(etf_code),
            side=OrderSide.SELL,
            quantity=int(sq),
            order_type=OrderType.LIMIT,
            price=float(sell_price),
            strategy_name="exit",
            remark="LAYER1",
        )
    ex: dict[str, Any] = {} if extra is None else dict(extra)
    ex.update({"sell_qty": int(sq), "sell_price": float(sell_price), "locked_qty": int(lq)})
    return LayerDecision(action="FULL_EXIT", order=order, reason=str(reason), extra=ex)


def check_gap_protection(*, now_time: time, last_price: float, stop_price: float) -> Layer1Trigger:
    if now_time not in GAP_CHECK_TIMES:
        return Layer1Trigger(triggered=False, reason="NOT_GAP_CHECK_TIME")
    if float(last_price) < float(stop_price) * float(GAP_STOP_MULTIPLIER):
        return Layer1Trigger(triggered=True, reason="GAP_PROTECTION")
    return Layer1Trigger(triggered=False, reason="GAP_OK")


def check_deadwater(*, days_held: int, current_return: float) -> Layer1Trigger:
    if int(days_held) >= int(DEADWATER_MIN_DAYS_HELD) and abs(float(current_return)) <= float(DEADWATER_MAX_ABS_RETURN):
        return Layer1Trigger(triggered=True, reason="DEADWATER")
    return Layer1Trigger(triggered=False, reason="NOT_DEADWATER")


def should_freeze_t0(*, t0_realized_loss_pct: float) -> bool:
    loss = float(t0_realized_loss_pct)
    if float(loss) < 0.0:
        raise AssertionError(f"t0_realized_loss_pct must be non-negative (loss as positive): {loss}")
    return bool(float(loss) >= float(T0_DAILY_LOSS_CIRCUIT_BREAKER_PCT))


def check_stop_break(*, last_price: float, stop_price: float) -> Layer1Trigger:
    if float(last_price) < float(stop_price):
        return Layer1Trigger(triggered=True, reason="STOP_BREAK")
    return Layer1Trigger(triggered=False, reason="STOP_OK")


def decide_layer1_on_trigger(
    *,
    etf_code: str,
    instrument: InstrumentInfo,
    snapshot: TickSnapshot,
    stop_price: float,
    score_soft: float,
    data_health: Mapping[str, DataQuality],
    lifeboat_used: bool,
    total_qty: int,
    sellable_qty: int,
    now: datetime,
) -> LayerDecision:
    if not (float(snapshot.last_price) < float(stop_price)):
        raise AssertionError("Layer 1 触发但价格未破 Stop，逻辑错误")

    sq = int(sellable_qty)
    tq = int(total_qty)
    if sq < 0 or tq < 0:
        raise AssertionError(f"invalid qty: total={tq} sellable={sq}")
    if sq > tq and tq > 0:
        raise AssertionError(f"sellable_qty > total_qty: {sq} > {tq}")
    locked_qty = int(tq) - int(sq)
    if locked_qty < 0:
        locked_qty = 0

    has_unavailable = any(v == DataQuality.UNAVAILABLE for v in data_health.values())
    if has_unavailable:
        return decide_full_exit(
            etf_code=etf_code,
            instrument=instrument,
            snapshot=snapshot,
            reason="SIGNAL_UNAVAILABLE",
            sellable_qty=int(sq),
            total_qty=int(tq),
            locked_qty=int(locked_qty),
            extra={},
        )

    if float(score_soft) > 0.0:
        return decide_full_exit(
            etf_code=etf_code,
            instrument=instrument,
            snapshot=snapshot,
            reason="SOFT_SCORE_POSITIVE",
            sellable_qty=int(sq),
            total_qty=int(tq),
            locked_qty=int(locked_qty),
            extra={"score_soft": float(score_soft)},
        )

    if float(score_soft) != 0.0:
        raise AssertionError(f"Score_soft must be discrete, got: {score_soft}")

    if not bool(lifeboat_used):
        plan = plan_lifeboat_sell(instrument=instrument, snapshot=snapshot, sellable_qty=int(sq), now=now)
        if int(plan.sell_qty) > int(sq):
            raise AssertionError(f"卖出 {plan.sell_qty} 超过可用余额 {sq}")
        order = None
        if int(plan.sell_qty) > 0:
            order = OrderRequest(
                etf_code=str(etf_code),
                side=OrderSide.SELL,
                quantity=int(plan.sell_qty),
                order_type=OrderType.LIMIT,
                price=float(plan.sell_price),
                strategy_name="exit",
                remark="LIFEBOAT_SELL",
            )
        return LayerDecision(
            action="LIFEBOAT_70_30",
            order=order,
            reason="SCORE_ZERO_FIRST_TIME",
            extra={
                "sell_qty": int(plan.sell_qty),
                "sell_price": float(plan.sell_price),
                "retain_qty": int(plan.retain_qty),
                "tight_stop": float(plan.tight_stop),
                "sell_time": plan.sell_time.isoformat(timespec="seconds"),
            },
        )

    return decide_full_exit(
        etf_code=etf_code,
        instrument=instrument,
        snapshot=snapshot,
        reason="SCORE_ZERO_BUT_LIFEBOAT_USED",
        sellable_qty=int(sq),
        total_qty=int(tq),
        locked_qty=int(locked_qty),
        extra={},
    )
