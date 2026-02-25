# 仓位管理 position/ 模块实现 Spec

## Why
现有系统已具备 `core/` 合约层与 `entry/`、`exit/` 两大业务模块，但缺少符合 `position_management_specification.md` v2.1 的仓位管理实现。需要在不修改 `core/`、`entry/`、`exit/` 的前提下，新增 `position/` 模块以满足验证套件的 27 个验收场景与运行时断言要求。

## What Changes
- 新增 `position/` 包及固定文件集（不得合并/拆分/重命名），按验证套件 §5.2 的 6 阶段顺序实现。
- `position/` 仅消费 `core/` 合约层（`core.interfaces`、`core.models`、`core.enums`、`core.state_manager`、`core.cash_manager`、`core.price_utils`、`core.validators` 等），不新增自定义替代实现。
- 与 `exit/` 联动：复用退出执行器完成熔断清仓、止损清仓等卖出侧动作；共享互斥锁语义（锁仅保护“写状态+提交委托”的瞬间）。
- 对外提供 `position_fsm.py` 的统一 API（§12.3），供入场/退出模块调用。
- 全量实现 JSONL 决策日志（4 类）与关键运行时断言（验证套件 Part 3）。

## Impact
- Affected specs: ATR 风险平价定仓、6 态 FSM、金字塔加仓、T+0 从属子系统、账户级熔断、相关性互斥、减仓后回补、决策日志与审计。
- Affected code:
  - 仅新增 `d:\Quantitative_Trading\position\` 下文件。
  - 运行时将读写 `core.state_manager.StateManager` 管理的 `PortfolioState`（含 `positions`、`circuit_breaker`、`hwm`、`nav` 等）。
  - 调用 `exit/` 的既有能力完成卖出侧执行（尤其是熔断清仓复用 Layer 1 执行器）。

## ADDED Requirements

### Requirement: 模块边界与文件清单
系统必须创建 `position/` 包，包含且仅包含如下文件，并严格按职责实现（不得重命名/合并/拆分）：
- 阶段 1：`constants.py`、`types.py`、`atr_sizing.py`、`correlation.py`
- 阶段 2：`fsm_transitions.py`
- 阶段 3：`scale_prerequisites.py`、`scale_signal.py`、`scale_executor.py`
- 阶段 4：`t0_controller.py`、`t0_mutex.py`
- 阶段 5：`circuit_breaker.py`、`rebuild.py`
- 阶段 6：`position_logger.py`、`position_fsm.py`、`__init__.py`

### Requirement: ATR 风险平价仓位计算（§3.4 / 验证 Part 1.1）
系统必须实现 ATR 风险平价仓位计算器，满足：
- risk_budget = clamp(current_nav × 0.02, 2500, 6000)
- atr_pct = max(ATR_20 / close_price_T_minus_1, 0.015)
- stop_multiplier 固定为 3.5，且运行时必须校验 stop_multiplier ≥ 3.0（方向性断言）
- effective_slot = min(risk_budget / (atr_pct × stop_multiplier), 70000)
- 由 effective_slot 联动计算：
  - base_target = effective_slot × 0.71
  - scale_1_amt = effective_slot × 0.19
  - scale_2_amt = effective_slot × 0.10
  - trial_amt = base_target × 0.30（普通）或 × 0.50（强信号）
  - confirm_amt = base_target - trial_amt

#### Scenario: 计算验收（场景 1-6）
- **WHEN** 输入 NAV、ATR_pct 与 stop_mult
- **THEN** effective_slot 与 base_target 等结果与验证套件 Part 2 场景 1-6 完全一致

### Requirement: 相关性过滤器（§3.3 / 验证 Part 1.8）
系统必须实现 20 日 Pearson 相关性互斥规则：
- 窗口 = 过去 20 个交易日收盘价（对数收益率 Pearson）
- 阈值 = ρ ≥ 0.60 互斥（不得降低）
- 仅当存在可比较的持仓标的时进行比较；但无论相关性如何，持仓满 2 只时不得新开仓

#### Scenario: 相关性互斥（场景 25）
- **WHEN** 已持仓 A，准备建仓 B，且 corr(A,B) ≥ 0.60
- **THEN** 系统拒绝建仓 B，并输出可审计信息（日志/返回值至少其一）

### Requirement: 6 态 FSM 与跃迁合法性（§4 / 验证 Part 1.2）
系统必须以 `core.enums.FSMState` 作为唯一状态枚举，且仅允许验证套件定义的合法跃迁集合；任何非法跃迁必须触发运行时异常（AssertionError）。
- 跃迁到 S0 时必须执行 S0 清理规则：保留 `pending_sell_locked` / `pending_sell_unfilled`，清除可安全清除的临时状态（例如当日加仓挂单/T+0 本地状态等）
- 回补仅允许 S5→S4，且每波段最多 1 次；第二次 Layer 2 触发必须走 S5→S0

#### Scenario: FSM 验收（场景 7-11）
- **WHEN** 输入对应事件序列（入场、确认、加仓、Layer2、Layer1、回补、再次 Layer2）
- **THEN** 状态序列与拒绝行为与验证套件场景 7-11 一致

### Requirement: 加仓前提检查（§5.1 / 验证 Part 1.3）
系统必须实现 6 项加仓前提条件（AND）：
- state ∈ {S2, S3}
- 浮盈 ≥ 1.5 × ATR_14（注意：此处必须使用 ATR_14，不得混用 ATR_20）
- circuit_breaker 未触发且 intraday_freeze == False
- Score_soft < 0.5（硬安全阀）
- 上次加仓距今 ≥ 3 个交易日
- 加仓后总仓位 ≤ effective_slot

#### Scenario: 前提拦截（场景 12-14）
- **WHEN** 任一前提不满足
- **THEN** 系统不进入加仓信号评估/不提交加仓委托，并产出可审计信息（日志/返回值）

### Requirement: 加仓信号四条件共振（§5.2 / 验证 Part 1.3）
系统必须实现 4 条件共振（AND），缺一不可：
1) 日线趋势：KAMA(10) 连续上升 ≥ 2 日 AND Elder Impulse = Green（计算口径与入场策略一致）
2) 回调：回调 ≥ 1.0 × ATR_14 且未破 Chandelier Stop
3) 筹码：密集支撑区（density 前 30%），价格触及支撑区上沿 ± 0.3×ATR_14
4) 微观止跌：近 30min 缩量 30%+ AND 未破支撑下沿 AND 阳线收盘

#### Scenario: 四条件缺一（场景 15）
- **WHEN** 仅满足 3/4 条件
- **THEN** 不触发加仓

### Requirement: 加仓执行（§5.3 / 验证 Part 1.3）
系统必须在触发加仓时执行：
- 挂单价 = tick_ceil(Bid1)（不得使用 Ask1；不得追涨）
- 买入数量取整到 100 份（仅买入取整；卖出不取整）
- TIF = 当日有效（收盘自动撤单，不隔夜）；未成交不写 pending
- 成交后更新 `core.models.PositionState` 的：state、base_qty/scale_qty、total_qty、avg_cost、scale_count、last_scale_date，并重算 t0_max_exposure

#### Scenario: 正常加仓（场景 16）
- **WHEN** 6 前提✅且 4 条件✅
- **THEN** 提交 Bid1 限价买单；收盘前未成交则撤单

### Requirement: T+0 从属子系统（§7.2-7.7 / 验证 Part 1.5-1.6）
系统必须实现：
- 激活矩阵：S0/S1/S5 → OFF；S2/S3/S4 → ON 但需同时满足 浮盈>1% 且 日亏<0.3% 且 t0_frozen==False
- 额度公式：max_exposure = min(底仓市值×20%, cash_manager.available_reserve())（仅两项，不得新增第三项）
- 方向决策：VWAP σ带（±1.5σ）决定正T/反T；极端行情冻结（日涨幅>6% 冻结反T；日跌幅>5% 冻结正T）
- Mutex 三场景：正T+止损、反T+止损、T0 传输中 + Layer2（WAIT≤10s）
- Mutex 持锁范围：仅保护“写状态 + 提交委托”瞬间；任何路径持锁不得超过 2 秒；Layer 1 止损不得因等待锁延迟超过 2 秒
- 正T 未闭环或止损触发导致 locked_qty：必须写入 pending_sell_locked，次日 09:30 跌停价强平

#### Scenario: T+0 联动（场景 17-21, 26）
- **WHEN** 触发 S2 激活、Layer2 减仓、极端行情、正T/反T 与止损竞态、正T 14:55 未闭环
- **THEN** 行为与验证套件场景 17-21 与 26 一致，并输出对应日志类型

### Requirement: 账户级熔断器（§3.2 / 验证 Part 1.4）
系统必须实现账户级熔断：
- HWM 单调递增：HWM = max(HWM, current_nav)，且任何时候不得下调
- 盘中每 3 秒：nav_estimate = 持仓市值 + 可用现金
  - nav_estimate ≤ HWM×0.92：冻结所有新开仓（试探/加仓/做T买入），允许卖出类操作
  - nav_estimate ≤ HWM×0.90：触发不可撤销盘中熔断并清仓
- 清仓必须复用退出策略 Layer 1 卖出执行器（不得另写简化卖出逻辑）
- “不可撤销”仅指冷静期状态不可撤销，不代表卖出执行不可重试；pending_sell_unfilled 必须当日持续重试
- 盘后 15:30：current_nav ≤ HWM×0.90 → 全清 + 5 日冷静期；解锁需同时满足 ≥5 日 + 大盘>MA20 + 人工 ACK

#### Scenario: 熔断验收（场景 22-24）
- **WHEN** nav_estimate 触发软/硬阈值与冷静期解锁尝试
- **THEN** 冻结/清仓/拒绝解锁行为与验证套件一致

### Requirement: 减仓后回补（§6 / 验证 Part 1.7）
系统必须实现 S5→S4 回补逻辑：
- 回补条件 6 项 AND（冷却≥5日、密集区、压力测试、放量突破、Score_soft=0、情绪>50）
- 回补视为全新建仓：重新计算 ATR/止损参数，不沿用旧参数
- 回补最多 1 次/波段；第二次 Layer2 触发必须走 S5→S0
- 回补挂单已提交但条件失效（Score_soft 从 0→0.6）：必须立即撤单并保持 S5，写日志

#### Scenario: 回补边界（场景 27）
- **WHEN** 回补中途条件变坏
- **THEN** 立即撤单且不跃迁，保留 S5 并记录原因

### Requirement: 决策日志（验证 Part 4）
系统必须将以下事件写入 JSONL（每条一行），字段满足验证套件格式约束：
- FSM_TRANSITION
- SCALE_SIGNAL_EVAL
- T0_OPERATION
- CIRCUIT_BREAKER

### Requirement: 对外接口（§12.3）
`position_fsm.py` 必须对外暴露以下 API（签名与语义保持一致）：
- 入场策略 → 仓位管理：`on_trial_filled` / `on_confirm_filled` / `on_entry_failed`
- 仓位管理 → 退出策略：`get_position_state` / `get_total_qty` / `get_sellable_qty` / `get_t0_frozen`
- 退出策略 → 仓位管理：`on_layer2_reduce` / `on_layer1_clear` / `on_lifeboat_rebuy`
- 与 CashManager：仅使用既有 `lock_cash(priority=2)` / `release_cash()` 等接口（不新增接口）

## MODIFIED Requirements
无（本变更不修改 `core/`、`entry/`、`exit/`；如发现规格书与现有合约不兼容，需在实现前以适配层方式消化差异，而非修改既有模块）。

## REMOVED Requirements
无

