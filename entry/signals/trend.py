from __future__ import annotations

import math

from core.interfaces import Bar


def _ema(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise AssertionError(f"invalid ema period: {period}")
    alpha = 2.0 / (float(period) + 1.0)
    out: list[float] = []
    prev = float(values[0]) if values else 0.0
    for v in values:
        prev = alpha * float(v) + (1.0 - alpha) * prev
        out.append(prev)
    return out


def _kama(values: list[float], period: int = 10, fast: float = 2.0 / 3.0, slow: float = 2.0 / 31.0) -> list[float]:
    if len(values) == 0:
        return []
    out: list[float] = [float(values[0])]
    for i in range(1, len(values)):
        if i < period:
            out.append(float(values[i]))
            continue
        change = abs(float(values[i]) - float(values[i - period]))
        volatility = 0.0
        for j in range(i - period + 1, i + 1):
            volatility += abs(float(values[j]) - float(values[j - 1]))
        er = (change / volatility) if volatility > 0 else 0.0
        sc = (er * (fast - slow) + slow) ** 2
        prev = out[-1]
        out.append(prev + sc * (float(values[i]) - prev))
    return out


def compute_trend(bars: list[Bar]) -> int:
    if len(bars) < 30:
        return 0
    closes = [float(b.close) for b in bars]
    kama = _kama(closes, period=10)
    if len(kama) < 3:
        return 0
    kama_rising = bool(kama[-1] > kama[-2] > kama[-3])

    ema13 = _ema(closes, 13)
    ema13_rising = bool(ema13[-1] > ema13[-2])

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, signal)]
    macd_rising = bool(hist[-1] > hist[-2] and not math.isclose(hist[-1], hist[-2]))

    elder_green = bool(ema13_rising and macd_rising)
    return 1 if (kama_rising and elder_green) else 0


kama = _kama
ema = _ema
