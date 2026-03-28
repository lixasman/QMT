# sentiment-history Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `--signal-hot-top` 批量流程结束后，把当日 50 只 ETF 的情绪评级追加写入累计 CSV，并按 `date+code` 去重。

**Architecture:** 复用 `summary_rows` 作为数据源，新增一个写入函数 `_append_hot_etf_sentiment_history`；读取/合并/去重后写回固定路径。

**Tech Stack:** Python 3, pandas, argparse, pytest.

---

### Task 1: Tests for history append + dedupe

**Files:**
- Modify: `tests/test_finintel_hot_signal_batch.py`

**Step 1: Write failing test**

Add a unit test for new append function:

```python
def test_append_hot_etf_sentiment_history_dedupes(tmp_path: Path, monkeypatch) -> None:
    import finintel.main as finintel_main

    monkeypatch.chdir(tmp_path)
    day = "20260318"
    rows = [
        {"code": "512480.SH", "name": "A", "grade": "B", "confidence": "HIGH"},
        {"code": "159107.SZ", "name": "B", "grade": "C", "confidence": "LOW"},
    ]

    # first write
    finintel_main._append_hot_etf_sentiment_history(rows, day)
    # second write with updated grade for one code (same day)
    rows2 = [
        {"code": "512480.SH", "name": "A", "grade": "A", "confidence": "HIGH"},
        {"code": "159107.SZ", "name": "B", "grade": "C", "confidence": "LOW"},
    ]
    finintel_main._append_hot_etf_sentiment_history(rows2, day)

    out_path = tmp_path / "output" / "finintel_50ETF_sentiment_history" / "finintel_sentiment_history.csv"
    assert out_path.exists()
    df = pd.read_csv(out_path, dtype=str)
    # deduped to 2 rows
    assert len(df) == 2
    # latest grade kept
    latest = dict(zip(df["code"], df["grade"]))
    assert latest["512480.SH"] == "A"
```

**Step 2: Run tests to confirm failures**

Run: `python -m pytest tests/test_finintel_hot_signal_batch.py -k "sentiment_history" -v`
Expected: FAIL with `AttributeError: module 'finintel.main' has no attribute '_append_hot_etf_sentiment_history'`.

**Step 3: Commit (optional red checkpoint)**

```bash
git add tests/test_finintel_hot_signal_batch.py
git commit -m "test: add sentiment history dedupe coverage"
```

---

### Task 2: Implement history append + wire into batch flow

**Files:**
- Modify: `finintel/main.py`
- Modify: `README.md`

**Step 1: Add append function**

Implement in `finintel/main.py`:

```python
def _append_hot_etf_sentiment_history(rows: list[dict[str, object]], day: str) -> None:
    if not rows:
        return
    out_dir = Path("output") / "finintel_50ETF_sentiment_history"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "finintel_sentiment_history.csv"

    new_rows: list[dict[str, object]] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        new_rows.append(
            {
                "date": str(day),
                "code": code,
                "name": str(row.get("name") or ""),
                "grade": str(row.get("grade") or ""),
                "confidence": str(row.get("confidence") or ""),
            }
        )
    if not new_rows:
        return
    new_df = pd.DataFrame(new_rows, columns=["date", "code", "name", "grade", "confidence"])

    if out_path.exists():
        try:
            old_df = pd.read_csv(out_path, dtype=str)
        except Exception:
            old_df = pd.DataFrame(columns=new_df.columns)
        merged = pd.concat([old_df, new_df], ignore_index=True)
    else:
        merged = new_df

    merged["date"] = merged["date"].astype(str)
    merged["code"] = merged["code"].astype(str)
    merged = merged.drop_duplicates(subset=["date", "code"], keep="last")
    merged.to_csv(out_path, index=False, encoding="utf-8-sig")
```

**Step 2: Wire into batch flow**

After `_emit_hot_etf_sentiment_summary(summary_rows, day)` call:

```python
_append_hot_etf_sentiment_history(summary_rows, day)
```

**Step 3: Update README**

Add a short note in FinIntel output section:

- `output/finintel_50ETF_sentiment_history/finintel_sentiment_history.csv`：累计情绪评级历史（含 date/code/grade）。

**Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_finintel_hot_signal_batch.py -k "sentiment_history" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add finintel/main.py README.md tests/test_finintel_hot_signal_batch.py
git commit -m "feat: persist hot ETF sentiment history"
```
