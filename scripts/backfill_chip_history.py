from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.time_utils import get_trading_dates
from etf_chip_engine.daily_batch import run_daily_batch
from etf_chip_engine.data.xtdata_provider import latest_trade_date


def _parse_ymd(v: str) -> date:
    s = str(v or "").strip()
    if len(s) == 10 and "-" in s:
        s = s.replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"invalid date: {v!r}; expect YYYYMMDD or YYYY-MM-DD")
    return datetime.strptime(s, "%Y%m%d").date()


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _default_start(today: date) -> date:
    try:
        return today.replace(year=today.year - 3)
    except ValueError:
        return today - timedelta(days=365 * 3 + 1)


def _read_codes_file(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"codes file not found: {path}")
    out: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        for raw in line.replace("\t", ",").replace(" ", ",").split(","):
            code = str(raw).strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(code)
    return out


def _read_codes_csv(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                c = str(row.get("code") or "").strip().upper()
                if c:
                    out.add(c)
    except Exception:
        return set()
    return out


def _integration_chip_path(trade_date: str) -> Path:
    return ROOT / "output" / "integration" / "chip" / f"batch_results_{trade_date}.csv"


def _main_chip_path(trade_date: str) -> Path:
    return ROOT / "etf_chip_engine" / "data" / f"batch_results_{trade_date}.csv"


def _is_day_ready(*, trade_date: str, expected_codes: Iterable[str]) -> tuple[bool, int, int]:
    codes = set(str(x).strip().upper() for x in expected_codes if str(x).strip())
    p = _integration_chip_path(trade_date)
    if not p.exists():
        return False, 0, len(codes)
    got = _read_codes_csv(p)
    if not got:
        return False, 0, len(codes)
    missing = len(codes - got)
    return missing == 0, len(got), missing


@dataclass(frozen=True)
class BackfillConfig:
    start: str
    end: str
    codes_file: Optional[Path]
    daily_universe: bool
    ready_min_rows: int
    force_download: bool
    l1_csv: bool
    retention_days: int
    skip_existing: bool
    max_days: int
    sleep_seconds: float
    stop_on_error: bool
    strict_coverage: bool
    industry_etf_min_a_share_ratio: Optional[float]
    industry_etf_max_constituents: Optional[int]
    liquidity_prefilter_enabled: Optional[bool]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python scripts/backfill_chip_history.py")
    p.add_argument("--start", default="", help="start date (YYYYMMDD), default: today-3y")
    p.add_argument("--end", default="", help="end date (YYYYMMDD), default: latest trade date")
    p.add_argument("--codes-file", default="backtest/default_universe_50.txt", help="ETF codes file (one code per line)")
    p.add_argument(
        "--daily-universe",
        action="store_true",
        help="use daily_batch built-in admission/filter universe (ignore --codes-file)",
    )
    p.add_argument(
        "--ready-min-rows",
        type=int,
        default=1,
        help="when --daily-universe, treat day as ready if integration csv rows >= this threshold",
    )
    p.add_argument("--force-download", action="store_true", help="force tick download in each batch day")
    p.add_argument("--l1-csv", action="store_true", help="use csv fallback for L1 snapshots")
    p.add_argument("--retention-days", type=int, default=0, help="must be 0 for long backfill")
    p.add_argument("--skip-existing", action="store_true", help="skip trade date if integration csv already covers all codes")
    p.add_argument("--max-days", type=int, default=0, help="for smoke-run; 0 means all dates")
    p.add_argument("--sleep-seconds", type=float, default=0.0, help="sleep between days")
    p.add_argument("--stop-on-error", action="store_true", help="stop loop on first failure")
    p.add_argument("--strict-coverage", action="store_true", help="treat missing codes as failure")
    p.add_argument(
        "--industry-etf-min-a-share-ratio",
        default="",
        help="override service A-share ratio admission threshold; empty keeps service default",
    )
    p.add_argument(
        "--industry-etf-max-constituents",
        default="",
        help="override service max-constituents admission threshold; empty keeps default",
    )
    p.add_argument(
        "--liquidity-prefilter-enabled",
        default="",
        help="override service liquidity prefilter switch; empty keeps service default",
    )
    return p


def _resolve_cfg(ns: argparse.Namespace) -> BackfillConfig:
    today = datetime.now().astimezone().date()
    end_td = latest_trade_date(_ymd(today)) or _ymd(today)
    end_d = _parse_ymd(ns.end) if str(ns.end).strip() else _parse_ymd(end_td)
    start_d = _parse_ymd(ns.start) if str(ns.start).strip() else _default_start(end_d)
    if start_d > end_d:
        raise RuntimeError(f"start must <= end, got {start_d} > {end_d}")
    min_a_share_ratio: Optional[float] = None
    if str(ns.industry_etf_min_a_share_ratio).strip():
        min_a_share_ratio = float(str(ns.industry_etf_min_a_share_ratio).strip())
    max_constituents: Optional[int] = None
    if str(ns.industry_etf_max_constituents).strip():
        max_constituents = int(str(ns.industry_etf_max_constituents).strip())
    liq_prefilter_enabled: Optional[bool] = None
    if str(ns.liquidity_prefilter_enabled).strip():
        s = str(ns.liquidity_prefilter_enabled).strip().lower()
        liq_prefilter_enabled = s in {"1", "true", "t", "yes", "y", "on"}

    return BackfillConfig(
        start=_ymd(start_d),
        end=_ymd(end_d),
        codes_file=(None if bool(ns.daily_universe) else Path(str(ns.codes_file))),
        daily_universe=bool(ns.daily_universe),
        ready_min_rows=max(1, int(ns.ready_min_rows)),
        force_download=bool(ns.force_download),
        l1_csv=bool(ns.l1_csv),
        retention_days=int(ns.retention_days),
        skip_existing=bool(ns.skip_existing),
        max_days=max(0, int(ns.max_days)),
        sleep_seconds=max(0.0, float(ns.sleep_seconds)),
        stop_on_error=bool(ns.stop_on_error),
        strict_coverage=bool(ns.strict_coverage),
        industry_etf_min_a_share_ratio=min_a_share_ratio,
        industry_etf_max_constituents=max_constituents,
        liquidity_prefilter_enabled=liq_prefilter_enabled,
    )


def _count_rows_csv(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for _ in r:
                n += 1
    except Exception:
        return 0
    return int(n)


def _is_day_ready_dynamic(*, trade_date: str, min_rows: int) -> tuple[bool, int]:
    p = _integration_chip_path(trade_date)
    got = _count_rows_csv(p)
    return got >= int(max(min_rows, 1)), got


def main() -> int:
    args = _build_parser().parse_args()
    cfg = _resolve_cfg(args)
    codes: Optional[list[str]] = None
    if not cfg.daily_universe:
        if cfg.codes_file is None:
            raise RuntimeError("codes_file is required when daily_universe is disabled")
        codes = _read_codes_file(cfg.codes_file)
        if not codes:
            raise RuntimeError("empty codes list")

    days = [d for d in get_trading_dates(cfg.start, cfg.end) if isinstance(d, str) and len(d) == 8 and d.isdigit()]
    if cfg.max_days > 0:
        days = days[: cfg.max_days]
    total = len(days)
    if total <= 0:
        print(json.dumps({"ok": True, "message": "no trading days in range", "start": cfg.start, "end": cfg.end}, ensure_ascii=False))
        return 0

    ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    log_dir = ROOT / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"chip_backfill_{ts}.jsonl"

    print(
        json.dumps(
            {
                "phase": "start",
                "start": cfg.start,
                "end": cfg.end,
                "trade_days": total,
                "daily_universe": bool(cfg.daily_universe),
                "codes": (None if codes is None else len(codes)),
                "codes_file": ("" if cfg.codes_file is None else str(cfg.codes_file)),
                "ready_min_rows": int(cfg.ready_min_rows),
                "force_download": cfg.force_download,
                "retention_days": cfg.retention_days,
                "skip_existing": cfg.skip_existing,
                "strict_coverage": cfg.strict_coverage,
                "industry_etf_min_a_share_ratio": cfg.industry_etf_min_a_share_ratio,
                "industry_etf_max_constituents": cfg.industry_etf_max_constituents,
                "liquidity_prefilter_enabled": cfg.liquidity_prefilter_enabled,
                "log_path": str(log_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    ok_full_count = 0
    ok_partial_count = 0
    skip_count = 0
    fail_count = 0
    t_all0 = time.perf_counter()

    with log_path.open("a", encoding="utf-8", newline="\n") as lf:
        for i, d in enumerate(days, start=1):
            if codes is None:
                ready, got_n = _is_day_ready_dynamic(trade_date=d, min_rows=cfg.ready_min_rows)
                miss_n = -1
                existing_has_rows = got_n >= int(cfg.ready_min_rows)
            else:
                ready, got_n, miss_n = _is_day_ready(trade_date=d, expected_codes=codes)
                existing_has_rows = got_n > 0
            if cfg.skip_existing and (ready if cfg.strict_coverage else existing_has_rows):
                skip_count += 1
                event = {"trade_date": d, "idx": i, "total": total, "status": "skipped", "rows": got_n, "missing_codes": miss_n}
                lf.write(json.dumps(event, ensure_ascii=False) + "\n")
                print(json.dumps(event, ensure_ascii=False), flush=True)
                continue

            t0 = time.perf_counter()
            err: Optional[str] = None
            try:
                run_daily_batch(
                    trade_date=d,
                    limit=None,
                    codes=(None if codes is None else list(codes)),
                    l1_csv=cfg.l1_csv,
                    force_download=cfg.force_download,
                    retention_days=int(max(cfg.retention_days, 0)),
                    industry_etf_min_a_share_ratio=cfg.industry_etf_min_a_share_ratio,
                    industry_etf_max_constituents=cfg.industry_etf_max_constituents,
                    liquidity_prefilter_enabled=cfg.liquidity_prefilter_enabled,
                    out=None,
                )
            except Exception as e:
                err = repr(e)

            if codes is None:
                ready2, got_n2 = _is_day_ready_dynamic(trade_date=d, min_rows=cfg.ready_min_rows)
                miss_n2 = -1
            else:
                ready2, got_n2, miss_n2 = _is_day_ready(trade_date=d, expected_codes=codes)
            sec = round(time.perf_counter() - t0, 3)
            if err is None:
                if ready2:
                    ok_full_count += 1
                    status = "ok_full"
                elif cfg.strict_coverage:
                    fail_count += 1
                    status = "failed_incomplete"
                else:
                    ok_partial_count += 1
                    status = "ok_partial"
                event2 = {
                    "trade_date": d,
                    "idx": i,
                    "total": total,
                    "status": status,
                    "seconds": sec,
                    "rows": got_n2,
                    "missing_codes": miss_n2,
                    "integration_csv": str(_integration_chip_path(d)),
                    "main_csv": str(_main_chip_path(d)),
                }
                lf.write(json.dumps(event2, ensure_ascii=False) + "\n")
                print(json.dumps(event2, ensure_ascii=False), flush=True)
                if status == "failed_incomplete" and cfg.stop_on_error:
                    break
            else:
                fail_count += 1
                event3 = {
                    "trade_date": d,
                    "idx": i,
                    "total": total,
                    "status": "failed",
                    "seconds": sec,
                    "rows": got_n2,
                    "missing_codes": miss_n2,
                    "error": err,
                    "integration_csv": str(_integration_chip_path(d)),
                }
                lf.write(json.dumps(event3, ensure_ascii=False) + "\n")
                print(json.dumps(event3, ensure_ascii=False), flush=True)
                if cfg.stop_on_error:
                    break

            if cfg.sleep_seconds > 0:
                time.sleep(cfg.sleep_seconds)

    total_sec = round(time.perf_counter() - t_all0, 3)
    summary = {
        "phase": "done",
        "start": cfg.start,
        "end": cfg.end,
        "trade_days": total,
        "ok_full_days": ok_full_count,
        "ok_partial_days": ok_partial_count,
        "skipped_days": skip_count,
        "failed_days": fail_count,
        "seconds_total": total_sec,
        "log_path": str(log_path),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
