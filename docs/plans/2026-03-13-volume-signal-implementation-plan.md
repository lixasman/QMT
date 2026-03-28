# 分级量能信号 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将入场 Phase2 的 S_volume 从二值改为分级 0/0.5/1，并完成 588200 在 2025 年全年的回测对比。

**Architecture:** 仅修改 `entry/signals/volume_break.py` 的量能突破评分逻辑，维持原有评分权重与阈值不变；新增单测覆盖 0/0.5/1 三档返回。

**Tech Stack:** Python 3.10+, pytest

---

### Task 1: 添加分级量能信号单测

**Files:**
- Create: `tests/test_entry/test_volume_break.py`

**Step 1: 确认 Python 可用**

Run: `Get-Command python`

Expected: 返回 python 可执行路径。

**Step 2: 写入失败用例**

```python
from __future__ import annotations

from datetime import date, datetime, time

from core.interfaces import Bar
from entry.signals.volume_break import compute_volume_break


def _dbar(d: date, *, o: float, h: float, l: float, c: float, v: float, a: float = 0.0) -> Bar:
    return Bar(time=datetime.combine(d, time(15, 0)), open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v), amount=float(a))


def _make_bars(*, base_vol: float, last_vol: float, last_open: float, last_high: float, last_low: float, last_close: float) -> list[Bar]:
    bars: list[Bar] = []
    d0 = date(2025, 1, 1)
    for i in range(20):
        bars.append(_dbar(date(2025, 1, 1 + i), o=1.0, h=1.1, l=0.9, c=1.0, v=base_vol))
    bars.append(_dbar(date(2025, 1, 21), o=last_open, h=last_high, l=last_low, c=last_close, v=last_vol))
    return bars


def test_volume_break_strong_returns_one() -> None:
    bars = _make_bars(base_vol=100.0, last_vol=160.0, last_open=1.0, last_high=2.0, last_low=0.9, last_close=2.0)
    out = compute_volume_break(bars, resistance_price=1.1)
    assert out == 1.0


def test_volume_break_moderate_returns_half() -> None:
    bars = _make_bars(base_vol=100.0, last_vol=130.0, last_open=1.0, last_high=2.0, last_low=0.9, last_close=2.0)
    out = compute_volume_break(bars, resistance_price=1.1)
    assert out == 0.5


def test_volume_break_weak_or_no_break_returns_zero() -> None:
    bars = _make_bars(base_vol=100.0, last_vol=110.0, last_open=1.0, last_high=1.2, last_low=0.9, last_close=1.1)
    out = compute_volume_break(bars, resistance_price=1.2)
    assert out == 0.0
```

**Step 3: 运行测试，确认失败**

Run: `python -m pytest -q tests/test_entry/test_volume_break.py`

Expected: FAIL，提示 0.5 断言失败。若提示缺少依赖，停止并请用户确认环境。

### Task 2: 实现分级量能信号

**Files:**
- Modify: `entry/signals/volume_break.py`

**Step 1: 修改实现**

```python
def compute_volume_break(bars: list[Bar], resistance_price: Optional[float]) -> float:
    if len(bars) < 21:
        return 0.0
    t = len(bars) - 1
    close_t = float(bars[t].close)
    open_t = float(bars[t].open)
    high_t = float(bars[t].high)
    low_t = float(bars[t].low)
    vol_t = float(bars[t].volume)

    if resistance_price is None:
        resistance = max(float(b.high) for b in bars[t - 20 : t])
    else:
        resistance = float(resistance_price)

    price_break = bool(close_t > resistance)

    vol_ma20 = sum(float(b.volume) for b in bars[t - 20 : t]) / 20.0
    vol_ratio = (vol_t / vol_ma20) if vol_ma20 > 0 else 0.0

    denom = max(high_t - low_t, 0.001)
    body_ratio = (close_t - open_t) / denom
    solid_candle = bool(body_ratio > 0.5)

    if not (price_break and solid_candle):
        return 0.0
    if vol_ratio >= 1.5:
        return 1.0
    if vol_ratio >= 1.2:
        return 0.5
    return 0.0
```

**Step 2: 运行测试，确认通过**

Run: `python -m pytest -q tests/test_entry/test_volume_break.py`

Expected: PASS。

**Step 3: Commit**

```bash
git -C D:\Quantitative_Trading add D:\Quantitative_Trading\entry\signals\volume_break.py D:\Quantitative_Trading\tests\test_entry\test_volume_break.py
git -C D:\Quantitative_Trading commit -m "feat: grade volume break signal"
```

### Task 3: 回测 588200（2025 全年）并对比

**Files:**
- Output: `output/backtest_588200_vol15_voltier/`

**Step 1: 运行回测**

Run:

```bash
python -m backtest.main --start 20250101 --end 20251231 --data-root backtest/data --codes 588200.SH --out-dir output/backtest_588200_vol15_voltier --initial-cash 300000 --fee-bps 0.85
```

Expected: 生成 `summary.json`、`daily_equity.csv`、`fills.csv` 与 logs。若因筹码文件缺失报错，改用 `--allow-missing-chip` 重跑。

**Step 2: 输出对比摘要**

Run:

```powershell
Get-Content output/backtest_588200_vol15_voltier/summary.json
Get-Content output/backtest_588200_vol15/summary.json
```

Expected: 得到新旧年化、回撤、交易次数等对比信息，供人工判断是否回退。
