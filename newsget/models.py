from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class NewsItem:
    source: str
    rank: int
    title: str
    url: str
    hot: Optional[str] = None
    publish_time: Optional[str] = None
    content: Optional[str] = None
    crawl_time: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def pick_first(mapping: Mapping[str, Any], keys: list[str]) -> Any:
    for k in keys:
        if k in mapping and mapping[k] not in (None, ""):
            return mapping[k]
    return None
