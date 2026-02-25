from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from ..http import get_json
from ..models import NewsItem, now_iso, pick_first

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClsSearchHit:
    title: str
    url: str
    publish_time: Optional[str]


def fetch_cls_latest_news_by_keyword(
    session: requests.Session,
    *,
    keyword: str,
    max_age_days: int = 3,
    page_size: int = 10,
) -> Optional[ClsSearchHit]:
    keyword = re.sub(r"\s+", " ", keyword or "").strip()
    if not keyword:
        return None

    data = _fetch_cls_search_api(session, keyword=keyword, page=1, page_size=page_size)
    if isinstance(data, dict) and data.get("errno") not in (0, "0", None):
        logger.warning("CLS 搜索失败 errno=%s msg=%s", data.get("errno"), data.get("msg"))
    hits = _extract_hits_from_any_json(data)
    if not hits:
        return None

    for hit in hits:
        if not hit.publish_time or max_age_days <= 0:
            return hit
        if _is_within_days(hit.publish_time, days=max_age_days):
            return hit
    return None


def build_cls_news_item_from_hit(hit: ClsSearchHit, *, rank: int) -> NewsItem:
    crawl_time = now_iso()
    return NewsItem(
        source="财联社",
        rank=rank,
        title=re.sub(r"\s+", " ", hit.title).strip(),
        url=hit.url,
        hot=None,
        publish_time=hit.publish_time,
        crawl_time=crawl_time,
    )


def _fetch_cls_search_api(session: requests.Session, *, keyword: str, page: int, page_size: int) -> Any:
    os = "android"
    sv = "835"
    app = "cailianpress"
    canonical = f"app={app}&os={os}&sv={sv}&keyword={keyword}&page={page}&rn={page_size}"
    sign = hashlib.md5(hashlib.sha1(canonical.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    url = "https://api3.cls.cn/search/v1/search"
    return get_json(
        session,
        url,
        params={"app": app, "os": os, "sv": sv, "keyword": keyword, "page": page, "rn": page_size, "sign": sign},
        headers={"Referer": "https://www.cls.cn/", "Accept": "application/json,*/*"},
        timeout_seconds=25.0,
    )


def _extract_hits_from_any_json(data: Any) -> list[ClsSearchHit]:
    if not isinstance(data, dict):
        return []
    if data.get("errno") not in (0, "0", None):
        return []

    candidates: list[dict[str, Any]] = []
    for k in ("data", "Data", "result", "Result"):
        v = data.get(k)
        if isinstance(v, list):
            candidates.extend(x for x in v if isinstance(x, dict))
        elif isinstance(v, dict):
            for kk in ("data", "Data", "list", "List", "items", "Items"):
                vv = v.get(kk)
                if isinstance(vv, list):
                    candidates.extend(x for x in vv if isinstance(x, dict))

    out: list[ClsSearchHit] = []
    for obj in candidates:
        title = pick_first(obj, ["title", "Title", "name", "Name"])
        id_ = pick_first(obj, ["id", "Id", "article_id", "articleId"])
        url = pick_first(obj, ["url", "Url", "share_url", "shareUrl"])
        pub = pick_first(obj, ["ctime", "time", "publish_time", "publishTime", "date", "Date"])
        title_s = str(title).strip() if title not in (None, "") else ""
        pub_s = str(pub).strip() if pub not in (None, "") else None

        final_url = None
        if url:
            final_url = str(url).strip()
            if final_url.startswith("//"):
                final_url = "https:" + final_url
        elif id_:
            final_url = f"https://api3.cls.cn/share/article/{id_}?os=ios&sv=835"

        if not title_s or not final_url:
            continue
        out.append(ClsSearchHit(title=title_s, url=final_url, publish_time=pub_s))
        if len(out) >= 20:
            break
    return out


def _is_within_days(ts: str, *, days: int) -> bool:
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz=tz)
    dt = _parse_time(ts, tz=tz)
    if not dt:
        return False
    return now - dt <= timedelta(days=days)


def _parse_time(ts: str, *, tz: timezone) -> Optional[datetime]:
    ts = ts.strip()
    if re.fullmatch(r"\d{10}", ts):
        try:
            return datetime.fromtimestamp(int(ts), tz=tz)
        except Exception:
            return None
    if re.fullmatch(r"\d{13}", ts):
        try:
            return datetime.fromtimestamp(int(ts) / 1000.0, tz=tz)
        except Exception:
            return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts, fmt)
            return dt.replace(tzinfo=tz)
        except Exception:
            continue
    return None
