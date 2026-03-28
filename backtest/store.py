from __future__ import annotations

import bisect
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

from core.interfaces import Bar
from .corporate_actions import CorporateActionEvent, cumulative_price_factor, infer_split_events_from_daily_bars, rescale_bar
from .fail_fast_warn import degrade_once, warn_once


_AM_START = time(9, 30)
_AM_END = time(11, 30)
_PM_START = time(13, 0)
_PM_END = time(15, 0)
logger = logging.getLogger("backtest.store")


def _is_intraday_session(t: time) -> bool:
    return (_AM_START <= t < _AM_END) or (_PM_START <= t < _PM_END)


def _to_float(v: object) -> float:
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _to_optional_float(v: object) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        x = float(s)
    except Exception:
        return None
    if x <= 0:
        return None
    return float(x)


def _is_non_decreasing(values: list[float], *, eps: float = 1e-9) -> bool:
    if len(values) <= 1:
        return True
    prev = float(values[0])
    for x in values[1:]:
        cur = float(x)
        if cur + float(eps) < prev:
            return False
        prev = float(cur)
    return True


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _parse_datetime(raw: str, *, daily: bool) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    # xtdata exports often write epoch as float-like strings, e.g. "1740720720000.0"
    if not s.isdigit():
        try:
            fv = float(s)
            if fv.is_integer():
                s = str(int(fv))
        except Exception:
            pass
    if s.isdigit():
        if len(s) == 8:
            try:
                d = datetime.strptime(s, "%Y%m%d")
                if daily:
                    return datetime.combine(d.date(), time(15, 0))
                return d
            except Exception:
                return None
        if len(s) == 14:
            try:
                return datetime.strptime(s, "%Y%m%d%H%M%S")
            except Exception:
                return None
        if len(s) >= 13:
            try:
                return datetime.fromtimestamp(float(s) / 1000.0)
            except Exception:
                return None
        if len(s) == 10:
            try:
                return datetime.fromtimestamp(float(s))
            except Exception:
                return None

    s2 = s.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s2, fmt)
            if fmt == "%Y-%m-%d" and daily:
                return datetime.combine(d.date(), time(15, 0))
            return d
        except Exception:
            continue
    try:
        d2 = datetime.fromisoformat(s2.replace("Z", "+00:00"))
        if d2.tzinfo is not None:
            d2 = d2.astimezone().replace(tzinfo=None)
        if daily and d2.time() == time(0, 0):
            return datetime.combine(d2.date(), time(15, 0))
        return d2
    except Exception:
        return None


def _normalize_code(code: str) -> str:
    return str(code or "").strip().upper()


def _parse_day_yyyymmdd(v: str) -> date:
    s = str(v or "").strip()
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_day_dir_name(name: str) -> Optional[date]:
    s = str(name or "").strip()
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except Exception:
            return None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None
    return None


@dataclass(frozen=True)
class _MinuteDayCache:
    bars: list[Bar]
    times: list[datetime]
    cum_volume: list[int]
    cum_amount: list[float]


@dataclass(frozen=True)
class _TickPoint:
    time: datetime
    last_price: float
    volume: float
    amount: float
    bid1_price: float
    bid1_vol: int
    ask1_price: float
    ask1_vol: int
    iopv: Optional[float]
    stock_status: int


@dataclass(frozen=True)
class _TickDayCache:
    points: list[_TickPoint]
    times: list[datetime]
    cum_volume: list[int]
    cum_amount: list[float]


