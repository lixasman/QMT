# 策略对比说明（原策略 vs 当前策略）

更新时间：2026-03-26

## 1. 目的

把 2026-03-14 之后这几轮关键策略改动和回测结果汇总到一处，便于后续复盘时快速回答三个问题：

1. 当前策略相对原策略多了哪些核心约束。
2. 这些约束在 2024 / 2025 趋势年是否带来了显著收益提升。
3. 在 2023 这类弱趋势环境里，哪些改动没有带来普适性改善。

## 2. 原策略与当前策略的核心差异

| 项目 | 原策略 | 当前策略 | 影响 |
| --- | --- | --- | --- |
| 确认单后再入场 | 无额外限制 | `--bt-no-reentry-after-confirm` | 确认单成交后，S2 及以上状态禁止重复加仓，直到卖出为止 |
| ATR 止损距离 | 无上下限 | `--exit-atr-pct-min 0.025 --exit-atr-pct-max 0.04` | 低波动时不过窄，高波动时不过宽 |
| 盈利保护 | 固定 Chandelier | `--exit-k-accel --exit-k-accel-step-pct 0.05 --exit-k-accel-step-k 0.2 --exit-k-accel-kmin 1.0` | 浮盈扩大后逐步收紧止损，减少高位利润回吐 |
| 回测成交价口径 | 买入默认追价，卖出默认折价 | `--buy-use-ask1 --exit-layer1-use-stop-price` | 回测成交价更接近 ETF 实际流动性，减少过度悲观滑点 |
| 高位追买拦截 | 无 | `--bt-skip-high-chase-after-first-signal --bt-high-chase-signal-source all_signals --bt-high-chase-lookback-days 60 --bt-high-chase-max-rise 0.15` | 两个月窗口内相对首个信号已涨超 15% 的入场被屏蔽 |
| continuation 续强入场 | 较早版本未纳入当前全量对照 | `cont_e1/b3`，后续又加入 `mature block` | 用于补趋势中继买点，但也可能改变高位追买记忆路径 |
| mature block 与首信号记忆 | 旧实现存在耦合 | 当前已解耦 | 被屏蔽的 continuation 不执行下单，但仍可作为 high-chase 首信号记忆 |

## 3. 当前主回测口径

### 3.1 50 票同账户主口径

适用于 2024 / 2025 全量对照的主命令族：

```text
--no-watch-auto
--allow-missing-chip
--initial-cash 400000
--light-logs
--bt-no-reentry-after-confirm
--exit-atr-pct-min 0.025
--exit-atr-pct-max 0.04
--exit-k-accel
--exit-k-accel-step-pct 0.05
--exit-k-accel-step-k 0.2
--exit-k-accel-kmin 1.0
--exit-layer1-use-stop-price
--buy-use-ask1
```

在此基础上，再叠加高位追买拦截：

```text
--bt-skip-high-chase-after-first-signal
--bt-high-chase-signal-source all_signals
--bt-high-chase-lookback-days 60
--bt-high-chase-max-rise 0.15
```

当前采取的最佳策略：

* 对 `50` 票同账户主回测，当前最佳策略就是“`3.1` 主口径 + `all_signals` 高位追买拦截”这一整套组合。
* 也就是：`禁确认后重复入场 + ATR%[2.5%,4.0%] + 盈利加速止损 + 回测成交价校准 + all_signals/60天/15% 高位追买拦截`。
* 当前不把 `continuation` / `mature block` 作为默认最佳策略的一部分；它们保留为独立实验分支。

新增的仓位同比例缩放参数：

* `--position-slot-cap`：单票名义仓位上限，默认 `70000`。
* `--position-risk-budget-min`：仓位 sizing 风险预算下限，默认 `2500`。
* `--position-risk-budget-max`：仓位 sizing 风险预算上限，默认 `6000`。

### 3.1.1 小资金同比例缩放模板

