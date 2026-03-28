from __future__ import annotations

import json
import requests

import finintel.deepseek_client as dc
from finintel.deepseek_client import DeepSeekClient, DeepSeekConfig


def test_from_env_reads_crs_oai_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CRS_OAI_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_MODEL", raising=False)

    client = DeepSeekClient.from_env(requests.Session())

    assert client._cfg.api_key == "test-key"
    assert client._cfg.base_url == "https://www.leishen-ai.cn/openai"
    assert client._cfg.model == "gpt-5.4"


def test_from_env_prefers_openai_mode_over_legacy_deepseek_overrides(monkeypatch) -> None:
    monkeypatch.setenv("CRS_OAI_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    monkeypatch.setattr(
        dc,
        "_load_codex_provider_settings",
        lambda: {
            "base_url": "https://www.leishen-ai.cn/openai",
            "wire_api": "responses",
            "env_key": "CRS_OAI_KEY",
            "model": "gpt-5.4",
        },
    )

    client = DeepSeekClient.from_env(requests.Session())

    assert client._cfg.base_url == "https://www.leishen-ai.cn/openai"
    assert client._cfg.model == "gpt-5.4"
    assert client._cfg.wire_api == "responses"


def test_chat_fallbacks_to_deepseek_when_gpt_fails(monkeypatch, caplog) -> None:
    session = requests.Session()
    primary = DeepSeekConfig(
        api_key="gpt-key",
        base_url="https://www.leishen-ai.cn/openai",
        model="gpt-5.4",
        wire_api="responses",
    )
    fallback = DeepSeekConfig(
        api_key="ds-key",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        wire_api="chat_completions",
    )
    client = DeepSeekClient(session, primary, fallback_cfg=fallback)

    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    def ok(*args, **kwargs):
        return "OK"

    monkeypatch.setattr(client, "_chat_via_responses", fail)
    monkeypatch.setattr(client, "_chat_via_chat_completions", ok)
    with caplog.at_level("WARNING"):
        out = client.chat(system="s", user="u", temperature=0.0, force_json=False)

    assert out == "OK"
    assert any("fallback to DeepSeek" in rec.message for rec in caplog.records)


def test_chat_supports_responses_stream_and_collects_text() -> None:
    class FakeStreamResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/event-stream"}
            self.text = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self, decode_unicode: bool = True):
            lines = [
                'event: response.output_text.delta',
                'data: {"type":"response.output_text.delta","delta":"O"}',
                '',
                'event: response.output_text.delta',
                'data: {"type":"response.output_text.delta","delta":"K"}',
                '',
                'event: response.completed',
                'data: {"type":"response.completed","response":{"status":"completed"}}',
                '',
            ]
            for line in lines:
                yield line

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post(self, url: str, headers=None, data=None, timeout=None, stream: bool = False):
            self.calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "timeout": timeout,
                    "stream": stream,
                }
            )
            return FakeStreamResponse()

    session = FakeSession()
    client = DeepSeekClient(
        session,
        DeepSeekConfig(
            api_key="test-key",
            base_url="https://www.leishen-ai.cn/openai",
            model="gpt-5.4",
            wire_api="responses",
        ),
    )

    out = client.chat(system="You are concise.", user="Reply with OK only.", temperature=0.0, force_json=False)

    assert out == "OK"
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "https://www.leishen-ai.cn/openai/v1/responses"
    assert call["stream"] is True
    payload = json.loads(str(call["data"]))
    assert payload["model"] == "gpt-5.4"
    assert payload["stream"] is True
    assert payload["store"] is False
    assert payload["input"][0]["role"] == "system"
    assert payload["input"][1]["role"] == "user"


def test_chat_responses_ignores_unparseable_irrelevant_events() -> None:
    class FakeStreamResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/event-stream"}
            self.text = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self, decode_unicode: bool = True):
            lines = [
                'event: response.created',
                'data: {"type":"response.created","response":{"instructions":"unterminated',
                '',
                'event: response.output_text.delta',
                'data: {"type":"response.output_text.delta","delta":"OK"}',
                '',
                'event: response.completed',
                'data: {"type":"response.completed","response":{"status":"completed"}}',
                '',
            ]
            for line in lines:
                yield line

    class FakeSession:
        def post(self, url: str, headers=None, data=None, timeout=None, stream: bool = False):
            return FakeStreamResponse()

    client = DeepSeekClient(
        FakeSession(),
        DeepSeekConfig(
            api_key="test-key",
            base_url="https://www.leishen-ai.cn/openai",
            model="gpt-5.4",
            wire_api="responses",
        ),
    )

    out = client.chat(system="You are concise.", user="Reply with OK only.", temperature=0.0, force_json=False)

    assert out == "OK"


def test_chat_responses_returns_delta_text_even_if_completed_event_is_unparseable() -> None:
    class FakeStreamResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/event-stream"}
            self.text = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self, decode_unicode: bool = True):
            lines = [
                'event: response.output_text.delta',
                'data: {"type":"response.output_text.delta","delta":"OK"}',
                '',
                'event: response.completed',
                'data: {"type":"response.completed","response":{"instructions":"unterminated',
                '',
            ]
            for line in lines:
                yield line

    class FakeSession:
        def post(self, url: str, headers=None, data=None, timeout=None, stream: bool = False):
            return FakeStreamResponse()

    client = DeepSeekClient(
        FakeSession(),
        DeepSeekConfig(
            api_key="test-key",
            base_url="https://www.leishen-ai.cn/openai",
            model="gpt-5.4",
            wire_api="responses",
        ),
    )

    out = client.chat(system="You are concise.", user="Reply with OK only.", temperature=0.0, force_json=False)

    assert out == "OK"


def test_chat_responses_supports_utf8_bytes_stream() -> None:
    class FakeStreamResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/event-stream"}
            self.text = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self, decode_unicode: bool = True):
            lines = [
                b'event: response.output_text.delta',
                ('data: {"type":"response.output_text.delta","delta":"你好"}').encode('utf-8'),
                b'',
            ]
            for line in lines:
                yield line

    class FakeSession:
        def post(self, url: str, headers=None, data=None, timeout=None, stream: bool = False):
            return FakeStreamResponse()

    client = DeepSeekClient(
        FakeSession(),
        DeepSeekConfig(
            api_key="test-key",
            base_url="https://www.leishen-ai.cn/openai",
            model="gpt-5.4",
            wire_api="responses",
        ),
    )

    out = client.chat(system="You are concise.", user="Say 你好 only.", temperature=0.0, force_json=False)

    assert out == "你好"

