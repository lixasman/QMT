# 回测测试汇总（2026-03-15 ~ 2026-03-21）

说明
* 年化收益率取 `summary.json` 的 `annualized_return`（小数，0.03 = 3%）。 
* 如无特别说明，均为：单标的、`initial_cash=300000`、`--no-watch-auto`、`--bt-no-reentry-after-confirm`、`tick_seconds=3`、`fee_bps=0.85bps`。 
* 目录名即 run name，完整输出在 `output/<run>/`。 

## 回测运行命令（当前推荐模板 + 参数释义）

### 单票回测（常用基线）

```powershell
python -m backtest.main --start 20250101 --end 20251231 --data-root backtest/data --out-dir output/<run_name> --codes 588200.SH --no-watch-auto --initial-cash 300000 --light-logs --bt-no-reentry-after-confirm --bt-skip-high-chase-after-first-signal --bt-high-chase-signal-source all_signals --bt-high-chase-lookback-days 60 --bt-high-chase-max-rise 0.15 --exit-atr-pct-min 0.025 --exit-atr-pct-max 0.04 --exit-k-accel --exit-k-accel-step-pct 0.05 --exit-k-accel-step-k 0.2 --exit-k-accel-kmin 1.0 --exit-layer1-use-stop-price --buy-use-ask1
```

参数说明（仅列本策略常用项）：
* `--start/--end`：回测区间（YYYYMMDD）。 
* `--data-root`：数据目录（含 1d/tick 子目录）。 
* `--out-dir`：输出目录。 
* `--codes`：回测标的（可单票或逗号分隔多票）。 
* `--no-watch-auto`：关闭自动候选，使用静态 codes。 
* `--initial-cash`：初始资金。 
* `--position-slot-cap`：单票名义仓位上限；默认 `70000`，可按账户规模同比例缩放。 
* `--position-risk-budget-min`：仓位 sizing 风险预算下限；默认 `2500`。 
* `--position-risk-budget-max`：仓位 sizing 风险预算上限；默认 `6000`。 
* `--light-logs`：关闭高频 JSONL 决策日志（更快）。 
* `--bt-no-reentry-after-confirm`：确认单成交后禁止再入场（S2+ 直到卖出）。 
* `--bt-skip-high-chase-after-first-signal`：回测专用；若当前买入信号价格相对窗口内首个参考信号涨幅过大，则跳过本次入场。 
* `--bt-high-chase-signal-source`：高位追买窗口的记忆来源；`all_signals`=记录窗口内首个普通 Phase2 信号，`missed_executable`=仅记录“本可执行但因现金不足错过”的买点。 
* `--bt-high-chase-lookback-days`：高位追买窗口回看天数（当前测试用 `60`）。 
* `--bt-high-chase-max-rise`：相对窗口首信号的最大允许涨幅（当前测试用 `0.15`）。 
* `--exit-atr-pct-min/--exit-atr-pct-max`：Chandelier ATR% 下限/上限（如 2.5%~4%）。 
* `--exit-k-accel`：盈利加速止损（PSAR 式）开关。 
* `--exit-k-accel-step-pct`：浮盈每增加多少触发一次收紧（默认 5%）。 
* `--exit-k-accel-step-k`：每次收紧的 k 递减幅度（默认 0.2）。 
* `--exit-k-accel-kmin`：k 的最小值下限（默认 1.0）。 
* `--exit-layer1-sell-discount`：L1 / lifeboat 卖单按 `bid1 × 折价系数` 出单，默认 `0.98`。 
* `--exit-layer1-use-stop-price`：L1 / lifeboat 卖单按止损价出单，适合回测口径，不改变默认实盘行为。 
* `--buy-aggressive-multiplier`：主入场 / lifeboat 回补买单按 `ask1 × 系数` 出单，默认 `1.003`。 
* `--buy-use-ask1`：主入场 / lifeboat 回补直接按 `ask1` 出单，适合回测口径。 

### 全量回测（50 票同账户）

