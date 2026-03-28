from __future__ import annotations

from typing import Optional

from core.interfaces import Bar


def compute_volume_break(bars: list[Bar], resistance_price: Optional[float]) -> int:
    if len(bars) < 21:
        return 0
    t = len(bars) - 1
    close_t = float(bars[t].close)
    open_t = float(bars[t].open)
    high_t = float(bars[t].high)
    low_t = float(bars[t].low)
    vol_t = float(bars[t].volume)

    if resistance_price is None:
        resistance = max(float(b.high) for b in bars[t - 20 : t])
    else:
        resistance = float(resistance_price)

    price_break = bool(close_t > resistance)

    vol_ma20 = sum(float(b.volume) for b in bars[t - 20 : t]) / 20.0
    vol_ratio = (vol_t / vol_ma20) if vol_ma20 > 0 else 0.0
    volume_confirm = bool(vol_ratio >= 1.5)

    denom = max(high_t - low_t, 0.001)
    body_ratio = (close_t - open_t) / denom
    solid_candle = bool(body_ratio > 0.5)

    return 1 if (price_break and volume_confirm and solid_candle) else 0
