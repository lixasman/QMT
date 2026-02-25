# T+0 日内做T策略 — 开发提示词

> **角色**：你是一位严谨的量化系统工程师。你的任务是根据 T+0 策略规格书和验证套件，在已有 `core/` 合约层之上实现 `t0/` 模块。
> **核心原则**：宁可不做也不滥做 

---

## 1. 你必须先读完的文档（按优先级排列）

1. `.trae/quantitative strategy/t0_strategy_specification.md` v1.4（1069 行）— 完整策略规格
2. `.trae/quantitative strategy/t0_implementation_verification.md` v1.0（595 行）— 编码合约 + 25 个验收场景 + 运行时断言
3. `core/interfaces.py` — `DataAdapter`、`TradingAdapter` 抽象接口
4. `core/models.py` — `PositionState`、`PortfolioState`、`T0TradeRecord`、`PendingSell` 数据模型
5. `core/enums.py` — `FSMState`、`OrderSide`、`OrderType`、`OrderStatus`、`OrderTimeInForce`
6. `core/cash_manager.py` — `CashManager`（T0_SCALP 优先级 = 4，最低）
7. `core/price_utils.py` — `round_to_tick`、`tick_ceil`、`tick_floor`、`clamp_to_limits`、`align_order_price`
8. `core/time_utils.py` — `is_trading_time`、`next_trading_day`
9. `core/constants.py` — `TICK_SIZE = 0.001`、`T0_REGIME_DECISION_TIME = time(9, 26)`

---

## 2. 模块结构 & 文件清单

```
t0/
├── __init__.py          # 包初始化 + __all__
├── constants.py         # 全部硬编码常量
├── types.py             # 数据类型定义（frozen dataclass）
│
├── regime.py            # §2 Regime 日级使能 — 09:26 计算一次
├── vwap_engine.py       # §3 VWAP σ-带信号引擎 — 增量计算 + sigma_floor
├── kde_support.py       # §4 KDE 筹码支撑位读取 + VWAP 协作
├── iopv_premium.py      # §5 IOPV 溢价率置信度标签
├── signal_engine.py     # 三路信号聚合 + k 值动态调整
│
├── time_window.py       # §8 时间窗口硬约束 + close-only 判定
├── order_manager.py     # §7 预埋限价单管理 + 部分成交处理
├── reconciliation.py    # §7.5 超时对账协议（10s + 3 CASE）
│
├── breaker.py           # §9 四层熔断 + 极端行情冻结
├── sweeper.py           # §8.3 14:15 / 14:55 清道夫
├── t0_logger.py         # 6 种 JSONL 日志
├── t0_fsm.py            # 主编排器 — 继承 position_management §7 接口
```

---

## 3. 分阶段开发顺序

### 阶段 1 — 离线管线 + 日级判定（无实时依赖，可独立测试）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `constants.py` | 全部硬编码常量（k_buy/k_sell 默认值、sigma 窗口、时间窗口、熔断阈值、GUI 配额等） | §10 参数表 | — |
| `types.py` | `RegimeResult`, `VwapSnapshot`, `T0Signal`, `RoundTripResult`, `BreakerDecision`, `ReconciliationResult` | 验证 §5.3 | — |
| `regime.py` | `compute_regime(auction_vol_ratio, atr5_percentile) → RegimeResult` | §2 | 1-4 |
| `kde_support.py` | `load_kde_zones(etf_code, date) → list[DenseZone]`；`find_nearest_support(zones, price) → float|None` | §4 | — |

→ **验收**：场景 1-4 全部通过

### 阶段 2 — 盘中信号核心（依赖 L1 引擎）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `vwap_engine.py` | 增量 VWAP 计算 + sigma 滚动 60 快照 + sigma_floor 保护 + 偏差方向判定 | §3.2-3.6 | 5-8 |
| `iopv_premium.py` | `compute_iopv_confidence(price, iopv) → "HIGH"|"NORMAL"` | §5 | — |
| `signal_engine.py` | 聚合 VWAP + KDE + IOPV → `T0Signal`；k 值动态调整（trend_state） | §3.3-3.5, §4.3 | 5-10 |
| `time_window.py` | `is_buy_allowed(now) → bool`；`is_sell_allowed(now) → bool`；`is_reverse_sell_allowed(now) → bool`；`is_close_only(now) → bool` | §8 | 11-16 |

