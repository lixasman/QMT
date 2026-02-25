from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import WatchlistItem


def parse_watchlist_item(d: dict[str, Any]) -> WatchlistItem:
    etf_code = str(d.get("etf_code") or d.get("code") or "").strip()
    if not etf_code:
        raise AssertionError("watchlist item missing etf_code")
    sentiment_score = int(d.get("sentiment_score") if d.get("sentiment_score") is not None else d.get("sentiment") or 0)
    profit_ratio = float(d.get("profit_ratio") if d.get("profit_ratio") is not None else d.get("pr") or 0.0)
    nr = d.get("nearest_resistance")
    nearest_resistance = None if nr is None else float(nr)
    micro_caution = bool(d.get("micro_caution") or False)
    vpin_rank = d.get("vpin_rank")
    ofi_daily = d.get("ofi_daily")
    vs_max = d.get("vs_max")
    extra = {k: v for k, v in d.items() if k not in {"etf_code", "code", "sentiment_score", "sentiment", "profit_ratio", "pr", "nearest_resistance", "micro_caution", "vpin_rank", "ofi_daily", "vs_max"}}
    return WatchlistItem(
        etf_code=etf_code,
        sentiment_score=sentiment_score,
        profit_ratio=profit_ratio,
        nearest_resistance=nearest_resistance,
        micro_caution=micro_caution,
        vpin_rank=(None if vpin_rank is None else float(vpin_rank)),
        ofi_daily=(None if ofi_daily is None else float(ofi_daily)),
        vs_max=(None if vs_max is None else float(vs_max)),
        extra=extra,
    )


def load_watchlist(path: str | Path) -> list[WatchlistItem]:
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise AssertionError("watchlist json must be a list")
    items: list[WatchlistItem] = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        items.append(parse_watchlist_item(x))
    return items


def validate_watchlist(items: list[WatchlistItem]) -> None:
    seen: set[str] = set()
    for it in items:
        if it.etf_code in seen:
            raise AssertionError(f"duplicate etf_code in watchlist: {it.etf_code}")
        seen.add(it.etf_code)
        if not (0 <= int(it.sentiment_score) <= 100):
            raise AssertionError(f"sentiment_score out of range: {it.etf_code} {it.sentiment_score}")
        if not (0.0 <= float(it.profit_ratio) <= 100.0):
            raise AssertionError(f"profit_ratio out of range: {it.etf_code} {it.profit_ratio}")


def filter_watchlist(items: list[WatchlistItem]) -> list[WatchlistItem]:
    out: list[WatchlistItem] = []
    for it in items:
        if int(it.sentiment_score) < 60:
            continue
        if float(it.profit_ratio) < 75.0:
            continue
        micro_caution = bool(it.micro_caution)
        if it.vpin_rank is not None and float(it.vpin_rank) > 0.70:
            micro_caution = True
        if it.ofi_daily is not None and float(it.ofi_daily) < 0:
            micro_caution = True
        if micro_caution != it.micro_caution:
            out.append(
                WatchlistItem(
                    etf_code=it.etf_code,
                    sentiment_score=int(it.sentiment_score),
                    profit_ratio=float(it.profit_ratio),
                    nearest_resistance=it.nearest_resistance,
                    micro_caution=bool(micro_caution),
                    vpin_rank=it.vpin_rank,
                    ofi_daily=it.ofi_daily,
                    vs_max=it.vs_max,
                    extra=dict(it.extra),
                )
            )
        else:
            out.append(it)
    return out
