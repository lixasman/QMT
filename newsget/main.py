from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Iterable

from .http import HttpConfig, build_session
from .models import NewsItem, now_iso
from .sources.cls import fetch_cls_article_content, fetch_cls_hot_top5
from .sources.eastmoney import fetch_eastmoney_article_content, fetch_eastmoney_hot_top5


def _default_output_path() -> Path:
    ts = now_iso().replace(":", "").replace("-", "")
    safe = ts.replace("+", "_").replace(".", "_")
    return Path("output") / f"news_hot_{safe}.json"


def _print_items(items: Iterable[NewsItem]) -> None:
    for it in items:
        hot = f" | 热度: {it.hot}" if it.hot else ""
        pub = f" | 时间: {it.publish_time}" if it.publish_time else ""
        print(f"[{it.source}] #{it.rank} {it.title}{hot}{pub}\n  {it.url}")
        if it.content:
            print(it.content)
        print()


def _enrich_items_with_content(
    session, items: list[NewsItem], *, debug: bool, errors: list[dict[str, str]]
) -> list[NewsItem]:
    enriched: list[NewsItem] = []
    for it in items:
        content = None
        try:
            if it.source == "财联社":
                content = fetch_cls_article_content(session, it.url, debug=debug)
            elif it.source == "东方财富":
                content = fetch_eastmoney_article_content(session, it.url, debug=debug)
        except Exception as e:
            errors.append({"source": it.source, "error": f"content_fetch_failed: {it.url} {repr(e)}"})
        enriched.append(replace(it, content=content))
    return enriched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取财联社/东方财富热度 Top5 新闻")
    parser.add_argument(
        "--source",
        default="all",
        choices=["all", "cls", "eastmoney"],
        help="选择抓取来源（默认 all）",
    )
    parser.add_argument("--debug", action="store_true", help="输出调试信息到 stderr")
    parser.add_argument(
        "--output",
        default=str(_default_output_path()),
        help="输出 JSON 文件路径（默认 output/news_hot_<timestamp>.json）",
    )
    parser.add_argument("--no-file", action="store_true", help="不落盘，仅打印")
    parser.add_argument("--no-content", action="store_true", help="不抓取正文，仅标题/链接/热度等")
    args = parser.parse_args(argv)

    session = build_session(HttpConfig())

    errors: list[dict[str, str]] = []
    cls_items: list[NewsItem] = []
    em_items: list[NewsItem] = []

    if args.source in ("all", "cls"):
        try:
            cls_items = fetch_cls_hot_top5(session, debug=args.debug)
        except Exception as e:
            errors.append({"source": "财联社", "error": repr(e)})

    if args.source in ("all", "eastmoney"):
        try:
            em_items = fetch_eastmoney_hot_top5(session, debug=args.debug)
        except Exception as e:
            errors.append({"source": "东方财富", "error": repr(e)})

    all_items = cls_items + em_items
    if all_items and not args.no_content:
        all_items = _enrich_items_with_content(session, all_items, debug=args.debug, errors=errors)

    _print_items(all_items)
    if errors:
        for err in errors:
            print(f"[ERROR] {err.get('source')}: {err.get('error')}", file=sys.stderr)

    if not args.no_file:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "crawl_time": now_iso(),
            "items": [it.to_dict() for it in all_items],
            "errors": errors,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入: {out_path.resolve()}")

    return 0