→ **验收**：场景 5-16 全部通过

### 阶段 3 — 订单管理 + 风控（依赖前两阶段）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `order_manager.py` | 预埋限价单 CRUD + GUI 配额计数 + 部分成交处理(3分钟容忍) + 每日 RT≤1 强制 | §7 | — |
| `reconciliation.py` | 10s 超时 → 冻结 → 强制对账（CASE A/B/C）→ 解冻/人工 | §7.5 | 22-24 |
| `breaker.py` | Layer 5(日)+ 7(周)+ 8(月)+ 9(连续亏损)+ 极端行情冻结 | §9 | 17-21 |
| `sweeper.py` | 14:15 撤买单(含反T接回) + 14:55 撤残余 + 微型仓位处理 | §8.3 | 16 |

→ **验收**：场景 17-25 全部通过

### 阶段 4 — 日志 + 主编排

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `t0_logger.py` | 6 种 JSONL 日志格式（Regime/Signal/RT/Breaker/Reconciliation/每日审计） | 验证 §4 | — |
| `t0_fsm.py` | 主编排器：9 层门控链 + position_management §7 接口继承 + Mutex | 全文 | 全部 |
| `__init__.py` | `__all__` = `["T0Engine"]` | — | — |

---

## 4. 代码风格要求

遵循与 `exit/` 和 `position/` 模块完全一致的风格：

```python
# 1. 每个文件第一行
from __future__ import annotations

# 2. 类型注解：全部使用，包括返回值
def compute_regime(*, auction_vol_ratio: float, atr5_percentile: float) -> RegimeResult:

# 3. keyword-only 参数
def execute_t0_buy(*, etf_code: str, price: float, amount: float) -> Optional[int]:

# 4. 断言使用 AssertionError，不用 assert 语句（生产环境 -O 会跳过 assert）
if sigma < price * 0.0005:
    raise AssertionError(f"sigma {sigma} 低于 floor {price * 0.0005}")

# 5. 不可变数据类型使用 frozen=True
@dataclass(frozen=True)
class RegimeResult:
    regime_active: bool
    reason: str
    auction_vol_ratio: float
    atr5_percentile: float

# 6. 使用 core/ 的工具，不要自己实现
from core.price_utils import round_to_tick, clamp_to_limits, align_order_price
from core.time_utils import next_trading_day, is_trading_time

# 7. 常量全部从 constants.py 引入，不在逻辑代码中出现魔数

# 8. 日志格式严格遵循验证套件 Part 4 定义
```

---

## 5. 核心公式速查

### 5.1 Regime 日级使能（§2）

```python
# 09:26 计算一次，全天不变
regime_active = (
    auction_volume_ratio > 1.5     # 集合竞价量比
    OR
    atr5_percentile > 65            # ATR_5 在 60日滚动分位
)

auction_volume_ratio = today_auction_volume / MA(auction_volume, 10)
atr5_percentile = percentile_rank(ATR_5, window=60)
```

### 5.2 VWAP σ-带信号（§3）

```python
# 增量计算！！L1 快照的 volume 是累计量，必须差分
delta_vol = snapshot_volume - prev_snapshot_volume
delta_amount = snapshot_amount - prev_snapshot_amount
IF delta_vol <= 0 OR delta_amount <= 0:
    data_quality = STALE   # 跳过异常快照

cumulative_pv += delta_amount
cumulative_vol += delta_vol
vwap = cumulative_pv / cumulative_vol

# sigma = 价格与 VWAP 偏差的滚动标准差
deviations.append(price - vwap)
raw_sigma = std(deviations[-60:])          # 60 个快照 = 3 分钟
sigma_floor = price × 0.0005               # 最低 5 bps
sigma = max(raw_sigma, sigma_floor)

# 买入/卖出信号
buy_trigger  = vwap - k_buy × sigma
sell_trigger = vwap + k_sell × sigma

# 挂单价后处理（两步，顺序不可换）
step1: price = round_to_tick(price, tick_size=0.001)
step2: price = clamp(price, lower_limit, upper_limit)
```

