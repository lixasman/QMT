# 入场策略（Entry）使用说明

## 目标与边界

- 目标：将 `entry_strategy_specification.md` v1.3 的 Phase 2/Phase 3 逻辑落地为可复用模块，并按 `entry_implementation_verification.md` v1.1 的硬约束用断言与单测防止沉默逻辑错误。
- 边界：Phase 1 由你现有筛选策略产出候选池，本模块只提供候选池数据结构/加载校验接口，不重复实现筛选逻辑。
- 交易与数据：入场业务逻辑不直接调用 XtQuant API，只依赖 `core` 的 `DataAdapter`/`TradingAdapter`；因此可在 XtQuant 模拟盘与 GUI 实盘之间切换适配器而不改入场逻辑。

## 目录结构

- `entry/watchlist.py`：候选池加载与校验（你每日跑完筛选后把结果喂进来）
- `entry/phase2.py` + `entry/signals/*` + `entry/scoring.py`：五信号计算与固定权重评分/门控
- `entry/vwap_tracker.py`：VWAP 累计与热身期/锚点管理
- `entry/phase3_confirmer.py`：盘中确认（跳空保护/VWAP/IOPV/14:30/STALE）
- `entry/entry_fsm.py`：pending_entries 状态写入骨架、崩溃恢复对账骨架、下单后的状态落盘与日志记录
- `entry/entry_logger.py`：Phase 2/3 决策日志（JSONL）
- `entry/archiver.py`：entry_events/near_miss/watchlist_daily 归档
- `entry/preemption.py`：Preemption 抢占判定（弱势仓/盈利保护/T+1 约束）

## 候选池接口（Phase 1 → Entry）

你可以每天把候选池结果保存为 JSON 文件，然后用 `entry.watchlist.load_watchlist()` 读入。

候选池字段（最小集）：

- `etf_code`：例如 `512480.SH`
- `sentiment_score`：0~100
- `profit_ratio`：0~100（PR%）
- `nearest_resistance`：可为空
- `micro_caution`：可为空（软门控标记）
- `vpin_rank` / `ofi_daily` / `vs_max`：可为空（为空时 Phase 2 的 `S_micro` 记为 0）

## Phase 2（盘后信号计算）

典型流程：

1. 读取候选池（Phase 1 产物）
2. 对每个候选标的拉取日线 `bars = data_adapter.get_bars(etf_code, period="1d", count=... )`
3. 调用 `entry.phase2.evaluate_phase2(...)` 产出评分与可选的 `SignalFired`
4. 将结果写入：
   - 决策日志：`entry.entry_logger.log_phase2_score(...)`
   - 事件归档：`entry.archiver.archive_signal_fired(...)` 或 `archive_near_miss(...)`
   - 若触发：将 `SignalFired` 转为 `core.models.PendingEntry` 写入 `state.json` 的 `pending_entries`

内置防护（用于发现沉默错误）：

- 评分只接受 5 个信号键，出现额外键会触发断言失败
- `score` 先 `round(score, 2)` 再比较阈值
- `score` 越界会触发断言失败

## Phase 3（盘中确认）

典型流程：

1. 启动时加载 `state.json`，调用 `EntryFSM.recover_on_startup()` 做“委托/锁仓”对账骨架（以券商查询为真相）
2. 盘中每次获取到 L1 快照：
   - `VwapTracker.update(snapshot, prev_snapshot)`（自动丢弃 09:30 前快照）
   - 对 `pending_entries` 中处于确认阶段的标的，调用 `Phase3Confirmer.decide(...)`
   - 若返回 `CONFIRM_ENTRY`，把 `OrderRequest` 交给 `TradingAdapter.place_order()`，并通过 `StateManager.save()` 原子写入状态与锁仓记录

确认条件与硬约束：

- 跳空保护：`gap_ratio > max(1%, 0.5*ATR_20/Close_T)` 禁止确认
- VWAP：09:50 前不使用斜率判定；09:30 前快照不纳入累计
- IOPV：确认阈值 0.3%，缺失可降级跳过
- 时间：14:30 后禁止确认建仓
- STALE：数据新鲜度 gate 生效（`core.validators.assert_action_allowed`）

## 日志与归档

- 决策日志：默认写入 `data/logs/entry_decisions.jsonl`
  - `PHASE2_SCORE` / `PHASE3_DECISION` / `PHASE3_REJECTED`
- 归档目录（默认 `data/`）：
  - `data/entry_events/{date}_{etf}.json`
  - `data/near_miss_events/{date}_{etf}.json`（仅 `score ∈ [0.25, 0.45)`）
  - `data/watchlist_daily/{date}.json`

## 测试

本仓库中入场策略相关的单元测试位于：

- `tests/test_entry/*`

建议在没有外部依赖（requests/numpy 等）安装齐全的情况下，优先运行：

```bash
python -m pytest -q tests/test_core tests/test_entry
```

