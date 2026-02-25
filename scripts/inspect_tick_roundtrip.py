from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_chip_engine.data.xtdata_provider import _download_history_data2_compat, normalize_etf_code  # type: ignore


def _len_any(x) -> int:
    try:
        return int(len(x))
    except Exception:
        return -1


def main() -> int:
    from xtquant import xtdata  # type: ignore

    override_dir = os.environ.get("XT_DATA_DIR", "").strip()
    if override_dir:
        try:
            xtdata.data_dir = override_dir
        except Exception as e:
            print("set_xtdata_data_dir_fail", repr(e))

    code = normalize_etf_code("512480.SH") or "512480.SH"
    now = datetime.now().astimezone()
    trade_date = now.strftime("%Y%m%d")
    t1 = (now - timedelta(days=1)).strftime("%Y%m%d")
    start = f"{trade_date}093000"
    end = f"{trade_date}151000"
    start_t1 = f"{t1}093000"
    end_t1 = f"{t1}151000"

    data_dir = getattr(xtdata, "data_dir", None)
    print("xtdata.data_dir", str(data_dir or ""))

    t0 = time.perf_counter()
    try:
        _download_history_data2_compat([code], "tick", start_time=start, end_time=end)
        dl_ok = True
    except Exception as e:
        print("download_tick_today_fail", repr(e))
        dl_ok = False
    t1a = time.perf_counter()

    t1b0 = time.perf_counter()
    try:
        _download_history_data2_compat([code], "tick", start_time=start_t1, end_time=end_t1)
        dl_t1_ok = True
    except Exception as e:
        print("download_tick_tminus1_fail", repr(e))
        dl_t1_ok = False
    t1b1 = time.perf_counter()

    t2 = time.perf_counter()
    try:
        raw_local = xtdata.get_local_data(
            field_list=["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"],
            stock_list=[code],
            period="tick",
            start_time=start_t1,
            end_time=end_t1,
            count=-1,
            dividend_type="none",
            fill_data=True,
        )
    except Exception as e:
        raw_local = None
        print("get_local_data_tick_fail", repr(e))
    t3 = time.perf_counter()

    t4 = time.perf_counter()
    try:
        raw_market = xtdata.get_market_data(
            field_list=["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"],
            stock_list=[code],
            period="tick",
            start_time=start_t1,
            end_time=end_t1,
            count=-1,
            dividend_type="none",
            fill_data=True,
        )
    except Exception as e:
        raw_market = None
        print("get_market_data_tick_fail", repr(e))
    t5 = time.perf_counter()

    local_len = -1
    if isinstance(raw_local, dict):
        v = raw_local.get(code)
        if v is None and raw_local:
            v = next(iter(raw_local.values()))
        local_len = _len_any(v)

    market_len = -1
    if isinstance(raw_market, dict):
        v2 = raw_market.get(code)
        if v2 is None and raw_market:
            v2 = next(iter(raw_market.values()))
        market_len = _len_any(v2)

    print("today", trade_date, "t_minus_1", t1)
    print("download_today_ok", int(dl_ok), "download_today_seconds", round(t1a - t0, 3))
    print("download_t1_ok", int(dl_t1_ok), "download_t1_seconds", round(t1b1 - t1b0, 3))
    print("local_seconds", round(t3 - t2, 3), "local_len", int(local_len))
    print("market_seconds", round(t5 - t4, 3), "market_len", int(market_len))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
