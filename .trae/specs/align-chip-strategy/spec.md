# ETF 筹码分布策略一致性校验与对齐 Spec

## Why
当前 `etf_chip_engine` 的实现需要与自定义的《ETF筹码分布计算_执行策略_优化后》完全一致，避免因策略误读导致输出指标偏差。

## What Changes
- 梳理并固化“策略文档 vs 代码实现”的逐模块对照点，形成可验证的验收清单
- 对齐 Module A（IOPV）中“成分股覆盖率外推”计算细节
- 对齐 Module D（申赎修正器）中“申购注入分布”的高斯参数化细节
- 增加关键公式的回归测试，防止未来回归
- 最终输出核对结果摘要（差异点、修正点、验证方式与结论）

## Impact
- Affected specs: IOPV 计算、MaxEnt 成交分布求解、换手衰减、申赎修正、布朗扩散、指标输出、主控编排
- Affected code:
  - etf_chip_engine/modules/iopv_calculator.py
  - etf_chip_engine/modules/redemption.py
  - etf_chip_engine/engine.py（仅用于验证链路，不强制变更）
  - 新增测试文件（位置待实现阶段确定）

## 策略对照摘要（以策略文档为准）

### Module A: IOPV 计算器
- IOPV 定义：以“每篮子口径”的成分股市值 + cashBalance，除以 reportUnit 得到“每份 ETF”的 IOPV
- 覆盖率门槛：成分股价格覆盖率 < 50% 时，IOPV 视为不可用（nan），premium_rate 退化为 0
- 覆盖率外推：当覆盖率达标但未全覆盖时，需要按 `total_components / covered` 对 basket_nav 做外推

### Module B: MaxEnt 成交分布求解器
- 价格网格需归一化到 [0,1] 以使 γ 项生效
- γ 取值：`gamma = -sign(premium_rate) * min(|premium_rate| * k_gamma, gamma_max)`；无 IOPV 或 IOPV 不可用时 γ=0

### Module C: 换手衰减模型
- ETF 版本 α=0.5 的前景理论混合衰减：`turnover = base_turnover * (alpha * f_prospect + (1-alpha))`
- f_prospect 分三段：获利区对数增强、浅套区平方根损失厌恶、深套区线性止损释放

### Module D: 申赎修正器
- 申购（ΔS>0）：在当日 VWAP 附近按高斯注入新筹码，σ = 5 × bucket_size（等价为 σ=5 个桶）
- 赎回（ΔS<0）：按 `chips(p) * g(r)` 的权重扣除，`g(r)=1+max(r,0)`，获利越高越优先赎回

### Module E: 布朗扩散（仅日终批量）
- 以 ATR(10) 决定扩散强度：`sigma_price = k_diff * atr`，并转为桶尺度 sigma_buckets
- 扩散后保持总量守恒（按扩散前总量归一化）

### Module F: 指标输出
- 获利盘比例：`sum(p <= P_t) / sum(all) * 100%`
- 密集区：对筹码序列平滑后找峰，输出 top_n 支撑/压力（相对 current_price 判定）
- ASR：`sum(|p-P_t|<=k*ATR)/sum(all)`

## ADDED Requirements

### Requirement: 策略一致性（关键细节）
系统 SHALL 在不改变既有外部接口语义的前提下，使筹码计算关键细节与策略文档一致，且具备可回归验证的测试用例。

#### Scenario: IOPV 覆盖率外推（成功）
- **GIVEN** 成分股总数为 N，已有价格覆盖数为 covered，且 covered ≥ 0.5N
- **WHEN** 计算 IOPV
- **THEN** basket_nav SHALL 先按 `N/covered` 做外推，再加上 cashBalance，最后除以 reportUnit

#### Scenario: IOPV 覆盖率不足（退化）
- **GIVEN** covered < 0.5N 或 reportUnit≤0
- **WHEN** 计算 premium_rate
- **THEN** premium_rate SHALL 为 0（γ=0，MaxEnt 退化为两约束）

#### Scenario: 申购注入高斯形状（成功）
- **GIVEN** ΔS>0 且给定 vwap
- **WHEN** 执行申购修正
- **THEN** 注入权重 SHALL 与 `exp(-0.5*(offset/sigma_buckets)^2)` 成正比，且 sigma_buckets=5

### Requirement: 核对结果交付
系统 SHALL 在本次变更完成后，输出“核对结果摘要”，至少包含：
- 是否存在与策略不符的逻辑（逐条列出）
- 每条不符项对应的代码位置与原因解释
- 采取的修正方式（修改点概述）
- 如何验证修正有效（测试/冒烟验证说明）以及最终结论

## MODIFIED Requirements

### Requirement: 现有实现对齐策略文档
现有实现中与策略文档不一致的细节 SHALL 被修正；对策略无关的工程差异（如异常提示文本、初始化路径）不作为“一致性”验收阻塞项，除非会改变筹码分布数值结果。

## REMOVED Requirements
无。
