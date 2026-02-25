from __future__ import annotations

from typing import Optional, Sequence

from core.interfaces import Bar

from ..constants import (
    ADX_PERIOD,
    ADX_STRONG_TREND_THRESHOLD,
    RSI_MIN_PEAK_GAP_DAYS,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_PEAK_LEFT_RIGHT,
    RSI_PERIOD,
    VOLUME_MA_DAYS,
    VOLUME_NEW_HIGH_LOOKBACK_DAYS,
    VOLUME_SHRINK_NEW_HIGH_MIN_COUNT,
    VOLUME_SHRINK_RATIO,
    VOLUME_SHRINK_WINDOW_DAYS,
)


def _wilder_ema(values: Sequence[float], period: int) -> list[float]:
    p = int(period)
    if p <= 0:
        raise AssertionError(f"invalid period: {period}")
    if not values:
        return []
    out: list[float] = []
    alpha = 1.0 / float(p)
    prev = float(values[0])
    out.append(prev)
    for x in values[1:]:
        prev = (1.0 - alpha) * float(prev) + alpha * float(x)
        out.append(float(prev))
    return out


def _compute_rsi(closes: Sequence[float], period: int = RSI_PERIOD) -> list[Optional[float]]:
    if len(closes) < 2:
        return [None for _ in closes]
    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for i in range(1, len(closes)):
        chg = float(closes[i]) - float(closes[i - 1])
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))

    avg_g = _wilder_ema(gains, int(period))
    avg_l = _wilder_ema(losses, int(period))
    out: list[Optional[float]] = []
    for g, l in zip(avg_g, avg_l):
        if float(l) == 0.0:
            out.append(100.0 if float(g) > 0.0 else 50.0)
            continue
        rs = float(g) / float(l)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        out.append(float(rsi))
    return out


def _is_local_max(values: Sequence[float], idx: int, left_right: int) -> bool:
    n = len(values)
    lr = int(left_right)
    if idx - lr < 0 or idx + lr >= n:
        return False
    v = float(values[idx])
    for j in range(idx - lr, idx + lr + 1):
        if j == idx:
            continue
        if float(values[j]) >= float(v):
            return False
    return True


def _find_last_two_confirmed_peaks(values: Sequence[float], left_right: int, min_gap: int) -> Optional[tuple[int, int]]:
    n = len(values)
    lr = int(left_right)
    peaks: list[int] = []
    last_confirmable = n - 1 - lr
    for i in range(lr, last_confirmable + 1):
        if _is_local_max(values, i, lr):
            peaks.append(int(i))
    if len(peaks) < 2:
        return None
    p2 = peaks[-1]
    for p1 in reversed(peaks[:-1]):
        if int(p2) - int(p1) >= int(min_gap):
            return int(p1), int(p2)
    return None


def _compute_adx(bars: Sequence[Bar], period: int = ADX_PERIOD) -> list[Optional[float]]:
    p = int(period)
    if p <= 0:
        raise AssertionError(f"invalid period: {period}")
    if len(bars) < 2:
        return [None for _ in bars]

    tr: list[float] = [0.0]
    pdm: list[float] = [0.0]
    ndm: list[float] = [0.0]
    for i in range(1, len(bars)):
        hi = float(bars[i].high)
        lo = float(bars[i].low)
        pc = float(bars[i - 1].close)
        ph = float(bars[i - 1].high)
        pl = float(bars[i - 1].low)

        tr_i = max(hi - lo, abs(hi - pc), abs(lo - pc))
        up_move = hi - ph
        down_move = pl - lo
        pdm_i = up_move if up_move > down_move and up_move > 0 else 0.0
        ndm_i = down_move if down_move > up_move and down_move > 0 else 0.0

        tr.append(float(tr_i))
        pdm.append(float(pdm_i))
        ndm.append(float(ndm_i))

    atr = _wilder_ema(tr, p)
    pdi_s = _wilder_ema(pdm, p)
    ndi_s = _wilder_ema(ndm, p)

    dx: list[float] = []
    for a, pp, nn in zip(atr, pdi_s, ndi_s):
        if float(a) <= 0.0:
            dx.append(0.0)
            continue
        pdi = 100.0 * float(pp) / float(a)
        ndi = 100.0 * float(nn) / float(a)
        denom = float(pdi) + float(ndi)
        if denom <= 0.0:
            dx.append(0.0)
            continue
        dx.append(100.0 * abs(float(pdi) - float(ndi)) / denom)

    adx_raw = _wilder_ema(dx, p)
    out: list[Optional[float]] = []
    for v in adx_raw:
        out.append(float(v))
    return out


def _volume_shrink_new_high(bars: Sequence[Bar]) -> bool:
    if len(bars) < max(int(VOLUME_SHRINK_WINDOW_DAYS), int(VOLUME_MA_DAYS), int(VOLUME_NEW_HIGH_LOOKBACK_DAYS)) + 1:
        return False
    w = list(bars[-int(VOLUME_SHRINK_WINDOW_DAYS) :])
    count = 0
    for i in range(len(w)):
        idx_global = len(bars) - len(w) + i
        if idx_global < int(VOLUME_MA_DAYS):
            continue
        close_d = float(bars[idx_global].close)
        lookback = bars[idx_global - int(VOLUME_NEW_HIGH_LOOKBACK_DAYS) : idx_global]
        if not lookback:
            continue
        if float(close_d) <= max(float(x.close) for x in lookback):
            continue
        ma5 = sum(float(x.volume) for x in bars[idx_global - int(VOLUME_MA_DAYS) : idx_global]) / float(VOLUME_MA_DAYS)
        if float(bars[idx_global].volume) < float(ma5) * float(VOLUME_SHRINK_RATIO):
            count += 1
    return bool(int(count) >= int(VOLUME_SHRINK_NEW_HIGH_MIN_COUNT))


def compute_s_diverge(bars: Sequence[Bar]) -> float:
    if len(bars) < 30:
        return 0.0
    closes = [float(b.close) for b in bars]
    rsi = _compute_rsi(closes, int(RSI_PERIOD))
    peaks = _find_last_two_confirmed_peaks(closes, int(RSI_PEAK_LEFT_RIGHT), int(RSI_MIN_PEAK_GAP_DAYS))
    if peaks is None:
        return 0.0
    p1, p2 = peaks
    r1 = rsi[p1]
    r2 = rsi[p2]
    if r1 is None or r2 is None:
        return 0.0
    if not (float(closes[p2]) > float(closes[p1]) and float(r2) < float(r1) and float(r1) > float(RSI_OVERBOUGHT_THRESHOLD)):
        return 0.0

    adx = _compute_adx(bars, int(ADX_PERIOD))
    adx_t = adx[-1]
    adx_t_1 = adx[-2] if len(adx) >= 2 else None
    adx_t_2 = adx[-3] if len(adx) >= 3 else None
    adx_turn = False
    if adx_t is not None and adx_t_1 is not None and adx_t_2 is not None:
        if float(adx_t_1) >= float(adx_t_2) and float(adx_t) < float(adx_t_1) and float(adx_t) > float(ADX_STRONG_TREND_THRESHOLD):
            adx_turn = True

    shrink_high = _volume_shrink_new_high(bars)
    return 1.0 if (adx_turn or shrink_high) else 0.0