```powershell
python -m backtest.main --start 20250101 --end 20251231 --data-root backtest/data --out-dir output/<run_name> --no-watch-auto --allow-missing-chip --initial-cash 400000 --light-logs --bt-no-reentry-after-confirm --bt-skip-high-chase-after-first-signal --bt-high-chase-signal-source all_signals --bt-high-chase-lookback-days 60 --bt-high-chase-max-rise 0.15 --exit-atr-pct-min 0.025 --exit-atr-pct-max 0.04 --exit-k-accel --exit-k-accel-step-pct 0.05 --exit-k-accel-step-k 0.2 --exit-k-accel-kmin 1.0 --exit-layer1-use-stop-price --buy-use-ask1
```

补充说明：
* `--allow-missing-chip`：允许 chip 覆盖不全时继续回测（全量时常用）。 
* 当前推荐命令模板默认采用高位追买拦截的优选口径：`all_signals + 60天 + 15%`。 
* 若要复现实验对照组，可仅把 `--bt-high-chase-signal-source all_signals` 替换为 `--bt-high-chase-signal-source missed_executable`。 

### 全量回测（50 票同账户，小资金同比例缩放复现）

`5` 万账户模板：

```powershell
python -m backtest.main --start 20240101 --end 20241231 --data-root backtest/data --out-dir output/<run_name> --no-watch-auto --allow-missing-chip --initial-cash 50000 --position-slot-cap 8750 --position-risk-budget-min 312.5 --position-risk-budget-max 750 --light-logs --bt-no-reentry-after-confirm --bt-skip-high-chase-after-first-signal --bt-high-chase-signal-source all_signals --bt-high-chase-lookback-days 60 --bt-high-chase-max-rise 0.15 --exit-atr-pct-min 0.025 --exit-atr-pct-max 0.04 --exit-k-accel --exit-k-accel-step-pct 0.05 --exit-k-accel-step-k 0.2 --exit-k-accel-kmin 1.0 --exit-layer1-use-stop-price --buy-use-ask1
```

`20` 万账户模板：

```powershell
python -m backtest.main --start 20240101 --end 20241231 --data-root backtest/data --out-dir output/<run_name> --no-watch-auto --allow-missing-chip --initial-cash 200000 --position-slot-cap 35000 --position-risk-budget-min 1250 --position-risk-budget-max 3000 --light-logs --bt-no-reentry-after-confirm --bt-skip-high-chase-after-first-signal --bt-high-chase-signal-source all_signals --bt-high-chase-lookback-days 60 --bt-high-chase-max-rise 0.15 --exit-atr-pct-min 0.025 --exit-atr-pct-max 0.04 --exit-k-accel --exit-k-accel-step-pct 0.05 --exit-k-accel-step-k 0.2 --exit-k-accel-kmin 1.0 --exit-layer1-use-stop-price --buy-use-ask1
```

补充说明：
* 这组参数等价于把 `40` 万主口径按 `0.125` 缩放到 `5` 万账户。 
* `20` 万账户对应 `0.5` 缩放：`70000 -> 35000`，`2500 -> 1250`，`6000 -> 3000`。 
* 对应关系是：`70000 -> 8750`，`2500 -> 312.5`，`6000 -> 750`。 
* 若要复现 `20` 万账户，可按同样方式把这三项乘以 `0.5`。 

## 588200 全年：入场门槛与 L2 阈值对照（目的：验证 chip_min 与 l2t 对年化影响）

| Run | 区间 | 关键条件 | 年化(小数) |
| --- | --- | --- | --- |
| backtest_588200_20250101_20251231_paramtest_cash300k_noreentry | 20250102-20251231 | p2=0.35, chip_min=0.85, kn=2.0, kc=1.8, kr=1.5, l2t=0.7 | 0.017363 |
| backtest_588200_20250101_20251231_paramtest_cash300k_noreentry_chip100 | 20250102-20251231 | 同上，chip_min=1.0 | 0.033477 |
| backtest_588200_20250101_20251231_paramtest_cash300k_noreentry_chip100_l2t05 | 20250102-20251231 | 同上，l2t=0.5 | 0.033477 |

## 588200 全年：exit-k-normal × exit-layer2-threshold 网格（目的：检验 kn 与 l2t 敏感性）

说明：固定 chip_min=1.0，遍历 kn 与 l2t，其余参数为当时默认或未显式覆盖。 

