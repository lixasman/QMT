from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_chip_engine.daily_batch import run_daily_batch
from integrations.premarket_prep import chip_integration_path, prev_trading_date


def _read_head(path: Path, n: int = 3) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for i, row in enumerate(r):
            out.append({str(k): ("" if v is None else str(v)) for k, v in row.items()})
            if i + 1 >= n:
                break
    return out


def main() -> int:
    now = datetime.now().astimezone()
    t1 = prev_trading_date(now)
    if not t1:
        raise RuntimeError("T-1 missing")

    t0 = time.perf_counter()
    _ = run_daily_batch(trade_date=t1, limit=None, codes=None, l1_csv=False, out=None)
    t1_end = time.perf_counter()

    p = chip_integration_path(trade_date=t1)
    if not p.exists():
        raise RuntimeError(f"integration chip csv missing: {p}")

    head = _read_head(p, n=3)
    cols = list(head[0].keys()) if head else []

    ok = True
    issues: list[str] = []
    if not head:
        ok = False
        issues.append("empty_csv")
    if "code" not in cols:
        ok = False
        issues.append("missing_code_col")
    if "profit_ratio" not in cols:
        ok = False
        issues.append("missing_profit_ratio_col")
    if not any(c.startswith("ms_") for c in cols):
        issues.append("missing_ms_columns")

    obj = {
        "bench": "t1_chip_full_market",
        "trade_date": str(t1),
        "integration_path": str(p),
        "seconds_total": round(t1_end - t0, 3),
        "sanity_ok": bool(ok),
        "issues": issues,
        "columns_count": len(cols),
        "columns_sample": cols[:20],
        "head": head,
    }
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
