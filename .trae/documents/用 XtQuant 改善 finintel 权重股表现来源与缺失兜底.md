## 结论：能否通过 XtQuant “直接”获取前五大权重股表现？
- **不能直接一行拿到“ETF 前五大权重股 + 权重 + 当日涨跌幅”这种成品结果**。
- 但 **可以用 XtQuant 提供的两类数据做“可用的兜底/近似”**：
  - **ETF 申赎清单（creation/redemption basket）**：`xtquant.xtdata.get_etf_info(etf_code)` 返回成分股与 `componentVolume`（数量），但**不包含“权重”字段**。可用 `componentVolume × 成分股价格` 归一化得到“近似权重”，再取 Top5。
  - **指数权重**：`xtdata.get_index_weight(index_code)` / `download_index_weight()` 能拿到“指数成分权重”，若 ETF 明确跟踪某指数，可作为“ETF 权重”的近似替代，但这不是基金真实持仓。

## 目前 finintel 的获取方式是什么？
- **权重股列表来源**：目前是抓取东方财富基金 F10 页面 HTML。
  - 代码：`newsget/sources/eastmoney_etf.py::fetch_etf_top10_holdings()` 通过 `FundArchivesDatas.aspx?type=jjcc&code=...` 抓表格，解析出 **股票代码/股票名称**，不含权重。见 [eastmoney_etf.py](file:///d:/Quantitative_Trading/newsget/sources/eastmoney_etf.py)
- **权重股“表现”计算方式**：拿到 top5 股票代码后，在 `finintel/etf_signal_pipeline.py::run_etf_signal_pipeline()` 把 ETF/大盘/权重股代码一起用 xtdata 下载日线，再在 `compute_etf_features()` 里计算每只权重股 **当日涨跌幅** 并拼成 `top5_stocks_perf`。见 [compute_etf_features](file:///d:/Quantitative_Trading/finintel/etf_signal_pipeline.py#L857-L1015)

## 为什么经常会出现缺失？
- **持仓抓取链路本身不稳定（最主要）**：
  - 东方财富页面是脚本拼装内容，可能会变更结构/字段，导致解析不到 table。
  - 可能遇到网络抖动、限流、反爬、超时等，`fetch_etf_top10_holdings()` 会抛异常或返回空列表。
  - 即使抓到表格，取的是“基金季报持仓”口径；某些 ETF（债券/货币/QDII 等）可能持仓表不按同样形式展示或为空。
- **信号批量模式可能主动关闭抓取**：例如热门 ETF 批处理时，为提速/降依赖会 `fetch_holdings=False`，此时 top5 自然为空。
- **行情拼接也可能导致“表现不可算”**：
  - 抓到的股票代码只有 6 位数字，虽然后续会补交易所，但个别情况下仍可能出现代码不匹配/停牌数据缺失，导致涨跌幅为空。

## 我建议的改进方案（需要你确认后执行）
### 1) 增加 XtQuant 兜底：当东方财富持仓缺失时，自动用 get_etf_info 推 Top5
- 当 `fetch_etf_top10_holdings()` 返回空或失败：
  - 调 `xtdata.get_etf_info(etf_code_norm)` 取 `stocks` 字典（成分股 + componentVolume）。
  - 用 xtdata 同步拉取这些成分股的当日/最近收盘价。
  - 计算 `componentVolume × close` 的金额占比作为“近似权重”，取 Top5。
  - 继续复用现有 `compute_etf_features()` 的涨跌幅拼接逻辑。
- 好处：**不依赖网页结构**，缺失率会显著下降。
- 代价/注意：这是“申赎篮子近似”，不是严格的基金季报权重。

### 2) 输出中标注来源与口径，提升可解释性
- 在 fields 里增加：`top5_source`（eastmoney/xtquant_basket/none）、`top5_as_of`（若 eastmoney 有截止日期则带上）。
- prompt 里把“权重股表现”行改成：
  - `前5大权重股（来源：{top5_source}，截至：{top5_as_of}）今日涨幅：{top5_stocks_perf}`

### 3) 缺失时更可控
- 缺失时保留你现在看到的兜底文案，并确保 stderr 有 warning（已做了一部分）。
- 增加环境变量 `HOLDINGS_STRICT=1`：严格模式下持仓缺失直接报错，方便你批跑时第一时间发现。

### 4) 验证
- 用 2-3 只经常缺失的 ETF（如 516150）跑一次 `--signal-etf`：
  - 确认能在日志里看到来源切换（eastmoney→xtquant 兜底）
  - 确认 `top5_stocks_perf` 不再为空

如果你确认，我将按上述 1-4 点改动 `finintel/etf_signal_pipeline.py` 与 `finintel/prompts.py`，并跑样例验证。