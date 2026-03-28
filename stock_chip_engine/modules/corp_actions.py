from __future__ import annotations

import math

import numpy as np

from etf_chip_engine.models import ChipDistribution


def boundary_adjustment_factor(*, close_none_prev: float, close_front_prev: float) -> float:
    """Compute the boundary adjustment factor between unadjusted and front-adjusted closes.

    For trade_date=T, callers should pass close(T-1) from:
      - dividend_type='none'  -> close_none_prev
      - dividend_type='front' -> close_front_prev (anchored at end_time=T)
    """
    try:
        a = float(close_front_prev)
        b = float(close_none_prev)
    except Exception:
        return float("nan")
    if not (np.isfinite(a) and np.isfinite(b)) or b <= 0:
        return float("nan")
    return a / b


def is_boundary_adjustment_significant(*, factor: float, close_none_prev: float, tick_size: float) -> bool:
    """Decide whether the boundary factor implies a corporate action.

    Threshold uses half a tick in relative terms to avoid float noise while still
    catching small cash dividends.
    """
    try:
        f = float(factor)
        close_prev = float(close_none_prev)
        tick = float(tick_size)
    except Exception:
        return False
    if not (np.isfinite(f) and np.isfinite(close_prev) and np.isfinite(tick)):
        return False
    if close_prev <= 0 or tick <= 0:
        return False
    rel_eps = 0.5 * tick / max(close_prev, 1e-6)
    return abs(f - 1.0) > rel_eps


def rescale_chip_distribution(
    chips: ChipDistribution,
    *,
    price_factor: float,
    new_bucket_size: float,
    factor_bounds: tuple[float, float] = (0.2, 5.0),
) -> ChipDistribution:
    """Rescale chip distribution price grid by a factor and re-bin into new bucket_size.

    Uses a two-bucket linear split to preserve total mass.
    """
    if chips.chips.size <= 0:
        return chips

    try:
        f = float(price_factor)
        bucket = float(new_bucket_size)
    except Exception:
        raise ValueError("invalid price_factor/new_bucket_size")

    if not (np.isfinite(f) and f > 0):
        raise ValueError(f"invalid price_factor: {price_factor}")
    lo, hi = factor_bounds
    if f < float(lo) or f > float(hi):
        raise ValueError(f"price_factor out of bounds: {f} not in [{lo}, {hi}]")

    if not (np.isfinite(bucket) and bucket > 0):
        raise ValueError(f"invalid new_bucket_size: {new_bucket_size}")

    old_grid = chips.get_price_grid().astype(np.float64, copy=False)
    new_prices = old_grid * f

    p_min = float(np.min(new_prices)) if new_prices.size else float("nan")
    p_max = float(np.max(new_prices)) if new_prices.size else float("nan")
    if not (np.isfinite(p_min) and np.isfinite(p_max)):
        raise ValueError("invalid price grid for rescale")

    base_price = math.floor(p_min / bucket) * bucket
    frac = (new_prices - base_price) / bucket
    idx0 = np.floor(frac).astype(np.int64)
    w1 = frac - idx0.astype(np.float64)
    w0 = 1.0 - w1

    max_idx0 = int(np.max(idx0)) if idx0.size else 0
    new_len = max(max_idx0 + 2, 1)
    new_chips = np.zeros(new_len, dtype=np.float64)

    mass = chips.chips.astype(np.float64, copy=False)
    # idx0 is expected >= 0 due to base_price <= p_min; keep guards anyway.
    valid0 = (idx0 >= 0) & (idx0 < new_len)
    if valid0.any():
        np.add.at(new_chips, idx0[valid0], mass[valid0] * w0[valid0])
    idx1 = idx0 + 1
    valid1 = (idx1 >= 0) & (idx1 < new_len)
    if valid1.any():
        np.add.at(new_chips, idx1[valid1], mass[valid1] * w1[valid1])

    new_chips = np.maximum(new_chips, 0.0)

    total_before = float(np.sum(mass))
    total_after = float(np.sum(new_chips))
    if total_before > 0 and total_after > 0:
        new_chips *= total_before / total_after

    out = ChipDistribution(
        etf_code=str(chips.etf_code),
        base_price=float(base_price),
        bucket_size=float(bucket),
        chips=new_chips.astype(np.float32),
        total_shares=float(chips.total_shares),
        last_update=chips.last_update,
    )
    return out

