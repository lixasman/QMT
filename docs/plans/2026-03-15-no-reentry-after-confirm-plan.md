# 回测确认单后禁止再入场 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在回测中新增可选开关，确认单成交后禁止同一 ETF 再入场，默认不影响现有行为。
**Architecture:** 通过回测 CLI 传入布尔开关到 BacktestEngine，并下传到 BacktestStrategyRunner；在 `_allow_phase2_candidate` 增加“确认单成交后禁止再入场”的门禁，仅拦截 S2 及以上状态。该门禁仅存在于回测侧，不触及实盘主流程与 StrategyRunner。
**Tech Stack:** Python, argparse, pytest, backtest runner

---

### Task 1: 回测 CLI 开关透传到 BacktestEngine

**Files:**
- Modify: `backtest/main.py`
- Test: `tests/test_backtest_main.py`

**Step 1: Write the failing test**

```python
def test_main_passes_no_reentry_after_confirm_flag(tmp_path: Path, monkeypatch) -> None:
    captured_engine: dict[str, object] = {}

    monkeypatch.setattr(backtest_main, "warn_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_main, "setup_backtest_logging", lambda out_dir: {"run_tag": "t", "log_path": str(tmp_path / "run.log")})
    monkeypatch.setattr(backtest_main, "_preflight_chip_coverage", lambda **kwargs: (tmp_path / "batch_results_20240102.csv", []))
    monkeypatch.setattr(backtest_main, "StrategyConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(backtest_main, "MarketDataStore", lambda **kwargs: SimpleNamespace(**kwargs))

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            captured_engine.update(kwargs)
        def run(self):
            return SimpleNamespace(summary={"final_nav": 1_000_000.0, "total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0})

    monkeypatch.setattr(backtest_main, "BacktestEngine", _FakeEngine)
    monkeypatch.setattr(backtest_main, "write_backtest_result", lambda **kwargs: {
        "summary": str(tmp_path / "summary.json"),
        "daily_equity": str(tmp_path / "daily_equity.csv"),
        "fills": str(tmp_path / "fills.csv"),
    })

    rc = backtest_main.main(argv=[
        "--start", "20240103", "--end", "20240110",
        "--codes", "512480.SH",
        "--out-dir", str(tmp_path / "out"),
        "--bt-no-reentry-after-confirm",
    ])

    assert rc == 0
    assert bool(captured_engine.get("bt_no_reentry_after_confirm")) is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_main.py::test_main_passes_no_reentry_after_confirm_flag -v`
Expected: FAIL (argument not recognized / flag not passed)

**Step 3: Write minimal implementation**

```python
# backtest/main.py (add CLI arg)
p.add_argument(
    "--bt-no-reentry-after-confirm",
    action="store_true",
    help="backtest only: block new entries after confirm fill until exit",
)

# pass into BacktestEngine(...)
engine = BacktestEngine(
    ...,
    bt_no_reentry_after_confirm=bool(ns.bt_no_reentry_after_confirm),
)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_main.py::test_main_passes_no_reentry_after_confirm_flag -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_backtest_main.py backtest/main.py
git commit -m "test: cover backtest no-reentry flag plumbing"
```

---

### Task 2: 回测门禁：确认单成交后禁止再入场

**Files:**
- Modify: `backtest/runner.py`
- Test: `tests/test_backtest_no_reentry_after_confirm.py`

**Step 1: Write the failing test**

