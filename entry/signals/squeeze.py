from __future__ import annotations

import math
from typing import Sequence

from core.interfaces import Bar


def _sma(x: Sequence[float], n: int) -> float:
    if len(x) < n:
        raise AssertionError(f"sma window too large: n={n} len={len(x)}")
    return float(sum(x[-n:]) / n)


def _std(x: Sequence[float], n: int) -> float:
    if len(x) < n:
        raise AssertionError(f"std window too large: n={n} len={len(x)}")
    w = [float(v) for v in x[-n:]]
    m = sum(w) / float(n)
    var = sum((v - m) ** 2 for v in w) / float(n)
    return float(math.sqrt(var))


def _linreg_predict_last(y: Sequence[float]) -> float:
    n = len(y)
    if n <= 1:
        return float(y[-1]) if y else 0.0
    sx = (n - 1) * n / 2.0
    sxx = (n - 1) * n * (2 * n - 1) / 6.0
    sy = sum(float(v) for v in y)
    sxy = sum(i * float(v) for i, v in enumerate(y))
    denom = n * sxx - sx * sx
    if denom == 0:
        return float(y[-1])
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    x_last = float(n - 1)
    return float(slope * x_last + intercept)


def _true_range(prev_close: float, high: float, low: float) -> float:
    return float(max(high - low, abs(high - prev_close), abs(low - prev_close)))


def compute_squeeze(bars: list[Bar]) -> int:
    if len(bars) < 25:
        return 0
    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    n = 20

    squeeze_on: list[bool] = []
    for i in range(len(bars)):
        if i + 1 < n:
            squeeze_on.append(False)
            continue
        w_close = closes[i + 1 - n : i + 1]
        bb_mid = float(sum(w_close) / n)
        bb_std = _std(w_close, n)
        bb_upper = bb_mid + 2.0 * bb_std
        bb_lower = bb_mid - 2.0 * bb_std

        trs: list[float] = []
        for j in range(i + 1 - n, i + 1):
            prev_c = closes[j - 1] if j - 1 >= 0 else closes[j]
            trs.append(_true_range(prev_c, highs[j], lows[j]))
        kc_atr = float(sum(trs) / n)
        kc_upper = bb_mid + 1.5 * kc_atr
        kc_lower = bb_mid - 1.5 * kc_atr
        squeeze_on.append(bool(bb_upper < kc_upper and bb_lower > kc_lower))

    t = len(bars) - 1
    if t < 4:
        return 0
    recent_squeeze = sum(1 for v in squeeze_on[t - 4 : t + 1] if v) >= 3

    w_close_t = closes[t + 1 - n : t + 1]
    bb_mid_t = float(sum(w_close_t) / n)
    bb_std_t = _std(closes[: t + 1], n)
    bb_upper_t = bb_mid_t + 2.0 * bb_std_t

    trs_t: list[float] = []
    for j in range(t + 1 - n, t + 1):
        prev_c = closes[j - 1] if j - 1 >= 0 else closes[j]
        trs_t.append(_true_range(prev_c, highs[j], lows[j]))
    kc_atr_t = float(sum(trs_t) / n)
    kc_upper_t = bb_mid_t + 1.5 * kc_atr_t

    fired = bool(recent_squeeze and (not squeeze_on[t]) and (bb_upper_t > kc_upper_t))

    lr_pred_last = _linreg_predict_last(w_close_t)
    momentum = float(closes[t] - lr_pred_last)
    momentum_positive = bool(momentum > 0 and not math.isclose(momentum, 0.0))
    return 1 if (fired and momentum_positive) else 0
