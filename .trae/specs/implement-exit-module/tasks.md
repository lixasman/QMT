# Tasks

- [x] Task 1: 创建 exit/ 包骨架与公共类型/常量
  - [x] 新建 exit/README.md、exit/__init__.py（含 __all__）
  - [x] 新建 exit/constants.py、exit/types.py 并对齐 core/ 数据结构

- [x] Task 2: 实现阶段 1 四个信号与评分模块
  - [x] 实现 signals/s_chip.py（3 Tier + 冷启动/除零保护/UNAVAILABLE）
  - [x] 实现 signals/s_sentiment.py（阈值 0.35 + 时效性/UNAVAILABLE）
  - [x] 实现 signals/s_diverge.py（RSI 背离确认 + ADX/缩量新高）
  - [x] 实现 signals/s_time.py（days_held/days_since_high/return）
  - [x] 实现 scoring.py（固定权重 + 精确 Score==0 + 阈值 0.9）

- [x] Task 3: 实现阶段 2 Chandelier Stop
  - [x] 实现 chandelier.py（ATR Wilder EMA(12) + HH + Stop + k 三档收紧）
  - [x] 嵌入 k 合法集合与单调递减运行时断言

- [x] Task 4: 实现阶段 3 救生衣机制
  - [x] 实现 lifeboat.py（70/30、超紧止损、冷却期=交易分钟、6 条件回补）
  - [x] 对齐 PositionState.lifeboat_used / lifeboat_sell_time 等持久化字段

- [x] Task 5: 实现阶段 5 数据健康模块与告警模板
  - [x] 实现 data_health.py（外部数据时效性校验、UNAVAILABLE 降级规则）
  - [x] 统一 data_health 三态与告警消息格式（用于 Layer 1/2 与救生衣）

- [x] Task 6: 实现阶段 4 Layer 1 / Layer 2 判定逻辑
  - [x] 实现 layer2.py（Score_soft≥0.9 → REDUCE_50 + k→1.5 + 暂停做 T）
  - [x] 实现 layer1.py（Stop 破位、跳空保护、死水区、T+0 熔断不阻止止损）

- [x] Task 7: 实现 exit_fsm 执行与崩溃恢复
  - [x] 实现 exit_fsm.py（Mutex、Layer1 优先级、pending 合并/去重/优先级）
  - [x] 实现“券商持仓 + 当日委托”为真相的恢复逻辑
  - [x] 实现 T+1 locked_qty 次日 09:30 无条件挂卖 pending

- [x] Task 8: 实现阶段 5 决策日志并补齐运行时断言
  - [x] 实现 exit_logger.py（Layer1/Layer2/Lifeboat/Rejected JSONL）
  - [x] 嵌入验证套件 Part 3 要求的运行时断言（raise AssertionError）

- [x] Task 9: 添加并跑通 22 个验收场景的自动化测试
  - [x] 建立单测输入夹具（bars/snapshot/state/trading adapter stub）
  - [x] 覆盖场景 1-22 的断言（含交易分钟跨午休、价格取整与涨跌停夹取）

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2, Task 3, Task 5
- Task 6 depends on Task 2, Task 3, Task 4, Task 5
- Task 7 depends on Task 6
- Task 8 depends on Task 6
- Task 9 depends on Task 2, Task 3, Task 4, Task 6, Task 7, Task 8
