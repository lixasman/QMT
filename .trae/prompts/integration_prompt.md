# 模块集成提示词：构建全策略主循环

## 目标

将 5 个已审计通过的独立模块（`core`, `entry`, `exit`, `position`, `t0`）集成为一个完整的、可在 QMT/MiniQMT 环境中运行的策略主循环。

**你不需要修改任何现有模块的内部逻辑**。你只需要编写"胶水代码"和"主循环入口"，将它们串联起来。

---

## 一、现有架构总览

### 1.1 模块职责

```
core/              # 基础设施层（接口、模型、状态管理、工具）
├── interfaces.py  → DataAdapter(ABC), TradingAdapter(ABC), OrderRequest, OrderResult, TickSnapshot, Bar, InstrumentInfo
├── enums.py       → FSMState(S0-S5), OrderSide, OrderStatus, ActionType, DataQuality
├── models.py      → PortfolioState, PositionState, PendingEntry, T0TradeRecord, CircuitBreakerInfo, LockedOrder
├── state_manager.py → StateManager (JSON 原子持久化)
├── cash_manager.py  → CashManager (lock_cash/release_cash/available_reserve)
├── validators.py    → assert_action_allowed, compute_position_sizing
├── price_utils.py   → tick_floor/ceil, limit_up/down_price, align_order_price
├── time_utils.py    → is_trading_time, trading_minutes_between, next_trading_day
├── orchestrator.py  → Orchestrator (薄调度器，add_event/run_due_events)
├── replay.py        → ReplayEvent (事件回放)
└── adapters/
    ├── data_adapter.py      → XtDataAdapter (xtquant.xtdata 封装)
    └── gui_trading_adapter.py → GuiTradingAdapter (easytrader 封装，带 GUI 操作限额)

entry/             # 入场策略
├── entry_fsm.py   → EntryFSM (308行)
│   ├── upsert_watchlist()         // 日更新候选池
│   ├── record_phase2_result()     // Phase2 打分结果
│   ├── add_pending_entry()        // Phase3 信号触发 → 创建 PendingEntry
│   ├── recover_on_startup()       // 重启恢复
│   ├── record_phase3_action()     // Phase3 确认动作记录
│   └── apply_confirm_action()     // 执行试探单/确认单
├── scoring.py, phase2.py, phase3_confirmer.py
├── preemption.py  → 抢跑逻辑
├── vwap_tracker.py
├── watchlist.py
└── signals/       → 信号计算子模块

exit/              # 出场策略
├── exit_fsm.py    → ExitFSM (525行)
│   ├── recover_on_startup()           // 重启恢复
│   ├── execute_pending_locked()       // 执行待卖清单
│   ├── apply_layer1_checks()          // Layer1 止损/止盈检查
│   ├── apply_layer2_if_needed()       // Layer2 主动减仓
│   └── apply_lifeboat_buyback_check() // 救生艇回购
├── layer1.py → 止损逻辑 (Chandelier, 数据断裂, 止损跌穿等)
├── layer2.py → 分数驱动减仓
├── lifeboat.py → 救生艇回购
├── chandelier.py → Chandelier Exit 计算
└── data_health.py → 数据健康检查

position/          # 仓位管理中枢（PositionFSM 是真正的执行编排器）
├── position_fsm.py → PositionFSM (956行) — 系统核心
│   ├── on_trial_filled() / on_confirm_filled() / on_entry_failed()  // 入场成交回调
│   ├── on_layer2_reduce() / on_layer1_clear()                       // 出场回调
│   ├── evaluate_scale_signal() / execute_scale()                     // 加仓逻辑
│   ├── evaluate_t0() / t0_prepare_day() / execute_t0_live()         // T+0 完整闭环
│   ├── evaluate_circuit_breaker() / on_post_close()                  // 组合熔断
│   ├── evaluate_rebuild()  / on_rebuild_filled()                     // 重建
│   └── reset_t0_daily()                                              // T0 日重置
├── t0_controller.py → decide_t0_operation() (额外 T0 门控层)
├── t0_mutex.py      → T0/Exit 互斥工具
├── scale_*.py       → 加仓信号/前置检查/执行
├── circuit_breaker.py, fsm_transitions.py, rebuild.py
└── types.py → T0Decision, CircuitBreakerDecision, ScaleSignalEval

t0/                # T+0 信号引擎（PositionFSM 的子系统）
├── t0_fsm.py      → T0Engine (120行)
│   ├── compute_daily_regime()  // 日频 Regime 判定
│   ├── load_daily_kde()        // 加载 KDE 支撑位
│   └── evaluate_tick()         // 逐 tick 信号生成 → T0Signal
├── vwap_engine.py → VWAP/KAMA 计算
├── signal_engine.py → 买卖信号生成
├── regime.py → 市场状态分类
├── breaker.py → 4层 T0 熔断
├── sweeper.py → 清道夫（超时撤单）
├── order_manager.py → T0 订单管理器
├── reconciliation.py → 对账
└── kde_support.py → KDE 支撑位
```

