## 先回答你新增的确认点

### 1) AkShare 交易所接口的“ETF份额”到底是什么？
- `ak.fund_etf_scale_sse(...)`（上交所）/ `ak.fund_etf_scale_szse()`（深交所）返回的是**“每一只 ETF 自己的基金份额/规模（份）”**。
- 这不是板块汇总，也不是行业指数份额。
- 你可以从 AkShare 源码注释直接确认：
  - [fund_etf_sse.py](file:///d:/Quantitative_Trading/qmt_env/Lib/site-packages/akshare/fund/fund_etf_sse.py#L13-L66) 明确是“ETF基金份额数据”，字段 `基金代码/基金简称/基金份额`。
  - [fund_etf_szse.py](file:///d:/Quantitative_Trading/qmt_env/Lib/site-packages/akshare/fund/fund_etf_szse.py#L15-L64) 明确是“ETF基金份额”，字段 `基金代码/基金简称/基金份额`。
- 因此拿到的 `基金份额` 可以直接对应到某个特定 ETF（通过基金代码匹配）。

### 2) “每天第一次运行把所有ETF份额都拉到本地，第二天直接对比”
- 这个思路非常正确，也和我准备的修复方向一致：把“份额快照”做成**每日全量快照**，然后对比“上一交易日/上一次快照”。

## 改造计划（在你原计划基础上增强）

### A. 份额获取：改为“交易所ETF份额表 → 本地每日快照”
1. 改写 `snapshot_all_etf_shares()`：
   - 调用 `fund_etf_scale_sse(YYYYMMDD)` + `fund_etf_scale_szse()`，得到两市 ETF 的 `基金代码 -> 基金份额`。
   - 将基金代码映射为系统内部的 `xxxxxx.SH/xxxxxx.SZ`（按两市来源确定后缀）。
   - 写入**带日期**的快照文件：
     - `output/state/etf_share_snapshot_YYYYMMDD.json`
   - 同时更新一个“最新快照”文件：
     - `output/state/etf_share_snapshot.json`
     方便现有逻辑/人工查看。
2. `compute_share_change_from_snapshot(etf_code_norm)`：
   - 当前日快照：读 `..._YYYYMMDD.json`（不存在则生成）
   - 对比基准：优先找“前一日（或前一交易日）的快照文件”；没有则回退到 `etf_share_snapshot.json` 中的上一次。
   - 任一缺失仍返回空，保证健壮。
3. 这样做的效果：
   - **每天第一次运行**会把全市场 ETF 份额抓取并落盘；
   - **第二天**再运行时天然能对比到昨天（即使你昨天只跑过一次/多次都不影响）。

### B. yesterday_evaluation：注入“综合评级+操作建议”两段
1. 新增解析函数：从昨日 `deepseek_output` 中提取：
   - `### 3. 综合评级` 整段
   - `### 4. 操作建议` 整段
2. 写入 `output/eval/finintel_signal_eval_{ETF6}_{YYYYMMDD}.md`（多行），保留旧 `.txt` 作为简版。
3. 读取时优先 md（更完整），再回退 txt，再回退旧 JSON 解析（你给的 `finintel_signal_159857_20260210.json` 可直接用于验证）。

### C. 验证（按你指定的 159857 文件 + 份额不为空）
1. 用 `FININTEL_FAKE_TODAY=20260211` 回放：
   - 159857 的 prompt “昨日综合评价”应包含昨日的“### 3/### 4”两段。
2. 同时检查输出：
   - “一级市场情绪：份额较昨日 增加/减少 X 万份”应有数值。

## 交付物
- 份额字段稳定输出：不再出现“份额较昨日 万份”空白。
- 每日全量份额快照：`output/state/etf_share_snapshot_YYYYMMDD.json` 可复用、可审计。
- yesterday_evaluation 注入增强：包含昨日两大节内容，帮助 DeepSeek 继承上下文。