from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from core.buy_order_config import get_aggressive_buy_multiplier, get_aggressive_buy_use_ask1
from core.enums import DataQuality, OrderSide
from core.interfaces import InstrumentInfo, TickSnapshot
from core.price_utils import align_order_price
from core.time_utils import trading_minutes_between

from .constants import (
    LIFEBOAT_BUYBACK_CUTOFF_TIME,
    LIFEBOAT_BUYBACK_REENTRY_TICKS,
    LIFEBOAT_COOLDOWN_TRADING_MINUTES,
    LIFEBOAT_DEAD_CAT_LIMITDOWN_MULTIPLIER,
    LIFEBOAT_SELL_PCT,
    LIFEBOAT_TIGHT_STOP_PCT,
)
from .exit_config import get_exit_layer1_sell_discount, get_exit_layer1_use_stop_price
from .types import LifeboatBuybackPlan, LifeboatSellPlan


@dataclass(frozen=True)
class BuybackEvaluation:
    passed: bool
    trading_minutes_elapsed: int
    conditions: dict[str, Any]


def _layer1_sell_price(
    *,
    instrument: InstrumentInfo,
    bid1: float,
    stop_price: float | None = None,
    sell_discount: float | None = None,
    use_stop_price: bool | None = None,
) -> float:
    discount = float(get_exit_layer1_sell_discount()) if sell_discount is None else float(sell_discount)
    prefer_stop = bool(get_exit_layer1_use_stop_price()) if use_stop_price is None else bool(use_stop_price)
    raw_price = float(bid1) * float(discount)
    if bool(prefer_stop) and stop_price is not None and float(stop_price) > 0.0:
        raw_price = float(stop_price)
    return align_order_price(
        price=float(raw_price),
        side=OrderSide.SELL.value,
        lower_limit=float(instrument.limit_down),
        upper_limit=float(instrument.limit_up),
        tick_size=float(instrument.price_tick),
    )


def _buyback_price(
    *,
    instrument: InstrumentInfo,
    ask1: float,
    buy_multiplier: float | None = None,
    use_ask1: bool | None = None,
) -> float:
    raw_price = float(ask1)
    prefer_ask1 = bool(get_aggressive_buy_use_ask1()) if use_ask1 is None else bool(use_ask1)
    multiplier = float(get_aggressive_buy_multiplier()) if buy_multiplier is None else float(buy_multiplier)
    if not bool(prefer_ask1):
        raw_price = float(raw_price) * float(multiplier)
    return align_order_price(
        price=float(raw_price),
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
    stop_price: float | None = None,
    sell_discount: float | None = None,
    use_stop_price: bool | None = None,
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
    sell_price = _layer1_sell_price(
        instrument=instrument,
        bid1=float(snapshot.bid1_price),
        stop_price=stop_price,
        sell_discount=sell_discount,
        use_stop_price=use_stop_price,
    )
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
    reentry_tick = max(0.0, float(instrument.price_tick))
    reentry_price = float(stop_price) + float(LIFEBOAT_BUYBACK_REENTRY_TICKS) * float(reentry_tick)
    a = bool(elapsed >= int(LIFEBOAT_COOLDOWN_TRADING_MINUTES))
    b = bool(float(snapshot.last_price) >= float(reentry_price))
    c = bool(float(score_soft) == 0.0)
    d = bool(snapshot.data_quality != DataQuality.STALE)
    e = bool(float(snapshot.last_price) > float(instrument.limit_down) * float(LIFEBOAT_DEAD_CAT_LIMITDOWN_MULTIPLIER))
    f = bool(now.time() <= LIFEBOAT_BUYBACK_CUTOFF_TIME)
    passed = bool(a and b and c and d and e and f)
    conditions: dict[str, Any] = {
        "a_cooldown": {"pass": a, "minutes": int(elapsed), "required": int(LIFEBOAT_COOLDOWN_TRADING_MINUTES)},
        "b_price_above_stop": {
            "pass": b,
            "price": float(snapshot.last_price),
            "stop": float(stop_price),
            "required_price": float(reentry_price),
            "buffer_ticks": int(LIFEBOAT_BUYBACK_REENTRY_TICKS),
        },
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
    buy_multiplier: float | None = None,
    use_ask1: bool | None = None,
) -> LifeboatBuybackPlan:
    qty = int(current_total_qty)
    if qty <= 0:
        raise AssertionError(f"current_total_qty must be positive for buyback, got {current_total_qty}")
    raw = int(round(float(qty) / 0.30 * 0.70 / 100.0) * 100)
    if raw <= 0:
        raise AssertionError(f"buyback qty rounds to 0 from current_total_qty={current_total_qty}")
    price = _buyback_price(
        instrument=instrument,
        ask1=float(snapshot.ask1_price),
        buy_multiplier=buy_multiplier,
        use_ask1=use_ask1,
    )
    return LifeboatBuybackPlan(
        buy_qty=int(raw),
        buy_price=float(price),
        trading_minutes_elapsed=int(trading_minutes_elapsed),
        now=now,
    )
