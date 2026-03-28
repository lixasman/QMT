# ETF Signal Pool + GPT Switch Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 ETF 批量情绪分析从“热门 15 只”升级为“热门 10 只 + 固定 50 池中当日涨幅大于 1% ETF”的并集分析，同时切换到 GPT 兼容接口，并在批量运行后自动清理 3 天前的历史情绪分析产物。

**Architecture:** 保留 `finintel/main.py` 中现有 `--signal-hot-top` 入口作为唯一批量编排点，在 `finintel/etf_selector.py` 中追加固定 50 池文件读取与最新日涨幅筛选辅助函数，并在 `finintel/main.py` 中完成并集、来源标记和历史清理。`finintel/deepseek_client.py` 保持 OpenAI 兼容请求结构，只调整默认环境变量、模型和网关。`strategy_config.py` 仅下调默认 `hot_top`。

**Tech Stack:** Python 3、pandas、requests、pytest、PowerShell

---

**Plan Constraints:**

- 不修改依赖声明或锁文件
- 不运行安装/恢复依赖命令
- 实施时优先使用 `@superpowers:test-driven-development`
- 若遇到异常行为，先使用 `@superpowers:systematic-debugging`
- 完成前使用 `@superpowers:verification-before-completion`
- 本仓库当前约束下**不要执行 `git commit`**，除非用户明确要求

### Task 1: 固定 50 池文件读取与涨幅筛选测试

**Files:**
- Create: `tests/test_finintel_hot_signal_batch.py`
- Modify: `finintel/etf_selector.py`

**Step 1: Write the failing test**

```python
from pathlib import Path

import pandas as pd

import finintel.etf_selector as selector


def test_load_default_universe_codes_reads_non_empty_lines(tmp_path) -> None:
    path = tmp_path / "default_universe_50.txt"
    path.write_text("512480.SH\n\n159107.SZ\n", encoding="utf-8")

    out = selector.load_universe_etf_codes(path)

    assert out == ["512480.SH", "159107.SZ"]


def test_select_universe_daily_gainers_filters_strictly_above_threshold(monkeypatch, tmp_path) -> None:
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

    out = selector.select_universe_daily_gainers(universe_path=path, gain_threshold=0.01)

    assert out["code"].tolist() == ["512480.SH"]
    assert out["source_tag"].tolist() == ["universe_up_gt_1pct"]
```

**Step 2: Run test to verify it fails**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: FAIL，提示 `load_universe_etf_codes` 或 `select_universe_daily_gainers` 尚不存在。

**Step 3: Write minimal implementation**

```python
def load_universe_etf_codes(path: str | Path) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def select_universe_daily_gainers(*, universe_path: str | Path, gain_threshold: float = 0.01) -> pd.DataFrame:
    codes = load_universe_etf_codes(universe_path)
    snap = load_latest_daily_snapshot(codes)
    snap = snap.copy()
    snap["pct_change"] = snap["close"] / snap["prev_close"] - 1.0
    out = snap[snap["pct_change"] > float(gain_threshold)].copy()
    out["source_tag"] = "universe_up_gt_1pct"
    return out.reset_index(drop=True)
```

**Step 4: Run test to verify it passes**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: PASS，两个测试通过。

**Step 5: Record checkpoint**

- 不提交 Git；仅确认 `tests/test_finintel_hot_signal_batch.py` 与 `finintel/etf_selector.py` 变更范围正确。

### Task 2: 固定 50 池最新日线快照实现与缺失容错

**Files:**
- Modify: `finintel/etf_selector.py`
- Test: `tests/test_finintel_hot_signal_batch.py`

**Step 1: Write the failing test**

```python
def test_load_latest_daily_snapshot_skips_codes_with_insufficient_history(monkeypatch) -> None:
    part = pd.DataFrame(
        {
            "code": ["512480.SH", "512480.SH", "159107.SZ"],
            "time": ["20260306", "20260307", "20260307"],
            "close": [1.0, 1.03, 2.0],
        }
    )
    monkeypatch.setattr(selector, "fetch_daily_history_for_codes", lambda codes, history_days=2: part)

    out = selector.load_latest_daily_snapshot(["512480.SH", "159107.SZ"])

    assert out["code"].tolist() == ["512480.SH"]
    assert out.iloc[0]["prev_close"] == 1.0
    assert out.iloc[0]["close"] == 1.03
```

**Step 2: Run test to verify it fails**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: FAIL，提示 `load_latest_daily_snapshot` 或 `fetch_daily_history_for_codes` 缺失。

**Step 3: Write minimal implementation**

