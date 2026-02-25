from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from xtquant import xtdata  # type: ignore

    trade_date = datetime.now().strftime("%Y%m%d")
    code = "512480.SH"
    start = f"{trade_date}093000"
    end = f"{trade_date}151000"
    raw = xtdata.get_market_data(
        field_list=["time", "open", "high", "low", "close", "volume", "amount"],
        stock_list=[code],
        period="1m",
        start_time=start,
        end_time=end,
        count=-1,
        dividend_type="none",
        fill_data=True,
    )
    print("trade_date", trade_date, "period", "1m", "raw_type", type(raw).__name__)
    if isinstance(raw, dict) and code in raw:
        v = raw[code]
        try:
            print("len", len(v), "dtype", getattr(v, "dtype", None))
        except Exception as e:
            print("len_err", repr(e))
    elif isinstance(raw, dict):
        print("keys", list(raw.keys())[:10])
        v = next(iter(raw.values())) if raw else None
        print("value_type", type(v).__name__)
        try:
            print("len", len(v))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

