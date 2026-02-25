from __future__ import annotations

import numpy as np
import pandas as pd

from etf_chip_engine.models import ChipDistribution


def recalibrate_tails(
    chips: ChipDistribution,
    recent_close: float,
    atr: float,
    *,
    atr_k: float = 2.0,
    decay_rate: float = 0.1,
) -> None:
    """将远离当前价 ±atr_k×ATR 的尾部筹码按距离指数衰减。

    冷启动后调用，防止三角分布在远端残留"幽灵筹码"长期影响密集区。
    """
    if chips.chips.size <= 0 or atr <= 0:
        return
    grid = chips.get_price_grid()
    dist = np.abs(grid - float(recent_close))
    threshold = float(atr_k) * float(atr)
    mask = dist > threshold
    if mask.any():
        excess = (dist[mask] - threshold) / float(atr)
        chips.chips[mask] *= np.exp(-float(decay_rate) * excess).astype(np.float32)


def cold_start_from_daily(
    daily_df: pd.DataFrame,
    chips: ChipDistribution,
    *,
    decay: float = 0.95,
    total_shares: float = 0.0,
) -> None:
    if daily_df is None or daily_df.empty:
        return
    n = int(len(daily_df))
    for i, (_, row) in enumerate(daily_df.iterrows()):
        weight = float(decay) ** float(n - 1 - i)
        vol = float(row["volume"]) * weight
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])

        if high < low:
            high, low = low, high
        chips.ensure_range(low, high, padding_buckets=20)

        price_range = np.arange(low, high + chips.bucket_size, chips.bucket_size, dtype=np.float64)
        if price_range.size < 2:
            idx = chips.price_to_index(close)
            if 0 <= idx < len(chips.chips):
                chips.chips[idx] += np.float32(vol)
            continue

        for p in price_range.tolist():
            idx = chips.price_to_index(float(p))
            if not (0 <= idx < len(chips.chips)):
                continue
            if p <= close:
                ratio = (p - low) / max(close - low, 1e-6)
            else:
                ratio = (high - p) / max(high - close, 1e-6)
            chips.chips[idx] += np.float32(vol * max(ratio, 0.0) / float(price_range.size))

    # 冷启动结束后设置 total_shares（改进 4：防止后续 base_tr 分母为 0）
    if total_shares > 0:
        chips.total_shares = float(total_shares)

