from __future__ import annotations

from datetime import date, datetime, time, timedelta

import entry.phase2 as phase2_mod
from core.interfaces import Bar
from entry.phase2 import evaluate_phase2
from entry.phase2_config import set_phase2_continuation_config
from entry.types import WatchlistItem


def _dbar(d: date, *, o: float, h: float, l: float, c: float, v: float = 1000.0) -> Bar:
    return Bar(
        time=datetime.combine(d, time(15, 0)),
        open=float(o),
        high=float(h),
        low=float(l),
        close=float(c),
        volume=float(v),
        amount=float(v) * float(c),
    )


def _build_continuation_bars() -> list[Bar]:
    start = date(2025, 1, 1)
    bars: list[Bar] = []
    price = 1.00
    for idx in range(34):
        close_px = price + 0.004 * idx
        bars.append(
            _dbar(
                start + timedelta(days=idx),
                o=close_px - 0.004,
                h=close_px + 0.005,
                l=close_px - 0.008,
                c=close_px,
            )
        )
    last_close = float(bars[-1].close)
    bars.append(
        _dbar(
            start + timedelta(days=34),
            o=last_close + 0.001,
            h=last_close + 0.020,
            l=last_close - 0.002,
            c=last_close + 0.010,
        )
    )
    return bars


def _build_weak_breakout_bars() -> list[Bar]:
    bars = _build_continuation_bars()
    prev_high = max(float(b.high) for b in bars[-11:-1])
    prev_close3 = max(float(b.close) for b in bars[-4:-1])
    weak_close = max(prev_close3 + 0.001, prev_high * 1.001)
    bars[-1] = _dbar(
        bars[-1].time.date(),
        o=weak_close - 0.001,
        h=prev_high + 0.003,
        l=weak_close - 0.010,
        c=weak_close,
    )
    return bars


def _build_mature_continuation_bars(*, with_pullback: bool = False) -> list[Bar]:
    start = date(2025, 2, 1)
    bars: list[Bar] = []
    for idx in range(25):
        close_px = 1.000 + 0.001 * float(idx % 2)
        bars.append(
            _dbar(
                start + timedelta(days=idx),
                o=close_px - 0.001,
                h=close_px + 0.003,
                l=close_px - 0.003,
                c=close_px,
            )
        )

    for step in range(9):
        close_px = 1.020 + 0.020 * float(step)
        low_px = close_px - 0.004
        if with_pullback and step == 7:
            low_px = close_px - 0.180
        bars.append(
            _dbar(
                start + timedelta(days=25 + step),
                o=close_px - 0.003,
                h=close_px + 0.008,
                l=low_px,
                c=close_px,
            )
        )

    last_close = float(bars[-1].close)
    bars.append(
        _dbar(
            start + timedelta(days=34),
            o=last_close + 0.004,
            h=last_close + 0.030,
            l=last_close + 0.002,
            c=last_close + 0.022,
        )
    )
    return bars


def _set_continuation_config(
    *,
    enabled: bool,
    min_close_breakout_pct: float = 0.0,
    mature_block_enabled: bool = False,
) -> None:
    set_phase2_continuation_config(
        enabled=enabled,
        chip_min=0.6,
        micro_min=0.4,
        lookback=10,
        min_close_breakout_pct=float(min_close_breakout_pct),
        mature_block_enabled=bool(mature_block_enabled),
        mature_leg_days=5,
        mature_bias_atr=2.0,
        mature_near_high_atr=0.5,
        mature_pullback_lookback=4,
        mature_min_pullback_bias=0.2,
    )


def test_phase2_continuation_disabled_by_default(monkeypatch) -> None:
    bars = _build_continuation_bars()
    watch = WatchlistItem(etf_code="159811.SZ", sentiment_score=70, profit_ratio=82.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.6)
    monkeypatch.setattr(
        phase2_mod,
        "compute_phase2_signals",
        lambda **kwargs: {"S_squeeze": 0.0, "S_volume": 0.0, "S_chip_pr": 0.6, "S_trend": 1.0, "S_micro": 0.4},
    )

    _set_continuation_config(enabled=False)
    try:
        res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    finally:
        _set_continuation_config(enabled=False)

    assert res.score == 0.31
    assert res.is_triggered is False
    assert res.signal_fired is None


def test_phase2_continuation_triggers_without_squeeze(monkeypatch) -> None:
    bars = _build_continuation_bars()
    watch = WatchlistItem(etf_code="159811.SZ", sentiment_score=70, profit_ratio=82.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.6)
    monkeypatch.setattr(
        phase2_mod,
        "compute_phase2_signals",
        lambda **kwargs: {"S_squeeze": 0.0, "S_volume": 0.0, "S_chip_pr": 0.6, "S_trend": 1.0, "S_micro": 0.4},
    )
    monkeypatch.setattr(phase2_mod, "next_trading_day", lambda current_date, n=1: current_date)

    _set_continuation_config(enabled=True)
    try:
        res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    finally:
        _set_continuation_config(enabled=False)

    assert res.score == 0.31
    assert res.is_triggered is True
    assert res.is_strong is False
    assert res.signal_fired is not None
    assert res.note.startswith("continuation_entry")
    assert res.signal_fired.expire_date == bars[-1].time.date()


