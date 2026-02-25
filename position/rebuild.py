from __future__ import annotations

from typing import Optional

from core.enums import OrderSide, OrderTimeInForce, OrderType
from core.interfaces import OrderRequest
from core.price_utils import tick_ceil


def can_rebuild(*, conditions: dict[str, bool]) -> bool:
    c = dict(conditions)
    return all(bool(v) for v in c.values())


def plan_rebuild_order(*, etf_code: str, target_amount: float, bid1_price: float) -> Optional[OrderRequest]:
    code = str(etf_code)
    price = float(tick_ceil(float(bid1_price)))
    qty = int(float(target_amount) / float(price))
    qty = (int(qty) // 100) * 100
    if qty <= 0:
        return None
    return OrderRequest(
        etf_code=code,
        side=OrderSide.BUY,
        quantity=int(qty),
        order_type=OrderType.LIMIT,
        price=float(price),
        tif=OrderTimeInForce.DAY,
        strategy_name="position",
        remark="REBUILD",
    )


def should_cancel_rebuild(*, score_soft: float) -> bool:
    return float(score_soft) >= 0.5


def assert_rebuild_allowed(*, rebuild_count_this_wave: int) -> None:
    if int(rebuild_count_this_wave) > 0:
        raise AssertionError(f"回补次数={int(rebuild_count_this_wave)}，本波段已用完回补机会")


def rebuild_wave_key(*, etf_code: str, entry_date: str) -> str:
    return f"{str(etf_code)}:{str(entry_date)}"
