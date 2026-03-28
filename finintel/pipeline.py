from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

import requests

from core.warn_utils import warn_once
from newsget.ingestion import fetch_top10_news
from newsget.models import NewsItem

from .deepseek_client import DeepSeekClient
from .prompts import PROMPT_A, PROMPT_B

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Phase2Result:
    item: NewsItem
    summary: str


def build_raw_news_text(item: NewsItem) -> str:
    parts: list[str] = []
    parts.append(f"source: {item.source}")
    parts.append(f"title: {item.title}")
    if item.publish_time:
        parts.append(f"publish_time: {item.publish_time}")
    parts.append(f"url: {item.url}")
    if item.content:
        parts.append("")
        parts.append(item.content)
    return "\n".join(parts).strip()


def render_prompt(template: str, *, raw_news_text: Optional[str] = None, summaries_list: Optional[str] = None) -> str:
    out = template
    if raw_news_text is not None:
        out = out.replace("{{raw_news_text}}", raw_news_text)
    if summaries_list is not None:
        out = out.replace("{{summaries_list}}", summaries_list)
    return out


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception as e:
        warn_once(
            "finintel_extract_json_fallback",
            f"FinIntel: DeepSeek 返回非严格JSON，已降级使用正则提取JSON片段。err={repr(e)}",
            logger_name=__name__,
        )

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("无法从返回内容中提取 JSON 对象")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("返回 JSON 不是对象")
    return obj


def run_pipeline(
    session: requests.Session,
    deepseek: DeepSeekClient,
    *,
    debug: bool = False,
    max_workers: Optional[int] = None,
) -> dict[str, Any]:
    items = fetch_top10_news(session, include_content=True, debug=debug)
    if not items:
        logger.error("Phase1: 未获取到任何新闻，停止执行")
        raise RuntimeError("Phase1 数据获取失败：未获取到任何新闻")

    max_workers = max_workers or int(os.environ.get("PHASE2_MAX_WORKERS", "8"))
    results: list[Phase2Result] = []
    errors: list[dict[str, str]] = []

    def worker(it: NewsItem) -> Phase2Result:
        raw_text = build_raw_news_text(it)
        prompt = render_prompt(PROMPT_A, raw_news_text=raw_text)
        summary = deepseek.chat(system="你是一个严谨的金融信息抽取助手。", user=prompt, temperature=0.2, force_json=False)
        return Phase2Result(item=it, summary=summary.strip() or "该新闻无可提取的金融信息")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(worker, it): it for it in items}
        for fut in as_completed(futures):
            it = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                logger.warning("Phase2: 摘要生成失败 source=%s url=%s err=%s", it.source, it.url, repr(e))
                errors.append({"phase": "phase2", "source": it.source, "url": it.url, "error": repr(e)})
                results.append(Phase2Result(item=it, summary="该新闻无可提取的金融信息"))

    results.sort(key=lambda r: (r.item.source, r.item.rank))
    summaries = [r.summary for r in results]
    summaries_json = json.dumps(summaries, ensure_ascii=False, indent=2)

    prompt_b = render_prompt(PROMPT_B, summaries_list=summaries_json)
    try:
        raw_b = deepseek.chat(system="只输出 JSON。", user=prompt_b, temperature=0.0, force_json=True)
    except Exception as e:
        logger.error("Phase3: 聚合筛选调用失败 err=%s", repr(e))
        raise

    try:
        obj = extract_json_object(raw_b)
        _validate_top3_shape(obj)
    except Exception as e:
        logger.error("Phase3: 返回 JSON 校验失败 err=%s raw=%s", repr(e), raw_b[:500])
        raise

    return {
        "top_3": obj["top_3"],
        "errors": errors,
        "raw_news": [it.to_dict() for it in items],
        "summaries": summaries,
    }


def _validate_top3_shape(obj: dict[str, Any]) -> None:
    if "top_3" not in obj or not isinstance(obj["top_3"], list):
        raise ValueError("缺少 top_3 数组")
    if len(obj["top_3"]) != 3:
        raise ValueError("top_3 必须包含 3 条")
    for x in obj["top_3"]:
        if not isinstance(x, dict):
            raise ValueError("top_3 条目必须为对象")
        if not isinstance(x.get("rank"), int):
            raise ValueError("rank 必须为整数")
        if not isinstance(x.get("summary"), str):
            raise ValueError("summary 必须为字符串")
        if not isinstance(x.get("reason"), str):
            raise ValueError("reason 必须为字符串")
