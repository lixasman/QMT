from __future__ import annotations

from typing import Optional, Sequence

from core.interfaces import Bar

from .constants import ATR_PERIOD
from .exit_config import (
    get_exit_atr_pct_max,
    get_exit_atr_pct_min,
    get_exit_k_chip_decay,
    get_exit_k_normal,
    get_exit_k_reduced,
)
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


def choose_k(
    *,
    reduced: bool,
    s_chip: float,
    k_normal: float | None = None,
    k_chip_decay: float | None = None,
    k_reduced: float | None = None,
) -> float:
    normal = float(get_exit_k_normal()) if k_normal is None else float(k_normal)
    chip_decay = float(get_exit_k_chip_decay()) if k_chip_decay is None else float(k_chip_decay)
    reduced_k = float(get_exit_k_reduced()) if k_reduced is None else float(k_reduced)
    if bool(reduced):
        k = float(reduced_k)
    else:
        k = float(chip_decay) if float(s_chip) >= 0.3 else float(normal)
    if float(k) <= 0:
        raise AssertionError(f"k invalid: {k}")
    return float(k)


def compute_chandelier_state(
    *,
    bars: Sequence[Bar],
    prev_hh: float,
    reduced: bool,
    s_chip: float,
    prev_k: Optional[float] = None,
    atr_pct_min: float | None = None,
    atr_pct_max: float | None = None,
    k_normal: float | None = None,
    k_chip_decay: float | None = None,
    k_reduced: float | None = None,
) -> ChandelierState:
    hh = update_highest_high(float(prev_hh), bars)
    atr = compute_atr_wilder(bars, period=int(ATR_PERIOD))
    close_ref = float(bars[-1].close) if bars else 0.0
    if close_ref > 0:
        min_pct = get_exit_atr_pct_min() if atr_pct_min is None else float(atr_pct_min)
        max_pct = get_exit_atr_pct_max() if atr_pct_max is None else float(atr_pct_max)
        if min_pct is not None and float(min_pct) > 0:
            atr = max(float(atr), float(min_pct) * float(close_ref))
        if max_pct is not None and float(max_pct) > 0:
            atr = min(float(atr), float(max_pct) * float(close_ref))
    k = choose_k(
        reduced=bool(reduced),
        s_chip=float(s_chip),
        k_normal=k_normal,
        k_chip_decay=k_chip_decay,
        k_reduced=k_reduced,
    )
    if prev_k is not None and float(k) > float(prev_k):
        raise AssertionError(f"k increased: prev={prev_k} now={k}")
    stop = float(hh) - float(k) * float(atr)
    return ChandelierState(hh=float(hh), atr=float(atr), k=float(k), stop=float(stop))

