from __future__ import annotations

import math

from .constants import ETF_LIMIT_DOWN_PCT, ETF_LIMIT_UP_PCT, TICK_SIZE


def tick_floor(x: float, *, tick_size: float = TICK_SIZE) -> float:
    scale = 1.0 / float(tick_size)
    return math.floor(float(x) * scale) / scale


def tick_ceil(x: float, *, tick_size: float = TICK_SIZE) -> float:
    scale = 1.0 / float(tick_size)
    return math.ceil(float(x) * scale) / scale


def round_to_tick(x: float, *, tick_size: float = TICK_SIZE) -> float:
    scale = 1.0 / float(tick_size)
    return round(float(x) * scale) / scale


def clamp(x: float, low: float, high: float) -> float:
    return min(max(float(x), float(low)), float(high))


def limit_up_price(prev_close: float, *, limit_up_pct: float = ETF_LIMIT_UP_PCT, tick_size: float = TICK_SIZE) -> float:
    raw = float(prev_close) * (1.0 + float(limit_up_pct))
    return tick_floor(raw, tick_size=tick_size)


def limit_down_price(
    prev_close: float, *, limit_down_pct: float = ETF_LIMIT_DOWN_PCT, tick_size: float = TICK_SIZE
) -> float:
    raw = float(prev_close) * (1.0 - float(limit_down_pct))
    return tick_ceil(raw, tick_size=tick_size)


def clamp_to_limits(price: float, *, prev_close: float, tick_size: float = TICK_SIZE) -> float:
    up = limit_up_price(prev_close, tick_size=tick_size)
    down = limit_down_price(prev_close, tick_size=tick_size)
    return clamp(price, down, up)


def align_order_price(*, price: float, side: str, lower_limit: float, upper_limit: float, tick_size: float = TICK_SIZE) -> float:
    s = str(side).upper()
    if s == "BUY":
        aligned = tick_ceil(price, tick_size=tick_size)
    elif s == "SELL":
        aligned = tick_floor(price, tick_size=tick_size)
    else:
        raise AssertionError(f"unknown side: {side}")
    return clamp(aligned, lower_limit, upper_limit)
