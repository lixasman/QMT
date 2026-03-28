from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from entry.watchlist import filter_watchlist
from integrations.premarket_prep import finintel_hot_csv_path, prev_trading_date
from integrations.watchlist_loader import load_watchlist_items, normalize_etf_code


def _parse_day(s: str) -> datetime:
    t = str(s or "").strip()
    if not t:
        raise ValueError("empty date")
    if len(t) == 8 and t.isdigit():
        return datetime.strptime(t, "%Y%m%d")
    return datetime.strptime(t, "%Y-%m-%d")


def _fmt_day(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _load_hot_codes(path: Path, *, top_n: int) -> list[str]:
    out: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            c = str(row.get("code") or "").strip()
            if not c:
                continue
            out.append(c)
            if top_n > 0 and len(out) >= int(top_n):
                break
    return out


def _reason(sentiment_score: int, profit_ratio: float) -> str:
    parts: list[str] = []
    if int(sentiment_score) < 60:
        parts.append(f"sentiment<60({int(sentiment_score)})")
    if float(profit_ratio) < 75.0:
        parts.append(f"profit_ratio<75({float(profit_ratio):.2f})")
    return "; ".join(parts) if parts else "pass"


def _ordered_items(items: Iterable, order_codes: list[str]):
    by_code = {str(x.etf_code): x for x in items}
    out = []
    for c in order_codes:
        cn = normalize_etf_code(str(c))
        it = by_code.get(str(cn))
        if it is not None:
            out.append(it)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="模拟明日开盘 watch-auto 选池，并评估今日 TopN 的入池情况")
    p.add_argument("--open-date", default="", help="模拟开盘日期，YYYYMMDD 或 YYYY-MM-DD；默认明天")
    p.add_argument("--hot-day", default="", help="可选：指定 hot CSV 日期（YYYYMMDD）；为空则按开盘逻辑取 T-1 交易日")
    p.add_argument("--top-n", type=int, default=15, help="从 hot CSV 读取前 N 只，默认 15")
    p.add_argument("--integration-dir", default="output/integration", help="integration 根目录")
    p.add_argument("--output", default="", help="可选：将结果写入 JSON 文件")
    args = p.parse_args(argv)

    open_dt = (_parse_day(args.open_date) if str(args.open_date).strip() else (datetime.now().astimezone() + timedelta(days=1)))
    hot_day = str(args.hot_day).strip() or prev_trading_date(open_dt)
    if not hot_day:
        raise SystemExit("无法解析 T-1 交易日，请用 --hot-day 指定")

    hot_csv = finintel_hot_csv_path(day=hot_day)
    if not hot_csv.exists():
        raise SystemExit(f"未找到 hot CSV: {hot_csv}")

    hot_codes = _load_hot_codes(hot_csv, top_n=int(args.top_n))
    if not hot_codes:
        raise SystemExit(f"hot CSV 无可用代码: {hot_csv}")

    res = load_watchlist_items(etf_codes=hot_codes, now=open_dt, integration_dir=args.integration_dir)
    base_items = _ordered_items(res.items, hot_codes)
    passed_items = filter_watchlist(base_items)
    passed_codes = {str(x.etf_code) for x in passed_items}

    rows: list[dict[str, object]] = []
    for i, it in enumerate(base_items, start=1):
        keep = str(it.etf_code) in passed_codes
        rows.append(
            {
                "rank_in_hot": int(i),
                "etf_code": str(it.etf_code),
                "sentiment_score": int(it.sentiment_score),
                "profit_ratio": float(it.profit_ratio),
                "micro_caution": bool(it.micro_caution),
                "enter_watchlist": bool(keep),
                "reason": _reason(int(it.sentiment_score), float(it.profit_ratio)),
                "extra": dict(it.extra),
            }
        )

    head = (
        f"模拟开盘日={_fmt_day(open_dt)} | hot_day={hot_day} | "
        f"top_n={len(base_items)} | 入池={sum(1 for x in rows if x['enter_watchlist'])}"
    )
    print(head)
    print("-" * len(head))
    for r in rows:
        print(
            f"{int(r['rank_in_hot']):02d}. {r['etf_code']} | sentiment={int(r['sentiment_score'])} "
            f"| profit_ratio={float(r['profit_ratio']):.2f} | micro_caution={bool(r['micro_caution'])} "
            f"| enter={bool(r['enter_watchlist'])} | {r['reason']}"
        )

    if str(args.output).strip():
        out_path = Path(str(args.output))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "open_date": _fmt_day(open_dt),
            "hot_day": str(hot_day),
            "hot_csv": str(hot_csv),
            "chip_csv_path": (res.chip_csv_path or ""),
            "rows": rows,
            "watchlist_items": [asdict(x) for x in passed_items],
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
