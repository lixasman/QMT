# Tasks

- [ ] Task 1: 建立 entry 模块骨架与数据契约
  - [ ] 定义 entry 常量、枚举、数据结构（WatchlistItem/SignalFired/EntryState/ConfirmAction）
  - [ ] 对齐 core 接口：DataAdapter/TradingAdapter/CashManager/StateManager/price_utils/validators
  - [ ] 明确运行模式边界：模拟盘/影子模式/GUI 实盘（仅通过适配器切换）

- [ ] Task 2: 实现 Phase 1 离线筛选（Watchlist）
  - [ ] 按规格书实现 3 门控（含微观结构软门控 micro_caution）
  - [ ] 支持筹码引擎冷启动降级与必要字段输出
  - [ ] watchlist 日度归档输出（watchlist_daily）

- [ ] Task 3: 实现 Phase 2 五信号与评分触发（SignalFired）
  - [ ] 实现 5 个信号模块与评分函数（固定权重、round、门控）
  - [ ] 产出 SignalFired（H/L/signal_date/expire_date/is_strong/signals 快照）
  - [ ] 事件归档与 Phase 2 评分日志（按验证套件格式）

- [ ] Task 4: 实现 VWAPTracker 与 Phase 3 盘中确认
  - [ ] 实现 VWAP 累计与锚点（丢弃 09:30 前快照，热身期与低量延长）
  - [ ] 实现 5 条件 AND（跳空保护、VWAP、IOPV 溢价、时间截止、STALE）
  - [ ] 输出确认动作与拒绝原因，并记录 Phase 3 决策/拒绝日志

- [ ] Task 5: 实现入场状态机与 state.json 持久化/恢复
  - [ ] 实现 6 态入场 FSM 与原子写入（下单后先写状态再执行外部操作）
  - [ ] 实现启动恢复：对账券商委托与持仓并修正状态，过期 pending 自动失败
  - [ ] 实现入场/退出互锁（共享 Mutex/执行锁）与动作允许性校验

- [ ] Task 6: 实现 Preemption 抢占评估与执行
  - [ ] 实现触发判定、弱势仓识别、盈利保护线与 T+1 约束
  - [ ] 实现执行前二次校验、先卖后买、30 分钟备用金路径与 T+2 降级冷却
  - [ ] 记录抢占事件与相关状态字段到 state.json

- [ ] Task 7: 实现归档与审计数据落盘
  - [ ] 归档 signal_fired、near_miss、watchlist（路径与阈值按规格书/计划）
  - [ ] 保证 near_miss 仅记录 score ∈ [0.25, 0.45) 且结构与入场事件一致

- [ ] Task 8: 新增 pytest 覆盖 19 个验收场景并接入回归
  - [ ] 覆盖 Phase 2 场景 1-7（含浮点精度与多维度门控）
  - [ ] 覆盖 Phase 3 场景 8-13（跳空/VWAP/IOPV/尾盘/STALE/集合竞价过滤）
  - [ ] 覆盖 Preemption 与边界场景 14-19（盈利保护、弱势判定、价格 clamp、持仓变化二次校验）

# Task Dependencies
- Task 3 depends on Task 2
- Task 4 depends on Task 3
- Task 5 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 3
- Task 8 depends on Task 3, Task 4, Task 5, Task 6

