from __future__ import annotations

from core.price_utils import align_order_price, clamp_to_limits, limit_down_price, limit_up_price, tick_ceil, tick_floor


def test_tick_floor_ceil() -> None:
    assert tick_floor(1.20932) == 1.209
    assert tick_ceil(1.20901) == 1.21


def test_limit_prices() -> None:
    assert limit_up_price(1.0) == 1.1
    assert limit_down_price(1.0) == 0.9


def test_clamp_to_limits() -> None:
    assert clamp_to_limits(1.101, prev_close=1.0) == 1.1
    assert clamp_to_limits(0.899, prev_close=1.0) == 0.9


def test_align_order_price_pipeline() -> None:
    assert align_order_price(price=1.04661, side="BUY", lower_limit=0.9, upper_limit=1.1) == 1.047
    assert align_order_price(price=1.04661, side="SELL", lower_limit=0.9, upper_limit=1.1) == 1.046
