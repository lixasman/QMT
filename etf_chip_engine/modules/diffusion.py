from __future__ import annotations

import numpy as np

from etf_chip_engine.models import ChipDistribution


def _gaussian_filter1d(x: np.ndarray, sigma: float) -> np.ndarray:
    try:
        from scipy.ndimage import gaussian_filter1d  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 scipy：请先安装 scipy 后再使用扩散/平滑功能") from e
    return gaussian_filter1d(x, sigma=float(sigma))


def apply_brownian_diffusion(chips: ChipDistribution, atr: float, *, k_diff: float = 0.08) -> None:
    if chips.chips.size <= 0:
        return
    if not np.isfinite(float(atr)) or float(atr) <= 0:
        return
    sigma_price = float(k_diff) * float(atr)
    sigma_buckets = max(sigma_price / float(chips.bucket_size), 0.5)
    total_before = float(chips.chips.sum())
    chips.chips = _gaussian_filter1d(chips.chips.astype(np.float64), sigma=sigma_buckets).astype(np.float32)
    total_after = float(chips.chips.sum())
    if total_after > 0 and total_before > 0:
        chips.chips *= total_before / total_after
