from __future__ import annotations

import logging
from dataclasses import replace
from typing import List

import requests

from .models import NewsItem
from .sources.cls import fetch_cls_article_content, fetch_cls_hot_top5
from .sources.eastmoney import fetch_eastmoney_article_content, fetch_eastmoney_hot_top5

logger = logging.getLogger(__name__)


def fetch_top10_news(
    session: requests.Session,
    *,
    include_content: bool = True,
    debug: bool = False,
) -> List[NewsItem]:
    items: list[NewsItem] = []
    try:
        items.extend(fetch_cls_hot_top5(session, debug=debug))
    except Exception as e:
        logger.warning("Phase1: 财联社抓取失败: %s", repr(e))
    try:
        items.extend(fetch_eastmoney_hot_top5(session, debug=debug))
    except Exception as e:
        logger.warning("Phase1: 东方财富抓取失败: %s", repr(e))

    if not include_content:
        return items

    enriched: list[NewsItem] = []
    for it in items:
        content = None
        try:
            if it.source == "财联社":
                content = fetch_cls_article_content(session, it.url, debug=debug)
            elif it.source == "东方财富":
                content = fetch_eastmoney_article_content(session, it.url, debug=debug)
        except Exception as e:
            logger.warning("Phase1: 正文抓取失败 source=%s url=%s err=%s", it.source, it.url, repr(e))
        enriched.append(replace(it, content=content))
    return enriched