### 1.2 模块间调用关系（已实现）

```
EntryFSM
  └─ 产出 PendingEntry → 存入 PortfolioState.pending_entries
  └─ apply_confirm_action() 下单后 → 需要调用 PositionFSM.on_trial_filled() / on_confirm_filled()

ExitFSM
  ├─ 共享 EXIT_MUTEX（与 PositionFSM 共用同一把锁）
  ├─ apply_layer1_checks() → 触发后需调用 PositionFSM.on_layer1_clear()
  ├─ apply_layer2_if_needed() → 触发后需调用 PositionFSM.on_layer2_reduce()
  └─ execute_pending_locked() → 执行前一天的待卖指令

PositionFSM（中枢）
  ├─ 接收 EntryFSM 的成交回调
  ├─ 接收 ExitFSM 的减仓/清仓回调
  ├─ 内部包含 T0Engine 实例 → self._t0_engine
  ├─ execute_t0_live() 调用 T0Engine.evaluate_tick() → 获取信号 → 自己下单/对账
  └─ 所有状态变更通过 StateManager 持久化到 JSON
```

### 1.3 共享状态

**所有模块共享同一个 `PortfolioState` 实例**（通过构造函数注入）：

```python
# 创建方式
sm = StateManager("data/state/portfolio.json")
state = sm.load()  # → PortfolioState

# 注入到各 FSM
entry_fsm = EntryFSM(state_manager=sm, data=data, trading=trading, state=state)
exit_fsm  = ExitFSM(state_manager=sm, data=data, trading=trading, state=state)
pos_fsm   = PositionFSM(state_manager=sm, data=data, trading=trading, state=state)
# T0Engine 由 PositionFSM 内部创建（或外部注入 t0_engine 参数）
```

### 1.4 线程安全

- `exit/exit_fsm.py` 定义了全局 `EXIT_MUTEX = threading.Lock()`
- `PositionFSM.__init__` 的 `mutex` 默认值就是 `EXIT_MUTEX`（从 exit_fsm 导入）
- `entry/entry_fsm.py` 有独立的 `ENTRY_MUTEX`
- **关键约束：Exit 操作和 T0 操作共享 EXIT_MUTEX，不可并发**

### 1.5 适配器（双模式：QMT 模拟盘 + easytrader 实盘）

> [!IMPORTANT]
> 系统需要同时支持两种运行模式：
> - **QMT 模拟盘**：使用 `XtTradingAdapter`（`xtquant.xttrader` API 直连），无 GUI 操作限额
> - **easytrader 实盘**：使用 `GuiTradingAdapter`（`easytrader` GUI 自动化），**有 20 次 GUI 操作限额**

#### DataAdapter（两种模式共用）
- `XtDataAdapter`（`core/adapters/data_adapter.py`，98行）
- 依赖 `xtquant.xtdata`，QMT/MiniQMT 客户端必须已登录
- 提供：`get_snapshot()`, `get_bars()`, `get_instrument_info()`, `subscribe_quote()`, `get_auction_volume()`

#### TradingAdapter（两套实现，选其一）

