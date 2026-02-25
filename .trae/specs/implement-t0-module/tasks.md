# Tasks
- [ ] Task 1: 落地 t0/ 包骨架与类型/常量契约
  - [ ] 新建 t0/ 目录与 `__init__.py`、`constants.py`、`types.py`
  - [ ] 对齐 core/ 与 position/ 的命名/数据类型风格（frozen dataclass、keyword-only、无魔数）
  - [ ] 为关键约束添加 `raise AssertionError(...)` 的断言入口（不使用 assert 语句）

- [ ] Task 2: 实现阶段1（Regime + KDE 读取）并覆盖场景 1-4
  - [ ] 实现 `regime.compute_regime(...)`（09:26 一次性语义由编排层保证）
  - [ ] 实现 `kde_support.load_kde_zones(...)` 与 `find_nearest_support(...)`（仅文件读取）
  - [ ] 新增 `tests/test_t0/test_regime_acceptance.py` 覆盖 1-4

- [ ] Task 3: 实现阶段2（VWAP/σ + IOPV + 信号聚合 + 时间窗口）并覆盖场景 5-16
  - [ ] 实现 `vwap_engine.VwapEngine`（Δvolume/Δamount、60窗口、sigma_floor、data_quality）
  - [ ] 实现 `iopv_premium.compute_iopv_confidence(...)`
  - [ ] 实现 `signal_engine.SignalEngine`（trend_state→k 动态、KDE 协作、可交易性≥25bps）
  - [ ] 实现 `time_window`（11:25 早盘新开截止、14:00 反T新开截止、close-only）
  - [ ] 新增单元测试覆盖场景 5-16（含 10:00 gate）

- [ ] Task 4: 实现阶段3（订单管理 + 对账 + 熔断 + 清道夫）并覆盖场景 17-25
  - [ ] 实现 `order_manager.OrderManager`（预埋限价单CRUD、3分钟修改间隔、>2tick修改、GUI配额/冻结阈值、RT≤1、部分成交微型仓位）
  - [ ] 实现 `reconciliation`（confirm 10s；超时强制对账 CASE A/B/C；对账失败不解冻）
  - [ ] 实现 `breaker`（日/周/月/连续亏损 + 极端行情冻结）
  - [ ] 实现 `sweeper`（14:15 撤买保卖、14:55 撤残余）
  - [ ] 新增单元测试覆盖场景 17-25

- [ ] Task 5: 实现阶段4（日志 + 主编排）并通过全量验收
  - [ ] 实现 `t0_logger`（6类 JSONL，字段严格对齐验证套件）
  - [ ] 实现 `t0_fsm.T0Engine`（9层门控链、与 position_management §7 的接口对接、Mutex 协调）
  - [ ] 端到端测试：用 Fake DataAdapter/TradingAdapter 回放 25 场景并产出日志样例

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2 and Task 3
- Task 5 depends on Task 4

