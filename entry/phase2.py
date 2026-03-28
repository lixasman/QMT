from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional

from core.interfaces import Bar
from core.warn_utils import degrade_once
from core.time_utils import next_trading_day

from .constants import STRONG_SIGNAL_THRESHOLD
from .phase2_config import get_phase2_continuation_config
from .scoring import compute_entry_score
from .signals.chip_strength import compute_chip_strength
from .signals.micro import compute_micro
from .signals.squeeze import compute_squeeze
from .signals.trend import compute_trend
from .signals.volume_break import compute_volume_break
from .types import SignalFired, WatchlistItem


def compute_atr_20(bars: list[Bar]) -> float:
    if len(bars) < 21:
        return 0.0
    trs: list[float] = []
    for i in range(len(bars) - 20, len(bars)):
        prev_close = float(bars[i - 1].close)
        high = float(bars[i].high)
        low = float(bars[i].low)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(float(tr))
    return float(sum(trs) / 20.0)


def compute_phase2_signals(*, bars: list[Bar], watch: WatchlistItem, s_micro_missing: Optional[float] = None) -> dict[str, float]:
    s_squeeze = float(compute_squeeze(bars))
    s_trend = float(compute_trend(bars))
    s_chip = float(compute_chip_strength(watch.profit_ratio))
    s_volume = float(compute_volume_break(bars, watch.nearest_resistance))

    if watch.ofi_daily is None or watch.vpin_rank is None or watch.vs_max is None:
        s_micro = 0.0 if s_micro_missing is None else float(max(0.0, min(1.0, float(s_micro_missing))))
    else:
        s_micro = float(compute_micro(watch.ofi_daily, watch.vpin_rank, watch.vs_max))

    return {
        "S_squeeze": s_squeeze,
        "S_volume": s_volume,
        "S_chip_pr": s_chip,
        "S_trend": s_trend,
        "S_micro": s_micro,
    }


@dataclass(frozen=True)
class Phase2Result:
    score: float
    is_triggered: bool
    is_strong: bool
    signals: dict[str, float]
    signal_fired: Optional[SignalFired]
    h_signal: float
    l_signal: float
    close_signal_day: float
    note: str = ""


def _sanitize_daily_bars_for_phase2(*, etf_code: str, bars: list[Bar], signal_date: date) -> list[Bar]:
    """
    Defensive cleaning for daily OHLCV bars.

    Why: If the upstream daily data feed returns placeholder rows (e.g. volume=0 for long spans),
    Phase2 moving-window signals (especially volume break) can be distorted and trigger late/incorrect entries.

    In that situation we prefer to drop bad bars and treat Phase2 as "insufficient data" rather than trade.
    """
    if not bars:
        degrade_once(
            f"phase2_daily_bars_empty:{str(etf_code)}:{signal_date.strftime('%Y%m%d')}",
            f"Phase2 received empty daily bars; etf={etf_code} signal_date={signal_date.isoformat()}",
            logger_name="entry.phase2",
        )
        return []

    # Sort, then de-dup by trading date (keep the latest bar for that date).
    by_day: dict[date, Bar] = {}
    for b in sorted(bars, key=lambda x: x.time):
        by_day[b.time.date()] = b

    cleaned: list[Bar] = []
    dropped = 0
    for d in sorted(by_day.keys()):
        b = by_day[d]
        o = float(b.open)
        h = float(b.high)
        l = float(b.low)
        c = float(b.close)
        v = float(b.volume)
        if c <= 0 or v <= 0:
            dropped += 1
            continue
        if o <= 0 or h <= 0 or l <= 0:
            dropped += 1
            continue
        if h + 1e-9 < l:
            dropped += 1
            continue
        if h + 1e-9 < max(o, c) or l - 1e-9 > min(o, c):
            dropped += 1
            continue
        cleaned.append(b)

    if dropped or len(cleaned) < 30:
        degrade_once(
            f"phase2_daily_bars_sanitized:{str(etf_code)}:{signal_date.strftime('%Y%m%d')}",
            (
                "Phase2 daily bars unhealthy; "
                f"etf={etf_code} signal_date={signal_date.isoformat()} input={len(by_day)} dropped={dropped} kept={len(cleaned)} required>=30"
            ),
            logger_name="entry.phase2",
        )
    return cleaned


def _window_max_high(*, bars: list[Bar], lookback: int) -> float:
    if len(bars) <= 1:
        return 0.0
    n = int(max(1, int(lookback)))
    start = max(0, len(bars) - 1 - n)
    prev = bars[start:-1]
    if not prev:
        return 0.0
    return float(max(float(b.high) for b in prev))


