# ETF 波段入场策略 — 完整策略规格书 v1.3

> **版本**：v1.3（v1.2 + 跳空保护 + IOPV溢价门控 + ATR弱势仓位 + 抢占降级路径）  
> **文档性质**：纯策略方案，不含代码。确认后再进入编码阶段。  
> **配对文档**：[exit_strategy_specification.md](file:///d:/新建文件夹/exit_strategy_specification.md) v4.1

---

## 1. 系统约束速查（与退出策略共享）

| 约束 | 值 | 对入场策略的影响 |
|:---|:---|:---|
| 数据 | L1 快照 / 3 秒（XtQuant 回调推送）；日线 OHLCV | 盘中确认用快照，信号生成用日线 |
| 延迟 | 信号→成交：最坏 9 秒 (3s 采样 + 6s easytrader) | **禁止追涨突破瞬间**，必须用确认期 |
| 资金 | 20 万，2×7万持仓 + 6万备用金 | 分两步建仓（试探 30% + 确认 70%）；备用金用于 T+0 做 T + 抢占机制 |
| 持仓 | 波段 1-4 周 + 日内做 T | 入场信号必须瞄准≥1 周级别的趋势 |
| 结算 | A 股 T+1 | 试探仓当日不可卖，确认期设计必须覆盖 |
| 已有 | 筹码引擎 (MaxEnt) + LLM 情绪管线 + 微观结构因子引擎 | 直接消费其输出 |

---

## 2. 核心架构：三阶段入场流水线

> [!IMPORTANT]
> 入场决策分为三个完全隔离的阶段，每个阶段有独立的数据窗口和判定权限。
> 只有前一阶段通过，才进入下一阶段。

```
Phase 1: 盘后离线筛选 (T日 15:30-20:00)
  LLM情绪 + 筹码阻力 + 微观确认 → 候选池 (Watchlist)
                                    ↓
Phase 2: 日终信号判定 (T日 盘后计算)
  量化触发器 (Squeeze / 量价突破 / 波动率) + 趋势过滤器 → Signal_Fired
                                    ↓
Phase 3: 盘中确认执行 (T+1 ~ T+3 盘中)
  L1快照价格确认 + VWAP斜率验证 → 实盘买入 / 失效撤销
```

---

## 3. Phase 1：盘后离线筛选（候选池构建）

### 3.1 Gate 1：LLM 情绪先验 — S_sentiment_entry

**数据**：`finintel --signal-etf` 输出的 `sentiment_score ∈ [0, 100]`  
**更新**：每日盘后，T+1 延迟

```
S_sentiment_entry = sentiment_score

Gate 通过条件：S_sentiment_entry ≥ 60
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| 入池阈值 | 60 | 研究建议 75 过于激进，会错过启动初期；60 = 情绪边际转暖，宁可多看不错过 |

> [!WARNING]
> 入池阈值 60 vs 退出阈值 35：入场情绪要求远宽松于退出。这是刻意的不对称设计——
> 入场允许"可能性"，退出要求"确定性"。入场假阳性由后续 Phase 2/3 过滤。

### 3.2 Gate 2：筹码阻力扫描 — S_chip_entry

**数据**：筹码引擎 `calc_profit_ratio()` + `find_dense_zones()`  
**更新**：每日盘后，由 `daily_batch.py` 产出

```
PR_t = calc_profit_ratio(chips, close_t)    # 获利盘比例 (%)
dense_zones = find_dense_zones(chips, close_t)  # 筹码密集区列表

Gate 通过条件：PR_t ≥ 75%
```

**附加信息提取**（不做 Gate，用于 Phase 2 评分）：

```
# 上方最近阻力位
resistance_zones = [z for z in dense_zones if z["type"] == "resistance"]
nearest_resistance = min(resistance_zones, key=lambda z: z["price"])
    if resistance_zones else None

# 距最近阻力的空间
headroom = (nearest_resistance["price"] - close_t) / close_t
    if nearest_resistance else float('inf')

# 底部筹码集中度（ASR 反向指标）
bottom_concentration = calc_asr(chips, close_t, atr, k=1.0)
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| PR 入池阈值 | 75% | 研究建议 80%，但入池阶段适度放宽；Phase 2 对 PR ≥ 85% 给额外加分 |

### 3.3 Gate 3：微观结构健康检查 — S_micro_health

**数据**：微观结构因子引擎 `MicrostructureEngine.process_daily()` 输出  
**更新**：每日盘后

```
Gate 通过条件（任意一条不满足则观察但不移除）：
  VPIN_filtered ≤ 0.7 的百分位排名
    （VPIN 极高 = 知情交易者大量抛售，禁止入场）
  OFI_daily ≥ 0
    （日度订单流必须非负，确认买方主导）
```

> [!NOTE]
> Gate 3 是**软门控**：不满足时标的仍留在候选池，但标记 `micro_caution = True`，
> Phase 2 评分中扣分。目的是不遗漏情绪强+筹码好但当日微观结构暂时弱势的标的。

### 3.4 候选池输出

```
watchlist = [
    {
        "etf_code": "512480.SH",
        "sentiment_score": 72,
        "profit_ratio": 82.3,
        "nearest_resistance": 1.125,  # or None
        "headroom": 0.038,            # 距阻力 3.8%
        "bottom_concentration": 0.45, # ASR
        "micro_caution": False,
        "vpin_rank": 0.35,
        "ofi_daily": 15200,
    },
    ...
]
```

---

## 4. Phase 2：日终信号判定（评分触发）

### 4.1 评分公式

```
Score_entry = S_squeeze × 0.30
            + S_volume  × 0.25
            + S_chip_pr × 0.20
            + S_trend   × 0.15
            + S_micro   × 0.10

理论满分 = 1.00

ℹ️ 浮点精度规则：
  所有阈值比较前对 Score_entry 做 round(score, 2)。
  原因：IEEE 754 下 0.30 + 0.15 可能产生 0.44999...999，
        直接 >= 0.45 判定会失败。
  实现：使用 Python Decimal 类型或 round(score, 2) 后再比较。
```

| 信号 | 类型 | 满分贡献 | 计算时机 |
|:---|:---:|:---:|:---|
| S_squeeze | 二值 0/1 | 0.30 | 盘后（日线级） |
| S_volume | 二值 0/1 | 0.25 | 盘后（日线级） |
| S_chip_pr | 连续 [0, 1] | 0.20 | 盘后（筹码引擎） |
| S_trend | 二值 0/1 | 0.15 | 盘后（日线级） |
| S_micro | 连续 [0, 1] | 0.10 | 盘后（微观引擎） |

### 4.2 阈值与动作

| 阈值 | 值 | 动作 |
|:---|:---:|:---|
| 信号触发 | **≥ 0.45** | 生成 Signal_Fired，记录 H_signal / L_signal，进入 Phase 3 |
| 强信号 | **≥ 0.70** | 确认期缩短为 T+1~T+2（加速建仓） |

> [!IMPORTANT]
> **多维度门控**：Score_entry ≥ 0.45 的基础上，额外要求 `S_volume > 0 OR S_chip_pr > 0`。
> 纯靠 Squeeze + Trend（同属价格/波动率维度）不能独立触发入场。
> 这防止"阴跌后缩量反抽、布林带张口 + 均线短暂走平"这类假信号触发入场。

**典型触发组合验证**：

| 组合 | 得分 | 多维度门控 | 触发？ |
|:---|:---:|:---:|:---:|
| 仅 S_squeeze | 0.30 | ❌ 无量/筹码 | ❌ |
| S_squeeze + S_trend | **0.45** | ❌ 无量/筹码 | ❌ 门控拦截 |
| S_squeeze + S_chip_pr(0.5) | 0.40 | ✅ 有筹码 | ❌ 分数不足 |
| S_squeeze + S_chip_pr(0.8) | **0.46** | ✅ 有筹码 | ✅ |
| S_squeeze + S_volume | **0.55** | ✅ 有量 | ✅ |
| S_squeeze + S_volume + S_trend | **0.70** | ✅ 有量 | ✅ 强信号 |
| S_volume + S_chip_pr(1.0) + S_trend + S_micro(1.0) | **0.70** | ✅ 有量+筹码 | ✅ 强信号 |
| S_squeeze + S_volume + S_chip_pr(1.0) + S_trend + S_micro(1.0) | **1.00** | ✅ 有量+筹码 | ✅ 满分 |

> [!IMPORTANT]
> **设计原则**：至少需要一个触发器信号（S_squeeze 或 S_volume）加上一个确认信号才能入场。
> 单一触发器永远无法独立达标（0.30 或 0.25 < 0.45），这是防假突破的核心保障。

---

## 5. 信号详述

### 5.1 S_squeeze — 波动率挤压爆发 {0, 1}

**数据**：日线 OHLCV（至少 25 日回看）

**算法**：TTM Squeeze (Bollinger Bands vs Keltner Channels)

```
Step 1: 计算 BB 和 KC

  BB_mid  = SMA(Close, 20)
  BB_std  = StdDev(Close, 20)
  BB_upper = BB_mid + 2.0 × BB_std
  BB_lower = BB_mid - 2.0 × BB_std

  TR_i = max(High_i − Low_i, |High_i − Close_{i-1}|, |Low_i − Close_{i-1}|)
  KC_ATR = SMA(TR, 20)
  KC_upper = BB_mid + 1.5 × KC_ATR
  KC_lower = BB_mid - 1.5 × KC_ATR

Step 2: 检测压缩状态 (Squeeze On)

  squeeze_on_t = (BB_upper < KC_upper) AND (BB_lower > KC_lower)

Step 3: 检测爆发

  # 过去 5 个交易日中，至少 3 天处于压缩状态
  recent_squeeze = sum(squeeze_on_{t-4} ... squeeze_on_{t}) ≥ 3

  # T日布林带重新突破肯特纳通道
  squeeze_fired = recent_squeeze
                  AND NOT squeeze_on_t
                  AND (BB_upper > KC_upper)

  # 动量方向为正（线性回归偏差）
  linreg_val = Close_t - LinearRegression(Close, 20)_t
  momentum_positive = linreg_val > 0

  S_squeeze = 1  IF  squeeze_fired AND momentum_positive
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| BB 周期/乘数 | 20, 2.0 | 标准参数，与日线 OHLCV 周期匹配 |
| KC 周期/ATR 乘数 | 20, 1.5 | 标准 TTM Squeeze 参数 |
| 近期压缩天数 | ≥ 3/5 | 过滤偶发的单日瞬间压缩假信号 |
| 线性回归周期 | 20 | 与 BB 周期一致 |

### 5.2 S_volume — 量价共振突破 {0, 1}

**数据**：日线 OHLCV + 筹码引擎 `find_dense_zones()`

```
Step 1: 确定阻力位

  # 优先使用筹码引擎输出的阻力位（比主观画线更精准）
  resistance_zones = find_dense_zones(chips, close_t)
  overhead = [z for z in resistance_zones if z["type"] == "resistance"]

  IF overhead 非空:
    Resistance = overhead[0]["price"]   # 密度最高的上方阻力
  ELSE:
    Resistance = max(High_{t-1}, ..., High_{t-20})  # 回退到 20 日高点

Step 2: 突破判定

  price_break = Close_t > Resistance

Step 3: 放量确认

  vol_ratio = Volume_t / SMA(Volume, 20)_{t-1}
  volume_confirm = vol_ratio ≥ 2.0

Step 4: 实体 K 线过滤（排除放量滞涨/长上影线）

  body_ratio = (Close_t - Open_t) / max(High_t - Low_t, 0.001)
  solid_candle = body_ratio > 0.5

  S_volume = 1  IF  price_break AND volume_confirm AND solid_candle
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| 放量倍数 K | 2.0 | 研究实测，行业 ETF 2 倍量为强确认信号 |
| 实体 K 线比例 | > 0.5 | 过滤上影线≥实体的"假突破"形态 |
| 阻力位回退 | 20 日高点 | 当筹码引擎无上方阻力时的兜底方案 |

### 5.3 S_chip_pr — 筹码获利比例强度 [0, 1]

**数据**：筹码引擎 `calc_profit_ratio()` 输出 PR_t  
**注意**：此信号是连续值，不是二值

```
IF PR_t < 75%:
    S_chip_pr = 0  （Phase 1 Gate 已过滤，此处为防御性重复检查）

IF 75% ≤ PR_t < 80%:
    S_chip_pr = 0.3  （基础通过，但上方仍有 20%+ 套牢盘）

IF 80% ≤ PR_t < 85%:
    S_chip_pr = 0.6  （良好，上方阻力有限）

IF 85% ≤ PR_t < 90%:
    S_chip_pr = 0.85  （优秀，进入拉升真空区）

IF PR_t ≥ 90%:
    S_chip_pr = 1.0  （极优，几乎无上方抛压）
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| 分段阈值 | 75/80/85/90 | 与研究建议的 85% 标杆对齐，分段化避免硬门控 |

### 5.4 S_trend — 趋势状态过滤 {0, 1}

**数据**：日线 OHLCV（至少 30 日回看）

**算法**：KAMA 方向 + Elder Impulse 共振

```
Step 1: KAMA(10) 斜率

  ER_t = |Close_t - Close_{t-10}| / sum(|Close_i - Close_{i-1}|, i=t-9..t)
  fast = 2/3,  slow = 2/31
  SC_t = (ER_t × (fast - slow) + slow)²
  KAMA_t = KAMA_{t-1} + SC_t × (Close_t - KAMA_{t-1})

  kama_rising = KAMA_t > KAMA_{t-1} > KAMA_{t-2}

Step 2: Elder Impulse System

  ema13_rising = EMA(13)_t > EMA(13)_{t-1}

  MACD_hist = MACD Line - Signal Line
                (标准参数: MACD(12,26,9))
  macd_rising = MACD_hist_t > MACD_hist_{t-1}

  elder_green = ema13_rising AND macd_rising

Step 3: 组合

  S_trend = 1  IF  kama_rising AND elder_green
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| KAMA 周期 | 10 | 研究推荐值，短周期高灵敏度 |
| KAMA 连续上升天数 | 2 | KAMA_t > KAMA_{t-1} > KAMA_{t-2} = 趋势确立 |
| EMA 周期 | 13 | Elder 标准参数 |
| MACD 参数 | 12, 26, 9 | 经典参数 |

### 5.5 S_micro — 微观结构确认 [0, 1]

**数据**：微观结构因子引擎 `MicrostructureEngine.process_daily()` + `FeaturePipeline`

```
score = 0.0

# 子信号 A: OFI 方向确认 (+0.4)
IF ofi_daily > 0:
    score += 0.4

# 子信号 B: VPIN 处于活跃区间但非极端 (+0.3)
IF 0.3 ≤ vpin_rank ≤ 0.85:
    score += 0.3
    （VPIN 过低 = 无知情交易 = 无机构入场信号
      VPIN 过高 = 可能是知情抛售 = 危险）

# 子信号 C: Volume Surprise 确认异常兴趣 (+0.3)
IF vs_max ≥ 1.5:
    score += 0.3

S_micro = min(score, 1.0)
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| OFI 阈值 | > 0 | 简单方向判定，正值 = 买压主导 |
| VPIN Rank 活跃区间 | [0.3, 0.85] | 排除极端两端 |
| VS 异常阈值 | ≥ 1.5 | 成交量为 EMA 的 1.5 倍以上 = 异常兴趣 |

---

## 6. Phase 3：盘中确认与执行

### 6.1 确认期定义

```
T 日：信号触发日（盘后判定 Score_entry ≥ 0.45）
     记录：H_signal = High_T, L_signal = Low_T

确认窗口：
  普通信号 (Score < 0.70)：T+1 ~ T+3 日
  强信号   (Score ≥ 0.70)：T+1 ~ T+2 日
```

### 6.2 试探性建仓（T+1 日早盘）

```
IF Score_entry ≥ 0.45:
  T+1 日 09:35 ~ 10:00（避开集合竞价波动）:
    IF 开盘价 > L_signal:  (未跳空跌破信号日低点)
      → 买入计划总仓位的 30%（≈ 2.1 万元，基于单只 7 万）
      → 挂单价 = tick_ceil(Ask1 × 1.003)
      → 写入 state.json: {"intent": "TRIAL_ENTRY", "qty": N, ...}

IF Score_entry ≥ 0.70:  (强信号)
  T+1 日 09:35 ~ 10:00:
    IF 开盘价 > L_signal:
      → 买入计划总仓位的 50%（≈ 3.5 万元）
      → 挂单价 = tick_ceil(Ask1 × 1.003)
```

### 6.3 确认建仓（T+1 ~ T+3 盘中）

```
接收 L1 快照（3 秒/次）:

确认成立条件（全部满足）：

  a) 盘中最新价 > H_signal
     （突破信号日最高点 = 趋势延续确认）

     ⚠️ 跳空过大保护（防追高）：
       gap_ratio = (last_price - H_signal) / H_signal
       gap_threshold = max(1%, 0.5 × ATR_20 / Close_T)
       IF gap_ratio > gap_threshold:
         → 取消即时确认
         → 等待回踩确认：价格回落至 H_signal × (1 + gap_threshold) 以内
           或 10:00 后 VWAP 旜率确认上行
         → 告警：「⚠️ 跳空 {gap_ratio:.1%} 超过阈值，等待回踩确认」

  b) VWAP 斜率确认（含热身期规则）
     VWAP 计算：
       vwap_now = 日内累计 Amount / 日内累计 Volume
       每 60 个快照（3 分钟）记录一次 VWAP 值
       最近 3 个锚点: vwap_{-6min}, vwap_{-3min}, vwap_now
       slope_positive = vwap_now > vwap_{-3min} > vwap_{-6min}

     ⚠️ 数据窗口边界：
       VWAP 累计必须从 09:30:00 开始，不是 XtQuant 开始推送的时刻。
       09:15~09:29 的集合竞价 L1 快照必须丢弃，不可纳入 VWAP 计算。
       IF 收到 timestamp < 09:30:00 的快照：
         → 跳过，不累加到 Amount/Volume

     ⚠️ VWAP 热身期规则：
       09:30~09:50 开盘前 20 分钟的 VWAP 受集合竞价大单要污染，不可用于斜率判定。
       IF 当前时间 < 09:50:
         → 跳过条件 b)，仅用条件 a) 判定（价格突破）
         → 允许试探仓买入，但确认建仓必须等燭身完成

       额外安全阀：
       IF 09:35~09:50 累计成交量 < 历史同期 MA20 的 30%:
         → VWAP 热身延长至 10:00
         → 告警：「⚠️ 开盘成交量异常低迷，VWAP 热身期延长至 10:00」

  c) ETF 溢价率门控（防套利回调追入）
     premium = (last_price - iopv) / iopv
     试探仓：premium_threshold = 0.5%
     确认仓：premium_threshold = 0.3%

     IF premium > premium_threshold:
       → 暂缓当次买入，每 3 分钟重新检查直到溢价收敛或确认窗口过期
       → 告警：「⚠️ {etf_code} 溢价 {premium:.2f}%，暂缓入场等待收敛」

     降级处理：
       IF IOPV 数据不可用 (data_quality = "MISSING"):
         → 跳过条件 c)，按其他条件正常判断
         → 告警：「⚠️ IOPV 数据不可用，溢价保护降级」

  d) 当前时间 ≤ 14:30
     （禁止尾盘建仓，规避尾盘异动 + 留出日内观察窗口）

  e) data_feed ≠ STALE
     （L1 快照新鲜度 ≤ 15 秒）

