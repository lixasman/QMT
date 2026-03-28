# HotETF Sentiment Summary Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 `python -m finintel --signal-hot-top N --no-trace` 在 stderr 输出当日候选 ETF 的情绪评级汇总（含评级/分数/置信度），覆盖已跳过的 ETF。

**Architecture:** 新增“当日情绪摘要读取”辅助函数，优先读 `output/integration/finintel/sentiment_*.json`，缺失则降级读 `output/finintel_signal_*.json` 并用现有映射生成评分；在热榜批处理循环中收集每只 ETF 的摘要，结束后统一写入 stderr。

**Tech Stack:** Python 3.10+，标准库（json/pathlib/logging），现有 finintel 解析逻辑。

---

### Task 1: Add Tests For Sentiment Summary Loading

**Files:**
- Modify: `tests/test_finintel_hot_signal_batch.py`

**Step 1: Write the failing test**

```python

def test_load_today_sentiment_summary_prefers_integration(tmp_path, monkeypatch):
    import json
    from finintel import main as fin_main

    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "output" / "integration" / "finintel"
    out_dir.mkdir(parents=True)
    (out_dir / "sentiment_512480_20260308.json").write_text(
        json.dumps({
            "etf_code": "512480.SH",
            "day": "20260308",
            "sentiment_grade": "A",
            "confidence": "HIGH",
            "sentiment_score_100": 88,
            "sentiment_score_01": 0.88,
        }),
        encoding="utf-8",
    )

    summary = fin_main._load_today_sentiment_summary("512480.SH", "20260308")
    assert summary["grade"] == "A"
    assert summary["score_100"] == 88
    assert summary["confidence"] == "HIGH"
```

**Step 2: Run test to verify it fails**

Run:

```
Wait-Process -Id (Start-Process -FilePath "python" -ArgumentList "-m pytest tests/test_finintel_hot_signal_batch.py -k load_today_sentiment_summary -q" -PassThru -NoNewWindow).Id -Timeout 60
```

Expected: FAIL with missing `_load_today_sentiment_summary` or assertion errors.

**Step 3: Commit**

```
git add tests/test_finintel_hot_signal_batch.py
git commit -m "test: add sentiment summary loading"
```

### Task 2: Implement HotETF Summary Loading + Logging

**Files:**
- Modify: `finintel/main.py`

**Step 1: Write minimal implementation**

Add helpers:
- `_load_today_sentiment_summary(etf_code_norm: str, day: str) -> dict`
- `_emit_hot_etf_sentiment_summary(rows: list[dict], day: str) -> None`

Integrate into `--signal-hot-top` loop:
- After each ETF completes or is skipped, call `_load_today_sentiment_summary` and append `{"code","name","grade","score_100","confidence"}` to a list.
- After the loop, call `_emit_hot_etf_sentiment_summary` to write stderr lines.

**Step 2: Run tests**

Run:

```
Wait-Process -Id (Start-Process -FilePath "python" -ArgumentList "-m pytest tests/test_finintel_hot_signal_batch.py -k load_today_sentiment_summary -q" -PassThru -NoNewWindow).Id -Timeout 60
```

Expected: PASS.

**Step 3: Commit**

```
git add finintel/main.py
git commit -m "feat: summarize hot etf sentiment to stderr"
```

### Task 3: Validate End-to-End Behavior (Optional)

**Files:**
- None (runtime check)

**Step 1: Run hot signal batch (optional, user-run)**

Run:

```
python -m finintel --signal-hot-top 10 --no-trace
```

Expected: stderr includes `HotETF Summary YYYYMMDD (n=...)` and per-ETF lines with评级/分数/置信度。

---

**Execution Handoff**

Plan complete and saved to `docs/plans/2026-03-18-hot-etf-sentiment-summary-implementation-plan.md`. Two execution options:

1. Subagent-Driven (this session)
2. Parallel Session (separate)

Which approach?