```python
def load_latest_daily_snapshot(codes: list[str]) -> pd.DataFrame:
    history = fetch_daily_history_for_codes(codes, history_days=2)
    rows: list[dict[str, object]] = []
    for code, grp in history.sort_values(["code", "time"]).groupby("code", sort=False):
        if len(grp) < 2:
            logger.warning("HotETF: insufficient daily history for %s", code)
            continue
        last2 = grp.tail(2)
        rows.append(
            {
                "code": code,
                "name": str(last2.iloc[-1].get("name") or ""),
                "prev_close": float(last2.iloc[0]["close"]),
                "close": float(last2.iloc[1]["close"]),
            }
        )
    return pd.DataFrame(rows, columns=["code", "name", "prev_close", "close"])
```

**Step 4: Run test to verify it passes**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: PASS，历史不足的 ETF 被跳过。

**Step 5: Record checkpoint**

- 不提交 Git；确认新增 helper 没有影响 `select_top_hot_etfs(...)` 原有逻辑。

### Task 3: 热门池与补充池并集、来源标记与默认 Top10

**Files:**
- Modify: `finintel/main.py`
- Modify: `strategy_config.py`
- Test: `tests/test_finintel_hot_signal_batch.py`

**Step 1: Write the failing test**

```python
import pandas as pd

from finintel.main import merge_signal_candidate_pools
from strategy_config import StrategyConfig


def test_merge_signal_candidate_pools_deduplicates_and_merges_source_tags() -> None:
    hot = pd.DataFrame(
        {
            "code": ["512480.SH", "159107.SZ"],
            "name": ["A", "B"],
            "score": [0.9, 0.8],
            "source_tag": ["hot", "hot"],
        }
    )
    gainers = pd.DataFrame(
        {
            "code": ["159107.SZ", "159998.SZ"],
            "name": ["B", "C"],
            "source_tag": ["universe_up_gt_1pct", "universe_up_gt_1pct"],
        }
    )

    out = merge_signal_candidate_pools(hot, gainers)

    assert out["code"].tolist() == ["512480.SH", "159107.SZ", "159998.SZ"]
    assert out["source_tag"].tolist() == ["hot", "hot+universe_up_gt_1pct", "universe_up_gt_1pct"]


def test_strategy_config_default_hot_top_is_10() -> None:
    assert StrategyConfig().hot_top == 10
```

**Step 2: Run test to verify it fails**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: FAIL，提示 `merge_signal_candidate_pools` 不存在，且 `hot_top` 仍为 `15`。

**Step 3: Write minimal implementation**

```python
def merge_signal_candidate_pools(hot_df: pd.DataFrame, gainers_df: pd.DataFrame) -> pd.DataFrame:
    hot = hot_df.copy()
    hot["source_tag"] = hot.get("source_tag", "hot")
    gainers = gainers_df.copy()
    gainers["score"] = gainers.get("score", 0.0)
    merged = pd.concat([hot, gainers], ignore_index=True, sort=False)
    merged = merged.groupby("code", as_index=False).agg(
        {
            "name": "first",
            "score": "max",
            "source_tag": lambda xs: "+".join(sorted(dict.fromkeys(str(x) for x in xs if str(x).strip()))),
        }
    )
    return merged


@dataclass(frozen=True)
class StrategyConfig:
    hot_top: int = 10
```

**Step 4: Run test to verify it passes**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: PASS，并集结果稳定且默认热门数量为 10。

**Step 5: Record checkpoint**

- 不提交 Git；人工确认 `finintel/main.py` 中 `final_obj["top_n"]` 与汇总 CSV/JSON 中保留了新的来源字段。

### Task 4: 批量后自动清理 3 天前历史结果

**Files:**
- Modify: `finintel/main.py`
- Test: `tests/test_finintel_hot_signal_batch.py`

**Step 1: Write the failing test**

```python
from pathlib import Path

from finintel.main import cleanup_old_signal_outputs


def test_cleanup_old_signal_outputs_only_removes_matching_old_files(tmp_path) -> None:
    old_json = tmp_path / "finintel_signal_512480_20260301.json"
    old_eval = tmp_path / "eval" / "finintel_signal_eval_512480_20260301.md"
    keep_json = tmp_path / "finintel_signal_512480_20260307.json"
    keep_other = tmp_path / "other_module_20260301.json"
    old_eval.parent.mkdir(parents=True)
    old_json.write_text("x", encoding="utf-8")
    old_eval.write_text("x", encoding="utf-8")
    keep_json.write_text("x", encoding="utf-8")
    keep_other.write_text("x", encoding="utf-8")

    summary = cleanup_old_signal_outputs(output_dir=tmp_path, today_yyyymmdd="20260308", retention_days=3)

    assert summary["deleted"] == 2
    assert not old_json.exists()
    assert not old_eval.exists()
    assert keep_json.exists()
    assert keep_other.exists()
```