### 5.3 k 值动态调整（§3.6）

```python
# 日内趋势方向判定
IF price > vwap AND kama_10 > kama_10[-60]:
    trend_state = "UP"
    k_buy, k_sell = 1.8, 2.8      # 上升趋势中回调更容易反弹

ELIF price < vwap AND kama_10 < kama_10[-60]:
    trend_state = "DOWN"
    k_buy, k_sell = 2.2, 2.6      # CAUTION: k_sell在下降趋势=2.6（非3.0）

ELSE:
    trend_state = "RANGE"
    k_buy, k_sell = 2.0, 2.8      # 默认值
```

> ⚠️ **k_sell 在下降趋势日 = 2.6σ（非 3.0σ）!** 规格书 §3.6 与 §10 参数表中：
> - 高波日（趋势向下时更容易回落）→ k_sell = **2.6**
> - 低波日 → k_sell = **3.0**
> - 默认 → k_sell = **2.8**

### 5.4 额度上限（§7.3, 继承 position_management §7.3）

```python
t0_quota = min(base_value × 0.20, available_reserve)
# 仅两项！不要加第三项 (slot_target - current_value)

# CashManager 优先级 = 4 = T0_SCALP（最低）
cash_manager.lock_cash(priority=4, strategy_name="t0")
```

### 5.5 Round-trip 成本模型（§6）

```python
# 理论 BE = 12.1 bps（保守口径 14.15 bps）
# 最低止盈目标 = 25 bps（硬编码，不可调）
# 单笔金额范围 = 10,000 ~ 14,000 元
MIN_PROFIT_TARGET_BPS = 25
```

---

## 6. 验收场景速查（完整 25 个场景见验证套件 Part 2）

### Regime 判定（#1-4）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 1 | vol_ratio=1.8, atr=50 | regime=True（量比触发） |
| 2 | vol_ratio=1.0, atr=70 | regime=True（ATR触发） |
| 3 | vol_ratio=1.2, atr=60 | regime=False → 全天无T+0 |
| 4 | 09:26 False, 10:30 波动率飙升 | 仍为False（日级不重算） |

### VWAP σ-带信号（#5-10）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 5 | vwap=1.055, σ=0.0042, k=2.0, price=1.0466 | 买入✅ |
| 6 | price=2.0, raw_σ=0.00001 | σ=max(0.00001, 2.0×0.0005)=**0.001** |
| 7 | vwap=1.055, σ=0.004, k=2.8, price=1.0662 | 卖出✅ |
| 8 | prev_cum=1M, cur_cum=1.05M | Δvol=**50000**（不是1050000） |
| 9 | time=09:55, 信号触发 | 禁止提交挂单 |
| 10 | 计算价=1.04661 | tick对齐后=1.047 |

### 时间窗口（#11-16）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 11 | 09:58 | 禁止任何T+0 |
| 12 | 11:26, 有持仓 | 允许平仓，禁止新买入 |
| 13 | 12:00, 有持仓 | 允许平仓，禁止买入/反T |
| 14 | 14:02, 无反T仓 | 禁止新开反T卖出 |
| 15 | 14:05, 有反T敞口 | 允许反T接回买入 |
| 16 | 14:15, 3买+1卖 | 撤3买，保留1卖 |

### 风控熔断（#17-21）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 17 | t0_daily_pnl=-610 (0.305%NAV) | 当日冻结 |
| 18 | 连续3笔亏损 | consecutive=3→冻结 |
| 19 | 亏→亏→赢→亏 | count: 1→2→0→1（不触发） |
| 20 | 涨幅+6.5% | 禁止反T卖出 |
| 21 | 跌幅-5.5% | 禁止正T买入 |

### 超时对账（#22-24）+ Mutex（#25）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 22 | 内存=SUBMITTED, 券商=已成交 | CASE A: 更正FILLED+同步PnL |
| 23 | 内存=SUBMITTED, 券商=已报 | CASE B: 撤单+部分成交处理 |
| 24 | 内存=SUBMITTED, 券商=无此委托 | CASE C: 更正REJECTED |
| 25 | 正T买入已成交, L1止损 | 清sellable，locked→pending_sell_locked |