满足后：
  → 买入剩余仓位（70% 或 50%）
  → 挂单价 = tick_ceil(Ask1 × 1.003)
  → 写入 state.json: {"intent": "CONFIRM_ENTRY", ...}
  → 标记 position_established = True
  → 激活退出策略系统的 Layer 1 / Layer 2 监控
```

### 6.4 失效撤退

```
失效条件（任一满足）：

  a) 确认窗口到期（T+3 或 T+2 收盘）且未确认
     → 取消入场计划

  b) 窗口期内任何一日收盘价 < L_signal
     → 立即取消入场计划
     → 若已有试探仓，次日（T+2/T+3/T+4）开盘卖出
       挂单价 = tick_floor(Bid1 × 0.98)

  c) 窗口期内 LLM 情绪评分骤降 (sentiment_score < 40)
     → 立即取消入场计划
     → 若已有试探仓，按上述价格退出

失效处理后：
  → 记录 signal_failed = True
  → 该 ETF 进入 5 个交易日冷却期（防止连续假信号消耗资金）
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| 试探仓比例 | 30% (普通) / 50% (强) | 普通信号保守试探，强信号加大试探 |
| 确认窗口 | T+3 (普通) / T+2 (强) | 强信号更快确认或放弃 |
| VWAP 斜率检查间隔 | 3 分钟 | 60 个 L1 快照 = 3 分钟，足够平滑 |
| VWAP 热身期 | ≥ 09:50 | 开盘前 20 分钟 VWAP 受集合竞价污染，不可用于斜率判定 |
| 跳空保护阈值 | max(1%, 0.5×ATR₀/Close) | 超过此幅度的跳空禁止即时确认，必须等待回踩 |
| ETF 溢价门控 | 试探 0.5% / 确认 0.3% | 溢价超标时暂缓入场，防套利回调追入 |
| 尾盘截止 | 14:30 | 与退出策略救生衣回补截止时间一致 |
| 冷却期 | 5 交易日 | 防止"假突破→重新触发→再假突破"的亏损循环 |
| 情绪骤降阈值 | < 40 | 接近退出策略的恶化阈值 35，在此之上留 5 分缓冲 |

