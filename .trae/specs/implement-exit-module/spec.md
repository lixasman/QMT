# exit/ 退出模块实现 Spec

## Why
当前工程缺少与退出策略规格书 v4.1 对齐的 `exit/` 模块实现，无法在实盘/影子模式中对波段仓位提供 Layer 1 硬止损兜底与 Layer 2 预警减仓。

## What Changes
- 新增 `exit/` Python 包，严格按验证套件 §5.2 的 5 阶段编码顺序落地指定文件（不得合并/拆分/重命名）。
- 基于 `core/` 合约层（`DataAdapter`/`TradingAdapter`/`StateManager`/`PortfolioState`/工具函数）实现退出策略的离线信号、盘前计算与盘中触发执行。
- 增加单元测试覆盖验证套件 Part 2 的 22 个关键场景，并将 Part 3 的运行时断言嵌入实现中（以 `raise AssertionError(...)` 实现）。
- 增加 JSONL 决策日志（Layer 1 / Layer 2 / Lifeboat / Rejected）与数据健康模块（时效性校验、UNAVAILABLE 降级、告警文案）。

## Impact
- Affected specs:
  - `exit_strategy_specification.md` v4.1（唯一真相源）
  - `exit_implementation_verification.md` v1.0（编码合约/验收场景/断言/日志）
- Affected code:
  - 新增：`d:/Quantitative_Trading/exit/**`
  - 复用：`core/interfaces.py`、`core/models.py`、`core/state_manager.py`、`core/price_utils.py`、`core/time_utils.py`
  - 参考风格：`entry/entry_fsm.py`、`entry/entry_logger.py`

## ADDED Requirements

### Requirement: 模块与文件边界（硬约束）
系统 SHALL 创建并仅创建如下文件（路径与命名固定），其职责与策略规格书/验证套件一致：

**阶段 1 — 信号计算（离线，可独立测试）**
- `exit/signals/s_chip.py`：DPC 3-Tier 渐进式筹码恶化 `S_chip ∈ {0,0.3,0.5,0.7,1.0}` 与 UNAVAILABLE 冷启动/时效性降级。
- `exit/signals/s_sentiment.py`：LLM 情绪二值 `S_sentiment ∈ {0,1}` 与 UNAVAILABLE 时效性降级。
- `exit/signals/s_diverge.py`：`S_diverge ∈ {0,1}`（RSI 顶背离确认 + (ADX 拐头 OR 缩量新高≥2)），支持日线不足/时效性异常的 UNAVAILABLE。
- `exit/signals/s_time.py`：`S_time ∈ {0,1}`（days_held、days_since_high、return 条件）。
- `exit/scoring.py`：`Score_soft` 计算与 Layer 2 阈值判定（仅 4 信号；权重固定；`Score_soft == 0` 精确比较）。
- `exit/constants.py`：全部硬编码常量（权重、阈值、k 值、时间窗口、折扣、阈值等）。
- `exit/types.py`：退出模块数据类型（冻结 dataclass、枚举/字面量、运行态 state 结构）。

**阶段 2 — Chandelier Stop**
- `exit/chandelier.py`：Wilder EMA(12) ATR、HH、Stop 与 k 三档收紧（2.8 → 2.38 → 1.5；盘前计算，盘中固定比较）。

**阶段 3 — 救生衣机制**
- `exit/lifeboat.py`：70/30 分仓、30 交易分钟冷却期（排除午休，使用交易分钟函数）、6 条件回补、超紧止损（再跌 1% 清掉）、回补后 T+1 锁定行为与状态持久化字段对齐 `core.models.PositionState`。

**阶段 4 — Layer 1 / Layer 2 判定 + 执行**
- `exit/layer1.py`：硬止损判定（仅 `last_price < Stop`）、跳空保护（09:25/13:00，`price < Stop*0.97` 无救生衣）、死水区强平、T+0 做 T 熔断（仅冻结 T+0，不阻止止损）。
- `exit/layer2.py`：预警减仓（仅 `Score_soft ≥ 0.9`），动作 REDUCE_50，减仓后 k 收紧到 1.5，暂停做 T。
- `exit/exit_fsm.py`：退出状态机（面向 `PortfolioState.positions[etf_code]`）、Mutex、崩溃恢复（券商持仓 + 当日委托为真相）、pending 队列（包含 T+1 locked 次日 09:30 无条件挂卖、跌停未成交重挂），并落实 Layer 1 优先级 > Layer 2 的竞态规则。

**阶段 5 — 日志与监控**
- `exit/exit_logger.py`：JSONL 决策日志，字段与验证套件 §4.1-4.4 一致（包括拒绝日志）。
- `exit/data_health.py`：外部数据时效性校验（DPC/LLM/日线日期）、UNAVAILABLE 三态降级规则、告警消息模板。

