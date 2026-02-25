# Tasks

- [ ] Task 1: 建立统一工程骨架与模块边界
  - [ ] 定义代码目录结构：data/adapters/execution/state/strategies/risk/logging/tests
  - [ ] 提炼跨模块 Shared Constraints（含冲突裁决与断言/测试点）
  - [ ] 定义跨模块数据契约（L1Snapshot、OrderIntent、PositionSnapshot、StateStore 等）
  - [ ] 定义“盘前/盘中/盘后”时序调度入口与事件总线

- [ ] Task 2: 落地 state.json WAL 与崩溃恢复框架
  - [ ] 实现原子写入（.tmp + os.replace）与 schema 版本控制
  - [ ] 实现启动三步核对：state.json + 券商持仓 + 当日委托
  - [ ] 实现 pending 队列（含 pending_sell_locked / pending_sell_unfilled）与去重/优先级

- [ ] Task 3: 落地 XtQuant 数据适配层与离线回放
  - [ ] 基于 xtdata 订阅 L1 快照，统一时间戳与字段映射
  - [ ] 实现增量成交量/成交额差分（Δvolume/Δamount），并注入数据健康标记
  - [ ] 实现快照录制与回放模式（同一输入序列决策可复现）

- [ ] Task 4: 落地 XtQuant 交易适配层与“10秒超时强制对账”
  - [ ] 封装下单/撤单/查询/回调，支持同步与异步路径
  - [ ] 实现 10 秒内订单状态确认与超时触发对账（CASE A/B/C）
  - [ ] 建立冻结模式（断连/对账失败）与可恢复条件

- [ ] Task 5: 先实现退出策略（作为全系统兜底）
  - [ ] 严格对齐 exit_implementation_verification.md 的编码合约/验收场景/断言/日志
  - [ ] 覆盖救生衣、跳空保护、死水区、T+1 locked_qty 次日处理等路径
  - [ ] 提供单元测试覆盖 22 个关键场景并可一键运行

- [ ] Task 6: 实现入场策略与资金/抢占机制
  - [ ] 严格对齐 entry_implementation_verification.md 的评分/门控/确认/定价/崩溃恢复
  - [ ] 实现 CashManager 的优先级仲裁（Preemption > Trial > T0）
  - [ ] 覆盖 19 个关键场景并建立回归测试

- [ ] Task 7: 实现仓位管理 FSM 与系统级熔断
  - [ ] 严格对齐 position_management_verification.md 的 6态 FSM 与 ATR 风险平价
  - [ ] 实现盘中软熔断/盘后硬熔断与冷静期解锁条件
  - [ ] 覆盖 27 个关键场景并建立回归测试

- [ ] Task 8: 最后实现 T+0 子系统（低优先级增厚）
  - [ ] 严格对齐 t0_implementation_verification.md 的 Regime/VWAP/KDE/窗口/配额/对账/Mutex
  - [ ] 实现 14:15 清道夫与 14:55 残余撤单规则
  - [ ] 覆盖 25 个关键场景并建立回归测试

- [ ] Task 9: 建立统一日志、审计与渐进部署流水线
  - [ ] 落地各子系统日志格式并提供每日审计脚本（校验关键字段与异常）
  - [ ] 建立运行模式切换：回放/影子/模拟盘/小额实盘/常态运行
  - [ ] 定义降级与回滚规则（断言触发/对账失败/幽灵成交等）

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 1
- Task 5 depends on Task 2, Task 3, Task 4
- Task 6 depends on Task 2, Task 3, Task 4
- Task 7 depends on Task 2, Task 3, Task 4
- Task 8 depends on Task 2, Task 3, Task 4, Task 7
- Task 9 depends on Task 5, Task 6, Task 7, Task 8