---

## 7. 挂单价汇总

| 场景 | 挂单价 | 取整 | 理由 |
|:---|:---|:---:|:---|
| 试探性建仓买入 | min(Ask1 × 1.003, 涨停价) | ceil | 确保成交 + 不超涨停 |
| 确认建仓买入 | min(Ask1 × 1.003, 涨停价) | ceil | 同上 |
| 失效退出卖出 | max(Bid1 × 0.98, 跌停价) | floor | 与退出策略 Layer 1 一致 + 不低于跌停 |

**价格精度规则**：与退出策略完全一致
- A 股 ETF 最小价格变动单位 = **0.001 元**
- 买入：`tick_ceil(price) = math.ceil(price * 1000) / 1000`
- 卖出：`tick_floor(price) = math.floor(price * 1000) / 1000`

> [!WARNING]
> **涨跌停价校验**：所有挂单价必须 clamp 到 [跌停价, 涨停价] 区间内。
> 超过涨停价的买单或低于跌停价的卖单会被券商拒绝，导致入场失败。
> 涨停价 = 前一交易日收盘价 × (1 + 涨跌幅限制)，ETF 通常为 10%。

---

## 8. Layer 2：日内 T+0 增厚策略

> [!IMPORTANT]
> Layer 2 仅在 Layer 1 底仓已建立且处于浮盈状态时激活。
> 与退出策略的 T+0 熔断共享 0.3% 日亏损上限。

