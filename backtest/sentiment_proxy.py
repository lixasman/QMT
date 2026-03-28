from __future__ import annotations

from typing import Sequence

from core.interfaces import Bar


def compute_sentiment_proxy(bars: Sequence[Bar], *, volume_ma_window: int = 5) -> tuple[int, float]:
    """Multi-factor continuous sentiment proxy for backtest use.

    Returns (score_100, score_01):
      score_100 ∈ [0, 100]  — for entry Phase 1 Gate 1 (threshold 60)
      score_01  ∈ [0.1, 0.9] — for exit S_sentiment (threshold 0.35)

    Factors:
      F1 (40%): Return momentum — average daily return over the window
      F2 (30%): Volume trend   — latest volume vs prior average, directional
      F3 (30%): Price position  — close relative to high-low range
    """
    if int(volume_ma_window) <= 0:
        raise AssertionError(f"volume_ma_window must be positive: {volume_ma_window}")
    need = int(volume_ma_window) + 1
    if len(bars) < need:
        return 50, 0.5

    window_bars = list(bars[-need:])

    # F1: return momentum — normalized to [0, 1]
    returns: list[float] = []
    for i in range(1, len(window_bars)):
        prev_close = float(window_bars[i - 1].close)
        if prev_close > 0:
            returns.append((float(window_bars[i].close) - prev_close) / prev_close)
    if not returns:
        return 50, 0.5
    avg_ret = sum(returns) / len(returns)
    f1 = max(0.0, min(1.0, avg_ret * 10.0 + 0.5))

    # F2: volume trend — directional
    vol_prior = [float(b.volume) for b in window_bars[:-1]]
    vol_avg = sum(vol_prior) / len(vol_prior) if vol_prior else 1.0
    vol_latest = float(window_bars[-1].volume)
    vol_ratio = vol_latest / vol_avg if vol_avg > 0 else 1.0
    latest_ret = returns[-1] if returns else 0.0
    if latest_ret >= 0:
        vol_signal = vol_ratio
    else:
        vol_signal = 2.0 - vol_ratio
    f2 = max(0.0, min(1.0, vol_signal / 2.0))

    # F3: price position in window range
    highs = [float(b.high) for b in window_bars]
    lows = [float(b.low) for b in window_bars]
    range_hl = max(highs) - min(lows)
    if range_hl > 0:
        f3 = (float(window_bars[-1].close) - min(lows)) / range_hl
    else:
        f3 = 0.5

    # composite score
    raw = 0.4 * f1 + 0.3 * f2 + 0.3 * f3
    score_01 = round(max(0.1, min(0.9, raw)), 4)
    score_100 = int(round(score_01 * 100))
    score_100 = max(0, min(100, score_100))
    return score_100, float(score_01)
