# ETF 筹码分布计算引擎（etf_chip_engine）

本目录提供一套独立的“行业/主题 ETF 筹码分布计算引擎”，数据源为 XtQuant（`xtquant.xtdata`），并按你的《ETF筹码分布计算_执行策略_优化后.md》实现了 IOPV + 三约束 MaxEnt + ETF 换手衰减 + 申赎修正 + 日终扩散与指标输出。

## 依赖与前置条件

- 运行环境：MiniQMT / XtQuant 可用且已登录
- Python 依赖：numpy / pandas / scipy（扩散与峰值检测依赖 SciPy）
- 份额/成交量口径：
  - tick 的 `volume` 单位为“股/份”
  - tick 的 `amount/volume` 为日内累计，需要做差分得到窗口增量

## 输出指标

- 获利盘比例 `profit_ratio`：0~100（百分比）
- 筹码密集区 `dense_zones`：若干价格点（support/resistance）
- ASR `asr`：0~1（仅在“日批量模式”里有 ATR 时输出）

## 三种模式（怎么用）

### 1) 冷启动（首次无历史筹码快照）

当找不到“上一交易日的筹码快照（.npz）”时，会自动触发冷启动：

- 数据源：最近 N 天（日线 OHLCV，默认 60）  
- 方法：三角分布法 + 指数衰减初始化筹码分布

对应实现：
- 冷启动算法：[cold_start.py](file:///d:/Quantitative_Trading/etf_chip_engine/cold_start.py)
- 引擎入口：[ETFChipEngine.cold_start](file:///d:/Quantitative_Trading/etf_chip_engine/engine.py)

### 2) 每日批量（收盘后跑全市场/部分 ETF）

适合每天 15:30 后运行：加载昨日筹码分布 → 拉取并处理当日 tick 历史 → 做申赎修正（T+1 份额）→ 日终扩散 → 保存今日筹码快照 → 输出指标。

命令行运行（请确保在项目根目录 `d:\Quantitative_Trading` 下执行）：

```bash
# PowerShell 示例：
#   cd /d D:\Quantitative_Trading
python -m etf_chip_engine.daily_batch --date auto
```

最简单的调用方式是使用 service 门面：

```python
from etf_chip_engine.service import IndustryETFChipService

svc = IndustryETFChipService()
df = svc.run_daily("20260212", limit=20)  # limit 可选：只跑前 20 只
print(df.head())
```

对应实现：
- 门面入口：[service.py](file:///d:/Quantitative_Trading/etf_chip_engine/service.py)
- 日批量主逻辑：[ETFChipEngine.process_daily](file:///d:/Quantitative_Trading/etf_chip_engine/engine.py)

落盘位置（默认在包内 data 目录）：
- L1/tick 快照：当前代码未强制按日落 parquet（后续若要重算可加）
- 筹码快照：`etf_chip_engine/data/chip_snapshots/{ETF}_{YYYYMMDD}.npz`

### 3) 盘中在线（实时模式，单只 ETF）

适合交易时段内对单只 ETF 做实时更新：订阅 tick → 每次有增量就更新筹码分布与指标（不做日终扩散、也不做申赎修正）。

命令行运行：

```bash
python -m etf_chip_engine.realtime --etf 560780.SH --seconds 12 --min-ticks 10 --top-components 50
```

参数说明：
- `--seconds`：最多等待多久收到足够 tick
- `--min-ticks`：至少处理多少个“增量快照”才退出
- `--top-components`：用于 IOPV 的成分股数量上限（从 xtdata.get_etf_info 返回的篮子里取 componentVolume 最大的前 N）

在线模式的“可用性注意”：
- 如果当前时段 `get_full_tick` 的 `amount/volume` 不增长（无成交/非交易时段），订阅回调不会产生“增量快照”。为便于验证，代码会自动切换 `mode=replay_market_data_tick`：用 `xtdata.get_market_data(period='tick')` 拉取最近若干条 tick 来回放更新流程。

对应实现：
- 在线入口：[realtime.py](file:///d:/Quantitative_Trading/etf_chip_engine/realtime.py)

## 核心模块对应关系

- IOPV： [modules/iopv_calculator.py](file:///d:/Quantitative_Trading/etf_chip_engine/modules/iopv_calculator.py)
- 三约束 MaxEnt： [modules/maxent_solver.py](file:///d:/Quantitative_Trading/etf_chip_engine/modules/maxent_solver.py)
- ETF 换手衰减： [modules/turnover_model.py](file:///d:/Quantitative_Trading/etf_chip_engine/modules/turnover_model.py)
- 申赎修正： [modules/redemption.py](file:///d:/Quantitative_Trading/etf_chip_engine/modules/redemption.py)
- 日终扩散： [modules/diffusion.py](file:///d:/Quantitative_Trading/etf_chip_engine/modules/diffusion.py)
- 指标输出： [modules/indicators.py](file:///d:/Quantitative_Trading/etf_chip_engine/modules/indicators.py)
- XtQuant 数据封装： [data/xtdata_provider.py](file:///d:/Quantitative_Trading/etf_chip_engine/data/xtdata_provider.py)
- tick 差分适配： [data/tick_adapter.py](file:///d:/Quantitative_Trading/etf_chip_engine/data/tick_adapter.py)

## 常见问题排查

- 在线模式返回 `etf_ticks=0`
  - 常见原因：非交易时段/该 ETF 无成交导致 `amount/volume` 不增长
  - 建议：交易时段运行；或观察输出里的 `mode` 是否已切到 `replay_market_data_tick`
- 运行时报 “xtdata 不可用”
  - 必须在 MiniQMT/XtQuant 环境里运行（确保已登录）
- 运行时报 “缺少 scipy”
  - 安装 scipy 后重试；扩散与峰值检测依赖 SciPy


## Daily Batch Download + Auto Date Rule (2026-02)

- `python -m etf_chip_engine.daily_batch --date auto` uses local-time switching:
- Trading day before `15:30` => `T-1`
- Trading day at/after `15:30` => `T`
- Non-trading day => latest trading day
- Daily batch pre-downloads tick data before running per-ETF chip computation.
- Same-day re-runs are deduplicated with a local state file:
- `output/cache/chip_tick_download/tick_YYYYMMDD.json`
- If a symbol still has empty tick data, the job auto-retries download once per code per day.
- Optional full refresh: `python -m etf_chip_engine.daily_batch --date auto --force-download`
- Default retention cleanup keeps only last `365` days of dated files.
- Cleanup scope includes:
- `etf_chip_engine/data/l1_snapshots/<YYYYMMDD>/`
- `etf_chip_engine/data/chip_snapshots/*_<YYYYMMDD>.npz|.ema.json`
- `etf_chip_engine/data/batch_results_*.csv`
- `output/integration/chip/batch_results_*.csv`
- `output/cache/chip_tick_download/tick_YYYYMMDD.json`
- `xtdata` local `datadir` dated `.dat` files (`SH/` + `SZ/`)
- Custom retention:
- `python -m etf_chip_engine.daily_batch --date auto --retention-days 365`