### 8.1 策略定义：3 分钟 KAMA-VWAP 偏离均值回归

```
前提条件（全部满足）：
  1. position_established = True (底仓已建)
  2. current_return > 1%  (浮盈 > 1%，非亏损头寸)
  3. 退出策略 reduced ≠ True  (Layer 2 未触发减仓)
  4. 日内 T+0 累计亏损 < 0.3%

数据构建：
  L1 快照 → 3 分钟 K 线重采样
  KAMA_3min = KAMA(10) 基于 3 分钟 Close
  VWAP_intraday = 日内累计成交额 / 累计成交量

左侧低吸条件（全部满足）：
  a) 3 分钟 Close < VWAP_intraday × (1 - 1.5σ_intraday)
     σ_intraday = 近 40 根 3 分钟 K 线的收盘价标准差

  b) KAMA_3min 斜率 < 0 且连续下行 ≥ 3 根 K 线
     （确认急跌，非缓慢阴跌）

  c) 当前时间 ≤ 14:15
     （T+0 必须当日闭环，预留 45 分钟平仓窗口）

买入方式：
  → 在 VWAP_intraday × (1 - 2σ_intraday) 位置提前挂限价单
  → 挂单量 = 底仓的 30%（≈ 2-3 万元）
  → 由于是提前挂单，完全免疫 4-6 秒延迟

平仓离场条件（任一满足）：
  a) 3 分钟 Close 回升至 KAMA_3min 附近
     （价格回到均值 = 均值回归完成）

  b) 持仓 ≥ 45 分钟
     （时间止损，强制闭环）

  c) 亏损触及 0.3% 日熔断线

平仓方式：
  → 市价卖出（easytrader 模拟），挂 tick_floor(Bid1)
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| KAMA 3min 周期 | 10 | 与日线 KAMA 一致，保持参数简洁 |
| 偏离度触发 | 1.5σ | 排除正常波动的日内回调 |
| 挂单位置 | 2σ | 预设更深位置，利用限价单免疫延迟 |
| T+0 闭环截止 | 14:15 | 14:15 + 45分钟时间止损 = 最迟 15:00 |
| 日熔断 | 0.3% | 与退出策略的 T+0 熔断线完全一致 |

---

## 9. 数据健康与降级策略

与退出策略共享数据健康监控基础设施。

### 9.1 入场特有的数据健康检查

```
entry_data_health = {
    "S_sentiment_entry": AVAILABLE / UNAVAILABLE,  # LLM 超时/API 失败
    "S_chip_pr":         AVAILABLE / UNAVAILABLE,  # 筹码引擎崩溃/冷启动
    "S_squeeze":         AVAILABLE / UNAVAILABLE,  # 日线数据不足 25 日
    "S_volume":          AVAILABLE / UNAVAILABLE,  # 日线数据 / 筹码引擎
    "S_trend":           AVAILABLE / UNAVAILABLE,  # 日线数据不足 30 日
    "S_micro":           AVAILABLE / UNAVAILABLE,  # 微观引擎崩溃/数据缺失
}
```

### 9.2 降级策略

| 场景 | 处理 |
|:---|:---|
| 筹码引擎冷启动 (< 10 天) | S_chip_pr = UNAVAILABLE；Gate 2 改用 PR 替代规则：20日高点突破 |
| LLM 不可用 | Gate 1 跳过（开放入池），但 Score_entry 总分上限降至 0.85 |
| 微观引擎不可用 | S_micro = 0（不贡献分数），不影响其他信号 |
| 日线数据不足 25 日 | S_squeeze = UNAVAILABLE，S_volume 回退到 20 日高点 |
| **Phase 3 期间 L1 数据停滞** | **暂停确认判定，不执行买入** |

> [!WARNING]
> **与退出策略的关键差异**：退出策略在数据缺失时倾向于"最高风控"（无救生衣全清仓），
> 入场策略在数据缺失时采取**不作为**（不买入）。这是正确的不对称：
> - 退出时错过 = 亏损扩大 → 宁可误杀
> - 入场时错过 = 错失机会 → 可以等下一次

---

## 10. 场景走查

### 场景 1：教科书式底部爆发

```
T-20~T-5: 半导体 ETF 长期横盘，BB 内嵌于 KC (Squeeze On)
          LLM 评分逐步从 50 → 68 → 73
          PR: 65% → 72% → 78%（底部筹码逐渐聚集）

T-2:  LLM = 73 ≥ 60 → ✅ Gate 1
      PR = 78% ≥ 75% → ✅ Gate 2
      → 进入候选池

T 日: Squeeze Fired (BB 重新突破 KC) + 动量正
      Volume = 2.3× 均量, Close > 阻力位, 实体 K 比 0.72
      KAMA 连升 2 日, Elder 绿色
      OFI = +28000, VS_max = 2.1, VPIN_rank = 0.52

      Score = 0.30 (squeeze) + 0.25 (volume) + 0.20×0.6 (PR=78%)
            + 0.15 (trend) + 0.10×1.0 (micro)
            = 0.30 + 0.25 + 0.12 + 0.15 + 0.10 = 0.92 ≥ 0.70 → ★ 强信号 ★

      记录 H_signal = 1.085, L_signal = 1.050

