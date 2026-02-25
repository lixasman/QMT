from __future__ import annotations

from typing import Any, Optional

import numpy as np

from etf_chip_engine.models import ChipDistribution


def _gaussian_filter1d(x: np.ndarray, sigma: float) -> np.ndarray:
    try:
        from scipy.ndimage import gaussian_filter1d  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 scipy：请先安装 scipy 后再使用扩散/平滑功能") from e
    return gaussian_filter1d(x, sigma=float(sigma))


def _find_peaks(x: np.ndarray, *, height: float) -> np.ndarray:
    try:
        from scipy.signal import find_peaks  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 scipy：请先安装 scipy 后再使用峰值检测功能") from e
    peaks, props = find_peaks(x, height=height)
    _ = props
    return np.asarray(peaks, dtype=np.int64)


class SmoothedPRTracker:
    """获利盘比例 EMA 平滑器，过滤盘中价格噪声导致的 PR 抖动。

    输出双尺度 EMA：短期用于盘中决策，长期用于趋势判断。
    每次调用 update() 时传入原始 PR 值，返回平滑后的 dict。
    """

    def __init__(self, span_short: int = 30, span_long: int = 120):
        self.ema_short: Optional[float] = None
        self.ema_long: Optional[float] = None
        self._alpha_s = 2.0 / (max(int(span_short), 2) + 1)
        self._alpha_l = 2.0 / (max(int(span_long), 2) + 1)

    def update(self, raw_pr: float) -> dict[str, float]:
        pr = float(raw_pr)
        if self.ema_short is None:
            self.ema_short = pr
            self.ema_long = pr
        else:
            self.ema_short += self._alpha_s * (pr - self.ema_short)
            self.ema_long += self._alpha_l * (pr - self.ema_long)
        return {
            "pr_raw": pr,
            "pr_ema_short": round(self.ema_short, 4),  # type: ignore[arg-type]
            "pr_ema_long": round(self.ema_long, 4),  # type: ignore[arg-type]
        }

    def reset(self) -> None:
        self.ema_short = None
        self.ema_long = None


def calc_profit_ratio(chips: ChipDistribution, current_price: float) -> float:
    if chips.chips.size <= 0:
        return 0.0
    idx = min(chips.price_to_index(float(current_price)), len(chips.chips) - 1)
    profitable = float(chips.chips[: idx + 1].sum())
    total = float(chips.chips.sum())
    return (profitable / total * 100.0) if total > 0 else 0.0


def find_dense_zones(
    chips: ChipDistribution,
    current_price: float,
    *,
    top_n: int = 3,
    smooth_sigma: float = 50.0,
    min_height_ratio: float = 0.1,
    atr: Optional[float] = None,
    bucket_size: Optional[float] = None,
) -> list[dict[str, Any]]:
    if chips.chips.size <= 0:
        return []
    # ATR 自适应平滑：sigma = max(3, 0.2 * ATR / bucket_size)
    sigma = float(smooth_sigma)
    if atr is not None and bucket_size is not None and float(atr) > 0 and float(bucket_size) > 0:
        sigma = max(5.0, 0.2 * float(atr) / float(bucket_size))
    smoothed = _gaussian_filter1d(chips.chips.astype(np.float64), sigma=sigma)
    h = float(smoothed.max()) * float(min_height_ratio) if smoothed.size else 0.0
    peaks = _find_peaks(smoothed, height=h)

    results: list[dict[str, Any]] = []
    total = float(smoothed.sum())
    cp = float(current_price)
    for p_idx in peaks.tolist():
        price = chips.index_to_price(int(p_idx))
        density = float(smoothed[int(p_idx)] / total) if total > 0 else 0.0
        zone_type = "support" if price < cp else "resistance"
        results.append({"price": float(price), "density": density, "type": zone_type})

    results.sort(key=lambda x: float(x.get("density", 0.0)), reverse=True)
    return results[: max(int(top_n), 0)]


def calc_asr(chips: ChipDistribution, current_price: float, *, atr: float, k: float = 1.0) -> float:
    if chips.chips.size <= 0:
        return 0.0
    cp = float(current_price)
    lo = max(0, chips.price_to_index(cp - float(k) * float(atr)))
    hi = min(len(chips.chips) - 1, chips.price_to_index(cp + float(k) * float(atr)))
    active = float(chips.chips[lo : hi + 1].sum())
    total = float(chips.chips.sum())
    return (active / total) if total > 0 else 0.0

