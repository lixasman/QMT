from __future__ import annotations

import re
import sys
import hashlib
import logging
from typing import Any, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.warn_utils import info_once
from ..http import get_json, get_text
from ..models import NewsItem, now_iso, pick_first


def fetch_cls_hot_top5(session: requests.Session, *, debug: bool = False) -> List[NewsItem]:
    crawl_time = now_iso()

    api_items = _fetch_hot_list_api(session, crawl_time=crawl_time)
    if api_items:
        return api_items[:5]

    entry_url = "https://api3.cls.cn/quote/toplist?app=cailianpress&sv=835&os=android"
    headers = {"Referer": "https://www.cls.cn/"}

    html = get_text(session, entry_url, headers=headers)

    items = _try_parse_embedded_items(html, crawl_time=crawl_time)
    if items:
        return items[:5]

    script_urls = _extract_script_urls(html, base_url=entry_url)
    if debug:
        print(f"[CLS] scripts={len(script_urls)}", file=sys.stderr)
    api_candidates = _discover_api_candidates(session, script_urls, referer=entry_url)
    if debug:
        print(f"[CLS] api_candidates={len(api_candidates)}", file=sys.stderr)
    for api_url in api_candidates:
        parsed = _try_fetch_json_loose(session, api_url, referer=entry_url)
        if parsed is None:
            continue
        extracted = _extract_items_from_any_json(parsed, crawl_time=crawl_time)
        if extracted:
            return extracted[:5]

    html_items = _try_parse_html_items(html, crawl_time=crawl_time)
    return html_items[:5]


def fetch_cls_article_content(session: requests.Session, url: str, *, debug: bool = False) -> Optional[str]:
    headers = {"Referer": "https://www.cls.cn/"}
    html = get_text(session, url, headers=headers, timeout_seconds=25.0)
    content = _extract_cls_content_from_html(html)
    if debug and not content:
        print(f"[CLS] empty content: {url}", file=sys.stderr, flush=True)
    return content


def _extract_cls_content_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script,style,noscript"):
        tag.decompose()

    body = soup.body or soup
    candidates: list[tuple[int, str]] = []
    for el in body.find_all(["article", "div"], limit=2500):
        attrs = f"{el.get('id','')} {' '.join(el.get('class', []) if isinstance(el.get('class', []), list) else [])}".lower()
        if any(bad in attrs for bad in ["comment", "reply", "footer", "header", "nav", "share", "recommend", "related"]):
            continue
        txt = el.get_text("\n", strip=True)
        if len(txt) < 60:
            continue
        p_cnt = len(el.find_all("p"))
        a_cnt = len(el.find_all("a"))
        bonus = 0
        if any(k in attrs for k in ["article", "content", "detail", "rich", "text"]):
            bonus += 400
        penalty = txt.count("阅读") * 200 + txt.count("展开") * 200 + txt.count("收起") * 200
        score = len(txt) + p_cnt * 120 - a_cnt * 80 + bonus - penalty
        candidates.append((score, txt))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        text = candidates[0][1]
    else:
        text = body.get_text("\n", strip=True)

    lines = [ln.strip().replace("\u3000", " ").replace("\xa0", " ") for ln in text.splitlines()]
    cleaned: list[str] = []
    for ln in lines:
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln:
            continue
        if re.search(r"\b阅读\b", ln) and re.search(r"\d", ln):
            continue
        if ln in {"展开", "收起"}:
            continue
        if any(k in ln for k in ["打开APP", "APP内打开", "扫码", "免责声明", "相关推荐"]):
            continue
        cleaned.append(ln)

    cut_idx = None
    for i, ln in enumerate(cleaned):
        if " 回复 " in ln or ln.endswith(" 回复") or ln.startswith("回复 "):
            cut_idx = i
            break
    if cut_idx is not None and cut_idx > 0:
        cleaned = cleaned[:cut_idx]

    out = "\n".join(cleaned).strip()
    return out or None


