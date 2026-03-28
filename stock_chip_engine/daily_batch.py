from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from datetime import date, datetime, timedelta
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


def _remove_path(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_dir():
        n_files = 0
        n_bytes = 0
        for f in path.rglob("*"):
            if f.is_file():
                n_files += 1
                try:
                    n_bytes += int(f.stat().st_size)
                except Exception:
                    pass
        shutil.rmtree(path)
        return n_files, n_bytes
    n_bytes = 0
    try:
        n_bytes = int(path.stat().st_size)
    except Exception:
        n_bytes = 0
    path.unlink()
    return 1, n_bytes


def _cleanup_dated_paths(*, entries: list[tuple[Path, date]], keep_days: int, today: date) -> dict[str, int]:
    keep_i = int(max(int(keep_days), 0))
    if keep_i <= 0:
        return {"checked": int(len(entries)), "removed_paths": 0, "removed_files": 0, "removed_bytes": 0}

    cutoff = today - timedelta(days=keep_i)
    removed_paths = 0
    removed_files = 0
    removed_bytes = 0
    for p, d in entries:
        if d >= cutoff:
            continue
        try:
            c_files, c_bytes = _remove_path(p)
            removed_paths += 1
            removed_files += int(c_files)
            removed_bytes += int(c_bytes)
        except Exception as e:
            warn_once(
                f"stock_retention_cleanup_failed:{str(p)}",
                f"Stock retention: 清理过期文件失败，已降级跳过: path={p} err={repr(e)}",
                logger_name=__name__,
            )
    return {
        "checked": int(len(entries)),
        "removed_paths": int(removed_paths),
        "removed_files": int(removed_files),
        "removed_bytes": int(removed_bytes),
    }


def _collect_l1_snapshot_entries(root: Path) -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not root.exists():
        return out
    for d in root.iterdir():
        if not d.is_dir():
            continue
        dd = _parse_yyyymmdd(d.name)
        if dd is not None:
            out.append((d, dd))
    return out


def _collect_chip_snapshot_entries(root: Path) -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not root.exists():
        return out
    pat = re.compile(r"_(\d{8})\.(?:npz|ema\.json)$")
    for p in root.iterdir():
        if not p.is_file():
            continue
        m = pat.search(p.name)
        if not m:
            continue
        dd = _parse_yyyymmdd(m.group(1))
        if dd is not None:
            out.append((p, dd))
    return out


def _collect_batch_result_entries(root: Path) -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not root.exists():
        return out
    pat = re.compile(r"^stock_batch_results_(\d{8})\.csv$")
    for p in root.iterdir():
        if not p.is_file():
            continue
        m = pat.match(p.name)
        if not m:
            continue
        dd = _parse_yyyymmdd(m.group(1))
        if dd is not None:
            out.append((p, dd))
    return out


def _apply_data_retention(*, keep_days: int) -> dict[str, dict[str, int]]:
    today_d = datetime.now().date()

    stock_root = Path("stock_chip_engine") / "data"
    chip_dir = stock_root / "chip_snapshots"
    l1_dir = stock_root / "l1_snapshots"
    integ_dir = Path("output") / "integration" / "stock_chip"

    groups = {
        "l1_snapshots": _collect_l1_snapshot_entries(l1_dir),
        "chip_snapshots": _collect_chip_snapshot_entries(chip_dir),
        "batch_results": _collect_batch_result_entries(stock_root),
        "integration_batch_results": _collect_batch_result_entries(integ_dir),
    }

    stats: dict[str, dict[str, int]] = {}
    for name, entries in groups.items():
        stats[name] = _cleanup_dated_paths(entries=entries, keep_days=int(keep_days), today=today_d)
    return stats


def run_daily_batch(
    *,
    trade_date: str,
    codes: list[str],
    limit: Optional[int] = None,
    force_download: bool = False,
    retention_days: int = 365,
    l1_csv: bool = False,
) -> Path:
    t0 = time.perf_counter()
    xdp.require_xtdata()

    td = _resolve_trade_date(str(trade_date))
    cfg: dict[str, object] = {}
    if bool(l1_csv):
        cfg["l1_fallback_csv"] = "1"

    svc = StockChipService(config=(cfg or None))
    t1 = time.perf_counter()
    df = svc.run_daily(td, codes=codes, limit=limit, force_download=bool(force_download))
    t2 = time.perf_counter()

    out_path = Path("stock_chip_engine") / "data" / f"stock_batch_results_{td}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    integ_dir = Path("output") / "integration" / "stock_chip"
    integ_dir.mkdir(parents=True, exist_ok=True)
    out_path2 = integ_dir / f"stock_batch_results_{td}.csv"
    df.to_csv(out_path2, index=False, encoding="utf-8-sig")
    t3 = time.perf_counter()

    retention_stats = _apply_data_retention(keep_days=int(retention_days))
    t4 = time.perf_counter()

    print(
        json.dumps(
            {
                "timing": "stock_chip_engine.daily_batch.run_daily_batch",
                "trade_date": str(td),
                "codes": int(len(list(codes or []))),
                "rows": int(len(df)),
                "force_download": bool(force_download),
                "retention_days": int(retention_days),
                "seconds": {
                    "init": round(t1 - t0, 3),
                    "run": round(t2 - t1, 3),
                    "write": round(t3 - t2, 3),
                    "retention": round(t4 - t3, 3),
                    "total": round(t4 - t0, 3),
                },
                "retention": retention_stats,
                "paths": {
                    "human": str(out_path),
                    "integration": str(out_path2),
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    return out_path


def _parse_codes_arg(v: str) -> list[str]:
    s = str(v or "").strip()
    if not s:
        return []
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Stock chip + microstructure daily batch (isolated from ETF).")
    ap.add_argument("--date", default="auto", help="YYYYMMDD / auto / latest")
    ap.add_argument("--codes", default="", help="Comma-separated codes, e.g. 600000.SH,000001.SZ")
    ap.add_argument(
        "--watch",
        action="append",
        default=[],
        help="Repeatable watch code. Prefer this in PowerShell to preserve leading zeros, e.g. --watch 003040 --watch 600693",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--retention-days", type=int, default=365)
    ap.add_argument("--l1-csv", action="store_true", help="Fallback to CSV when parquet engine missing")

    args = ap.parse_args(argv)
    codes = _parse_codes_arg(args.codes) + [str(x).strip() for x in list(args.watch or []) if str(x).strip()]
    if not codes:
        raise SystemExit("no codes: specify --codes or repeat --watch")

    run_daily_batch(
        trade_date=str(args.date),
        codes=codes,
        limit=args.limit,
        force_download=bool(args.force_download),
        retention_days=int(args.retention_days),
        l1_csv=bool(args.l1_csv),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