| Run | kn | l2t | 年化(小数) |
| --- | --- | --- | --- |
| backtest_588200_grid_chip100_kn16_l2t06 | 1.6 | 0.6 | 0.033477 |
| backtest_588200_grid_chip100_kn16_l2t07 | 1.6 | 0.7 | 0.033477 |
| backtest_588200_grid_chip100_kn16_l2t08 | 1.6 | 0.8 | 0.030794 |
| backtest_588200_grid_chip100_kn18_l2t06 | 1.8 | 0.6 | 0.033477 |
| backtest_588200_grid_chip100_kn18_l2t07 | 1.8 | 0.7 | 0.033477 |
| backtest_588200_grid_chip100_kn18_l2t08 | 1.8 | 0.8 | 0.030794 |
| backtest_588200_grid_chip100_kn20_l2t06 | 2.0 | 0.6 | 0.033477 |
| backtest_588200_grid_chip100_kn20_l2t07 | 2.0 | 0.7 | 0.033477 |
| backtest_588200_grid_chip100_kn20_l2t08 | 2.0 | 0.8 | 0.030794 |

## 588200 短窗：L2 score 日志与信号调整（目的：定位 10-11 月止损触发信号）

| Run | 区间 | 关键条件 | 年化(小数) |
| --- | --- | --- | --- |
| backtest_588200_20250901_20251130_chip100_l2score | 20250901-20251128 | chip_min=1.0，`--exit-layer2-score-log` | 0.051198 |
| backtest_588200_20250901_20251130_chip100_l2score_senttime | 20250901-20251128 | 调整 S_sentiment/S_time 阈值，仍记录 L2 score | 0.051198 |
| backtest_588200_20250901_20251130_chip100_l2score_diverge | 20250901-20251128 | 放宽 S_diverge 阈值并复跑；当前目录结果对应 p2=0.35 版本 | 0.030129 |

## 588200 短窗：L1 快止损网格（目的：加速 L1 止损，观察卖点与年化变化）

说明：固定 chip_min=1.0、p2=0.35，遍历 kn 与 kc。 

| Run | kn | kc | 年化(小数) |
| --- | --- | --- | --- |
| backtest_588200_20250901_20251130_chip100_kn16_kc16 | 1.6 | 1.6 | 0.016788 |
| backtest_588200_20250901_20251130_chip100_kn16_kc18 | 1.6 | 1.8 | 0.039775 |
| backtest_588200_20250901_20251130_chip100_kn16_kc20 | 1.6 | 2.0 | 0.036391 |
| backtest_588200_20250901_20251130_chip100_kn18_kc16 | 1.8 | 1.6 | 0.016788 |
| backtest_588200_20250901_20251130_chip100_kn18_kc18 | 1.8 | 1.8 | 0.039775 |
| backtest_588200_20250901_20251130_chip100_kn18_kc20 | 1.8 | 2.0 | 0.036391 |
| backtest_588200_20250901_20251130_chip100_kn20_kc16 | 2.0 | 1.6 | 0.016788 |
| backtest_588200_20250901_20251130_chip100_kn20_kc18 | 2.0 | 1.8 | 0.039775 |
| backtest_588200_20250901_20251130_chip100_kn20_kc20 | 2.0 | 2.0 | 0.036391 |

## 588200 短窗：L1 快止损细化（目的：细化 kc 在 1.7~1.9 区间）

说明：固定 chip_min=1.0、p2=0.35、kn=2.0。 

| Run | kc | 年化(小数) |
| --- | --- | --- |
| backtest_588200_20250901_20251130_chip100_kn20_kc17 | 1.7 | 0.014462 |
| backtest_588200_20250901_20251130_chip100_kn20_kc18 | 1.8 | 0.039775 |
| backtest_588200_20250901_20251130_chip100_kn20_kc19 | 1.9 | 0.038001 |

## 588200 全年验证（目的：用短窗最优 kc=1.8 全年验证）

| Run | 区间 | 关键条件 | 年化(小数) |
| --- | --- | --- | --- |
| backtest_588200_20250101_20251231_chip100_kn20_kc18 | 20250102-20251231 | p2=0.35, chip_min=1.0, kn=2.0, kc=1.8 | 0.030794 |

## 159825 全年：基线与 paramtest 对照（目的：建立历史对照基线）