| 特性 | `XtTradingAdapter` | `GuiTradingAdapter` |
|:---|:---|:---|
| 文件 | `core/adapters/xt_trading_adapter.py` (98L) | `core/adapters/gui_trading_adapter.py` (143L) |
| 依赖 | `xtquant.xttrader` | `easytrader` |
| 下单 API | `trader.order_stock(code, side, qty, price, type)` | `client.buy()` / `client.sell()` |
| GUI 操作限额 | ❌ 无 | ✅ 20 次（超 15 次警告，超 20 次冻结） |
| 持仓查询节流 | ❌ 无 | ✅ 10 秒间隔限制 |
| freeze_mode | 有（通用 frozen 标志） | 有（含 gui_ops 计数重置） |
| 初始化 | `XtTradingAdapter(xt_trader)` | `GuiTradingAdapter(easytrader_client)` |

#### 适配器初始化代码

```python
# QMT 模拟盘模式
from xtquant import xttrader
from core.adapters.data_adapter import XtDataAdapter
from core.adapters.xt_trading_adapter import XtTradingAdapter

data = XtDataAdapter()
trader = xttrader.XtQuantTrader(...)  # 需要 session_id 等
trading = XtTradingAdapter(trader)

# easytrader 实盘模式
import easytrader
from core.adapters.data_adapter import XtDataAdapter
from core.adapters.gui_trading_adapter import GuiTradingAdapter

data = XtDataAdapter()  # 数据源仍用 xt
client = easytrader.use(config.easytrader_broker)  # 如 "ths"
client.prepare(...)  # 连接券商 GUI
trading = GuiTradingAdapter(client, gui_ops_limit=config.gui_ops_limit)
```

#### StrategyRunner 适配器差异处理

```python
# 盘后重置：只有 GuiTradingAdapter 需要
if isinstance(self._trading, GuiTradingAdapter):
    self._trading.exit_freeze_mode()  # 重置 GUI ops 计数
# XtTradingAdapter 无此需求
```

---

## 二、需要你实现的内容

### 2.1 主入口文件 `main.py`（根目录）

创建 `d:\Quantitative_Trading\main.py`，它是整个策略系统的唯一入口。

#### 核心职责

1. **初始化**：创建适配器、加载状态、实例化 3 个 FSM
2. **日生命周期管理**：pre_open → intraday_loop → post_close
3. **事件驱动的盘中循环**：按 tick 驱动所有策略模块

#### 完整生命周期

```
[策略启动]
  │
  ├─ 1. 初始化阶段
  │   ├─ 创建 DataAdapter, TradingAdapter
  │   ├─ StateManager.load() → PortfolioState
  │   ├─ 实例化 EntryFSM, ExitFSM, PositionFSM
  │   ├─ EntryFSM.recover_on_startup()
  │   └─ ExitFSM.recover_on_startup()
  │
  ├─ 2. 每日盘前 (pre_open, 09:15-09:25)
  │   ├─ 同步券商资产 → 更新 PortfolioState.cash / nav
  │   ├─ ExitFSM.execute_pending_locked()  // 执行昨日待卖清单
  │   ├─ PositionFSM.reset_t0_daily()      // 重置 T0 日计数器
  │   ├─ 重置 t0_daily_pnl = 0 for each PositionState
  │   ├─ 为有仓位的 ETF: PositionFSM.t0_prepare_day()  // Regime + KDE
  │   └─ EntryFSM.upsert_watchlist()       // 更新候选池
  │
  ├─ 3. 盘中循环 (09:30-15:00, 按 tick 间隔)
  │   │
  │   │  对每个持仓 ETF:
  │   ├─ 3a. 数据质量检查 → assert_action_allowed()
  │   ├─ 3b. 组合熔断 → PositionFSM.evaluate_circuit_breaker()
  │   ├─ 3c. Exit Layer1 → ExitFSM.apply_layer1_checks()
  │   │     └─ 若触发 → PositionFSM.on_layer1_clear() 或 on_layer2_reduce()
  │   ├─ 3d. Exit Layer2 → ExitFSM.apply_layer2_if_needed()
  │   │     └─ 若触发 → PositionFSM.on_layer2_reduce()
  │   ├─ 3e. T0 执行 → PositionFSM.execute_t0_live()
  │   ├─ 3f. 加仓评估 → PositionFSM.evaluate_scale_signal() + execute_scale()
  │   │
  │   │  对候选 ETF (PendingEntry):
  │   ├─ 3g. 入场确认 → EntryFSM 的 Phase3 逻辑
  │   │     └─ 下单成交后 → PositionFSM.on_trial_filled() / on_confirm_filled()
  │   │
  │   │  救生艇:
  │   └─ 3h. ExitFSM.apply_lifeboat_buyback_check()
  │
  ├─ 4. 收盘后 (post_close, 15:00+)
  │   ├─ PositionFSM.on_post_close() → HWM 和熔断
  │   ├─ 更新 t0_pnl_5d / t0_pnl_30d 滚动窗口
  │   ├─ 入场信号日频扫描 → EntryFSM.add_pending_entry()
  │   ├─ StateManager.save() → 最终持久化
  │   └─ (仅 GUI 模式) GuiTradingAdapter.exit_freeze_mode() → 重置 GUI 操作计数
  │
  └─ [等待下一个交易日]
```

