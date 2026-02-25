from __future__ import annotations

import re
import sys
import logging
from typing import Any, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.warn_utils import info_once
from ..http import get_json, get_text
from ..models import NewsItem, now_iso, pick_first


def fetch_eastmoney_hot_top5(session: requests.Session, *, debug: bool = False) -> List[NewsItem]:
    crawl_time = now_iso()

    entry_url = "https://vipmoney.eastmoney.com/collect/stockranking/pages/ranking9_3/list.html"
    headers = {"Referer": "https://vipmoney.eastmoney.com/"}

    html = get_text(session, entry_url, headers=headers)
    if "window.location.replace" in html and "collect/app_ranking/ranking/app.html" in html:
        return _fetch_hot_stock_trace(session, crawl_time=crawl_time)[:5]

    items = _try_parse_embedded_items(html, crawl_time=crawl_time)
    if items:
        return items[:5]

    script_urls = _extract_script_urls(html, base_url=entry_url)
    if debug:
        print(f"[EASTMONEY] scripts={len(script_urls)}", file=sys.stderr, flush=True)
        for u in script_urls[:8]:
            print(f"[EASTMONEY] script: {u}", file=sys.stderr, flush=True)

    for u in script_urls[:8]:
        if "static/script/ranking9_3/list/list_" not in u:
            continue
        try:
            js = get_text(session, u, headers={"Referer": entry_url}, timeout_seconds=25.0)
        except Exception:
            js = ""
        if "window.location.replace" in js and "collect/app_ranking/ranking/app.html" in js:
            return _fetch_hot_stock_trace(session, crawl_time=crawl_time)[:5]
    api_candidates = _discover_api_candidates(session, script_urls, referer=entry_url)
    if debug:
        print(f"[EASTMONEY] api_candidates={len(api_candidates)}", file=sys.stderr, flush=True)
        for u in api_candidates[:8]:
            print(f"[EASTMONEY] candidate: {u}", file=sys.stderr, flush=True)

    for api_url in api_candidates:
        parsed = _try_fetch_json_loose(session, api_url, referer=entry_url)
        if parsed is None:
            continue
        extracted = _extract_items_from_any_json(parsed, crawl_time=crawl_time)
        if extracted:
            return extracted[:5]

    html_items = _try_parse_html_items(html, crawl_time=crawl_time)
    if html_items:
        return html_items[:5]

    fallback = _fetch_hot_stock_trace(session, crawl_time=crawl_time)
    if debug and not fallback:
        print("[EASTMONEY] fallback empty", file=sys.stderr, flush=True)
    return fallback[:5]


def fetch_eastmoney_article_content(session: requests.Session, url: str, *, debug: bool = False) -> Optional[str]:
    headers = {"Referer": "https://finance.eastmoney.com/"}
    html = get_text(session, url, headers=headers, timeout_seconds=25.0)
    content = _extract_eastmoney_content_from_html(html)
    if debug and not content:
        print(f"[EASTMONEY] empty content: {url}", file=sys.stderr, flush=True)
    return content


def _extract_eastmoney_content_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script,style,noscript"):
        tag.decompose()

    selectors = [
        "#ContentBody",
        "div#ContentBody",
        "div.txtinfos",
        "div.newsContent",
        "div.Body",
        "div.body",
        "article",
    ]
    container = None
    for sel in selectors:
        el = soup.select_one(sel)
        if el and len(el.get_text(" ", strip=True)) > 100:
            container = el
            break
    if container is None:
        container = soup.body or soup

    for bad in container.select(
        "div.zwothers,div.sourcebox,div.sublab,div.sm,div.xgxw,div.relateNews,div.share,div.tool,div.editor,div.left"
    ):
        bad.decompose()

    text = container.get_text("\n", strip=True)
    lines = [ln.strip().replace("\u3000", " ").replace("\xa0", " ") for ln in text.splitlines()]
    cleaned: list[str] = []
    for ln in lines:
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln:
            continue
        if any(k in ln for k in ["专业金融数据", "全新妙想", "郑重声明", "风险自担", "责任编辑：", "原标题："]):
            continue
        cleaned.append(ln)

    out = "\n".join(cleaned).strip()
    return out or None


def _fetch_hot_stock_trace(session: requests.Session, *, crawl_time: str) -> list[NewsItem]:
    url = "https://stock.eastmoney.com/a/cggdj.html"
    html = get_text(session, url, headers={"Referer": "https://stock.eastmoney.com/"}, timeout_seconds=25.0)
    soup = BeautifulSoup(html, "html.parser")

    items: list[NewsItem] = []
    seen: set[str] = set()
    for a in soup.select("div.Wydj .tabList a[href]"):
        href = (a.get("href") or "").strip()
        title = (a.get_text() or "").strip()
        if not href or not title:
            continue
        if any(x in href for x in ["javascript:", "#"]):
            continue
        if "eastmoney.com" not in href and not href.startswith("/"):
            continue
        if href.startswith("/"):
            full = "https://stock.eastmoney.com" + href
        elif href.startswith("//"):
            full = "https:" + href
        else:
            full = href
        if full in seen:
            continue

        seen.add(full)
        items.append(
            NewsItem(
                source="东方财富",
                rank=len(items) + 1,
                title=re.sub(r"\s+", " ", title).strip(),
                url=full,
                hot=None,
                publish_time=None,
                crawl_time=crawl_time,
            )
        )
        if len(items) >= 5:
            break

    return items


