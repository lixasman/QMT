from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout_seconds: float = 40.0
    max_retries: int = 3
    backoff_seconds: float = 1.0


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self, session: requests.Session, config: DeepSeekConfig) -> None:
        self._session = session
        self._cfg = config

    @staticmethod
    def from_env(session: requests.Session) -> "DeepSeekClient":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise DeepSeekError("DEEPSEEK_API_KEY 未设置")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
        timeout = float(os.environ.get("DEEPSEEK_TIMEOUT", "40").strip() or "40")
        max_retries = int(os.environ.get("DEEPSEEK_MAX_RETRIES", "3").strip() or "3")
        backoff = float(os.environ.get("DEEPSEEK_BACKOFF", "1").strip() or "1")
        return DeepSeekClient(
            session,
            DeepSeekConfig(
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_seconds=timeout,
                max_retries=max_retries,
                backoff_seconds=backoff,
            ),
        )

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> str:
        url = self._cfg.base_url.rstrip("/") + "/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max(1, self._cfg.max_retries) + 1):
            payload: dict[str, Any] = {
                "model": self._cfg.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            }
            if force_json:
                payload["response_format"] = {"type": "json_object"}

            try:
                resp = self._session.post(url, headers=headers, data=json.dumps(payload), timeout=self._cfg.timeout_seconds)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_exc = e
                if attempt >= self._cfg.max_retries:
                    break
                time.sleep(self._cfg.backoff_seconds * (2 ** (attempt - 1)))
                continue

            if resp.status_code >= 400:
                if force_json and resp.status_code in (400, 422):
                    payload.pop("response_format", None)
                    try:
                        resp = self._session.post(url, headers=headers, data=json.dumps(payload), timeout=self._cfg.timeout_seconds)
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                        last_exc = e
                        if attempt >= self._cfg.max_retries:
                            break
                        time.sleep(self._cfg.backoff_seconds * (2 ** (attempt - 1)))
                        continue
                if resp.status_code >= 400:
                    last_exc = DeepSeekError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")
                    if attempt >= self._cfg.max_retries:
                        break
                    time.sleep(self._cfg.backoff_seconds * (2 ** (attempt - 1)))
                    continue

            data = resp.json()
            try:
                return (data["choices"][0]["message"]["content"] or "").strip()
            except Exception as e:
                last_exc = DeepSeekError(f"DeepSeek 响应解析失败: {repr(e)}; raw={str(data)[:500]}")
                if attempt >= self._cfg.max_retries:
                    break
                time.sleep(self._cfg.backoff_seconds * (2 ** (attempt - 1)))

        raise DeepSeekError(f"DeepSeek 请求失败: {repr(last_exc)}")
