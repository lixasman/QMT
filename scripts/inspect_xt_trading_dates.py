from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from xtquant import xtdata  # type: ignore
    except Exception as e:
        print("xtdata_import_fail", repr(e))
        return 2

    now = datetime.now().astimezone()
    end = now.strftime("%Y%m%d")
    start = (now - timedelta(days=40)).strftime("%Y%m%d")

    try:
        dates = xtdata.get_trading_dates("SH", start, end, -1)
    except Exception as e:
        print("get_trading_dates_fail", repr(e))
        return 3

    print("start", start, "end", end, "len", (len(dates) if isinstance(dates, list) else type(dates)))
    if isinstance(dates, list):
        for i, d in enumerate(dates[:10]):
            print(i, type(d).__name__, repr(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

