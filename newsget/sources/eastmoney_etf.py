from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from ..http import get_text


@dataclass(frozen=True)
class EtfHolding:
    stock_code: str
    stock_name: str


def _normalize_etf_code6(code: str) -> str:
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s


def fetch_etf_top10_holdings(
    session: requests.Session,
    etf_code: str,
    *,
    topline: int = 10,
) -> tuple[str, Optional[str], List[EtfHolding]]:
    etf_code = _normalize_etf_code6(etf_code)
    url = (
        "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
        f"?type=jjcc&code={etf_code}&topline={topline}&year=&month=&rt=0.1"
    )
    text = get_text(
        session,
        url,
        headers={"Referer": "https://fundf10.eastmoney.com/"},
        timeout_seconds=25.0,
    )
    html = _extract_apidata_content_html(text)
    if not html:
        raise RuntimeError("无法解析 ETF 持仓 apidata.content")

    soup = BeautifulSoup(html, "html.parser")
    etf_name = _extract_etf_name(soup) or etf_code
    as_of = _extract_as_of_date(soup)
    holdings = _extract_first_table_holdings(soup, topline=topline)
    return etf_name, as_of, holdings


def _extract_apidata_content_html(text: str) -> Optional[str]:
    m = re.search(r'content:"(?P<content>.*?)"\s*,\s*arryear', text, re.DOTALL)
    if not m:
        m = re.search(r'content:"(?P<content>.*?)"\s*\}', text, re.DOTALL)
    if not m:
        return None
    escaped = m.group("content")
    if "\\" not in escaped:
        return escaped
    if not any(x in escaped for x in ("\\u", "\\x", "\\n", "\\t", "\\r", "\\/", '\\"', "\\'")):
        return escaped
    try:
        return bytes(escaped, "utf-8").decode("unicode_escape")
    except Exception:
        return escaped


def _extract_etf_name(soup: BeautifulSoup) -> Optional[str]:
    a = soup.select_one("h4 a[title]")
    if a and a.get("title"):
        return str(a.get("title")).strip() or None
    if a and a.get_text(strip=True):
        return a.get_text(strip=True)
    return None


def _extract_as_of_date(soup: BeautifulSoup) -> Optional[str]:
    font = soup.select_one("label.right font.px12")
    if font:
        s = font.get_text(strip=True)
        if s:
            return s
    text = soup.get_text(" ", strip=True)
    m = re.search(r"截止至：\s*(20\d{2}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _extract_first_table_holdings(soup: BeautifulSoup, *, topline: int) -> List[EtfHolding]:
    table = soup.select_one("table")
    if not table:
        return []
    rows = table.select("tr")
    out: list[EtfHolding] = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        code = tds[1].get_text(strip=True)
        name = tds[2].get_text(strip=True)
        if not re.fullmatch(r"\d{6}", code):
            continue
        if not name:
            continue
        out.append(EtfHolding(stock_code=code, stock_name=name))
        if len(out) >= topline:
            break
    return out
