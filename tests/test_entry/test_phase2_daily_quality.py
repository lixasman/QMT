from __future__ import annotations

from datetime import date, datetime, time

from core.interfaces import Bar
from entry.phase2 import evaluate_phase2
from entry.types import WatchlistItem


def _dbar(d: date, *, o: float, h: float, l: float, c: float, v: float, a: float) -> Bar:
    # Phase2 treats bars as daily; time-of-day doesn't matter much, but keep 15:00 convention.
    return Bar(time=datetime.combine(d, time(15, 0)), open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v), amount=float(a))


def test_phase2_sanitizes_invalid_daily_bars_prevent_false_volume_break() -> None:
    """
    Guardrail: if daily bars contain long spans of placeholder rows (e.g. volume=0),
    volume_break can be distorted and incorrectly trigger entries.

    We drop invalid daily bars in evaluate_phase2 to avoid trading on corrupted history.
    """
    # 21 bars total so volume_break has enough lookback *if* invalid bars were kept.
    # Construct: last-20 includes one real bar (v=100) + 19 placeholder bars (v=0),
    # and the current day has a huge volume spike and price break.
    d0 = date(2025, 1, 1)
    bars: list[Bar] = []

    # One valid baseline day (kept)
    bars.append(_dbar(d0, o=1.00, h=1.10, l=0.95, c=1.05, v=100.0, a=1000.0))

    # 19 placeholder days (should be dropped)
    for i in range(1, 20):
        bars.append(_dbar(date(2025, 1, 1 + i), o=1.30, h=1.30, l=1.30, c=1.30, v=0.0, a=0.0))

    # Current day (kept) would satisfy price_break + volume_confirm + solid_candle if placeholders were included.
    bars.append(_dbar(date(2025, 1, 21), o=1.00, h=2.00, l=0.90, c=2.00, v=1000.0, a=20000.0))

    watch = WatchlistItem(
        etf_code="159566.SZ",
        sentiment_score=100,
        profit_ratio=90.0,  # -> S_chip_pr=1.0
        nearest_resistance=1.00,  # fixed resistance so price_break is deterministic
        ofi_daily=1.0,
        vpin_rank=0.5,
        vs_max=2.0,
    )

    res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    assert res.signals["S_volume"] == 0.0
    assert res.is_triggered is False

