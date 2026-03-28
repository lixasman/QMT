## Backtest Module (isolated from live trading)

This folder adds a standalone backtest pipeline and does not modify the live strategy flow in `main.py`.

### What it does

- Reuses your existing strategy logic (`StrategyRunner`) through isolated adapters.
- Uses `tick` rows as intraday snapshots and `1d` bars for Phase2/exit features.
- Replaces sentiment with proxy:
  - bullish day (`close[t-1] > close[t-2]`) and volume expansion (`vol[t-1] > MA5(vol[t-6:t-2])`) -> `100 / 0.9`
  - otherwise -> `0 / 0.1`
- Applies commission only (`0.85 bps` by default), no slippage, immediate fills.
- Defaults to `T+1` sellable logic (`--enable-t0` to allow same-day sell).
- Default disables intraday `execute_t0_live` path in backtest (`--enable-t0-exec` to turn it on explicitly).

### Data layout

Put CSV files under:

- `backtest/data/1d/`
- `backtest/data/tick/`

Filename examples:

- `512480_SH.csv`
- `159363_SZ.csv`

Required CSV columns (case-insensitive):

- `1d`: `time` (or `datetime`/`date`), `open`, `high`, `low`, `close`, `volume`, `amount`
- `tick`: `time`, `lastPrice` (or `close`/`price`), `volume`, `amount`

Optional tick fields (recommended):

- `bidPrice1`, `bidVol1`, `askPrice1`, `askVol1`, `iopv`, `stockStatus`

Notes:

- Tick rows outside trading sessions are ignored automatically.
- Existing integration factors are still loaded from `output/integration/chip/batch_results_YYYYMMDD.csv`.
- Backtest has a preflight check for chip-factor coverage before start date; by default, missing coverage raises an error.

### Export from QMT xtdata

```bash
python -m backtest.export_xtdata ^
  --codes "512480.SH,159363.SZ" ^
  --start 20240101 ^
  --end 20260301 ^
  --periods 1d,tick ^
  --out-root backtest/data
```

### Run backtest

> Important: current local `backtest/data/tick` coverage is treated as reliable from `2025-03-01`.
> If start date is earlier than `2025-03-01`, run may be under-traded because of missing tick rows.

```bash
python -m backtest.main ^
  --start 20250101 ^
  --end 20260301 ^
  --data-root backtest/data ^
  --out-dir output/backtest ^
  --initial-cash 300000 ^
  --fee-bps 0.85

# Force run even if chip integration files are missing/incomplete
python -m backtest.main --start 20250101 --end 20260301 --allow-missing-chip
```

With your 50-ETF universe, omit `--codes` and it uses the built-in list.

### Position sizing by account cash

Backtest now supports:

- `--position-sizing-cash <account_cash>`

This derives the three sizing fields from the `400000` baseline and overrides manual values:

- `position-slot-cap`
- `position-risk-budget-min`
- `position-risk-budget-max`

Formula:

```text
scale = account_cash / 400000
position-slot-cap = 70000 * scale
position-risk-budget-min = 2500 * scale
position-risk-budget-max = 6000 * scale
```

Examples:

- `--position-sizing-cash 50000` -> `8750 / 312.5 / 750`
- `--position-sizing-cash 200000` -> `35000 / 1250 / 3000`

### Recommended best-strategy template

Current recommended backtest template uses:

- `no_reentry_after_confirm`
- `all_signals / 60d / 15%` high-chase block
- `Layer2 threshold = 0.7`
- `ATR clamp = [2.5%, 4.0%]`
- `profit accel stop`
- backtest-only pricefix: `--exit-layer1-use-stop-price --buy-use-ask1`

5 万账户示例：

```bash
python -m backtest.main ^
  --start 20240101 ^
  --end 20241231 ^
  --data-root backtest/data ^
  --out-dir output/<run_name> ^
  --no-watch-auto ^
  --allow-missing-chip ^
  --initial-cash 50000 ^
  --position-sizing-cash 50000 ^
  --light-logs ^
  --bt-no-reentry-after-confirm ^
  --bt-skip-high-chase-after-first-signal ^
  --bt-high-chase-signal-source all_signals ^
  --bt-high-chase-lookback-days 60 ^
  --bt-high-chase-max-rise 0.15 ^
  --exit-layer2-threshold 0.7 ^
  --exit-atr-pct-min 0.025 ^
  --exit-atr-pct-max 0.04 ^
  --exit-k-accel ^
  --exit-k-accel-step-pct 0.05 ^
  --exit-k-accel-step-k 0.2 ^
  --exit-k-accel-kmin 1.0 ^
  --exit-layer1-use-stop-price ^
  --buy-use-ask1
```

20 万账户只需改两处：

```bash
--initial-cash 200000 --position-sizing-cash 200000
```

### Run Single-ETF Batch

This mode runs one isolated backtest per ETF and aggregates long-term single-name performance.

```bash
python -m backtest.single_etf_batch ^
  --start 20250304 ^
  --end 20260301 ^
  --out-dir output/backtest_single_etf ^
  --allow-missing-chip
```

Key outputs:

- `output/backtest_single_etf/single_etf_summary.csv`
- `output/backtest_single_etf/single_etf_summary.json`
- `output/backtest_single_etf/single_etf_overview.json`
- `output/backtest_single_etf/per_etf/<CODE>/summary.json`

Default behavior uses a static single-code watchlist for each ETF run so results reflect standalone strategy behavior on that ETF, not cross-ETF slot competition.

### Output files

- `output/backtest/summary.json`
- `output/backtest/daily_equity.csv`
- `output/backtest/fills.csv`
- `output/backtest/logs/*.jsonl` (entry/exit/position/t0 decision logs)
- `output/backtest/logs/backtest_run_<YYYYMMDD_HHMMSS>.log`
