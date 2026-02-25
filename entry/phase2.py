from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from core.interfaces import Bar
from core.time_utils import next_trading_day

from .constants import STRONG_SIGNAL_THRESHOLD
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


def compute_phase2_signals(*, bars: list[Bar], watch: WatchlistItem) -> dict[str, float]:
    s_squeeze = float(compute_squeeze(bars))
    s_trend = float(compute_trend(bars))
    s_chip = float(compute_chip_strength(watch.profit_ratio))
    s_volume = float(compute_volume_break(bars, watch.nearest_resistance))

    if watch.ofi_daily is None or watch.vpin_rank is None or watch.vs_max is None:
        s_micro = 0.0
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


def evaluate_phase2(*, etf_code: str, bars: list[Bar], watch: WatchlistItem, signal_date: date) -> Phase2Result:
    signals = compute_phase2_signals(bars=bars, watch=watch)
    score, is_triggered, is_strong = compute_entry_score(signals)
    last = bars[-1] if bars else None
    h_signal = float(last.high) if last is not None else 0.0
    l_signal = float(last.low) if last is not None else 0.0
    close_t = float(last.close) if last is not None else 0.0
    if not is_triggered:
        return Phase2Result(
            score=score,
            is_triggered=False,
            is_strong=bool(score >= STRONG_SIGNAL_THRESHOLD),
            signals=signals,
            signal_fired=None,
            h_signal=h_signal,
            l_signal=l_signal,
            close_signal_day=close_t,
        )

    atr_20 = compute_atr_20(bars)

    d0 = signal_date.strftime("%Y%m%d")
    expire = next_trading_day(d0, 2 if is_strong else 3)
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
    )
