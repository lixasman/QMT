# 回测：确认单成交后禁止再入场 设计

## 背景与目标
用户希望回测中加入一个仅影响 backtest 的开关：当某 ETF 完成“确认单成交”后，在卖出前不再允许新的入场（包括后续金字塔/追加）。该改动仅用于回测对照，需保留回退路径，默认行为不变。

## 设计概述
在回测入口增加 CLI 开关 `--bt-no-reentry-after-confirm`（默认关闭），将该布尔值从 `backtest/main.py` 传入 `BacktestEngine`，再下沉到 `BacktestStrategyRunner`。在 `BacktestStrategyRunner._allow_phase2_candidate` 内增加一层门禁：当该 ETF 的持仓状态已达到“确认单成交后”的状态时（`FSMState.S2_BASE/S3_SCALED/S4_FULL/S5_REDUCED`），直接阻断 Phase2 入场候选。这样不会阻断试探单阶段（`S1_TRIAL`），符合“确认单成交后才开始禁止”的要求。

## 关键决策
- **仅回测侧改动**：避免影响实盘与其他入口。
- **门禁位置**：放在 `_allow_phase2_candidate`，能在 Phase2 阶段前截断，不污染 FSM 与交易逻辑。
- **状态判定**：以 PositionFSM 的状态为准，S2 及以上视为已确认入场。

## 影响范围
- 修改 `backtest/main.py`：新增 CLI 参数并传递。
- 修改 `backtest/runner.py`：在 `BacktestEngine` 与 `BacktestStrategyRunner` 增加布尔字段，`_allow_phase2_candidate` 中使用该门禁。
- 不改 `strategy_config.py`、`strategy_runner.py`，不影响实盘。

## 风险与回退
- 风险：若状态判断与预期不一致，可能过度阻断。通过默认关闭与显式开关降低影响。
- 回退：不传该参数即可回到原行为。

## 验证思路
- 运行同一回测区间，比较开关开启/关闭下 `fills.csv` 的买入次数与买点分布。
- 关注确认单成交后的时段是否不再出现新的买入记录。
