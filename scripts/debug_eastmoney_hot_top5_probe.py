from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from newsget.http import HttpConfig, build_session


def _resolve_scripts(entry_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for sc in soup.find_all("script"):
        src = (sc.get("src") or "").strip().replace("\\", "/")
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        out.append(src if src.startswith("http") else urljoin(entry_url, src))
    return list(dict.fromkeys(out))


def main() -> int:
    s = build_session(HttpConfig(timeout_seconds=25.0))

    entry_url = "https://vipmoney.eastmoney.com/collect/stockranking/pages/ranking9_3/list.html"
    entry = s.get(entry_url, headers={"Referer": "https://vipmoney.eastmoney.com/"}, timeout=25)
    print("[entry] status =", entry.status_code)
    print("[entry] content-type =", entry.headers.get("Content-Type"))
    print("[entry] len =", len(entry.text))
    print("[entry] has __NEXT_DATA__ =", "__NEXT_DATA__" in entry.text)
    print("[entry] has window.__NUXT__ =", "window.__NUXT__" in entry.text)

    scripts = _resolve_scripts(entry_url, entry.text)
    print("[entry] scripts =", len(scripts))
    for u in scripts:
        print("[script]", u)

    app_url = "https://vipmoney.eastmoney.com/collect/app_ranking/ranking/app.html"
    try:
        app = s.get(app_url, headers={"Referer": "https://vipmoney.eastmoney.com/"}, timeout=25)
        app_html = app.text or ""
        print("[app] status =", app.status_code)
        print("[app] content-type =", app.headers.get("Content-Type"))
        print("[app] len =", len(app_html))
        print("[app] has __NEXT_DATA__ =", "__NEXT_DATA__" in app_html)
        print("[app] has window.__NUXT__ =", "window.__NUXT__" in app_html)
    except Exception as e:
        print("[app] error =", repr(e))

    for su in scripts[:12]:
        try:
            resp = s.get(su, headers={"Referer": entry_url}, timeout=25)
        except Exception as e:
            print("[script.fetch] error =", su, repr(e))
            continue

        text = resp.text or ""
        if "static/script/ranking9_3/list/list_" in su:
            out = Path("output") / "cache" / "debug_ranking9_3_list_js.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            print("[script.fetch] saved =", str(out))
        print("[script.fetch] url =", su)
        print("[script.fetch] status =", resp.status_code)
        print("[script.fetch] ct =", resp.headers.get("Content-Type"))
        print("[script.fetch] len =", len(text))
        print("[script.fetch] contains app_ranking =", "app_ranking" in text)
        print("[script.fetch] contains collect/app_ranking =", "collect/app_ranking" in text)

        abs_urls = re.findall(r"https?://[a-z0-9.-]+eastmoney\\.com/[0-9A-Za-z_./?=&%-]+", text)
        abs_any = re.findall(r"https?://[^\\s\"']{10,200}", text)
        print("[script.fetch] abs_urls(eastmoney.com) =", len(abs_urls))
        for u in abs_urls[:8]:
            print("[url.em]", u)
        print("[script.fetch] abs_urls(any) =", len(abs_any))
        for u in abs_any[:8]:
            print("[url.any]", u)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