### 2.2 文件结构

```
d:\Quantitative_Trading\
├── main.py                      # [NEW] 策略主入口
├── strategy_config.py           # [NEW] 全局配置
└── strategy_runner.py           # [NEW] StrategyRunner 类
```

### 2.3 详细要求

#### A. `strategy_config.py`

定义集中配置：

```python
@dataclass(frozen=True)
class StrategyConfig:
    # 状态文件路径
    state_path: str = "data/state/portfolio.json"

    # 日志路径
    entry_log_path: str = "data/logs/entry_decisions.jsonl"
    exit_log_path: str = "data/logs/exit_decisions.jsonl"
    position_log_path: str = "data/logs/position_decisions.jsonl"
    t0_log_path: str = "data/logs/t0_decisions.jsonl"

    # ETF 候选池（你正在交易的 ETF 列表）
    watchlist_etf_codes: tuple[str, ...] = ("512480.SH", "159107.SZ")

    # 盘中循环间隔（秒）
    tick_interval_s: float = 3.0

    # ── 适配器配置 ──
    # 运行模式: "xt" = QMT 模拟盘, "gui" = easytrader 实盘
    trading_adapter_type: str = "xt"  # "xt" | "gui"

    # QMT 模拟盘参数 (trading_adapter_type="xt" 时生效)
    xt_session_id: str = ""  # xttrader session id

    # easytrader 实盘参数 (trading_adapter_type="gui" 时生效)
    easytrader_broker: str = "ths"  # "ths" | "htzq" | ...
    gui_ops_limit: int = 20
    gui_freeze_threshold: int = 15
```

配置应支持通过命令行参数覆盖：

```bash
# QMT 模拟盘（默认）
python main.py

# easytrader 实盘
python main.py --adapter gui --broker ths
```

#### B. `strategy_runner.py` — `StrategyRunner` 类

这是核心集成类，负责：

1. **`__init__`**: 创建适配器、加载状态、实例化 3 个 FSM
2. **`run_day()`**: 执行完整的一天交易周期
3. **`_pre_open()`**: 盘前准备
4. **`_intraday_loop()`**: 盘中主循环
5. **`_post_close()`**: 盘后处理
6. **`_tick_cycle()`**: 单个 tick 周期的所有策略逻辑

##### 关键实现约束

```
MUST:
  ✅ 所有 FSM 共享同一个 PortfolioState 实例
  ✅ 所有 FSM 共享同一个 StateManager 实例
  ✅ 所有 FSM 共享同一个 DataAdapter 实例
  ✅ 所有 FSM 共享同一个 TradingAdapter 实例
  ✅ PositionFSM 和 ExitFSM 必须共享 EXIT_MUTEX（已通过默认参数实现）
  ✅ 每个 tick cycle 必须先检查数据质量，质量不合格时跳过该 ETF 的操作
  ✅ 必须 catch 每个模块调用的异常，记录日志后继续（不能因为单 ETF 异常导致全策略崩溃）
  ✅ 循环中检查 is_trading_time()，非交易时间不执行盘中逻辑
  ✅ 每次状态变更后调用 StateManager.save()
  ✅ 若使用 GuiTradingAdapter，需处理 GUI 操作限额（默认 20 次），超限自动 freeze，盘后 exit_freeze_mode() 重置；XtTradingAdapter 无此约束

MUST NOT:
  ❌ 不要修改 core/, entry/, exit/, position/, t0/ 中的任何现有文件
  ❌ 不要创建新的 FSM 状态或枚举值
  ❌ 不要重复实现已有模块的功能（如 Breaker、Reconciliation 等）
  ❌ 不要直接操作 PortfolioState.positions 字典，使用 PositionFSM.upsert_position()
  ❌ 不要在主循环中直接 import/调用 t0/ 子模块 — T0 操作全部通过 PositionFSM
```

