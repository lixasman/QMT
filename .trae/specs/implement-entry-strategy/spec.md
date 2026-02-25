# 入场策略实现 Spec

## Why
需要将 `entry_strategy_specification.md` v1.3 的三阶段入场流水线落地到代码中，并通过 `entry_implementation_verification.md` v1.1 的硬约束与 19 个关键场景验证，确保策略不会出现“沉默逻辑错误”。

## What Changes
- 新增 `entry/` 业务包：Phase 1 离线筛选、Phase 2 信号计算与评分、Phase 3 盘中确认与执行、入场状态机、抢占、日志与归档
- 与 `core/` 对接：复用 `DataAdapter`/`TradingAdapter`/`CashManager`/`StateManager`/`price_utils`/`validators` 的统一约束与接口
- 新增入场策略单元测试：覆盖验证套件 Part 2 的 19 个验收场景
- 支持 XtQuant 模拟盘交易，并预留后续 GUI 实盘切换能力（通过 `core.adapters.*` 的适配器切换，不改入场业务逻辑）

## Impact
- Affected specs: 三阶段入场（Phase 1/2/3）、资金管理与抢占（Preemption）、state.json WAL/崩溃恢复、统一挂单价格规则、决策日志与归档
- Affected code:
  - 新增：`entry/`（含 signals、scoring、phase1/phase3、vwap_tracker、entry_fsm、preemption、logger、archiver）
  - 依赖：`core/models.py`, `core/enums.py`, `core/interfaces.py`, `core/adapters/*`, `core/price_utils.py`, `core/validators.py`, `core/cash_manager.py`, `core/state_manager.py`
  - 新增：`tests/test_entry/*`

---

## ADDED Requirements

### Requirement: Phase 1 候选池筛选
系统 SHALL 在盘后基于情绪、筹码与微观结构输出候选池（Watchlist），并为 Phase 2 提供必要的辅助字段（如阻力位、micro_caution）。

#### Scenario: 入池成功
- **WHEN** sentiment_score ≥ 60 且 profit_ratio(PR) ≥ 75
- **THEN** 标的进入候选池并携带 `sentiment_score`、`profit_ratio`、`nearest_resistance`（可缺失）、`micro_caution` 等字段

#### Scenario: 微观结构软门控
- **WHEN** VPIN/OFI 未达标
- **THEN** 标的不从候选池剔除，但 SHALL 标记 `micro_caution = True`

### Requirement: Phase 2 五信号评分与门控
系统 SHALL 仅使用 5 个信号（S_squeeze、S_volume、S_chip_pr、S_trend、S_micro）按固定权重计算 `score_entry`，并用多维度门控决定是否触发 `SignalFired`。

#### Constraints
- SHALL 固定权重：`0.30/0.25/0.20/0.15/0.10`，不得动态调整
- SHALL `score = round(sum(signal * weight), 2)` 后再做阈值比较
- SHALL 执行多维度门控：`score ≥ 0.45 AND (S_volume > 0 OR S_chip_pr > 0)`
- SHALL 将 `score ≥ 0.70` 标记为强信号（确认窗口缩短）
- SHALL NOT 将 sentiment 参与 Phase 2 评分

#### Scenario: 评分验收（场景 1-7）
系统 SHALL 满足 `entry_implementation_verification.md` Part 2 “Phase 2 评分验收” 的全部输入/输出预期。

### Requirement: Phase 3 盘中确认与执行
系统 SHALL 在确认窗口内（T+1~T+3，强信号 T+1~T+2）基于 L1 快照进行确认建仓判定，确认需同时满足 5 条件（a-e）。

#### Constraints
- SHALL 满足 5 条件 AND：a) 价格突破+跳空保护，b) VWAP 斜率（09:50 后启用），c) IOPV 溢价门控（试探/确认阈值不同，缺失可降级跳过），d) ≤14:30，e) 快照非 STALE
- SHALL 实现跳空保护：`gap_ratio > max(1%, 0.5*ATR_20/Close_T)` 时禁止即时确认
- SHALL 丢弃 09:30:00 前快照，VWAP 累计窗口从 09:30:00 开始
- SHALL 在 09:50 前不使用 VWAP 斜率作确认决策

#### Scenario: Phase 3 验收（场景 8-13）
系统 SHALL 满足 `entry_implementation_verification.md` Part 2 “Phase 3 确认验收” 的全部输入/输出预期。

### Requirement: 试探仓与确认仓的挂单定价与仓位上限
系统 SHALL 严格按统一的 tick 与涨跌停 clamp 规则计算挂单价，并遵守资金与仓位上限。

#### Constraints
- SHALL 买入价：`tick_ceil(min(Ask1 × 1.003, limit_up))`
- SHALL 卖出价：`tick_floor(max(Bid1 × 0.98, limit_down))`
- SHALL clamp 到 `[limit_down, limit_up]`
- SHALL 单只仓位上限 7 万，最多 2 只持仓；Preemption 临时仓位不得超过 4 万
- SHALL 试探仓比例：普通 30%，强信号 50%

### Requirement: 入场状态机（Entry FSM）与 state.json 持久化
系统 SHALL 以状态机管理试探/确认的下单与成交状态，并在每次状态转换时原子写入 state.json，满足崩溃恢复约束。

#### Scenario: 崩溃恢复
- **WHEN** 系统重启
- **THEN** SHALL 以券商“当日委托 + 实际持仓”为真相，对比 state.json 并修正/失败标记，过期 pending 自动失败

### Requirement: Preemption 抢占机制
系统 SHALL 在满仓且出现极强信号时，按规则识别弱势仓并执行“先卖后买”的抢占流程，并提供降级路径。

#### Constraints
- SHALL 触发条件：持仓数=2 且 新 score ≥ 0.85 且 存在弱势仓
- SHALL 弱势定义：类型 A（试探未确认）或 类型 B（已确认且浮盈 < 0.5×ATR 且持有≥3天）
- SHALL 硬保护：浮盈 > 2% 的已确认仓永远不可抢占
- SHALL T+1 约束：不得抢占当日买入仓位；执行前 09:35 需二次校验持仓数，若 <2 则转常规入场
- SHALL 降级：T+2 仍无法卖出弱势仓则放弃抢占 + 3 日冷却

#### Scenario: 抢占验收（场景 14-15 与 19）
系统 SHALL 满足 `entry_implementation_verification.md` Part 2 对 Preemption 的验收预期，并满足“持仓变化二次校验”的场景 19。

### Requirement: 决策日志与归档
系统 SHALL 按验证套件 Part 4 的 JSON 结构记录 Phase 2/3 决策日志，并归档 signal_fired、near_miss、watchlist 等事件数据供审计与后续模型训练。

---

## MODIFIED Requirements

### Requirement: XtQuant 使用方式（通过 core 适配层）
系统 SHALL 仅通过 `core.adapters` 使用 XtQuant 行情与交易能力：入场业务逻辑不直接依赖 xtdata/xttrader API，从而支持模拟盘与后续 GUI 实盘适配器切换。

---

## REMOVED Requirements
无。

