# 修复P1级IOPV降级与成分股清理计划

## 背景与问题定义

`python -m etf_chip_engine.daily_batch` 在日批计算中会 `attach_iopv()`，但日批并不会像实时模式那样为 IOPV 注入成分股价格，因此 IOPV 覆盖率长期为 0，触发“成分股覆盖率过低，已降级为 NaN”，并使 `premium_rate≈0`，进而影响筹码分布（MaxEnt 的溢价偏度项）以及部分微观结构因子（AP filter / premium 交叉项）。

该问题属于 P1：不会导致任务直接失败，但会造成交易链路关键输入（如 `profit_ratio` 等）系统性偏移。

## 目标

1. 日批计算出的 `premium_rate` 不再因为 IOPV 覆盖率=0 而系统性退化为 0。
2. 当为了 IOPV 计算需要下载成分股数据时，在计算完成后清理相关下载数据，避免磁盘占用持续增长。
3. 保持现有产出格式兼容（`etf_chip_engine/data/batch_results_*.csv` 与 `output/integration/chip/batch_results_*.csv`）。

## 总体策略（两级兜底）

### 方案A（优先，零额外磁盘）：直接使用 tick 数据自带的 iopv 字段生成 premium_rate

若 XtQuant 的历史 tick 支持返回 iopv（项目内已在实时 full_tick 中存在 `t["iopv"]` 的读取逻辑），则：

1. 扩展 `xtdata_provider.get_market_tick_data()` / `get_local_tick_data()` 的 `field_list`，将 `iopv`（或 XtQuant 对应字段名）纳入返回结果。
2. 扩展 `ticks_to_snapshots()`，将 iopv 透传到 snapshots，并生成 `premium_rate=(close-iopv)/iopv`（iopv<=0 或缺失则置为 0/NaN，并标注质量字段可选）。
3. 修改 `ETFChipEngine.process_snapshot()` 的分支选择：当 snapshots 中已携带 `premium_rate` 时，直接使用，不再触发 `IOPVCalculator` 分支（避免 coverage=0 导致的系统性退化）。
4. 微观结构端 `premium_rates` 优先直接使用 snapshots 的 `premium_rate` 序列（无需 IOPVCalculator）。

优点：不需要下载成分股 tick；不会引入额外磁盘清理问题；premium 与行情源一致。

风险：XtQuant 历史 tick 可能不提供 iopv 字段或字段名不同，需要在实现中做“可用性探测 + 回退到方案B”。

### 方案B（兜底，全市场下载成分股）：对全市场ETF进行 IOPV 计算，并在任务结束后清理下载数据

当方案A不可用（拿不到 iopv 字段）时，为保证最小改动并修复 P1：

1. 构建全市场ETF的成分股集合：
   - 对 universe 内每只 ETF 调用 `get_etf_info()`，取 `stocks`；
   - 按 `componentVolume` 排序取 Top-N（复用 realtime 的语义，N 通过 config/常量控制，默认保持保守值以避免数据爆炸）；
   - 将所有 ETF 的 Top-N 成分股做去重并形成 `all_component_codes`。
2. 预下载全量成分股 tick（按 chunk）：
   - `ensure_tick_data_downloaded(all_component_codes, trade_date)`（必要时复用现有 chunk_size）。
3. 批量获取成分股“同日收盘附近价格”并灌入 IOPV：
   - 使用 `xtdata.get_local_data(..., stock_list=[...], period="tick", end_time=trade_date 151000, count=1)` 或等价接口按 chunk 拉取每只成分股的最后价；
   - 构建 `component_last_price_map[code] = lastPrice`；
   - 对每只 ETF 的 `IOPVCalculator`，遍历其 Top-N 成分股并调用 `update_stock_price(code, last)`；
   - 这样 `calculate_iopv()` 的覆盖率显著提升，`get_premium_rate()` 不再系统性返回 0。
4. 磁盘清理策略（满足“不占用磁盘”的硬约束）：
   - 在整批计算结束、CSV 已落盘后，删除 xtdata datadir 下与 `trade_date` 对应的 tick 数据文件（按文件名包含/等于交易日的模式匹配）。
   - 说明：若 xtdata 的本地存储是“按交易日聚合文件”，则无法只删除成分股而不影响ETF自身 tick；此处以“计算完成后立即释放磁盘”为优先，允许清理该交易日相关 tick 缓存文件。

## 具体改动点（预期文件范围）

1. `etf_chip_engine/data/xtdata_provider.py`
   - 扩展 tick 取数字段：尝试加入 iopv 字段；
   - 新增“下载前后文件快照 + 清理”的工具函数（仅在方案B启用）。
2. `etf_chip_engine/data/tick_adapter.py`
   - 透传 iopv（若存在）并生成 `premium_rate` 列。
3. `etf_chip_engine/service.py`
   - `premium_rates` 生成逻辑改为优先使用 snapshots 自带 `premium_rate`；
   - 仅在需要且可用时 attach/使用 `IOPVCalculator`（避免无意义 attach 导致告警）。
4. （可选）`etf_chip_engine/daily_batch.py`
   - 增加配置/参数以控制是否启用成分股下载、Top-N、以及清理策略开关（若你希望通过 CLI 控制）。

## 验证方式

1. 运行 `python -m etf_chip_engine.daily_batch --date auto --limit 10`：
   - 不再出现“IOPV: 成分股覆盖率过低，已降级为 NaN”（方案A成功时应消失；方案B时应显著减少或仅在极端异常时出现）。
   - `batch_results_*.csv` 中 `profit_ratio` 与历史对比不再出现系统性异常偏移（抽样对比 2-3 只ETF的结果分布即可）。
2. 观察磁盘：
   - 若启用方案B：日志输出清理统计，且 xtdata datadir 在该次运行后不增加（或仅增加可解释的少量文件）。
3. 单元/回归：
   - 为 tick_adapter 的 `premium_rate` 生成逻辑补充测试（iopv 缺失、iopv<=0、正常值）。

## 交付物

1. 日批 premium 不再长期退化为 0（消除 P1 影响源）。
2. 成分股下载数据在计算完成后可清理（best-effort，并明确 xtdata 存储结构的边界）。