#### C. `main.py` — 入口

```python
# 最终入口应该支持两种运行模式：
# 1. 正常模式：等待交易时间，执行完整日循环
# 2. 回放模式（未来扩展，现在不实现）：从 replay 文件加载事件
```

### 2.4 EntryFSM 集成要点

> [!CAUTION]
> **EntryFSM 只负责下单，不负责确认成交。** `apply_confirm_action()` 调用 `place_order()` 后将 PendingEntry.status 设为 `TRIAL_PLACED` 或 `CONFIRM_PLACED`，然后就返回了。**StrategyRunner 必须自己轮询订单状态、确认成交、并回调 PositionFSM。**

```python
# apply_confirm_action() 执行流程 (entry_fsm.py L227-307):
# 1. 校验 act.action → CONFIRM_ENTRY
# 2. 检查 available_cash
# 3. self._trading.place_order(req) → 只下单
# 4. pe.status = "TRIAL_PLACED" / "CONFIRM_PLACED"
# 5. 锁定资金
# 6. 返回（✅ 但 未确认成交）

# StrategyRunner 后续需要:
for pe in state.pending_entries:
    if pe.status == "TRIAL_PLACED" and pe.trial_order_id:
        result = trading.confirm_order(pe.trial_order_id, timeout_s=10.0)
        if result.status == OrderStatus.FILLED:
            pe.status = "TRIAL_FILLED"
            pos_fsm.on_trial_filled(pe.etf_code, result.filled_qty, result.avg_price)
        elif result.status in (OrderStatus.CANCELED, OrderStatus.REJECTED):
            pe.status = "FAILED"
            pos_fsm.on_entry_failed(pe.etf_code)
            # 释放锁定资金
            cm.release_cash(pe.trial_order_id)
    elif pe.status == "CONFIRM_PLACED" and pe.confirm_order_id:
        result = trading.confirm_order(pe.confirm_order_id, timeout_s=10.0)
        if result.status == OrderStatus.FILLED:
            pe.status = "CONFIRM_FILLED"
            pos_fsm.on_confirm_filled(pe.etf_code, result.filled_qty, result.avg_price)
        elif result.status in (OrderStatus.CANCELED, OrderStatus.REJECTED):
            pe.status = "FAILED"
            pos_fsm.on_entry_failed(pe.etf_code)
            cm.release_cash(pe.confirm_order_id)
```

### 2.5 ExitFSM 集成要点

> [!CAUTION]
> **ExitFSM 直接修改 PositionState.state 但不调用 PositionFSM 的回调方法。** 这意味着 ExitFSM 只更新了 FSM 状态，但没有执行 PositionFSM 里的仓位数量清零逻辑（base_qty、scale_1_qty 等）。**StrategyRunner 必须在 ExitFSM 操作后手动调用 PositionFSM 的对应方法。**