```python
from datetime import datetime
from pathlib import Path

from backtest.adapters import BacktestDataAdapter, BacktestTradingAdapter
from backtest.clock import SimulatedClock
from backtest.runner import BacktestStrategyRunner
from backtest.state_manager import InMemoryStateManager
from backtest.store import MarketDataStore
from core.enums import FSMState
from core.models import PortfolioState
from entry.types import WatchlistItem
from strategy_config import StrategyConfig

def _write_daily_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("time,open,high,low,close,volume,amount\n", encoding="utf-8")

def test_backtest_no_reentry_after_confirm_blocks_phase2(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    tick_root = tmp_path / "ticks"
    _write_daily_csv(data_root / "1d" / "512480_SH.csv")

    clock = SimulatedClock(datetime(2025, 3, 6, 9, 35, 0))
    store = MarketDataStore(data_root=data_root, codes=["512480.SH"], tick_root=tick_root, load_minute=False)
    data = BacktestDataAdapter(store=store, clock=clock)
    trading = BacktestTradingAdapter(clock=clock, initial_cash=100000.0, fee_rate=0.0, enable_t0=False)
    state_manager = InMemoryStateManager(initial_state=PortfolioState(nav=100000.0, cash=100000.0))
    cfg = StrategyConfig(
        state_path=str(tmp_path / "state" / "portfolio.json"),
        entry_log_path=str(tmp_path / "logs" / "entry.jsonl"),
        exit_log_path=str(tmp_path / "logs" / "exit.jsonl"),
        position_log_path=str(tmp_path / "logs" / "position.jsonl"),
        t0_log_path=str(tmp_path / "logs" / "t0.jsonl"),
        watchlist_etf_codes=("512480.SH",),
        phase2_min_chip_days=0,
        phase2_open_coverage_window=0,
        phase2_min_open_coverage=0.0,
        phase2_micro_coverage_window=0,
        phase2_min_micro_coverage=0.0,
    )
    runner = BacktestStrategyRunner(
        config=cfg,
        data=data,
        trading=trading,
        state_manager=state_manager,
        fee_rate=0.0,
        disable_t0_ops=True,
        bt_no_reentry_after_confirm=True,
    )
    ps = runner._pos_fsm.upsert_position(etf_code="512480.SH")
    ps.state = FSMState.S2_BASE

    item = WatchlistItem(etf_code="512480.SH", sentiment_score=50, profit_ratio=0.0)
    ok, _ = runner._allow_phase2_candidate(now=datetime(2025, 3, 6, 15, 1, 0), item=item)

    assert ok is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_no_reentry_after_confirm.py::test_backtest_no_reentry_after_confirm_blocks_phase2 -v`
Expected: FAIL (constructor/logic missing)

**Step 3: Write minimal implementation**

```python
# backtest/runner.py
class BacktestStrategyRunner(StrategyRunner):
    def __init__(..., bt_no_reentry_after_confirm: bool = False, ...) -> None:
        self._bt_no_reentry_after_confirm = bool(bt_no_reentry_after_confirm)
        ...

    def _allow_phase2_candidate(self, *, now: datetime, item: WatchlistItem) -> tuple[bool, str]:
        code = str(item.etf_code or "").strip().upper()
        if self._bt_no_reentry_after_confirm:
            st = self._pos_fsm.get_position_state(code)
            if st in (FSMState.S2_BASE, FSMState.S3_SCALED, FSMState.S4_FULL, FSMState.S5_REDUCED):
                return False, "no_reentry_after_confirm"
        ...  # existing gates

class BacktestEngine:
    def __init__(..., bt_no_reentry_after_confirm: bool = False, ...) -> None:
        self._bt_no_reentry_after_confirm = bool(bt_no_reentry_after_confirm)
        ...

    def run(self) -> BacktestResult:
        ...
        runner = BacktestStrategyRunner(..., bt_no_reentry_after_confirm=self._bt_no_reentry_after_confirm)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_no_reentry_after_confirm.py::test_backtest_no_reentry_after_confirm_blocks_phase2 -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backtest/runner.py tests/test_backtest_no_reentry_after_confirm.py
git commit -m "feat: backtest gate blocks reentry after confirm fill"
```

---

### Task 3: 对照回测（手工执行）

**Files:**
- None (run backtest commands)

**Step 1: Run baseline with flag**

Run:
`python -m backtest.main --start 20250101 --end 20251231 --data-root backtest/data --out-dir output/backtest_159825_20250101_20251231_baseline_cash300k_noreentry --codes 159825.SZ --no-watch-auto --initial-cash 300000 --light-logs --bt-no-reentry-after-confirm`

Expected: `summary.json`/`fills.csv`/`daily_equity.csv` produced.

**Step 2: Run paramtest with flag**

Run:
`python -m backtest.main --start 20250101 --end 20251231 --data-root backtest/data --out-dir output/backtest_159825_20250101_20251231_paramtest_cash300k_noreentry --codes 159825.SZ --no-watch-auto --initial-cash 300000 --phase2-score-threshold 0.35 --exit-k-normal 2.0 --exit-k-chip-decay 1.8 --exit-k-reduced 1.5 --exit-layer2-threshold 0.7 --light-logs --bt-no-reentry-after-confirm`

Expected: 对照输出目录生成，买点数量减少。

---

**Notes:** 执行本计划需使用 `@superpowers:executing-plans`。