---

## 7. 运行时断言（完整清单见验证套件 Part 3）

以下断言必须嵌入代码中。使用 `raise AssertionError()`，不用 `assert` 语句：

```python
# ===== VWAP 计算 =====
if sigma < price * 0.0005:
    raise AssertionError(f"sigma {sigma} 低于 floor {price * 0.0005}")

if delta_volume < 0:
    raise AssertionError(f"Δvolume 为负: {delta_volume}（可能用了累计量）")

# ===== 时间窗口 =====
if is_buy_order and order_type == "T0":
    if not ((time(10,0) <= now <= time(11,25)) or (time(13,15) <= now <= time(14,15))):
        raise AssertionError(f"T+0 买入在禁止时段: {now}")

if is_reverse_t_sell:
    if now > time(14, 0):
        raise AssertionError(f"反T卖出在 14:00 截止后: {now}")

# ===== 额度与频次 =====
if order_amount > t0_quota:
    raise AssertionError(f"T+0 下单金额 {order_amount} 超过额度 {t0_quota}")

if daily_round_trip_count > 1:
    raise AssertionError(f"日RT次数 {daily_round_trip_count} 超过上限 1")

if t0_gui_ops >= 15:
    raise AssertionError(f"T+0 GUI操作 {t0_gui_ops} 已达冻结阈值")

# ===== 挂单价格 =====
if order_price != round(order_price, 3):
    raise AssertionError(f"挂单价 {order_price} 未tick对齐(0.001)")

if order_price > limit_up or order_price < limit_down:
    raise AssertionError(f"挂单价 {order_price} 超出涨跌停范围")

# ===== 熔断 =====
if t0_daily_loss_pct >= 0.003 and allow_new_t0_order:
    raise AssertionError("日亏损已触发0.3%熔断，不应允许新订单")

if consecutive_loss_count >= 3 and allow_new_t0_order:
    raise AssertionError(f"连续{consecutive_loss_count}笔亏损，应冻结")

if price_change_today > 0.06 and is_reverse_t_sell:
    raise AssertionError(f"涨幅{price_change_today:.1%}>6%，禁止反T卖出")

if price_change_today < -0.05 and is_forward_t_buy:
    raise AssertionError(f"跌幅{price_change_today:.1%}<-5%，禁止正T买入")

# ===== Mutex =====
if layer1_stop_triggered and any_t0_buy_pending:
    raise AssertionError("Layer1止损触发，不应有T+0买入挂单")
```

---

## 8. 决策日志格式（JSONL）

### 8.1 六种日志类型速查

| type | 触发时机 | 关键字段 |
|:---|:---|:---|
| `T0_REGIME` | 09:26 每日 1 条 | regime_active, reason, auction_vol_ratio, atr5_percentile |
| `T0_SIGNAL` | 每次信号触发 | signal_type, vwap, sigma, k_value, target_price, kde_support, iopv_confidence |
| `T0_ROUND_TRIP` | 每次 RT 闭环 | direction, buy/sell_price, net_pnl_bps, actual_be_bps, consecutive_loss_count |
| `T0_BREAKER` | 每次熔断触发 | breaker_layer, trigger_value, threshold, action |
| `T0_RECONCILIATION` | 超时对账 | case(A/B/C), memory_state, broker_state, action |
| `T0_AUDIT` | 每日收盘后 | 7 项审计检查状态 |

### 8.2 日志示例（见验证套件 Part 4.1-4.5）

各格式的完整 JSON 示例已定义在验证套件中，严格遵循。

---

## 9. 吸取历次审计教训

> ⚠️ 以下是从 exit 策略和 position 策略审计中提炼的高频陷阱，T+0 模块**必须**规避：

1. **VWAP 增量计算**：L1 快照的 `volume` 是**全天累计量**，必须自行差分为增量 `Δvolume`。直接用累计量会导致 VWAP 计算崩溃。

