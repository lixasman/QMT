from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

import requests

from newsget.models import NewsItem, now_iso
from newsget.sources.cls import fetch_cls_article_content
from newsget.sources.cls_search import fetch_cls_latest_news_by_keyword
from newsget.sources.akshare_stock import fetch_stock_latest_news_from_akshare
from newsget.sources.eastmoney_etf import EtfHolding, fetch_etf_top10_holdings
from newsget.sources.eastmoney import fetch_eastmoney_article_content
from newsget.sources.eastmoney_stock import fetch_stock_latest_news_from_eastmoney

from .deepseek_client import DeepSeekClient
from .pipeline import build_raw_news_text, extract_json_object, render_prompt
from .prompts import PROMPT_A, PROMPT_ETF_B

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EtfPhase2Result:
    item: NewsItem
    summary: str


@dataclass(frozen=True)
class EtfPhase1Outcome:
    stock_code: str
    stock_name: str
    rank: int
    used_source: str
    status: str
    reason: str
    error: Optional[str]


def run_etf_pipeline(
    session: requests.Session,
    deepseek: DeepSeekClient,
    *,
    etf_code: str,
    max_age_days: int = 3,
    etf_source: str = "auto",
    max_workers: Optional[int] = None,
    debug: bool = False,
) -> dict[str, Any]:
    crawl_time = now_iso()
    etf_name, as_of, holdings = fetch_etf_top10_holdings(session, etf_code, topline=10)
    if not holdings:
        logger.error("ETF: 未获取到前十大持仓 etf=%s", etf_code)
        raise RuntimeError("ETF 持仓获取失败：无有效持仓")

    if etf_source == "akshare":
        try:
            import akshare  # type: ignore
        except Exception:
            logger.warning("ETF: etf_source=akshare 但未安装 akshare，Phase1 将无法获取新闻")

    news_items: list[NewsItem] = []
    phase1_outcomes: list[EtfPhase1Outcome] = []
    for idx, h in enumerate(holdings, start=1):
        it, meta = _fetch_latest_news_for_holding(
            session,
            h,
            rank=idx,
            crawl_time=crawl_time,
            max_age_days=max_age_days,
            etf_source=etf_source,
            debug=debug,
        )
        if it is not None:
            news_items.append(it)
        phase1_outcomes.append(
            EtfPhase1Outcome(
                stock_code=h.stock_code,
                stock_name=h.stock_name,
                rank=idx,
                used_source=str(meta.get("used_source") or ""),
                status=str(meta.get("status") or ""),
                reason=str(meta.get("reason") or ""),
                error=(str(meta.get("error")) if meta.get("error") else None),
            )
        )

    if not news_items:
        counts: dict[str, int] = {}
        for x in phase1_outcomes:
            key = f"{x.used_source}:{x.status}:{x.reason}"
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:6]
        logger.warning("ETF: Phase1 未获取到近%s天内有效新闻 etf=%s breakdown=%s", max_age_days, etf_code, top)

    max_workers = max_workers or int(os.environ.get("PHASE2_MAX_WORKERS", "8"))
    results: list[EtfPhase2Result] = []
    errors: list[dict[str, str]] = []

    def worker(it: NewsItem) -> EtfPhase2Result:
        raw_text = build_raw_news_text(it)
        prompt = render_prompt(PROMPT_A, raw_news_text=raw_text)
        summary = deepseek.chat(system="你是一个严谨的金融信息抽取助手。", user=prompt, temperature=0.2, force_json=False)
        return EtfPhase2Result(item=it, summary=summary.strip() or "该新闻无可提取的金融信息")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(worker, it): it for it in news_items}
        for fut in as_completed(futures):
            it = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                logger.warning("ETF Phase2: 摘要生成失败 url=%s err=%s", it.url, repr(e))
                errors.append({"phase": "phase2", "url": it.url, "error": repr(e)})
                results.append(EtfPhase2Result(item=it, summary="该新闻无可提取的金融信息"))

    results.sort(key=lambda r: r.item.rank)
    summaries = [r.summary for r in results]
    phase2 = [
        {
            "rank": r.item.rank,
            "source": r.item.source,
            "title": r.item.title,
            "url": r.item.url,
            "publish_time": r.item.publish_time,
            "summary": r.summary,
        }
        for r in results
    ]

    summaries_json = json.dumps(summaries, ensure_ascii=False, indent=2)
    prompt_b = PROMPT_ETF_B.replace("{{ETF_name}}", etf_name).replace("{{news_summaries_list}}", summaries_json)

    try:
        raw_b = deepseek.chat(system="只输出 JSON。", user=prompt_b, temperature=0.0, force_json=True)
    except Exception as e:
        logger.error("ETF Phase3: 聚合筛选调用失败 err=%s", repr(e))
        raise

    try:
        obj = extract_json_object(raw_b)
        _validate_top3_len_le3(obj)
    except Exception as e:
        logger.error("ETF Phase3: 返回 JSON 校验失败 err=%s raw=%s", repr(e), raw_b[:500])
        raise
    if summaries and not obj.get("top_3"):
        logger.warning("ETF Phase3: 有摘要但筛选结果为空 etf=%s summaries=%s", etf_code, len(summaries))

    return {
        "etf_code": etf_code,
        "etf_name": etf_name,
        "holdings_as_of": as_of,
        "top_3": obj.get("top_3", []),
        "errors": errors,
        "phase1": {
            "holdings_total": len(holdings),
            "news_items": len(news_items),
            "max_age_days": max_age_days,
            "outcomes": [x.__dict__ for x in phase1_outcomes],
        },
        "raw_news": [it.to_dict() for it in news_items],
        "summaries": summaries,
        "phase2": phase2,
        "phase3_prompt": prompt_b,
        "phase3_raw": raw_b,
    }


