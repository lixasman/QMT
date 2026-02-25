from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_chip_engine.data.xtdata_provider import _download_history_data2_compat  # type: ignore


def main() -> int:
    from xtquant import xtdata  # type: ignore

    trade_date = datetime.now().strftime("%Y%m%d")
    code = "512480.SH"
    start = f"{trade_date}093000"
    end = f"{trade_date}151000"

    t0 = time.perf_counter()
    _download_history_data2_compat([code], "tick", start_time=start, end_time=end)
    t1 = time.perf_counter()

    raw = xtdata.get_market_data(
        field_list=["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"],
        stock_list=[code],
        period="tick",
        start_time=start,
        end_time=end,
        count=-1,
        dividend_type="none",
        fill_data=True,
    )

    n = -1
    if isinstance(raw, dict) and code in raw:
        v = raw[code]
        try:
            n = int(len(v))
        except Exception:
            n = -2
    print("trade_date", trade_date, "download_seconds", round(t1 - t0, 3), "ticks_len", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