2. **sigma_floor 不可遗漏**：`sigma = max(raw_sigma, price × 0.0005)`。清淡时段 raw_sigma 可能趋近 0，导致 VWAP ± k×sigma 带宽收窄至 0，任何微小波动都触发信号。

3. **挂单价两步处理顺序不可换**：先 `round_to_tick`，后 `clamp(涨跌停)`。反过来会导致 clamp 后的价格不在 tick 网格上。

4. **Regime 日级不翻转**：09:26 计算一次后全天不变。盘中不重新评估，即使波动率条件变化。

5. **日志必须记录决策前/后完整状态**：exit 审计中发现 3 个方法完全没写日志导致影子模式无法运行。T+0 的每个信号评估、订单操作、熔断触发都必须写日志。

6. **FSM 日志 from_state 必须在状态突变前保存**：先 `prev_state = ps.state`，再改状态，日志用 `prev_state.value`。position 审计中发现此 P0 级 bug。

7. **资金锁定时序**：position 审计发现"先下单后锁资金"导致未追踪订单。T+0 也是如此：先确认资金足够再下单，失败后要有取消订单的 fallback。

8. **买入量取整到 100 份（手），卖出量不取整**：A 股 ETF 规则。`qty = (int(amount / price) // 100) * 100`。

9. **CashManager 优先级规则**：T0_SCALP = 4（最低）。Layer1 止损 = 0，Scale 买入 = 2。T+0 不得挤占高优先级操作的资金。

10. **超时后不能仅冻结，必须强制对账**：仅冻结可能导致幽灵仓位（系统以为未成交，实际已成交），造成重复下单风险。

---

## 10. 接口契约

### 10.1 T+0 → Position Management（继承 §7）

```python
# T+0 引擎需要从 PositionFSM 获取的信息
def get_position_state(etf_code: str) -> FSMState     # S2/S3/S4 才允许T+0
def get_t0_frozen(etf_code: str) -> bool               # FSM级冻结
def get_total_qty(etf_code: str) -> int
def get_sellable_qty(etf_code: str) -> int
```

### 10.2 T+0 → CashManager

```python
# T+0 使用 CashManager 的方法
cash_manager.available_reserve(reserve_cap=RESERVE_CASH_CAP, safety_buffer=0)
cash_manager.lock_cash(priority=4, strategy_name="t0")  # 最低优先级
cash_manager.release_cash(order_id)
```

### 10.3 T+0 → Exit Strategy（Mutex）

```python
# 共享 EXIT_MUTEX 锁
from exit.exit_fsm import EXIT_MUTEX

# 所有涉及 sellable_qty / locked_qty 的操作必须在 EXIT_MUTEX 内
```

### 10.4 T+0 → DataAdapter

```python
# L1 快照获取（3 秒/次）
data.get_snapshot(etf_code) -> Snapshot  # 含 price, volume(累计), amount(累计), bid1, ask1, iopv
```

### 10.5 KDE 数据接口（文件）

```python
# T-1 日 18:00 批处理产出，T+0 盘中只读
# 路径：data/kde_zones/{etf_code}_{date}.json
# 格式：{"dense_zones": [{"upper": 1.050, "lower": 1.045, "strength": 0.85}, ...]}
```

---

## 11. 关键设计决策提醒

### 11.1 时间窗口行为矩阵（§8.2）

| 时段 | 买入 | 正T平仓卖 | 反T新开卖 | 撤单 |
|:---|:---:|:---:|:---:|:---:|
| 09:30-10:00 | ❌ | ❌ | ❌ | ✅ |
| 10:00-11:25 | ✅ | ✅ | ✅ | ✅ |
| 11:25-13:15 | ❌ | ✅(close-only) | ❌ | ✅ |
| 13:15-14:00 | ✅ | ✅ | ✅ | ✅ |
| 14:00-14:15 | ✅(仅接回) | ✅ | **❌** | ✅ |
| 14:15-14:30 | ❌ | ✅(close-only) | ❌ | ✅ |
| 14:30-15:00 | ❌ | ❌ | ❌ | ✅ |

### 11.2 9 层门控链（全部 AND，任一 FAIL → 静默）

