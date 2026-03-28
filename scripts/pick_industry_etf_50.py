from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable, Optional

import pandas as pd

try:
    from xtquant import xtdata  # type: ignore
except Exception as e:  # pragma: no cover
    xtdata = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from etf_chip_engine.data.xtdata_provider import get_all_etf_universe


@dataclass(frozen=True)
class Theme:
    name: str
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...] = ()


DEFAULT_EXCLUDE_NAME_KEYWORDS: tuple[str, ...] = (
    # Cash / bonds / money-market
    "货币",
    "现金",
    "快线",
    "债",
    "国债",
    "地方债",
    "可转债",
    "信用",
    "利率",
    "存单",
    "短融",
    "回购",
    # Commodity
    "黄金",
    "白银",
    "豆粕",
    "商品",
    "期货",
    "原油",
    # Overseas / QDII
    "QDII",
    "跨境",
    "海外",
    "美国",
    "纳斯达克",
    "标普",
    "道琼斯",
    "日经",
    "恒生",
    "香港",
    "港股",
    "中韩",
    "韩国",
    "H股",
    "中概",
    "德国",
    "法国",
    "巴西",
    "印度",
    "越南",
    # Not-ETF / wrappers
    "联接",
    "REIT",
)


THEMES_50: tuple[Theme, ...] = (
    # TMT / Tech
    Theme("半导体", ("半导体",), exclude_keywords=("设备",)),
    Theme("芯片", ("芯片",)),
    Theme("电子", ("电子",), exclude_keywords=("消费电子",)),
    Theme("消费电子", ("消费电子",)),
    Theme("软件", ("软件",), exclude_keywords=("工业软件",)),
    Theme("工业软件", ("工业软件",)),
    Theme("计算机", ("计算机",)),
    Theme("云计算", ("云计算",)),
    Theme("信创", ("信创",)),
    Theme("信息安全", ("信息安全", "安全")),
    Theme("通信", ("通信",), exclude_keywords=("5G",)),
    Theme("5G", ("5G",)),
    Theme("人工智能", ("人工智能", "AI")),
    Theme("机器人", ("机器人",)),
    Theme("互联网", ("互联网",)),
    Theme("传媒", ("传媒",)),
    # New energy / manufacturing
    Theme("新能源车", ("新能源车",)),
    Theme("智能汽车", ("智能汽车",)),
    Theme("电池", ("电池", "锂电")),
    Theme("光伏", ("光伏",)),
    Theme("储能", ("储能",)),
    Theme("新能源", ("新能源",), exclude_keywords=("新能源车",)),
    Theme("新材料", ("新材料",)),
    Theme("高端装备", ("高端装备", "装备")),
    Theme("工业母机", ("工业母机",)),
    Theme("机械", ("机械",)),
    # Defense / aerospace
    Theme("军工", ("军工",)),
    Theme("航天", ("航天",)),
    Theme("卫星", ("卫星",)),
    # Healthcare
    Theme("医药", ("医药",), exclude_keywords=("创新药", "生物医药", "中药", "医疗器械")),
    Theme("创新药", ("创新药",)),
    Theme("医疗器械", ("医疗器械",)),
    Theme("中药", ("中药",)),
    Theme("生物医药", ("生物医药", "生物")),
    # Consumer
    Theme("消费", ("消费",), exclude_keywords=("消费电子",)),
    Theme("食品饮料", ("食品饮料", "饮料")),
    Theme("酒", ("酒",)),
    Theme("家电", ("家电",)),
    Theme("农业", ("农业",)),
    Theme("养殖", ("养殖",)),
    # Finance / real estate / infra
    Theme("证券", ("证券", "券商")),
    Theme("银行", ("银行",)),
    Theme("保险", ("保险",)),
    Theme("地产", ("地产", "房地产")),
    Theme("基建", ("基建",)),
    # Cyclical / resources
    Theme("有色", ("有色",)),
    Theme("稀土", ("稀土",)),
    Theme("煤炭", ("煤炭",)),
    Theme("钢铁", ("钢铁",)),
    Theme("化工", ("化工",)),
)


def _chunked(xs: list[str], n: int) -> Iterable[list[str]]:
    if n <= 0:
        yield xs
        return
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def _filter_universe_by_name_keywords(df: pd.DataFrame, *, exclude_keywords: tuple[str, ...]) -> pd.DataFrame:
    out = df.copy()
    out["code"] = out["code"].astype(str)
    out["name"] = out.get("name", "").astype(str).fillna("")
    out = out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    if not exclude_keywords:
        return out
    pattern = "|".join(re.escape(x) for x in exclude_keywords if str(x).strip())
    if not pattern:
        return out
    out = out[~out["name"].astype(str).str.contains(pattern, regex=True, na=False)].copy()
    return out.reset_index(drop=True)


