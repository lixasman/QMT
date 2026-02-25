# 全策略主循环集成 Spec

## Why
当前 5 个模块（core/entry/exit/position/t0）已分别审计通过，但缺少统一的“胶水代码”与可运行的策略主入口，导致无法在 QMT/MiniQMT 中以完整日生命周期方式执行。

## What Changes
- 新增 `strategy_config.py`：集中配置（路径、watchlist、tick 间隔、适配器类型与参数）并支持命令行覆盖
- 新增 `strategy_runner.py`：实现 `StrategyRunner`，串联 DataAdapter/TradingAdapter、StateManager、EntryFSM/ExitFSM/PositionFSM，并提供 pre_open/intraday/post_close 生命周期与 tick 驱动
- 新增 `main.py`：系统唯一入口，解析 CLI、初始化日志、启动 `StrategyRunner` 并运行每日循环
- 新增 `tests/test_integration/test_strategy_runner.py`：集成测试，覆盖日循环与关键联动路径
- 更新根目录 `README.md`：追加“模块 4：策略交易系统”章节（使用方式与两种适配器模式）

## Impact
- Affected specs: 运行时生命周期调度、跨模块状态共享、异常隔离、适配器切换、持久化与重启恢复
- Affected code: 仅新增/更新顶层集成文件与测试；**不修改** `core/ entry/ exit/ position/ t0/` 任何现有文件（**BREAKING**：无）

## ADDED Requirements
### Requirement: 统一入口与生命周期
系统 SHALL 提供根目录 `main.py` 作为唯一入口，支持启动并执行每日交易生命周期：盘前 → 盘中 tick 循环 → 盘后。

#### Scenario: 正常启动并运行一天
- **WHEN** 用户执行 `python main.py`
- **THEN** 系统创建适配器与 FSM、完成 `recover_on_startup()`，并在交易日按阶段执行 pre_open、intraday_loop、post_close
- **AND** 日志记录阶段切换与异常（如有）

### Requirement: 集中配置与 CLI 覆盖
系统 SHALL 以 `StrategyConfig` 集中定义状态路径、日志路径、watchlist、tick 周期、适配器模式与参数，并支持通过命令行覆盖核心字段（至少包含 adapter 与 broker）。

#### Scenario: 切换至 GUI 实盘适配器
- **WHEN** 用户执行 `python main.py --adapter gui --broker ths`
- **THEN** 交易适配器使用 `GuiTradingAdapter` 并启用 GUI 操作计数限额

### Requirement: 跨模块共享状态与依赖注入
系统 SHALL 确保所有 FSM 共享同一个 `PortfolioState`、同一个 `StateManager`、同一个 `DataAdapter`、同一个 `TradingAdapter` 实例。

#### Scenario: 共享状态一致性
- **WHEN** 任一 FSM 引起状态变化并触发保存
- **THEN** 其他 FSM 在同一 tick 内读取到的 state 内容保持一致（同一对象引用）

### Requirement: 盘中 tick 驱动顺序与互斥约束
系统 SHALL 在盘中对每个 ETF（持仓与候选）以 tick 驱动执行，并满足：
- 每个 tick 周期先做数据质量检查，不合格则跳过该 ETF 的所有操作
- Exit 与 T0 不并发（共享 EXIT_MUTEX 的互斥约束由现有实现保证）
- T0 全部通过 `PositionFSM` 访问，不直接 import/调用 `t0/` 子模块

#### Scenario: 数据质量降级跳过操作
- **WHEN** `get_snapshot()` 返回 `data_quality = STALE`
- **THEN** 当 tick 对该 ETF 的 Exit/T0/Scale/Entry 相关动作均不执行

### Requirement: Entry 成交确认与 Position 回调
系统 SHALL 在 `EntryFSM.apply_confirm_action()` 下单后，轮询订单状态并在成交/失败时调用 `PositionFSM` 回调：
- 试探单成交：调用 `on_trial_filled()`
- 确认单成交：调用 `on_confirm_filled()`
- 被拒/撤单：调用 `on_entry_failed()` 并释放锁定资金

#### Scenario: PendingEntry 从下单到成交
- **WHEN** `PendingEntry.status` 进入 `TRIAL_PLACED` 或 `CONFIRM_PLACED`
- **THEN** StrategyRunner 通过 `TradingAdapter.confirm_order()` 确认订单结果并按结果更新 PendingEntry 与 PositionFSM

### Requirement: Exit 下单后的 Position 回调补全
系统 SHALL 在 `ExitFSM.apply_layer1_checks()` / `apply_layer2_if_needed()` 返回订单号后确认成交量，并调用 `PositionFSM.on_layer1_clear()` / `PositionFSM.on_layer2_reduce()` 以补全数量拆分清零/更新逻辑。

#### Scenario: Layer1 清仓回调
- **WHEN** `apply_layer1_checks()` 触发并下卖单
- **THEN** StrategyRunner 确认订单成交量后调用 `on_layer1_clear(etf_code, sold_qty)`

### Requirement: 状态持久化与目录初始化
系统 SHALL 在启动时确保 `data/state/` 与 `data/logs/` 目录存在，并在每次关键状态变更后调用 `StateManager.save()` 持久化。

#### Scenario: 状态文件存在且可反序列化
- **WHEN** 完整日循环结束并执行保存
- **THEN** `state_path` 指向的文件存在，且可成功 load 为 `PortfolioState`

### Requirement: GUI 操作限额处理
系统 SHALL 在使用 `GuiTradingAdapter` 时记录 GUI 操作计数与冻结状态，并在盘后调用 `exit_freeze_mode()` 重置计数；`XtTradingAdapter` 不需要该逻辑。

#### Scenario: 盘后重置 GUI 限额
- **WHEN** 当日执行到 post_close 且交易适配器为 GUI
- **THEN** GUI ops 计数被重置，冻结状态解除（若满足适配器实现逻辑）

### Requirement: 异常隔离与不中断运行
系统 SHALL 对每个 ETF 的快照获取与每个策略模块调用单独捕获异常，记录日志并继续处理其他 ETF，避免单标的异常导致全策略崩溃。

#### Scenario: 单 ETF 异常不影响其他 ETF
- **WHEN** 某 ETF 在 layer1 或 t0 执行抛出异常
- **THEN** 该异常被记录，其他 ETF 的 tick 周期仍继续执行

## MODIFIED Requirements
无（本变更只新增集成层与测试，不改动既有模块的行为定义）。

## REMOVED Requirements
无。
