from __future__ import annotations

from datetime import datetime, time

from .constants import T0_ACTIVE_BUY_WINDOWS, T0_CLOSE_ONLY_WINDOW, T0_REVERSE_SELL_CUTOFF, T0_SWEEPER_CANCEL_BUY_AT


def is_buy_allowed(*, now: datetime) -> bool:
    t = now.time()
    for w in T0_ACTIVE_BUY_WINDOWS:
        if w[0] <= t < w[1]:
            return True
    return False


def is_reverse_sell_allowed(*, now: datetime) -> bool:
    t = now.time()
    if t > time(15, 0):
        return False
    if t >= T0_REVERSE_SELL_CUTOFF:
        return False
    if time(10, 0) <= t < time(11, 25):
        return True
    if time(13, 15) <= t < T0_REVERSE_SELL_CUTOFF:
        return True
    return False


def is_close_only(*, now: datetime) -> bool:
    t = now.time()
    if T0_CLOSE_ONLY_WINDOW[0] <= t < T0_CLOSE_ONLY_WINDOW[1]:
        return True
    if T0_SWEEPER_CANCEL_BUY_AT <= t <= time(14, 30):
        return True
    return False


def is_sell_allowed(*, now: datetime) -> bool:
    t = now.time()
    if time(10, 0) <= t <= time(11, 25):
        return True
    if T0_CLOSE_ONLY_WINDOW[0] <= t < T0_CLOSE_ONLY_WINDOW[1]:
        return True
    if time(13, 15) <= t < time(14, 15):
        return True
    if T0_SWEEPER_CANCEL_BUY_AT <= t <= time(14, 30):
        return True
    return False
