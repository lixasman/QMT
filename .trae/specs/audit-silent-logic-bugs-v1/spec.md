# 全链路“沉默逻辑 Bug”静态审计 Spec

## Why
当前项目包含多模块、多时序、多信号的策略链路，易出现“代码可运行但逻辑错误”的沉默缺陷，导致真实交易决策偏离预期且难以及时发现。

## What Changes
- 新增一套分批次的全链路静态审计工作流与交付规范（按模块切片，逐批输出 findings）
- 对每个信号函数建立端到端的数据血缘追溯（追溯至 DataAdapter 层）
- 系统性枚举沉默死分支、return 0/0.0 路径与触发概率，并给出可构造的具体触发输入
- 对齐信号输出刻度、score 聚合范围、Phase2 阈值与极端边界（全拉满/全为零）
- 审计关键时序点与状态机跨日一致性（避免未来函数、跨日 pending 漂移）
- 交付按 P0/P1/P2 分级的 findings 列表（每条包含文件:行号、问题描述、触发条件、修复建议）
- 如确认缺陷为“必修复”，可在后续实施阶段补充最小回归测试或静态断言以防回归（可能影响交易决策的修改标记为 **BREAKING**）

## Impact
- Affected specs: 入场(Phase1/2/3)、退出(L1/L2)、仓位FSM与T+0、DataAdapter/TradingAdapter、集成主循环与回放
- Affected code: core/、entry/、exit/、position/、integrations/、etf_chip_engine/、finintel/、main.py（以实际调用链为准）

## ADDED Requirements
### Requirement: 分批次全链路静态审计交付
审计流程 SHALL 按“模块切片”分批开展，每一批 SHALL 输出独立的 findings 集合，避免一次性审计导致上下文过大与注意力稀释。

#### Scenario: 切片审计（成功）
- **WHEN** 审计开始
- **THEN** 首先输出模块切片清单（含本批范围与下一批范围）与本批审计入口文件列表
- **AND THEN** 仅对本批范围输出 findings（按 P0/P1/P2 分级）

### Requirement: Findings 结构化格式
每一条 finding SHALL 包含以下字段，且字段内容具备可复现性：
- 文件:行号（精确到行）
- 具体问题描述（说明为什么是“能跑但错”或“沉默风险”）
- 构造的触发条件（具体数值/具体状态/具体输入形态，能触发该路径或错误）
- 修复建议（最小修复、替代实现、额外断言或测试建议）

#### Scenario: 单条 finding（成功）
- **WHEN** 发现逻辑缺陷或高风险沉默路径
- **THEN** 输出包含上述四要素的完整记录

### Requirement: 数据流完整性追溯
对每个信号函数（entry/exit/position 的 signals 及 scoring 相关入口）审计过程 SHALL：
- 追溯其每个输入参数的来源，最终追到 DataAdapter 层或外部输入（配置/状态/缓存）
- 标记“参数已定义但传入时始终为默认占位值”的情况，并给出证据链（调用栈/默认值路径）
- 盘点 WatchlistItem 字段在信号计算中的消费情况，输出“定义未使用/使用但未定义/字段名不一致”的差异

#### Scenario: 信号血缘（成功）
- **WHEN** 审计一个具体信号函数
- **THEN** 输出输入参数 → 调用者传参 → 上游产出字段/默认值 → DataAdapter 取数点 的链路

### Requirement: 沉默死分支与退化路径审计
审计过程 SHALL：
- 为每个 if/else 分支构造可触发的具体数值输入；若无法构造则判定为死分支并记录
- 枚举所有 return 0 / return 0.0 / score=0 的路径，并基于真实行情分布与上游缺失概率给出触发概率分级（高/中/低）
- 审计 score_entry 等多维门控规则是否严格按规则执行（例如“必须有 S_volume 或 S_chip_pr > 0”）

#### Scenario: 分支可达性（成功）
- **WHEN** 审计一个条件分支
- **THEN** 至少给出一组能进入该分支的具体输入（含数值范围、边界值与缺失形态）

### Requirement: 数值刻度一致性与极端边界
审计过程 SHALL：
- 给出每个信号输出范围（理论范围 + 实际裁剪/饱和/归一化后的范围）
- 对齐 scoring.py 的权重乘积与聚合逻辑，计算 entry score 的上下界（极端行情：全部拉满/全部为零）
- 明确 Phase2 进入阈值，并验证阈值与 score 上下界在同一刻度（避免阈值过大/过小导致永不触发或常态触发）

#### Scenario: 极端边界（成功）
- **WHEN** 对 entry score 做边界分析
- **THEN** 输出 score_min/score_max 及其构造方式，并对照 Phase2 阈值给出可达性判断

### Requirement: 时序与状态一致性（避免未来函数）
审计过程 SHALL：
- 检查 Phase2 在 _post_close 等时间点被调用时所使用 bars 数据是否包含当日，若包含则验证是否引入未来数据
- 审计 EntryFSM 的 pending signal 跨日保持时，状态的一致性与幂等性如何保证（崩溃恢复/重入/重复触发）

#### Scenario: 收盘后调用（成功）
- **WHEN** 发现收盘后调用使用了“当天收盘价/当天bar”的数据
- **THEN** 明确该数据在调用时刻是否已可得，若不可得则记录为 P0/P1 的未来函数风险并给出修复方案

## MODIFIED Requirements
无

## REMOVED Requirements
无

