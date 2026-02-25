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
        field_list=["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"],
        stock_list=[code],
        period="tick",
        start_time=start,
        end_time=end,
        count=-1,
        dividend_type="none",
        fill_data=True,
    )
    print("trade_date", trade_date, "start", start, "end", end)
    print("raw_type", type(raw).__name__)
    if isinstance(raw, dict):
        print("keys", list(raw.keys())[:20])
        for k, v in list(raw.items())[:10]:
            print("field", k, "type", type(v).__name__)
            if hasattr(v, "shape"):
                print("shape", getattr(v, "shape"))
            if hasattr(v, "index"):
                idx = getattr(v, "index")
                print("index_len", (len(idx) if idx is not None else None), "index_head", list(idx)[:3] if idx is not None else None)
            if hasattr(v, "columns"):
                cols = getattr(v, "columns")
                print("cols_len", (len(cols) if cols is not None else None), "cols_head", list(cols)[:3] if cols is not None else None)
            try:
                if hasattr(v, "iloc"):
                    print("sample_row0_head", v.iloc[0, :5].to_list())
            except Exception as e:
                print("sample_err", repr(e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

