# 验收 Checklist（position/ 模块）

## 结构与边界
- [x] `position/` 包文件清单与验证套件 §5.2 完全一致（不多不少、不改名）
- [x] 未修改 `core/`、`entry/`、`exit/` 任何文件（仅新增 `position/` 与测试）
- [x] 全部 `position/*.py` 第一行均为 `from __future__ import annotations`
- [x] 全部对外函数具备完整类型注解（含返回值）
- [x] 外部输入均做显式 `float()/int()/str()` 防御性转型
- [x] 断言全部使用 `raise AssertionError(...)`（不使用 `assert` 语句）

## 关键口径一致性
- [x] ATR 风险平价：risk_budget = clamp(nav×0.02, 2500, 6000)
- [x] stop_multiplier 固定为 3.5（且运行时校验 ≥ 3.0）
- [x] effective_slot = min(risk_budget/(max(atr_pct,0.015)*3.5), 70000)
- [x] ATR_20 仅用于定仓/回补；ATR_14 仅用于加仓前提浮盈检查（不混用）
- [x] 加仓挂单价 = tick_ceil(Bid1)，不追 Ask
- [x] 买入量取整到 100 份；卖出量不取整（A 股 odd lot 可卖）
- [x] T+0 额度 = min(底仓市值×20%, available_reserve)（仅两项）
- [x] S0 清理：保留 pending_sell_locked / pending_sell_unfilled，清除其他临时状态
- [x] HWM 单调递增，任何时候不可被下调
- [x] 熔断清仓复用退出策略 Layer 1 执行器（不另写卖出逻辑）

## 日志（JSONL）
- [x] FSM_TRANSITION 日志字段满足验证套件 §4.1
- [x] SCALE_SIGNAL_EVAL 日志字段满足验证套件 §4.2
- [x] T0_OPERATION 日志字段满足验证套件 §4.3
- [x] CIRCUIT_BREAKER 日志字段满足验证套件 §4.4
- [x] 每条决策路径（通过/拒绝/冻结/撤单/竞态）均有日志输出（不可遗漏）

## 运行时断言（验证套件 Part 3）
- [x] ATR 风险平价断言（stop_multiplier/risk_budget/effective_slot/ATR_pct 下限保护）
- [x] FSM 跃迁合法性断言（非法跃迁直接报错）
- [x] 加仓触发断言（状态/Score_soft/间隔/slot 上限/订单价与 TIF）
- [x] T+0 状态联动断言（S0/S1/S5 绝不激活；ON 时浮盈/日亏约束）
- [x] T+0 额度上限断言（t0_exposure ≤ max_t0）
- [x] 熔断断言（HWM 不下调；软熔断冻结新开仓；硬熔断触发清仓且不可撤销）
- [x] 回补次数限制断言（每波段最多 1 次）
- [x] Mutex 断言（持锁 < 2 秒；Layer1 不被阻塞）
- [x] pending 重试安全检查断言（状态/熔断/冻结/slot/资金等）

## 27 个验收场景（验证套件 Part 2）
### ATR 风险平价（1-6）
- [x] 场景 1
- [x] 场景 2
- [x] 场景 3
- [x] 场景 4
- [x] 场景 5
- [x] 场景 6

### FSM 跃迁（7-11）
- [x] 场景 7
- [x] 场景 8
- [x] 场景 9
- [x] 场景 10
- [x] 场景 11

### 加仓信号（12-16）
- [x] 场景 12
- [x] 场景 13
- [x] 场景 14
- [x] 场景 15
- [x] 场景 16

### T+0 联动（17-21）
- [x] 场景 17
- [x] 场景 18
- [x] 场景 19
- [x] 场景 20
- [x] 场景 21

### 熔断器（22-25）
- [x] 场景 22
- [x] 场景 23
- [x] 场景 24
- [x] 场景 25

### 边界场景（26-27）
- [x] 场景 26
- [x] 场景 27
