# ETF T+0 日内做T模块（t0/）实现 Spec

## Why
当前仓位管理侧仅具备“是否允许T+0”的粗粒度门控与简化决策，缺少按策略规格书 v1.4 与实现验证套件 v1.0 定义的完整信号、订单、对账、熔断与日志闭环，存在“沉默逻辑错误”与“幽灵仓位/重复下单”风险。需要在既有 core/ 合约层之上新增 t0/ 模块，并以验证套件作为上线前硬验收口径。

## What Changes
- 新增 `t0/` 包与 14 个模块文件（见用户给定文件清单），对外暴露 `T0Engine`（`t0/__init__.py` 的 `__all__`）。
- 实现日级 Regime（09:26 计算一次，全天不翻转）、VWAP σ-带信号（增量Δvolume + sigma_floor）、KDE/IOPV 增强、时间窗口硬约束、订单管理（预埋限价单+配额+RT≤1+部分成交）、10s 超时强制对账（CASE A/B/C）、四层熔断+极端行情冻结、14:15/14:55 清道夫、6 种 JSONL 决策日志。
- 为验证套件 Part 2（25 个关键场景）与 Part 3（运行时断言）新增单元测试与运行时断言落地。
- **BREAKING（候选）**：将仓位管理侧现有 `position.t0_controller` 作为“Legacy/过渡实现”保留但不再作为最终策略来源；最终由 `t0.T0Engine` 产出信号与订单动作，供 position_management §7 编排层调用。

## Impact
- Affected specs:
  - `t0_strategy_specification.md` v1.4
  - `t0_implementation_verification.md` v1.0
  - `formalize-quant-system-workflow/spec.md` 的 Shared Constraints（尤其：时间窗口裁决=11:25、对账10s、tick+clamp、Δvolume）
