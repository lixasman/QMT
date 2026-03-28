from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from core.warn_utils import warn_once
from stock_chip_engine.data import xtdata_provider as xdp
from stock_chip_engine.service import StockChipService


def _resolve_trade_date(date_arg: str) -> str:
    if date_arg and str(date_arg).lower() not in {"auto", "latest"}:
        return str(date_arg)
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    latest = xdp.latest_trade_date(today) or xdp.prev_trade_date(today) or today
    if latest != today:
        return latest

    cutoff = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < cutoff:
        return xdp.prev_trade_date(today) or today
    return today


def _parse_yyyymmdd(v: str) -> Optional[date]:
    if not re.fullmatch(r"\d{8}", str(v)):
        return None
    try:
        return datetime.strptime(str(v), "%Y%m%d").date()
    except Exception:
        return None


def _trade_date_from_xt_value(v: object) -> str:
    # Keep compatibility with xtdata.get_trading_dates output (ms timestamps).
    if isinstance(v, (int, float)) and v >= 10_000_000_000:
        tm = time.localtime(float(v) / 1000.0)
        return time.strftime("%Y%m%d", tm)
    s = str(v)
    m = re.search(r"(\d{8})", s)
    return m.group(1) if m else ""


def _list_trade_dates(*, start_date: str, end_date: str, market: str = "SH") -> list[str]:
    xtdata = xdp.require_xtdata()
    dates = xtdata.get_trading_dates(str(market), start_time=str(start_date), end_time=str(end_date), count=-1)
    if not isinstance(dates, list):
        return []
    out: list[str] = []
    for v in dates:
        td = _trade_date_from_xt_value(v)
        if td:
            out.append(td)
    return out


def _parse_codes_arg(v: str) -> list[str]:
    s = str(v or "").strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def run_backfill(
    *,
    start_date: str,
    end_date: str,
    codes: list[str],
    limit: Optional[int] = None,
    l1_csv: bool = False,
) -> dict[str, object]:
    xdp.require_xtdata()

    start_td = str(start_date).strip()
    end_td = _resolve_trade_date(str(end_date))
    if _parse_yyyymmdd(start_td) is None:
        raise ValueError(f"invalid --start: {start_date}")
    if _parse_yyyymmdd(end_td) is None:
        raise ValueError(f"invalid --end: {end_date} -> {end_td}")

    trade_dates = _list_trade_dates(start_date=start_td, end_date=end_td, market="SH")
    if not trade_dates:
        raise RuntimeError(f"no trading dates between {start_td} and {end_td}")

    cfg: dict[str, object] = {}
    if bool(l1_csv):
        cfg["l1_fallback_csv"] = "1"
    svc = StockChipService(config=(cfg or None))

    stock_data_root = Path("stock_chip_engine") / "data"
    integ_dir = Path("output") / "integration" / "stock_chip"
    stock_data_root.mkdir(parents=True, exist_ok=True)
    integ_dir.mkdir(parents=True, exist_ok=True)

    last_out: Optional[Path] = None
    for td in trade_dates:
        t0 = time.perf_counter()
        df = svc.run_daily(td, codes=codes, limit=limit)
        t1 = time.perf_counter()

        out_path = stock_data_root / f"stock_batch_results_{td}.csv"
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        out_path2 = integ_dir / f"stock_batch_results_{td}.csv"
        df.to_csv(out_path2, index=False, encoding="utf-8-sig")
        last_out = out_path

        print(
            json.dumps(
                {
                    "timing": "stock_chip_engine.backfill.run_backfill",
                    "trade_date": td,
                    "codes": int(len(list(codes or []))),
                    "rows": int(len(df)),
                    "seconds": {"run": round(t1 - t0, 3)},
                    "paths": {"human": str(out_path), "integration": str(out_path2)},
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    return {
        "start_date": start_td,
        "end_date": end_td,
        "trade_dates": trade_dates,
        "last_path": str(last_out) if last_out is not None else "",
        "last_integration_path": str((integ_dir / f"stock_batch_results_{trade_dates[-1]}.csv")),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill stock chip + microstructure from start_date to end_date.")
    ap.add_argument("--start", required=True, help="YYYYMMDD (first tick-available date), e.g. 20260204")
    ap.add_argument("--end", default="auto", help="YYYYMMDD / auto / latest")
    ap.add_argument("--codes", default="", help="Comma-separated codes, e.g. 600693.SH,300771.SZ")
    ap.add_argument(
        "--watch",
        action="append",
        default=[],
        help="Repeatable watch code. Prefer this in PowerShell to preserve leading zeros, e.g. --watch 003040 --watch 600693",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--l1-csv", action="store_true", help="Fallback to CSV when parquet engine missing")

    args = ap.parse_args(argv)
    codes = _parse_codes_arg(args.codes) + [str(x).strip() for x in list(args.watch or []) if str(x).strip()]
    if not codes:
        raise SystemExit("no codes: specify --codes or repeat --watch")

    try:
        out = run_backfill(
            start_date=str(args.start),
            end_date=str(args.end),
            codes=codes,
            limit=args.limit,
            l1_csv=bool(args.l1_csv),
        )
    except Exception as e:
        warn_once("stock_backfill_failed", f"Stock backfill failed: err={repr(e)}", logger_name=__name__)
        raise

    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