| Run | 区间 | 关键条件 | 年化(小数) |
| --- | --- | --- | --- |
| backtest_159825_20250101_20251231_baseline_cash300k | 20250102-20251231 | 默认参数 | -0.033746 |
| backtest_159825_20250101_20251231_baseline_cash300k_noreentry | 20250102-20251231 | 默认参数 + 禁再入场 | -0.025065 |
| backtest_159825_20250101_20251231_paramtest_cash300k | 20250102-20251231 | p2=0.35, kn=2.0, kc=1.8, kr=1.5, l2t=0.7, chip_min=0.85 | -0.053100 |
| backtest_159825_20250101_20251231_paramtest_cash300k_noreentry | 20250102-20251231 | 同上 + 禁再入场 | -0.050761 |
| backtest_159825_20250101_20251231_paramtest_cash300k_noreentry_chip100 | 20250102-20251231 | 同上 + chip_min=1.0 | -0.044864 |

## 159825 全年：使用 588200 最优止损参数（目的：验证参数泛化）

| Run | 区间 | 关键条件 | 年化(小数) |
| --- | --- | --- | --- |
| backtest_159825_20250101_20251231_chip100_kn20_kc18 | 20250102-20251231 | p2=0.35, chip_min=1.0, kn=2.0, kc=1.8 | -0.044864 |

## 159825 全年：归因隔离（目的：验证禁再入场与 chip_min 影响）

| Run | 区间 | 关键条件 | 年化(小数) |
| --- | --- | --- | --- |
| backtest_159825_20250101_20251231_chip100_kn20_kc18_noreentryOFF | 20250102-20251231 | p2=0.35, chip_min=1.0, kn=2.0, kc=1.8, 禁再入场=OFF | -0.053588 |
| backtest_159825_20250101_20251231_chip085_kn20_kc18 | 20250102-20251231 | p2=0.35, chip_min=0.85, kn=2.0, kc=1.8 | -0.050761 |

## 159825 全年：止损收紧试探（目的：在入场放宽前提下收紧 kc）

说明：固定 p2=0.35、chip_min=1.0、kn=2.8，遍历 kc。 

| Run | kc | 年化(小数) |
| --- | --- | --- |
| backtest_159825_20250101_20251231_chip100_p2s035_kn28_kc21 | 2.1 | -0.052345 |
| backtest_159825_20250101_20251231_chip100_p2s035_kn28_kc20 | 2.0 | -0.047351 |
| backtest_159825_20250101_20251231_chip100_p2s035_kn28_kc19 | 1.9 | -0.044327 |
| backtest_159825_20250101_20251231_chip100_p2s035_kn28_kc18 | 1.8 | -0.044864 |
| backtest_159825_20250101_20251231_chip100_p2s035_kn28_kc17 | 1.7 | -0.049273 |

## 备注

* 部分 run 名不同但参数可能重合，例如 `backtest_159825_20250101_20251231_paramtest_cash300k_noreentry_chip100` 与 `backtest_159825_20250101_20251231_chip100_kn20_kc18` 年化相同，参数集高度重合。 
* `backtest_588200_20250901_20251130_chip100_l2score_diverge` 目录被多次复跑，当前年化对应最后一次复跑结果（p2=0.35 版本）。 

## 2026-03-16 新增：Chandelier ATR% 止损上下限（目的：减少低波动洗盘、控制高波动回撤）

说明：新增 `--exit-atr-pct-min/--exit-atr-pct-max`，把 ATR/Close 夹在区间内（这里测试 2.5%~4%）。
| Run | 区间 | 关键条件 | 年化(小数) | 交易次数 |
| --- | --- | --- | --- | --- |
| backtest_159732_20250101_20251231_baseline_cash300k_noreentry | 20250102-20251231 | 默认参数 | -0.017087 | 22 |
| backtest_159732_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040 | 20250102-20251231 | 默认参数 + ATR%[2.5%,4%] | -0.007068 | 14 |
| backtest_588200_20250101_20251231_baseline_cash300k_noreentry | 20250102-20251231 | 默认参数 | 0.021508 | 12 |
| backtest_588200_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040 | 20250102-20251231 | 默认参数 + ATR%[2.5%,4%] | 0.042015 | 9 |
| backtest_159825_20250101_20251231_baseline_cash300k_noreentry | 20250102-20251231 | 默认参数 | -0.025065 | 12 |
| backtest_159825_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040 | 20250102-20251231 | 默认参数 + ATR%[2.5%,4%] | -0.018186 | 16 |

