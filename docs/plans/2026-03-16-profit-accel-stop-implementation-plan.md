# Profit Accel Stop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional profit-accelerating Chandelier stop that tightens `k` with unrealized PnL to reduce high-level drawdown while keeping trade count growth within ~20%.

**Architecture:** Compute the base Chandelier state as today, then apply a profit-based `k` reduction using unrealized PnL, and recompute the stop from `HH` and `ATR`. Expose flags in `backtest.main` to enable/disable and tune parameters. Keep default off for easy rollback.

**Tech Stack:** Python

---

### Task 1: Add exit accel configuration API

**Files:**
- Modify: `exit/exit_config.py`
- Modify: `backtest/main.py`

**Step 1: Add config fields and accessors**

Add in `exit/exit_config.py`:

```python
_EXIT_K_ACCEL_ENABLED = False
_EXIT_K_ACCEL_STEP_PCT = 0.05
_EXIT_K_ACCEL_STEP_K = 0.2
_EXIT_K_ACCEL_KMIN = 1.0

def set_exit_k_accel(enabled: bool | None = None, step_pct: float | None = None, step_k: float | None = None, k_min: float | None = None) -> None:
    ...

def get_exit_k_accel() -> tuple[bool, float, float, float]:
    ...
```

**Step 2: Expose CLI flags**

In `backtest/main.py`, add:

- `--exit-k-accel` (bool)
- `--exit-k-accel-step-pct` (float, default 0.05)
- `--exit-k-accel-step-k` (float, default 0.2)
- `--exit-k-accel-kmin` (float, default 1.0)

Wire them into `set_exit_k_accel(...)`.

**Step 3: Commit**

```bash
git add exit/exit_config.py backtest/main.py
git commit -m "feat: add profit accel stop config"
```

### Task 2: Implement profit accel k calculation

**Files:**
- Modify: `strategy_runner.py`
- (Optional) Modify: `exit/chandelier.py` or create `exit/accel.py`

**Step 1: Add helper function**

Add a small helper (either inside `strategy_runner.py` or a new `exit/accel.py`) to compute accelerated `k`:

```python
def compute_accel_k(k_base: float, pnl_pct: float, step_pct: float, step_k: float, k_min: float) -> float:
    if pnl_pct <= 0 or k_base <= 0:
        return k_base
    steps = int(pnl_pct // step_pct)
    return max(k_min, k_base - steps * step_k)
```

**Step 2: Apply to Chandelier stop**

In `_compute_stop` (strategy_runner), after `compute_chandelier_state(...)` returns `hh`, `atr`, `k`:

- Compute `pnl_pct = (last_price - ps.avg_cost) / ps.avg_cost` (guard avg_cost > 0).
- Read accel params from `exit_config.get_exit_k_accel()`.
- If enabled, compute `k_adj` and recompute `stop = hh - k_adj * atr`.
- Return adjusted stop and `k_adj`.

**Step 3: Commit**

```bash
git add strategy_runner.py exit/accel.py
git commit -m "feat: apply profit accel k to chandelier stop"
```

### Task 3: Tests

**Files:**
- Create: `tests/test_exit/test_profit_accel_k.py`

**Step 1: Write test for helper**

```python
from exit.accel import compute_accel_k

def test_accel_k_no_profit():
    assert compute_accel_k(2.8, 0.0, 0.05, 0.2, 1.0) == 2.8

def test_accel_k_steps():
    # 10% profit -> 2 steps
    assert compute_accel_k(2.8, 0.10, 0.05, 0.2, 1.0) == 2.4

def test_accel_k_floor():
    assert compute_accel_k(1.2, 1.0, 0.05, 0.2, 1.0) == 1.0
```

**Step 2: Run test**

Run: `pytest tests/test_exit/test_profit_accel_k.py -v`

Expected: PASS.

**Step 3: Commit**

```bash
git add tests/test_exit/test_profit_accel_k.py
git commit -m "test: add profit accel k unit tests"
```

### Task 4: Backtest validation (588200 only)

**Step 1: Baseline with ATR clamp**

Run:

```bash
python -m backtest.main --start 20250101 --end 20251231 --data-root backtest/data --out-dir output/backtest_588200_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040 --codes 588200.SH --no-watch-auto --initial-cash 300000 --light-logs --bt-no-reentry-after-confirm --exit-atr-pct-min 0.025 --exit-atr-pct-max 0.04
```

**Step 2: Baseline + profit accel**

Run:

```bash
python -m backtest.main --start 20250101 --end 20251231 --data-root backtest/data --out-dir output/backtest_588200_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040_accel --codes 588200.SH --no-watch-auto --initial-cash 300000 --light-logs --bt-no-reentry-after-confirm --exit-atr-pct-min 0.025 --exit-atr-pct-max 0.04 --exit-k-accel --exit-k-accel-step-pct 0.05 --exit-k-accel-step-k 0.2 --exit-k-accel-kmin 1.0
```

**Step 3: Evaluate**

Compare `max_drawdown`, `annualized_return`, `trade_count` (<= +20%).

---

Plan complete and saved to `docs/plans/2026-03-16-profit-accel-stop-implementation-plan.md`. Two execution options:

1. Subagent-Driven (this session) - I dispatch fresh subagent per task, review between tasks, fast iteration
2. Parallel Session (separate) - Open new session with executing-plans, batch execution with checkpoints

Which approach?