class MarketDataStore:
    def __init__(
        self,
        *,
        data_root: str | Path,
        codes: list[str],
        tick_root: str | Path | None = None,
        load_minute: bool = False,
    ) -> None:
        self._root = Path(data_root)
        self._codes = [_normalize_code(x) for x in codes if _normalize_code(x)]
        self._tick_root = Path(tick_root) if tick_root is not None else (self._root / "tick")
        self._load_minute = bool(load_minute)
        self._daily: dict[str, list[Bar]] = {}
        self._corp_actions: dict[str, list[CorporateActionEvent]] = {}
        self._minute: dict[str, dict[date, _MinuteDayCache]] = {}
        self._tick: dict[str, dict[date, _TickDayCache]] = {}
        self._tick_mode = "none"  # none | by_code | by_day
        self._active_tick_day: Optional[date] = None
        self._tick_day_cache: dict[str, _TickDayCache] = {}
        self._tick_day_missing: set[str] = set()
        self._trade_days: set[date] = set()
        self._sorted_trade_days: Optional[list[date]] = None
        self._load_all()

    @property
    def codes(self) -> list[str]:
        return list(self._codes)

    def _iter_tick_day_dirs(self):
        try:
            for p in self._tick_root.iterdir():
                if not p.is_dir():
                    continue
                if _parse_day_dir_name(p.name) is not None:
                    yield p
                    continue
                if len(p.name) == 4 and str(p.name).isdigit():
                    for month_dir in p.iterdir():
                        if not month_dir.is_dir():
                            continue
                        for day_dir in month_dir.iterdir():
                            if day_dir.is_dir() and _parse_day_dir_name(day_dir.name) is not None:
                                yield day_dir
        except Exception:
            return

    def _load_all(self) -> None:
        minute_dir_exists = bool(self._load_minute and (self._root / "1m").exists())
        tick_root_exists = bool(self._tick_root.exists())
        tick_by_day = False
        tick_day_dirs: list[Path] = []
        if tick_root_exists:
            try:
                tick_day_dirs = list(self._iter_tick_day_dirs())
                tick_by_day = bool(tick_day_dirs)
            except Exception:
                tick_by_day = False
        self._tick_mode = "by_day" if tick_by_day else ("by_code" if tick_root_exists else "none")
        logger.info(
            "market store load start | root=%s codes=%s tick_root=%s tick_mode=%s load_1m=%s",
            str(self._root),
            len(self._codes),
            str(self._tick_root),
            str(self._tick_mode),
            bool(self._load_minute),
        )
        if self._tick_mode == "none":
            warn_once(
                "bt_tick_root_missing",
                f"Backtest tick root missing: {self._tick_root}",
                logger_name="backtest.store",
            )
        if self._tick_mode == "by_day":
            try:
                for p in tick_day_dirs:
                    d = _parse_day_dir_name(p.name)
                    if d is not None:
                        self._trade_days.add(d)
            except Exception:
                pass
        for code in self._codes:
            daily = self._load_period(code=code, period="1d")
            minute = self._load_period(code=code, period="1m") if minute_dir_exists else []
            ticks = self._load_tick_period(code=code) if self._tick_mode == "by_code" else []
            daily.sort(key=lambda x: x.time)
            minute.sort(key=lambda x: x.time)
            ticks.sort(key=lambda x: x.time)
            self._daily[code] = daily
            self._corp_actions[code] = infer_split_events_from_daily_bars(etf_code=code, bars=daily)
            self._minute[code] = self._build_minute_cache(minute)
            self._tick[code] = self._build_tick_cache(ticks) if self._tick_mode == "by_code" else {}
            for b in daily:
                self._trade_days.add(b.time.date())
            for b in minute:
                self._trade_days.add(b.time.date())
            if self._tick_mode == "by_code":
                for t in ticks:
                    self._trade_days.add(t.time.date())
            if not daily:
                degrade_once(
                    f"bt_no_daily:{code}",
                    f"Backtest: no 1d bars loaded for code={code}; phase2/exit daily factors may degrade.",
                    logger_name="backtest.store",
                )
            if self._tick_mode == "by_code" and not ticks:
                degrade_once(
                    f"bt_no_tick:{code}",
                    f"Backtest: no tick rows loaded for code={code}; tick snapshot actions may be blocked.",
                    logger_name="backtest.store",
                )
            if minute_dir_exists and not minute:
                warn_once(
                    f"bt_no_minute:{code}",
                    f"Backtest: no 1m bars loaded for code={code}; minute-bar queries may degrade.",
                    logger_name="backtest.store",
                )
            logger.debug("code loaded | code=%s daily=%s minute=%s tick=%s", code, len(daily), len(minute), len(ticks))
            for ev in self._corp_actions.get(code, []):
                logger.info(
                    "corporate action inferred | code=%s day=%s factor=%.6f qty_factor=%.6f ratio=%.6f source=%s",
                    str(code),
                    ev.effective_day.isoformat(),
                    float(ev.price_factor),
                    float(ev.quantity_factor),
                    float(ev.raw_ratio),
                    str(ev.source),
                )
        self._sorted_trade_days = sorted(self._trade_days)
        logger.info("market store load done | trade_days=%s", len(self._trade_days))

    def _resolve_csv_path_under(self, *, base_dir: Path, code: str) -> Optional[Path]:
        c6 = code.split(".", 1)[0]
        exch = code.split(".", 1)[1] if "." in code else ""
        candidates = [
            f"{code}.csv",
            f"{code.replace('.', '_')}.csv",
            f"{c6}.csv",
            f"{c6}_{exch}.csv" if exch else "",
            f"{c6}_{exch.lower()}.csv" if exch else "",
        ]
        for name in candidates:
            if not name:
                continue
            p = base_dir / name
            if p.exists():
                return p

        globbed = sorted(base_dir.glob(f"*{c6}*.csv"))
        if not globbed:
            return None
        if exch:
            for p in globbed:
                if exch.upper() in p.name.upper():
                    return p
        return globbed[0]

    def _resolve_tick_day_dir(self, *, day: date) -> Optional[Path]:
        if self._tick_mode != "by_day":
            return None
        candidates = [
            self._tick_root / day.strftime("%Y%m%d"),
            self._tick_root / day.strftime("%Y-%m-%d"),
            self._tick_root / day.strftime("%Y") / day.strftime("%m") / day.strftime("%Y%m%d"),
            self._tick_root / day.strftime("%Y") / day.strftime("%m") / day.strftime("%Y-%m-%d"),
        ]
        for p in candidates:
            if p.exists() and p.is_dir():
                return p
        return None

    def _load_tick_day_cache(self, *, code: str, day: date) -> Optional[_TickDayCache]:
        day_dir = self._resolve_tick_day_dir(day=day)
        if day_dir is None:
            return None
        path = self._resolve_csv_path_under(base_dir=day_dir, code=code)
        if path is None or not path.exists():
            return None

        out: list[_TickPoint] = []
        parse_fail = 0
        session_drop = 0
        non_positive_last = 0
        try:
            f = path.open("r", encoding="utf-8-sig", newline="")
        except Exception:
            f = path.open("r", newline="")
        with f:
            reader = csv.DictReader(f)
            for raw_row in reader:
                row = {str(k or "").strip().lower(): ("" if v is None else str(v).strip()) for k, v in raw_row.items()}
                ts = _pick(row, ("time", "datetime", "date", "trade_time", "dt"))
                dt = _parse_datetime(ts, daily=False)
                if dt is None:
                    parse_fail += 1
                    continue
                if not _is_intraday_session(dt.time()):
                    session_drop += 1
                    continue

                last = _to_float(_pick(row, ("lastprice", "last_price", "current", "close", "c", "price", "last")))
                if float(last) <= 0:
                    non_positive_last += 1
                    continue

                vol = _to_float(_pick(row, ("volume", "vol", "v", "pvolume")))
                amt = _to_float(_pick(row, ("amount", "amt", "money", "turnover")))
                bid1 = _to_float(_pick(row, ("bidprice1", "bid_price1", "b1_p", "bid1", "bidprice", "bp1", "buy1")))
                ask1 = _to_float(_pick(row, ("askprice1", "ask_price1", "a1_p", "ask1", "askprice", "ap1", "sell1")))
                bid1_vol = int(round(_to_float(_pick(row, ("bidvol1", "bid_vol1", "b1_v", "bid1_vol", "bidvol", "bv1")))))
                ask1_vol = int(round(_to_float(_pick(row, ("askvol1", "ask_vol1", "a1_v", "ask1_vol", "askvol", "av1")))))
                iopv = _to_optional_float(_pick(row, ("iopv", "iopv_price", "ref_iopv")))
                stock_status = int(round(_to_float(_pick(row, ("stockstatus", "stock_status", "status")))))

                out.append(
                    _TickPoint(
                        time=dt,
                        last_price=float(last),
                        volume=float(vol),
                        amount=float(amt),
                        bid1_price=float(bid1),
                        bid1_vol=max(0, int(bid1_vol)),
                        ask1_price=float(ask1),
                        ask1_vol=max(0, int(ask1_vol)),
                        iopv=iopv,
                        stock_status=int(stock_status),
                    )
                )
        if parse_fail > 0:
            warn_once(
                f"bt_tick_day_parse_fail:{code}:{day.strftime('%Y%m%d')}",
                f"Backtest: parsed day tick csv with dropped rows by time parse. code={code} dropped={parse_fail} file={path}",
                logger_name="backtest.store",
            )
        if session_drop > 0:
            warn_once(
                f"bt_tick_day_session_drop:{code}:{day.strftime('%Y%m%d')}",
                f"Backtest: day tick rows outside trading sessions dropped. code={code} dropped={session_drop} file={path}",
                logger_name="backtest.store",
            )
        if non_positive_last > 0:
            warn_once(
                f"bt_tick_day_non_positive_last:{code}:{day.strftime('%Y%m%d')}",
                f"Backtest: day tick rows with non-positive last_price dropped. code={code} dropped={non_positive_last} file={path}",
                logger_name="backtest.store",
            )
        if not out:
            return None
        cache = self._build_tick_cache(out).get(day)
        return cache

    def _resolve_csv_path(self, *, code: str, period: str) -> Optional[Path]:
        period_dir = self._root / period
        if not period_dir.exists():
            warn_once(
                f"bt_period_dir_missing:{period}",
                f"Backtest: data period directory missing: {period_dir}",
                logger_name="backtest.store",
            )
            return None
        c6 = code.split(".", 1)[0]
        exch = code.split(".", 1)[1] if "." in code else ""
        candidates = [
            f"{code}.csv",
            f"{code.replace('.', '_')}.csv",
            f"{c6}.csv",
            f"{c6}_{exch}.csv" if exch else "",
            f"{c6}_{exch.lower()}.csv" if exch else "",
        ]
        for name in candidates:
            if not name:
                continue
            p = period_dir / name
            if p.exists():
                return p

        globbed = sorted(period_dir.glob(f"*{c6}*.csv"))
        if not globbed:
            degrade_once(
                f"bt_csv_missing:{period}:{code}",
                f"Backtest: csv not found for code={code} period={period} under {period_dir}",
                logger_name="backtest.store",
            )
            return None
        if exch:
            exch_upper = exch.upper()
            for p in globbed:
                if exch_upper in p.stem.upper():
                    return p
        return globbed[0]

    def _load_period(self, *, code: str, period: str) -> list[Bar]:
        path = self._resolve_csv_path(code=code, period=period)
        if path is None or not path.exists():
            return []
        out: list[Bar] = []
        parse_fail = 0
        session_drop = 0
        non_positive_close = 0
        is_daily = str(period) == "1d"
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for raw_row in reader:
                row = {str(k or "").strip().lower(): ("" if v is None else str(v).strip()) for k, v in raw_row.items()}
                ts = _pick(row, ("time", "datetime", "date", "trade_time", "dt"))
                dt = _parse_datetime(ts, daily=is_daily)
                if dt is None:
                    parse_fail += 1
                    continue
                if not is_daily and not _is_intraday_session(dt.time()):
                    session_drop += 1
                    continue
                bar = Bar(
                    time=dt,
                    open=_to_float(_pick(row, ("open", "o"))),
                    high=_to_float(_pick(row, ("high", "h"))),
                    low=_to_float(_pick(row, ("low", "l"))),
                    close=_to_float(_pick(row, ("close", "c", "lastprice", "last_price", "price"))),
                    volume=_to_float(_pick(row, ("volume", "vol", "v"))),
                    amount=_to_float(_pick(row, ("amount", "amt", "turnover"))),
                )
                if bar.close <= 0:
                    non_positive_close += 1
                    continue
                out.append(bar)
        if parse_fail > 0:
            degrade_once(
                f"bt_parse_fail:{period}:{code}",
                f"Backtest: parsed csv with dropped rows by time parse. code={code} period={period} dropped={parse_fail} file={path}",
                logger_name="backtest.store",
            )
        if session_drop > 0 and str(period) == "1m":
            warn_once(
                f"bt_session_drop:{code}",
                f"Backtest: 1m rows outside trading sessions dropped. code={code} dropped={session_drop} file={path}",
                logger_name="backtest.store",
            )
        if non_positive_close > 0:
            warn_once(
                f"bt_non_positive_close:{period}:{code}",
                f"Backtest: rows with non-positive close dropped. code={code} period={period} dropped={non_positive_close} file={path}",
                logger_name="backtest.store",
            )
        logger.debug("period loaded | code=%s period=%s bars=%s file=%s", code, period, len(out), str(path))
        return out

    def _load_tick_period(self, *, code: str) -> list[_TickPoint]:
        period = "tick"
        path = self._resolve_csv_path(code=code, period=period)
        if path is None or not path.exists():
            return []

        out: list[_TickPoint] = []
        parse_fail = 0
        session_drop = 0
        non_positive_last = 0
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for raw_row in reader:
                row = {str(k or "").strip().lower(): ("" if v is None else str(v).strip()) for k, v in raw_row.items()}
                ts = _pick(row, ("time", "datetime", "date", "trade_time", "dt"))
                dt = _parse_datetime(ts, daily=False)
                if dt is None:
                    parse_fail += 1
                    continue
                if not _is_intraday_session(dt.time()):
                    session_drop += 1
                    continue

                last = _to_float(_pick(row, ("lastprice", "last_price", "close", "c", "price", "last")))
                if float(last) <= 0:
                    non_positive_last += 1
                    continue

                vol = _to_float(_pick(row, ("volume", "vol", "v", "pvolume")))
                amt = _to_float(_pick(row, ("amount", "amt", "turnover")))
                bid1 = _to_float(_pick(row, ("bidprice1", "bid_price1", "bid1", "bidprice", "bp1", "buy1")))
                ask1 = _to_float(_pick(row, ("askprice1", "ask_price1", "ask1", "askprice", "ap1", "sell1")))
                bid1_vol = int(round(_to_float(_pick(row, ("bidvol1", "bid_vol1", "bid1_vol", "bidvol", "bv1")))))
                ask1_vol = int(round(_to_float(_pick(row, ("askvol1", "ask_vol1", "ask1_vol", "askvol", "av1")))))
                iopv = _to_optional_float(_pick(row, ("iopv", "iopv_price", "ref_iopv")))
                stock_status = int(round(_to_float(_pick(row, ("stockstatus", "stock_status", "status")))))

                out.append(
                    _TickPoint(
                        time=dt,
                        last_price=float(last),
                        volume=float(vol),
                        amount=float(amt),
                        bid1_price=float(bid1),
                        bid1_vol=max(0, int(bid1_vol)),
                        ask1_price=float(ask1),
                        ask1_vol=max(0, int(ask1_vol)),
                        iopv=iopv,
                        stock_status=int(stock_status),
                    )
                )

        if parse_fail > 0:
            degrade_once(
                f"bt_tick_parse_fail:{code}",
                f"Backtest: parsed tick csv with dropped rows by time parse. code={code} dropped={parse_fail} file={path}",
                logger_name="backtest.store",
            )
        if session_drop > 0:
            warn_once(
                f"bt_tick_session_drop:{code}",
                f"Backtest: tick rows outside trading sessions dropped. code={code} dropped={session_drop} file={path}",
                logger_name="backtest.store",
            )
        if non_positive_last > 0:
            warn_once(
                f"bt_tick_non_positive_last:{code}",
                f"Backtest: tick rows with non-positive last_price dropped. code={code} dropped={non_positive_last} file={path}",
                logger_name="backtest.store",
            )
        logger.debug("period loaded | code=%s period=tick rows=%s file=%s", code, len(out), str(path))
        return out

    def _build_tick_cache(self, points: list[_TickPoint]) -> dict[date, _TickDayCache]:
        out: dict[date, _TickDayCache] = {}
        by_day: dict[date, list[_TickPoint]] = {}
        for p in points:
            by_day.setdefault(p.time.date(), []).append(p)
        for day, day_points in by_day.items():
            day_points.sort(key=lambda x: x.time)
            times = [p.time for p in day_points]

            raw_vol = [max(0.0, float(p.volume)) for p in day_points]
            raw_amt = [max(0.0, float(p.amount)) for p in day_points]
            vol_is_cum = _is_non_decreasing(raw_vol)
            amt_is_cum = _is_non_decreasing(raw_amt)

            cum_v: list[int] = []
            cum_a: list[float] = []
            sv = 0.0
            sa = 0.0
            for i in range(len(day_points)):
                if vol_is_cum:
                    sv = float(raw_vol[i])
                else:
                    sv += float(raw_vol[i])
                if amt_is_cum:
                    sa = float(raw_amt[i])
                else:
                    sa += float(raw_amt[i])
                iv = int(round(float(sv)))
                if cum_v and iv < cum_v[-1]:
                    iv = int(cum_v[-1])
                ia = float(sa)
                if cum_a and ia < cum_a[-1]:
                    ia = float(cum_a[-1])
                cum_v.append(int(max(0, iv)))
                cum_a.append(float(max(0.0, ia)))

            out[day] = _TickDayCache(points=day_points, times=times, cum_volume=cum_v, cum_amount=cum_a)
        return out

    def _build_minute_cache(self, bars: list[Bar]) -> dict[date, _MinuteDayCache]:
        out: dict[date, _MinuteDayCache] = {}
        by_day: dict[date, list[Bar]] = {}
        for b in bars:
            by_day.setdefault(b.time.date(), []).append(b)
        for day, day_bars in by_day.items():
            day_bars.sort(key=lambda x: x.time)
            times = [b.time for b in day_bars]
            cum_v: list[int] = []
            cum_a: list[float] = []
            sv = 0.0
            sa = 0.0
            for b in day_bars:
                sv += float(b.volume)
                sa += float(b.amount)
                cum_v.append(int(round(sv)))
                cum_a.append(float(sa))
            out[day] = _MinuteDayCache(bars=day_bars, times=times, cum_volume=cum_v, cum_amount=cum_a)
        return out

    def available_days(self, *, start: str, end: str) -> list[date]:
        if not self._trade_days:
            degrade_once(
                "bt_trade_days_empty",
                "Backtest: trade day set is empty; no data available in selected root.",
                logger_name="backtest.store",
            )
            return []
        s = _parse_day_yyyymmdd(start)
        e = _parse_day_yyyymmdd(end)
        sorted_days = self._sorted_trade_days
        if sorted_days is None:
            sorted_days = sorted(self._trade_days)
            self._sorted_trade_days = list(sorted_days)
        days = [d for d in sorted_days if s <= d <= e]
        if not days:
            warn_once(
                f"bt_no_days_in_range:{start}:{end}",
                f"Backtest: no trade days in requested range start={start} end={end}",
                logger_name="backtest.store",
            )
        return days

    def minute_snapshot(self, *, code: str, now: datetime) -> Optional[tuple[Bar, int, float]]:
        c = _normalize_code(code)
        day_cache = self._minute.get(c, {}).get(now.date())
        if day_cache is None:
            return None
        idx = bisect.bisect_right(day_cache.times, now) - 1
        if idx < 0:
            return None
        b = day_cache.bars[idx]
        return b, int(day_cache.cum_volume[idx]), float(day_cache.cum_amount[idx])

    def _activate_tick_day(self, *, day: date) -> None:
        if self._active_tick_day == day:
            return
        self._active_tick_day = day
        self._tick_day_cache.clear()
        self._tick_day_missing.clear()

    def preload_tick_day(self, *, day: date, codes: list[str], workers: int = 1) -> None:
        if self._tick_mode != "by_day":
            return
        self._activate_tick_day(day=day)
        pending: list[str] = []
        seen: set[str] = set()
        for raw_code in codes:
            code = _normalize_code(raw_code)
            if not code or code in seen:
                continue
            seen.add(code)
            if code in self._tick_day_cache or code in self._tick_day_missing:
                continue
            pending.append(code)
        if not pending:
            return

        max_workers = int(max(1, int(workers)))
        loaded: dict[str, Optional[_TickDayCache]] = {}
        if max_workers <= 1 or len(pending) <= 1:
            for code in pending:
                loaded[code] = self._load_tick_day_cache(code=code, day=day)
        else:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(pending))) as executor:
                futures = {executor.submit(self._load_tick_day_cache, code=code, day=day): code for code in pending}
                for future in as_completed(futures):
                    code = futures[future]
                    loaded[code] = future.result()

        for code, cache in loaded.items():
            if cache is None:
                self._tick_day_missing.add(code)
            else:
                self._tick_day_cache[code] = cache

    def tick_snapshot(self, *, code: str, now: datetime) -> Optional[tuple[_TickPoint, int, float]]:
        c = _normalize_code(code)
        if self._tick_mode == "by_day":
            self._activate_tick_day(day=now.date())
            if c in self._tick_day_missing:
                return None
            day_cache = self._tick_day_cache.get(c)
            if day_cache is None:
                loaded = self._load_tick_day_cache(code=c, day=now.date())
                if loaded is None:
                    self._tick_day_missing.add(c)
                    return None
                self._tick_day_cache[c] = loaded
                day_cache = loaded
            idx = bisect.bisect_right(day_cache.times, now) - 1
            if idx < 0:
                return None
            p = day_cache.points[idx]
            return p, int(day_cache.cum_volume[idx]), float(day_cache.cum_amount[idx])

        day_cache = self._tick.get(c, {}).get(now.date())
        if day_cache is None:
            return None
        idx = bisect.bisect_right(day_cache.times, now) - 1
        if idx < 0:
            return None
        p = day_cache.points[idx]
        return p, int(day_cache.cum_volume[idx]), float(day_cache.cum_amount[idx])

    def daily_bars(self, *, code: str, now: datetime, count: int, include_today: bool) -> list[Bar]:
        c = _normalize_code(code)
        src = self._daily.get(c, [])
        events = self._corp_actions.get(c, [])
        out: list[Bar] = []
        for b in src:
            d = b.time.date()
            if d < now.date() or (include_today and d == now.date()):
                factor = cumulative_price_factor(events=events, from_day=d, to_day=now.date())
                out.append(rescale_bar(b, price_factor=float(factor)) if abs(float(factor) - 1.0) > 1e-12 else b)
        if int(count) <= 0:
            return out
        return out[-int(count) :]

    def minute_bars(self, *, code: str, now: datetime, count: int) -> list[Bar]:
        c = _normalize_code(code)
        by_day = self._minute.get(c, {})
        out: list[Bar] = []
        for day in sorted(by_day.keys()):
            if day > now.date():
                break
            cache = by_day[day]
            if day < now.date():
                out.extend(cache.bars)
                continue
            idx = bisect.bisect_right(cache.times, now) - 1
            if idx >= 0:
                out.extend(cache.bars[: idx + 1])
        if int(count) <= 0:
            return out
        return out[-int(count) :]

    def previous_close(self, *, code: str, day: date) -> Optional[float]:
        c = _normalize_code(code)
        bars = self._daily.get(c, [])
        events = self._corp_actions.get(c, [])
        for b in reversed(bars):
            if b.time.date() < day:
                factor = cumulative_price_factor(events=events, from_day=b.time.date(), to_day=day)
                return float(b.close) * float(factor)
        return None

    def corporate_action_on_day(self, *, code: str, day: date) -> Optional[CorporateActionEvent]:
        c = _normalize_code(code)
        for ev in self._corp_actions.get(c, []):
            if ev.effective_day == day:
                return ev
        return None

    def close_on_day(self, *, code: str, day: date) -> Optional[float]:
        c = _normalize_code(code)
        bars = self._daily.get(c, [])
        for b in reversed(bars):
            if b.time.date() == day:
                return float(b.close)
        return None

    def mark_price(self, *, code: str, now: datetime, prefer_tick: bool = True) -> float:
        if bool(prefer_tick):
            snap_tick = self.tick_snapshot(code=code, now=now)
            if snap_tick is not None:
                return float(snap_tick[0].last_price)
        snap_min = self.minute_snapshot(code=code, now=now)
        if snap_min is not None:
            return float(snap_min[0].close)
        close_today = self.close_on_day(code=code, day=now.date())
        if close_today is not None:
            return float(close_today)
        prev = self.previous_close(code=code, day=now.date())
        if prev is not None:
            return float(prev)
        return 0.0
