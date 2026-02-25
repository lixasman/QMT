## 需求解读
- 保留现有 `--signal-etf` 单ETF分析。
- 新增自动筛选“今日热门 Top10 ETF”并批量跑 deepseek 分析与输出。
- 新增的 ETF 选股函数 `select_top_hot_etfs` 严格按你给的 4 步策略实现（负面清单/流动性/趋势过滤 → 3因子 → RankPCT → Score → Top10），并尽量用 Pandas 向量化/`groupby+transform`。

## 关键实现点（含你补充的 159857 验证诉求）

### 1) yesterday_evaluation 的匹配与注入（增强：支持仅有 JSON 的情况）
你提供了昨天的文件：`output/finintel_signal_159857_20260210.json`。为了确保它能作为明天的 `yesterday_evaluation` 输入，即使没有 `output/eval/*.txt` 也能工作，我会把“昨日评价读取逻辑”升级为两级回退：
- **优先**：读取 `output/eval/finintel_signal_eval_{ETF6}_{YYYYMMDD}.txt`
- **回退**：若 eval 文件不存在，则扫描 `output/finintel_signal_{ETF6}_*.json`，取最近一个日期的 JSON，解析其中 `deepseek_output`，用规则提取“综合评级”（例如 `中性/观望`）并注入

同时，为了能在今天（系统日期仍为 20260210）验证“明天能读到昨天 20260210 的文件”，我会加入一个**仅用于测试/回放**的日期覆盖：
- 环境变量 `FININTEL_FAKE_TODAY=YYYYMMDD`，用于让程序把“今天”当成指定日期，从而验证昨天匹配逻辑。

### 2) select_top_hot_etfs：数据来源与向量化计算
- **ETF全量列表（code/name）**：
  - 首选（按指南）：`xtdata.download_etf_info()` → `xtdata.get_etf_info()` 解析出 ETF 代码与名称
  - 兜底：若上述 API 缺失或返回空，则用 AkShare `fund_etf_spot_em()` 获取 `代码/名称` 并映射到 `xxxxxx.SZ/xxxxxx.SH`
- **日线数据**：对全量 ETF 批量 `download_history_data2(...,'1d', start_time=...)` + `get_market_data(field_list=['time','close','amount'], count=30)`
- **向量化处理**：把 xtdata 的宽表转成长表 `code,time,close,amount` 后 `groupby('code')` 计算 rolling 指标：
  - MA5/MA20
  - amount_5 / amount_20
  - 20D 波动率：`returns.rolling(20).std()`
- **过滤**：
  - 负面清单：name 包含 ["货币","债","存单","豆粕","QDII","跨境","海外","美国","日经","德国","法国","纳斯达克","标普"]
  - 流动性：当日 amount < 5000万
  - 趋势：close > MA5
- **因子**：
  - A=(close-MA20)/MA20
  - B=amount_5/amount_20（分母0或不足20天 → NaN → 剔除）
  - C=20D returns std
- **打分**：截面 RankPCT（0~1），Score=0.4*A_rank+0.4*B_rank+0.2*C_rank，取 Top10。
- **输出**：返回包含 `code,name,close,score,factor_a,factor_b,factor_c,amount` 的 DataFrame。

### 3) 批量跑 Top10 深度分析与输出
- CLI 新增：
  - `--signal-hot-top 10`：筛选 TopN 并逐个分析
  - `--signal-hot-max-workers 2`：批量 deepseek 并发数（默认 1~2，避免触发限流）
- 对 Top10 逐个调用现有 `run_etf_signal_pipeline`，并保持你现有的输出命名：
  - `output/finintel_signal_{ETF6}_{YYYYMMDD}.json/.md`
  - `output/eval/finintel_signal_eval_{ETF6}_{YYYYMMDD}.txt`
- 额外输出一个汇总：`output/finintel_signal_hot_{YYYYMMDD}.csv`（Top10 与打分因子）

## 代码组织
- 新增：`finintel/etf_selector.py`（实现 `select_top_hot_etfs`，关键处加中文注释）
- 修改：`finintel/main.py`
  - 增加 hot 模式 CLI 分支
  - 增强 yesterday_evaluation 读取：支持从你给的 `finintel_signal_159857_20260210.json` 回退提取
  - 加入 `FININTEL_FAKE_TODAY` 支持以便验证

## 验证（包含你指定的159857文件）
1. 先跑一次：`FININTEL_FAKE_TODAY=20260211 python -m finintel --signal-etf 159857 --no-trace`
   - 预期：程序会把 `output/finintel_signal_159857_20260210.json` 当作“昨天”，从其中提取综合评级并注入 prompt 的“昨日综合评价”。
2. 跑 hot 模式：`python -m finintel --signal-hot-top 10 --no-trace`
   - 预期：生成 Top10 的 CSV + 10 份 json/md/eval。
3. 二次跑（同一天）：验证 yesterday_evaluation 会从 eval 或 json 正确回填。