**Step 2: Run test to verify it fails**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: FAIL，提示清理函数不存在。

**Step 3: Write minimal implementation**

```python
def cleanup_old_signal_outputs(*, output_dir: str | Path = "output", today_yyyymmdd: str, retention_days: int = 3) -> dict[str, int]:
    root = Path(output_dir)
    deleted = 0
    cutoff = (datetime.strptime(today_yyyymmdd, "%Y%m%d") - timedelta(days=retention_days)).strftime("%Y%m%d")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if not name.startswith("finintel_signal"):
            continue
        match = re.search(r"(\d{8})", name)
        if not match:
            continue
        if match.group(1) < cutoff:
            path.unlink()
            deleted += 1
    return {"deleted": deleted}
```

**Step 4: Run test to verify it passes**

```powershell
pytest tests/test_finintel_hot_signal_batch.py -q
```

Expected: PASS，只删除超过保留期的 `finintel_signal*` 文件。

**Step 5: Record checkpoint**

- 不提交 Git；确认主流程在批量结束后调用清理函数，且清理失败只记 warning。

### Task 5: GPT 兼容接口默认值与环境变量切换

**Files:**
- Modify: `finintel/deepseek_client.py`
- Create: `tests/test_finintel_deepseek_client.py`
- Optional Modify: `README.md`

**Step 1: Write the failing test**

```python
import os

import requests

from finintel.deepseek_client import DeepSeekClient


def test_from_env_reads_crs_oai_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CRS_OAI_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    client = DeepSeekClient.from_env(requests.Session())

    assert client._cfg.api_key == "test-key"
    assert client._cfg.base_url == "https://www.leishen-ai.cn/openai"
    assert client._cfg.model == "gpt-5.4"
```

**Step 2: Run test to verify it fails**

```powershell
pytest tests/test_finintel_deepseek_client.py -q
```

Expected: FAIL，当前实现仍读取 `DEEPSEEK_API_KEY` / `https://api.deepseek.com` / `deepseek-chat`。

**Step 3: Write minimal implementation**

```python
@staticmethod
def from_env(session: requests.Session) -> "DeepSeekClient":
    api_key = os.environ.get("CRS_OAI_KEY", "").strip()
    if not api_key:
        raise DeepSeekError("CRS_OAI_KEY 未设置")
    base_url = os.environ.get("OPENAI_COMPAT_BASE_URL", "https://www.leishen-ai.cn/openai").strip()
    model = os.environ.get("OPENAI_COMPAT_MODEL", "gpt-5.4").strip()
    ...
```

**Step 4: Run test to verify it passes**

```powershell
pytest tests/test_finintel_deepseek_client.py -q
```

Expected: PASS，客户端默认值切换成功。

**Step 5: Update operator docs**

- 若 README 中仍描述为 DeepSeek 默认环境变量，同步更新对应段落。

验证命令：

```powershell
pytest tests/test_finintel_deepseek_client.py -q
```

Expected: PASS，且 README 中不再把 `DEEPSEEK_API_KEY` 作为默认必填项。

### Task 6: 批量主流程回归验证

**Files:**
- Test: `tests/test_finintel_hot_signal_batch.py`
- Test: `tests/test_finintel_deepseek_client.py`
- Optional Test: `tests/test_finintel_pipeline.py`

**Step 1: Add one high-level orchestration test**

```python
def test_signal_hot_top_batch_uses_union_of_hot_and_universe_gainers(monkeypatch, tmp_path) -> None:
    ...
    # mock 热门池 2 只，其中 1 只与固定池上涨补充池重合
    # mock run_etf_signal_pipeline 与写文件逻辑
    # 断言最终仅分析并输出 3 只 ETF，且 source_tag 正确
```

**Step 2: Run focused suite**

```powershell
pytest tests/test_finintel_hot_signal_batch.py tests/test_finintel_deepseek_client.py -q
```

Expected: PASS。

**Step 3: Run adjacent FinIntel regression tests**

```powershell
pytest tests/test_finintel_pipeline.py tests/test_finintel_etf_pipeline.py -q
```

Expected: PASS；若失败，应仅处理与本次改动直接相关的问题。

**Step 4: Manual smoke command (user-local data only)**

```powershell
python -m finintel --signal-hot-top 10 --no-trace
```

Expected: 

- 生成当日 `output/finintel_signal_hot_<YYYYMMDD>.csv`
- 若固定 50 池中存在涨幅 `>1%` 的 ETF，则汇总数量大于等于 10
- `output/` 与 `output/eval/` 中 3 天前的 `finintel_signal*` 历史文件被清理

**Step 5: Final checkpoint**

- 确认所有定向测试通过
- 确认未修改依赖文件
- 确认未执行 `git commit`

