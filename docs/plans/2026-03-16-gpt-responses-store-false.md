# GPT Responses Store-False Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 GPT（Leishen OpenAI-compat）/v1/responses 请求加入 store=false，避免 400 拒绝。

**Architecture:** 在 DeepSeekClient 的 responses payload 构造处按 base_url 条件注入 store=false，不影响 DeepSeek 直连与降级逻辑。

**Tech Stack:** Python, requests, pytest

---

### Task 1: Add failing test for store=false in responses payload

**Files:**
- Modify: `tests/test_finintel_deepseek_client.py:47`

**Step 1: Write the failing test**

```python
# 在 test_chat_supports_responses_stream_and_collects_text 中新增断言：
assert payload["store"] is False
```

**Step 2: Run test to verify it fails**

Run:
```
python -m pytest tests/test_finintel_deepseek_client.py::test_chat_supports_responses_stream_and_collects_text -v
```
Expected: FAIL (payload 不含 store)

**Step 3: Commit (optional, only if user requests)**

```bash
git add tests/test_finintel_deepseek_client.py
git commit -m "test: assert responses store flag"
```

---

### Task 2: Implement store=false for GPT responses

**Files:**
- Modify: `finintel/deepseek_client.py:305`

**Step 1: Minimal implementation**

```python
base_url_norm = active_cfg.base_url.rstrip("/").lower()
if "leishen-ai.cn/openai" in base_url_norm:
    payload["store"] = False
```

**Step 2: Run tests**

Run:
```
python -m pytest tests/test_finintel_deepseek_client.py -v
```
Expected: PASS

**Step 3: Commit (optional, only if user requests)**

```bash
git add finintel/deepseek_client.py
git commit -m "feat: set store=false for gpt responses"
```
