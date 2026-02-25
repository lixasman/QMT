# 🔴 Red Team Audit Report: Premarket Prep System

**审计时间**: 2026-02-24  
**审计范围**: `premarket_prep.py`, `daily_batch.py`, `strategy_config.py`, `strategy_runner.py` 及交叉依赖  
**审计立场**: 不信任作者意图，以实盘损失为最终标尺

---

## 🏆 最危险的 3 个风险点（Executive Summary）

| # | 风险 | 严重度 | 核心问题 |
|---|------|--------|---------|
| 1 | **全面降级后静默开盘** | P0 | chip 失败 + hot 失败 + sentiment 全部失败时，系统以 `sentiment=50, profit_ratio=0.0` 的"中性"假数据继续交易，既无 READY/NOT READY 硬信号，也无自动降级到"只管持仓"模式 |
| 2 | **T-1 日期双源不一致** | P0 | `premarket_prep.prev_trading_date()` 使用 `time_utils.get_trading_dates`（可能 fallback 到工作日历），而 `daily_batch.run_daily_batch()` 内部调 `xtdata_provider.prev_trade_date()`（强依赖 xtdata）。两者算出的 T-1 可能不同，导致 chip 文件写在"错误日期"，loader 选不到 |
| 3 | **`filter_watchlist` 硬过滤与 `watch_auto` 语义冲突** | P0 | `watch_auto` 模式下对 hot+手工 watchlist 执行 `filter_watchlist`，该函数以 `sentiment<60` 或 `profit_ratio<75` 直接剔除。如果 sentiment 降级为默认 50，所有标的都会被过滤掉，候选池为空，当日不会有任何新入场 |

---

## 📋 完整问题列表

### P0 — 可能导致误交易 / 重大损失

---

#### P0-1: 全面数据降级后系统静默进入盘中交易

