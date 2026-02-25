# 微观结构因子引擎策略一致性校验与对齐 Spec

## Why
当前 `etf_chip_engine/microstructure` 的实现需要与《微观结构因子引擎_执行策略.md》一致，避免因策略误读导致微观因子数值偏差，并确保与筹码引擎在日批产出层正确联动。

## What Changes
- 梳理并固化“策略文档 vs 代码实现”的逐模块对照点，形成可验证的验收清单
- 对齐 Module M1-M6 的关键公式、边界条件、默认参数与输出字段命名
- 对齐与筹码引擎的联动：premium_rate 输入接入、交叉因子计算与日批输出合并
- 增加最小集合回归测试，覆盖核心公式与退化路径
- 输出最终核对结果摘要（差异点、修正点、验证方式与结论）

## Impact
- Affected specs: BVC 买卖分类、VPIN、Cont-Stoikov OFI、Kyle's Lambda、Volume Surprise、特征标准化管线、因子引擎主控、日批合并输出
- Affected code:
  - etf_chip_engine/microstructure/bvc.py
  - etf_chip_engine/microstructure/vpin.py
  - etf_chip_engine/microstructure/ofi.py
  - etf_chip_engine/microstructure/auxiliary_factors.py
  - etf_chip_engine/microstructure/feature_pipeline.py
  - etf_chip_engine/microstructure/factor_engine.py
  - etf_chip_engine/service.py
  - etf_chip_engine/daily_batch.py（如需要补充交叉因子/列输出）
  - 新增测试文件（位置待实现阶段确定）

## ADDED Requirements

### Requirement: 微观结构因子与策略一致
系统 SHALL 使 `microstructure/` 子包的核心算法与策略文档一致，并在输入缺失或样本不足时按策略给定的退化逻辑返回中性/不可用值。

#### Scenario: BVC σ_ΔP 滚动估计与零波动退化（成功）
- **GIVEN** 输入快照含 open/close/volume，滚动窗口 lookback=100，min_periods=20
- **WHEN** 计算 `dp = close - open` 的滚动标准差 σ_ΔP
- **THEN** σ_ΔP SHALL 用 dp 的 rolling std 计算；当 σ_ΔP < 1e-8 时，v_buy=v_sell=V/2

#### Scenario: VPIN 成交量桶与末桶丢弃（成功）
- **GIVEN** 全日成交量 total_volume>0 且 n_buckets=50
- **WHEN** 构造成交量桶（允许桶跨快照按剩余成交量切分）
- **THEN** bucket_size SHALL 为 total_volume/n_buckets；最后一个未满桶 SHALL 被丢弃

#### Scenario: VPIN 溢折价过滤（成功）
- **GIVEN** premium_threshold=0.003 且 premium_saturate=0.01，且提供每快照 premium_rate
- **WHEN** 计算 VPIN 滚动序列（window=50）
- **THEN** 当窗口内平均绝对溢折价超过阈值时，VPIN_filtered SHALL 乘以 `max(0, 1 - avg_abs_premium / premium_saturate)`

#### Scenario: OFI（Cont-Stoikov）快照差分（成功）
- **GIVEN** 输入快照含 bid1/bid1_vol/ask1/ask1_vol 且有至少 2 行
- **WHEN** 计算 OFI(t)
- **THEN** OFI(t) SHALL 满足策略文档的分段定义，且输出 ofi_daily 以及末尾 20/60/240 快照聚合

#### Scenario: Kyle's Lambda（成功）
- **GIVEN** 输入快照含 close/v_buy/v_sell 且末尾 window=240 样本足够
- **WHEN** 计算 `ΔP` 与 `SignedVol=v_buy-v_sell` 的协方差/方差比
- **THEN** λ SHALL 为 `Cov(ΔP, SignedVol)/Var(SignedVol)`；当样本不足或 SignedVol 方差过小返回 nan

#### Scenario: Volume Surprise 分钟级聚合（成功）
- **GIVEN** 输入快照为 3 秒级，按 20 个快照聚合为 1 分钟成交量序列
- **WHEN** 计算 `VS(t)=V_minute(t)/EMA(V_minute, 20)`
- **THEN** 输出 SHALL 至少包含 vs_mean、vs_max、vs_last（用于下游或日终摘要）

#### Scenario: 特征标准化的回看窗口（成功）
- **GIVEN** history_days=20 且历史存储包含多日原始因子值
- **WHEN** 对 VPIN 做 Rolling Rank、对 OFI/Lambda 做 Rolling Z-Score、对 VS 做 Log+Z
- **THEN** 标准化 SHALL 仅使用最近 history_days 的历史窗口（忽略更早数据）

### Requirement: 与筹码引擎联动因子一致
系统 SHALL 在日批合并输出阶段计算并产出策略文档定义的联动因子。

#### Scenario: Premium-VPIN 交叉因子（成功）
- **GIVEN** vpin_rank 与当日平均溢折价 avg_premium
- **WHEN** 计算交叉因子
- **THEN** 返回值 SHALL 与策略文档的 `premium_vpin_cross` 一致

#### Scenario: 获利盘-OFI 背离因子（成功）
- **GIVEN** profit_ratio（来自筹码引擎）与 ofi_daily_z（来自微观因子引擎）
- **WHEN** 计算背离因子
- **THEN** 返回值 SHALL 与策略文档的 `profit_ofi_divergence` 一致

#### Scenario: ASR 变速度因子（成功）
- **GIVEN** asr_today 与 asr_yesterday
- **WHEN** 计算 asr_velocity
- **THEN** 返回值 SHALL 为 `asr_today - asr_yesterday`

### Requirement: 核对结果交付
系统 SHALL 输出“核对结果摘要”，至少包含：
- 是否存在与策略不符的逻辑（逐条列出）
- 每条不符项对应的代码位置与原因解释
- 采取的修正方式（修改点概述）
- 如何验证修正有效（测试/冒烟验证说明）以及最终结论

## MODIFIED Requirements

### Requirement: 日批链路的微观因子可用性
现有日批链路中微观因子模块 SHALL 支持接入 premium_rate（可选），并且在 premium_rate 不可用时仍可计算基础因子；当策略要求的输入不可得时，退化行为 SHALL 显式且可验证。

## REMOVED Requirements
无。
