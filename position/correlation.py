from __future__ import annotations

import math
from typing import Optional

from .constants import CORRELATION_THRESHOLD, CORRELATION_WINDOW_DAYS


def pearson_corr_20d(*, x: list[float], y: list[float]) -> float:
    xs = list(x)
    ys = list(y)
    if len(xs) != len(ys):
        raise AssertionError(f"len mismatch: {len(xs)} vs {len(ys)}")
    if len(xs) < int(CORRELATION_WINDOW_DAYS) + 1:
        raise AssertionError(f"need >= {int(CORRELATION_WINDOW_DAYS)+1} closes, got {len(xs)}")

    closes_x = [float(v) for v in xs[-(int(CORRELATION_WINDOW_DAYS) + 1) :]]
    closes_y = [float(v) for v in ys[-(int(CORRELATION_WINDOW_DAYS) + 1) :]]

    for v in closes_x + closes_y:
        if v <= 0:
            raise AssertionError(f"non-positive close: {v}")

    rx: list[float] = []
    ry: list[float] = []
    for i in range(1, len(closes_x)):
        rx.append(float(math.log(closes_x[i] / closes_x[i - 1])))
        ry.append(float(math.log(closes_y[i] / closes_y[i - 1])))

    n = len(rx)
    if n <= 1:
        return 0.0
    mx = sum(rx) / float(n)
    my = sum(ry) / float(n)

    sxx = 0.0
    syy = 0.0
    sxy = 0.0
    for i in range(n):
        dx = float(rx[i]) - float(mx)
        dy = float(ry[i]) - float(my)
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy

    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    return float(sxy / math.sqrt(sxx * syy))


def is_mutually_exclusive(*, held_etf: Optional[str], new_etf: str, corr: float) -> bool:
    _ = str(new_etf)
    if held_etf is None or str(held_etf) == "":
        return False
    return float(corr) >= float(CORRELATION_THRESHOLD)