T+1:  09:35 开盘 1.070 > L_signal
      → 买入 50% (强信号)，≈ 5 万元 @Ask1×1.003
      10:30 盘中 1.091 > H_signal (1.085)
            VWAP 连续上行 ✅
      → 买入剩余 50% @Ask1×1.003
      → 激活退出策略监控

结果：1 天完成建仓，入场价 ≈ 1.075
```

### 场景 2：假突破被确认期过滤

```
T 日: 军工 ETF squeeze fired，放量突破阻力 0.950
      Score = 0.55 (squeeze + volume) → 普通信号

      H_signal = 0.958, L_signal = 0.932

T+1:  09:35 买入 30% 试探仓 @0.945
      全天最高 0.952 < H_signal (0.958) → 不确认

T+2:  最高 0.948 < 0.958 → 仍不确认
      收盘 0.940 > L_signal → 继续等待

T+3:  收盘 0.928 < L_signal (0.932) → ★ 失效 ★
      → 取消入场计划
      → 5 日冷却期

T+4:  09:35 卖出试探仓 @tick_floor(Bid1×0.98)
      亏损 ≈ 0.945 → 0.920 ≈ -2.6% × 3万 = -780 元（总资金 0.39%）

总结：确认期成功拦截假突破，试探仓限制了亏损
```

### 场景 3：微观结构否决

```
T 日: 新能源 ETF 表面信号强劲
      LLM = 72, PR = 83%, Squeeze Fired, 放量突破
      但：VPIN_rank = 0.92 (极端，可能是知情抛售)
          OFI_daily = -15000（卖方主导）

      Phase 1: micro_caution = True
      Phase 2: S_micro = 0.0
      Score = 0.30 + 0.25 + 0.12 + 0.15 + 0 = 0.82

      仍然触发（0.82 ≥ 0.45），但系统已记录 micro_caution
      → 降级为普通信号（不享受强信号加速）
      → 确认期 T+1~T+3

T+2:  价格果然冲高回落，OFI 持续为负
      → 确认期到期，未满足条件 → 失效撤退

总结：微观结构虽未阻止信号触发，但通过降低分数延长了确认期，
      给了市场时间暴露真实意图
```

### 场景 4：筹码引擎冷启动降级

```
系统刚部署第 5 天，筹码引擎仍在冷启动

T 日: AI ETF, LLM = 78
      S_chip_pr = UNAVAILABLE → Gate 2 切换到 20 日高点突破
      Price > 20日 High → 替代 Gate 通过

      S_squeeze = 1, S_volume = 1
      S_chip_pr = UNAVAILABLE → 贡献 0
      S_trend = 1, S_micro = 0.7

      Score = 0.30 + 0.25 + 0 + 0.15 + 0.07 = 0.77

      → 触发（因为筹码分缺失不影响触发器信号）
      → 告警：「⚠️ 筹码引擎冷启动第 5/10 天，S_chip_pr 已禁用，
              入场信号仅依赖量价+趋势+微观指标。」
```

### 场景 5：T+0 日内增厚

```
底仓已建立：光伏 ETF @1.200, 当前 1.230 (浮盈 2.5%)

14:00  3 分钟 K 线急跌，Close_3min = 1.218
       VWAP_intraday = 1.228
       σ_intraday = 0.004
       偏离 = (1.218 - 1.228) / 1.228 = -0.81%
       1.5σ = 0.6% → 偏离 0.81% > 0.6% → ✅
       KAMA_3min 连续下行 3 根 ✅
       时间 14:00 ≤ 14:15 ✅

       → 在 1.228 × (1 - 2×0.004/1.228) ≈ 1.220 提前挂限价单买入
       → 14:02 成交 @1.220，买入底仓的 30%

14:25  价格回升至 1.226 ≈ KAMA_3min
       → 市价卖出 @Bid1 = 1.225
       → 净赚 ≈ 0.005/1.220 × 3万 = 123 元

       日内增厚 123 元，持仓成本微幅下降
```

---

## 11. 盘前 / 盘后时序（入场相关）

| 时间 | 内容 |
|:---|:---|
| **15:30** | 筹码引擎 daily_batch + 微观因子引擎 daily_batch |
| **16:00** | LLM 情绪评分管线执行 |
| **18:00~20:00** | **Phase 1 筛选**：遍历全市场 ETF，构建候选池 |
| | **Phase 2 判定**：对候选池内 ETF 计算 Score_entry |
| | Signal_Fired 的 ETF 写入 pending_entry 队列 |
| **09:00** | 检查 state.json → pending_entry 队列 |
| | 数据健康检查 + 冷启动检查 |
| **09:25** | 跳空检查：若开盘价 < L_signal → 取消入场 |
| **09:35** | 执行试探性建仓（如有 pending_entry） |
| **09:30~15:00** | L1 快照监控：价格 vs H_signal 确认判定 |
| | T+0 做 T 模块运行（如底仓已建立） |

---

## 12. 持仓数量与互斥规则

### 12.1 常规持仓规则

```
最大同时持仓 ETF 数量 = 2

IF 当前波段持仓 ETF 数 = 2:
    → Phase 1 候选池继续扫描（保持感知市场变化）
    → Phase 2/3 常规入场暂停
    → 但 Preemption 机制仍可触发（见 12.2）
    → 告警：「ℹ️ 已持仓 2 只 ETF，常规入场暂停，抢占扫描继续」

IF 当前有 1 只 ETF 处于 Phase 3 确认期中:
    → 另一只 ETF 可独立触发 Phase 1/2/3
    → 但试探仓总额 ≤ 7 万元

资金分配：
  20 万总资金
  单只 ETF 最大仓位 = 7 万
  双持仓 = 2 × 7 万 = 14 万（70% 仓位）
  备用金 = 6 万（用于 T+0 做 T 弹药 + 抢占机制临时仓位）
```

### 12.2 强信号抢占机制 (Preemption)

> [!WARNING]
> 抢占是**极稀缺事件**（预计年化 5-10 次），仅在"满仓 + 极强新信号"时触发。
> 目的是避免因持仓占满而错过最高置信度的趋势启动。

```
触发条件（全部满足）：
  a) 当前已持仓 2 只 ETF（满仓状态）
  b) 新标的 Score_entry ≥ 0.85（极强信号，五维高度共振）
  c) 至少存在 1 只弱势仓位（定义见下方）

弱势仓位定义（满足任一）：
  类型 A：处于 Phase 3 试探阶段且尚未确认的仓位
  类型 B：已确认但满足以下全部条件：
    ① 浮盈 < 0.5 × ATR_20（以买入日收盘 ATR 为基准）
    ② 持有 ≥ 3 个交易日

  🛡️ 硬约束：永远不抢占浮盈 > 2% 的已确认仓位。

T+1 约束：
  弱势仓位必须已持有 ≥ 1 个交易日（当天买入的不可被抢占）

