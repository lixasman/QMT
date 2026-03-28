# signal-hot-all-50 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `--signal-hot-top` 模式下新增 `--signal-hot-all-50`，允许 50 只 ETF 全量并入情绪池，并使用 `universe_all_50` 标记，默认仍按涨幅 >1% 筛选。

**Architecture:** 扩展 `select_universe_daily_gainers` 增加 `include_all` 分支；主流程解析新参数并传入；测试覆盖选择器与批量热度流程，README 增加示例。

**Tech Stack:** Python 3, pandas, argparse, pytest.

---

### Task 1: Tests for all-50 flag and selector behavior

**Files:**
- Modify: `tests/test_finintel_hot_signal_batch.py`

**Step 1: Write failing tests**

Add new test for include_all:

```python
def test_select_universe_daily_gainers_include_all_returns_all_and_tags(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "default_universe_50.txt"
    path.write_text("512480.SH\n159107.SZ\n159998.SZ\n", encoding="utf-8")

    snap = pd.DataFrame(
        {
            "code": ["512480.SH", "159107.SZ", "159998.SZ"],
            "name": ["A", "B", "C"],
            "close": [1.02, 1.01, 0.99],
            "prev_close": [1.00, 1.00, 1.00],
        }
    )
    monkeypatch.setattr(selector, "load_latest_daily_snapshot", lambda codes: snap)

    out = selector.select_universe_daily_gainers(universe_path=path, gain_threshold=0.01, include_all=True)

    assert out["code"].tolist() == ["512480.SH", "159107.SZ", "159998.SZ"]
    assert out["source_tag"].tolist() == ["universe_all_50", "universe_all_50", "universe_all_50"]
```

Update existing monkeypatch in `test_signal_hot_top_batch_uses_union_of_hot_and_universe_gainers` to accept include_all:

```python
monkeypatch.setattr(
    finintel_main,
    "select_universe_daily_gainers",
    lambda universe_path, gain_threshold=0.01, include_all=False: pd.DataFrame(
        {
            "code": ["159107.SZ", "159998.SZ"],
            "name": ["B", "C"],
            "source_tag": ["universe_up_gt_1pct", "universe_up_gt_1pct"],
        }
    ),
)
```

Add new test for CLI flag:

```python
def test_signal_hot_top_batch_all_50_passes_include_all(monkeypatch, tmp_path: Path, capsys) -> None:
    import finintel.main as finintel_main

    called: dict[str, object] = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(finintel_main, "build_session", lambda cfg: object())
    monkeypatch.setattr(finintel_main.DeepSeekClient, "from_env", lambda session: object())
    monkeypatch.setattr(finintel_main, "_today_yyyymmdd", lambda: "20260308")
    monkeypatch.setattr(
        finintel_main,
        "select_top_hot_etfs",
        lambda top_n: pd.DataFrame(
            {
                "code": ["512480.SH", "159107.SZ"],
                "name": ["A", "B"],
                "score": [0.9, 0.8],
            }
        ),
    )
    monkeypatch.setattr(
        finintel_main,
        "_diversify_hot_pool",
        lambda top_df_raw, target_n, max_per_theme: (
            top_df_raw,
            {
                "raw_candidates": len(top_df_raw),
                "selected": len(top_df_raw),
                "max_per_theme": max_per_theme,
                "unique_themes": 2,
                "raw_theme_top": [],
                "selected_theme_top": [],
            },
        ),
    )
    monkeypatch.setattr(finintel_main, "_load_hot_top_must_include_holdings", lambda: ({}, "none"))
    monkeypatch.setattr(finintel_main, "_inject_holdings_into_hot_pool", lambda top_df, holdings: (top_df, []))

    def fake_select(universe_path, gain_threshold=0.01, include_all=False):
        called["include_all"] = include_all
        return pd.DataFrame(
            {
                "code": ["159107.SZ", "159998.SZ"],
                "name": ["B", "C"],
                "source_tag": ["universe_all_50", "universe_all_50"],
            }
        )

    monkeypatch.setattr(finintel_main, "select_universe_daily_gainers", fake_select)
    monkeypatch.setattr(finintel_main, "_load_latest_yesterday_eval", lambda etf_code_norm: "x")
    monkeypatch.setattr(
        finintel_main,
        "run_etf_signal_pipeline",
        lambda *args, **kwargs: {"deepseek_output": "ok", "sentiment_struct": {"sentiment_grade": "B", "confidence": "HIGH"}},
    )
    monkeypatch.setattr(finintel_main, "_write_signal_json_and_optional_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(finintel_main, "_write_signal_human_files", lambda *args, **kwargs: None)
    monkeypatch.setattr(finintel_main, "cleanup_old_signal_outputs", lambda **kwargs: {"deleted": 1, "failed": 0})

    rc = finintel_main.main(["--signal-hot-top", "10", "--signal-hot-all-50", "--no-trace"])

    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert called["include_all"] is True
    assert [item["source_tag"] for item in out["selected"]] == ["hot", "hot+universe_all_50", "universe_all_50"]
```

**Step 2: Run tests to confirm failures**

Run: `python -m pytest tests/test_finintel_hot_signal_batch.py -k "include_all or hot_all_50" -v`
Expected: FAIL with `TypeError: select_universe_daily_gainers() got an unexpected keyword argument 'include_all'` and/or CLI arg not recognized.

**Step 3: Commit (optional red test checkpoint)**

```bash
git add tests/test_finintel_hot_signal_batch.py
git commit -m "test: cover signal-hot-all-50 selection"
```

---

### Task 2: Implement selector branch and CLI flag wiring

**Files:**
- Modify: `finintel/etf_selector.py`
- Modify: `finintel/main.py`
- Modify: `README.md`

**Step 1: Implement include_all branch**

Update signature and logic:

```python
def select_universe_daily_gainers(
    *,
    universe_path: str | Path,
    gain_threshold: float = 0.01,
    include_all: bool = False,
) -> pd.DataFrame:
    ...
    out = snap.copy()
    out["pct_change"] = out["close"] / out["prev_close"] - 1.0
    if include_all:
        out["source_tag"] = "universe_all_50"
        return out.reset_index(drop=True)
    threshold = float(gain_threshold)
    out = out[out["pct_change"] > (threshold + 1e-12)].copy()
    out["source_tag"] = "universe_up_gt_1pct"
    return out.reset_index(drop=True)
```

**Step 2: Wire new CLI flag**

Add argument and pass through:

```python
parser.add_argument(
    "--signal-hot-all-50",
    action="store_true",
    help="仅用于 --signal-hot-top：直接把默认 50 只 ETF 全量加入情绪池（不做涨幅筛选）",
)
```

And update call:

```python
universe_gainers_df = select_universe_daily_gainers(
    universe_path=universe_path,
    gain_threshold=0.01,
    include_all=bool(args.signal_hot_all_50),
)
```

**Step 3: Update README usage**

Add example and note near existing `--signal-hot-top` usage:

```bash
python -m finintel --signal-hot-top 10 --signal-hot-all-50 --no-trace
```

And a brief bullet: “开启 `--signal-hot-all-50` 时，50 只 ETF 全量进入情绪池，`source_tag=universe_all_50`。”

**Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_finintel_hot_signal_batch.py -k "include_all or hot_all_50" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add finintel/etf_selector.py finintel/main.py README.md tests/test_finintel_hot_signal_batch.py
git commit -m "feat: add signal-hot-all-50 to include full universe"
```
