from __future__ import annotations

import numpy as np

from etf_chip_engine.models import ChipDistribution


class RedemptionCorrector:
    def apply_creation(self, chips: ChipDistribution, delta_shares: float,
                       vwap: float, sigma_buckets: float = 5.0) -> None:
        """注入申购新增份额，以 VWAP 为中心按高斯分布分散。

        Args:
            sigma_buckets: 高斯分布的标准差（桶数），由调用方基于日内波动动态计算。
        """
        if delta_shares <= 0 or chips.chips.size <= 0:
            return
        idx = chips.price_to_index(float(vwap))
        # 下限 2 桶，防止极低波动日过度集中在单一价位
        sigma_buckets = max(float(sigma_buckets), 2.0)
        spread = int(np.ceil(3.0 * sigma_buckets))
        lo = max(0, idx - spread)
        hi = min(len(chips.chips), idx + spread + 1)
        if hi <= lo:
            return
        offsets = np.arange(lo, hi, dtype=np.float64) - float(idx)
        weights = np.exp(-0.5 * (offsets / sigma_buckets) ** 2)
        s = float(weights.sum())
        if s <= 0:
            return
        weights /= s
        chips.chips[lo:hi] += float(delta_shares) * weights.astype(np.float32)

    def apply_redemption(self, chips: ChipDistribution, delta_shares: float, current_price: float) -> None:
        delta = abs(float(delta_shares))
        if delta <= 0 or chips.chips.size <= 0:
            return
        price_grid = chips.get_price_grid()
        r = (float(current_price) - price_grid) / np.maximum(price_grid, 1e-6)
        g = 1.0 + np.maximum(r, 0.0)
        w = chips.chips.astype(np.float64) * g
        total = float(w.sum())
        if total <= 0:
            return
        chips.chips = np.maximum(chips.chips.astype(np.float64) - delta * (w / total), 0.0).astype(np.float32)
