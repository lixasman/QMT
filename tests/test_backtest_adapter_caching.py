from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backtest.adapters import BacktestDataAdapter
from backtest.clock import SimulatedClock
from core.enums import DataQuality
from core.interfaces import Bar


@dataclass(frozen=True)
class _FakeTickPoint:
    time: datetime
    last_price: float
    volume: int
    amount: float
    bid1_price: float
    ask1_price: float
    bid1_vol: int
    ask1_vol: int
    iopv: float | None = None
    stock_status: int = 0


class _FakeStore:
    def __init__(self) -> None:
        self.tick_calls = 0
        self.daily_calls = 0
        self.mark_price_calls = 0

    def tick_snapshot(self, *, code: str, now: datetime):
        self.tick_calls += 1
        pt = _FakeTickPoint(
            time=now,
            last_price=1.23,
            volume=100,
            amount=12300.0,
            bid1_price=1.229,
            ask1_price=1.231,
            bid1_vol=10,
            ask1_vol=12,
        )
        return pt, 100, 12300.0

    def mark_price(self, *, code: str, now: datetime, prefer_tick: bool = True) -> float:
        self.mark_price_calls += 1
        return 1.23

    def daily_bars(self, *, code: str, now: datetime, count: int, include_today: bool):
        self.daily_calls += 1
        bars = [
            Bar(
                time=datetime(2026, 1, 1, 15, 0, 0),
                open=1.0,
                high=1.3,
                low=0.9,
                close=1.2,
                volume=1000.0,
                amount=1200.0,
            )
            for _ in range(80)
        ]
        if int(count) <= 0:
            return bars
        return bars[-int(count) :]

    def minute_bars(self, *, code: str, now: datetime, count: int):
        return []


def test_get_snapshot_caches_same_code_within_same_tick() -> None:
    clock = SimulatedClock(datetime(2026, 3, 6, 10, 0, 0))
    store = _FakeStore()
    adapter = BacktestDataAdapter(store=store, clock=clock)

    snap1 = adapter.get_snapshot("512480.SH")
    snap2 = adapter.get_snapshot("512480.SH")

    assert snap1.data_quality == DataQuality.OK
    assert snap2.data_quality == DataQuality.OK
    assert store.tick_calls == 1


def test_get_snapshot_invalidates_cache_when_time_changes() -> None:
    clock = SimulatedClock(datetime(2026, 3, 6, 10, 0, 0))
    store = _FakeStore()
    adapter = BacktestDataAdapter(store=store, clock=clock)

    _ = adapter.get_snapshot("512480.SH")
    clock.sleep(3.0)
    _ = adapter.get_snapshot("512480.SH")

    assert store.tick_calls == 2


def test_get_bars_caches_daily_bars_within_same_day_and_include_today_flag() -> None:
    clock = SimulatedClock(datetime(2026, 3, 6, 10, 0, 0))
    store = _FakeStore()
    adapter = BacktestDataAdapter(store=store, clock=clock)

    _ = adapter.get_bars("512480.SH", "1d", 40)
    _ = adapter.get_bars("512480.SH", "1d", 40)
    assert store.daily_calls == 1

    clock.reset(datetime(2026, 3, 6, 15, 1, 0))
    _ = adapter.get_bars("512480.SH", "1d", 40)
    assert store.daily_calls == 2

    clock.reset(datetime(2026, 3, 7, 10, 0, 0))
    _ = adapter.get_bars("512480.SH", "1d", 40)
    assert store.daily_calls == 3


def test_get_bars_reuses_full_daily_history_across_different_counts_same_day() -> None:
    clock = SimulatedClock(datetime(2026, 3, 6, 10, 0, 0))
    store = _FakeStore()
    adapter = BacktestDataAdapter(store=store, clock=clock)

    bars_40 = adapter.get_bars("512480.SH", "1d", 40)
    bars_60 = adapter.get_bars("512480.SH", "1d", 60)

    assert len(bars_40) == 40
    assert len(bars_60) == 60
    assert store.daily_calls == 1
