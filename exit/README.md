# exit/ 退出模块

## 模块目标
- 实现 ETF 波段退出策略：Layer 2 预警减仓（评分触发）+ Layer 1 硬止损兜底（价格触发）
- 严格对齐：
  - `.trae/quantitative strategy/exit_strategy_specification.md` v4.1
  - `.trae/quantitative strategy/exit_implementation_verification.md` v1.0

## 快速开始（如何使用）

本仓库目前没有把策略完整串起来的 runner（仅提供合约层 + FSM 本体）。`exit/` 模块的推荐接入方式：

1) 由你的系统启动入口实例化基础设施：
- `DataAdapter`：负责提供行情快照、合约信息、日线 bars 等
- `TradingAdapter`：负责查询持仓、下单、确认成交、进入冻结模式
- `StateManager`：负责 `PortfolioState` 的原子落盘与读取

2) 从 state 文件加载组合状态并创建 `ExitFSM`：

```python
from core.adapters.data_adapter import XtDataAdapter
from core.adapters.xt_trading_adapter import XtTradingAdapter
from core.state_manager import StateManager
from core.models import PortfolioState
from exit.exit_fsm import ExitFSM

data = XtDataAdapter(...)
trading = XtTradingAdapter(...)
sm = StateManager(state_path="data/state.json")
state: PortfolioState = sm.load()

exit_fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=state)
exit_fsm.recover_on_startup()
```

3) 在你的调度/回调里按频率调用 FSM：
- **启动时**：`recover_on_startup()`（一次）
- **次日 09:30–09:35**：`execute_pending_locked(now=...)`（在窗口内每 10–30 秒一次即可）
- **盘中常规**（对每个持仓 ETF 分别调用）：
  - `apply_layer2_if_needed(...)`（建议低频，例如每 1–5 分钟一次或每次信号刷新点）
  - `apply_layer1_checks(...)`（建议高于 Layer2：例如每 3 秒快照到达、或每 10–30 秒心跳一次）
  - `apply_lifeboat_buyback_check(...)`（可与 Layer1 同频或更低频）

> 注意：`apply_layer2_if_needed` 支持传入 `signals`（4 个信号原值）用于日志审计；若不传，日志中的 `signals` 为空。

## 目录结构（固定，不得合并/改名）
```
exit/
  __init__.py
  README.md
  constants.py
  types.py
  scoring.py
  chandelier.py
  lifeboat.py
  layer1.py
  layer2.py
  exit_fsm.py
  exit_logger.py
  data_health.py
  signals/
    s_chip.py
    s_sentiment.py
    s_diverge.py
    s_time.py
```

## 关键原则（必须）
- Layer 1 与 Layer 2 为两套独立系统：
  - Layer 1 触发仅看 `last_price < Stop`
  - Layer 2 触发仅看 `Score_soft ≥ 0.9`
  - 唯一联动：Layer 2 减仓后 k 从 2.8 收紧到 1.5

## 执行策略详解

### Layer 2：预警减仓（评分触发）
- 输入：四信号 `S_chip/S_sentiment/S_diverge/S_time` → `Score_soft`
- 触发：`Score_soft ≥ 0.9`
- 动作：
  - 卖出 `sellable_qty` 的 50%（整手取整到 100 的倍数，不足则至少 100）
  - 设置组合状态进入“已减仓”阶段（用于 `k=1.5`）
  - 冻结日内做 T（仅冻结 T 行为，不影响 Layer1 止损）
