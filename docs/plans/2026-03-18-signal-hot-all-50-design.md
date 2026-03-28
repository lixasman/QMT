# signal-hot-all-50 设计

日期：2026-03-18

## 1. 背景
当前 `python -m finintel --signal-hot-top N` 会从 50 只 ETF 中筛选涨幅 >1% 的标的加入情绪因子计算池。本需求希望新增一个开关，在需要时将 50 只 ETF 全量加入计算池，不再做涨幅筛选；默认仍保持现有筛选行为。

## 2. 目标
- 新增 CLI 开关 `--signal-hot-all-50`（默认关闭）。
- 关闭时保持现有逻辑：只取涨幅 >1% 的 ETF 并入候选池。
- 开启时跳过筛选，直接并入 50 只 ETF 全量，且 `source_tag` 设为 `universe_all_50`。
- 保持旧命令行为与结果兼容。

## 3. 非目标
- 不调整热门 ETF 选择逻辑（TopN、多样化等不变）。
- 不改变情绪因子计算、排序或输出格式。

## 4. 设计概述
### 4.1 选择器扩展（推荐方案B）
在 `finintel/etf_selector.py` 的 `select_universe_daily_gainers` 中增加参数 `include_all: bool = False`：
- `include_all=False`：保持原逻辑，按 `gain_threshold` 过滤，并设置 `source_tag = "universe_up_gt_1pct"`。
- `include_all=True`：不做涨幅过滤，直接返回 50 只 ETF 全量，并设置 `source_tag = "universe_all_50"`。

### 4.2 主流程接入
在 `finintel/main.py`：
- 新增 CLI 参数 `--signal-hot-all-50`。
- 仅在 `--signal-hot-top` 模式下读取该开关，并传入 `select_universe_daily_gainers(..., include_all=bool(args.signal_hot_all_50))`。
- 其他流程保持不变。

## 5. 兼容性
- 默认 `--signal-hot-all-50` 关闭，现有命令输出不受影响。
- 仅新增可选行为，不破坏已存在参数与输出文件格式。

## 6. 验证方式（手工）
- 现有行为：
  - `python -m finintel --signal-hot-top 10 --no-trace`
- 新开关行为：
  - `python -m finintel --signal-hot-top 10 --signal-hot-all-50 --no-trace`
- 检查合并池中 ETF 数量及 `source_tag` 是否为 `universe_all_50`。