**触发条件**: chip 计算失败 + hot CSV 生成失败 + 个股 sentiment 补齐全部失败（场景 E）  
**具体位置**: [strategy_runner.py `_build_watchlist`](file:///d:/Quantitative_Trading/strategy_runner.py#L317-L336) → fallback 分支 L322-L336  
**影响**:
- 所有标的 `sentiment_score=50`, `profit_ratio=0.0`
- 进入 `_pre_open` 后 `filter_watchlist` 会因 `sentiment<60` 全部过滤掉（反而是"安全"的副作用）；但如果**不开** `watch_auto`，这些假数据会直接作为 watchlist 交给 `EntryFSM`
- 更严重的是：**已有持仓**的 exit scoring 使用降级数据（`sentiment_score_01=0.5, profit_ratio=0.0, chip_engine_days=0`），可能触发错误的退出信号

**建议修复**:
1. 在 `_pre_open()` 末尾增加 readiness 检查：如果 chip 和 sentiment 同时缺失，设置 `self._safe_mode = True`
2. Safe mode 下：禁止新入场，现有持仓仅执行 hard stop（Layer1），跳过 Layer2 scoring
3. 必须输出 `READY` / `NOT READY` 的硬日志信号，格式如 `strategy PREMARKET_STATUS=NOT_READY chip=MISS sentiment=0/5`

---

#### P0-2: T-1 日期双源不一致导致 chip 文件日期错配

**触发条件**: xtdata 可用但与 `time_utils` 的日历提供者结果不同（例如 xtdata 尚未更新日历、或自定义 provider 被注入）  
**具体位置**:
- [premarket_prep.py `prev_trading_date`](file:///d:/Quantitative_Trading/integrations/premarket_prep.py#L18-L27) → 使用 `core.time_utils.get_trading_dates`
- [daily_batch.py `_resolve_trade_date`](file:///d:/Quantitative_Trading/etf_chip_engine/daily_batch.py#L15-L19) → 使用 `xtdata_provider.prev_trade_date`（强依赖 xtdata）

**影响**:
- `premarket_prep` 算出 T-1 = `20260220`，把这个日期传给 `run_daily_batch(trade_date="20260220")`
- 但 `run_daily_batch` 内部 `_resolve_trade_date("20260220")` **不会**再修改（因为不是 `"auto"`），所以这个场景下日期一致
- **真正危险的场景**：如果 `premarket_prep` 的 fallback 日历（工作日）算出 T-1 = 周五（20260220），但 20260220 实际是节假日（春节）。那么 chip 文件会写成 `batch_results_20260220.csv`，但 xtdata 环境的下一次独立 batch 可能写成不同日期，造成文件冲突

**建议修复**:
1. **统一日期源**：`premarket_prep` 和 `daily_batch` 应共用同一个 `prev_trading_date()` 函数
2. 最简方案：让 `premarket_prep.prev_trading_date()` 优先尝试 `xtdata_provider.prev_trade_date()`，fallback 到当前逻辑
3. 在 `ensure_tminus1_ready` 里增加断言：`run_daily_batch` 返回的 csv 文件名中的日期 == `t1`

---

#### P0-3: `filter_watchlist` + 降级 sentiment = 候选池清空

**触发条件**: `--watch-auto` 开启 + sentiment 全部降级为默认值 50  
**具体位置**: [strategy_runner.py L399-L402](file:///d:/Quantitative_Trading/strategy_runner.py#L399-L402) + [entry/watchlist.py `filter_watchlist`](file:///d:/Quantitative_Trading/entry/watchlist.py#L61-L89)  
**影响**:
- `filter_watchlist` 的硬门槛：`sentiment_score < 60` → 剔除
- 降级 sentiment = 50 → 全部被剔除 → `wl = []` → `EntryFSM.upsert_watchlist(watchlist=[])` → 当日无入场候选
- 这个行为在 sentiment 正常时是"安全的过滤"，但在降级时变成了"静默拒绝所有交易"，用户完全没有意识到

**建议修复**:
1. `filter_watchlist` 不应用默认降级值做过滤判断。增加参数 `skip_degraded=True`，当检测到 `sentiment_score == 50 and profit_ratio == 0.0` 时标记为降级，不参与过滤
2. 或者：在 `_pre_open` 中，如果 `filter_watchlist` 后 `wl` 为空但 filter 前 `wl` 非空，输出 WARNING 并保留原始 watchlist（加 `micro_caution=True`）

---

#### P0-4: `FININTEL_FAKE_TODAY` 环境变量污染跨模块

**触发条件**: `ensure_tminus1_ready` 中 `_fake_finintel_today(t1)` 的 `with` 块内抛出异常并被外层 `try/except` 捕获但未正确恢复  
**具体位置**: [premarket_prep.py L107-L143](file:///d:/Quantitative_Trading/integrations/premarket_prep.py#L107-L143)

**分析**:
- `_fake_finintel_today` 使用 `contextmanager`，`finally` 块会正确恢复。✅ 这个实现是正确的
- **但**：`finintel/etf_signal_pipeline.py` L625-L629 有自己的 `_today_yyyymmdd()` 也读 `FININTEL_FAKE_TODAY`。这意味着被 `with` 块包裹的 `fin_main()` 调用链中**所有**使用 `_today_yyyymmdd()` 的地方都会受到影响
- 这是**设计意图**，但也是**隐性耦合**：如果有人在 `with` 块内添加了不相关的逻辑（比如日志文件命名），也会被影响

**最终结论**: 当前实现在正常路径下是安全的（`contextmanager` 保证 `finally` 执行）。但全局 env var 是进程级共享的，如果未来引入多线程将彻底崩溃。

**建议修复**:
1. 短期：增加注释 `# WARNING: 此 env var 是进程级全局状态，不可在多线程中使用`
2. 中期：将 `day` 参数显式传递给 `fin_main()` 而非通过 env var 隐式传递（例如增加 `--fake-today` CLI 参数）

---

### P1 — 会导致错过交易或风控失效

---

#### P1-1: xtdata 不可用时的工作日 fallback 把节假日当交易日

**触发条件**: xtdata 模块不可用（`_xtdata = None`）+ 当前日期在春节/国庆长假后  
**具体位置**: [core/time_utils.py `_default_calendar_provider`](file:///d:/Quantitative_Trading/core/time_utils.py#L19-L39) L30-L38  
**影响**:
- fallback 逻辑只排除周末（`weekday() < 5`），不排除法定节假日
- 例如：2026-01-30（周五，春节假期）会被认为是交易日
- `prev_trading_date` 会返回一个不存在交易数据的节假日，file matching 失败

**回溯窗口 40 天是否足够**: 中国最长连续非交易日（春节 + 前后周末）约 9-10 天，40 天窗口绰绰有余 ✅

**建议修复**:
1. fallback 日历中嵌入硬编码的中国法定节假日表（至少覆盖当年和次年）
2. 或：在 `get_trading_dates` 返回空列表时输出 CRITICAL 日志并阻止交易

---

#### P1-2: `_pre_open` 中 `auto_prep` 无超时控制

**触发条件**: chip 全市场计算耗时 > 60 分钟（08:30 开始，卡到 09:30+）  
**具体位置**: [strategy_runner.py `_pre_open` L375-L394](file:///d:/Quantitative_Trading/strategy_runner.py#L375-L394)  
**影响**:
- `run_daily_batch` 是同步阻塞调用，全市场 ETF chip 计算可能需要 30-90 分钟
- 如果 08:30 启动，可能 09:40 才完成，错过开盘前 10 分钟的集合竞价信息
- `_pre_open` 后面的 `_intraday_loop` 不会被跳过（因为时间检查 `< 15:01`），但开盘后相当长时间没有 tick 处理

**建议修复**:
1. `auto_prep` 应设置硬超时（例如 `threading.Timer` 或 `signal.alarm`），到 09:20 必须中止
2. 如果超时中止，标记 `chip_ready=False` 并进入 safe mode
3. 或：将 chip batch 移到前一天收盘后的 cron job

---

#### P1-3: hot CSV `code` 列名硬编码依赖

**触发条件**: `select_top_hot_etfs()` 返回 DataFrame 的列名不是 `code`（例如改为 `etf_code` 或 `symbol`）  
**具体位置**:
- [premarket_prep.py L121](file:///d:/Quantitative_Trading/integrations/premarket_prep.py#L121)：`row.get("code")`
- [strategy_runner.py L261](file:///d:/Quantitative_Trading/strategy_runner.py#L261)：`row.get("code")`
- [finintel/main.py L262](file:///d:/Quantitative_Trading/finintel/main.py#L262)：`str(row["code"])`

**影响**: 如果列名变化，hot_codes 会是空列表，但不会报错（`row.get("code")` 返回 None → `str(None)` → 空字符串跳过）。候选池退化为仅手工 watchlist。

**建议修复**:
1. 在 `select_top_hot_etfs` 的返回值上增加 schema 断言：`assert "code" in df.columns`
2. hot CSV 读取时如果 `code` 列不存在，raise 异常而非静默降级

---

#### P1-4: 持仓代码后缀不一致导致 sentiment 补齐遗漏

**触发条件**: `state.positions` 中存储的 key 是 `159107.SZ`（带后缀），而 `_code6` 只提取前 6 位  
**具体位置**: [premarket_prep.py `_code6`](file:///d:/Quantitative_Trading/integrations/premarket_prep.py#L56-L60) + [strategy_runner.py L282-L287](file:///d:/Quantitative_Trading/strategy_runner.py#L282-L287)

**分析**:
- `_code6("159107.SZ")` → `"159107"` ✅ 这个是正确的
- `_code6("159107")` → `"159107"` ✅
- `_code6("512480.SH")` → `"512480"` ✅
- `_code6("")` → `""` → 跳过 ✅
- `_code6("SZ159107")` → `""` → 跳过（`.split(".", 1)[0]` = `"SZ159107"`，不是 6 位数字）⚠️ 但这种格式不太可能出现

**真正的问题**在 `strategy_runner._load_external_factors_for_watchlist` L282：
```python
for k in list(self._state.positions.keys()):
    s = str(k).strip()
```
如果 positions key 是 `"159107.SZ"`，它会被原样传给 `load_watchlist_items(etf_codes=[..., "159107.SZ"])`，然后经过 `normalize_etf_code` 和 `code6` 处理。这条路径是正确的。

但在 `premarket_prep.ensure_tminus1_ready` 中，`position_codes` 同样来自 `self._state.positions.keys()`，经过 `_code6` 提取。匹配是可以工作的。

**结论**: 当前代码对标准格式（`XXXXXX.SH/SZ` 或纯 6 位数字）是安全的，但对非标格式没有明确的错误报告。标记为 P1 而非 P0。

**建议修复**: 在 `_code6` 返回空时输出 warning，包含原始输入值

---

#### P1-5: chip 存在但 sentiment 不存在（或反之）时的候选池构建不一致

**触发条件**: 场景 A 的变体 — chip 就绪，但所有 sentiment 生成失败  
**具体位置**: [watchlist_loader.py `load_watchlist_items`](file:///d:/Quantitative_Trading/integrations/watchlist_loader.py#L127-L225)  
**影响**:
- chip 数据正常加载 → `profit_ratio` 有真实值（比如 85.0）
- sentiment 缺失 → 默认 `score100=50, score01=0.5`
- `filter_watchlist` 条件：`sentiment >= 60 AND profit_ratio >= 75`
- 结果：`profit_ratio=85.0 ≥ 75` ✅ 但 `sentiment=50 < 60` ❌ → 被过滤
- 这意味着哪怕 chip 信号非常好的标的也会因为 sentiment 降级被排除

**建议修复**: `filter_watchlist` 应区分"真实低分"和"降级默认值"。建议在 `WatchlistItem` 中增加 `sentiment_degraded: bool` 字段

---

### P2 — 影响可维护性 / 可观测性

---

#### P2-1: `premarket_prep` 中 `warn_once` import 但从未使用

**触发条件**: 始终  
**具体位置**: [premarket_prep.py L11](file:///d:/Quantitative_Trading/integrations/premarket_prep.py#L11)  
**分析**: grep 结果显示 `premarket_prep.py` 中没有任何 `warn_once` 调用。虽然代码中有使用 `warn_once` 形式的字符串 key（如 `"premarket_prev_trade_date_missing"`），但实际调用的是……等一下，让我重新检查。

实际检查：`premarket_prep.py` 第 85 行 `warn_once("premarket_prev_trade_date_missing", ...)` — 确实有使用。我之前的 grep 可能因为搜索路径问题没有找到。**此条作废**。

---

#### P2-2: `_write_signal_human_files` 使用 `_today_yyyymmdd()` 而非传入的 `day`

**触发条件**: `--signal-etf` 模式下，`_write_signal_human_files` 在 `_fake_finintel_today` 的 `with` 块**外部**被调用  
**具体位置**: [finintel/main.py L117-L137](file:///d:/Quantitative_Trading/finintel/main.py#L117-L137) vs [L357-L358](file:///d:/Quantitative_Trading/finintel/main.py#L357-L358)

**分析**:
- `main()` 中 `--signal-etf` 分支：L307-L316 调用 pipeline，L344-L356 写文件，L357-L358 调用 `_write_signal_human_files`
- 这些操作在 `premarket_prep` 的 `with _fake_finintel_today(t1)` 块**内部**执行（因为 `fin_main` 被 `with` 块包裹调用）
- 所以 `_today_yyyymmdd()` 会返回 fake 日期 ✅
- 但 `_write_signal_human_files` 内部 L120 `day = _today_yyyymmdd()` 依赖的是 env var 仍然生效。如果 `_fake_finintel_today` 的 `with` 块异常退出后再调 human files 就会用真实日期

**影响**: 轻微，因为当前流程中 `_write_signal_human_files` 总是在 `with` 块内被调用。但这是一个脆弱设计。

**建议修复**: `_write_signal_human_files` 应接受 `day` 参数而非自行调用 `_today_yyyymmdd()`

---

#### P2-3: `run_daily_batch` 的 integration 写入异常被静默吞没

**触发条件**: integration 目录写入失败（磁盘满、权限问题等）  
**具体位置**: [daily_batch.py L163-L175](file:///d:/Quantitative_Trading/etf_chip_engine/daily_batch.py#L163-L175)

```python
try:
    ...
    df2.to_csv(p2, index=False, encoding="utf-8-sig")
except Exception:
    pass  # ← 完全静默
```

**影响**: batch CSV 写成功但 integration CSV 写入失败，`premarket_prep` 检查 `chip_integration_path` 时发现不存在，然后再跑一次 `run_daily_batch` — 但这次主 CSV 已存在不会重复计算……等等，`run_daily_batch` 不检查已存在，每次都重新计算。所以会重跑全市场，浪费时间但不会出错。

**建议修复**: `except Exception: pass` 改为 `except Exception as e: warn_once(...)`

---

#### P2-4: 双重 `_today_yyyymmdd` 定义

**触发条件**: 始终  
**具体位置**:
- `finintel/main.py` L31-L35
- `finintel/etf_signal_pipeline.py` L625-L629
- `integrations/watchlist_loader.py` L37-L39（不读 FAKE env var）
- `integrations/premarket_prep.py` L14-L15（不读 FAKE env var）

**影响**: 4 个不同的 "today" 函数，行为不完全一致。维护风险高。

**建议修复**: 统一为 `core/time_utils.py` 中的单一函数，接受可选的 `override` 参数

---

#### P2-5: 测试覆盖不足

**触发条件**: 始终  
**具体位置**: [test_premarket_prep.py](file:///d:/Quantitative_Trading/tests/test_integrations/test_premarket_prep.py)

**已覆盖**:
- ✅ `prev_trading_date` 基础功能
- ✅ `ensure_tminus1_ready` 的 happy path（chip miss → 补齐，hot miss → 补齐，signal miss → 补齐）

**明显未覆盖**:
- ❌ chip 补齐失败后的降级路径
- ❌ hot CSV 补齐失败后的降级路径
- ❌ 个股 signal 部分失败的场景
- ❌ `FININTEL_FAKE_TODAY` 环境变量恢复测试
- ❌ `_code6` 的边界值（空字符串、非标格式）
- ❌ `_resolve_watch_codes` 的 hot CSV 列名异常
- ❌ `filter_watchlist` 在全降级 sentiment 下的行为
- ❌ `_pre_open` 在 `auto_prep` 超时情况下的行为

---

### P3 — 代码风格 / 小问题

---

#### P3-1: `premarket_prep.py` 中重复 import

**位置**: L108 `from finintel.main import main as fin_main` 和 L139 同样的 import  
**建议**: 放到函数顶部一次性 import

#### P3-2: `hot_top` 的类型转换冗余

**位置**: premarket_prep.py L107 `int(hot_top)` — 参数已经声明为 `int`  
**建议**: 直接使用 `hot_top`

---

## 📌 强制检查清单逐条结论

### 1. 日期与交易日逻辑

| 检查项 | 结论 |
|--------|------|
| T-1 在周一/节假日后是否正确 | ⚠️ xtdata 可用时正确；fallback 到工作日历时，节假日后的第一个工作日会错误地将节假日当作交易日 |
| 工作日 fallback 风险 | ❌ 会把法定节假日当作交易日 → T-1 选错 |
| 40 天回溯窗口 | ✅ 足够，中国最长连续非交易日 < 15 天 |

### 2. 文件选择逻辑一致性

| 检查项 | 结论 |
|--------|------|
| 盘前补齐文件日期 < today | ✅ `ensure_tminus1_ready` 写入日期 = T-1 < today |
| auto-prep 路径 vs loader 路径 | ✅ 两者都使用 `output/integration/chip/batch_results_{date}.csv` 和 `output/integration/finintel/sentiment_{code6}_{day}.json` |
| hot csv vs sentiment date 一致性 | ✅ 两者均使用 `t1`（同一个变量），在 `_fake_finintel_today(t1)` 块内 |

### 3. 副作用与可预期性

| 检查项 | 结论 |
|--------|------|
| FININTEL_FAKE_TODAY 污染 | ⚠️ contextmanager 保证恢复，但全局 env var 不线程安全 |
| auto-prep 卡住系统行为 | ❌ 无超时控制，可能阻塞到 09:30+ |

### 4. 失败降级路径安全性

| 检查项 | 结论 |
|--------|------|
| 默认值是否安全 | ❌ `sentiment=50` 不安全：在 `watch_auto` 模式下会清空候选池；在非 `watch_auto` 模式下会以假数据参与评分 |
| chip 存在 / sentiment 缺失 | ❌ 会因 sentiment 门槛被过滤而错过有价值的标的 |

### 5. 候选池来源与阈值逻辑

| 检查项 | 结论 |
|--------|------|
| watch_auto 误伤观察标的 | ✅ `filter_watchlist` 确实会过滤所有标的（包括观察但不交易的），但此逻辑的设计意图就是"只保留值得入场的" |
| micro_caution 软门控 vs 硬过滤 | ⚠️ `filter_watchlist` 不直接剔除 `micro_caution=True` 的标的，只是重新标记。但 `sentiment<60` 和 `profit_ratio<75` 是硬门槛。两者语义不同，但不矛盾 |

### 6. 持仓补齐逻辑

| 检查项 | 结论 |
|--------|------|
| 补齐范围完整性 | ✅ 补齐 = 持仓 + watch_codes + hot_codes 的 sentiment |
| code6 提取一致性 | ✅ 对标准格式一致，非标格式会静默跳过（有 P1 风险但不致命） |

### 7. 性能与资源风险

| 检查项 | 结论 |
|--------|------|
| chip 全市场计算耗时 | ❌ 无超时兜底。08:30 启动可能无法在 09:30 前完成 |
| finintel 串行调用超时 | ⚠️ hot top 15 + 若干 signal 串行调用，每次 DeepSeek API 请求 10-30s，总计 5-10 分钟可能不够 |
| 缓存命中（已存在就跳过） | ✅ `finintel/main.py` L266 检查 `out_json.exists()` 并跳过。但注意：跳过的是 `output/` 下的 json，不是 integration json。如果 integration json 缺失但 output json 存在，不会重新生成 integration json |

### 8. 可测试性与回归风险

| 检查项 | 结论 |
|--------|------|
| 测试覆盖危险路径 | ❌ 仅覆盖 happy path，数据降级路径无测试 |
| 隐性行为变化 | ⚠️ `--watch-auto` 改变了 `_pre_open` 的行为（增加 `filter_watchlist`），但该分支无单测 |

### 9. 实盘安全性

| 检查项 | 结论 |
|--------|------|
| 部分失败时继续交易是否安全 | ❌ 不安全。系统没有安全模式，会以降级数据继续交易 |
| 是否应有 READY/NOT READY 信号 | ✅ 强烈建议 |

---

## 🎯 对抗场景分析

### 场景 A：周一 08:30 启动，T-1 是上周五；hot CSV 存在但 chip integration 不存在

**执行路径**:
1. `prev_trading_date(now=周一 08:30)` → 返回上周五日期（如 `20260220`）
2. `chip_integration_path(trade_date="20260220")` → `output/integration/chip/batch_results_20260220.csv` → 不存在
3. 触发 `run_daily_batch(trade_date="20260220")` → 需要 xtdata 可用
4. 如果 xtdata 可用：全市场计算，耗时可能 30-90 分钟 ⚠️
5. hot CSV 已存在 → 跳过生成，直接读取 hot codes
6. 逐个检查 sentiment → 缺失的补齐

**风险**: 主要风险是 chip 计算耗时导致错过开盘。hot CSV 路径正确，此场景下不会有数据错误。

**结果**: **P1 风险** — 时间风险而非数据风险

---

### 场景 B：T-1 chip 存在但文件日期是"今天"（误生成），loader 选不到

**执行路径**:
1. 假设有人手动跑了 `daily_batch --date auto`，在盘前生成了 `batch_results_20260224.csv`
2. `watchlist_loader._pick_latest_before` 条件：`d >= today → continue`
3. 今天的文件被跳过 ✅ 这是设计意图
4. 但：如果除了今天的文件，没有其他文件，chip 数据就会**完全缺失**
5. `premarket_prep` 会检测到 chip 缺失并重新生成 T-1 的 chip → 问题修复

**风险**: `auto_prep` 能正确修复此场景。但如果 `auto_prep` 未开启，用户不会知道 chip 被降级。

**结果**: **P2 风险**（有 auto_prep 时自愈，无 auto_prep 时静默降级）

---

### 场景 C：持仓里有 `159107`（无后缀），热门 Top15 不包含它

**执行路径**:
1. `ensure_tminus1_ready(position_codes=["159107"])` 
2. `extra_codes` 构建：`{"159107"}`
3. `_code6("159107")` → `"159107"` ✅
4. 检查 `finintel_integration_path(code6="159107", day=t1)` → `sentiment_159107_{t1}.json`
5. 如果不存在 → 加入 `need_extra` → 调用 `fin_main(["--signal-etf", "159107", "--no-trace"])`
6. `finintel/main.py` `normalize_code("159107")` → `"159107.SZ"` → `etf6 = "159107"`
7. 在 `_fake_finintel_today(t1)` 下 → `day = t1` → 写入 `sentiment_159107_{t1}.json`

**结果**: ✅ **正确补齐**。`_code6` 和 `normalize_code` 的组合能正确处理无后缀的 6 位代码。

---

### 场景 D：xtdata 不可用，无法获取交易日历 / 无法跑 chip

**执行路径**:
1. `prev_trading_date()` → `get_trading_dates()` → fallback 到工作日历
2. 如果今天不是节假日后的第一个工作日 → T-1 可能正确
3. `run_daily_batch()` → 调用 `require_xtdata()` → **抛出 RuntimeError** ❌
4. 被 `ensure_tminus1_ready` 的 `except Exception` 捕获 → `chip_ok = False`，继续
5. `_fake_finintel_today(t1)` 下的 finintel 调用 — finintel 本身不依赖 xtdata，可以正常运行 ✅
6. 但：`strategy_runner._build_data_adapter()` 构建 `XtDataAdapter`，如果 xtdata 不可用，后续 `get_bars`/`get_snapshot` 全部失败
7. `run_day()` → `_pre_open()` → `_intraday_loop()` → 每个 tick 的 `get_snapshot` 都失败 → 所有持仓无法处理

**结果**: ❌ **P0 风险** — 系统不会崩溃但会进入"僵尸状态"（看起来在运行，实际什么都不做）。应在启动时检查 xtdata 可用性并 fail-fast。

> 但这不属于此次审计范围内的新增代码问题，而是既有的架构风险。

---

### 场景 E：DeepSeek key 缺失 / 接口超时，hot 失败 + 单只补齐也失败

**执行路径**:
1. `fin_main(["--signal-hot-top", "15"])` → `DeepSeekClient.from_env(session)` → key 缺失 → 抛异常
2. 被 `ensure_tminus1_ready` L109-L110 的 `except` 捕获 → hot CSV 不存在 → `hot_codes = []`
3. `need_extra` = watch_codes + position_codes 中 sentiment 缺失的
4. 逐个调 `fin_main(["--signal-etf", c6])` → 同样因为 key 缺失抛异常
5. 全部 sentiment 补齐失败 → `sentiment_ready_codes = ()`
6. `_build_watchlist()` → `load_watchlist_items()` → sentiment 全部降级为 `score100=50`
7. `watch_auto` 开启 → `filter_watchlist()` → `sentiment < 60` → **全部剔除** → `wl = []`
8. `EntryFSM.upsert_watchlist(watchlist=[])` → 当日无入场

**结果**: ❌ **P0 风险** — 系统在 09:30 开盘时 watchlist 为空，不会有任何新入场。**但更危险的是**：已有持仓的 exit scoring 使用降级 sentiment（`sentiment_score_01=0.5`），`compute_s_sentiment(0.5)` 可能得到中性分数，削弱退出信号的灵敏度。

---

### 场景 F：hot CSV 存在但内容字段不是 `code`（列名变化）

**执行路径**:
1. `hot_csv.exists()` → True
2. `csv.DictReader` 读取 → `row.get("code")` → 返回 `None`（列名已变）
3. `c = str(None).strip()` = `"None"` → 非空！→ 被加入 `hot_codes` ✅ **这是一个 bug**

**等等**，让我重新检查：
```python
c = str(row.get("code") or "").strip()
```
`row.get("code")` 返回 `None` → `None or ""` → `""` → `c = ""` → 空字符串被跳过 ✅

**正确分析**: 如果列名变化，`hot_codes = []`，候选池退化为仅手工 watchlist。**不会**加入垃圾数据，但 topN 热门标的全部丢失。

**结果**: **P1 风险** — 静默退化。应在读取 CSV 后检查 `len(hot_codes) == 0` 并输出 WARNING。

---

## 📊 最终建议

### 是否建议实盘默认开启 `--auto-prep --watch-auto`？

> **❌ 不建议当前版本直接开启。**

### 上线前还差什么：

| # | 必要改进 | 预估工作量 |
|---|---------|-----------|
| 1 | **增加 Readiness Gate**：`_pre_open` 结束后输出明确的 `PREMARKET_STATUS=READY/DEGRADED/FAILED` 信号，并在 `FAILED` 时自动进入 safe mode（禁止新入场） | 1-2h |
| 2 | **统一 T-1 日期源**：`premarket_prep.prev_trading_date` 和 `daily_batch._resolve_trade_date` 共用同一实现 | 0.5h |
| 3 | **`filter_watchlist` 降级感知**：当 sentiment 为默认值 50 时跳过 sentiment 门槛过滤，或在 `WatchlistItem` 中标记 `is_degraded` | 1h |
| 4 | **`auto_prep` 超时控制**：chip 计算设置 09:20 硬截止，超时标记为 chip_miss 并继续 | 1h |
| 5 | **integration CSV 写入失败增加日志**：`daily_batch.py` 的 `except Exception: pass` 改为有日志输出 | 0.1h |
| 6 | **增加降级路径的单元测试**：至少覆盖 chip 失败、hot 失败、全 sentiment 失败三种组合 | 2h |
| 7 | **finintel 缓存逻辑修复**：output json 存在但 integration json 缺失时应补写 integration json | 1h |

**总计约 7-8h 工作量，在此之后可以建议开启 `--auto-prep --watch-auto` 进行纸上交易（paper trading）验证 2-3 天，确认无异常后再切换到实盘。**
