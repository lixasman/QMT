from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class AkShareLatestNews:
    title: str
    url: str
    publish_time: Optional[str]
    content: Optional[str]


def fetch_stock_latest_news_from_akshare(
    *,
    stock_code: str,
    max_age_days: int = 3,
) -> Optional[AkShareLatestNews]:
    ak = _import_akshare()
    df = ak.stock_news_em(symbol=str(stock_code))
    if df is None or getattr(df, "empty", True):
        return None

    row = df.iloc[0]
    title = str(_safe_get(row, "新闻标题") or "").strip()
    url = str(_safe_get(row, "新闻链接") or "").strip()
    content = str(_safe_get(row, "新闻内容") or "").strip() or None
    publish_time = str(_safe_get(row, "发布时间") or "").strip() or None

    if not title or not url:
        return None
    if publish_time and max_age_days > 0 and not _is_within_days(publish_time, days=max_age_days):
        return None

    return AkShareLatestNews(title=title, url=url, publish_time=publish_time, content=content)


def _import_akshare():
    try:
        import akshare  # type: ignore
    except Exception as e:
        raise RuntimeError("AKShare 未安装，请先执行：python -m pip install akshare") from e
    return akshare


def _safe_get(row, key: str):
    try:
        return row.get(key)
    except Exception:
        try:
            return row[key]
        except Exception:
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