当账户资金不是 `40` 万，而是想按相同比例复现当前主策略时，可按 `目标资金 / 400000` 的比例缩放以下三项：

* `--position-slot-cap`
* `--position-risk-budget-min`
* `--position-risk-budget-max`

`5` 万账户示例：

```text
--initial-cash 50000
--position-slot-cap 8750
--position-risk-budget-min 312.5
--position-risk-budget-max 750
```

`20` 万账户示例：

```text
--initial-cash 200000
--position-slot-cap 35000
--position-risk-budget-min 1250
--position-risk-budget-max 3000
```

说明：

* 这组参数等价于把 `40` 万主口径按 `0.125` 线性缩放到 `5` 万账户。
* `20` 万账户对应的是 `0.5` 缩放，即 `70000 -> 35000`，`2500 -> 1250`，`6000 -> 3000`。
* 用途不是追求和大资金结果逐笔完全一致，而是检验“小资金账户能否近似复现同一套策略行为与收益结构”。

### 3.2 codes40 continuation 口径

适用于 2023 / 2024 `codes40` 测试的主命令族：

```text
--phase2-s-micro-missing 0.1
--phase2-continuation-entry
--phase2-continuation-chip-min 0.60
--phase2-continuation-micro-min 0.40
--phase2-continuation-lookback 10
--phase2-continuation-expire-days 1
--phase2-continuation-min-close-breakout-pct 0.003
--bt-no-reentry-after-confirm
--bt-skip-high-chase-after-first-signal
--bt-high-chase-signal-source all_signals
--bt-high-chase-lookback-days 60
--bt-high-chase-max-rise 0.15
```

如果继续打开 mature block，则再叠加：

```text
--phase2-continuation-mature-block
--phase2-continuation-mature-leg-days 5
--phase2-continuation-mature-bias-atr 2.0
--phase2-continuation-mature-near-high-atr 0.5
--phase2-continuation-mature-pullback-lookback 4
--phase2-continuation-mature-min-pullback-bias 0.2
```

## 4. 已完成的关键回测结果

### 当前最佳策略跨年份复跑

| 年份 | Universe | Run | 年化 | 最大回撤 | 交易数 |
| --- | --- | --- | --- | --- | --- |
| 2023 | 40票 | `backtest_2023_full40_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun` | 0.003652 | -0.103703 | 315 |
| 2024 | 50票 | `backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2` | 0.216177 | -0.058840 | 199 |
| 2025 | 50票 | `backtest_2025_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun` | 0.331445 | -0.094790 | 441 |

结论：

1. 当前最佳策略在 `2024`、`2025` 两个趋势年上都有效，其中 `2025` 最新复跑年化已经提升到 `33.14%`。
2. 同一套策略放到 `2023` 的 `40` 票样本上，年化只有 `0.37%`，说明它的适用边界依然是趋势年而非震荡/下跌年。
3. 因此当前阶段更合理的实盘定位是：把它视作“趋势环境高效、弱趋势环境保守”的策略，而不是追求全市场状态统一高收益。

对应结果文件：

* `D:\Quantitative_Trading\output\backtest_2023_full40_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun\summary.json`
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2\summary.json`
* `D:\Quantitative_Trading\output\backtest_2025_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun\summary.json`

### 小资金缩放复现（2024 / 50票 / 5万 / 0.125）

目的：

* 验证当前最佳策略从 `40` 万主口径缩到 `5` 万时，结果是否仍然接近线性复现。

| 口径 | Run | 初始资金 | 仓位缩放参数 | 年化 | 总收益 | 最大回撤 | 交易数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 主口径 | `backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2` | 400000 | 默认 | 0.216177 | 0.206768 | -0.058840 | 199 |
| 缩放复现 | `backtest_2024_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash50k_scale0125` | 50000 | `slot=8750, risk=[312.5,750]` | 0.210524 | 0.201381 | -0.062371 | 221 |

对比结论：

* `5` 万缩放版相对 `40` 万主口径，年化仅下降 `0.005653`，总收益下降 `0.005387`，最大回撤多 `0.003531`。
* 按 `40` 万结果完全线性缩到 `5` 万，期末净值应约为 `60338.398187`；实际 `5` 万版期末净值为 `60069.051741`，只少 `269.346446`。
* 这说明当前仓位缩放参数设计是有效的，小资金账户可以较高保真地复现主策略，不需要强行维持 `40` 万资金门槛。

对应结果文件：

* `D:\Quantitative_Trading\output\backtest_2024_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash50k_scale0125\summary.json`
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2\summary.json`

