# Tasks

- [x] 任务 1：建立 position/ 模块骨架与阶段 1 基础文件
  - [x] 创建 `position/` 包与固定文件清单（仅空实现占位）
  - [x] 定义 `position/constants.py` 全量硬编码常量（含阈值与比例）
  - [x] 定义 `position/types.py`（仅纯计算/决策数据类型；与 `core.models` 状态结构区分）

- [x] 任务 2：实现 ATR 风险平价与相关性过滤（阶段 1 计算）
  - [x] 实现 `atr_sizing.py`：risk_budget/atr_pct/effective_slot/base_target/scale_amt/trial_amt/confirm_amt
  - [x] 内嵌验证套件 Part 3 的 ATR 断言（以 `raise AssertionError` 形式）
  - [x] 实现 `correlation.py`：20 日 Pearson ρ 计算与 ρ≥0.60 互斥判定
  - [x] 新增 pytest：覆盖验收场景 1-6 与 25

- [x] 任务 3：实现 FSM 跃迁矩阵与 S0 清理规则（阶段 2）
  - [x] 实现 `fsm_transitions.py`：合法跃迁校验 + S0 清理（保留 pending_sell_locked / pending_sell_unfilled）
  - [x] 新增 pytest：覆盖验收场景 7-11

- [x] 任务 4：实现加仓引擎（阶段 3）
  - [x] 实现 `scale_prerequisites.py`：6 项前提 AND（含 ATR_14 口径与安全阀/间隔/slot 上限）
  - [x] 实现 `scale_signal.py`：4 条件共振 AND（趋势/回调/筹码/微观止跌）
  - [x] 实现 `scale_executor.py`：tick_ceil(Bid1) 限价买入 + 买入 100 股取整 + 收盘撤单 + 成交后状态更新
  - [x] 内嵌验证套件 Part 3 的加仓断言（状态/Score_soft/间隔/slot 上限/订单价与 TIF）
  - [x] 新增 pytest：覆盖验收场景 12-16

- [x] 任务 5：实现 T+0 子系统与竞态 Mutex（阶段 4）
  - [x] 实现 `t0_controller.py`：激活/冻结矩阵 + 额度公式（仅两项）+ VWAP σ带方向决策 + 极端行情冻结
  - [x] 实现 `t0_mutex.py`：三场景处理（正T+止损、反T+止损、传输中+Layer2 WAIT≤10s）与持锁≤2s 约束
  - [x] 内嵌验证套件 Part 3 的 T+0 断言（状态联动/额度/禁时段/竞态时序）
  - [x] 新增 pytest：覆盖验收场景 17-21 与 26

- [x] 任务 6：实现风控层（阶段 5）
  - [x] 实现 `circuit_breaker.py`：盘中软/硬熔断、HWM 单调、盘后硬熔断、冷静期解锁条件、不可撤销语义
  - [x] 复用退出策略 Layer 1 执行器完成熔断清仓（不另写卖出逻辑）
  - [x] 实现 `rebuild.py`：S5→S4 回补 6 条件 AND + 视为全新建仓 + 每波段最多 1 次
  - [x] 新增 pytest：覆盖验收场景 22-24 与 27

- [x] 任务 7：实现日志与主编排对外 API（阶段 6）
  - [x] 实现 `position_logger.py`：4 类 JSONL 日志，字段满足验证套件 §4.1-4.4
  - [x] 实现 `position_fsm.py`：对外统一 API（§12.3），编排 FSM/加仓/T+0/熔断/回补，并与 `exit/` 联动
  - [x] 实现 `position/__init__.py`：包导出与 `__all__`

- [x] 任务 8：构建验收测试套件与回归验证
  - [x] 新增 `tests/test_position/test_position_acceptance_scenarios.py`：覆盖 27 个验收场景（与验证套件 Part 2 一一对应）
  - [x] 新增 `tests/test_position/test_position_runtime_assertions.py`：覆盖关键运行时断言触发路径（验证套件 Part 3 子集）
  - [x] 运行全量 pytest 回归（包含既有 entry/exit/core 测试），确保无回归

# Task Dependencies
- 任务 2 依赖 任务 1
- 任务 3 依赖 任务 2
- 任务 4 依赖 任务 3
- 任务 5 依赖 任务 3
- 任务 6 依赖 任务 4 与 任务 5
- 任务 7 依赖 任务 6
- 任务 8 依赖 任务 7
