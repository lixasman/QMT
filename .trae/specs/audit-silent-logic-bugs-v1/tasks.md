# Tasks
- [ ] Task 1: 划分审计切片与入口清单
  - [ ] 产出“模块切片 → 入口文件/关键函数 → 主要数据源”的清单
  - [ ] 明确每批次交付顺序与依赖（先数据流与契约，再分支/刻度/时序）

- [ ] Task 2: 审计数据流完整性（Batch 1：Entry 信号链路）
  - [ ] 逐个 entry/signals 下的信号函数追溯输入参数来源至 DataAdapter 或上游产出
  - [ ] 盘点 WatchlistItem 字段消费情况并标注未用/缺失/命名不一致
  - [ ] 识别“参数始终为默认占位值”的调用链并记录证据

- [ ] Task 3: 审计沉默死分支（Batch 1：Entry 信号链路）
  - [ ] 为 entry/signals 与 entry/scoring 关键 if/else 分支构造可触发的具体输入
  - [ ] 汇总所有 return 0/0.0 路径并评估在正常行情下的触发概率分级
  - [ ] 核对 score_entry 多维门控规则的实现与规格一致性

- [ ] Task 4: 审计数值刻度一致性（Batch 2：Entry score 聚合与阈值）
  - [ ] 给出每个 entry 信号的输出范围与裁剪/归一化规则
  - [ ] 推导 scoring.py 聚合后的 score 上下界（全拉满/全为零）并核对阈值可达性
  - [ ] 标注刻度不一致导致“永不触发/常态触发/边界错误”的潜在沉默 bug

- [ ] Task 5: 审计时序与状态一致性（Batch 3：Phase2/EntryFSM 跨日）
  - [ ] 核对 Phase2/_post_close 的 bars 使用是否存在未来数据风险
  - [ ] 审计 EntryFSM pending 跨日保持与恢复路径的一致性、幂等性与去重逻辑
  - [ ] 汇总会导致重复下单/漏单/跨日漂移的沉默风险点并分级

- [ ] Task 6: 扩展审计到 Exit/Position/T+0 与主循环（Batch 4+）
  - [ ] 以相同四维度复用方法审计 exit/ 与 position/ 的 signals、scoring、FSM
  - [ ] 审计 core/orchestrator 与 integrations 的数据拼装、缓存与调用时序

- [ ] Task 7: 输出审计报告与可执行修复建议
  - [ ] 按 P0/P1/P2 汇总 findings（每条含文件:行号、触发条件、修复建议）
  - [ ] 对 P0/P1 建议提供最小回归测试/断言的落点清单（不在本任务中默认改代码）

# Task Dependencies
- Task 2 depends on Task 1: yes
- Task 3 depends on Task 2: yes
- Task 4 depends on Task 3: yes
- Task 5 depends on Task 4: yes
- Task 6 depends on Task 1: yes
- Task 7 depends on Task 2: yes