**包级文件**
- `exit/__init__.py`：导出 `__all__`（包含对外入口类型/主类/常量集合等）。
- `exit/README.md`：描述模块结构、依赖、运行模式（离线测试/影子模式/实盘），以及“Layer 1/2 完全独立”的关键原则。

#### Scenario: 文件边界验收
- **WHEN** 工程被扫描/导入
- **THEN** `exit/` 目录结构与文件名与上表完全一致，且每个文件首行均为 `from __future__ import annotations`

### Requirement: Layer 1 / Layer 2 独立性（必须可被测试与断言）
系统 SHALL 满足并在关键路径嵌入运行时断言：
- Layer 1 触发判定只看 `last_price < Stop`，不得引入 `Score_soft`。
- Layer 2 触发判定只看 `Score_soft ≥ 0.9`，不得引入 `price vs Stop`。
- 唯一联动：Layer 2 减仓后 k 从 2.8 收紧为 1.5。

#### Scenario: 断言捕获独立性错误
- **WHEN** Layer 1 被判定为触发
- **THEN** 必须校验 `last_price < stop_price`，否则抛出 `AssertionError`

### Requirement: Score_soft 评分（固定权重与精确 0）
系统 SHALL：
- 仅使用 `{S_chip, S_sentiment, S_diverge, S_time}` 四信号参与评分，权重固定为 `{0.7, 0.7, 0.5, 0.4}`。
- 评分值域限制在 `[0, 2.3]`，越界必须抛出 `AssertionError`。
- `Score_soft == 0` 使用精确比较（不允许 epsilon 容差）。

#### Scenario: 评分验收（场景 1-7）
- **WHEN** 以验证套件 Part 2 场景 1-7 的输入计算评分
- **THEN** 输出 score 与是否触发 Layer 2 与验收表一致

### Requirement: 救生衣启用/回补/禁止条件（完全对齐）
系统 SHALL 按验证套件 §1.3 与策略规格书 §2.1 实现救生衣：
- 启用条件：Layer 1 触发 + `Score_soft == 0` + `lifeboat_used=False` + 数据完整（无 UNAVAILABLE）。
- 启用动作：卖出 70%（从 `sellable_qty` 扣减），保留 30%，并对 30% 设置“再跌 1%”超紧止损。
- 回补条件：30 交易分钟冷却期 + `last_price > Stop` + `Score_soft == 0` + `data_feed != STALE` + `last_price > limit_down*1.02` + `current_time <= 14:30` 全部满足才回补 70%。
- 回补后：标记 `lifeboat_used=True`；并承认 70% 为 T+1 锁定股，日内不可卖；二次触发仅卖 `sellable_qty`，其余 `locked_qty` 次日处理。

#### Scenario: 救生衣验收（场景 9-17）
- **WHEN** 以验证套件 Part 2 场景 9-17 输入驱动救生衣
- **THEN** 卖出/保留/回补/拒绝的行为与日志字段与验收一致

### Requirement: 下单价格与数量硬约束
系统 SHALL：
- 统一使用 `core.price_utils.tick_floor/tick_ceil/align_order_price` 完成挂单价取整与涨跌停夹取。
- Layer 1 卖出：`max(limit_down, tick_floor(Bid1 * 0.98))`
- Layer 2 卖出：`tick_floor(Bid1)`（不打折）
- 救生衣回补买入：`tick_ceil(Ask1 * 1.003)`
- 所有卖出数量不得超过 `sellable_qty`；存在 `locked_qty` 时写入 pending 并次日 09:30 无条件执行。

#### Scenario: 挂单价/数量断言
- **WHEN** 生成卖/买单
- **THEN** 若出现 `sell_qty > sellable_qty`、或价格越过涨跌停区间，必须抛出 `AssertionError`

### Requirement: 崩溃恢复与真相来源
系统 SHALL 在启动/恢复时：
- 读取本地 `state.json`（WAL 原子写入由 `core.StateManager` 保证）。
- 查询券商实际持仓与当日委托（通过 `TradingAdapter.query_positions/query_orders`）。
- 若存在在途订单：不得重复提交同意图订单，必须等待其成交/撤单后再决策。
- 真相来源为券商持仓 + 当日委托，而非内存变量或 state 记录。

#### Scenario: 恢复避免重复下单
- **WHEN** state 中存在 pending 且券商端也存在未成交委托
- **THEN** 系统不提交重复卖单，仅恢复/等待并记录日志

## MODIFIED Requirements
无（仅新增 `exit/` 模块实现，不修改既有 `core/` 与 `entry/` 的业务逻辑）。

## REMOVED Requirements
无