def _fetch_hot_list_api(session: requests.Session, *, crawl_time: str) -> list[NewsItem]:
    entry_url = "https://api3.cls.cn/quote/toplist?app=cailianpress&sv=835&os=android"
    try:
        html = get_text(session, entry_url, headers={"Referer": "https://www.cls.cn/"}, timeout_seconds=20.0)
    except Exception:
        html = ""

    api3host = "https://api3.cls.cn/quote"
    m = re.search(r"API3HOST\\s*=\\s*['\"]([^'\"]+)['\"]", html)
    if m and m.group(1).strip():
        api3host = m.group(1).strip()
        if api3host.startswith("//"):
            api3host = "https:" + api3host

    os = "android"
    sv = "835"
    params = f"app=cailianpress&os={os}&sv={sv}"
    sign = hashlib.md5(hashlib.sha1(params.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    bases = [api3host, "https://api3.cls.cn", "https://api3.cls.cn/quote"]
    data: Any = None
    for base in bases:
        url = f"{base.rstrip('/')}/v1/hot_list?{params}&sign={sign}"
        try:
            data = get_json(session, url, headers={"Referer": "https://api3.cls.cn/quote/toplist"}, timeout_seconds=20.0)
            if isinstance(data, dict) and data.get("errno") == 0:
                break
        except Exception:
            continue
    if data is None:
        return []

    if not isinstance(data, dict):
        return []
    if data.get("errno") != 0:
        return []
    lst = data.get("data")
    if not isinstance(lst, list):
        return []

    items: list[NewsItem] = []
    for obj in lst:
        if not isinstance(obj, dict):
            continue
        title = obj.get("title")
        id_ = obj.get("id")
        if not title or not id_:
            continue
        url = f"https://api3.cls.cn/share/article/{id_}?os=ios&sv={sv}"
        hot = pick_first(obj, ["hot", "read", "views", "comment_count"])
        pub = pick_first(obj, ["ctime", "time", "publish_time", "publishTime"])
        items.append(
            NewsItem(
                source="财联社",
                rank=len(items) + 1,
                title=re.sub(r"\s+", " ", str(title)).strip(),
                url=url,
                hot=str(hot) if hot not in (None, "") else None,
                publish_time=str(pub) if pub not in (None, "") else None,
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
    for u in script_urls:
        try:
            js = get_text(session, u, headers=headers, timeout_seconds=20.0)
        except Exception:
            continue

        for m in re.finditer(r"https?://api\d+\.cls\.cn/[A-Za-z0-9_./?-]+", js):
            candidates.append(m.group(0))

        for m in re.finditer(r'"/quote/toplist[^"]*"', js):
            rel = m.group(0).strip('"')
            if rel.startswith("/"):
                candidates.append("https://api3.cls.cn" + rel)

    def score(url: str) -> int:
        s = 0
        if "toplist" in url:
            s += 3
        if "quote" in url:
            s += 1
        if "share" in url:
            s -= 2
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
    headers = {"Referer": referer, "Accept": "application/json,*/*"}
    try:
        return get_json(session, url, headers=headers, timeout_seconds=20.0)
    except Exception as e:
        info_once(f"newsget_cls_json_failed:{url}", f"NewsGet: CLS JSON 接口请求失败，已降级到文本解析: url={url} err={repr(e)}", logger_name=__name__)

    try:
        text = get_text(session, url, headers=headers, timeout_seconds=20.0)
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
        info_once(f"newsget_cls_text_json_parse_failed:{url}", f"NewsGet: CLS 文本回退 JSON 解析失败，已降级跳过: url={url} err={repr(e)}", logger_name=__name__)
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
            info_once("newsget_cls_next_data_parse_failed", f"NewsGet: CLS __NEXT_DATA__ 解析失败，已降级继续: err={repr(e)}", logger_name=__name__)

    m = re.search(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;\s*</script>", html, flags=re.S)
    if m:
        try:
            import json

            data = json.loads(m.group(1))
            items = _extract_items_from_any_json(data, crawl_time=crawl_time)
            if items:
                return items
        except Exception as e:
            info_once("newsget_cls_nuxt_parse_failed", f"NewsGet: CLS window.__NUXT__ 解析失败，已降级继续: err={repr(e)}", logger_name=__name__)

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
        if "cls.cn" not in href and not href.startswith("/"):
            continue
        url = href
        if url.startswith("/"):
            url = "https://www.cls.cn" + url
        items.append(
            NewsItem(
                source="财联社",
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
            if pick_first(obj, ["title", "name", "brief", "summary"]):
                score += 1
            if pick_first(obj, ["url", "share_url", "link", "mobile_url"]):
                score += 2
        if score > best_score:
            best_score = score
            best = [o for o in lst if isinstance(o, dict)]

    items: list[NewsItem] = []
    for obj in best:
        title = pick_first(obj, ["title", "name", "brief", "summary"])
        url = pick_first(obj, ["url", "share_url", "link", "mobile_url"])
        if not title or not url:
            continue
        hot = pick_first(obj, ["hot", "hot_value", "read", "views", "comment_count", "share_count"])
        pub = pick_first(obj, ["ctime", "time", "publish_time", "publishTime", "pub_time", "pubTime", "date"])

        if isinstance(url, dict):
            url = pick_first(url, ["url", "link", "href"])
        if not isinstance(url, str):
            continue
        if url.startswith("/"):
            url = "https://www.cls.cn" + url

        items.append(
            NewsItem(
                source="财联社",
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