```python
# ExitFSM.apply_layer1_checks() 内部行为 (exit_fsm.py L302-419):
# 1. 检查 GAP/DEADWATER/STOP_BREAK
# 2. 若触发 → self._trading.place_order() → 下卖单
# 3. 设 ps.state = FSMState.S0_IDLE （L393，直接修改！）
# 4. 返回 order_id
# ❌ 但 没有调用 PositionFSM.on_layer1_clear() → base_qty/scale_qty 未清零
#
# ExitFSM.apply_layer2_if_needed() 内部行为 (exit_fsm.py L262-300):
# 1. 若分数低 → place_order() 卖 50%
# 2. 设 ps.state = FSMState.S5_REDUCED + ps.t0_frozen = True （L297-298）
# 3. 返回 order_id
# ❌ 但 没有调用 PositionFSM.on_layer2_reduce() → 仓位拆分未更新

# StrategyRunner 正确集成方式:
layer1_oid = exit_fsm.apply_layer1_checks(
    now=now, etf_code=code, stop_price=stop_px,
    score_soft=score, data_health=health, days_held=days,
    current_return=ret, t0_realized_loss_pct=t0_loss
)
if layer1_oid is not None:
    # ExitFSM 已下单并设了 state=S0，但 qty 未清
    # 需要用 confirm_order 获取实际成交量，然后回调 PositionFSM
    result = trading.confirm_order(layer1_oid, timeout_s=10.0)
    sold_qty = result.filled_qty if result.filled_qty > 0 else ps.total_qty
    pos_fsm.on_layer1_clear(etf_code=code, sold_qty=sold_qty)

layer2_oid = exit_fsm.apply_layer2_if_needed(
    now=now, etf_code=code, score_soft=score
)
if layer2_oid is not None:
    result = trading.confirm_order(layer2_oid, timeout_s=10.0)
    sold_qty = result.filled_qty if result.filled_qty > 0 else 0
    if sold_qty > 0:
        pos_fsm.on_layer2_reduce(etf_code=code, sold_qty=sold_qty)
```

> [!WARNING]
> 注意 ExitFSM 和 PositionFSM **都会 set `ps.state`**，存在双写风险。由于 ExitFSM 在 L393 已经设了 S0_IDLE，而 PositionFSM.on_layer1_clear() 也会设 S0_IDLE，这里是幂等的不会冲突。但 Layer2 的 S5_REDUCED 状态设置同理。关键是 PositionFSM 的回调会额外清零 base_qty/avg_cost/t0_trades 等字段，这是 ExitFSM 不做的。

### 2.6 T0 集成要点

T0 **不需要** StrategyRunner 做任何特殊编排。只需调用：

```python
# 盘前
pos_fsm.t0_prepare_day(etf_code=..., now=..., trade_date=..., auction_vol_ratio=..., atr5_percentile=...)
# 盘中（每 tick）
pos_fsm.execute_t0_live(now=..., etf_code=...)
# 盘后
pos_fsm.reset_t0_daily()
```

所有 T0 内部逻辑（信号 → 门控 → 清道夫 → 下单 → 锁资 → 对账 → RT 闭环）已在 `position_fsm.py` L570-826 完整实现。

### 2.7 日频信号扫描

入场信号的日频扫描（Phase1/Phase2/Phase3）需要在收盘后或盘中适当时机执行。需要要调用：

1. `entry.signals` 子模块计算各维度信号
2. `entry.scoring.compute_score()` 合成评分
3. `EntryFSM.add_pending_entry()` 将达标信号加入待确认队列
4. 次日盘中由 `EntryFSM.apply_confirm_action()` 执行确认

**实现建议**：这部分可以在 `_post_close()` 中实现。如果信号计算需要外部数据（如情绪分数），可以先用 0 或 placeholder。

### 2.8 `t0_prepare_day()` 参数来源

```python
PositionFSM.t0_prepare_day(
    etf_code=...,
    now=datetime.now(),
    trade_date=datetime.now(),       # 当日日期
    auction_vol_ratio=...,           # 集合竞价量 / 过去 N 日均量
    atr5_percentile=...,             # 5 日 ATR / 历史分位数
)
```

- `auction_vol_ratio`：通过 `data.get_auction_volume(etf_code, date_str)` 获取当日集合竞价量，除以历史均量(可暂用固定值 1.0)
- `atr5_percentile`：通过 `data.get_bars()` 取 5 日 K 线计算 ATR 百分位（可暂用固定值 0.5）

---

## 三、异常处理要求

