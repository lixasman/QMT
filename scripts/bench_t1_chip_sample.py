from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_chip_engine.daily_batch import run_daily_batch
from etf_chip_engine.data.xtdata_provider import prev_trade_date
from integrations.premarket_prep import chip_integration_path


def main() -> int:
    today = datetime.now().strftime("%Y%m%d")
    t1 = prev_trade_date(today) or today
    limit = 10

    t0 = time.perf_counter()
    _ = run_daily_batch(trade_date=t1, limit=limit, codes=None, l1_csv=False, out=None)
    t1_end = time.perf_counter()

    p = chip_integration_path(trade_date=t1)
    if p.exists():
        try:
            df = pd.read_csv(p, encoding="utf-8-sig")
        except EmptyDataError:
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    issues: list[str] = []
    if df.empty:
        issues.append("empty_df")
    if "code" not in df.columns:
        issues.append("missing_code")
    if "profit_ratio" not in df.columns:
        issues.append("missing_profit_ratio")
    if not any(str(c).startswith("ms_") for c in df.columns):
        issues.append("missing_ms_columns")

    obj = {
        "bench": "t1_chip_sample",
        "today": today,
        "trade_date": t1,
        "limit": limit,
        "seconds_total": round(t1_end - t0, 3),
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "issues": issues,
        "head": df.head(3).to_dict(orient="records") if not df.empty else [],
    }
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