def _window_max_close(*, bars: list[Bar], lookback: int) -> float:
    if len(bars) <= 1:
        return 0.0
    n = int(max(1, int(lookback)))
    start = max(0, len(bars) - 1 - n)
    prev = bars[start:-1]
    if not prev:
        return 0.0
    return float(max(float(b.close) for b in prev))


def _ema_values(*, bars: list[Bar], span: int) -> list[float]:
    if not bars:
        return []
    n = int(max(1, int(span)))
    alpha = 2.0 / float(n + 1)
    vals: list[float] = []
    prev: float | None = None
    for b in bars:
        close_px = float(b.close)
        prev = close_px if prev is None else float(alpha * close_px + (1.0 - alpha) * prev)
        vals.append(float(prev))
    return vals


def _atr_values(*, bars: list[Bar], period: int) -> list[float]:
    if not bars:
        return []
    n = int(max(1, int(period)))
    trs: list[float] = []
    for idx, bar in enumerate(bars):
        prev_close = float(bars[idx - 1].close) if idx > 0 else float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(float(tr))

    out: list[float] = []
    window: list[float] = []
    total = 0.0
    for tr in trs:
        window.append(float(tr))
        total += float(tr)
        if len(window) > n:
            total -= float(window.pop(0))
        out.append(float(total / len(window)))
    return out


def _should_block_mature_continuation(*, bars: list[Bar]) -> tuple[bool, str]:
    cfg = get_phase2_continuation_config()
    if not bool(cfg.get("mature_block_enabled", False)):
        return False, ""
    if len(bars) < 20:
        return False, ""

    ema_vals = _ema_values(bars=bars, span=10)
    atr_vals = _atr_values(bars=bars, period=20)
    if len(ema_vals) != len(bars) or len(atr_vals) != len(bars):
        return False, ""

    last_idx = len(bars) - 1
    atr_last = float(atr_vals[last_idx])
    if atr_last <= 0.0:
        return False, ""

    leg_days = 0
    idx = last_idx
    while idx >= 1:
        close_px = float(bars[idx].close)
        ema_now = float(ema_vals[idx])
        ema_prev = float(ema_vals[idx - 1])
        if close_px > ema_now and ema_now > ema_prev:
            leg_days += 1
            idx -= 1
            continue
        break

    leg_days_min = int(cfg.get("mature_leg_days", 5) or 5)
    if leg_days < leg_days_min:
        return False, ""

    bias_atr = (float(bars[last_idx].close) - float(ema_vals[last_idx])) / atr_last
    bias_atr_min = float(cfg.get("mature_bias_atr", 2.0) or 2.0)
    if float(bias_atr) + 1e-12 < float(bias_atr_min):
        return False, ""

    high_window = bars[max(0, len(bars) - 20) :]
    hh20 = max(float(b.high) for b in high_window)
    near_high_atr = (float(hh20) - float(bars[last_idx].close)) / atr_last
    near_high_atr_max = float(cfg.get("mature_near_high_atr", 0.5) or 0.5)
    if float(near_high_atr) - 1e-12 > float(near_high_atr_max):
        return False, ""

    leg_start = last_idx - leg_days + 1
    recent_pullback_lookback = int(cfg.get("mature_pullback_lookback", 4) or 4)
    recent_start = max(int(leg_start), int(last_idx - recent_pullback_lookback + 1))
    pullback_biases = [
        (float(bars[i].low) - float(ema_vals[i])) / float(atr_vals[i])
        for i in range(recent_start, last_idx + 1)
        if float(atr_vals[i]) > 0.0
    ]
    if not pullback_biases:
        return False, ""
    min_pullback_bias = min(float(x) for x in pullback_biases)
    min_pullback_bias_floor = float(cfg.get("mature_min_pullback_bias", 0.2) or 0.2)
    if float(min_pullback_bias) <= float(min_pullback_bias_floor) + 1e-12:
        return False, ""

    return True, (
        "continuation_blocked "
        f"mature_leg>={leg_days_min} bias_atr>={bias_atr_min:.2f} near_high_atr<={near_high_atr_max:.2f} "
        f"recent_pullback[{recent_pullback_lookback}]>{min_pullback_bias_floor:.2f} "
        f"observed=({int(leg_days)},{float(bias_atr):.2f},{float(near_high_atr):.2f},{float(min_pullback_bias):.2f})"
    )


