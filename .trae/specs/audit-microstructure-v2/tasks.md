# Tasks
- [x] Task 1: 提炼策略规格书 V2 的模块契约
  - [x] 汇总 M0-M9 的输入/输出字段、参数默认值、公式与退化行为
  - [x] 汇总与筹码引擎联动因子与日批合并的字段契约

- [x] Task 2: 盘点当前实现与调用链
  - [x] 逐文件梳理 microstructure/ 的公开接口与关键内部逻辑
  - [x] 梳理 config.py、daily_batch.py 等集成点的参数与输出列

- [x] Task 3: 逐条对照并标注一致性结果
  - [x] 为每个模块输出“策略条款 → 代码位置 → 一致性结论”的对照表
  - [x] 对不一致项补充：影响、风险、建议修复方向与需用户决策点

- [x] Task 4: 输出审计结论与修复建议清单
  - [x] 给出整体结论（是否可视为与 V2 一致）
  - [x] 汇总建议修改项（按优先级/影响面排序），并明确“未改动代码”

# Task Dependencies
- Task 3 depends on Task 1: yes
- Task 3 depends on Task 2: yes
- Task 4 depends on Task 3: yes