```python
# 每个策略调用必须包裹 try/except，记录异常后继续
def _tick_cycle_for_etf(self, etf_code: str, now: datetime) -> None:
    try:
        snap = self._data.get_snapshot(etf_code)
        # 数据质量检查
        assert_action_allowed(snap.data_quality, ActionType.T0_SIGNAL)
    except AssertionError:
        # 数据不合格，跳过本 tick
        return
    except Exception as e:
        logger.error(f"snapshot failed for {etf_code}: {e}")
        return

    # 各策略调用分别 try/except
    try:
        pos_fsm.execute_t0_live(now=now, etf_code=etf_code)
    except Exception as e:
        logger.error(f"t0 failed for {etf_code}: {e}")

    try:
        exit_fsm.apply_layer1_checks(...)
    except Exception as e:
        logger.error(f"layer1 failed for {etf_code}: {e}")
    # ... 其他策略调用
```

---

## 四、日志

使用 Python 标准 `logging` 模块：

```python
import logging
logger = logging.getLogger("strategy")
# 输出到文件和控制台
# 格式: %(asctime)s | %(levelname)s | %(name)s | %(message)s
```

关键日志点：
- 策略启动/关闭
- 每日生命周期阶段切换（pre_open → intraday → post_close）
- 每个 tick cycle 的 ETF 列表
- 任何异常
- GUI 操作计数

---

## 五、测试

编写 `tests/test_integration/test_strategy_runner.py`：

### 5.1 Mock 模式

使用 tests 目录中已有的 mock 模式（参考 `test_position_t0_live_execution.py`）：

```python
# 核心 Mock:
# - _FakeData (DataAdapter)：返回预设 TickSnapshot
# - _FakeTrading (TradingAdapter)：记录下单并返回预设结果
# - _FakeT0Engine (可选)：注入到 PositionFSM
```

### 5.2 测试场景

1. **完整日循环**：pre_open → 3 个 tick cycle → post_close，验证：
   - 状态持久化文件存在且可反序列化
   - T0 daily 计数器已重置
   - GUI 操作计数不超过限额

2. **入场 → 持仓 → T0 → 出场 全流程**：
   - EntryFSM 产生 PendingEntry → 次日 apply_confirm_action → PositionFSM.on_trial_filled
   - 持仓进入 S2 后 T0 执行一个完整 round-trip
   - Layer1 触发 → PositionFSM.on_layer1_clear → 回到 S0

3. **数据质量降级**：snapshot.data_quality = DataQuality.STALE 时，所有策略操作被跳过

4. **GUI 操作限额**：连续下单 15 次后自动 freeze，后续下单被拒绝

---

## 六、不要遗漏的细节

1. **`t0_daily_pnl` 日重置**：每天盘前需要将 `PositionState.t0_daily_pnl` 归零。注意 `PositionFSM.reset_t0_daily()` 只重置了 `OrderManager`，`t0_daily_pnl` 在 `PositionState` 上，需要额外重置。

2. **`t0_pnl_5d` / `t0_pnl_30d` 滚动窗口**：盘后需要将当日 `t0_daily_pnl` append 到这两个列表中，并保持 5/30 的窗口长度。

3. **NAV 更新**：同步券商资产后需要更新 `PortfolioState.nav`。

4. **HWM 更新**：收盘后通过 `PositionFSM.on_post_close(now=..., current_nav=...)` 更新。

5. **`highest_high` 追踪**：盘中逐 tick 更新各 PositionState 的 `highest_high`（用于 Chandelier Exit）。

6. **目录初始化**：确保 `data/state/` 和 `data/logs/` 目录在启动时存在。

7. **信号中断恢复**：策略可能在盘中意外退出。下次启动时 EntryFSM.recover_on_startup() 和 ExitFSM.recover_on_startup() 会处理未完成的订单。

8. **`ExitFSM.apply_layer1_checks()` 的参数**：需要提供 `stop_price`（止损价）、`current_return`（当前收益率）、`days_held`（持仓天数）、`t0_realized_loss_pct`（T0 已实现亏损百分比）等。这些需要从 PositionState 和市场数据中计算。

---

## 七、最终交付物

| 文件 | 说明 |
|:---|:---|
| `strategy_config.py` | 全局配置 |
| `strategy_runner.py` | StrategyRunner 类（核心集成逻辑） |
| `main.py` | 入口 |
| `tests/test_integration/test_strategy_runner.py` | 集成测试 |
| `README.md` | **更新**：在现有 README 末尾添加"模块 4：策略交易系统"章节 |
