## 需求确认（按你的最新要求）
- `up_down_ratio`：直接实现；统计范围=“创业板 + 沪深A股”的所有股票。
- `share_change_*`：采用路线A；每次运行自动保存“所有ETF的份额”到本地；“较昨日变化”按“较上次运行变化”计算；建议每日跑一次。
- `net_inflow`：采用东方财富“主力净流入”数据替代；先取“前五权重股主力净流入”；若权重股净流入获取失败，改取“ETF自身主力净流入”替代。

## 可行性与数据来源说明
- `up_down_ratio`、`share_change_*` 将严格使用 XtQuant 使用指南里明确出现的 xtdata 接口：
  - `get_sector_list` / `get_stock_list_in_sector`
  - `get_instrument_detail`
  - `download_history_data2` / `get_market_data`
  - `download_etf_info` / `get_etf_info`
- `net_inflow` 明确按你要求使用“东方财富资金流向”作为替代数据源；为避免手工逆向接口，优先通过本机已存在的 AkShare 对东方财富接口的封装获取（底层即 Eastmoney）。

## 实现方案

### 1) up_down_ratio（创业板 + 沪深A股全市场涨跌比）
- **股票池获取**
  - 调用 `xtdata.get_sector_list()` 获取所有板块名称。
  - 选择板块名包含关键词的板块：
    - 包含“沪深A股”的板块（可能有多个，全部合并）
    - 包含“创业板”的板块（同样合并）
  - `xtdata.get_stock_list_in_sector(sector_name)` 拉取成分，合并去重得到最终股票池。
- **涨跌统计**
  - 为避免一次性请求过大，按固定批量（例如 1000~2000 只/批）分块处理。
  - 每批：
    - `download_history_data2(stock_list, '1d', start_time=..., end_time='')` 先补齐最新交易日数据
    - `get_market_data(field_list=['close','preClose'], period='1d', count=1)` 获取最新交易日 close/preClose
    - 对每只股票比较 close 与 preClose：上涨/下跌/平盘，汇总全市场
  - 输出字段：
    - 建议存成 `"上涨:下跌(平盘)"`，例如 `3200:1800(300)`

### 2) share_change_direction/share_change_val（ETF份额变化，路线A + 全量缓存）
- **全ETF列表获取（严格按指南）**
  - 每次运行先 `xtdata.download_etf_info()`（若耗时大可做“每日一次”缓存策略，后续再优化）。
  - `xtdata.get_etf_info()` 返回“所有申赎数据 dict”，从其 key/字段中抽取 ETF 代码列表（具体结构运行时做兼容解析）。
- **份额读取（路线A）**
  - 对每个 ETF：调用 `xtdata.get_instrument_detail(etf_code, False)`，优先取 `FloatVolume` 作为“流通份额/份额代理”。
  - 将本次全ETF的 `{etf_code: float_volume}` 保存到本地文件，例如：`output/state/etf_share_snapshot.json`（包含时间戳）。
- **与上次运行对比**
  - 读取上次 snapshot，取当前标的ETF的 float_volume 对比：
    - `delta = cur - prev`
    - `share_change_direction = 增加/减少/不变`
    - `share_change_val = abs(delta)/10000`（按你的提示词单位“万份”）
  - 若不存在上次 snapshot：保持为空并告警“首次运行无法计算变化”。

### 3) net_inflow（东方财富主力净流入替代）
- **优先：前五权重股主力净流入（今日）**
  - 对每只权重股，调用 AkShare 的东方财富资金流接口（底层 Eastmoney）：
    - `akshare.stock.stock_fund_em.stock_individual_fund_flow(stock=代码, market=sh/sz)`
    - 取最新一行的 `超大单净流入-净额 + 大单净流入-净额`，汇总前5只
  - 转换单位到“万元”（通常 Eastmoney 返回单位为元）：`net_inflow = total/10000`
- **回退：ETF自身主力净流入（今日）**
  - 调用 AkShare 的 ETF 东方财富行情接口：
    - `akshare.fund.fund_etf_em.fund_etf_spot_em()`
    - 过滤 `代码 == etf_code`，取该行的 `超大单净流入-净额 + 大单净流入-净额`（或主力净流入-净额，按你偏好）
- **告警机制**
  - 若权重股全失败但 ETF 成功：warning 并标注 fallback
  - 若两者都失败：warning 并保留空值

## 产出与验证
- 更新信号输出 JSON：
  - `fields` 中三项不再空（首跑 share_change_* 可能为空属预期）
  - 额外在 trace 中记录：
    - `up_down_ratio` 统计覆盖的股票数量
    - `share_change` 使用的基准快照时间
    - `net_inflow` 采用的来源：top5-stocks vs etf-fallback
- 端到端跑 `--signal-etf 159107` 验证字段填充。

## 需要你确认的唯一点（不影响我开始写代码，但决定口径）
- `net_inflow` 你最终想要用：
  - (A) `超大单+大单`（更贴近“特大单+大单”）
  - (B) `主力净流入-净额`
  两者都能取到；默认我按你原提示词“特大单+大单”采用 (A)。