抢占执行流程：
  T 日盘后：Score_entry ≥ 0.85 信号触发，识别弱势仓位
  T+1 日 09:35：
    0) 执行前二次校验：
       IF 当前持仓数 < 2（可能因 Layer 1 止损已在盘前/开盘清仓）：
         → 取消 Preemption，转为常规入场流程
         → 告警：「ℹ️ 持仓已不满，Preemption 转为常规入场」
    1) 先卖出弱势仓位（先卖后买原则）
       挂单价 = tick_floor(max(Bid1 × 0.98, 跌停价))
    2) 卖出成功后，动用备用金（≤ 4 万）买入新标的试探仓
       挂单价 = tick_ceil(min(Ask1 × 1.003, 涨停价))
       → 此时系统临时持有 2 只 ETF（先卖后买，不会出现 3 只）
    ’’ 备选流程（弱势仓当日无法卖出时）：
       IF 弱势仓卖出挂单 30 分钟未成交：
         → 仍可动用备用金买入新标的（临时 3 只）
  T+1 日收盘前：
    弱势仓位必须完成清仓 → 恢复为 2 只持仓
    IF 弱势仓位卖出失败：
      → T+2 日开盘优先级最高：限价卖出 tick_floor(max(Bid1 × 0.97, 跌停价))

  ⚠️ 抢占降级路径（弱势仓无法卖出时）：
    IF T+2 日开盘仍无法成交（跌停/无量极端行情）：
      → 放弃本次 Preemption
      → 抢占仓按试探仓失效逻辑退出（次日开盘卖出）
      → 系统进入 3 日冷却期（不再接受新的 Preemption）
      → 记录事件 {"type": "PREEMPTION_SELL_FAILED", ...}
      → 告警：「🔴 弱势仓 {etf_code} 连续 2 日无法卖出，抢占放弃」

抢占日特殊规则：
  → T+0 增厚策略暂停（备用金已占用）
  → 抢占仓在后续 T+1~T+3 走正常确认期流程
  → 如果抢占仓也确认失败 → 恢复为 1 只持仓 + 释放资金
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| 最大并行持仓 | 2 | 分散选错风险，20万足以支撑双持仓 |
| 单只上限 | 7 万 | 双持仓 14 万 + 6 万备用 = 20 万 |
| 备用金 | 6 万 | T+0 弹药 (3万) + 抢占临时仓 (≤4万) |
| 抢占阈值 | Score ≥ 0.85 | 仅接受五维高度共振信号（年化 5-10 次） |
| 弱势滞涨阈值 | 浮盈 < 0.5×ATR_20 且 ≥3 天 | ATR 自适应：高波动 ETF 阈值宽松，低波动 ETF 阈值严格 |
| 不可抢占保护线 | 浮盈 > 2% | 保护正在运行的盈利头寸 |
| 抢占降级 | 弱势仓 T+2 仍无法卖出 | 放弃抢占，防止极端行情下系统卡死 |

---

## 13. 生产加固

### 13.1 🔴 P0 — pending_entry 持久化

```
Phase 2 产生 Signal_Fired 后：
  写入 state.json: {
      "pending_entries": [{
          "etf_code": "512480.SH",
          "signal_date": "2026-02-17",
          "h_signal": 1.085,
          "l_signal": 1.050,
          "score": 0.92,
          "is_strong": true,
          "expire_date": "2026-02-19",  # T+2 (强信号)
          "trial_qty": 0,
          "confirmed": false,
          "status": "PENDING_TRIAL"
      }]
  }
```

### 13.2 🔴 P0 — 试探仓与确认仓执行状态

```
状态机：
  PENDING_TRIAL → TRIAL_PLACED → TRIAL_FILLED → PENDING_CONFIRM
  → CONFIRM_PLACED → CONFIRM_FILLED → ACTIVE
  → FAILED (任何阶段均可跳转)

每次状态转换：原子写入 state.json（与退出策略共享 os.replace 方法）
```

### 13.3 🔴 P0 — 入场/退出互锁

```
执行锁共享：
  入场买单和退出卖单使用同一个 Mutex
  → 永远不会同时提交买单和卖单

关键场景：
  IF 退出策略 Layer 1 正在执行（硬止损）:
      → 所有入场操作立即冻结
      → 待 Layer 1 完成后，检查是否需要继续入场

  IF 入场试探仓刚买入，同日退出策略 Layer 2 触发:
      → Layer 2 只减仓已确认的底仓，不动试探仓
      → 试探仓有独立的失效退出逻辑
```

### 13.4 � P0 — 备用金管理器 (CashManager)

> [!WARNING]
> T+0 Layer 2 和 Preemption 共享 6 万备用金池。
> T+0 挂单可冻结 ≤3 万，Preemption 需要 ≤4 万，最坏情况需 7 万 > 可用 6 万。
> **必须有优先级冲突解决机制**，否则券商将报 "Insufficient Cash"。

```
CashManager 职责：

  1. 追踪实时现金状态
     total_reserve  = 6 万
     locked_cash    = 所有 pending 挂单冻结金额（含 T+0 限价单）
     available_cash = total_reserve - locked_cash

  2. 优先级定义
     PREEMPTION  = 最高（极稀缺高价值事件）
     TRIAL_ENTRY = 中等（常规试探建仓）
     T0_SCALP    = 最低（日内增厚）

  3. 冲突解决流程（当 Preemption 触发且 available_cash 不足时）

     Step 1: 检查可用现金
       available = total_reserve - locked_cash
       IF available ≥ preemption_amount(≤4万):
         → 正常执行 Preemption，无需干预

     Step 2: 撤销低优先级挂单
       IF available < preemption_amount:
         → 查询所有 priority="T0_SCALP" 的 pending 挂单
         → 调用 easytrader.cancel_entrust(entrust_no) 逐一撤单
         → 不等待撤单确认（方案 B）

     Step 3: 直接提交 Preemption 买单
       → 立即提交买单，让券商做最终裁判
       → IF 券商接受 → Preemption 成功，T+0 当日暂停
       → IF 券商拒绝 (Insufficient Cash) →
            记录事件 {"type": "PREEMPTION_CASH_CONFLICT", ...}
            Preemption 顺延至 T+2 日重试（如果信号仍在确认期内）

  4. state.json 追踪
     "cash_manager": {
         "locked_orders": [
             {"entrust_no": "12345", "priority": "T0_SCALP",
              "amount": 30000, "etf_code": "512480.SH"}
         ],
         "last_conflict": "2026-03-15"  // 最近一次冲突日期
     }
```

| 参数 | 值 | 依据 |
|:---|:---:|:---|
| Preemption 优先级 | 最高 | 年化 5-10 次，潜在收益 2000-5000 元/次 |
| T+0 优先级 | 最低 | 高频低价值，~100 元/次，可牺牲 |
| 撤单后等待 | 不等待 | 方案 B：让券商做最终裁判，避免自建资金追踪的复杂度 |

### 13.5 �🟡 P1 — 信号重复触发保护

```
同一 ETF 的 Signal_Fired 在以下条件下不得重复触发：
  a) 已有活跃的 pending_entry（任何状态）
  b) 已持有该 ETF 的波段仓位
  c) 该 ETF 处于冷却期（失效后 5 个交易日）
```

