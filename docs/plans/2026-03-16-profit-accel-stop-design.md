# 盈利加速止损（PSAR式）设计

## 背景

当前策略在高位回撤时利润回吐明显。目标是在不显著增加交易次数的前提下，尽量提前锁定利润。

## 目标

- 以盈利加速止损收紧 Chandelier 止损，优先降低高位回撤。
- 对交易次数的增幅控制在约 20% 以内。
- 改动最小、可回退、默认关闭。

## 方案概述

仅在持仓进入盈利区间后，对 Chandelier 的 `k` 动态递减（类似 PSAR 加速）。基准 `k` 沿用当前策略（`k_normal / k_chip_decay / k_reduced`），不改变入场逻辑。

### 规则

- 仅当浮盈 `pnl_pct > 0` 时生效，亏损或微利不收紧。
- 递减公式：`steps = floor(pnl_pct / step_pct)`，`k = max(k_min, k_base - steps * step_k)`。
- 默认参数：`step_pct=0.05`（每增加 5% 浮盈收紧一次）、`step_k=0.2`、`k_min=1.0`。

### 作用范围

- 影响所有 Chandelier 止损计算（开盘 gap、盘中、盘后），统一使用动态 `k`。
- 与现有 ATR% clamp 兼容。

## 接口与配置

新增 CLI 开关（默认关闭）：

- `--exit-k-accel`
- `--exit-k-accel-step-pct`（默认 0.05）
- `--exit-k-accel-step-k`（默认 0.2）
- `--exit-k-accel-kmin`（默认 1.0）

## 验证计划

- 仅跑 588200。
- 对照 1：原参数 + ATR% clamp(2.5%~4%)。
- 对照 2：对照 1 + 盈利加速止损。
- 比较 `max_drawdown`、`annualized_return`、`trade_count`（<= 20%）。

## 回退

- 关闭 `--exit-k-accel` 即回退到原行为，无需改动其它参数。