def test_phase2_continuation_respects_chip_and_micro_thresholds(monkeypatch) -> None:
    bars = _build_continuation_bars()
    watch = WatchlistItem(etf_code="159811.SZ", sentiment_score=70, profit_ratio=78.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.6)
    monkeypatch.setattr(
        phase2_mod,
        "compute_phase2_signals",
        lambda **kwargs: {"S_squeeze": 0.0, "S_volume": 0.0, "S_chip_pr": 0.3, "S_trend": 1.0, "S_micro": 0.4},
    )

    _set_continuation_config(enabled=True)
    try:
        res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    finally:
        _set_continuation_config(enabled=False)

    assert res.is_triggered is False
    assert res.signal_fired is None


def test_phase2_continuation_respects_min_close_breakout_pct(monkeypatch) -> None:
    bars = _build_weak_breakout_bars()
    watch = WatchlistItem(etf_code="159732.SZ", sentiment_score=68, profit_ratio=82.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.2)
    monkeypatch.setattr(
        phase2_mod,
        "compute_phase2_signals",
        lambda **kwargs: {"S_squeeze": 0.0, "S_volume": 0.0, "S_chip_pr": 0.85, "S_trend": 1.0, "S_micro": 0.4},
    )

    _set_continuation_config(enabled=True, min_close_breakout_pct=0.003)
    try:
        res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    finally:
        _set_continuation_config(enabled=False)

    assert res.is_triggered is False
    assert res.signal_fired is None


def test_phase2_continuation_allows_strong_close_breakout(monkeypatch) -> None:
    bars = _build_continuation_bars()
    watch = WatchlistItem(etf_code="515880.SH", sentiment_score=76, profit_ratio=82.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.6)
    monkeypatch.setattr(
        phase2_mod,
        "compute_phase2_signals",
        lambda **kwargs: {"S_squeeze": 0.0, "S_volume": 0.0, "S_chip_pr": 0.6, "S_trend": 1.0, "S_micro": 0.4},
    )
    monkeypatch.setattr(phase2_mod, "next_trading_day", lambda current_date, n=1: current_date)

    _set_continuation_config(enabled=True, min_close_breakout_pct=0.003)
    try:
        res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    finally:
        _set_continuation_config(enabled=False)

    assert res.is_triggered is True
    assert res.signal_fired is not None
    assert "close_breakout>=0.003" in res.note


def test_phase2_continuation_mature_block_skips_late_extended_entry(monkeypatch) -> None:
    bars = _build_mature_continuation_bars()
    watch = WatchlistItem(etf_code="515250.SH", sentiment_score=68, profit_ratio=82.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.6)
    monkeypatch.setattr(
        phase2_mod,
        "compute_phase2_signals",
        lambda **kwargs: {"S_squeeze": 0.0, "S_volume": 0.0, "S_chip_pr": 0.6, "S_trend": 1.0, "S_micro": 0.4},
    )
    monkeypatch.setattr(phase2_mod, "next_trading_day", lambda current_date, n=1: current_date)

    _set_continuation_config(enabled=True, mature_block_enabled=True)
    try:
        res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    finally:
        _set_continuation_config(enabled=False)

    assert res.is_triggered is False
    assert res.signal_fired is None
    assert res.note.startswith("continuation_blocked")


def test_phase2_continuation_mature_block_allows_pullback_reset(monkeypatch) -> None:
    bars = _build_mature_continuation_bars(with_pullback=True)
    watch = WatchlistItem(etf_code="515250.SH", sentiment_score=68, profit_ratio=82.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.6)
    monkeypatch.setattr(
        phase2_mod,
        "compute_phase2_signals",
        lambda **kwargs: {"S_squeeze": 0.0, "S_volume": 0.0, "S_chip_pr": 0.6, "S_trend": 1.0, "S_micro": 0.4},
    )
    monkeypatch.setattr(phase2_mod, "next_trading_day", lambda current_date, n=1: current_date)

    _set_continuation_config(enabled=True, mature_block_enabled=True)
    try:
        res = evaluate_phase2(etf_code=watch.etf_code, bars=bars, watch=watch, signal_date=bars[-1].time.date(), s_micro_missing=0.1)
    finally:
        _set_continuation_config(enabled=False)

    assert res.is_triggered is True
    assert res.signal_fired is not None
    assert res.note.startswith("continuation_entry")