### 13.6 🟡 P1 — 崩溃恢复

```
系统启动时：
  1. 读取 state.json → pending_entries 列表
  2. 查询 easytrader 实际持仓
  3. 对比：
     trial_qty > 0 但实际无持仓 → 买单可能未成交 → 查询委托列表
     实际持仓 > 试探量 → 确认仓可能已成交 → 更新状态
  4. 检查 expire_date → 过期的 pending 自动标记 FAILED
```

### 13.7 🟢 P2 — 冷却期持久化

```
state.json: {
    "cooldown": {
        "512480.SH": "2026-02-22"  # 冷却期到期日
    }
}
```

---

## 14. 自检：逻辑一致性验证

| # | 检查项 | 结果 |
|:---:|:---|:---:|
| 1 | Phase 1/2/3 严格顺序，无跳级 | ✅ |
| 2 | 单一触发器无法独立达标（需 ≥2 个信号组合） | ✅ |
| 3 | 试探仓 T+1 不可卖 = 确认期 ≥ T+2 兜底 | ✅ |
| 4 | 入场/退出执行锁共享 Mutex | ✅ |
| 5 | 数据缺失 → 不作为（不买入） | ✅ |
| 6 | 挂单价 tick_ceil/floor 精度一致 | ✅ |
| 7 | T+0 熔断与退出策略共享 | ✅ |
| 8 | 最大持仓 2 只 ETF + 抢占临时 3 只 | ✅ |
| 9 | 抢占不可平浮盈 >2% 的已确认仓位 | ✅ |
| 10 | 冷却期防连续假信号消耗 | ✅ |
| 11 | 冷启动降级有兜底逻辑 | ✅ |
| 12 | 崩溃恢复查询实际持仓 + 在途委托 | ✅ |
| 13 | 强信号加速确认（T+2 vs T+3） | ✅ |
| 14 | 试探仓失效退出价格与 Layer 1 一致 | ✅ |
| 15 | pending_entry 原子写入 | ✅ |
| 16 | 尾盘 14:30 截止建仓 | ✅ |
| 17 | VWAP 热身期 09:50 前不用斜率确认 | ✅ |
| 18 | 近失事件 [0.25, 0.45) 归档作为 ML 负样本 | ✅ |
| 19 | Score_entry 比较前 round(score, 2) 防浮点精度丢失 | ✅ |
| 20 | CashManager 解决 T+0 与 Preemption 备用金竞争 | ✅ |
| 21 | 跳空过大 (>max(1%, 0.5ATR)) 禁止即时确认 | ✅ |
| 22 | IOPV 溢价门控（试探 0.5% / 确认 0.3%）+ 数据降级 | ✅ |
| 23 | Preemption 弱势仓 T+2 无法卖出 → 放弃抢占 + 冷却 | ✅ |
| 24 | 多维度门控：Score≥0.45 额外要求 S_volume>0 OR S_chip_pr>0 | ✅ |
| 25 | 挂单价 clamp 到 [跌停价, 涨停价] 区间 | ✅ |
| 26 | VWAP 从 09:30:00 开始累计，丢弃集合竞价快照 | ✅ |
| 27 | Preemption 先卖后买 + 执行前二次校验持仓数 | ✅ |
| 28 | 多模块并行时先卖后买排序，CashManager 实时校验资金 | ✅ |

---

## 15. 数据归档规范（为未来升级蓄力）

> [!IMPORTANT]
> 以下数据必须在系统运行的**第一天**就开始持久化。
> 它们是 Phase 2 所有升级（HMM、ML 权重优化、贝叶斯切换）的燃料。
> 缺失的历史数据**无法补录**。

### 15.1 入场事件日志（核心）

每次 Signal_Fired 产生时，归档以下完整快照：

```
存储：data/entry_events/{date}_{etf_code}.json
保留：永久（磁盘占用极低，每事件 < 2KB）

{
    // ── 事件元数据 ──
    "event_id": "uuid",
    "etf_code": "512480.SH",
    "signal_date": "2026-03-15",
    "score_entry": 0.82,
    "is_strong": true,

    // ── 各信号分量快照（用于回溯哪些信号在哪些场景下有效）──
    "signals": {
        "S_squeeze": 1,
        "S_volume": 1,
        "S_chip_pr": 0.6,
        "S_trend": 1,
        "S_micro": 0.7,
        "sentiment_score": 73,
        "profit_ratio": 82.3,
        "vpin_rank": 0.52,
        "ofi_daily": 28000,
        "vs_max": 2.1,
        "kyle_lambda": 0.00035
    },

    // ── 价格锚点 ──
    "h_signal": 1.085,
    "l_signal": 1.050,
    "close_signal_day": 1.078,

    // ── 筹码状态快照 ──
    "chip_snapshot": {
        "dense_zones": [{"price": 1.02, "density": 0.15, "type": "support"}],
        "asr": 0.45,
        "nearest_resistance": 1.125
    },

    // ── 结果（事后填写）──
    "outcome": {
        "status": "CONFIRMED | FAILED_TIMEOUT | FAILED_BREAK_LOW | FAILED_SENTIMENT",
        "entry_price": 1.075,
        "exit_price": 1.180,
        "holding_days": 18,
        "return_pct": 9.77,
        "max_adverse": -1.2,
        "max_favorable": 12.5
    }
}
```

**用途**：

| 归档字段 | Phase 2 用途 |
|:---|:---|
| signals 分量 | 统计每个信号的条件胜率，用于标定贝叶斯似然比 |
| signals 分量 | 训练 ML 模型学习自适应权重 |
| outcome.status | 计算各信号组合的 False Positive Rate |
| outcome.return_pct | 构建 HMM 训练标签（高收益事件 → Bull Regime） |
| max_adverse / max_favorable | 优化止损/止盈参数 |
| chip_snapshot | 训练筹码形态识别模型 |

### 15.2 日度因子矩阵（全市场）

```
存储：data/factor_daily/{date}.parquet
保留：至少 2 年（约 500 个交易日 × 300 只 ETF × 20 列 ≈ 50MB/年）

每行一只 ETF，列包含：
  etf_code, date,
  close, volume, atr,
  profit_ratio, asr,
  squeeze_on, squeeze_fired,
  kama_slope, elder_green,
  bb_width, kc_width,
  vpin_filtered, vpin_rank, ofi_daily, kyle_lambda, vs_max,
  sentiment_score,
  vol_ratio  (当日成交量 / MA20)
```

**用途**：HMM 训练输入 + 多因子回测 + 参数灵敏度分析

### 15.3 入场信号未触发的候选池快照

```
存储：data/watchlist_daily/{date}.json
保留：6 个月滚动

记录每日 Phase 1 候选池内容（即使 Phase 2 未触发）。
用途：分析“差一点触发”的案例，评估阈值 0.45 是否过严/过松。
```

### 15.4 确认期价格行为

