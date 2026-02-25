from __future__ import annotations

from typing import Optional, Sequence

from core.interfaces import Bar

from .constants import ATR_PERIOD, K_CHIP_DECAY, K_NORMAL, K_REDUCED
from .types import ChandelierState


def _true_range(*, high: float, low: float, prev_close: float) -> float:
    h = float(high)
    l = float(low)
    pc = float(prev_close)
    return max(h - l, abs(h - pc), abs(l - pc))


def compute_atr_wilder(bars: Sequence[Bar], *, period: int = ATR_PERIOD) -> float:
    p = int(period)
    if p <= 0:
        raise AssertionError(f"invalid ATR period: {period}")
    if not bars:
        raise AssertionError("bars empty")

    atr = float(bars[0].high) - float(bars[0].low)
    alpha = 1.0 / float(p)
    for i in range(1, len(bars)):
        tr = _true_range(
            high=float(bars[i].high),
            low=float(bars[i].low),
            prev_close=float(bars[i - 1].close),
        )
        atr = (1.0 - alpha) * float(atr) + alpha * float(tr)
    return float(atr)


def update_highest_high(prev_hh: float, bars: Sequence[Bar]) -> float:
    hh = float(prev_hh)
    for b in bars:
        hh = max(float(hh), float(b.high))
    return float(hh)


def choose_k(*, reduced: bool, s_chip: float) -> float:
    if bool(reduced):
        k = float(K_REDUCED)
    else:
        k = float(K_CHIP_DECAY) if float(s_chip) >= 0.3 else float(K_NORMAL)
    if float(k) not in (float(K_NORMAL), float(K_CHIP_DECAY), float(K_REDUCED)):
        raise AssertionError(f"k invalid: {k}")
    if bool(reduced) and float(k) != float(K_REDUCED):
        raise AssertionError(f"reduced but k={k}, expect {K_REDUCED}")
    return float(k)


def compute_chandelier_state(
    *,
    bars: Sequence[Bar],
    prev_hh: float,
    reduced: bool,
    s_chip: float,
    prev_k: Optional[float] = None,
) -> ChandelierState:
    hh = update_highest_high(float(prev_hh), bars)
    atr = compute_atr_wilder(bars, period=int(ATR_PERIOD))
    k = choose_k(reduced=bool(reduced), s_chip=float(s_chip))
    if prev_k is not None and float(k) > float(prev_k):
        raise AssertionError(f"k increased: prev={prev_k} now={k}")
    stop = float(hh) - float(k) * float(atr)
    return ChandelierState(hh=float(hh), atr=float(atr), k=float(k), stop=float(stop))

