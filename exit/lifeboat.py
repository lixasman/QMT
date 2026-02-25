from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from core.enums import DataQuality, OrderSide
from core.interfaces import InstrumentInfo, TickSnapshot
from core.price_utils import align_order_price
from core.time_utils import trading_minutes_between

from .constants import (
    LIFEBOAT_BUYBACK_CUTOFF_TIME,
    LIFEBOAT_BUYBACK_UPLIFT,
    LIFEBOAT_COOLDOWN_TRADING_MINUTES,
    LIFEBOAT_DEAD_CAT_LIMITDOWN_MULTIPLIER,
    LIFEBOAT_SELL_PCT,
    LIFEBOAT_TIGHT_STOP_PCT,
    LAYER1_SELL_DISCOUNT,
)
from .types import LifeboatBuybackPlan, LifeboatSellPlan


@dataclass(frozen=True)
class BuybackEvaluation:
    passed: bool
    trading_minutes_elapsed: int
    conditions: dict[str, Any]


def _layer1_sell_price(*, instrument: InstrumentInfo, bid1: float) -> float:
    return align_order_price(
        price=float(bid1) * float(LAYER1_SELL_DISCOUNT),
        side=OrderSide.SELL.value,
        lower_limit=float(instrument.limit_down),
        upper_limit=float(instrument.limit_up),
        tick_size=float(instrument.price_tick),
    )


def _buyback_price(*, instrument: InstrumentInfo, ask1: float) -> float:
    return align_order_price(
        price=float(ask1) * float(LIFEBOAT_BUYBACK_UPLIFT),
        side=OrderSide.BUY.value,
        lower_limit=float(instrument.limit_down),
        upper_limit=float(instrument.limit_up),
        tick_size=float(instrument.price_tick),
    )


def plan_lifeboat_sell(
    *,
    instrument: InstrumentInfo,
    snapshot: TickSnapshot,
    sellable_qty: int,
    now: datetime,
) -> LifeboatSellPlan:
    sq = int(sellable_qty)
    if sq < 0:
        raise AssertionError(f"sellable_qty negative: {sq}")
    raw_sell = int(float(sq) * float(LIFEBOAT_SELL_PCT))
    if raw_sell > sq:
        raw_sell = sq
    if raw_sell < 0:
        raw_sell = 0
    sell_qty = int(raw_sell)
    retain_qty = int(sq) - int(sell_qty)
    if retain_qty < 0:
        retain_qty = 0
    sell_price = _layer1_sell_price(instrument=instrument, bid1=float(snapshot.bid1_price))
    tight_stop = float(snapshot.last_price) * (1.0 - float(LIFEBOAT_TIGHT_STOP_PCT))
    return LifeboatSellPlan(
        sell_qty=int(sell_qty),
        retain_qty=int(retain_qty),
        sell_price=float(sell_price),
        tight_stop=float(tight_stop),
        sell_time=now,
    )


def evaluate_buyback(
    *,
    instrument: InstrumentInfo,
    snapshot: TickSnapshot,
    stop_price: float,
    score_soft: float,
    data_health: Mapping[str, DataQuality],
    lifeboat_used: bool,
    lifeboat_sell_time: datetime,
    current_total_qty: int,
    now: datetime,
) -> BuybackEvaluation:
    if bool(lifeboat_used):
        return BuybackEvaluation(
            passed=False,
            trading_minutes_elapsed=0,
            conditions={"rejected": {"pass": True, "reason": "LIFEBOAT_USED"}},
        )

    elapsed = int(trading_minutes_between(lifeboat_sell_time, now))
    a = bool(elapsed >= int(LIFEBOAT_COOLDOWN_TRADING_MINUTES))
    b = bool(float(snapshot.last_price) > float(stop_price))
    c = bool(float(score_soft) == 0.0)
    d = bool(snapshot.data_quality != DataQuality.STALE)
    e = bool(float(snapshot.last_price) > float(instrument.limit_down) * float(LIFEBOAT_DEAD_CAT_LIMITDOWN_MULTIPLIER))
    f = bool(now.time() <= LIFEBOAT_BUYBACK_CUTOFF_TIME)
    passed = bool(a and b and c and d and e and f)
    conditions: dict[str, Any] = {
        "a_cooldown": {"pass": a, "minutes": int(elapsed), "required": int(LIFEBOAT_COOLDOWN_TRADING_MINUTES)},
        "b_price_above_stop": {"pass": b, "price": float(snapshot.last_price), "stop": float(stop_price)},
        "c_score_zero": {"pass": c, "score": float(score_soft)},
        "d_data_fresh": {"pass": d, "quality": snapshot.data_quality.value},
        "e_not_dead_cat": {
            "pass": e,
            "price": float(snapshot.last_price),
            "limit_down_102": float(instrument.limit_down) * float(LIFEBOAT_DEAD_CAT_LIMITDOWN_MULTIPLIER),
        },
        "f_before_cutoff": {"pass": f, "current_time": now.strftime("%H:%M")},
    }
    if any(v == DataQuality.UNAVAILABLE for v in data_health.values()):
        conditions["data_health"] = {str(k): v.value for k, v in data_health.items()}
    return BuybackEvaluation(passed=passed, trading_minutes_elapsed=int(elapsed), conditions=conditions)


def plan_lifeboat_buyback(
    *,
    instrument: InstrumentInfo,
    snapshot: TickSnapshot,
    current_total_qty: int,
    trading_minutes_elapsed: int,
    now: datetime,
) -> LifeboatBuybackPlan:
    total = int(current_total_qty)
    if total <= 0:
        raise AssertionError(f"invalid current_total_qty: {total}")
    est_orig = int(round(float(total) * (10.0 / 3.0)))
    buy_qty_raw = int(est_orig) - int(total)
    buy_qty = (int(buy_qty_raw) // 100) * 100
    if buy_qty <= 0:
        raise AssertionError(f"buyback qty non-positive after lot rounding: total={total} est_orig={est_orig} raw={buy_qty_raw}")
    buy_price = _buyback_price(instrument=instrument, ask1=float(snapshot.ask1_price))
    return LifeboatBuybackPlan(
        buy_qty=int(buy_qty),
        buy_price=float(buy_price),
        trading_minutes_elapsed=int(trading_minutes_elapsed),
        now=now,
    )
