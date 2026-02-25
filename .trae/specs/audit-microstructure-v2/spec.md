# 微观结构因子引擎 V2 一致性审计 Spec

## Why
`etf_chip_engine/microstructure` 的微观因子代码由 AI 生成，需要核查其实现逻辑是否与《微观结构因子引擎_执行策略_v2.md》逐条一致，以避免策略口径偏差导致下游因子矩阵失真。

## What Changes
- 建立“策略规格书 V2 → 代码实现”的逐模块、逐条款对照表（输入/输出、参数默认值、公式、退化/降级行为、字段契约）
- 对 `microstructure/` 与 `daily_batch.py` 等集成点做一致性核查并标注差异
- 输出不一致点的详细说明（策略条款、当前实现、差异原因、影响范围、建议修改方式）
- **BREAKING**：本变更不包含任何代码改动；仅做审计与建议。任何修复需用户确认后另起变更执行

## Impact
- Affected specs: M0-M9 全模块一致性、与筹码引擎联动因子、日批合并输出字段契约
- Affected code:
  - etf_chip_engine/microstructure/preprocessor.py
  - etf_chip_engine/microstructure/microprice.py
  - etf_chip_engine/microstructure/bvc.py
  - etf_chip_engine/microstructure/vpin.py
  - etf_chip_engine/microstructure/ofi.py
  - etf_chip_engine/microstructure/auxiliary_factors.py
  - etf_chip_engine/microstructure/orthogonalizer.py
  - etf_chip_engine/microstructure/feature_pipeline.py
  - etf_chip_engine/microstructure/factor_engine.py
  - etf_chip_engine/config.py（如存在微观因子参数定义）
  - etf_chip_engine/daily_batch.py（如存在合并输出与联动因子）

## ADDED Requirements
### Requirement: 策略规格书 V2 一致性审计交付
系统 SHALL 基于《微观结构因子引擎_执行策略_v2.md》对实现进行逐条核查，并在交付中提供可追溯的差异清单与建议修改方案。

#### Scenario: 审计交付（成功）
- **WHEN** 完成对 M0-M9 与日批合并的核查
- **THEN** 交付 SHALL 包含：
  - 逐模块一致性结论：一致 / 不一致 / 无法判定（注明原因）
  - 每条不一致项：对应的策略条款位置、代码位置（文件/函数/关键片段）、差异描述、潜在影响、建议修复方向
  - 需要用户决策的选项：当策略文本存在歧义或实现存在多种合理口径时，给出可选方案与取舍

### Requirement: 禁止未经确认的代码改动
系统 SHALL 在审计阶段不对代码库进行修改；仅输出分析与建议。任何修复类改动 SHALL 在用户明确确认后另起变更执行。

## MODIFIED Requirements
无。

## REMOVED Requirements
无。

