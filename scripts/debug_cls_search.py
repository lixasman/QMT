import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newsget.http import HttpConfig, build_session
from newsget.sources.cls_search import fetch_cls_latest_news_by_keyword


def main() -> None:
    s = build_session(HttpConfig(timeout_seconds=20))
    for kw in ["宁德时代 300750", "贵州茅台 600519", "招商银行 600036"]:
        hit = fetch_cls_latest_news_by_keyword(s, keyword=kw, max_age_days=30, page_size=10)
        print(kw, "=>", hit)


if __name__ == "__main__":
    main()
