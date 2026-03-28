from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FillPoint:
    timestamp: datetime
    side: str
    quantity: int
    price: float
    amount: float
    order_id: str


def _normalize_code(code: str) -> str:
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        return s
    if "_" in s and len(s) >= 8:
        left, right = s.split("_", 1)
        if left.isdigit() and right in {"SH", "SZ"}:
            return f"{left}.{right}"
    if len(s) == 6 and s.isdigit():
        if s.startswith(("5", "6", "9")):
            return f"{s}.SH"
        return f"{s}.SZ"
    return s


def _code_to_file_name(code: str) -> str:
    c = _normalize_code(code)
    if "." not in c:
        return f"{c}.csv"
    a, b = c.split(".", 1)
    return f"{a}_{b}.csv"


def _candidate_tick_file_names(code: str) -> list[str]:
    norm = _normalize_code(code)
    names: list[str] = []
    if not norm:
        return names
    names.append(_code_to_file_name(norm))
    if "." in norm:
        left, _right = norm.split(".", 1)
        names.append(f"{left}.csv")
        names.append(f"{norm}.csv")
    else:
        names.append(f"{norm}.csv")
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = str(name).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _normalize_trade_day_name(name: str) -> str:
    s = str(name or "").strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        return s
    return ""


def _iter_trade_day_dirs(tick_root: Path) -> list[tuple[str, Path]]:
    out: dict[str, Path] = {}
    if not tick_root.exists():
        return []
    for p in sorted(tick_root.iterdir()):
        if not p.is_dir():
            continue
        name = _normalize_trade_day_name(p.name)
        if name:
            out.setdefault(name, p)
            continue
        raw_year = str(p.name).strip()
        if len(raw_year) != 4 or not raw_year.isdigit():
            continue
        for month_dir in sorted(p.iterdir()):
            if not month_dir.is_dir():
                continue
            raw_month = str(month_dir.name).strip()
            if len(raw_month) != 2 or not raw_month.isdigit():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir():
                    continue
                day_name = _normalize_trade_day_name(day_dir.name)
                if not day_name:
                    continue
                out.setdefault(day_name, day_dir)
    return sorted(out.items(), key=lambda x: x[0])


