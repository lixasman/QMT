from __future__ import annotations

import argparse
import csv
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Optional


AM_START = time(9, 30)
AM_END = time(11, 30)
PM_START = time(13, 0)
PM_END = time(15, 0)


def _is_intraday_session(t: time) -> bool:
    return (AM_START <= t < AM_END) or (PM_START <= t < PM_END)


def _is_non_decreasing(values: list[float], *, eps: float = 1e-9) -> bool:
    if len(values) <= 1:
        return True
    prev = float(values[0])
    for x in values[1:]:
        cur = float(x)
        if cur + float(eps) < prev:
            return False
        prev = float(cur)
    return True


def _to_float(v: object) -> float:
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _parse_epoch_seconds(raw: str) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        x = float(s)
    except Exception:
        return None
    if x <= 0:
        return None
    # Accept both seconds and milliseconds.
    if x >= 1e12:
        x = x / 1000.0
    try:
        return datetime.fromtimestamp(float(x))
    except Exception:
        return None


def _parse_daily_date_from_time(raw: str) -> Optional[str]:
    """
    Return YYYYMMDD from daily csv time field.
    Supports epoch ms/s, YYYYMMDD, YYYY-MM-DD.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        if len(s) == 8:
            return s
        try:
            x = float(s)
            # Heuristic: >= 1e12 -> ms, else seconds.
            if x >= 1e12:
                dt = datetime.fromtimestamp(x / 1000.0)
            else:
                dt = datetime.fromtimestamp(x)
            return dt.strftime("%Y%m%d")
        except Exception:
            return None
    s2 = s.replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s2, fmt)
            return dt.strftime("%Y%m%d")
        except Exception:
            continue
    # Sometimes export writes epoch as float-like string: "1740720720000.0"
    try:
        x2 = float(s)
        if x2 >= 1e12:
            dt = datetime.fromtimestamp(x2 / 1000.0)
        else:
            dt = datetime.fromtimestamp(x2)
        return dt.strftime("%Y%m%d")
    except Exception:
        return None


def _epoch_ms_midnight_local(ymd8: str) -> str:
    d = datetime.strptime(ymd8, "%Y%m%d")
    # Keep the same convention with existing xtdata exports: epoch milliseconds.
    ms = int(round(d.timestamp() * 1000.0))
    return f"{ms}.0"


def _code_to_daily_fname(code: str) -> str:
    return f"{str(code).strip().upper().replace('.', '_')}.csv"


@dataclass(frozen=True)
class DailyBar:
    open: float
    high: float
    low: float
    close: float
    volume_hand: float
    amount: float


def compute_daily_bar_from_l1_csv(path: Path) -> Optional[DailyBar]:
    if not path.exists():
        return None
    times: list[datetime] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    amts: list[float] = []

    try:
        f = path.open("r", encoding="utf-8-sig", newline="")
    except Exception:
        f = path.open("r", newline="")
    with f:
        r = csv.DictReader(f)
        for raw_row in r:
            # Normalize keys to lower-case.
            row = {str(k or "").strip().lower(): ("" if v is None else str(v).strip()) for k, v in raw_row.items()}
            dt = _parse_epoch_seconds(row.get("time") or row.get("datetime") or row.get("dt") or "")
            if dt is None:
                continue
            if not _is_intraday_session(dt.time()):
                continue
            c = _to_float(row.get("close") or row.get("lastprice") or row.get("last_price") or row.get("price") or "")
            if c <= 0:
                continue
            o = _to_float(row.get("open") or "")
            h = _to_float(row.get("high") or "")
            l = _to_float(row.get("low") or "")
            if o <= 0:
                o = float(c)
            if h <= 0:
                h = float(c)
            if l <= 0:
                l = float(c)
            v = max(0.0, _to_float(row.get("volume") or row.get("vol") or row.get("v") or 0.0))
            a = max(0.0, _to_float(row.get("amount") or row.get("amt") or row.get("turnover") or 0.0))

            times.append(dt)
            opens.append(float(o))
            highs.append(float(h))
            lows.append(float(l))
            closes.append(float(c))
            vols.append(float(v))
            amts.append(float(a))

    if not times:
        return None

    idx = sorted(range(len(times)), key=lambda i: times[i])
    open_day = float(opens[idx[0]]) if float(opens[idx[0]]) > 0 else float(closes[idx[0]])
    close_day = float(closes[idx[-1]])
    high_day = max(float(highs[i]) for i in idx)
    low_day = min(float(lows[i]) for i in idx)
    if high_day <= 0 or low_day <= 0 or close_day <= 0 or open_day <= 0:
        return None

    raw_vol = [float(vols[i]) for i in idx]
    raw_amt = [float(amts[i]) for i in idx]
    vol_is_cum = _is_non_decreasing(raw_vol)
    amt_is_cum = _is_non_decreasing(raw_amt)
    vol_total = float(raw_vol[-1]) if vol_is_cum else float(sum(raw_vol))
    amt_total = float(raw_amt[-1]) if amt_is_cum else float(sum(raw_amt))

    # L1 snapshot volumes are in shares; backtest 1d volumes are in hands (lots=100 shares).
    volume_hand = float(vol_total) / 100.0
    return DailyBar(
        open=float(open_day),
        high=float(high_day),
        low=float(low_day),
        close=float(close_day),
        volume_hand=float(volume_hand),
        amount=float(amt_total),
    )


def _is_daily_row_invalid(row: dict[str, str]) -> bool:
    c = _to_float(row.get("close"))
    v = _to_float(row.get("volume"))
    a = _to_float(row.get("amount"))
    if c <= 0:
        return True
    # The observed failure mode: placeholder OHLC with volume/amount == 0.
    if v <= 0 or a <= 0:
        return True
    return False


def _load_daily_rows(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    """
    Returns: (fieldnames, ymd8->row) where row values are kept as strings (to preserve formatting where possible).
    """
    if not path.exists():
        return ["time", "open", "high", "low", "close", "volume", "amount"], {}
    try:
        f = path.open("r", encoding="utf-8-sig", newline="")
    except Exception:
        f = path.open("r", newline="")
    with f:
        r = csv.DictReader(f)
        fieldnames = list(r.fieldnames or []) or ["time", "open", "high", "low", "close", "volume", "amount"]
        by_day: dict[str, dict[str, str]] = {}
        for raw_row in r:
            row = {str(k or "").strip().lower(): ("" if v is None else str(v)) for k, v in raw_row.items()}
            ymd8 = _parse_daily_date_from_time(row.get("time") or row.get("datetime") or row.get("date") or "")
            if not ymd8:
                continue
            # Keep original keys for writing (use the original header casing/ordering later).
            # For simplicity, normalize to lower-case keys.
            by_day[str(ymd8)] = row
    return fieldnames, by_day


def _write_daily_rows(path: Path, *, fieldnames: list[str], by_day: dict[str, dict[str, str]]) -> None:
    # Sort by numeric time if possible; fallback to day string.
    def _sort_key(item: tuple[str, dict[str, str]]):
        ymd8, row = item
        t = row.get("time") or ""
        try:
            return float(str(t).strip())
        except Exception:
            return float("inf"), str(ymd8)

    items = sorted(by_day.items(), key=_sort_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        # Always write the canonical daily schema, but keep any extra columns if present in original.
        base = ["time", "open", "high", "low", "close", "volume", "amount"]
        out_fields = []
        seen = set()
        for k in fieldnames:
            kl = str(k).strip().lower()
            if not kl:
                continue
            if kl in seen:
                continue
            seen.add(kl)
            out_fields.append(kl)
        for k in base:
            if k not in seen:
                out_fields.append(k)
                seen.add(k)
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        for _, row in items:
            out_row = {k: ("" if row.get(k) is None else str(row.get(k))) for k in out_fields}
            w.writerow(out_row)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python scripts/backfill_backtest_daily_from_l1.py")
    p.add_argument("--tick-root", default="etf_chip_engine/data/l1_snapshots", help="L1 snapshot root with YYYYMMDD folders")
    p.add_argument("--daily-root", default="backtest/data/1d", help="backtest daily csv root")
    p.add_argument("--start", default="", help="start date YYYYMMDD (optional)")
    p.add_argument("--end", default="", help="end date YYYYMMDD (optional)")
    p.add_argument("--dry-run", action="store_true", help="only report, do not write")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)

    # Ensure repo root is importable when running as a script.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from backtest.universe import DEFAULT_UNIVERSE_CODES

    tick_root = Path(str(ns.tick_root))
    daily_root = Path(str(ns.daily_root))
    if not tick_root.exists():
        raise RuntimeError(f"tick root not found: {tick_root}")
    if not daily_root.exists():
        raise RuntimeError(f"daily root not found: {daily_root}")

    day_dirs = []
    for p in tick_root.iterdir():
        if not p.is_dir():
            continue
        name = p.name.strip()
        if len(name) == 8 and name.isdigit():
            day_dirs.append(name)
    day_dirs.sort()
    if not day_dirs:
        raise RuntimeError(f"no YYYYMMDD folders under: {tick_root}")

    start = str(ns.start or "").strip()
    end = str(ns.end or "").strip()
    if start and (len(start) != 8 or not start.isdigit()):
        raise RuntimeError(f"invalid --start, expect YYYYMMDD: {start}")
    if end and (len(end) != 8 or not end.isdigit()):
        raise RuntimeError(f"invalid --end, expect YYYYMMDD: {end}")

    days = [d for d in day_dirs if (not start or d >= start) and (not end or d <= end)]
    if not days:
        raise RuntimeError(f"no days within range start={start or 'auto'} end={end or 'auto'} under {tick_root}")

    backup_dir = daily_root.parent / f"1d_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if not bool(ns.dry_run):
        backup_dir.mkdir(parents=True, exist_ok=True)
        for p in sorted(daily_root.glob("*.csv")):
            shutil.copy2(p, backup_dir / p.name)

    report_dir = Path("output") / "backtest_daily_backfill"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    report_rows: list[dict[str, object]] = []

    total_replaced = 0
    total_missing_tick = 0
    total_failed_compute = 0

    for code in DEFAULT_UNIVERSE_CODES:
        fname = _code_to_daily_fname(code)
        daily_path = daily_root / fname
        fieldnames, by_day = _load_daily_rows(daily_path)

        replaced = 0
        missing_tick = 0
        failed_compute = 0

        for d in days:
            row = by_day.get(d)
            if row is not None and not _is_daily_row_invalid(row):
                continue

            tick_path = tick_root / d / fname
            if not tick_path.exists():
                missing_tick += 1
                continue

            bar = compute_daily_bar_from_l1_csv(tick_path)
            if bar is None or bar.close <= 0 or bar.volume_hand <= 0 or bar.amount <= 0:
                failed_compute += 1
                continue

            if row is None:
                row = {}
                row["time"] = _epoch_ms_midnight_local(d)
                by_day[d] = row

            row["open"] = f"{float(bar.open):.6f}"
            row["high"] = f"{float(bar.high):.6f}"
            row["low"] = f"{float(bar.low):.6f}"
            row["close"] = f"{float(bar.close):.6f}"
            row["volume"] = f"{float(bar.volume_hand):.1f}"
            row["amount"] = f"{float(bar.amount):.3f}"
            replaced += 1

        if replaced > 0 and not bool(ns.dry_run):
            _write_daily_rows(daily_path, fieldnames=fieldnames, by_day=by_day)

        total_replaced += int(replaced)
        total_missing_tick += int(missing_tick)
        total_failed_compute += int(failed_compute)

        report_rows.append(
            {
                "code": str(code),
                "daily_file": str(daily_path),
                "days_in_range": int(len(days)),
                "replaced_days": int(replaced),
                "missing_tick_files": int(missing_tick),
                "failed_compute_days": int(failed_compute),
            }
        )

    with report_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "code",
                "daily_file",
                "days_in_range",
                "replaced_days",
                "missing_tick_files",
                "failed_compute_days",
            ],
        )
        w.writeheader()
        for row in report_rows:
            w.writerow({k: row.get(k) for k in w.fieldnames})

    print(
        f"backfill done | days={len(days)} codes={len(report_rows)} replaced={total_replaced} "
        f"missing_tick={total_missing_tick} failed_compute={total_failed_compute}"
    )
    print(f"backup_dir={backup_dir if not bool(ns.dry_run) else 'dry_run'}")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