补充：把“588200 3.4% 参数”与 ATR clamp 叠加反而变差。
| Run | 区间 | 关键条件 | 年化(小数) | 交易次数 |
| --- | --- | --- | --- | --- |
| backtest_588200_20250101_20251231_paramtest_chip100_kn20_kc18_l2t07_atrclamp025_040 | 20250102-20251231 | p2=0.35, chip_min=1.0, kn=2.0, kc=1.8, kr=1.5, l2t=0.7 + ATR%[2.5%,4%] | 0.025506 | 24 |

结论（阶段性最优）：**默认参数 + ATR% 下限 2.5%（上限 4%）** 在 588200/159825/159732 上整体最稳健，是当前建议的对照方案。

## 2026-03-17 新增：盈利加速止损（PSAR式）A 方案（目的：降低高位回撤）

说明：在 **ATR%[2.5%,4%] + 禁再入场** 的基础上启用 `--exit-k-accel`，仅在浮盈区间收紧 Chandelier 的 `k`（每 +5% 浮盈，`k` 递减 0.2，最低 1.0）。  

| Run | 区间 | 关键条件 | 年化(小数) | 最大回撤 | 交易次数 |
| --- | --- | --- | --- | --- | --- |
| backtest_588200_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040 | 20250102-20251231 | 基线：默认参数 + ATR%[2.5%,4%] | 0.042015 | -0.031433 | 9 |
| backtest_588200_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040_accel | 20250102-20251231 | 基线 + 盈利加速止损 | 0.050066 | -0.012578 | 8 |
| backtest_159825_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040 | 20250102-20251231 | 基线：默认参数 + ATR%[2.5%,4%] | -0.018186 | -0.021167 | 16 |
| backtest_159825_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040_accel | 20250102-20251231 | 基线 + 盈利加速止损 | -0.018186 | -0.021167 | 16 |
| backtest_159732_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040 | 20250102-20251231 | 基线：默认参数 + ATR%[2.5%,4%] | -0.007068 | -0.009365 | 14 |
| backtest_159732_20250101_20251231_baseline_cash300k_noreentry_atrclamp025_040_accel | 20250102-20251231 | 基线 + 盈利加速止损 | -0.006612 | -0.008927 | 14 |

阶段性结论：盈利加速止损对 588200 的回撤/年化改善显著；对 159825 无影响；对 159732 有小幅改善。

## 2026-03-19 新增：回测成交价口径校准（目的：修正 L1 / lifeboat 与主入场成交价过于保守的问题）

本轮定位问题：
* 在分析 `159928.SZ` 2025-09 的 3 笔卖出时，发现日志里的触发价与 `trades.csv` 成交价存在系统性偏差。根因不是触发逻辑错误，而是**回测下单价本身被设置得过于保守**。 
* 旧逻辑下，`L1 / lifeboat` 卖单按 `bid1 × 0.98` 出单并在回测中直接按该委托价成交；对当前 ETF 资金体量，这相当于引入了显著高估的卖出滑点。 
* 旧逻辑下，主入场 `trial / confirm` 与 `lifeboat buyback` 买单按 `ask1 × 1.003` 出单并直接成交；对活跃 ETF 而言，这个买入滑点也偏保守。 

本轮已完成的代码操作：
* 新增回测可配置的 L1 / lifeboat 卖单定价参数：
  * `--exit-layer1-sell-discount`（默认 `0.98`，保持旧行为）
  * `--exit-layer1-use-stop-price`（回测可直接按止损价出单）
* 新增回测可配置的主入场 / lifeboat 回补买单定价参数：
  * `--buy-aggressive-multiplier`（默认 `1.003`，保持旧行为）
  * `--buy-use-ask1`（回测可直接按 `ask1` 出单）
* 以上改动均为**默认行为不变**的可回退开关；不加参数时，历史回测口径不变。 
* 已补充并通过聚焦测试：
  * `tests/test_entry/test_phase3_confirmer.py`
  * `tests/test_exit/test_exit_acceptance_scenarios.py`
  * `tests/test_exit/test_exit_fsm_lifeboat_once.py`
  * 本地验证结果：`20 passed`

当前建议口径：
* **回测建议**：使用更贴近 ETF 实际成交的口径，即：
  * `--exit-layer1-use-stop-price`
  * `--buy-use-ask1`