def _fetch_median_amount(
    codes: list[str],
    *,
    count: int,
    chunk_size: int,
) -> pd.Series:
    if xtdata is None:
        raise RuntimeError("xtquant.xtdata 不可用，无法计算成交额")
    if not codes:
        return pd.Series(dtype=float)

    medians: list[pd.Series] = []
    for chunk in _chunked(codes, int(chunk_size)):
        raw = xtdata.get_market_data(
            field_list=["amount"],
            stock_list=chunk,
            period="1d",
            start_time="",
            end_time="",
            count=int(count),
            dividend_type="none",
            fill_data=True,
        )
        amt_df = raw.get("amount")
        if amt_df is None or getattr(amt_df, "empty", True):
            continue
        if not isinstance(amt_df, pd.DataFrame):
            continue
        s = amt_df.replace(0, pd.NA).median(axis=1, skipna=True).fillna(0.0)
        s = s.astype(float)
        medians.append(s)

    if not medians:
        return pd.Series(dtype=float)

    out = pd.concat(medians, axis=0)
    out = out[~out.index.duplicated(keep="first")]
    out.name = "median_amount"
    return out


def _pick_for_theme(df: pd.DataFrame, theme: Theme, *, used: set[str]) -> Optional[pd.Series]:
    cand = df
    inc = [k for k in theme.keywords if str(k).strip()]
    if not inc:
        return None

    mask = pd.Series(False, index=cand.index)
    for k in inc:
        mask |= cand["name"].astype(str).str.contains(re.escape(k), regex=True, na=False)
    cand = cand[mask].copy()

    if theme.exclude_keywords and not cand.empty:
        ex_pattern = "|".join(re.escape(x) for x in theme.exclude_keywords if str(x).strip())
        if ex_pattern:
            cand = cand[~cand["name"].astype(str).str.contains(ex_pattern, regex=True, na=False)].copy()

    if cand.empty:
        return None

    cand = cand.sort_values(["median_amount", "code"], ascending=[False, True], kind="mergesort")
    for _, row in cand.iterrows():
        code = str(row["code"]).strip().upper()
        if not code or code in used:
            continue
        return row
    return None


def build_industry_50(
    *,
    themes: tuple[Theme, ...] = THEMES_50,
    exclude_name_keywords: tuple[str, ...] = DEFAULT_EXCLUDE_NAME_KEYWORDS,
    amount_days: int = 20,
    amount_chunk_size: int = 800,
) -> pd.DataFrame:
    universe = get_all_etf_universe()
    universe = _filter_universe_by_name_keywords(universe, exclude_keywords=exclude_name_keywords)

    codes = universe["code"].astype(str).tolist()
    med = _fetch_median_amount(codes, count=int(amount_days), chunk_size=int(amount_chunk_size))
    universe["median_amount"] = universe["code"].map(med).fillna(0.0).astype(float)

    used: set[str] = set()
    picked_rows: list[dict[str, object]] = []
    missing: list[str] = []
    for t in themes:
        row = _pick_for_theme(universe, t, used=used)
        if row is None:
            missing.append(t.name)
            continue
        code = str(row["code"]).strip().upper()
        used.add(code)
        picked_rows.append(
            {
                "theme": t.name,
                "code": code,
                "name": str(row.get("name") or ""),
                "median_amount": float(row.get("median_amount") or 0.0),
            }
        )

    out = pd.DataFrame(picked_rows, columns=["theme", "code", "name", "median_amount"])
    out = out.reset_index(drop=True)
    if missing:
        out.attrs["missing_themes"] = missing
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="选取 50 只代表性行业/主题 ETF（按主题匹配 + 近 N 日成交额中位数排序）")
    p.add_argument("--amount-days", type=int, default=20, help="成交额统计窗口（交易日数），默认 20")
    p.add_argument("--amount-chunk-size", type=int, default=800, help="xtdata.get_market_data 单次请求最大代码数，默认 800")
    p.add_argument("--out", default="", help="可选：输出 JSON 文件路径")
    args = p.parse_args(argv)

    df = build_industry_50(
        amount_days=int(args.amount_days),
        amount_chunk_size=int(args.amount_chunk_size),
    )
    missing = df.attrs.get("missing_themes", [])
    if missing:
        print(f"[WARN] missing themes ({len(missing)}): {', '.join(missing)}", flush=True)

    print(df[["theme", "code", "name"]].to_string(index=False))

    out = str(args.out).strip()
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "amount_days": int(args.amount_days),
            "themes": len(THEMES_50),
            "items": df[["theme", "code", "name"]].to_dict(orient="records"),
            "missing_themes": list(missing),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
