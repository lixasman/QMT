# Tasks
- [x] Task 1: 对齐 IOPV 覆盖率外推逻辑
  - [x] 将 IOPV 计算加入按覆盖率外推的步骤（N/covered）
  - [x] 保持覆盖率不足时的退化行为不变（nan / premium_rate=0）
  - [x] 确认与引擎 premium_rate 注入/回退逻辑兼容

- [x] Task 2: 对齐申购注入的高斯参数化
  - [x] 将申购注入权重的 sigma 调整为 5 个桶（sigma_buckets=5）
  - [x] 保持赎回扣除逻辑与现有一致（g(r)=1+max(r,0)）
  - [x] 确认注入后筹码总量增加量≈ΔS（数值误差允许）

- [x] Task 3: 增加关键回归测试（最小集合）
  - [x] 为 IOPV 覆盖率外推/覆盖率不足编写测试用例
  - [x] 为申购注入分布形状（sigma=5 桶）编写测试用例
  - [x] 将测试纳入可一键运行的命令（基于标准库 unittest）

- [x] Task 4: 冒烟验证日批/实时链路不破坏
  - [x] 使用单只 ETF 的历史快照数据跑一遍日批流程（不要求联网）
  - [x] 使用现有 realtime 的回放路径跑最小 tick 序列（不要求联网）

- [x] Task 5: 输出最终核对结果摘要
  - [x] 汇总“策略文档 vs 现有实现”的不一致点清单
  - [x] 汇总每个不一致点的具体修改（文件/函数/关键逻辑）
  - [x] 汇总验证证据（测试结果与关键断言）

# Task Dependencies
- Task 2 depends on Task 1: no
- Task 3 depends on Task 1: yes
- Task 3 depends on Task 2: yes
- Task 4 depends on Task 1: yes
- Task 4 depends on Task 2: yes
- Task 5 depends on Task 3: yes
