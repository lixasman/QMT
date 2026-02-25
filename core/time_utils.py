from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from .constants import TRADING_SESSIONS


try:
    from xtquant import xtdata as _xtdata  # type: ignore
except Exception:
    _xtdata = None


TradingCalendarProvider = Callable[[str, str], list[str]]


def _default_calendar_provider(start_yyyymmdd: str, end_yyyymmdd: str) -> list[str]:
    if _xtdata is not None:
        dates = _xtdata.get_trading_dates("SH", start_yyyymmdd, end_yyyymmdd, -1)
        out: list[str] = []
        for d in dates:
            if isinstance(d, int):
                if d >= 10_000_000_000:
                    import time as _time

                    tm = _time.localtime(float(d) / 1000.0)
                    out.append(_time.strftime("%Y%m%d", tm))
                else:
                    out.append(str(d))
            else:
                s = str(d)
                out.append(s[:8])
        return [x for x in out if len(x) == 8 and x.isdigit()]

    s = datetime.strptime(start_yyyymmdd, "%Y%m%d").date()
    e = datetime.strptime(end_yyyymmdd, "%Y%m%d").date()
    out2: list[str] = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            out2.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out2


_calendar_provider: TradingCalendarProvider = _default_calendar_provider


def set_trading_calendar_provider(provider: TradingCalendarProvider) -> None:
    global _calendar_provider
    _calendar_provider = provider


def get_trading_dates(start: str, end: str) -> list[str]:
    return _calendar_provider(start, end)


def get_trading_dates_strict(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    out: list[str] = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def next_trading_day(current_date: str, n: int = 1) -> str:
    if n <= 0:
        return current_date
    start = datetime.strptime(current_date, "%Y%m%d").date()
    end = start + timedelta(days=max(10, n * 10))
    cal = get_trading_dates(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    idx = -1
    for i, d in enumerate(cal):
        if d == current_date:
            idx = i
            break
    if idx < 0:
        raise RuntimeError(f"trading date not in calendar: {current_date}")
    j = idx + n
    if j >= len(cal):
        raise RuntimeError(f"calendar too short for next trading day: {current_date} +{n}")
    return cal[j]


def is_trading_day(d: date) -> bool:
    cal = get_trading_dates(d.strftime("%Y%m%d"), d.strftime("%Y%m%d"))
    return bool(cal and cal[0] == d.strftime("%Y%m%d"))


def is_trading_time(dt: datetime) -> bool:
    if not is_trading_day(dt.date()):
        return False
    t = dt.time()
    for a, b in TRADING_SESSIONS:
        if a <= t < b:
            return True
    return False


@dataclass(frozen=True)
class _Range:
    start: datetime
    end: datetime


def _overlap_seconds(a: _Range, b: _Range) -> float:
    s = max(a.start, b.start)
    e = min(a.end, b.end)
    if e <= s:
        return 0.0
    return (e - s).total_seconds()


def _session_ranges_for_date(d: date) -> list[_Range]:
    out: list[_Range] = []
    for a, b in TRADING_SESSIONS:
        out.append(_Range(datetime.combine(d, a), datetime.combine(d, b)))
    return out


def trading_minutes_between(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    d0 = start.date()
    d1 = end.date()
    cal = get_trading_dates(d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d"))
    if not cal:
        return 0
    total = 0.0
    full = _Range(start, end)
    for ds in cal:
        d = datetime.strptime(ds, "%Y%m%d").date()
        for sess in _session_ranges_for_date(d):
            total += _overlap_seconds(full, sess)
    return int(total // 60)


def add_trading_minutes(start: datetime, minutes: int) -> datetime:
    if minutes <= 0:
        return start
    cur = start
    remain_sec = float(int(minutes)) * 60.0
    while remain_sec > 0:
        if not is_trading_day(cur.date()):
            nd = next_trading_day(cur.date().strftime("%Y%m%d"), 1)
            cur = datetime.combine(datetime.strptime(nd, "%Y%m%d").date(), TRADING_SESSIONS[0][0])
            continue
        tcur = cur.time()
        moved = False
        for a, b in TRADING_SESSIONS:
            if tcur < a:
                cur = datetime.combine(cur.date(), a)
                moved = True
                break
            if a <= tcur < b:
                sec_left = (datetime.combine(cur.date(), b) - cur).total_seconds()
                if sec_left <= 0:
                    break
                can_take = min(remain_sec, sec_left)
                cur = cur + timedelta(seconds=can_take)
                remain_sec -= can_take
                moved = True
                break
        if moved:
            continue
        nd = next_trading_day(cur.date().strftime("%Y%m%d"), 1)
        cur = datetime.combine(datetime.strptime(nd, "%Y%m%d").date(), TRADING_SESSIONS[0][0])
    return cur