def _should_fire_continuation_entry(
    *,
    bars: list[Bar],
    signals: dict[str, float],
    continuation_cfg: Optional[Mapping[str, int | float | bool]] = None,
) -> tuple[bool, str]:
    cfg = get_phase2_continuation_config() if continuation_cfg is None else dict(continuation_cfg)
    if not bool(cfg.get("enabled", False)):
        return False, ""
    if len(bars) < 30:
        return False, ""
    if float(signals.get("S_squeeze", 0.0) or 0.0) > 0.0:
        return False, ""
    if float(signals.get("S_volume", 0.0) or 0.0) > 0.0:
        return False, ""
    if float(signals.get("S_trend", 0.0) or 0.0) < 1.0:
        return False, ""

    chip_min = float(cfg.get("chip_min", 0.60) or 0.60)
    micro_min = float(cfg.get("micro_min", 0.40) or 0.40)
    lookback = int(cfg.get("lookback", 10) or 10)
    min_close_breakout_pct = float(cfg.get("min_close_breakout_pct", 0.0) or 0.0)

    if float(signals.get("S_chip_pr", 0.0) or 0.0) + 1e-12 < float(chip_min):
        return False, ""
    if float(signals.get("S_micro", 0.0) or 0.0) + 1e-12 < float(micro_min):
        return False, ""

    last = bars[-1]
    prev_high = _window_max_high(bars=bars, lookback=lookback)
    prev_close = _window_max_close(bars=bars, lookback=3)
    if prev_high <= 0.0:
        return False, ""
    if float(last.high) + 1e-12 < float(prev_high):
        return False, ""
    min_close_breakout = float(prev_high) * (1.0 + float(min_close_breakout_pct))
    if float(last.close) + 1e-12 < float(min_close_breakout):
        return False, ""
    if float(last.close) <= float(last.open):
        return False, ""
    if prev_close > 0.0 and float(last.close) + 1e-12 < float(prev_close):
        return False, ""

    blocked, block_note = _should_block_mature_continuation(bars=bars)
    if blocked:
        return False, block_note

    return True, (
        "continuation_entry "
        f"chip>={chip_min:.2f} micro>={micro_min:.2f} lookback={lookback} "
        f"close_breakout>={min_close_breakout_pct:.3f}"
    )


def evaluate_phase2(
    *,
    etf_code: str,
    bars: list[Bar],
    watch: WatchlistItem,
    signal_date: date,
    s_micro_missing: Optional[float] = None,
    score_threshold: Optional[float] = None,
    continuation_cfg: Optional[Mapping[str, int | float | bool]] = None,
) -> Phase2Result:
    bars2 = _sanitize_daily_bars_for_phase2(etf_code=str(etf_code), bars=list(bars), signal_date=signal_date)
    signals = compute_phase2_signals(bars=bars2, watch=watch, s_micro_missing=s_micro_missing)
    score, is_triggered, is_strong = compute_entry_score(signals, score_threshold=score_threshold)
    continuation_cfg_local = get_phase2_continuation_config() if continuation_cfg is None else dict(continuation_cfg)
    last = bars2[-1] if bars2 else None
    h_signal = float(last.high) if last is not None else 0.0
    l_signal = float(last.low) if last is not None else 0.0
    close_t = float(last.close) if last is not None else 0.0
    note = ""
    continuation_triggered = False
    if not is_triggered:
        continuation_triggered, note = _should_fire_continuation_entry(
            bars=bars2,
            signals=signals,
            continuation_cfg=continuation_cfg_local,
        )
    if not is_triggered and not continuation_triggered:
        return Phase2Result(
            score=score,
            is_triggered=False,
            is_strong=bool(score >= STRONG_SIGNAL_THRESHOLD),
            signals=signals,
            signal_fired=None,
            h_signal=h_signal,
            l_signal=l_signal,
            close_signal_day=close_t,
            note=str(note or ""),
        )

    atr_20 = compute_atr_20(bars2)

    d0 = signal_date.strftime("%Y%m%d")
    expire_n = int(continuation_cfg_local.get("expire_days", 1) or 1) if continuation_triggered else (2 if is_strong else 3)
    expire = next_trading_day(d0, expire_n)
    expire_date = date(int(expire[:4]), int(expire[4:6]), int(expire[6:8]))

    fired = SignalFired(
        etf_code=etf_code,
        score=score,
        is_strong=is_strong,
        h_signal=h_signal,
        l_signal=l_signal,
        close_signal_day=close_t,
        atr_20=atr_20,
        signal_date=signal_date,
        expire_date=expire_date,
        signals=signals,
        watchlist=watch,
    )
    return Phase2Result(
        score=score,
        is_triggered=True,
        is_strong=is_strong,
        signals=signals,
        signal_fired=fired,
        h_signal=h_signal,
        l_signal=l_signal,
        close_signal_day=close_t,
        note=note,
    )