### 4.1 50 票同账户：2024 / 2025 主结果

| 年份 | Run | 关键条件 | 年化 | 最大回撤 | 交易数 |
| --- | --- | --- | --- | --- | --- |
| 2024 | `backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light` | baseline：ATR clamp + accel + pricefix | 0.161371 | -0.093815 | 249 |
| 2024 | `backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2` | baseline + high-chase(all_signals) | 0.216177 | -0.058840 | 199 |
| 2024 | `backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_missedexec` | baseline + high-chase(missed_executable) | 0.203143 | -0.058840 | 231 |
| 2025 | `backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light` | baseline：ATR clamp + accel + pricefix | 0.287009 | -0.093413 | 484 |
| 2025 | `backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15` | baseline + high-chase(all_signals) | 0.310249 | -0.093413 | 443 |
| 2025 | `backtest_2025_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun` | 最新代码口径复跑：baseline + high-chase(all_signals) | 0.331445 | -0.094790 | 441 |

结论：

1. `2024` 年，`all_signals` 版本把年化从 `16.14%` 提升到 `21.62%`，是显著抬升。
2. `2025` 年，`all_signals` 版本先把年化从 `28.70%` 提升到 `31.02%`，在最新代码口径复跑中进一步提升到 `33.14%`。
3. 高位追买拦截在趋势年是有效的，且 `all_signals` 口径优于 `missed_executable`。

对应结果文件：

* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light\summary.json`
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2\summary.json`
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_missedexec\summary.json`
* `D:\Quantitative_Trading\output\backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light\summary.json`
* `D:\Quantitative_Trading\output\backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15\summary.json`
* `D:\Quantitative_Trading\output\backtest_2025_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun\summary.json`

### 4.2 codes40：2023 / 2024 continuation 对照

| 年份 | Run | 关键条件 | 年化 | 最大回撤 | 交易数 |
| --- | --- | --- | --- | --- | --- |
| 2023 | `backtest_tick40_20230101_20231231_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_after1d_cont_e1_b3_codes40` | continuation `cont_e1_b3` | 0.014239 | -0.109652 | 339 |
| 2023 | `backtest_tick40_20230101_20231231_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_after1d_cont_e1_b3_mature_pb020_codes40` | continuation + mature block | 0.012318 | -0.111150 | 335 |
| 2024 | `backtest_tick40_20240101_20241231_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_after1d_cont_e1_b3_codes40` | continuation `cont_e1_b3` | 0.199398 | -0.081808 | 244 |

结论：

1. `2023` 年里，`mature block` 没有带来收益提升，年化从 `1.4239%` 下降到 `1.2318%`。
2. `2024` 年里，`codes40` continuation 口径年化能做到 `19.94%`，说明这类约束在趋势年依然有效。
3. 但当前默认最佳策略并不是 continuation 分支；按同一主策略口径重跑 `2023`，年化仅 `0.3652%`。
4. 因此不能把 `2023` 的结果简单外推到所有年份，更合理的结论是：当前策略对趋势环境更友好，对弱趋势环境普适性不足。

对应结果文件：

* `D:\Quantitative_Trading\output\backtest_tick40_20230101_20231231_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_after1d_cont_e1_b3_codes40\summary.json`
* `D:\Quantitative_Trading\output\backtest_tick40_20230101_20231231_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_after1d_cont_e1_b3_mature_pb020_codes40\summary.json`
* `D:\Quantitative_Trading\output\backtest_tick40_20240101_20241231_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_after1d_cont_e1_b3_codes40\summary.json`

### 4.3 单票修复验证：159811.SZ

这轮的核心修复不是“提升年化”，而是修掉 `mature continuation block` 与 `high-chase 首信号记忆` 的耦合问题。

问题现象：

* 在共享账户旧路径里，`2024-10-08` 曾出现一笔高位亏损买入。
* 根因不是简单的“多加了一条拦截规则”，而是 `continuation_blocked` 改变了后续首信号记忆与高位追买屏蔽路径。

修复后新增逻辑：

* 被 `continuation_blocked...` 挡掉的 continuation 信号，不执行下单。
* 但如果 `--bt-high-chase-signal-source all_signals` 开启，仍然把这次信号写入首信号记忆池。

单票验证结果：

| Run | 年化 | 交易数 | 关键验证 |
| --- | --- | --- | --- |
| `backtest_159811_20240101_20241231_mature_pb020_shadowseed` | -0.017366 | 15 | `2024-09-25` 到 `2024-10-15` 无任何成交，`2024-10-08/10-09` 均 `day_fills=0` |

对应结果文件：

* `D:\Quantitative_Trading\output\backtest_159811_20240101_20241231_mature_pb020_shadowseed\summary.json`
* `D:\Quantitative_Trading\output\backtest_159811_20240101_20241231_mature_pb020_shadowseed\fills.csv`
* `D:\Quantitative_Trading\output\backtest_159811_20240101_20241231_mature_pb020_shadowseed\logs\backtest_run_20260325_104520.log`

### 4.4 一次误跑结果说明

目录：

* `D:\Quantitative_Trading\output\backtest_tick40_20240101_20241231_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_after1d_cont_e1_b3_mature_pb020_shadowseed_codes40`

摘要：

* 年化 `0.240566`
* 最大回撤 `-0.077051`

说明：

* 这次任务名带了 `codes40`，但实际拉起的是 `50` 票 universe，不是严格可比的 `40` 票口径。
* 因此这个结果只能作为“修复后 50 票参考值”，不能直接与 `codes40` 表里的结果做正式同比。

## 5. 当前结论

1. 当前最佳策略对 `2024`、`2025` 这类趋势年有显著正效果，尤其是高位追买拦截对年化和回撤都有改善。
2. 当前最佳策略在 `2025` 最新复跑里年化已达到 `33.14%`，高于此前记录的 `31.02%`。
3. 当前策略对 `2023` 这类弱趋势环境没有表现出足够的普适性；即便交易有所约束，主矛盾仍然是首笔趋势单质量不足，而不是简单的重复追买。
4. `159811` 那条高位亏损买入路径已经在单票回测中被消除，说明“屏蔽执行”和“首信号记忆”解耦修复是有效的。
5. 如果后续要继续验证这条修复在共享账户 `codes40` / `50` 全量口径里是否完全生效，建议只跑 `2024-09-01` 到 `2024-11-15` 的短窗口，而不是直接重跑全年。

## 6. 本轮新增代码与测试

代码：

* `backtest/runner.py`

测试：

* `tests/test_entry_pending_lifecycle.py`

新增验证点：

* `test_backtest_blocked_continuation_still_seeds_high_chase_memory`

本地已通过：

* `pytest tests/test_entry_pending_lifecycle.py`
* `pytest tests/test_entry/test_phase2_continuation.py`

## 7. 相关文件

1. `backtest/main.py`
2. `backtest/runner.py`
3. `entry/phase2_config.py`
4. `entry/scoring.py`
5. `exit/exit_config.py`
6. `exit/chandelier.py`
7. `exit/scoring.py`
8. `exit/layer2.py`
9. `tests/test_entry_pending_lifecycle.py`
10. `docs/research/2026-03-16-backtest-test-summary.md`
