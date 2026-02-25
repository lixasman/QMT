## 现状与问题
- finintel 当前“资金流向（净流入万元）”依赖 AkShare/东方财富资金流接口 + 权重股链路，容易被限流/返回空，导致 [finintel_signal_159363_20260212.md:L41](file:///d:/Quantitative_Trading/output/finintel_signal_159363_20260212.md#L41) 出现空值。
- 你提供的文档提出用“日内微观结构因子”（VPIN/OFI/Kyle/VS）替代，这类指标可由 L1 快照稳定复现。

## 关键差距（已结合你的补充修正）
- XtQuant 的 tick/L1 历史数据理论上可包含盘口字段（`bidPrice/bidVol/askPrice/askVol`），但当前仓库：
  - 没有 parquet 落盘脚本；
  - tick→snapshots 适配器只保留了 OHLCVA，丢掉了盘口字段。
- 结论：要启用 OFI（需要 bid/ask），必须先把盘口字段“从今天开始”落盘保存；积累几天后可再做 OFI 的滚动标准化（ZScore）。

## 替代方案总设计
- 新增一个“微观结构因子引擎（Daily Batch）”并与筹码引擎同批输出。
- finintel 信号侧改为**读取日批量因子**，将 prompt 中“资金流向”替换为微观结构摘要，彻底去掉不稳定的资金流接口。

## 实施计划
### 1) 从今天开始落盘 L1 快照（含盘口字段）
- 目标产物：`etf_chip_engine/data/l1_snapshots/{trade_date}/{etf_code}.parquet`（每只ETF一份，便于并行与增量）。
- 数据来源优先级：
  1) `xtdata.get_market_data(period="tick")` 的结构化返回中，若 dtype 包含 `bidPrice1/askPrice1/bidVol1/askVol1`（或同义字段），则直接取并写入。
  2) 若历史 tick 不含盘口，则用 `xtdata.get_full_tick()` 做补充采集（若支持历史回看则用于 batch；否则仅用于实时落盘，后续再补历史）。
- 代码改动点（实现时）：
  - 扩展 `etf_chip_engine/data/tick_adapter.py`：在生成 DataFrame 时，除 `time/high/low/close/volume/amount` 外，若存在盘口字段则追加 `bid1/bid1_vol/ask1/ask1_vol`（并统一命名）。
  - 在 `IndustryETFChipService.run_daily()` 中对每只 ETF：
    - tick→snapshots 后立即写 parquet（即使后续筹码引擎/微观引擎失败也能保留原始日内数据）。

### 2) 实现微观结构因子引擎（按文档结构）
- 新增 `etf_chip_engine/microstructure/`：
  - `bvc.py`：BVC 买卖分类（close-open + rolling std）。
  - `vpin.py`：VPIN（先实现不带 premium filter；premium_rates 预留接口）。
  - `ofi.py`：Cont-Stoikov OFI（当 snapshot 有 bid/ask 时可算 raw；无盘口则返回 NaN 并写明原因）。
  - `auxiliary_factors.py`：Kyle’s Lambda + Volume Surprise。
  - `feature_pipeline.py`：Rolling Rank/ZScore/Winsorize。
  - `factor_engine.py`：MicrostructureEngine 汇总 raw+features。
- 数据适配：
  - VPIN/Kyle/VS 可用 OHLCVA 快照计算（即使盘口缺失也能输出）。
  - OFI 仅在 parquet/tick 中确实包含 bid/ask 时输出（否则只输出缺失告警）。

### 3) 历史因子存储与标准化（OFI“积累几天后启用”落点）
- 按文档：新增 `data/factor_history/{etf_code}.parquet` 存 raw 因子日序列，保留最近 60 天。
- 标准化启用策略：
  - Day1~Day4：历史不足，features 输出中性值（vpin_rank=0.5、z=0）。
  - Day5+：开始输出 rolling rank / zscore。
  - OFI：只要当日盘口存在就可输出 `ofi_daily`；但 `ofi_daily_z` 需要历史≥5天才有意义（这就符合你“积累几天再启用”的诉求）。

### 4) 合并输出到 batch_results（单文件对接 finintel）
- 修改 `etf_chip_engine/daily_batch.py`/`service.py`：
  - 计算筹码因子（已有） + 微观因子（新增） → 合并到同一个 DataFrame。
  - 输出仍为 `etf_chip_engine/data/batch_results_{trade_date}.csv`，新增列不会破坏现有 finintel chip 读取。
- 新增列示例：
  - raw：`ms_vpin_filtered/ms_vpin_max/ms_ofi_daily/ms_kyle_lambda/ms_vs_max`
  - features：`ms_vpin_rank/ms_vpin_max_rank/ms_ofi_daily_z/ms_kyle_lambda_z/ms_vs_max_logz`
  - 元信息：`ms_ofi_available`、`ms_snapshot_has_l1`。

### 5) finintel 替换“资金流向”并增加告警
- 扩展 finintel 对 batch_results 的读取：新增读取 `ms_*` 字段。
- 修改 `finintel/prompts.py`：
  - 将“资金流向 … 万元”替换为“资金压力(微观结构)”摘要（包含 VPIN 分位、OFI Z、Kyle Z、VS logZ 等）。
  - 若当日字段缺失：显示“数据缺失”，并 stderr warning；提供 `MICRO_STRICT=1` 严格模式直接 raise。

### 6) 验证策略
- 当日验证：
  - 先跑一次日批量，确认 `l1_snapshots/{trade_date}/` 产生 parquet，且 parquet 中含 `bid1/ask1` 列（若 XtQuant 返回）。
  - 检查 batch_results 新增列是否落出（至少 VPIN/Kyle/VS 应稳定有值）。
- 积累几天后验证：
  - 检查 `factor_history/` 的历史文件增长。
  - 确认 `ms_ofi_daily_z` 从“中性/缺失”逐步变为可用值。

## 我会额外在实现里做的防踩坑约束
- 全链路缺失告警：区分“采集无盘口” vs “接口失败” vs “数据为0/不足”。
- 对字段命名做统一映射：`bidPrice1→bid1`、`bidVol1→bid1_vol` 等，避免 XtQuant 不同版本字段名差异影响 OFI。

如无新增要求，我会按以上顺序落地：先把盘口字段落盘（从今天开始积累），同时让 VPIN/Kyle/VS 立刻替代 finintel 的资金流向；OFI 在盘口列可用且历史积累足够后自动启用 zscore。