from __future__ import annotations

from datetime import datetime

from core.time_utils import add_trading_minutes, set_trading_calendar_provider, trading_minutes_between


def test_trading_minutes_excludes_lunch() -> None:
    set_trading_calendar_provider(lambda s, e: [s] if s == e else [s, e])
    start = datetime(2026, 2, 23, 11, 20, 0)
    end = datetime(2026, 2, 23, 13, 15, 0)
    assert trading_minutes_between(start, end) == 25


def test_add_trading_minutes_skips_lunch() -> None:
    set_trading_calendar_provider(lambda s, e: [s] if s == e else [s, e])
    start = datetime(2026, 2, 23, 11, 20, 0)
    out = add_trading_minutes(start, 30)
    assert out == datetime(2026, 2, 23, 13, 20, 0)


def test_add_trading_minutes_preserves_seconds() -> None:
    set_trading_calendar_provider(lambda s, e: [s] if s == e else [s, e])
    start = datetime(2026, 2, 23, 9, 30, 30)
    out = add_trading_minutes(start, 1)
    assert out == datetime(2026, 2, 23, 9, 31, 30)