- Affected code:
  - 新增：[t0/](file:///d:/Quantitative_Trading/t0)（整包）
  - 影响（集成阶段）：[position_fsm.py](file:///d:/Quantitative_Trading/position/position_fsm.py) 的 T+0 评估/执行入口（由 Legacy 迁移至 t0 引擎）
  - 复用合约：[core/interfaces.py](file:///d:/Quantitative_Trading/core/interfaces.py)（DataAdapter/TradingAdapter/OrderRequest 等）

## ADDED Requirements

### Requirement: 模块边界与依赖
系统 SHALL 仅通过 `core.DataAdapter` 获取 L1 快照与竞价量，且仅通过 `core.TradingAdapter` 下单/撤单/查询/对账；业务逻辑不得直接依赖 xtdata/xttrader/GUI 具体实现。<mccoremem id="03fmwfs3a8xxl56wtw9r7rn28" />

#### Scenario: 依赖注入
- **WHEN** 初始化 `T0Engine(data=..., trading=..., cash_manager=..., position_port=...)`
- **THEN** t0 模块内部不直接 import 任意 XtQuant/GUI 实现包

### Requirement: Regime 日级使能（09:26 一次性）
系统 SHALL 提供 `compute_regime(*, auction_vol_ratio: float, atr5_percentile: float) -> RegimeResult`，并满足：
- `regime_active = (auction_vol_ratio > 1.5) OR (atr5_percentile > 65)`
- `regime_active` 在 09:26 确定后全天不翻转

#### Scenario: Regime 触发与不翻转
- **WHEN** `auction_vol_ratio=1.8, atr5_pct=50`
- **THEN** `regime_active=True`
- **WHEN** `auction_vol_ratio=1.0, atr5_pct=70`
- **THEN** `regime_active=True`
- **WHEN** `auction_vol_ratio=1.2, atr5_pct=60`
- **THEN** `regime_active=False`
- **WHEN** 09:26 判定 False 且 10:30 波动率飙升
- **THEN** 仍为 False（不重算）

### Requirement: VWAP σ-带信号引擎（增量口径 + sigma_floor）
系统 SHALL 基于 L1 快照（3s）以“增量 Δvolume/Δamount”累计计算 VWAP，并以 60 快照滚动标准差计算 sigma，且满足：
- `delta_volume = cur.volume - prev.volume`，`delta_amount = cur.amount - prev.amount`
- `delta_volume <= 0 OR delta_amount <= 0` 视为 STALE，跳过该快照（且不得更新 VWAP/sigma）
- `sigma = max(std(deviations[-60:]), price * 0.0005)`
- 不得在 10:00 gate 前提交任何 T+0 挂单
- 挂单价后处理固定顺序：先 tick 对齐（0.001），再 clamp 涨跌停

#### Scenario: 增量与 floor
- **WHEN** `prev_cum=1_000_000, cur_cum=1_050_000`
- **THEN** `Δvol=50_000`（不得使用累计量替代）
- **WHEN** `price=2.0, raw_sigma=0.00001`
- **THEN** `sigma=0.001`（sigma_floor 生效）

### Requirement: KDE 筹码支撑位（离线读取 + VWAP 必要条件）
系统 SHALL 仅从文件读取 KDE 密集区（T-1 日离线产出），并满足：
- 盘中不得实时计算 KDE
- KDE 不可独立触发买入；必须先有 VWAP 买入信号，再用 KDE 做增强/合并（±1 tick）

### Requirement: IOPV 溢价置信度（不改变动作）
系统 SHALL 计算 `premium = (price - iopv) / iopv`，并满足：
- `premium >= 0.15%` → confidence=HIGH
- `premium < 0.15%` 或数据缺失/过时 → confidence=NORMAL
- 置信度不改变交易动作，仅影响信号与日志标签

### Requirement: 时间窗口硬约束与 close-only
系统 SHALL 严格遵循实现验证套件口径：
- 新开买入窗口：10:00-11:25 与 13:15-14:15（14:00 后仅允许反T接回买入）
- 反T新开卖出截止：14:00（14:00-14:15 仅允许正T平仓卖出 + 反T接回买入）
- close-only：11:25-13:15 仅允许平仓卖出（禁止任何买入/反T新开）
- 14:15 清道夫：撤销所有未成交 T+0 买入挂单（含接回），保留卖出单；14:55 撤销残余挂单

### Requirement: 额度、频次与 GUI 配额
系统 SHALL 满足：
- `t0_quota = min(base_value * 0.20, CashManager.available_reserve(...))`
- 单笔金额范围：10,000 ~ 14,000
- 每日最多 1 次完整 RT（买+卖=1）
- GUI 独占配额 20 次/日，冻结阈值 15 次；查询/对账不计入 GUI 配额
- 修改预埋单最小间隔 3 分钟，且仅当新价偏离 >2 tick 才允许修改

### Requirement: 10 秒超时强制对账（不可仅冻结）
系统 SHALL 在下单后 10 秒内确认订单状态；超时则必须执行强制对账协议：
- Step 1 冻结 T+0 新交易
- Step 2 查询完整委托列表 + 持仓列表
- Step 3 逐条比对内存 vs 券商（CASE A/B/C）
- Step 4 对账通过解除冻结
- Step 5 对账失败保持冻结直至人工介入

### Requirement: 四层熔断 + 极端行情冻结
系统 SHALL 实现并记录熔断日志：
- Layer 5：日内亏损 ≥ 0.3% NAV → 当日冻结，次日自动恢复
- Layer 7：5日滚动亏损 ≥ 0.5% → 冻结至窗口滑出
- Layer 8：30日滚动亏损 ≥ 1.0% → 冻结 30 天（窗口滑出前不得自动恢复）
- Layer 9：连续亏损 ≥ 3 笔 → 当日冻结，次日自动恢复；盈利一笔即重置为 0
- 极端行情：涨幅 > +6% 禁止反T；跌幅 < -5% 禁止正T

### Requirement: Mutex 竞态处理（Layer1 最高优先级）
系统 SHALL 通过共享 Mutex 确保 Layer1 止损可抢占，并覆盖验证套件 4 场景规则（含 locked_qty 次日 09:30 强平）。<mccoremem id="03fmuwr4mue8w346qwb9c6bhs" />

### Requirement: 决策日志（JSONL）
系统 SHALL 输出 6 种 JSONL 日志，字段与格式严格匹配 `t0_implementation_verification.md` Part 4：
- T0_REGIME / T0_SIGNAL / T0_ROUND_TRIP / T0_BREAKER / T0_RECONCILIATION / T0_AUDIT

## MODIFIED Requirements

### Requirement: position_management 的 T+0 入口（集成阶段）
系统 SHALL 以 `t0.T0Engine` 作为 T+0 的唯一策略实现来源；仓位管理层仅负责编排（门控链、资金/持仓真相、Mutex 协调）与调用引擎输出动作。

## REMOVED Requirements

### Requirement: Legacy 简化 T0 决策（仅基于固定 1.5σ）
**Reason**：不满足 v1.4 的 k 动态、时间窗口细分、对账、清道夫与日志闭环，且与验证套件硬约束不一致。  
**Migration**：保留 Legacy 代码仅用于回归对照；新增 `t0/` 的单测验收通过后，将仓位管理侧调用点切换至 `T0Engine`。