- 卖出价：`tick_floor(Bid1)`（不打 0.98 折扣）
- 关键实现位置：
  - 评分：[scoring.py](file:///d:/Quantitative_Trading/exit/scoring.py)
  - Layer2 决策：[layer2.py](file:///d:/Quantitative_Trading/exit/layer2.py)
  - 执行入口：[exit_fsm.py](file:///d:/Quantitative_Trading/exit/exit_fsm.py)

### Layer 1：硬性防线（价格触发）
- 触发：`last_price < Stop`
- Stop：盘前使用日线 bars 计算 Chandelier Stop（ATR Wilder EMA(12) + HH + k 三档收紧）
- 触发后动作（按数据健康与软信号上下文决定力度）：
  - 若存在任何 `UNAVAILABLE`：直接卖出 `sellable_qty`（不启用救生衣），`locked_qty` 写入 pending 次日处理
  - 若 `Score_soft > 0`：直接卖出 `sellable_qty`（有预警信号亮灯）
  - 若 `Score_soft == 0` 且本波段未用救生衣：
    - 卖出 70%（允许零股；例：1500→1050），保留 30%
    - 设置 30% “超紧止损”：再跌 1% 即清掉
    - 记录 `sell_time`，进入回补评估
  - 若 `Score_soft == 0` 且已用救生衣：
    - 只卖出 `sellable_qty`，若存在 `locked_qty`，写入 pending 次日 09:30–09:35 自动挂卖
- 跳空保护（09:25 与 13:00）：`last_price < Stop × 0.97` → 立即全清（不走救生衣）
- 死水区强平：`days_held ≥ 10` 且 `|return| ≤ 2%` → 全清
- 卖出价：`max(limit_down, tick_floor(Bid1 × 0.98))`
- 关键实现位置：
  - 止损/跳空/死水/熔断判断：[layer1.py](file:///d:/Quantitative_Trading/exit/layer1.py)
  - Chandelier 计算：[chandelier.py](file:///d:/Quantitative_Trading/exit/chandelier.py)

### 救生衣回补（买入必须整手）
- 冷却期：30 个交易分钟（排除午休）
- 六条件全部满足才允许回补：价格回到 Stop 上方、Score=0、数据不 STALE、非跌停开板死猫跳、14:30 前等
- 买回 70%：买入价 `tick_ceil(Ask1 × 1.003)`，买入数量整手取整到 100 的倍数
- 重要后果：回补后 70% 为 T+1 锁定股，日内不可卖，只剩 30% 可卖
- 关键实现位置：[lifeboat.py](file:///d:/Quantitative_Trading/exit/lifeboat.py)

## 数据健康与降级（UNAVAILABLE）
- 盘前对外部数据源日期做时效性校验，过期或冷启动会将对应信号降级为 `UNAVAILABLE`
- Layer1 触发时若存在任何 `UNAVAILABLE`，策略会切换为最高风控：全清（无救生衣）
- Layer2 日常评分遇到 `UNAVAILABLE`：缺失信号按 0.5 贡献（中等风险假设）
- 关键实现位置：[data_health.py](file:///d:/Quantitative_Trading/exit/data_health.py)

## 日志（JSONL）
- 写入路径：默认 `data/logs/exit_decisions.jsonl`（由 `ExitFSM(log_path=...)` 指定）
- 覆盖事件：
  - Layer1 触发与决策
  - Layer2 减仓
  - 救生衣回补成功 / 拒绝原因
- 关键实现位置：
  - 日志格式：[exit_logger.py](file:///d:/Quantitative_Trading/exit/exit_logger.py)
  - 调用点：[exit_fsm.py](file:///d:/Quantitative_Trading/exit/exit_fsm.py)

## 模拟盘运行注意事项
- 强烈建议先跑“影子模式”：记录日志但不真实下单（由你的 runner 决定是否调用 `place_order`）
- 账户/券商差异：
  - 买入整手是硬规则，模块已做整手取整（Layer2 减仓属于卖出，但为了“主动减仓动作更规整”也做了整手取整）
  - 卖出允许零股；若你的券商端/接口限制卖出也必须整手，需要在 runner 层做适配并明确“零股残留处理”策略
- 时间源：交易分钟计算依赖 `core/time_utils.py` 的交易日/交易分钟逻辑，午休不计入冷却期
- 频率与风控：
  - 持仓查询/下单确认会触发券商 GUI 行为，建议在 runner 层做节流（例如 10–30 秒心跳）
  - `execute_pending_locked` 仅在 09:30–09:35 窗口内工作，超出时间窗调用会直接返回 0

## 常见问题与排查
- Layer2 日志 `signals` 为空：调用 `apply_layer2_if_needed(..., signals=...)` 时没有传 4 信号原值
- 救生衣回补一直不发生：检查 6 条件中的哪一条不满足（日志里会写 rejected 详情）
- 频繁进入冻结模式（freeze）：通常是下单失败/确认失败/行情或合约信息获取失败，需要从 TradingAdapter 的错误信息排查
- pending 次日不执行：确认 runner 在 09:30–09:35 时间窗内确实调用了 `execute_pending_locked`

## 可调参数（常用）
参数全部集中在 [constants.py](file:///d:/Quantitative_Trading/exit/constants.py)，通常只应在你重新评审策略后调整：
- Layer2：`LAYER2_THRESHOLD`、`SCORE_WEIGHTS`
- Chandelier：`ATR_PERIOD`、`K_NORMAL/K_CHIP_DECAY/K_REDUCED`
- Layer1 卖出折扣：`LAYER1_SELL_DISCOUNT`
- 跳空保护：`GAP_STOP_MULTIPLIER`、`GAP_CHECK_TIMES`
- 死水区：`DEADWATER_MIN_DAYS_HELD`、`DEADWATER_MAX_ABS_RETURN`
- 救生衣：`LIFEBOAT_SELL_PCT`、`LIFEBOAT_TIGHT_STOP_PCT`、`LIFEBOAT_COOLDOWN_TRADING_MINUTES`、`LIFEBOAT_DEAD_CAT_LIMITDOWN_MULTIPLIER`、`LIFEBOAT_BUYBACK_CUTOFF_TIME`
- T+0 熔断：`T0_DAILY_LOSS_CIRCUIT_BREAKER_PCT`（传入参数语义为当日已实现亏损比例的正数）

## 依赖
- 合约层：`core/interfaces.py`（DataAdapter/TradingAdapter）、`core/models.py`（PortfolioState/PositionState）、`core/state_manager.py`
- 工具：`core/price_utils.py`（tick_floor/tick_ceil/align_order_price）、`core/time_utils.py`（交易分钟/交易日）
