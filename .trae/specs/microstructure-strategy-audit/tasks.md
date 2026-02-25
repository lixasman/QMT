# Tasks
- [x] Task 1: 建立策略文档与实现的逐项对照表
  - [x] 汇总策略文档中的模块输入/输出、参数默认值、退化行为与公式
  - [x] 逐文件核对 microstructure/ 与 service/daily_batch 的实现差异并记录

- [x] Task 2: 对齐微观结构模块的关键差异点
  - [x] 对齐 Volume Surprise 的分钟级聚合口径与输出字段
  - [x] 对齐 FeaturePipeline 的输出字段与标准化窗口（history_days）
  - [x] 对齐 VPIN 溢折价过滤的 premium_rate 接入路径（可用则过滤，不可用则退化）

- [x] Task 3: 对齐与筹码引擎的联动与日批输出
  - [x] 在日批合并输出中补齐策略文档定义的联动因子
  - [x] 确认产出列名与下游消费方一致（原始因子/标准化特征/联动因子）

- [x] Task 4: 增加最小回归测试集合
  - [x] 为 BVC/VPIN/OFI/Kyle/VS 的关键公式与退化路径补测试
  - [x] 为日批合并输出与联动因子补测试或最小可复现样例

- [x] Task 5: 输出最终核对结果摘要
  - [x] 汇总“策略文档 vs 现有实现”的不一致点清单
  - [x] 汇总每个不一致点的具体修改（文件/函数/关键逻辑）
  - [x] 汇总验证证据（测试结果与关键断言）并给出一致性结论

# Task Dependencies
- Task 2 depends on Task 1: yes
- Task 3 depends on Task 1: yes
- Task 4 depends on Task 2: yes
- Task 5 depends on Task 4: yes