def _fetch_latest_news_for_holding(
    session: requests.Session,
    holding: EtfHolding,
    *,
    rank: int,
    crawl_time: str,
    max_age_days: int,
    etf_source: str,
    debug: bool,
) -> tuple[Optional[NewsItem], dict[str, Any]]:
    meta: dict[str, Any] = {"used_source": "", "status": "", "reason": "", "error": ""}

    if etf_source in {"auto", "akshare"}:
        try:
            latest_ak = fetch_stock_latest_news_from_akshare(stock_code=holding.stock_code, max_age_days=max_age_days)
        except Exception as e:
            logger.warning(
                "ETF Phase1: AKShare 新闻抓取失败 code=%s name=%s err=%s",
                holding.stock_code,
                holding.stock_name,
                repr(e),
            )
            latest_ak = None
        if latest_ak is not None:
            meta.update({"used_source": "akshare", "status": "ok", "reason": "", "error": ""})
            return NewsItem(
                source="东方财富",
                rank=rank,
                title=re.sub(r"\s+", " ", latest_ak.title).strip(),
                url=latest_ak.url,
                hot=None,
                publish_time=latest_ak.publish_time,
                content=latest_ak.content,
                crawl_time=crawl_time,
            ), meta
        if max_age_days > 0 and latest_ak is None:
            try:
                any_ak = fetch_stock_latest_news_from_akshare(stock_code=holding.stock_code, max_age_days=0)
            except Exception as e:
                logger.warning(
                    "ETF Phase1: AKShare 过期探测失败，已降级按无新闻处理 code=%s name=%s err=%s",
                    holding.stock_code,
                    holding.stock_name,
                    repr(e),
                )
                any_ak = None
            if any_ak is not None:
                meta.update({"used_source": "akshare", "status": "dropped", "reason": "too_old", "error": ""})
                return None, meta
        if latest_ak is None:
            meta.update({"used_source": "akshare", "status": "failed", "reason": "no_news_or_error", "error": ""})
        if etf_source == "akshare":
            return None, meta

    try:
        hit = fetch_cls_latest_news_by_keyword(
            session,
            keyword=f"{holding.stock_name} {holding.stock_code}",
            max_age_days=max_age_days,
        )
    except Exception as e:
        logger.warning(
            "ETF Phase1: 财联社新闻列表抓取失败 code=%s name=%s err=%s",
            holding.stock_code,
            holding.stock_name,
            repr(e),
        )
        hit = None

    if hit is None and etf_source == "cls":
        if max_age_days > 0:
            try:
                any_hit = fetch_cls_latest_news_by_keyword(
                    session, keyword=f"{holding.stock_name} {holding.stock_code}", max_age_days=0
                )
            except Exception as e:
                logger.warning(
                    "ETF Phase1: CLS 过期探测失败，已降级按无命中处理 code=%s name=%s err=%s",
                    holding.stock_code,
                    holding.stock_name,
                    repr(e),
                )
                any_hit = None
            if any_hit is not None:
                meta.update({"used_source": "cls", "status": "dropped", "reason": "too_old", "error": ""})
                return None, meta
        meta.update({"used_source": "cls", "status": "failed", "reason": "no_hit_or_error", "error": ""})
        return None, meta

    if hit is None and etf_source != "cls":
        if etf_source == "akshare":
            return None, meta
        try:
            latest = fetch_stock_latest_news_from_eastmoney(
                session,
                stock_code=holding.stock_code,
                stock_name=holding.stock_name,
                max_age_days=max_age_days,
            )
            if latest is None:
                if max_age_days > 0:
                    try:
                        any_latest = fetch_stock_latest_news_from_eastmoney(
                            session,
                            stock_code=holding.stock_code,
                            stock_name=holding.stock_name,
                            max_age_days=0,
                        )
                    except Exception as e:
                        logger.warning(
                            "ETF Phase1: 东财过期探测失败，已降级按无新闻处理 code=%s name=%s err=%s",
                            holding.stock_code,
                            holding.stock_name,
                            repr(e),
                        )
                        any_latest = None
                    if any_latest is not None:
                        meta.update({"used_source": "eastmoney", "status": "dropped", "reason": "too_old", "error": ""})
                        return None, meta
                meta.update({"used_source": "eastmoney", "status": "failed", "reason": "no_hit_or_error", "error": ""})
                return None, meta
            url = latest.url
            title = latest.title
            publish_time = latest.publish_time
        except Exception as e:
            logger.warning(
                "ETF Phase1: 东方财富新闻列表抓取失败 code=%s name=%s err=%s",
                holding.stock_code,
                holding.stock_name,
                repr(e),
            )
            meta.update({"used_source": "eastmoney", "status": "failed", "reason": "fetch_error", "error": repr(e)})
            return None, meta
        content = None
        try:
            content = fetch_eastmoney_article_content(session, url, debug=debug)
        except Exception as e:
            logger.warning("ETF Phase1: 东方财富正文抓取失败 code=%s url=%s err=%s", holding.stock_code, url, repr(e))

        meta.update({"used_source": "eastmoney", "status": "ok", "reason": "", "error": ""})
        return NewsItem(
            source="东方财富",
            rank=rank,
            title=re.sub(r"\s+", " ", title).strip(),
            url=url,
            hot=None,
            publish_time=publish_time,
            content=content,
            crawl_time=crawl_time,
        ), meta

    content = None
    try:
        content = fetch_cls_article_content(session, hit.url, debug=debug)
    except Exception as e:
        logger.warning("ETF Phase1: 财联社正文抓取失败 code=%s url=%s err=%s", holding.stock_code, hit.url, repr(e))

    meta.update({"used_source": "cls", "status": "ok", "reason": "", "error": ""})
    return NewsItem(
        source="财联社",
        rank=rank,
        title=re.sub(r"\s+", " ", hit.title).strip(),
        url=hit.url,
        hot=None,
        publish_time=hit.publish_time,
        content=content,
        crawl_time=crawl_time,
    ), meta


def _validate_top3_len_le3(obj: dict[str, Any]) -> None:
    if "top_3" not in obj or not isinstance(obj["top_3"], list):
        raise ValueError("缺少 top_3 数组")
    if len(obj["top_3"]) > 3:
        raise ValueError("top_3 至多包含 3 条")
    for x in obj["top_3"]:
        if not isinstance(x, dict):
            raise ValueError("top_3 条目必须为对象")
        if not isinstance(x.get("rank"), int):
            raise ValueError("rank 必须为整数")
        if not isinstance(x.get("summary"), str):
            raise ValueError("summary 必须为字符串")
        if not isinstance(x.get("reason"), str):
            raise ValueError("reason 必须为字符串")
