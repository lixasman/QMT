from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from core.time_utils import get_trading_dates
from core.warn_utils import warn_once

logger = logging.getLogger("strategy")


def _today_yyyymmdd(now: datetime) -> str:
    return now.astimezone().strftime("%Y%m%d")


def prev_trading_date(now: datetime) -> str:
    today = _today_yyyymmdd(now)
    start = (now - timedelta(days=40)).strftime("%Y%m%d")
    end = today
    cal = [d for d in get_trading_dates(start, end) if isinstance(d, str) and len(d) == 8 and d.isdigit()]
    prev = ""
    for d in cal:
        if d < today and d > prev:
            prev = d
    return prev


def chip_integration_path(*, trade_date: str, integration_dir: str | Path = "output/integration") -> Path:
    return Path(integration_dir) / "chip" / f"batch_results_{trade_date}.csv"


def finintel_integration_path(*, code6: str, day: str, integration_dir: str | Path = "output/integration") -> Path:
    return Path(integration_dir) / "finintel" / f"sentiment_{code6}_{day}.json"


def finintel_hot_csv_path(*, day: str, out_dir: str | Path = "output") -> Path:
    return Path(out_dir) / f"finintel_signal_hot_{day}.csv"


@contextmanager
def _fake_finintel_today(day: str):
    key = "FININTEL_FAKE_TODAY"
    prev = os.environ.get(key)
    os.environ[key] = str(day)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def _code6(code: str) -> str:
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    return s if len(s) == 6 and s.isdigit() else ""


@dataclass(frozen=True)
class PreMarketPrepResult:
    t_minus_1: str
    chip_ready: bool
    hot_csv: Optional[str]
    hot_codes: tuple[str, ...]
    sentiment_ready_codes: tuple[str, ...]


def ensure_tminus1_ready(
    *,
    now: datetime,
    watch_codes: Iterable[str],
    position_codes: Iterable[str],
    hot_top: int = 15,
    integration_dir: str | Path = "output/integration",
    out_dir: str | Path = "output",
) -> PreMarketPrepResult:
    start_all = time.perf_counter()
    t1 = prev_trading_date(now)
    if not t1:
        warn_once("premarket_prev_trade_date_missing", "PreMarket: 无法解析 T-1 交易日，跳过自动补齐")
        return PreMarketPrepResult(t_minus_1="", chip_ready=False, hot_csv=None, hot_codes=(), sentiment_ready_codes=())

    chip_ok = True
    chip_p = chip_integration_path(trade_date=t1, integration_dir=integration_dir)
    if not chip_p.exists():
        chip_ok = False
        t_chip0 = time.perf_counter()
        try:
            from etf_chip_engine.daily_batch import run_daily_batch

            run_daily_batch(trade_date=t1, limit=None, codes=None, l1_csv=False, out=None)
            chip_ok = chip_p.exists()
        except Exception as e:
            warn_once("premarket_chip_prepare_failed", f"PreMarket: T-1 筹码/微观因子补齐失败，已降级继续: date={t1} err={repr(e)}")
        t_chip1 = time.perf_counter()
        logger.warning(
            "%s",
            json.dumps(
                {
                    "timing": "premarket_prep.chip_prepare",
                    "t_minus_1": str(t1),
                    "chip_ready": bool(chip_ok),
                    "seconds": round(t_chip1 - t_chip0, 3),
                },
                ensure_ascii=False,
            ),
        )

    hot_csv = finintel_hot_csv_path(day=t1, out_dir=out_dir)
    hot_codes: list[str] = []
    sentiment_ready: set[str] = set()

    extra_codes = set(str(x).strip() for x in list(watch_codes) + list(position_codes) if str(x).strip())
    need_extra: list[str] = []

    with _fake_finintel_today(t1):
        t_hot0 = time.perf_counter()
        if int(hot_top) > 0 and not hot_csv.exists():
            try:
                from finintel.main import main as fin_main

                fin_main(["--signal-hot-top", str(int(hot_top)), "--no-trace"])
            except Exception as e:
                warn_once("premarket_finintel_hot_failed", f"PreMarket: 热门ETF/情绪因子补齐失败，已降级继续: top={hot_top} err={repr(e)}")
        t_hot1 = time.perf_counter()
        if int(hot_top) > 0:
            logger.warning(
                "%s",
                json.dumps(
                    {
                        "timing": "premarket_prep.finintel_hot_top",
                        "t_minus_1": str(t1),
                        "hot_top": int(hot_top),
                        "hot_csv_exists": bool(hot_csv.exists()),
                        "seconds": round(t_hot1 - t_hot0, 3),
                    },
                    ensure_ascii=False,
                ),
            )

        if hot_csv.exists():
            try:
                import csv

                with hot_csv.open("r", encoding="utf-8-sig", newline="") as f:
                    r = csv.DictReader(f)
                    for row in r:
                        c = str(row.get("code") or "").strip()
                        if c:
                            hot_codes.append(c)
            except Exception:
                hot_codes = []

        for c in hot_codes:
            extra_codes.add(c)

        for c in sorted(extra_codes):
            c6 = _code6(c)
            if not c6:
                continue
            p = finintel_integration_path(code6=c6, day=t1, integration_dir=integration_dir)
            if p.exists():
                sentiment_ready.add(c6)
                continue
            need_extra.append(c)

        for c in need_extra:
            c6 = _code6(c)
            if not c6:
                continue
            t_sig0 = time.perf_counter()
            try:
                from finintel.main import main as fin_main

                fin_main(["--signal-etf", c6, "--no-trace"])
            except Exception as e:
                warn_once(f"premarket_finintel_signal_failed:{c6}", f"PreMarket: 情绪因子补齐失败，已降级继续: etf={c6} date={t1} err={repr(e)}")
                continue
            p = finintel_integration_path(code6=c6, day=t1, integration_dir=integration_dir)
            if p.exists():
                sentiment_ready.add(c6)
            t_sig1 = time.perf_counter()
            logger.warning(
                "%s",
                json.dumps(
                    {
                        "timing": "premarket_prep.finintel_signal_etf",
                        "t_minus_1": str(t1),
                        "etf": str(c6),
                        "ok": bool(p.exists()),
                        "seconds": round(t_sig1 - t_sig0, 3),
                    },
                    ensure_ascii=False,
                ),
            )

    end_all = time.perf_counter()
    logger.warning(
        "%s",
        json.dumps(
            {
                "timing": "premarket_prep.ensure_tminus1_ready",
                "t_minus_1": str(t1),
                "chip_ready": bool(chip_ok),
                "hot_codes": int(len(hot_codes)),
                "sentiment_ready_codes": int(len(sentiment_ready)),
                "seconds": round(end_all - start_all, 3),
            },
            ensure_ascii=False,
        ),
    )
    return PreMarketPrepResult(
        t_minus_1=str(t1),
        chip_ready=bool(chip_ok),
        hot_csv=(str(hot_csv) if hot_csv.exists() else None),
        hot_codes=tuple(hot_codes),
        sentiment_ready_codes=tuple(sorted(sentiment_ready)),
    )
