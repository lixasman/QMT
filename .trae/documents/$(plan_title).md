# 开盘前运行保障计划（基于全仓代码梳理）

## 目标

在不改代码的前提下，建立对当前仓库“数据产出 → 外部因子集成 → 盘前初始化 → 盘中主循环 → 盘后落盘”的整体认知，并给出 **09:30 之前**你需要完成的操作清单，用来最大化“可启动、可下单、可恢复、可追踪”。

## 项目流程总览（你需要知道的最小闭环）

### 四个相对独立的模块

- 新闻抓取：`python -m newsget`（落盘 `output/` JSON）
- 情报聚合/ETF 情绪信号：`python -m finintel ...`（落盘 `output/`；交易系统消费 `output/integration/finintel/`）
- ETF 筹码/微观结构因子引擎：`python -m etf_chip_engine.daily_batch ...`（落盘引擎数据与 `output/integration/chip/`）
- 交易主循环：`python main.py ...`（读取上面两类 integration，驱动 entry/exit/position/t0 五模块，落盘 state/log）

### 交易主循环生命周期

交易系统入口是 [main.py](file:///d:/Quantitative_Trading/main.py#L21-L41)，核心调度在 [StrategyRunner.run_day](file:///d:/Quantitative_Trading/strategy_runner.py#L131-L149)：

- 等待到交易时段（可 09:00 启动后自动等待）
- 盘前：`_pre_open()` 做资金同步、外部因子刷新、遗留退出处理、T0 日内准备、写入 watchlist 与状态
- 盘中：`_intraday_loop()` 每 `tick_interval_s` 执行一次 tick cycle（pending entry / position / orders）
- 盘后：`_post_close()` 做收尾落盘与 Phase2 扫描（生成次日可能的 pending entry）

## 关键依赖与“必须存在/可用”的落盘

### 交易系统必须可读写

- 状态：`data/state/portfolio.json`（可用 `--state-path` 覆盖）
- 日志目录：`data/logs/`（`strategy.log` + 各模块 JSONL）

### 交易系统强烈建议在盘前就绪的外部因子（T-1 产出）

交易系统会从 `output/integration/` 读取 **严格早于 today** 的最新文件（见 [watchlist_loader.py](file:///d:/Quantitative_Trading/integrations/watchlist_loader.py#L42-L57)）：

- 筹码/微观结构：`output/integration/chip/batch_results_YYYYMMDD.csv`
- 情绪：`output/integration/finintel/sentiment_<code6>_YYYYMMDD.json`

说明：缺失时系统会降级为默认因子（sentiment=50、profit_ratio=0），仍可能启动但会显著影响候选筛选与风控（见 [watchlist_loader.py](file:///d:/Quantitative_Trading/integrations/watchlist_loader.py#L146-L176) 与 [StrategyRunner._build_watchlist](file:///d:/Quantitative_Trading/strategy_runner.py#L270-L288)）。

### 行情/交易侧必须可用

- 行情：`xtquant.xtdata`（项目固定使用；见 [XtDataAdapter](file:///d:/Quantitative_Trading/core/adapters/data_adapter.py#L19-L27)）
- 交易（2 选 1）：
  - xt 模式：`xtquant.xttrader` + QMT/MiniQMT 登录；且启动参数必须包含 `--xt-path/--xt-account/--xt-session`（否则直接失败，见 [strategy_runner.py](file:///d:/Quantitative_Trading/strategy_runner.py#L183-L188)）
  - gui 模式：easytrader + `data/easytrader.json`（prepare 失败会降级继续，但实盘风险高；见 [strategy_runner.py](file:///d:/Quantitative_Trading/strategy_runner.py#L162-L170)）

## 09:30 前你需要做什么（按时间顺序）

### A. 盘前必做（建议 08:40–09:10 完成）

- 确认运行环境选对了解释器：用于交易的那个 Python 能 import `xtquant`（xt 模式必须）
- 确认 QMT/MiniQMT 客户端已登录且行情权限正常（否则行情/日历/集合竞价取数会失败）
- 确认交易适配器参数准备好
  - xt：`--adapter xt --xt-path ... --xt-account ... --xt-session ...`
  - gui：`--adapter gui --broker ths` 且 `data/easytrader.json` 可用
- 确认状态/日志目录可写：`data/state/`、`data/logs/`（否则无法恢复与审计）

### B. T-1 数据就绪检查（建议 09:10–09:20 完成）

- 检查 `output/integration/chip/` 下存在 `batch_results_<T-1>.csv`，且日期 **小于** 今天（系统不会用今天或未来日期文件）
- 对你计划交易的每只 ETF：检查 `output/integration/finintel/` 下存在 `sentiment_<code6>_<T-1>.json`
- 如果缺失（推荐处置优先级）：
  - 缺 chip：优先补 `python -m etf_chip_engine.daily_batch --date auto`
  - 缺 sentiment：再补 `python -m finintel --signal-hot-top N --no-trace` 或对单只 `--signal-etf`

### C. 候选池确认（建议 09:20–09:25 完成）

- 明确当日 `--watch` 列表（可重复传参），这是交易系统的候选池入口
- 如果你按“硬门控”挑选候选：策略阈值在 [entry/watchlist.py](file:///d:/Quantitative_Trading/entry/watchlist.py#L61-L89)，核心是 `sentiment_score >= 60` 且 `profit_ratio >= 75`

### D. 盘前自检（建议 09:25–09:28 完成）

针对“已有持仓”的 ETF（state 里 `positions` 不为空的场景），盘前会做 T0 准备：读取 09:15–09:25 集合竞价成交量并计算比值（见 [StrategyRunner._pre_open](file:///d:/Quantitative_Trading/strategy_runner.py#L343-L367) 与 [XtDataAdapter.get_auction_volume](file:///d:/Quantitative_Trading/core/adapters/data_adapter.py#L163-L193)）。

因此至少需要确认：

- 集合竞价 tick 可取到（否则 `t0_prepare_day failed` 会在日志报错）
- 行情快照“新鲜度”正常（系统内部以 15 秒为阈值标记 STALE，见 [data_adapter.py](file:///d:/Quantitative_Trading/core/adapters/data_adapter.py#L47-L66)；若持续 stale，盘中动作可能被跳过或降级）

### E. 启动交易主循环（建议 09:00–09:10 启动，最晚不晚于 09:28）

- 推荐直接 09:00 启动 `python main.py ...`，程序会自动等待到交易时间再进入盘中循环
- 启动示例可参考 README 的 xt/gui 模式示例（[README.md](file:///d:/Quantitative_Trading/README.md#L321-L341)）

## 盘前“快速验证命令”（你可以手动执行）

以下命令只用于快速判断“环境与关键数据是否齐备”，不改代码：

- 验证能否导入关键依赖（xt 模式必做）
  - `python -c "import xtquant; from xtquant import xtdata; print('xtquant ok')"`
- 验证 integration 文件是否存在（按你当天 watchlist 替换 code6）
  - `python -c "from pathlib import Path; p=Path('output/integration/chip'); print(list(p.glob('batch_results_*.csv'))[:3])"`
  - `python -c "from pathlib import Path; p=Path('output/integration/finintel'); print(list(p.glob('sentiment_*_*.json'))[:3])"`

## 风险点与故障处置（开盘前最常见）

- **chip/sentiment 缺失**：系统会降级为默认因子但仍可能继续跑；建议宁可推迟启动也先补齐 T-1 产出（否则候选质量与风控阈值失真）
- **xtquant 不可用或未登录**：属于硬阻断（行情/交易不可用）；优先检查客户端登录、权限、Python 环境是否指向同一套 xtquant
- **GUI prepare 失败**：程序可能继续运行但下单不可靠；开盘前必须确认 easytrader 能正常驱动券商客户端
- **状态文件不可写**：会导致无法恢复、盘中风控/锁状态无法持久化；开盘前先修复权限或切换 `--state-path`

## 交付物（本计划会在你确认后输出到对话里）

- 一份可直接照做的“09:30 前操作清单”（含最少命令与检查点）
- 一张文字版流程图：哪些模块产出什么文件、交易主循环如何消费、失败时如何降级
- 一份“报警关键词速查”：开盘前/盘中出现哪些日志关键字应立即处理（基于 warn_once 与 logger 输出路径整理）

