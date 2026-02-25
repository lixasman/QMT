from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass(frozen=True)
class HttpConfig:
    timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_factor: float = 0.6


DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}


def build_session(config: Optional[HttpConfig] = None) -> requests.Session:
    cfg = config or HttpConfig()

    retry = Retry(
        total=cfg.max_retries,
        backoff_factor=cfg.backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)

    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def get_text(
    session: requests.Session,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout_seconds: float = 15.0,
    verify: Optional[bool] = None,
) -> str:
    kwargs: dict[str, Any] = {"params": params, "headers": headers, "timeout": timeout_seconds}
    if verify is not None:
        kwargs["verify"] = verify
    resp = session.get(url, **kwargs)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def get_json(
    session: requests.Session,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout_seconds: float = 15.0,
    verify: Optional[bool] = None,
) -> Any:
    kwargs: dict[str, Any] = {"params": params, "headers": headers, "timeout": timeout_seconds}
    if verify is not None:
        kwargs["verify"] = verify
    resp = session.get(url, **kwargs)
    resp.raise_for_status()

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        return resp.json()

    try:
        return resp.json()
    except Exception:
        return json.loads(resp.text)