* **实盘说明**：实盘卖出仍需保留 **2% 折价**（`0.98`）以保证挂单必定成交；本次调整仅为回测口径校准，**不改变实盘默认卖单折价要求**。 
* 实盘买入侧当前默认 `ask1 × 1.003` 未被强制改动；若后续要单独优化实盘买入追价逻辑，应与回测口径分开讨论。 

推荐的回测命令附加参数（在原基线命令后追加）：

```powershell
--exit-layer1-use-stop-price --buy-use-ask1
```

## 2026-03-20 新增：全账户跨年份验证（目的：验证当前最优方案在 50 票同账户下的稳定性）

说明：
* 本节采用同一口径对比 2024 / 2025 全年结果。 
* 关键条件统一为：`50票同账户 + initial_cash=400000 + --light-logs + --bt-no-reentry-after-confirm + --exit-atr-pct-min 0.025 + --exit-atr-pct-max 0.04 + --exit-k-accel + --exit-k-accel-step-pct 0.05 + --exit-k-accel-step-k 0.2 + --exit-k-accel-kmin 1.0 + --exit-layer1-use-stop-price + --buy-use-ask1`。 

| Run | 区间 | 年化(小数) | 总收益(小数) | 最大回撤 | 交易次数 |
| --- | --- | --- | --- | --- | --- |
| backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light | 20240102-20241231 | 0.161371 | 0.154497 | -0.093815 | 249 |
| backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light | 20250102-20251231 | 0.287009 | 0.275463 | -0.093413 | 484 |