```
存储：data/confirmation_trace/{event_id}.parquet
保留：永久

对每个 Signal_Fired 事件，记录 T+1~T+3 的 L1 快照子集：
  时间戳, close, high, low, volume, vwap_intraday

用途：分析确认期行为模式（成功 vs 失败的价格轨迹差异），
      优化确认条件（H_signal 突破 vs VWAP 斜率的必要性）。
```

### 15.5 近失事件归档（ML 负样本）

> [!NOTE]
> 机器学习不仅需要正样本（触发了），也需要负样本（没触发）。
> "差一点触发"的标的后来大涨 = 漏网之鱼，是修正阈值的最有价值数据。

```
存储：data/near_miss_events/{date}_{etf_code}.json
保留：永久（磁盘占用极低，每事件 < 1KB）

触发条件：Score_entry ∈ [0.25, 0.45)
  → 0.25 = 单个触发器火了但无共振（分析“为什么单指标失效”的关键样本）
  → < 0.25 = 纯噪声（不值得存储）

记录内容（与 entry_events 格式一致的 signals 快照）：
{
    "event_id": "uuid",
    "etf_code": "512480.SH",
    "signal_date": "2026-03-15",
    "score_entry": 0.42,
    "near_miss_reason": "score_below_threshold",
    "score_gap": 0.03,     // 差 0.03 才能触发

    "signals": { ... },    // 与 entry_events 完全一致
    "h_signal": 1.085,
    "l_signal": 1.050,
    "chip_snapshot": { ... },

    // 事后跟踪（用于评估"漏网之鱼"）
    "outcome": {
        "5d_return": 3.2,    // 之后 5 天收益率 %
        "10d_return": 7.1,   // 之后 10 天
        "20d_return": 12.5,  // 之后 20 天（覆盖典型波段周期）
        "max_favorable_20d": 15.2  // 20 天内最大顺向运动 %
    }
}
```

### 15.6 归档汇总

| 数据 | 存储路径 | 保留策略 | 磁盘估算 |
|:---|:---|:---:|:---|
| 入场事件日志 | `data/entry_events/` | 永久 | <1MB/年 |
| **近失事件** | **`data/near_miss_events/`** | **永久** | **<0.2MB/年** |
| 日度因子矩阵 | `data/factor_daily/` | 2 年 | ~50MB/年 |
| 候选池快照 | `data/watchlist_daily/` | 6 个月 | ~5MB/年 |
| 确认期轨迹 | `data/confirmation_trace/` | 永久 | ~10MB/年 |
| L1 快照原始数据 | `data/snapshots/` (已有) | 1 年 | ~2GB/年 |
| 筹码分布快照 | `data/chip_snapshots/` (已有) | 1 年 | ~200MB/年 |

---

## 16. 胜率提升路线图

### Phase 2A — 数据驱动的权重优化（前置：100+ 入场事件）

```
目标：用真实数据替代当前的人工经验权重

方法：
  1. 从 entry_events 日志提取所有 Signal_Fired 事件
  2. 标注 outcome.status 和 outcome.return_pct
  3. 对每个信号维度计算条件胜率：
     P(盈利 | S_squeeze=1) vs P(盈利 | S_squeeze=0)
  4. 用 Logistic Regression 拟合最优权重
  5. A/B 对比新权重 vs 旧权重的回测 Sharpe Ratio

预期收益：胜率 +3~5%
风险：低（仅调整权重，不改变信号定义和执行逻辑）
前置：约 4-6 个月实盘运行
```

### Phase 2B — 贝叶斯推断替代加权评分（前置：Phase 2A 完成）

```
目标：利用似然比的乘法更新获得非线性信号叠加效应

方法：
  1. 从 Phase 2A 提取每个信号的似然比：
     LR_i = P(S_i=1 | 成功入场) / P(S_i=1 | 失败入场)
  2. 验证似然比稳定性（不同时间段的变异系数 < 30%）
  3. 若稳定：切换为贝叶斯更新
     P_posterior ∝ P_prior × ∏ LR_i
     P_prior = f(sentiment_score)
  4. 在入场事件日志上做 Walk-Forward 验证

预期收益：多信号共振场景下胜率额外 +2~3%
风险：中（似然比不稳定则退回加权评分）
前置：200+ 事件样本 + Phase 2A 已验证信号独立性
```

### Phase 2C — HMM Regime 过滤器（前置：2 年日线数据）

```
目标：增加 P(Bull) 作为第六个评分维度

方法：
  1. 用 factor_daily 中的日收益率、日振幅、相对成交量训练 3-State HMM
  2. Walk-Forward 训练：滚动 500 天窗口，每天前向推断
  3. 输出 P(Bull) 作为 S_regime ∈ [0, 1] 加入评分公式
  4. 验证：P(Bull) > 0.6 时现有信号胜率是否显著更高

预期收益：降低 FPR 5~10%（盘整市中减少无效入场）
风险：高（模型复杂，需 GPU 训练管线 + 滚动训练框架）
前置：500+ 个交易日的 factor_daily 数据 + Phase 2A 基线建立
```

### Phase 2D — 筹码形态识别（前置：笹码引擎运行 > 60 天）

```
目标：识别“底部单峰密集→突破拉升”的经典主力建仓完毕形态

方法：
  1. 定义特征指标：
     笹码峰数量、主峰集中度、主峰距现价比例、峰形变化趋势
  2. 用 entry_events 中的 chip_snapshot 标注成功/失败形态
  3. 训练简单决策树分类器
  4. 输出 S_chip_pattern ∈ {0, 1} 替代或增强 S_chip_pr

预期收益：更精准的笹码状态判断
风险：中（依赖笹码引擎精度）
前置：60+ 天笹码历史 + 20+ 个入场事件样本
```

### Phase 2E — 行业轮动宏观过滤（前置：宏观数据管线）

```
目标：根据经济周期阶段缩小 ETF 扫描范围

方法：
  1. 接入宏观指标：PMI、社融、利率、CPI
  2. 映射经济周期阶段（复苏→过热→滞胀→衰退）
  3. 按周期阶段筛选适宜行业：
     复苏期 → 消费类 ETF；扩张期 → 制造/科技 ETF；过热期 → 原材料/能源 ETF
  4. 不在当前阶段的行业 → 从 Phase 1 候选池中排除

预期收益：减少“逆周期入场”的系统性错误
风险：低（仅做筛选，不改变信号逻辑）
前置：宏观指标数据管线搭建
```

### 路线图时间线

```
月份 0-3   系统投产，积累数据，不做任何修改
           （关键产出：entry_events + factor_daily 归档）

月份 3-6   Phase 2A: 权重优化（Logistic Regression）
           Phase 2D: 笹码形态识别（决策树）

月份 6-12  Phase 2B: 贝叶斯切换（如果 2A 的似然比稳定）
           Phase 2E: 行业轮动过滤

月份 12+   Phase 2C: HMM Regime（需要 500 天数据）
```

> [!TIP]
> **月份 0-3 的黄金法则**：运行时间越长、数据归档越完整，后续升级的效果越好。
> 即使 Phase 1 的胜率不理想，也不要急于修改参数——先积累足够样本做统计，
> 避免基于小样本的“幸存者偏差”调参。