def _extract_script_urls(html: str, *, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    base_href = base_tag.get("href") if base_tag else None
    resolve_base = urljoin(base_url, base_href) if base_href else base_url

    urls: list[str] = []
    for script in soup.find_all("script", src=True):
        src = (script.get("src") or "").strip().replace("\\", "/")
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        full = src if src.startswith("http") else urljoin(resolve_base, src)
        urls.append(full)

    return list(dict.fromkeys(urls))


def _discover_api_candidates(
    session: requests.Session,
    script_urls: Iterable[str],
    *,
    referer: str,
) -> list[str]:
    candidates: list[str] = []
    headers = {"Referer": referer}

    def norm(u: str) -> str:
        u = u.strip().strip('"').strip("'")
        return u.replace("\\u002F", "/").replace("\\/", "/")

    def add(u: str) -> None:
        u = norm(u)
        if not u:
            return
        lower = u.lower()
        if any(
            lower.endswith(ext)
            for ext in (
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".svg",
                ".css",
                ".js",
                ".woff",
                ".woff2",
                ".ttf",
                ".eot",
                ".ico",
            )
        ):
            return
        if any(
            host in lower
            for host in (
                "bdstatics.eastmoney.com",
                "bdwblog.eastmoney.com",
                "emdcadvertise.eastmoney.com",
            )
        ):
            return
        if u.startswith("//"):
            u = "https:" + u
        candidates.append(u)

    for u in list(script_urls)[:12]:
        try:
            js = get_text(session, u, headers=headers, timeout_seconds=25.0)
        except Exception:
            continue

        for m in re.finditer(r"https?://[a-z0-9.-]+eastmoney\.com/[0-9A-Za-z_./?=&%-]+", js):
            add(m.group(0))
        for m in re.finditer(r"https?:\\/\\/[a-z0-9.-]+eastmoney\\.com[0-9A-Za-z_\\\\/./?=&%-]+", js):
            add(m.group(0))

        for m in re.finditer(r'"/collectapi/[0-9A-Za-z_./?=&%-]+"', js):
            rel = norm(m.group(0))
            add("https://vipmoney.eastmoney.com" + rel.strip('"'))
        for m in re.finditer(r'"\\/collectapi\\/[0-9A-Za-z_./?=&%-]+"', js):
            rel = norm(m.group(0))
            add("https://vipmoney.eastmoney.com" + rel.strip('"'))

        for m in re.finditer(r'"collectapi/[0-9A-Za-z_./?=&%-]+"', js):
            rel = norm(m.group(0))
            add("https://vipmoney.eastmoney.com/" + rel.strip('"'))
        for m in re.finditer(r'"collectapi\\/[0-9A-Za-z_./?=&%-]+"', js):
            rel = norm(m.group(0))
            add("https://vipmoney.eastmoney.com/" + rel.strip('"'))

        for m in re.finditer(r'"/api/[0-9A-Za-z_./?=&%-]+"', js):
            rel = norm(m.group(0)).strip('"')
            add("https://vipmoney.eastmoney.com" + rel)
        for m in re.finditer(r'"\\/api\\/[0-9A-Za-z_./?=&%-]+"', js):
            rel = norm(m.group(0)).strip('"')
            add("https://vipmoney.eastmoney.com" + rel)

    def score(url: str) -> int:
        lower = url.lower()
        s = 0
        if "collectapi" in lower or "/api/" in lower:
            s += 3
        if any(k in lower for k in ["information", "info", "news", "article", "content", "msg", "notice", "zx"]):
            s += 3
        if any(k in lower for k in ["rank", "hot", "top", "list"]):
            s += 1
        if any(k in lower for k in ["stockrank", "guba", "topic", "kline", "ulist.np", "stock/get"]):
            s -= 4
        return -s

    uniq = list(dict.fromkeys(candidates))
    uniq.sort(key=score)
    return uniq


def _try_fetch_json_loose(
    session: requests.Session,
    url: str,
    *,
    referer: str,
) -> Optional[Any]:
    lower = url.lower()
    if any(
        lower.endswith(ext)
        for ext in (
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".svg",
            ".css",
            ".js",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".ico",
        )
    ):
        return None
    headers = {"Referer": referer, "Accept": "application/json,*/*"}
    try:
        return get_json(session, url, headers=headers, timeout_seconds=25.0)
    except Exception as e:
        info_once(f"newsget_eastmoney_json_failed:{url}", f"NewsGet: 东方财富 JSON 接口请求失败，已降级到文本解析: url={url} err={repr(e)}", logger_name=__name__)

    try:
        text = get_text(session, url, headers=headers, timeout_seconds=25.0)
    except Exception:
        return None

    text = text.strip()
    if not text:
        return None

    m = re.match(r"^[a-zA-Z0-9_$.]+\((.*)\)\s*;?\s*$", text, flags=re.S)
    if m:
        text = m.group(1).strip()

    try:
        import json

        return json.loads(text)
    except Exception as e:
        info_once(f"newsget_eastmoney_text_json_parse_failed:{url}", f"NewsGet: 东方财富 文本回退 JSON 解析失败，已降级跳过: url={url} err={repr(e)}", logger_name=__name__)
        return None


def _try_parse_embedded_items(html: str, *, crawl_time: str) -> list[NewsItem]:
    m = re.search(r"__NEXT_DATA__\s*=\s*({.*?})\s*</script>", html, flags=re.S)
    if m:
        try:
            import json

            data = json.loads(m.group(1))
            items = _extract_items_from_any_json(data, crawl_time=crawl_time)
            if items:
                return items
        except Exception as e:
            info_once("newsget_eastmoney_next_data_parse_failed", f"NewsGet: 东方财富 __NEXT_DATA__ 解析失败，已降级继续: err={repr(e)}", logger_name=__name__)

    m = re.search(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;\s*</script>", html, flags=re.S)
    if m:
        try:
            import json

            data = json.loads(m.group(1))
            items = _extract_items_from_any_json(data, crawl_time=crawl_time)
            if items:
                return items
        except Exception as e:
            info_once("newsget_eastmoney_nuxt_parse_failed", f"NewsGet: 东方财富 window.__NUXT__ 解析失败，已降级继续: err={repr(e)}", logger_name=__name__)

    return []


def _try_parse_html_items(html: str, *, crawl_time: str) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a[href]")
    items: list[NewsItem] = []
    for a in anchors:
        title = (a.get_text() or "").strip()
        href = (a.get("href") or "").strip()
        if not title or not href:
            continue
        if any(bad in href for bad in ["javascript:", "#"]):
            continue
        if "eastmoney.com" not in href and not href.startswith("/"):
            continue
        url = href
        if url.startswith("/"):
            url = "https://www.eastmoney.com" + url
        items.append(
            NewsItem(
                source="东方财富",
                rank=len(items) + 1,
                title=title,
                url=url,
                hot=None,
                publish_time=None,
                crawl_time=crawl_time,
            )
        )
        if len(items) >= 5:
            break
    return items


def _extract_items_from_any_json(payload: Any, *, crawl_time: str) -> list[NewsItem]:
    objs = _find_object_lists(payload)
    best: list[dict[str, Any]] = []
    best_score = 0
    for lst in objs:
        score = 0
        for obj in lst[:10]:
            if not isinstance(obj, dict):
                continue
            if pick_first(obj, ["title", "name", "Art_Title", "showTitle", "short_title"]):
                score += 1
            if pick_first(
                obj,
                [
                    "url",
                    "share_url",
                    "link",
                    "mobileUrl",
                    "mobile_url",
                    "Art_Uri",
                    "Art_Url",
                    "detailUrl",
                ],
            ):
                score += 2
        if score > best_score:
            best_score = score
            best = [o for o in lst if isinstance(o, dict)]

    items: list[NewsItem] = []
    for obj in best:
        title = pick_first(obj, ["title", "name", "Art_Title", "showTitle", "short_title"])
        url = pick_first(
            obj,
            [
                "url",
                "share_url",
                "link",
                "mobileUrl",
                "mobile_url",
                "Art_Uri",
                "Art_Url",
                "detailUrl",
            ],
        )
        if not title or not url:
            continue

        hot = pick_first(obj, ["hot", "hot_value", "read", "views", "comment_count", "replyCount", "hits"])
        pub = pick_first(obj, ["time", "publish_time", "publishTime", "date", "showTime", "Art_Date", "Art_ShowTime"])

        if isinstance(url, dict):
            url = pick_first(url, ["url", "link", "href"])
        if not isinstance(url, str):
            continue
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            url = "https://www.eastmoney.com" + url

        items.append(
            NewsItem(
                source="东方财富",
                rank=len(items) + 1,
                title=str(title).strip(),
                url=url,
                hot=str(hot) if hot not in (None, "") else None,
                publish_time=str(pub) if pub not in (None, "") else None,
                crawl_time=crawl_time,
            )
        )
        if len(items) >= 5:
            break

    return items


def _find_object_lists(payload: Any) -> list[list[Any]]:
    found: list[list[Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, list):
            if x and all(isinstance(i, dict) for i in x[:5]):
                found.append(x)
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)

    walk(payload)
    return found
