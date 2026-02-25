# Quantitative_Trading

一个以 ETF 为中心的“数据抓取 → 情报聚合/信号分析（LLM）→ 筹码/微观结构因子计算（XtQuant）”工具集合。代码主要由三个相对独立的子模块组成：

- `newsget/`：抓取财联社/东方财富热度新闻（Top5/Top10），可补全正文，输出 JSON
- `finintel/`：基于 DeepSeek 的两阶段聚合筛选（Top10 → 10 条摘要 → Top3）与 ETF 两种模式（权重股新闻/情绪信号）
- `etf_chip_engine/`：ETF 筹码分布计算引擎（MiniQMT/XtQuant 数据源），支持冷启动/日批量/盘中在线，并输出指标与状态快照

仓库里存在 `qmt_env/` 目录用于本地运行环境（包含大量三方包），不属于业务代码入口。

## 快速开始

### 运行环境

- Python：建议 3.10+（代码使用了 `X | Y` 类型写法）
- 依赖：见 [requirements.txt](file:///d:/Quantitative_Trading/requirements.txt)

安装：

```bash
python -m pip install -r requirements.txt
```

常见环境问题：

- 如果出现 `ImportError: Importing the numpy C-extensions failed` / `No module named numpy.core._multiarray_umath`，通常是 Python 版本与 NumPy 二进制包不匹配或安装损坏。建议在你的目标解释器上强制重装（以项目内 `qmt_env` 为例）：

```bash
.\qmt_env\Scripts\python.exe -m ensurepip --upgrade
.\qmt_env\Scripts\python.exe -m pip install --upgrade --force-reinstall --no-cache-dir numpy pandas scipy
```

目录约定：

- 默认输出目录：`output/`（自动创建）
- ETF 筹码状态/快照：默认在 `etf_chip_engine/data/` 下（可配置）

## 模块 1：NewsGet（新闻热度抓取）

用途：抓取

- 财联社「热度」Top5
- 东方财富「热度」Top5（当前取东方财富“网友点击排行榜”前 5）

并可选抓取每条新闻的正文内容，打印到控制台并可落盘 JSON。

入口：`python -m newsget`（[newsget/main.py](file:///d:/Quantitative_Trading/newsget/main.py)）

常用示例：

```bash
# 抓取两站并写入 JSON（默认 output/news_hot_<timestamp>.json）
python -m newsget

# 只打印不落盘
python -m newsget --no-file

# 不抓取正文（更快）
python -m newsget --no-content --no-file

# 仅抓取单站
python -m newsget --source cls --no-file
python -m newsget --source eastmoney --no-file
```

## 模块 2：FinIntel（DeepSeek 情报聚合/ETF 模式/信号）

入口：`python -m finintel`（[finintel/main.py](file:///d:/Quantitative_Trading/finintel/main.py)）

### 2.1 默认模式：Top10 新闻 → Top3（两阶段）

执行逻辑（见 [finintel/pipeline.py](file:///d:/Quantitative_Trading/finintel/pipeline.py#L68-L125)）：

1. Phase1：抓取 Top10 新闻（财联社 5 + 东方财富 5，含正文；由 [newsget/ingestion.py](file:///d:/Quantitative_Trading/newsget/ingestion.py) 提供）
2. Phase2：并发调用 DeepSeek（Prompt A）将每条新闻清洗成高密度摘要
3. Phase3：再次调用 DeepSeek（Prompt B）从 10 条摘要中筛出 Top3（严格 JSON）

运行：

```bash
python -m finintel
```

只输出最终 Top3，不写追踪文件：

```bash
python -m finintel --no-trace
```

输出（默认）：

- `output/finintel_top3_<timestamp>.json`：最终 Top3
- `output/finintel_trace_<timestamp>.json`：包含原文、摘要、错误等（可用 `--no-trace` 关闭）

### 2.2 ETF 权重股新闻模式：Top10 持仓 → 每股 1 条新闻 → Top3

逻辑（见 [finintel/etf_pipeline.py](file:///d:/Quantitative_Trading/finintel/etf_pipeline.py)）：

1. 获取 ETF 前十大持仓股（东方财富基金档案）
2. 对每只持仓股抓取“最新 1 条相关新闻”（可按发布时间过滤，失败时会回退）
3. 并发调用 DeepSeek 生成摘要
4. 用 ETF 专用 Prompt 聚合筛选 Top3（允许少于 3 条）

运行（以 510300 为例）：

```bash
python -m finintel --etf 510300 --no-trace
```

常用参数：

- `--etf-max-age-days`：新闻有效期（天），默认 3
- `--max-workers`：摘要并发数（覆盖 `PHASE2_MAX_WORKERS`）
- `--etf-source`：ETF 模式新闻来源（auto/akshare/cls/eastmoney），默认 auto

如需使用 AKShare 作为新闻来源（可选）：

```bash
python -m pip install akshare
```

### 2.3 ETF 情绪/信号模式：生成 JSON + 报告 + 昨日评价缓存

用途：对单只 ETF 产出“情绪/信号”类的结构化 JSON 与可读 Markdown，并自动继承昨日综合评价（用于 prompt 注入与连续性）。

运行（以 159107 为例）：

```bash
python -m finintel --signal-etf 159107 --no-trace
```

批量：先筛选热门 ETF TopN，再逐只生成信号（会写一个汇总 CSV）：

```bash
python -m finintel --signal-hot-top 10 --no-trace
```

输出（按日期命名，见 [finintel/main.py](file:///d:/Quantitative_Trading/finintel/main.py#L115-L153)）：

- `output/finintel_signal_<etf>_<YYYYMMDD>.json`：结构化结果
- `output/finintel_signal_<etf>_<YYYYMMDD>.md`：可读报告（含 Prompt 片段）
- `output/eval/finintel_signal_eval_<etf>_<YYYYMMDD>.txt|.md`：提取出的“综合评级/操作建议”片段
- `output/finintel_signal_hot_<YYYYMMDD>.csv`：热门 ETF TopN 筛选结果（仅 `--signal-hot-top`）

### 2.4 FinIntel 配置（环境变量）

- `DEEPSEEK_API_KEY`：必填
- `DEEPSEEK_BASE_URL`：可选，默认 `https://api.deepseek.com`
- `DEEPSEEK_MODEL`：可选，默认 `deepseek-chat`
- `DEEPSEEK_TIMEOUT`：可选，默认 40（reasoner 模型建议 120 以上）
- `DEEPSEEK_MAX_RETRIES`：可选，默认 3
- `DEEPSEEK_BACKOFF`：可选，默认 1（秒，退避 1,2,4...）
- `PHASE2_MAX_WORKERS`：可选，默认 8（可被 `--max-workers` 覆盖）
- `FININTEL_FAKE_TODAY`：可选，用于将“今日日期”固定为 `YYYYMMDD`（影响信号文件命名与昨日评价回溯）

Windows（PowerShell）配置示例（永久生效，新开终端后生效）：

```powershell
setx DEEPSEEK_API_KEY "你的Key"
setx DEEPSEEK_MODEL "deepseek-reasoner"
setx DEEPSEEK_TIMEOUT "120"
setx DEEPSEEK_MAX_RETRIES "3"
setx DEEPSEEK_BACKOFF "1"
setx PHASE2_MAX_WORKERS "8"
```

东方财富搜索接口如遇证书异常（仅排障用，不推荐长期启用）：

```powershell
setx EASTMONEY_INSECURE_SSL "1"
```

## 模块 3：ETF 筹码分布计算引擎（etf_chip_engine）

位置：`etf_chip_engine/`，完整使用说明见 [etf_chip_engine/README.md](file:///d:/Quantitative_Trading/etf_chip_engine/README.md)。

三种典型用法：

- 冷启动：无昨日 `.npz` 状态时，自动用最近 N 日日线初始化筹码
- 日批量：收盘后拉取当日 tick，更新筹码、做申赎修正/扩散，保存今日 `.npz` 并输出指标
- 盘中在线：订阅 tick 实时更新筹码与指标（不做日终扩散/申赎修正）

命令行示例：

```bash
# 请确保在项目根目录（d:\Quantitative_Trading）下运行以下命令
# PowerShell 示例：
#   cd /d D:\Quantitative_Trading
# 日批量：自动取最近交易日；跑全市场（或用 --limit 控制数量）
python -m etf_chip_engine.daily_batch --date auto --limit 20

# 日批量：只跑单只 ETF
python -m etf_chip_engine.daily_batch --date 20260212 --code 560780.SH

# 盘中在线：订阅 tick，处理够 min-ticks 后退出
python -m etf_chip_engine.realtime --etf 560780.SH --seconds 12 --min-ticks 10 --top-components 50
```

Python 调用示例：

```python
from etf_chip_engine.service import IndustryETFChipService

svc = IndustryETFChipService()
df = svc.run_daily("20260212", limit=20)
print(df.head())
```

关键门面入口：

- `IndustryETFChipService.run_daily(...)`：[etf_chip_engine/service.py](file:///d:/Quantitative_Trading/etf_chip_engine/service.py#L33-L140)
- 核心引擎流程（单次快照/日终）：[etf_chip_engine/engine.py](file:///d:/Quantitative_Trading/etf_chip_engine/engine.py#L33-L160)

说明：

- 该模块依赖 MiniQMT / XtQuant（`xtquant.xtdata`）可用且已登录
- 如希望将日内增量快照写为 parquet，需要额外安装 `pyarrow` 或 `fastparquet`（否则可配置降级写 csv）

## 模块 4：策略交易系统（全策略主循环）

用途：将 `core/entry/exit/position/t0` 五个模块集成为可在 QMT/MiniQMT 环境中运行的策略主循环，按“盘前→盘中→盘后”生命周期驱动，并使用同一份 `PortfolioState` 做状态共享与持久化。

入口：`python main.py`（[main.py](file:///d:/Quantitative_Trading/main.py)）

### 4.1 配置与状态/日志

- 状态文件：默认 `data/state/portfolio.json`（可用 `--state-path` 覆盖）
- 决策日志：默认写入 `data/logs/*.jsonl`（entry/exit/position/t0 各一份）
- 运行日志：`data/logs/strategy.log`

### 4.1.1 告警与降级（重要）

本项目采用“可降级但必须可见”的原则：当外部数据/状态/解析发生降级时，会在终端与日志中输出提示，避免静默降级导致难以排查。

告警分级（默认）：

- WARNING：影响交易主链路输入/状态一致性的降级（例如 integration 缺失、行情 K 线解析失败、订单回报关键字段无法解析等）
- INFO：外围链路或可接受回退（例如新闻源抓取回退、非关键特征计算跳过等）

严格模式（调试用）：

- 设置环境变量 `QT_STRICT_WARNINGS=1` 后，INFO/DEBUG 级别的降级提示会自动提升为 WARNING，便于在初期调试阶段实现“零告警运行”的目标。

### 4.2 启动方式（两种适配器模式）

QMT 模拟盘（XtQuant 交易适配器）：

```bash
python main.py --adapter xt --xt-path <miniQMT_userdata_path> --xt-account <account_id> --xt-session <session_id>
```

easytrader 实盘（GUI 交易适配器）：

```bash
python main.py --adapter gui --broker ths --gui-ops-limit 20 --gui-freeze-threshold 15
```

说明：

- 行情数据统一使用 `xtquant.xtdata`（要求 QMT/MiniQMT 客户端已登录，且 Python 环境可导入 `xtquant`）
- GUI 模式存在操作限额：默认 15 次进入冻结预警、20 次超限冻结；盘后会自动重置计数

### 4.3 推荐的“两日工作流”（T-1 产出 → T 日交易）

这套系统按“**T-1 盘后准备数据**，**T 日盘中执行交易**”的节奏运行。策略主循环在 T 日会从 `output/integration/` 目录读取上一交易日（严格 `< T 日`）的外部因子快照，并在盘后做 Phase2 扫描。

#### (1) T-1：盘后产出筹码分布 + 微观因子（15:30 后）

运行日批引擎（会同时计算筹码指标 + 微观结构因子，并写入给交易系统消费的 integration 文件）：

```bash
# PowerShell 示例：
.\qmt_env\Scripts\Activate.ps1
python -m etf_chip_engine.daily_batch --date auto
```

关键输出：

- 人读：`etf_chip_engine/data/batch_results_YYYYMMDD.csv`
- 机读（交易系统唯一路径）：`output/integration/chip/batch_results_YYYYMMDD.csv`
- 状态快照：`etf_chip_engine/data/chip_snapshots/<code>_<YYYYMMDD>.npz`
- L1 增量快照（用于审计/重算）：`etf_chip_engine/data/l1_snapshots/<YYYYMMDD>/<code>.parquet|.csv`

integration CSV 内会包含：

- 筹码字段（如 `profit_ratio`、`dense_zones_json`、`resistance_price_max_density`、`dpc_peak_density`、`chip_engine_days`）
- 微观字段（由微观结构引擎输出，常见为 `ms_` 前缀列；交易系统当前消费 `ms_vpin_rank`、`ms_ofi_daily_z`、`ms_vs_max_logz`）

#### (2) T-1：盘后产出情绪因子（可在 20:00 后运行）

需要 DeepSeek Key（见上文 FinIntel 环境变量）。建议用“热门 ETF TopN”批量生成：

```bash
python -m finintel --signal-hot-top 15 --no-trace
```

关键输出（交易系统消费）：

- `output/integration/finintel/sentiment_<code6>_YYYYMMDD.json`

该 JSON 会包含 `sentiment_score_01`（0-1）与 `sentiment_score_100`（0-100）等字段，供次日 `watchlist_loader` 读取。

#### (3) 构建 T 日候选池（Watchlist）

当前交易系统的候选池入口是命令行参数 `--watch`（可重复传入多只 ETF）。候选池的推荐构建逻辑与入场策略规格书一致：

- 入池硬门控：`sentiment_score >= 60` 且 `profit_ratio >= 75`
- 微观软门控：若 `vpin_rank > 0.70` 或 `ofi_daily < 0`，则标记 `micro_caution=True`（不剔除）

你可以用代码中的现成函数做筛选：

- 读取 integration 并构造 `WatchlistItem`：[watchlist_loader.py](file:///d:/Quantitative_Trading/integrations/watchlist_loader.py#L121-L207)
- 按策略阈值筛选候选池：[entry/watchlist.py](file:///d:/Quantitative_Trading/entry/watchlist.py#L61-L89)

实操上有两种方式：

- 手动方式：把你要关注的 ETF 列成参数
  - 例：`--watch 512480 --watch 159107`（支持 6 位代码或带交易所后缀；系统会在读取 integration 时规范化为 `.SH/.SZ`）
- 半自动方式：以 `finintel_signal_hot_YYYYMMDD.csv` 的 TopN 为初始集合，再用 `filter_watchlist()` 过滤出次日候选池，然后把结果拼成 `--watch` 参数启动交易系统

#### (4) T 日：运行量化交易主循环（开盘前启动即可）

建议在 09:00 左右启动。主程序会：

- 非交易时段自动等待
- 交易时段按 `tick_interval_s`（默认 3 秒）执行 tick 循环
- 15:00 后进入盘后流程（含 Phase2 扫描与状态落盘）
- 当天结束后自动退出（本项目是一日一跑；建议用任务计划程序/外部守护进程在下个交易日再次启动）

QMT 模拟盘：

```bash
# 注意：PowerShell 里尖括号 <...> 会被当作重定向运算符，示例中的占位符请直接替换为真实值，不要保留尖括号
python main.py --adapter xt --xt-path "D:\国金QMT交易端模拟\userdata_mini" --xt-account 8880354832 --xt-session 1 --watch 512480 --watch 159107
```

GUI 实盘：

```bash
python main.py --adapter gui --broker ths --watch 512480 --watch 159107
```

是否需要“盯盘”：

- 正常情况下不需要。开盘前启动后可以放着跑到收盘，程序会在盘后流程完成后自行退出。
- 如需中途停止，直接 `Ctrl+C` 即可（建议仅在非关键下单窗口使用；状态会在多处关键动作后持久化）。

### 4.4 盘前一键补齐（可选）

如果你希望每天只在开盘前运行一次程序（例如 08:30），并由程序自动检查/补齐 T-1 外部因子，可在启动时打开：

- `--auto-prep`：自动检查 `output/integration/` 下的 T-1 筹码/微观因子与情绪因子是否齐全；缺失则自动补齐
- `--watch-auto`：自动读取 `output/finintel_signal_hot_<T-1>.csv` 的 TopN 作为候选集合，并按策略阈值筛选后进入当天候选池
- `--hot-top N`：热门 ETF TopN（默认 15）

说明：

- 筹码/微观因子补齐会调用 `etf_chip_engine.daily_batch`，其默认行为是**全市场**计算（可用 `--limit` 在独立命令中限流）
- 情绪因子默认只对 TopN 计算；若你的持仓不在 TopN 内，程序会额外补齐持仓的情绪因子（用于 exit 评分/风控）

示例（xt 模式）：

```bash
python main.py --auto-prep --watch-auto --hot-top 15 --adapter xt --xt-path "D:\国金QMT交易端模拟\userdata_mini" --xt-account 8880354832 --xt-session 1
```

## 输出目录速查

- `output/`：newsget 与 finintel 默认输出根目录（自动创建）
- `output/cache/`、`output/state/`：部分流程使用的缓存/状态文件（用于复用与回溯）
- `etf_chip_engine/data/chip_snapshots/`：每日筹码状态（`.npz`）
- `etf_chip_engine/data/l1_snapshots/<YYYYMMDD>/`：日内增量快照（`.parquet` 或 `.csv`，按配置/依赖决定）

## 测试（离线可跑）

测试大多使用 mock，不依赖 DeepSeek Key 或真实网络：

```bash
python -m unittest discover -s tests
```

## 数据格式（示例）

newsget 落盘 JSON 形如：

```json
{
  "crawl_time": "2026-02-08T12:34:56+08:00",
  "items": [
    {
      "source": "财联社",
      "rank": 1,
      "title": "…",
      "url": "…",
      "hot": "…",
      "publish_time": "…",
      "content": "…",
      "crawl_time": "…"
    }
  ],
  "errors": [{ "source": "财联社", "error": "..." }]
}
```

## ETF Daily Batch Download And Date Semantics (2026-02)

- `python -m etf_chip_engine.daily_batch --date auto` now switches by local time:
- Trading day before `15:30`: use `T-1`
- Trading day at/after `15:30`: use `T`
- Non-trading day: use latest trading day
- Daily batch now pre-downloads tick data before factor/chip computation.
- Same-day reruns use per-day dedup state and only fetch missing codes:
- `output/cache/chip_tick_download/tick_YYYYMMDD.json`
- Empty-tick symbols trigger one automatic retry per code per day.
- Use `--force-download` to bypass dedup and re-download all selected symbols.
- Daily batch now also auto-cleans expired data with default retention `365` days.
- Coverage includes:
- `etf_chip_engine/data/l1_snapshots/<YYYYMMDD>/`
- `etf_chip_engine/data/chip_snapshots/*_<YYYYMMDD>.npz|.ema.json`
- `etf_chip_engine/data/batch_results_*.csv`
- `output/integration/chip/batch_results_*.csv`
- `output/cache/chip_tick_download/tick_YYYYMMDD.json`
- `xtdata` local `datadir` dated `.dat` files under `SH/` and `SZ/`
- Override retention days with:
- `python -m etf_chip_engine.daily_batch --date auto --retention-days 365`