```
Layer 0: circuit_breaker / intraday_freeze（position_management §3.2）
Layer 1: FSM state ∈ {S2, S3, S4}（position_management §7.2）
Layer 2: regime_active == True（本模块 §2）
Layer 3: time IN ACTIVE_WINDOWS（本模块 §8）
Layer 4: VWAP ± kσ（本模块 §3）
Layer 5: t0_daily_loss < 0.3%（本模块 §9.2）
Layer 6: t0_gui_ops < 15（本模块 §7.4）
Layer 7: t0_weekly_loss < 0.5%（5日滚动）
Layer 8: t0_monthly_loss < 1.0%（30日滚动）
Layer 9: consecutive_loss_count < 3（本模块 §9.2c）
```

### 11.3 Mutex 四场景优先级

| 场景 | 触发 | 处理 |
|:---|:---|:---|
| 1 | 正T成交 → L1止损 | 市价清sellable，locked→pending_sell_locked，次日09:30强平 |
| 2 | 反T挂单未成交 → L1止损 | 撤接回买单，良性异常 |
| 3 | T+0传输中 → L2止盈 | 等待10s后执行 |
| 4 | 反T接回已成交 → L1止损 | 同场景1（lock_reason="reverse_t_buyback_during_stop"） |

### 11.4 部分成交处理（§7.3）

```
成交 < 80% 目标 → 启动 3 分钟倒计时
3 分钟后仍部分成交 → 撤销剩余 → 已成交部分作微型仓位
微型仓位等待对应信号闭环，14:15 前仍未闭环 → 纳入底仓核算
```

### 11.5 VWAP 信号 ↔ KDE 协作规则

```
VWAP ✅ + KDE ✅（±1 tick）→ 合并为单一买单（最高置信）
VWAP ✅ + KDE ❌           → 执行买入（标准置信）
VWAP ❌ + KDE ✅           → 不执行（VWAP 是必要条件！）
VWAP ❌ + KDE ❌           → HOLD
```

### 11.6 可交易性检查（v1.1 P1-6）

```python
# 买入信号附加条件（第⑧条）：
tradability_bps = (k_buy × sigma / price) × 10000
if tradability_bps < 25:
    # 预期回归幅度不足以覆盖成本，信号无效
    signal = NO_SIGNAL
```

---

## 12. 最终交付检查清单

完成编码后，请逐一确认：

- [ ] 25 个验收场景全部通过
- [ ] 所有运行时断言已嵌入（非 `assert` 语句）
- [ ] 6 种 JSONL 日志格式正确，每个决策路径都有日志输出
- [ ] Regime 09:26 计算一次，全天不翻转
- [ ] VWAP 使用增量 Δvolume，不是累计量
- [ ] sigma_floor = price × 0.0005 已实现
- [ ] 挂单价先 round_to_tick 再 clamp 涨跌停
- [ ] k_buy/k_sell 按 trend_state 动态调整（不写死）
- [ ] 时间窗口行为矩阵完全覆盖（含 close-only、14:00 反T截止）
- [ ] 14:15 清道夫撤买单+反T接回单，保留卖单
- [ ] 额度公式仅两项：`min(base×20%, reserve)`
- [ ] CashManager 使用 priority=4（T0_SCALP，最低）
- [ ] 每日最多 1 次 RT，`daily_round_trip_count <= 1`
- [ ] GUI 操作配额 20 次独占，15 次冻结阈值
- [ ] 四层熔断全部实现：日(0.3%) + 周(0.5%) + 月(1.0%) + 连续3笔
- [ ] 极端行情：涨>6%禁反T，跌<-5%禁正T
- [ ] 超时后强制对账（CASE A/B/C），不仅冻结
- [ ] Mutex 四场景处理完整，Layer1 最高优先级
- [ ] 买入量取整到 100份，卖出量不取整
- [ ] 每个文件第一行 `from __future__ import annotations`
- [ ] `t0/__init__.py` 包含 `__all__` 列表
- [ ] 常量无魔数，全部引用 `constants.py`
- [ ] 可交易性检查：预期回归 ≥ 25 bps 才执行
