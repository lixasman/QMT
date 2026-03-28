# GPT Fallback to DeepSeek Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `DeepSeekClient.chat()` 中当 GPT 调用失败时自动降级到 DeepSeek，并仅记录 warning 日志。

**Architecture:** `DeepSeekClient` 增加可选降级配置（DeepSeek legacy）。`chat()` 先走主配置，任意异常触发 warning 后切换到降级配置再调用一次；无降级配置或降级失败则抛出异常。

**Tech Stack:** Python, requests, pytest

---

### Task 1: Add failing tests for GPT fallback

**Files:**
- Modify: `tests/test_finintel_deepseek_client.py:1-240`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run:
```
pytest tests/test_finintel_deepseek_client.py::test_chat_fallbacks_to_deepseek_when_gpt_fails -v
```
Expected: FAIL (DeepSeekClient does not accept fallback_cfg / no fallback logic yet)

**Step 3: Commit**

```bash
git add tests/test_finintel_deepseek_client.py
git commit -m "test: add gpt fallback regression"
```

---

### Task 2: Implement fallback config + warning log

**Files:**
- Modify: `finintel/deepseek_client.py:3-385`
- Modify: `tests/test_finintel_deepseek_client.py:1-260`

**Step 1: Implement fallback config + helper**

```python
import logging

logger = logging.getLogger(__name__)

class DeepSeekClient:
    def __init__(self, session: requests.Session, config: DeepSeekConfig, fallback_cfg: DeepSeekConfig | None = None) -> None:
        self._session = session
        self._cfg = config
        self._fallback_cfg = fallback_cfg

    @staticmethod
    def from_env(session: requests.Session) -> "DeepSeekClient":
        ...
        legacy_cfg = _build_legacy_cfg_if_present()
        if explicit_openai_signal or provider_cfg:
            if openai_api_key:
                return DeepSeekClient(session, openai_cfg, fallback_cfg=legacy_cfg)
        if legacy_cfg:
            return DeepSeekClient(session, legacy_cfg)
        ...

    def _chat_with_cfg(self, cfg: DeepSeekConfig, *, system: str, user: str, temperature: float, force_json: bool) -> str:
        prev = self._cfg
        self._cfg = cfg
        try:
            if _normalize_wire_api(self._cfg.wire_api) == "responses":
                return self._chat_via_responses(system=system, user=user, temperature=temperature, force_json=force_json)
            return self._chat_via_chat_completions(system=system, user=user, temperature=temperature, force_json=force_json)
        finally:
            self._cfg = prev

    def chat(...):
        try:
            return self._chat_with_cfg(self._cfg, system=system, user=user, temperature=temperature, force_json=force_json)
        except Exception as e:
            if not self._fallback_cfg:
                raise
            logger.warning("FinIntel: GPT call failed, fallback to DeepSeek. err=%s", repr(e))
            return self._chat_with_cfg(self._fallback_cfg, system=system, user=user, temperature=temperature, force_json=force_json)
```

**Step 2: Run tests**

Run:
```
pytest tests/test_finintel_deepseek_client.py -v
```
Expected: PASS

**Step 3: Commit**

```bash
git add finintel/deepseek_client.py tests/test_finintel_deepseek_client.py
git commit -m "feat: fallback to deepseek on gpt failure"
```