输出目录（绝对路径）：
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light`
* `D:\Quantitative_Trading\output\backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light`

阶段性结论：
* 当前方案在全账户口径下表现显著改善，2024 年化约 `16.14%`，2025 年化约 `28.70%`。 
* 结合此前单票测试，**ATR% 下限 2.5% + 盈利加速止损 + 回测成交价校准 + 禁再入场** 是当前阶段性最优组合。 

## 2026-03-20 新增：2024 全账户分票归因快照（目的：辅助复盘收益来源与拖累来源）

2024 拖累收益前五：

| ETF | PnL |
| --- | --- |
| 159811.SZ | -13211.798032 |
| 516970.SH | -8357.061504 |
| 588200.SH | -6541.189682 |
| 512710.SH | -5104.426997 |
| 159997.SZ | -4684.365949 |

2024 贡献收益前五：

| ETF | PnL |
| --- | --- |
| 562570.SH | 18651.302249 |
| 512200.SH | 16761.193814 |
| 512880.SH | 15908.616438 |
| 560280.SH | 14693.141541 |
| 512070.SH | 14580.633838 |

相关文件（绝对路径）：
* 分票归因：`D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light\per_etf_pnl.csv`
* 拖累票 SVG 索引：`D:\Quantitative_Trading\output\hourly_charts\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_losers\index.html`
* 贡献票 SVG 索引：`D:\Quantitative_Trading\output\hourly_charts\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_winners\index.html`

## 2026-03-20 新增：562500.SH 个案诊断（目的：解释 2024 年 2-3 月反弹前半段为什么没参与）

结论：
* 不是 `Phase3` 慢，而是 `Phase2` 一直到 `2024-02-27` 收盘才第一次达到默认阈值 `0.45`。 
* 触发当日的构成是：`S_volume=1.0 + S_chip_pr=0.85 + S_micro=0.3`，总分 `0.45`；`S_trend` 当天仍为 `0`。 

核心原因拆解：
* 本地 `1d` 数据从 `2024-01-18` 才开始，导致反弹前半程样本数过短。 
* `S_volume` 需要至少 21 根日线；`S_squeeze` 需要至少 25 根；`S_trend` 需要至少 30 根，因此 2 月前半段多个主分项天然为 `0`。 
* `watchlist` 中的 `profit_ratio / micro` 使用的是开盘前灌入的上一交易日筹码结果，存在 `T-1` 滞后。 
* `2024-02-26` 虽然已经放量突破，但实体 K 线条件未满足，`S_volume` 仍为 `0`；直到 `2024-02-27` 才首次满足。 

诊断日志与数据（绝对路径）：
* 聚焦回测目录：`D:\Quantitative_Trading\output\backtest_562500_20240101_20240331_focus_full_logs_pricefix_cash400k`
* Phase2 / Phase3 决策日志：`D:\Quantitative_Trading\output\backtest_562500_20240101_20240331_focus_full_logs_pricefix_cash400k\logs\entry_decisions.jsonl`
* 日线数据：`D:\Quantitative_Trading\backtest\data\1d\562500_SH.csv`

当前处理结论：
* 该问题本质是“新样本 + 急反弹 + 确认型入场”带来的先天偏晚，当前仅做行为记录，**暂不据此改策略参数**。 

## 2026-03-21 新增：全账户高位追买拦截对照（目的：减少资金不足后续补追与高位重复追买）

说明：
* 本节统一采用全账户口径：`50票同账户 + initial_cash=400000 + --light-logs + --bt-no-reentry-after-confirm + --exit-atr-pct-min 0.025 + --exit-atr-pct-max 0.04 + --exit-k-accel + --exit-k-accel-step-pct 0.05 + --exit-k-accel-step-k 0.2 + --exit-k-accel-kmin 1.0 + --exit-layer1-use-stop-price + --buy-use-ask1`。 
* `all_signals` 方案：两个月窗口内记录该 ETF 的**首个普通 Phase2 买入信号**；若当前信号相对首信号涨幅 `>=15%`，则跳过。 
* `missed_executable` 方案：只记录“`Phase3` 已准备下单，但因 `available_cash` 不足而错过”的首个可执行买点；同样用 `60` 天窗口和 `15%` 涨幅门槛阻断后续追买。 

| Run | 区间 | 信号来源 | 年化(小数) | 总收益(小数) | 最大回撤 | 交易次数 | 高位拦截次数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light | 20240102-20241231 | baseline | 0.161371 | 0.154497 | -0.093815 | 249 | - |
| backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2 | 20240102-20241231 | all_signals | 0.216177 | 0.206768 | -0.058840 | 199 | 32 |
| backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_missedexec | 20240102-20241231 | missed_executable | 0.203143 | 0.194346 | -0.058840 | 231 | 6 |
| backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light | 20250102-20251231 | baseline | 0.287009 | 0.275463 | -0.093413 | 484 | - |
| backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15 | 20250102-20251231 | all_signals | 0.310249 | 0.297665 | -0.093413 | 443 | 49 |

补充观察：
* 2024 年里，`all_signals` 相对基线年化提升约 `+5.48` 个百分点，且最大回撤从 `-9.38%` 收敛到 `-5.88%`。 
* 2024 年里，`missed_executable` 也优于基线，但弱于 `all_signals`；其全年仅记录到 `5` 次“资金不足错过的可执行买点”，最终只形成 `6` 次高位拦截，覆盖面明显偏窄。 
* 2025 年里，`all_signals` 没有伤害收益，反而把年化从 `28.70%` 提升到 `31.02%`，同时交易次数减少 `41` 笔，最大回撤不变。 

阶段性结论：
* 在当前两年全账户样本下，**`all_signals` 明显优于基线，也优于 `missed_executable`**。 
* 因此，高位追买拦截的当前建议口径是：`--bt-skip-high-chase-after-first-signal --bt-high-chase-signal-source all_signals --bt-high-chase-lookback-days 60 --bt-high-chase-max-rise 0.15`。 
* `missed_executable` 保留为实验开关，后续若要进一步收窄策略含义，可继续作为对照组使用。 

相关输出目录（绝对路径）：
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2`
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_missedexec`
* `D:\Quantitative_Trading\output\backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15`

## 2026-03-26 新增：当前最佳策略跨年份复跑（目的：确认当前最佳策略在 2023 / 2024 / 2025 的真实边界）

说明：
* 当前最佳策略统一定义为：`50票主口径/40票可跑口径 + --bt-no-reentry-after-confirm + --exit-atr-pct-min 0.025 + --exit-atr-pct-max 0.04 + --exit-k-accel + --exit-k-accel-step-pct 0.05 + --exit-k-accel-step-k 0.2 + --exit-k-accel-kmin 1.0 + --exit-layer1-use-stop-price + --buy-use-ask1 + --bt-skip-high-chase-after-first-signal + --bt-high-chase-signal-source all_signals + --bt-high-chase-lookback-days 60 + --bt-high-chase-max-rise 0.15`。 
* `2023` 由于当前数据覆盖限制，仅采用 `40` 只可跑 ETF；`2024/2025` 采用 `50` 票同账户口径。 
* 本节用于回答一个问题：当前最佳策略是否已经足够稳健，还是本质上只适合趋势年。 

| 年份 | Universe | Run | 年化(小数) | 总收益(小数) | 最大回撤 | 交易次数 |
| --- | --- | --- | --- | --- | --- | --- |
| 2023 | 40票 | backtest_2023_full40_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun | 0.003652 | 0.003507 | -0.103703 | 315 |
| 2024 | 50票 | backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2 | 0.216177 | 0.206768 | -0.058840 | 199 |
| 2025 | 50票 | backtest_2025_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun | 0.331445 | 0.317902 | -0.094790 | 441 |

补充对照：
* 2025 旧版同策略结果 `backtest_full_20250101_20251231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15` 年化为 `0.310249`，本次最新复跑提升到 `0.331445`。 
* 2023 旧版 `codes40 + continuation cont_e1_b3` 可做到 `0.014239`，但那不是当前默认最佳策略口径。 

阶段性结论：
* 当前最佳策略在 `2024`、`2025` 两个趋势年表现显著，尤其 `2025` 最新复跑已达到 `33.14%` 年化。 
* 同一套策略放到 `2023`，年化仅 `0.37%`，说明其本质上仍是趋势友好型策略，而不是震荡/下跌年也能稳定取利的万能策略。 
* `2023` 的主要问题并不是“交易没有减少”，而是减少掉的多数是重复入场/高位补追；主亏损来源仍然是弱趋势环境中的首笔失败开仓。 

相关输出（绝对路径）：
* `D:\Quantitative_Trading\output\backtest_2023_full40_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun`
* `D:\Quantitative_Trading\output\backtest_2025_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun`
* `D:\Quantitative_Trading\output\hourly_charts\backtest_2023_full40_opt_atrclamp025_040_accel_pricefix_highchase15_cash400k_light_rerun_losers\index.html`

## 2026-03-26 新增：仓位同比例缩放复现（目的：验证小资金账户能否近似复现 40 万主口径）

说明：
* 本节仍采用当前最佳策略，只新增仓位同比例缩放参数：`--position-slot-cap`、`--position-risk-budget-min`、`--position-risk-budget-max`。 
* `5` 万测试采用 `0.125` 缩放，即从 `40` 万主口径映射为：`70000 -> 8750`，`2500 -> 312.5`，`6000 -> 750`。 
* 目标不是逐笔成交完全一致，而是看净值、年化、回撤是否仍接近同一收益结构。 

| 口径 | Run | 初始资金 | 仓位参数 | 年化(小数) | 总收益(小数) | 最大回撤 | 交易次数 | 期末净值 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40万主口径 | backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2 | 400000 | 默认 | 0.216177 | 0.206768 | -0.058840 | 199 | 482707.185498 |
| 5万缩放复现 | backtest_2024_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash50k_scale0125 | 50000 | `slot=8750, risk=[312.5,750]` | 0.210524 | 0.201381 | -0.062371 | 221 | 60069.051741 |

对比结论：
* 相比 `40` 万主口径，`5` 万缩放版年化仅低 `0.005653`，总收益仅低 `0.005387`，说明收益结构保真度较高。 
* 最大回撤从 `-0.058840` 小幅放大到 `-0.062371`，交易次数增加 `22`，属于资金更紧后的合理副作用。 
* 若把 `40` 万主口径的期末净值完全线性缩到 `5` 万，理论值约 `60338.398187`；实际 `5` 万版为 `60069.051741`，只少 `269.346446`。 
* 因此，对想用 `20` 万、`10` 万、`5` 万账户复现策略的场景，优先建议使用“同比例缩放仓位参数”而不是硬套 `40` 万默认仓位。 

相关输出（绝对路径）：
* `D:\Quantitative_Trading\output\backtest_2024_full50_opt_atrclamp025_040_accel_pricefix_highchase15_cash50k_scale0125`
* `D:\Quantitative_Trading\output\backtest_full_20240101_20241231_opt_atrclamp025_040_accel_pricefix_cash400k_light_highchase15_v2`

