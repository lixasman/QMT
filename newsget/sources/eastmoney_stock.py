from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from ..http import get_text
from ..models import pick_first


@dataclass(frozen=True)
class StockLatestNews:
    title: str
    url: str
    publish_time: Optional[str]


def fetch_stock_latest_news_from_eastmoney(
    session: requests.Session,
    *,
    stock_code: str,
    stock_name: str,
    max_age_days: int = 3,
) -> Optional[StockLatestNews]:
    keyword = f"{stock_name} {stock_code}".strip()
    q = requests.utils.quote(keyword)
    urls = [
        "https://search-api.eastmoney.com/bussiness/Web/GetSearchList"
        f"?cb=cb&type=701&pageindex=1&pagesize=1&name=normal&keyword={q}",
        "https://api.so.eastmoney.com/bussiness/Web/GetSearchList"
        f"?cb=cb&type=701&pageindex=1&pagesize=1&name=normal&keyword={q}",
    ]

    allow_insecure = os.environ.get("EASTMONEY_INSECURE_SSL", "").strip().lower() in {"1", "true", "yes", "y"}
    last_err: Optional[BaseException] = None
    data = None
    for u in urls:
        try:
            text = get_text(
                session,
                u,
                headers={
                    "Referer": "https://so.eastmoney.com/news/s",
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "*/*",
                },
                timeout_seconds=25.0,
            )
            data = _parse_jsonp(text)
            if isinstance(data, dict):
                break
        except requests.exceptions.SSLError as e:
            last_err = e
            if "api.so.eastmoney.com" in u and allow_insecure:
                try:
                    text = get_text(
                        session,
                        u,
                        headers={
                            "Referer": "https://so.eastmoney.com/news/s",
                            "User-Agent": "Mozilla/5.0",
                            "Accept": "*/*",
                        },
                        timeout_seconds=25.0,
                        verify=False,
                    )
                    data = _parse_jsonp(text)
                    if isinstance(data, dict):
                        break
                except Exception as e2:
                    last_err = e2
            continue
        except Exception as e:
            last_err = e
            continue

    if not isinstance(data, dict):
        if last_err:
            raise last_err
        return None
    lst = data.get("Data")
    if not isinstance(lst, list) or not lst:
        return None
    item = lst[0]
    if not isinstance(item, dict):
        return None

    title = str(pick_first(item, ["Title", "title"]) or "").strip()
    href = str(pick_first(item, ["Url", "url", "LinkUrl", "linkUrl"]) or "").strip()
    pub = pick_first(item, ["DateTime", "ShowTime", "CreateTime", "showTime", "createTime"])
    pub_str = str(pub).strip() if pub not in (None, "") else None
    full_url = _normalize_url(href)
    if not title or not full_url:
        return None

    if pub_str and max_age_days > 0:
        if not _is_within_days(pub_str, days=max_age_days):
            return None

    return StockLatestNews(title=title, url=full_url, publish_time=pub_str)


def _parse_jsonp(text: str) -> Any:
    s = (text or "").strip()
    if not s:
        return None
    if s.startswith("{"):
        return json.loads(s)
    m = re.search(r"^[a-zA-Z0-9_]+\((\{[\s\S]*\})\)\s*;?\s*$", s)
    if not m:
        m = re.search(r"(\{[\s\S]*\})", s)
    if not m:
        return None
    return json.loads(m.group(1))


def _normalize_url(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://so.eastmoney.com" + href
    if href.startswith("http"):
        return href
    return None


def _is_within_days(ts: str, *, days: int) -> bool:
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz=tz)
    dt = _parse_time(ts, tz=tz)
    if not dt:
        return False
    return now - dt <= timedelta(days=days)


def _parse_time(ts: str, *, tz: timezone) -> Optional[datetime]:
    ts = ts.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts, fmt)
            return dt.replace(tzinfo=tz)
        except Exception:
            continue
    return None