def _resolve_trade_day_dir(*, tick_root: Path, day: str) -> Path | None:
    norm = _normalize_trade_day_name(day)
    if not norm:
        return None
    dashed = f"{norm[:4]}-{norm[4:6]}-{norm[6:8]}"
    candidates = [
        tick_root / norm,
        tick_root / dashed,
        tick_root / norm[:4] / norm[4:6] / norm,
        tick_root / norm[:4] / norm[4:6] / dashed,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _pick_row_value(row: dict[str, str], *candidates: str) -> str:
    lower_map = {str(k or "").strip().lower(): v for k, v in row.items()}
    for candidate in candidates:
        val = lower_map.get(str(candidate).strip().lower())
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _read_codes_file(path: Path) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.replace("\t", ",").replace(" ", ",").split(",")
        for raw in parts:
            c = _normalize_code(raw)
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(c)
    return out


def _default_codes() -> list[str]:
    try:
        repo_root = Path(__file__).resolve().parents[1]
        root_s = str(repo_root)
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        from backtest.universe import DEFAULT_UNIVERSE_CODES

        return [_normalize_code(x) for x in DEFAULT_UNIVERSE_CODES if _normalize_code(x)]
    except Exception:
        return []


def _infer_codes_from_tick_root(*, tick_root: Path, trade_days: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for day in trade_days:
        day_dir = _resolve_trade_day_dir(tick_root=tick_root, day=day)
        if day_dir is None:
            continue
        for f in sorted(day_dir.glob("*.csv")):
            stem = f.stem
            c = _normalize_code(stem)
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(c)
        if out:
            return out
    return out


def _list_trade_days(tick_root: Path, start: str, end: str) -> list[str]:
    days: list[str] = []
    for day, _path in _iter_trade_day_dirs(tick_root):
        if start <= day <= end:
            days.append(day)
    return days


def _parse_dt_any(s: str) -> datetime:
    x = str(s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(x, fmt)
        except Exception:
            pass
    raise ValueError(f"invalid datetime: {s}")


def _parse_tick_time_any(s: str) -> datetime:
    x = str(s or "").strip()
    if not x:
        raise ValueError("empty tick time")
    digits = x
    if digits.isdigit():
        if len(digits) == 14:
            return datetime.strptime(digits, "%Y%m%d%H%M%S")
        if len(digits) == 17:
            base = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
            return base + timedelta(milliseconds=int(digits[14:17]))
    if any(sep in x for sep in ("-", "/", "T", ":", " ")):
        return _parse_dt_any(x)
    fv = float(x)
    if abs(fv) >= 1e12:
        return datetime.fromtimestamp(fv / 1000.0)
    return datetime.fromtimestamp(fv)


def _is_trading_time(dt: datetime) -> bool:
    t = dt.time()
    return (dt_time(9, 30) <= t <= dt_time(11, 30)) or (dt_time(13, 0) <= t <= dt_time(15, 0))


def _hour_bucket_30m(dt: datetime) -> datetime:
    base = dt - timedelta(minutes=30)
    base = base.replace(minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=30)


def _load_fills(fills_path: Path, start: str, end: str) -> dict[str, list[FillPoint]]:
    out: dict[str, list[FillPoint]] = defaultdict(list)
    if not fills_path.exists():
        return out
    with fills_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = _normalize_code(str(row.get("etf_code") or ""))
            if not code:
                continue
            try:
                ts = _parse_dt_any(str(row.get("timestamp") or ""))
            except Exception:
                continue
            day = ts.strftime("%Y%m%d")
            if day < start or day > end:
                continue
            side = str(row.get("side") or "").strip().upper()
            if side not in {"BUY", "SELL"}:
                continue
            try:
                qty = int(float(row.get("quantity") or 0))
                price = float(row.get("price") or 0.0)
                amount = float(row.get("amount") or 0.0)
            except Exception:
                continue
            fp = FillPoint(
                timestamp=ts,
                side=side,
                quantity=qty,
                price=price,
                amount=amount,
                order_id=str(row.get("order_id") or ""),
            )
            out[code].append(fp)
    for k in out:
        out[k].sort(key=lambda x: x.timestamp)
    return out


def _load_hourly_close_for_code(
    *,
    tick_root: Path,
    trade_days: Iterable[str],
    code: str,
) -> tuple[list[tuple[datetime, float]], int]:
    hourly_close: dict[datetime, float] = {}
    covered_days = 0
    for day in trade_days:
        day_dir = _resolve_trade_day_dir(tick_root=tick_root, day=day)
        if day_dir is None:
            continue
        p = None
        for file_name in _candidate_tick_file_names(code):
            candidate = day_dir / file_name
            if candidate.exists():
                p = candidate
                break
        if p is None:
            continue
        covered_days += 1
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t_raw = _pick_row_value(row, "time", "timestamp", "datetime", "date")
                c_raw = _pick_row_value(row, "close", "current", "lastprice", "last_price", "price")
                if not t_raw or not c_raw:
                    continue
                try:
                    dt = _parse_tick_time_any(t_raw)
                    close = float(str(c_raw))
                except Exception:
                    continue
                if close <= 0 or (not _is_trading_time(dt)):
                    continue
                bucket = _hour_bucket_30m(dt)
                hourly_close[bucket] = close
    points = sorted(hourly_close.items(), key=lambda x: x[0])
    return points, covered_days


def _month_ticks(start_dt: datetime, end_dt: datetime) -> list[datetime]:
    cur = datetime(start_dt.year, start_dt.month, 1)
    if cur < start_dt:
        if cur.month == 12:
            cur = datetime(cur.year + 1, 1, 1)
        else:
            cur = datetime(cur.year, cur.month + 1, 1)
    out: list[datetime] = []
    while cur <= end_dt:
        out.append(cur)
        if cur.month == 12:
            cur = datetime(cur.year + 1, 1, 1)
        else:
            cur = datetime(cur.year, cur.month + 1, 1)
    if len(out) > 14:
        out = out[::2]
    return out


def _safe_scale(v: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    if hi <= lo:
        return (out_lo + out_hi) * 0.5
    ratio = (v - lo) / (hi - lo)
    return out_lo + ratio * (out_hi - out_lo)


def _svg_for_code(
    *,
    code: str,
    points: list[tuple[datetime, float]],
    fills: list[FillPoint],
    start_dt: datetime,
    end_dt: datetime,
) -> str:
    w = 1800
    h = 760
    ml, mr, mt, mb = 90, 30, 60, 95
    pw = w - ml - mr
    ph = h - mt - mb

    if not points:
        esc_code = html.escape(code)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
            '<rect width="100%" height="100%" fill="#ffffff"/>'
            f'<text x="40" y="80" font-size="28" fill="#111111">{esc_code} - 无小时数据</text>'
            f'<text x="40" y="130" font-size="18" fill="#666666">period: {start_dt:%Y-%m-%d} to {end_dt:%Y-%m-%d}</text>'
            "</svg>"
        )

    x0 = points[0][0].timestamp()
    x1 = points[-1][0].timestamp()
    if x1 <= x0:
        x1 = x0 + 1.0

    ys: list[float] = [p[1] for p in points]
    ys.extend([f.price for f in fills if math.isfinite(f.price)])
    y_min = min(ys)
    y_max = max(ys)
    if y_max <= y_min:
        y_max = y_min + max(0.01, y_min * 0.01)
    pad = (y_max - y_min) * 0.08
    y_min -= pad
    y_max += pad

    def sx(dt: datetime) -> float:
        return _safe_scale(dt.timestamp(), x0, x1, ml, ml + pw)

    def sy(v: float) -> float:
        return _safe_scale(v, y_min, y_max, mt + ph, mt)

    path_parts: list[str] = []
    for i, (dt, v) in enumerate(points):
        x = sx(dt)
        y = sy(v)
        if i == 0:
            path_parts.append(f"M{x:.2f},{y:.2f}")
        else:
            path_parts.append(f"L{x:.2f},{y:.2f}")
    line_path = " ".join(path_parts)

    items: list[str] = []
    items.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">')
    items.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    items.append(
        f'<text x="{ml}" y="34" font-size="26" font-family="Segoe UI, Arial, sans-serif" fill="#111111">'
        f'{html.escape(code)} hourly close with trades ({start_dt:%Y-%m-%d} to {end_dt:%Y-%m-%d})</text>'
    )

    # Grid + y ticks
    for i in range(6):
        val = y_min + (y_max - y_min) * i / 5.0
        y = sy(val)
        items.append(f'<line x1="{ml}" y1="{y:.2f}" x2="{ml + pw}" y2="{y:.2f}" stroke="#e5e7eb" stroke-width="1"/>')
        items.append(
            f'<text x="{ml - 10}" y="{y + 5:.2f}" text-anchor="end" font-size="14" '
            f'font-family="Consolas, monospace" fill="#4b5563">{val:.4f}</text>'
        )

    # x ticks (month starts)
    for tick in _month_ticks(start_dt=points[0][0], end_dt=points[-1][0]):
        tx = sx(tick)
        items.append(f'<line x1="{tx:.2f}" y1="{mt}" x2="{tx:.2f}" y2="{mt + ph}" stroke="#f3f4f6" stroke-width="1"/>')
        items.append(
            f'<text x="{tx:.2f}" y="{mt + ph + 28}" text-anchor="middle" font-size="13" '
            f'font-family="Consolas, monospace" fill="#4b5563">{tick:%Y-%m}</text>'
        )

    items.append(f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="none" stroke="#111827" stroke-width="1"/>')
    items.append(f'<path d="{line_path}" fill="none" stroke="#1d4ed8" stroke-width="1.4"/>')

    # hourly points with timestamp tooltip
    for dt, v in points:
        x = sx(dt)
        y = sy(v)
        tip = f"{code} {dt:%Y-%m-%d %H:%M:%S} close={v:.4f}"
        items.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.8" fill="#1d4ed8" opacity="0.45"><title>{html.escape(tip)}</title></circle>'
        )

    # trades markers
    for fp in fills:
        if fp.price <= 0:
            continue
        if fp.timestamp < points[0][0] or fp.timestamp > points[-1][0]:
            continue
        x = sx(fp.timestamp)
        y = sy(fp.price)
        if fp.side == "BUY":
            pts = f"{x:.2f},{y - 7:.2f} {x - 6:.2f},{y + 5:.2f} {x + 6:.2f},{y + 5:.2f}"
            color = "#16a34a"
        else:
            pts = f"{x:.2f},{y + 7:.2f} {x - 6:.2f},{y - 5:.2f} {x + 6:.2f},{y - 5:.2f}"
            color = "#dc2626"
        tip = (
            f"{code} {fp.side} {fp.timestamp:%Y-%m-%d %H:%M:%S} "
            f"qty={fp.quantity} price={fp.price:.4f} amount={fp.amount:.2f} order={fp.order_id}"
        )
        items.append(
            f'<polygon points="{pts}" fill="{color}" stroke="#111827" stroke-width="0.8"><title>{html.escape(tip)}</title></polygon>'
        )

    lx = ml + 12
    ly = mt + 20
    items.append(f'<line x1="{lx}" y1="{ly}" x2="{lx + 24}" y2="{ly}" stroke="#1d4ed8" stroke-width="2"/>')
    items.append(
        f'<text x="{lx + 32}" y="{ly + 5}" font-size="14" font-family="Segoe UI, Arial, sans-serif" fill="#1f2937">hourly close</text>'
    )
    by = ly + 22
    items.append(
        f'<polygon points="{lx + 12},{by - 7} {lx + 6},{by + 5} {lx + 18},{by + 5}" fill="#16a34a" stroke="#111827" stroke-width="0.8"/>'
    )
    items.append(
        f'<text x="{lx + 32}" y="{by + 5}" font-size="14" font-family="Segoe UI, Arial, sans-serif" fill="#1f2937">BUY fill</text>'
    )
    sy0 = by + 22
    items.append(
        f'<polygon points="{lx + 12},{sy0 + 7} {lx + 6},{sy0 - 5} {lx + 18},{sy0 - 5}" fill="#dc2626" stroke="#111827" stroke-width="0.8"/>'
    )
    items.append(
        f'<text x="{lx + 32}" y="{sy0 + 5}" font-size="14" font-family="Segoe UI, Arial, sans-serif" fill="#1f2937">SELL fill</text>'
    )

    items.append("</svg>")
    return "\n".join(items)


def _write_hourly_csv(path: Path, points: list[tuple[datetime, float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "close"])
        for dt, v in points:
            w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), f"{v:.6f}"])


def _write_trade_csv(path: Path, fills: list[FillPoint]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "side", "quantity", "price", "amount", "order_id"])
        for fp in fills:
            w.writerow(
                [
                    fp.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    fp.side,
                    fp.quantity,
                    f"{fp.price:.6f}",
                    f"{fp.amount:.2f}",
                    fp.order_id,
                ]
            )


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python scripts/plot_backtest_hourly_svg.py")
    p.add_argument("--run-dir", default="output/backtest_cmp30_phase2", help="backtest output dir (contains fills.csv/summary.json)")
    p.add_argument("--tick-root", default="etf_chip_engine/data/l1_snapshots", help="tick snapshot root by day")
    p.add_argument("--out-dir", default="output/hourly_charts", help="output dir for svg/csv/index")
    p.add_argument("--start", default="", help="start date YYYYMMDD (default: from summary.json)")
    p.add_argument("--end", default="", help="end date YYYYMMDD (default: from summary.json)")
    p.add_argument("--codes", default="", help="comma separated codes, e.g. 512480.SH,159363.SZ")
    p.add_argument("--codes-file", default="", help="file that contains codes")
    p.add_argument("--max-codes", type=int, default=0, help="limit codes for quick debug (0=all)")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_arg_parser().parse_args(argv)
    run_dir = Path(str(ns.run_dir))
    tick_root = Path(str(ns.tick_root))
    out_dir = Path(str(ns.out_dir))
    charts_dir = out_dir / "svg"
    data_dir = out_dir / "data"
    charts_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise RuntimeError(f"summary.json not found: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    start = str(ns.start or summary.get("start_date") or "").strip().replace("-", "")
    end = str(ns.end or summary.get("end_date") or "").strip().replace("-", "")
    if len(start) != 8 or not start.isdigit() or len(end) != 8 or not end.isdigit():
        raise RuntimeError("invalid start/end date, expect YYYYMMDD")

    if ns.codes_file:
        codes = _read_codes_file(Path(str(ns.codes_file)))
    elif str(ns.codes).strip():
        raw = str(ns.codes).replace(" ", ",").split(",")
        codes = [_normalize_code(x) for x in raw if _normalize_code(x)]
    else:
        codes = _default_codes()

    if int(ns.max_codes) > 0:
        codes = codes[: int(ns.max_codes)]

    if not tick_root.exists():
        raise RuntimeError(f"tick root not found: {tick_root}")
    trade_days = _list_trade_days(tick_root=tick_root, start=start, end=end)
    if not trade_days:
        raise RuntimeError(f"no trade day dirs under {tick_root} for {start}..{end}")
    if not codes:
        codes = _infer_codes_from_tick_root(tick_root=tick_root, trade_days=trade_days)
    if not codes:
        raise RuntimeError("codes is empty; pass --codes or --codes-file")

    fills_by_code = _load_fills(run_dir / "fills.csv", start=start, end=end)
    start_dt = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d") + timedelta(hours=23, minutes=59, seconds=59)

    index_rows: list[dict[str, object]] = []
    print(f"plot start | codes={len(codes)} days={len(trade_days)} run={run_dir}")
    for i, code in enumerate(codes, start=1):
        t0 = time.time()
        points, covered_days = _load_hourly_close_for_code(tick_root=tick_root, trade_days=trade_days, code=code)
        fills = list(fills_by_code.get(code, []))

        svg = _svg_for_code(code=code, points=points, fills=fills, start_dt=start_dt, end_dt=end_dt)
        svg_name = f"{code}.svg"
        svg_path = charts_dir / svg_name
        svg_path.write_text(svg, encoding="utf-8")

        _write_hourly_csv(data_dir / f"{code}_hourly.csv", points)
        _write_trade_csv(data_dir / f"{code}_trades.csv", fills)

        secs = time.time() - t0
        index_rows.append(
            {
                "code": code,
                "hourly_points": len(points),
                "trade_count": len(fills),
                "covered_days": covered_days,
                "svg": f"svg/{svg_name}",
                "hourly_csv": f"data/{code}_hourly.csv",
                "trade_csv": f"data/{code}_trades.csv",
            }
        )
        print(
            f"[{i:02d}/{len(codes):02d}] {code} done | points={len(points)} trades={len(fills)} covered_days={covered_days} cost={secs:.1f}s"
        )

    report_csv = out_dir / "index.csv"
    with report_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["code", "hourly_points", "trade_count", "covered_days", "svg", "hourly_csv", "trade_csv"],
        )
        w.writeheader()
        for row in index_rows:
            w.writerow(row)

    html_lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'><title>Backtest Hourly Charts</title>",
        "<style>",
        "body{font-family:Segoe UI,Arial,sans-serif;padding:16px;background:#f8fafc;color:#0f172a;}",
        "table{border-collapse:collapse;width:100%;background:#fff;}",
        "th,td{border:1px solid #e2e8f0;padding:8px;font-size:13px;}",
        "th{background:#f1f5f9;}",
        "a{color:#1d4ed8;text-decoration:none;}",
        "a:hover{text-decoration:underline;}",
        "</style></head><body>",
        f"<h2>Backtest Hourly Charts ({start} - {end})</h2>",
        f"<p>run_dir={html.escape(str(run_dir))}</p>",
        "<table><thead><tr><th>Code</th><th>Hourly Points</th><th>Trades</th><th>Covered Days</th><th>SVG</th><th>Hourly CSV</th><th>Trades CSV</th></tr></thead><tbody>",
    ]
    for row in index_rows:
        html_lines.append(
            "<tr>"
            f"<td>{html.escape(str(row['code']))}</td>"
            f"<td>{row['hourly_points']}</td>"
            f"<td>{row['trade_count']}</td>"
            f"<td>{row['covered_days']}</td>"
            f"<td><a href='{row['svg']}' target='_blank'>open</a></td>"
            f"<td><a href='{row['hourly_csv']}' target='_blank'>open</a></td>"
            f"<td><a href='{row['trade_csv']}' target='_blank'>open</a></td>"
            "</tr>"
        )
    html_lines.append("</tbody></table></body></html>")
    (out_dir / "index.html").write_text("\n".join(html_lines), encoding="utf-8")

    print(f"done | output={out_dir} index={out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
