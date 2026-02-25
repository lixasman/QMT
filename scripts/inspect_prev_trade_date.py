from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_chip_engine.data.xtdata_provider import prev_trade_date


def main() -> int:
    today = datetime.now().strftime("%Y%m%d")
    print("today", today)
    print("prev_trade_date", prev_trade_date(today))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

