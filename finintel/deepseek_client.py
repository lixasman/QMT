from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


def _env(name: str) -> str:
    return str(os.environ.get(name, "")).strip()


def _first_non_empty(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_wire_api(value: str) -> str:
    text = str(value or "").strip().lower()
    if text == "responses":
        return "responses"
    return "chat_completions"


def _load_codex_provider_settings() -> dict[str, str]:
    if tomllib is None:
        return {}
    raw_path = _env("CODEX_CONFIG_PATH")
    path = Path(raw_path) if raw_path else (Path.home() / ".codex" / "config.toml")
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    provider_key = str(data.get("model_provider") or "").strip()
    providers = data.get("model_providers")
    provider: dict[str, Any] = {}
    if provider_key and isinstance(providers, dict):
        raw_provider = providers.get(provider_key)
        if isinstance(raw_provider, dict):
            provider = raw_provider
    out: dict[str, str] = {}
    for key in ("base_url", "wire_api", "env_key"):
        value = str(provider.get(key) or "").strip()
        if value:
            out[key] = value
    model = str(data.get("model") or "").strip()
    if model:
        out["model"] = model
    return out


def _extract_text_from_response_obj(response_obj: object) -> str:
    if not isinstance(response_obj, dict):
        return ""
    output = response_obj.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                output_text = part.get("output_text")
                if isinstance(output_text, str) and output_text:
                    parts.append(output_text)
        text2 = item.get("text")
        if isinstance(text2, str) and text2:
            parts.append(text2)
    return "".join(parts).strip()


def _consume_responses_stream(resp: requests.Response) -> tuple[str, str, str]:
    text_done = ""
    text_parts: list[str] = []
    final_text = ""
    current_event = ""
    current_data_lines: list[bytes] = []

    def _flush_event() -> None:
        nonlocal current_event, current_data_lines, text_done, final_text
        if not current_data_lines:
            current_event = ""
            current_data_lines = []
            return
        payload_bytes = b"".join(current_data_lines).strip()
        event_name = str(current_event or "").strip()
        current_event = ""
        current_data_lines = []
        payload_text = payload_bytes.decode("utf-8")
        if not payload_text or payload_text == "[DONE]":
            return
        if event_name and event_name not in {"response.output_text.delta", "response.output_text.done", "response.completed", "response.failed", "error"}:
            return
        try:
            obj = json.loads(payload_text)
        except json.JSONDecodeError as e:
            if event_name in {"response.output_text.delta", "response.output_text.done", "response.failed", "error"} or not event_name:
                raise DeepSeekError(
                    f"DeepSeek responses JSON 解析失败: event={event_name or '<unknown>'} payload={payload_text[:500]} err={repr(e)}"
                )
            return
        typ = str(obj.get("type") or event_name)
        if typ == "response.output_text.delta":
            text_parts.append(str(obj.get("delta") or ""))
        elif typ == "response.output_text.done":
            text_done = str(obj.get("text") or "")
        elif typ == "response.completed":
            final_text = _extract_text_from_response_obj(obj.get("response")) or final_text
        elif typ in {"response.failed", "error"}:
            raise DeepSeekError(f"DeepSeek responses failed: {str(obj)[:500]}")

    for raw in resp.iter_lines(decode_unicode=False):
        if raw is None:
            continue
        raw_bytes = bytes(raw) if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
        if raw_bytes.startswith(b"event: "):
            current_event = raw_bytes[7:].decode("utf-8", errors="ignore").strip()
            continue
        if raw_bytes.startswith(b"data: "):
            current_data_lines.append(raw_bytes[6:])
            continue
        if not raw_bytes:
            _flush_event()
    _flush_event()
    return ("".join(text_parts).strip(), text_done.strip(), final_text.strip())


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://www.leishen-ai.cn/openai"
    model: str = "gpt-5.4"
    wire_api: str = "responses"
    timeout_seconds: float = 40.0
    max_retries: int = 3
    backoff_seconds: float = 1.0


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(
        self,
        session: requests.Session,
        config: DeepSeekConfig,
        fallback_cfg: Optional[DeepSeekConfig] = None,
    ) -> None:
        self._session = session
        self._cfg = config
        self._fallback_cfg = fallback_cfg

    @staticmethod
    def from_env(session: requests.Session) -> "DeepSeekClient":
        provider_cfg = _load_codex_provider_settings()
        explicit_openai_signal = any(
            (
                _env("CRS_OAI_KEY"),
                _env("OPENAI_COMPAT_API_KEY"),
                _env("OPENAI_COMPAT_BASE_URL"),
                _env("OPENAI_COMPAT_MODEL"),
                _env("OPENAI_COMPAT_WIRE_API"),
            )
        )
        openai_env_key = _first_non_empty(_env("OPENAI_COMPAT_ENV_KEY"), provider_cfg.get("env_key", ""), "CRS_OAI_KEY")
        openai_api_key = _first_non_empty(_env("OPENAI_COMPAT_API_KEY"), _env(openai_env_key), _env("CRS_OAI_KEY"))
        legacy_api_key = _env("DEEPSEEK_API_KEY")
        legacy_cfg: Optional[DeepSeekConfig] = None
        if legacy_api_key:
            base_url = _first_non_empty(_env("DEEPSEEK_BASE_URL"), "https://api.deepseek.com")
            model = _first_non_empty(_env("DEEPSEEK_MODEL"), "deepseek-chat")
            timeout = float(_first_non_empty(_env("DEEPSEEK_TIMEOUT"), "40") or "40")
            max_retries = int(_first_non_empty(_env("DEEPSEEK_MAX_RETRIES"), "3") or "3")
            backoff = float(_first_non_empty(_env("DEEPSEEK_BACKOFF"), "1") or "1")
            legacy_cfg = DeepSeekConfig(
                api_key=legacy_api_key,
                base_url=base_url,
                model=model,
                wire_api="chat_completions",
                timeout_seconds=timeout,
                max_retries=max_retries,
                backoff_seconds=backoff,
            )

        if explicit_openai_signal or provider_cfg:
            if openai_api_key:
                base_url = _first_non_empty(_env("OPENAI_COMPAT_BASE_URL"), provider_cfg.get("base_url", ""), "https://www.leishen-ai.cn/openai")
                model = _first_non_empty(_env("OPENAI_COMPAT_MODEL"), provider_cfg.get("model", ""), "gpt-5.4")
                wire_api = _normalize_wire_api(_first_non_empty(_env("OPENAI_COMPAT_WIRE_API"), provider_cfg.get("wire_api", ""), "responses"))
                timeout = float(_first_non_empty(_env("OPENAI_COMPAT_TIMEOUT"), "120") or "120")
                max_retries = int(_first_non_empty(_env("OPENAI_COMPAT_MAX_RETRIES"), "3") or "3")
                backoff = float(_first_non_empty(_env("OPENAI_COMPAT_BACKOFF"), "1") or "1")
                return DeepSeekClient(
                    session,
                    DeepSeekConfig(
                        api_key=openai_api_key,
                        base_url=base_url,
                        model=model,
                        wire_api=wire_api,
                        timeout_seconds=timeout,
                        max_retries=max_retries,
                        backoff_seconds=backoff,
                    ),
                    fallback_cfg=legacy_cfg,
                )
            raise DeepSeekError(f"{openai_env_key} ???")

        if legacy_cfg:
            return DeepSeekClient(session, legacy_cfg)
        raise DeepSeekError("CRS_OAI_KEY / DEEPSEEK_API_KEY ???")

    def _chat_via_chat_completions(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        force_json: bool,
        cfg: Optional[DeepSeekConfig] = None,
    ) -> str:
        active_cfg = cfg or self._cfg
        url = active_cfg.base_url.rstrip("/") + "/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {active_cfg.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max(1, active_cfg.max_retries) + 1):
            payload: dict[str, Any] = {
                "model": active_cfg.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            }
            if force_json:
                payload["response_format"] = {"type": "json_object"}

            try:
                resp = self._session.post(url, headers=headers, data=json.dumps(payload), timeout=active_cfg.timeout_seconds)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_exc = e
                if attempt >= active_cfg.max_retries:
                    break
                time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
                continue

            if resp.status_code >= 400:
                if force_json and resp.status_code in (400, 422):
                    payload.pop("response_format", None)
                    try:
                        resp = self._session.post(url, headers=headers, data=json.dumps(payload), timeout=active_cfg.timeout_seconds)
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                        last_exc = e
                        if attempt >= active_cfg.max_retries:
                            break
                        time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
                        continue
                if resp.status_code >= 400:
                    last_exc = DeepSeekError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")
                    if attempt >= active_cfg.max_retries:
                        break
                    time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
                    continue

            data = resp.json()
            try:
                return (data["choices"][0]["message"]["content"] or "").strip()
            except Exception as e:
                last_exc = DeepSeekError(f"DeepSeek ??????: {repr(e)}; raw={str(data)[:500]}")
                if attempt >= active_cfg.max_retries:
                    break
                time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
        raise DeepSeekError(f"DeepSeek ????: {repr(last_exc)}")

    def _chat_via_responses(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        force_json: bool,
        cfg: Optional[DeepSeekConfig] = None,
    ) -> str:
        active_cfg = cfg or self._cfg
        url = active_cfg.base_url.rstrip("/") + "/v1/responses"
        headers = {
            "Authorization": f"Bearer {active_cfg.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max(1, active_cfg.max_retries) + 1):
            payload: dict[str, Any] = {
                "model": active_cfg.model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user}]},
                ],
                "temperature": temperature,
                "stream": True,
            }
            base_url_norm = active_cfg.base_url.rstrip("/").lower()
            if "leishen-ai.cn/openai" in base_url_norm:
                payload["store"] = False
            if force_json:
                payload["text"] = {"format": {"type": "json_object"}}
            text_done = ""
            text_parts = ""
            final_text = ""
            try:
                with self._session.post(
                    url,
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=active_cfg.timeout_seconds,
                    stream=True,
                ) as resp:
                    if resp.status_code >= 400:
                        if force_json and resp.status_code in (400, 422):
                            payload.pop("text", None)
                            with self._session.post(
                                url,
                                headers=headers,
                                data=json.dumps(payload),
                                timeout=active_cfg.timeout_seconds,
                                stream=True,
                            ) as resp2:
                                if resp2.status_code >= 400:
                                    raise DeepSeekError(f"DeepSeek HTTP {resp2.status_code}: {resp2.text[:500]}")
                                text_parts, text_done, final_text = _consume_responses_stream(resp2)
                        else:
                            raise DeepSeekError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")
                    else:
                        text_parts, text_done, final_text = _consume_responses_stream(resp)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_exc = e
                if attempt >= active_cfg.max_retries:
                    break
                time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
                continue
            except DeepSeekError as e:
                last_exc = e
                if attempt >= active_cfg.max_retries:
                    break
                time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
                continue
            except Exception as e:
                last_exc = DeepSeekError(f"DeepSeek responses ????: {repr(e)}")
                if attempt >= active_cfg.max_retries:
                    break
                time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
                continue

            result = str(text_parts or "").strip() or text_done.strip() or final_text.strip()
            if result:
                return result
            last_exc = DeepSeekError("DeepSeek responses ????")
            if attempt >= active_cfg.max_retries:
                break
            time.sleep(active_cfg.backoff_seconds * (2 ** (attempt - 1)))
        raise DeepSeekError(f"DeepSeek ????: {repr(last_exc)}")

    def _chat_with_cfg(
        self,
        cfg: DeepSeekConfig,
        *,
        system: str,
        user: str,
        temperature: float,
        force_json: bool,
    ) -> str:
        if _normalize_wire_api(cfg.wire_api) == "responses":
            return self._chat_via_responses(
                system=system,
                user=user,
                temperature=temperature,
                force_json=force_json,
                cfg=cfg,
            )
        return self._chat_via_chat_completions(
            system=system,
            user=user,
            temperature=temperature,
            force_json=force_json,
            cfg=cfg,
        )

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> str:
        try:
            return self._chat_with_cfg(
                self._cfg,
                system=system,
                user=user,
                temperature=temperature,
                force_json=force_json,
            )
        except Exception as e:
            if not self._fallback_cfg:
                raise
            logger.warning("FinIntel: GPT call failed, fallback to DeepSeek. err=%s", repr(e))
            return self._chat_with_cfg(
                self._fallback_cfg,
                system=system,
                user=user,
                temperature=temperature,
                force_json=force_json,
            )
