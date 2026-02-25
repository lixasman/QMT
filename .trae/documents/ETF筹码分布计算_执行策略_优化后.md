# ETF 筹码分布计算引擎 —— 技术规格书

> **用途**：本文档供 AI 编程助手直接阅读并实现代码。所有模块的输入、输出、依赖关系均已明确定义。

***

## 目录

1. [项目概览](#1-项目概览)
2. [执行模式](#2-执行模式)
3. [项目结构](#3-项目结构)
4. [数据结构定义](#4-数据结构定义)
5. [Module A: IOPV 计算器](#5-module-a-iopv-计算器)
6. [Module B: MaxEnt 成交分布求解器](#6-module-b-maxent-成交分布求解器)
7. [Module C: 换手衰减模型](#7-module-c-换手衰减模型)
8. [Module D: 申赎修正器](#8-module-d-申赎修正器)
9. [Module E: 布朗扩散](#9-module-e-布朗扩散)
10. [Module F: 指标输出](#10-module-f-指标输出)
11. [Module G: 筹码引擎主控](#11-module-g-筹码引擎主控)
12. [参数速查表](#12-参数速查表)

***

## 1. 项目概览

### 1.1 系统定位

A股行业 ETF 的筹码分布计算引擎，基于 XtQuant L1 快照数据（3秒级），输出三类指标：

| 输出            | 消费方                  | 用途           |
| ------------- | -------------------- | ------------ |
| 获利盘比例 (%)     | DeepSeek 情绪分析 Prompt | 情绪因子         |
| 筹码密集区 (价格列表)  | 量化止损/止盈模型            | 支撑/阻力位       |
| ASR 因子 (0\~1) | 策略切换逻辑               | 动量 vs 均值回归判断 |

### 1.2 硬件环境

* CPU: AMD 7800X3D (8核, 96MB L3 V-Cache)

* GPU: AMD 7900XTX (24GB VRAM, 未来回测加速)

* RAM: 48GB DDR5

* 技术栈: Python 3.11 / NumPy / SciPy / Pandas

### 1.3 ETF 与个股的核心差异（必须在实现中体现）

| 差异                 | 影响        | 应对模块                 |
| ------------------ | --------- | -------------------- |
| 份额每日因申赎变化（质量不守恒）   | 筹码总量会凭空增减 | Module D: 申赎修正器      |
| 交易者含做市商+套利资金（非纯散户） | 前景理论衰减需弱化 | Module C: α=0.5 混合衰减 |
| 存在 IOPV 作为公允价值锚    | 可判断溢折价方向  | Module A: IOPV 计算器   |

***

## 2. 执行模式

本引擎支持 **3 种执行模式**，按使用阶段选择：

### 2.1 模式一：冷启动（首次运行）

**场景**：系统首次部署，没有任何历史 L1 快照数据。

```
输入：XtQuant 历史日线数据（最近 60 个交易日的 OHLCV）
处理：用日线级三角分布法初始化一个"粗糙但可用"的筹码分布
输出：初始筹码分布快照（存储到本地）

注意：此模式精度最低，仅作为 bootstrap。运行 5-10 个交易日的
     "每日批量模式"后，L1 数据的逐日累积会逐渐覆盖初始误差。
     冷启动后会执行尾部再校准（recalibrate_tails），清除 ±2×ATR 外的
     "幽灵筹码"，防止远端三角分布残留污染密集区检测。
```

**冷启动算法**（日线级，非 MaxEnt）：

```python
def cold_start_from_daily(daily_df: pd.DataFrame, chips: ChipDistribution,
                          decay: float = 0.95, total_shares: float = 0.0):
    """
    用历史日线数据初始化筹码分布（三角分布法 + 指数衰减）
    
    Args:
        daily_df: 最近 60 日的日线数据, 列: date, open, high, low, close, volume
        chips: 空的 ChipDistribution 容器
        decay: 每日衰减因子（越老的数据权重越低）
        total_shares: ETF 当前总份额（必须传入，防止后续 base_tr 分母为 0）
    """
    n = len(daily_df)
    for i, (_, row) in enumerate(daily_df.iterrows()):
        weight = decay ** (n - 1 - i)  # 最新一天权重=1, 60天前≈0.05
        vol = row['volume'] * weight
        low, high, close = row['low'], row['high'], row['close']

        # 三角分布：将成交量分散在 [low, high] 区间
        price_range = np.arange(low, high + chips.bucket_size, chips.bucket_size)
        if len(price_range) < 2:
            idx = chips.price_to_index(close)
            if 0 <= idx < len(chips.chips):
                chips.chips[idx] += vol
            continue

        for p in price_range:
            idx = chips.price_to_index(p)
            if 0 <= idx < len(chips.chips):
                if p <= close:
                    ratio = (p - low) / max(close - low, 1e-6)
                else:
                    ratio = (high - p) / max(high - close, 1e-6)
                chips.chips[idx] += vol * max(ratio, 0) / len(price_range)

    # 冷启动结束后必须设置 total_shares
    if total_shares > 0:
        chips.total_shares = total_shares


def recalibrate_tails(chips: ChipDistribution, recent_close: float, atr: float,
                     atr_k: float = 2.0, decay_rate: float = 0.1):
    """将远离当前价 ±atr_k×ATR 的尾部筹码按距离指数衰减。
    
    冷启动后调用，防止三角分布在远端残留"幽灵筹码"长期影响密集区。
    """
    grid = chips.get_price_grid()
    dist = np.abs(grid - recent_close)
    threshold = atr_k * atr
    mask = dist > threshold
    if mask.any():
        excess = (dist[mask] - threshold) / atr
        chips.chips[mask] *= np.exp(-decay_rate * excess)
```

### 2.2 模式二：每日批量（常规运行，收盘后执行）

**场景**：每个交易日收盘后（15:30 之后），批量处理当天所有 L1 快照数据。

```
前置条件：收盘后通过 XtQuant 拉取当日历史 L1 快照数据（无需盘中订阅）。

执行流程：
  1. 加载昨日筹码分布快照
  2. 通过 XtQuant 拉取今日 L1 快照数据
  3. 逐快照执行 MaxEnt → 衰减 → 注入
  4. 执行申赎修正（XtQuant T+1 份额数据）
  5. 执行日终布朗扩散
  6. 保存今日筹码分布快照
  7. 输出指标（获利盘、密集区、ASR）

数据存储要求：
  - 每天保存 L1 快照的 Parquet 文件（用于可能的重算）
  - 每天保存筹码分布快照（用于次日加载）

耗时：约 200-300 只 A股行业 ETF ≈ 15-20 秒
```

### 2.3 模式三：盘中实时（针对单只 ETF）

**场景**：交易时段内，对关注的 ETF 实时更新筹码分布，用于盘中决策。

```
前置条件：
  1. 该 ETF 已有昨日的筹码分布快照（来自模式二）
  2. XtQuant 正在实时订阅该 ETF 的 L1 快照

执行流程：
  订阅 XtQuant L1 回调 → 每收到一个新快照：
    1. MaxEnt 求解该快照的成交分布
    2. 衰减旧筹码 + 注入新筹码
    3. 实时更新获利盘比例、筹码密集区
    4. (不执行布朗扩散，扩散仅在日终)

性能：单只 ETF 单次快照处理 < 15ms，远快于 3秒的快照间隔。
     可同时实时追踪 10-20 只 ETF 无压力。
```

### 2.4 三种模式的对比

| 维度   | 冷启动      | 每日批量          | 盘中实时            |
| ---- | -------- | ------------- | --------------- |
| 执行时机 | 仅首次      | 每日 15:30 后    | 交易时段 9:30-15:00 |
| 数据源  | 日线 OHLCV | L1 快照 (收盘后拉取) | L1 快照 (实时订阅)    |
| 处理范围 | 全市场过滤后   | 全市场过滤后        | 单只/少量 ETF       |
| 精度   | 低（三角分布）  | 高（MaxEnt）     | 高（MaxEnt）       |
| 布朗扩散 | 不执行      | 执行            | 不执行（日终补）        |
| 申赎修正 | 不执行      | 执行（用真实份额）     | 不执行（日终补）        |

### 2.5 推荐的日常运行流程

```
交易日流程（仅批量模式，无盘中实时）：

15:30  通过 XtQuant 拉取今日历史 L1 快照数据 → 存入 Parquet
15:31  执行"每日批量模式"，处理今日全量数据
15:32  输出今日全市场 ETF 的筹码指标

交易日流程（含盘中实时）：

09:15  启动 XtQuant，对重点关注的 ETF 订阅 L1 快照
09:30  启动"盘中实时模式"，实时更新筹码分布
15:00  收盘，停止实时模式
15:30  拉取全量历史数据，执行"每日批量模式"
15:32  输出筹码指标
```

### 2.6 ETF 过滤规则

仅处理 A股行业/主题 ETF，排除以下类别（按名称关键词过滤）：

```python
# config.py
ETF_EXCLUDE_KEYWORDS = [
    "货币", "债", "存单",        # 固收类
    "豆粕",                     # 商品类
    "黄金",                     # 贵金属
    "QDII", "跨境", "海外",    # 跨境类
    "美国", "纳斯达克", "标普",  # 美股指数
    "道琼斯", "巴西",          # 其他外国指数
    "日经", "德国", "法国",    # 欧亚指数
]
```

过滤后预计缩减至 **200-300 只** A股行业/主题 ETF，批量处理耗时降至约 **15-20 秒**。

***

## 3. 项目结构

```
etf_chip_engine/
├── config.py                # 全局参数配置
├── models/
│   ├── __init__.py
│   └── chip_distribution.py # ChipDistribution 数据结构
├── modules/
│   ├── __init__.py
│   ├── iopv_calculator.py   # Module A: IOPV 计算
│   ├── maxent_solver.py     # Module B: MaxEnt 求解
│   ├── turnover_model.py    # Module C: 换手衰减
│   ├── redemption.py        # Module D: 申赎修正
│   ├── diffusion.py         # Module E: 布朗扩散
│   └── indicators.py        # Module F: 指标输出
├── engine.py                # Module G: 主控引擎（ETFChipEngine）
├── cold_start.py            # 冷启动逻辑
├── realtime.py              # 盘中实时模式入口
├── daily_batch.py           # 每日批量模式入口
└── data/
    ├── snapshots/            # L1 快照 Parquet 文件（按日期）
    └── chip_snapshots/       # 每日筹码分布快照（.npz）
```

***

## 4. 数据结构定义

### 4.1 ChipDistribution

**文件**: `models/chip_distribution.py`

```python
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ChipDistribution:
    """
    单只 ETF 的筹码分布容器。
    
    使用动态稠密桶（Dense Buckets）存储，价格轴连续，
    仅覆盖 [base_price, base_price + len(chips) * bucket_size] 区间。
    
    内存占用：约 18KB / ETF（4500 桶 × 4 字节）
    """
    etf_code: str
    base_price: float           # 价格桶起始价格
    bucket_size: float = 0.001  # 桶宽度（元），ETF 最小变动单位
    chips: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    total_shares: float = 0.0   # ETF 当前总份额
    last_update: datetime = field(default_factory=datetime.now)

    def price_to_index(self, price: float) -> int:
        return int(round((price - self.base_price) / self.bucket_size))

    def index_to_price(self, index: int) -> float:
        return self.base_price + index * self.bucket_size

    def get_price_grid(self) -> np.ndarray:
        """返回完整价格网格数组"""
        return self.base_price + np.arange(len(self.chips)) * self.bucket_size

    def save(self, path: str):
        np.savez_compressed(path,
            chips=self.chips,
            meta=np.array([self.base_price, self.bucket_size, self.total_shares])
        )

    @classmethod
    def load(cls, path: str, etf_code: str) -> 'ChipDistribution':
        data = np.load(path)
        meta = data['meta']
        return cls(
            etf_code=etf_code,
            base_price=float(meta[0]),
            bucket_size=float(meta[1]),
            chips=data['chips'].astype(np.float32),
            total_shares=float(meta[2])
        )
```

### 4.2 XtQuant ETF 信息结构

**数据来源**: `xtdata.get_etf_info(etf_code)` 返回 dict，关键字段：

```python
# xtdata.get_etf_info("159915.SZ") 返回示例：
{
    "etfCode": "159915",
    "name": "创业板ETF易方达",
    "reportUnit": 1000000,       # 最小申赎单位（ETF 份额数/篮子）
    "navPerCU": 3308909.44,      # 每篮子净值（可用于校验 IOPV）
    "nav": 3.3089,               # 每份净值
    "cashBalance": 1916.44,      # 篮子中的现金差额
    "stocks": {                  # 成分股持仓（每篮子口径）
        "302132.SZ": {
            "componentCode": "302132",
            "componentVolume": 200,  # 每篮子中该成分股的股数
            ...
        },
        ...
    }
}
```

> **关键**：`componentVolume` 是"每个申赎篮子"口径，不是"每份 ETF"口径。
> 计算 IOPV 时分母必须用 `reportUnit`。

不再需要独立的 `ETFHoldings` 数据模型，直接消费 XtQuant 返回的 dict。

***

## 5. Module A: IOPV 计算器

**文件**: `modules/iopv_calculator.py`\
**职责**: 用成分股实时价格计算 ETF 的参考净值（IOPV），输出溢折价率。

### 算法

$$\text{IOPV} = \frac{\sum\_{i=1}^{N} P\_i \times \text{componentVolume}\_i + \text{cashBalance}}{\text{reportUnit}}$$

$$\text{PremiumRate} = \frac{P\_{ETF} - \text{IOPV}}{\text{IOPV}}$$

| 变量                          | 含义              | XtQuant 字段                     |
| --------------------------- | --------------- | ------------------------------ |
| $P\_i$                      | 成分股 $i$ 实时价格    | XtQuant 行情订阅                   |
| $\text{componentVolume}\_i$ | 每篮子中成分股 $i$ 的股数 | `stocks[code].componentVolume` |
| $\text{cashBalance}$        | 篮子中现金替代部分       | `cashBalance`                  |
| $\text{reportUnit}$         | 一篮子对应的 ETF 份额数  | `reportUnit`                   |

校验公式：`navPerCU / reportUnit` 应 `= nav`（每份净值）

### 接口

```python
class IOPVCalculator:
    def __init__(self, etf_info: dict): ...
    def update_stock_price(self, stock_code: str, price: float) -> None: ...
    def calculate_iopv(self) -> float: ...
    def get_premium_rate(self, etf_price: float) -> float: ...
    def get_coverage(self) -> float:
        """返回已覆盖成分股占比 [0,1]，用于衰减 premium 可信度"""
```

### 实现

```python
import numpy as np
from typing import Dict


class IOPVCalculator:
    def __init__(self, etf_info: dict):
        """
        Args:
            etf_info: xtdata.get_etf_info(etf_code) 的返回值
        """
        self.stocks = etf_info['stocks']           # {code: {componentVolume, ...}}
        self.cash_balance = etf_info['cashBalance'] # 篮子现金差额
        self.report_unit = etf_info['reportUnit']   # 每篮子 ETF 份额数
        self.nav_per_cu = etf_info.get('navPerCU', 0)  # 用于校验
        self.stock_prices: Dict[str, float] = {}

    def update_stock_price(self, stock_code: str, price: float):
        """更新单只成分股的最新价格"""
        self.stock_prices[stock_code] = price

    def calculate_iopv(self) -> float:
        """
        计算 IOPV（每份 ETF 的参考净值）
        
        Returns:
            IOPV 值。若成分股覆盖率不足 50% 则返回 nan。
        """
        basket_nav = 0.0
        total_components = len(self.stocks)
        covered = 0

        for stock_code, stock_info in self.stocks.items():
            if stock_code in self.stock_prices:
                basket_nav += self.stock_prices[stock_code] * stock_info['componentVolume']
                covered += 1

        if covered < total_components * 0.5:
            return float('nan')  # 覆盖率不足，IOPV 不可靠

        # 按覆盖率外推未覆盖的成分股
        basket_nav *= total_components / covered

        # 加上现金差额
        basket_nav += self.cash_balance

        # 除以每篮子份额数 = 每份 IOPV
        return basket_nav / self.report_unit

    def get_premium_rate(self, etf_price: float) -> float:
        """计算溢折价率。正值=溢价，负值=折价"""
        iopv = self.calculate_iopv()
        if np.isnan(iopv) or iopv <= 0:
            return 0.0  # 无法计算时返回 0，MaxEnt δ 自动退化为两约束
        return (etf_price - iopv) / iopv

    def get_coverage(self) -> float:
        """返回已覆盖成分股占比 [0, 1]，用于衰减 premium 可信度。
        
        当 coverage < 1.0 时，premium 乘以 coverage 作为可信度权重，
        避免少量成分股报价导致 IOPV 偏差污染 MaxEnt 偏度。
        """
        total = sum(1 for s in self.stocks.values()
                    if isinstance(s, dict) and s.get('componentVolume') is not None)
        covered = sum(1 for k in self.stocks if k in self.stock_prices)
        return covered / max(total, 1)
```

***

## 6. Module B: MaxEnt 成交分布求解器

**文件**: `modules/maxent_solver.py`\
**职责**: 在每个 L1 快照窗口内，用最大熵原理还原成交量的价格分布。

### 算法

给定 L1 快照中的 High, Low, Volume, Amount，求解成交量概率分布 $v(p)$。

最大化香农熵 $H\[v] = -\sum\_p v(p) \ln v(p)$，受 3 个约束：

| 约束            | 公式                                                   | 来源       |
| ------------- | ---------------------------------------------------- | -------- |
| C1: 归一化       | $\sum\_p v(p) = 1$                                   | 数学要求     |
| C2: 均值 = VWAP | $\sum\_p p \cdot v(p) = \text{Amount}/\text{Volume}$ | L1 快照    |
| C3: 溢折价偏度     | $\delta$ 由 IOPV 的 PremiumRate 驱动                     | Module A |

解的形式（玆尔兹曼分布）：

$$v(p') = \frac{1}{Z(\beta, \delta)} \exp\bigl(-\beta p' + \delta (p' - 0.5)^3\bigr)$$

> **偏度项设计（红队审计后重新设计）**
>
> 旧版使用 $-\gamma \cdot p'^2$（偶函数），无法真正表达偏度方向，仅调节端点权重。
> 新版使用 $+\delta \cdot (p' - 0.5)^3$（奇函数，围绕中点），真正表达左/右偏度：
> - 溢价（premium > 0）→ δ > 0 → 右偏（高价端权重增大，反映买入压力）
> - 折价（premium < 0）→ δ < 0 → 左偏（低价端权重增大，反映卖出压力）

> **映射函数（tanh 替代线性截断）**
>
> 旧版：$\gamma = -\text{sign}(pr) \times \min(|pr| \times k\_{gamma}, \gamma\_{max})$（硬阈值 1e-6 + 线性截断）
> 新版：$\delta = \delta\_{max} \times \tanh(pr / \text{sensitivity})$
> - `sensitivity = 0.0005`（≈5 bps），在 ±5bps 内 tanh 自然衰减为死区
> - 大溢折价时自动饱和到 $\delta\_{max}$，无需硬截断

$\beta$ 通过 Newton-Raphson 求解（通常 5 次迭代内收敛）：

$$\beta\_{n+1} = \beta\_n + \frac{\langle p' \rangle\_{\beta\_n} - \text{VWAP}'}{\text{Var}\[p']\_{\beta\_n}}$$

### 接口

```python
class MaxEntSolver:
    def __init__(self, max_iter: int = 20, tol: float = 1e-8): ...
    def solve(self, price_grid: np.ndarray, vwap: float,
              premium_rate: float = 0.0,
              delta_max: float = 2.0,
              premium_sensitivity: float = 0.0005) -> np.ndarray: ...
```

### 实现

```python
class MaxEntSolver:
    def __init__(self, max_iter: int = 20, tol: float = 1e-8):
        self.max_iter = max_iter
        self.tol = tol

    def solve(self, price_grid: np.ndarray, vwap: float,
              premium_rate: float = 0.0,
              delta_max: float = 2.0, premium_sensitivity: float = 0.0005) -> np.ndarray:
        """三约束 MaxEnt（归一化 + VWAP + 溢折价偏度）

        偏度项使用围绕中点的奇函数 δ·(p'-0.5)^3，真正表达左/右偏度。
        premium → δ 使用 tanh 映射，在 ±sensitivity 内自然衰减为零。
        """
        N = len(price_grid)
        if N <= 1:
            return np.ones(N)

        # 价格归一化到 [0, 1]，使 δ 生效
        p_min, p_max = price_grid.min(), price_grid.max()
        p_range = p_max - p_min
        if p_range < 1e-12:
            return np.ones(N) / N
        p_norm = (price_grid - p_min) / p_range
        vwap_norm = np.clip((vwap - p_min) / p_range, 0, 1)

        # δ 偏度映射：tanh 自然提供死区 + 饱和
        delta = delta_max * np.tanh(premium_rate / max(premium_sensitivity, 1e-6))

        # 奇函数偏度基底：围绕 p'=0.5 的三次函数
        p_centered = p_norm - 0.5

        beta = 0.0
        for _ in range(self.max_iter):
            log_v = -beta * p_norm + delta * (p_centered ** 3)
            log_v -= log_v.max()
            v = np.exp(log_v)
            v /= v.sum()

            mean_p = np.dot(v, p_norm)
            residual = mean_p - vwap_norm
            if abs(residual) < self.tol:
                break

            var_p = np.dot(v, p_norm**2) - mean_p**2
            if var_p < 1e-12:
                break
            beta += residual / var_p

        log_v = -beta * p_norm + delta * (p_centered ** 3)
        log_v -= log_v.max()
        v = np.exp(log_v)
        v /= v.sum()
        return v
```

***

## 7. Module C: 换手衰减模型

**文件**: `modules/turnover_model.py`\
**职责**: 计算每个价位的筹码衰减率。ETF 版本弱化了前景理论的影响。

### 算法

持仓收益率: $r = (P\_{current} - p) / p$

$$\text{Turnover}(p) = \text{BaseTurnover} \times \big\[\alpha \cdot f\_{prospect}(r) + (1 - \alpha) \cdot 1.0\big]$$

其中 $\alpha = 0.5$（ETF 专用，个股用 0.8-1.0）。

$f\_{prospect}(r)$ 分三段：

| 区间                   | 公式                                               | 含义       |
| -------------------- | ------------------------------------------------ | -------- |
| $r > 0$              | $1.0 + \kappa\_1 \cdot \ln(1+r)$                 | 获利区：落袋为安 |
| $-0.2 \leq r \leq 0$ | $\max(0.1, ; 1.0 - \kappa\_2 \cdot \|r\|^{0.5})$ | 浅套：损失厌恶  |
| $r < -0.2$           | $0.1 + \kappa\_3 \cdot (\|r\| - 0.2)$            | 深套：止损释放  |

建议参数: $\kappa\_1 = 1.5, ; \kappa\_2 = 1.5, ; \kappa\_3 = 0.5$

### 接口

```python
class TurnoverModel:
    def __init__(self, alpha=0.5, kappa1=1.5, kappa2=1.5, kappa3=0.5): ...
    def prospect_factor(self, returns: np.ndarray) -> np.ndarray: ...
    def calculate_turnover(self, price_grid: np.ndarray,
                           current_price: float,
                           base_turnover: float) -> np.ndarray: ...
```

### 实现

```python
class TurnoverModel:
    def __init__(self, alpha=0.5, kappa1=1.5, kappa2=1.5, kappa3=0.5):
        self.alpha = alpha
        self.kappa1 = kappa1
        self.kappa2 = kappa2
        self.kappa3 = kappa3

    def prospect_factor(self, returns: np.ndarray) -> np.ndarray:
        f = np.ones_like(returns)
        # 获利区
        mask = returns > 0
        f[mask] = 1.0 + self.kappa1 * np.log1p(returns[mask])
        # 浅套区
        mask = (returns >= -0.2) & (returns <= 0)
        f[mask] = np.maximum(0.1, 1.0 - self.kappa2 * np.sqrt(np.abs(returns[mask])))
        # 深套区
        mask = returns < -0.2
        f[mask] = 0.1 + self.kappa3 * (np.abs(returns[mask]) - 0.2)
        return f

    def calculate_turnover(self, price_grid, current_price, base_turnover):
        returns = (current_price - price_grid) / np.maximum(price_grid, 1e-6)
        f = self.alpha * self.prospect_factor(returns) + (1 - self.alpha)
        return base_turnover * f
```

***

## 8. Module D: 申赎修正器

**文件**: `modules/redemption.py`\
**职责**: 修正 ETF 份额变化导致的筹码质量不守恒问题。

**数据来源**: XtQuant 提供 ETF 总份额数据（**T+1 延迟**），因此：

* **每日批量模式**: 使用真实份额差值 $\Delta S = S\_{today} - S\_{yesterday}$ 精确修正

* **盘中实时模式**: 不执行申赎修正（日终由批量模式补偿）

### 算法

设 $\Delta S = S\_{today} - S\_{yesterday}$（从 XtQuant 获取的真实份额变化量）：

**申购（$\Delta S > 0$）**：在当日 VWAP 附近注入新筹码
$$\Delta\text{Chips}(p) = \Delta S \cdot G(p; \mu=\text{VWAP}, \sigma=\text{creation\_sigma\_k} \times \text{DailyRange})$$

其中 $\text{DailyRange} = \text{High}_{max} - \text{Low}_{min}$（当日全部快照的极差），$\text{creation\_sigma\_k} = 0.2$。
$\sigma$ 转换为桶数后取下限 2 桶，防止极低波动日过度集中。

> **大额申购 σ 加宽保护**（红队审计新增）
>
> 当单日申购量 $\Delta S$ 占比超过 `creation_large_threshold`（默认 5%）时，
> 说明大量资金涌入，实际建仓分布更宽。此时强制加宽：
> $$\sigma_{buckets} \geq \text{creation\_large\_sigma\_k} \times \frac{\text{DailyRange}}{\text{bucket\_size}}$$
> `creation_large_sigma_k` 默认 0.3，确保大额申购的注入分布更接近日内全量成交分散度。

**赎回（$\Delta S < 0$）**：获利盘优先扣除
$$\Delta\text{Chips}(p) = |\Delta S| \times \frac{S(p) \cdot g(r)}{\sum\_p S(p) \cdot g(r)}$$

其中 $g(r) = 1 + \max(r, 0)$，获利越多赎回概率越大。

### 接口

```python
class RedemptionCorrector:
    def apply_creation(self, chips: ChipDistribution, delta_shares: float,
                       vwap: float, sigma_buckets: float): ...
    def apply_redemption(self, chips: ChipDistribution, delta_shares: float, current_price: float): ...
```

### 实现

```python
class RedemptionCorrector:
    def apply_creation(self, chips, delta_shares, vwap, sigma_buckets):
        """sigma_buckets 由调用方基于日内波动动态计算"""
        idx = chips.price_to_index(vwap)
        sigma_buckets = max(float(sigma_buckets), 2.0)  # 下限 2 桶
        spread = int(np.ceil(3.0 * sigma_buckets))
        lo = max(0, idx - spread)
        hi = min(len(chips.chips), idx + spread + 1)
        offsets = np.arange(lo, hi) - idx
        weights = np.exp(-0.5 * (offsets / sigma_buckets)**2)
        weights /= weights.sum()
        chips.chips[lo:hi] += delta_shares * weights

    def apply_redemption(self, chips, delta_shares, current_price):
        delta_shares = abs(delta_shares)
        price_grid = chips.get_price_grid()
        returns = (current_price - price_grid) / np.maximum(price_grid, 1e-6)
        g = 1.0 + np.maximum(returns, 0)
        w = chips.chips * g
        total = w.sum()
        if total > 0:
            chips.chips = np.maximum(chips.chips - delta_shares * (w / total), 0)
```

***

## 9. Module E: 布朗扩散

**文件**: `modules/diffusion.py`\
**职责**: 日终对筹码分布施加高斯卷积，模拟信息熵随时间增加。

### 算法

$$S\_{t+1}(p) = S\_t(p) \* G(0, \sigma\_{diff}^2)$$

$$\sigma\_{diff} = k\_{diff} \times ATR(10)$$

$k\_{diff} = 0.1$（推荐值）

卷积后保持总量守恒（归一化）。

### 接口与实现

```python
from scipy.ndimage import gaussian_filter1d

def apply_brownian_diffusion(chips: ChipDistribution, atr: float, k_diff: float = 0.08):
    sigma_price = k_diff * atr
    sigma_buckets = max(sigma_price / chips.bucket_size, 0.5)
    total_before = chips.chips.sum()
    chips.chips = gaussian_filter1d(chips.chips, sigma=sigma_buckets)
    total_after = chips.chips.sum()
    if total_after > 0:
        chips.chips *= total_before / total_after
```

**注意**: 仅在"每日批量模式"的日终执行，盘中实时模式不执行。

***

## 10. Module F: 指标输出

**文件**: `modules/indicators.py`\
**职责**: 从筹码分布计算三类输出指标。

### 10.1 获利盘比例

$$\text{ProfitRatio} = \frac{\sum\_{p \leq P\_t} S(p)}{\sum\_p S(p)} \times 100%$$

```python
def calc_profit_ratio(chips: ChipDistribution, current_price: float) -> float:
    idx = min(chips.price_to_index(current_price), len(chips.chips) - 1)
    profitable = chips.chips[:idx + 1].sum()
    total = chips.chips.sum()
    return (profitable / total * 100) if total > 0 else 0.0
```

### 10.2 筹码密集区（ATR 自适应平滑）

寻找筹码分布的峰值位置，作为支撑/阻力位。

> **ATR 自适应平滑**（红队审计改进）
>
> 高波动时峰值更宽、需要更大 σ 才能找到有意义的密集区。
> 当传入 `atr` 和 `bucket_size` 时，σ 自动计算为：
> $$\sigma = \max(5.0,\ 0.2 \times ATR / \text{bucket\_size})$$
> 不传 `atr` 时回退到默认 `smooth_sigma=50.0`。

```python
from scipy.signal import find_peaks

def find_dense_zones(chips: ChipDistribution, current_price: float,
                     top_n: int = 3, smooth_sigma: float = 50.0,
                     atr: float = None, bucket_size: float = None) -> list[dict]:
    """
    返回: [
        {"price": 1.05, "density": 0.15, "type": "support"},
        {"price": 1.12, "density": 0.10, "type": "resistance"},
    ]
    """
    # ATR 自适应平滑：sigma = max(5, 0.2 * ATR / bucket_size)
    sigma = float(smooth_sigma)
    if atr is not None and bucket_size is not None and atr > 0 and bucket_size > 0:
        sigma = max(5.0, 0.2 * atr / bucket_size)

    smoothed = gaussian_filter1d(chips.chips, sigma=sigma)
    peaks, _ = find_peaks(smoothed, height=smoothed.max() * 0.1)
    
    results = []
    total = smoothed.sum()
    for p_idx in peaks:
        price = chips.index_to_price(p_idx)
        density = smoothed[p_idx] / total if total > 0 else 0
        zone_type = "support" if price < current_price else "resistance"
        results.append({"price": price, "density": density, "type": zone_type})
    
    results.sort(key=lambda x: x["density"], reverse=True)
    return results[:top_n]
```

### 10.3 获利盘比例 EMA 平滑（新增）

> **PR EMA 双尺度平滑**（红队审计改进）
>
> 原始 PR 值受盘中价格噪声影响抖动剧烈。新增 `SmoothedPRTracker` 提供双尺度 EMA：
> - **短期 EMA**（span=30 ≈ 1.5分钟）：用于盘中决策
> - **长期 EMA**（span=120 ≈ 6分钟）：用于趋势判断
>
> 每 ETF 独立维护状态，新交易日自动 `reset()`。

```python
class SmoothedPRTracker:
    """获利盘比例 EMA 平滑器"""
    def __init__(self, span_short: int = 30, span_long: int = 120):
        self.ema_short = None
        self.ema_long = None
        self._alpha_s = 2.0 / (span_short + 1)
        self._alpha_l = 2.0 / (span_long + 1)

    def update(self, raw_pr: float) -> dict:
        if self.ema_short is None:
            self.ema_short = raw_pr
            self.ema_long = raw_pr
        else:
            self.ema_short += self._alpha_s * (raw_pr - self.ema_short)
            self.ema_long  += self._alpha_l * (raw_pr - self.ema_long)
        return {
            "pr_raw": raw_pr,
            "pr_ema_short": round(self.ema_short, 4),
            "pr_ema_long":  round(self.ema_long, 4),
        }

    def reset(self):
        self.ema_short = None
        self.ema_long = None
```

### 10.4 ASR 因子

$$ASR = \frac{\sum\_{|p - P\_t| \leq k \cdot ATR} S(p)}{\sum\_p S(p)}$$

```python
def calc_asr(chips: ChipDistribution, current_price: float,
             atr: float, k: float = 1.0) -> float:
    lo = max(0, chips.price_to_index(current_price - k * atr))
    hi = min(len(chips.chips) - 1, chips.price_to_index(current_price + k * atr))
    active = chips.chips[lo:hi + 1].sum()
    total = chips.chips.sum()
    return (active / total) if total > 0 else 0.0
```

***

## 11. Module G: 筹码引擎主控

**文件**: `engine.py`\
**职责**: 编排所有模块，提供三种执行模式的统一入口。

### 核心迭代方程

每收到一个 L1 快照，执行：

$$S\_t(p) = S\_{t-1}(p) \times (1 - \text{Turnover}(p)) + V\_{MaxEnt}(p) \times \text{Volume}\_t$$

### 接口

```python
class ETFChipEngine:
    def __init__(self, config: dict): ...
    
    # 冷启动
    def cold_start(self, etf_code: str, daily_df: pd.DataFrame) -> ChipDistribution: ...
    
    # 处理单个快照（盘中实时模式调用）
    def process_snapshot(self, etf_code: str, snapshot: dict) -> dict: ...

    # 处理一天的全部快照（每日批量模式调用）
    def process_daily(self, etf_code: str, snapshots: pd.DataFrame,
                      shares_today: float, shares_yesterday: float,
                      atr: float) -> dict: ...
    
    # 获取当前指标
    def get_indicators(self, etf_code: str, current_price: float,
                       atr: float) -> dict: ...
    
    # 持久化
    def save_state(self, etf_code: str, path: str): ...
    def load_state(self, etf_code: str, path: str): ...
```

### 实现

```python
class ETFChipEngine:
    def __init__(self, config: dict):
        self.config = config
        self.maxent = MaxEntSolver(
            max_iter=int(config.get('maxent_max_iter', 20)),
            tol=float(config.get('maxent_tol', 1e-8)),
        )
        self.turnover = TurnoverModel(alpha=config.get('alpha', 0.5))
        self.redemption = RedemptionCorrector()
        self.chips: Dict[str, ChipDistribution] = {}
        self.iopv: Dict[str, IOPVCalculator] = {}
        self._pr_trackers: Dict[str, SmoothedPRTracker] = {}  # 每 ETF 独立 PR 平滑器

    def process_snapshot(self, etf_code: str, snapshot: Snapshot) -> dict:
        """
        盘中实时模式：处理单个 L1 快照
        
        入口校验（红队审计改进 #4, #8）：
        - total_shares <= 0 → 抛 ValueError
        - volume < 0 → 抛 ValueError
        
        > **volume 语义声明**（v2 审计 P2c）
        >
        > snapshot.volume 必须为**增量语义**（本快照期间的新增成交量），
        > 不得为当日累计值。realtime.py 中的 `_EtfAccumulator`
        > 负责将 XtQuant 累计量转换为增量。
        """
        chips = self.chips[etf_code]

        # ─── 入口校验 ───
        if chips.total_shares <= 0:
            raise ValueError(f"{etf_code}: total_shares={chips.total_shares} <= 0")
        if snapshot.volume < 0:
            raise ValueError(f"{etf_code}: snapshot.volume={snapshot.volume} < 0")
        if snapshot.volume == 0:
            return self.get_indicators(etf_code, snapshot.close, None)

        high, low = snapshot.high, snapshot.low

        # ─── v2 审计 P0: 做市/套利成交折扣 ───
        mm_ratio = float(self.config.get('mm_ratio', 0.0))
        volume_eff = snapshot.volume * (1.0 - mm_ratio)

        vwap = snapshot.amount / snapshot.volume  # VWAP 用原始量（金额/成交量）

        # ─── 溢折价 + IOPV coverage 衰减（改进 #7）───
        premium = 0.0
        if etf_code in self.iopv:
            raw_premium = self.iopv[etf_code].get_premium_rate(snapshot.close)
            coverage = self.iopv[etf_code].get_coverage()
            premium = raw_premium * coverage  # coverage 不足时自动衰减

        # ─── MaxEnt 求解（三约束 + δ cubic 偏度，改进 #1）───
        grid = np.arange(low, high + chips.bucket_size, chips.bucket_size)
        if len(grid) < 2:
            grid = np.array([snapshot.close])
        v_dist = self.maxent.solve(
            grid, vwap,
            premium_rate=premium,
            delta_max=float(self.config.get('delta_max', 2.0)),
            premium_sensitivity=float(self.config.get('premium_sensitivity', 0.0005)),
        )

        # ─── 衰减旧筹码（使用折扣后有效成交量）───
        full_grid = chips.get_price_grid()
        base_tr = volume_eff / chips.total_shares
        tr = self.turnover.calculate_turnover(full_grid, snapshot.close, base_tr)
        chips.chips *= (1.0 - np.clip(tr, 0, 1))

        # ─── 注入新筹码（使用折扣后有效成交量）───
        new_vol = v_dist * volume_eff
        indices = np.array([chips.price_to_index(p) for p in grid])
        valid = (indices >= 0) & (indices < len(chips.chips))
        np.add.at(chips.chips, indices[valid], new_vol[valid])

        # ─── 指标输出 + PR EMA 平滑（改进 #2）───
        result = self.get_indicators(etf_code, snapshot.close, None)
        if etf_code not in self._pr_trackers:
            self._pr_trackers[etf_code] = SmoothedPRTracker(
                span_short=int(self.config.get('pr_ema_span_short', 30)),
                span_long=int(self.config.get('pr_ema_span_long', 120)),
            )
        pr_ema = self._pr_trackers[etf_code].update(result['profit_ratio'])
        result['profit_ratio_ema_short'] = pr_ema['pr_ema_short']
        result['profit_ratio_ema_long']  = pr_ema['pr_ema_long']
        return result

    def process_daily(self, etf_code: str, snapshots: pd.DataFrame,
                      shares_today: float, shares_yesterday: float,
                      atr: float, trade_date: date | None = None) -> dict:
        """每日批量模式：处理一天全部快照"""
        if etf_code not in self.chips:
            self.cold_start(etf_code, snapshots)

        # v3 终审 C3: 日期连续性校验
        data_gap_days = 0
        if trade_date is not None:
            last_td = self._last_trade_dates.get(etf_code)
            if last_td is not None:
                gap = (trade_date - last_td).days
                if gap > 5:  # 超过 5 日历日（≈3 个交易日以上）
                    data_gap_days = gap
                    warnings.warn(f"{etf_code}: 跨越 {gap} 天，将按 gap 倍数执行额外扩散")
            self._last_trade_dates[etf_code] = trade_date

        # 新交易日重置 PR 平滑器
        if etf_code in self._pr_trackers:
            self._pr_trackers[etf_code].reset()

        # Step 1: 逐快照迭代
        for _, snap in snapshots.iterrows():
            self.process_snapshot(etf_code, snap.to_dict())

        # Step 2: 申赎修正
        delta = shares_today - shares_yesterday
        if abs(delta) > 0:
            last = snapshots.iloc[-1]
            day_vwap = snapshots['amount'].sum() / max(snapshots['volume'].sum(), 1)
            if delta > 0:
                # 大额申购 σ 加宽保护（改进 #6）
                daily_range = float(snapshots['high'].max()) - float(snapshots['low'].min())
                creation_sigma_k = float(self.config.get('creation_sigma_k', 0.2))
                sigma_buckets = creation_sigma_k * daily_range / self.chips[etf_code].bucket_size
                ratio = delta / max(shares_yesterday, 1)
                if ratio > float(self.config.get('creation_large_threshold', 0.05)):
                    sigma_buckets = max(sigma_buckets,
                        float(self.config.get('creation_large_sigma_k', 0.3)) * daily_range / self.chips[etf_code].bucket_size)
                self.redemption.apply_creation(self.chips[etf_code], delta, day_vwap, sigma_buckets=sigma_buckets)
            else:
                self.redemption.apply_redemption(self.chips[etf_code], delta, last['close'])
            self.chips[etf_code].total_shares = shares_today

        # Step 3: 日终布朗扩散（含跨日 gap 补偿）
        diffusion_rounds = max(1, data_gap_days // 2) if data_gap_days > 0 else 1
        for _ in range(diffusion_rounds):
            apply_brownian_diffusion(self.chips[etf_code], atr)

        result = self.get_indicators(etf_code, current_price=snapshots.iloc[-1]['close'], atr=atr)
        if data_gap_days > 0:
            result['data_gap_days'] = data_gap_days
        return result

    def get_indicators(self, etf_code: str, current_price: float,
                       atr: float = None) -> dict:
        chips = self.chips[etf_code]
        bucket_size = float(self.config.get('bucket_size', 0.001))
        result = {
            "etf_code": etf_code,
            "profit_ratio": calc_profit_ratio(chips, current_price),
            "dense_zones": find_dense_zones(chips, current_price,
                                            atr=atr, bucket_size=bucket_size),
        }
        if atr is not None:
            result["asr"] = calc_asr(chips, current_price, atr)
        return result

    def save_state(self, etf_code: str, path: str):
        """持久化筹码分布 + PR EMA 状态（v3 终审 C1）"""
        self.chips[etf_code].save(path)
        # PR EMA sidecar (.ema.json)
        tracker = self._pr_trackers.get(etf_code)
        if tracker is not None and tracker.ema_short is not None:
            ema_path = Path(path).with_suffix('.ema.json')
            ema_path.write_text(json.dumps({
                'ema_short': tracker.ema_short,
                'ema_long': tracker.ema_long,
            }))

    def load_state(self, etf_code: str, path: str):
        """加载筹码分布 + PR EMA 状态（v3 终审 C1）"""
        self.chips[etf_code] = ChipDistribution.load(path, etf_code)
        ema_path = Path(path).with_suffix('.ema.json')
        if ema_path.exists():
            ema_data = json.loads(ema_path.read_text())
            tracker = self._get_pr_tracker(etf_code)
            tracker.ema_short = ema_data['ema_short']
            tracker.ema_long = ema_data['ema_long']
```

***

## 12. 参数速查表

所有可调参数集中在 `config.py`。以下为经过逐一推导的实盘参数：

```python
# config.py
CONFIG = {
    # ─── 数据结构 ───
    "bucket_size": 0.001,         # 价格桶精度（元），等于 ETF 最小变动单位

    # ─── MaxEnt 求解器 ───
    # maxent_max_iter=20, maxent_tol=1e-8 已冻结为 ETFChipEngine 类常量（v3 终审 S5）
    "delta_max": 2.0,             # δ 偏度上限（tanh 饱和值）
    "premium_sensitivity": 0.0005,# tanh 死区宽度（≈5bps），premium 在 ±5bps 内自然衰减为零

    # ─── 获利盘 EMA 平滑（新增）───
    "pr_ema_span_short": 30,      # 短期 EMA span（≈1.5分钟 @3秒快照）
    "pr_ema_span_long": 120,      # 长期 EMA span（≈6分钟 @3秒快照）

    # ─── 做市商折扣（v2 审计新增）───
    "mm_ratio": 0.30,             # 做市/套利成交占比折扣（有效量 = volume × (1-mm_ratio)）

    # ─── 换手衰减（前景理论混合模型）───
    "alpha": 0.5,                 # 前景理论权重
    "kappa1": 1.5,                # 获利区卖出加速系数
    "kappa2": 1.5,                # 浅套区损失厌恶强度
    # kappa3=0.5 已冻结为 ETFChipEngine 类常量（v3 终审 S5）

    # ─── 申赎修正 ───
    "creation_sigma_k": 0.2,       # 申购注入 σ = creation_sigma_k × 日内极差
    "creation_large_threshold": 0.05,  # 大额申购阈值（占总份额比）
    "creation_large_sigma_k": 0.3,     # 大额申购时强制加宽 σ 系数

    # ─── 冷启动（审计后新增再校准）───
    "cold_start_lookback": 60,    # 冷启动回看天数
    "cold_start_decay": 0.95,     # 冷启动衰减因子
    "tail_recalibrate_atr_k": 2.0,# 尾部再校准阈值 = atr_k × ATR

    # ─── 布朗扩散 ───
    "k_diff": 0.08,               # 扩散强度 = k_diff × ATR

    # ─── ASR ───
    "asr_k": 1.0,                 # 活跃筹码范围 = k × ATR

    # ─── 存储路径 ───
    "snapshot_dir": "data/snapshots",
    "chip_snapshot_dir": "data/chip_snapshots",
    "holdings_dir": "data/holdings",
}
```

### 参数推导依据

| 参数                         | 值           | 推导逻辑                                                                                                      |
| -------------------------- | ----------- | --------------------------------------------------------------------------------------------------------- |
| `bucket_size`              | **0.001**   | ETF 最小变动单位，不可改                                                                                            |
| `delta_max`                | **2.0**     | δ 偏度上限。tanh 饱和后 δ=2.0，在 (p'-0.5)^3 基底上产生显著但不极端的偏度。与旧 γ_max 数值一致但语义不同 |
| `premium_sensitivity`      | **0.0005**  | ≈5bps。tanh(pr/0.0005) 在 ±5bps 内近似线性衰减为零，提供自然死区。ETF 正常盘中噪声约 1-3bps，被死区过滤 |
| `pr_ema_span_short`        | **30**      | 30×3秒=1.5分钟。捕捉盘中短线情绪变化，同时滤除逐快照噪声 |
| `pr_ema_span_long`         | **120**     | 120×3秒=6分钟。接近一个波段级别的趋势窗口，用于判断获利盘是否持续增减 |
| `mm_ratio`                 | **0.30**    | 热门 ETF 做市/套利成交占比约 30-50%。0.30 为中间值，可按日均成交量分级（>10亿: 0.40, 3-10亿: 0.30, <3亿: 0.15） |
| `alpha`                    | **0.5**     | 热门 ETF 交易者构成：做市商+套利≈50%（理性，无行为偏差）+ 散户+机构方向性交易≈50%（有行为偏差）                                                  |
| `kappa1`                   | **1.5**     | 获利 10% 时卖出意愿仅增加 7%（受 α=0.5 调节后）。偏温和，符合短线动量追涨者"不急于了结"的心理                            |
| `kappa2`                   | **1.5**     | ETF 持有者含大量无行为偏差的机构（FOF、保险），损失厌恶弱于个股散户。-5% 浅套时换手仅降低 13%                              |
| `kappa3`                   | **0.5**     | ETF 短期跌超 20% 极罕见（仅极端行情）。保持温和释放即可，非核心参数                                                                    |
| `creation_sigma_k`         | **0.2**     | 申购注入 σ = 0.2 × 日内极差。日内极差 0.03 元时 σ≈6 桶，3σ 截断≈18 桶。下限 2 桶防退化 |
| `creation_large_threshold` | **0.05**    | 单日申购占总份额 >5% 视为大额。行业 ETF 日常申购通常 <2%，5% 已属异常大量资金涌入 |
| `creation_large_sigma_k`   | **0.3**     | 大额申购时 σ 下限提高到 0.3×极差，比常规 0.2 宽 50%。反映大资金建仓更分散的特点 |
| `cold_start_decay`         | **0.95**    | 半衰期 ≈ 13.5 天，与 1-10 天持仓周期匹配。60 天前数据权重衰减至 5%                                  |
| `cold_start_lookback`      | **60**      | 3 个月历史足够建立 baseline。配合 decay=0.95，真正有效数据集中在近 30 天                                                         |
| `tail_recalibrate_atr_k`   | **2.0**     | ±2×ATR 外的筹码按指数衰减。比 3×ATR 更激进，更快清除冷启动三角分布在远端的"幽灵筹码" |
| `k_diff`                   | **0.08**    | ⚠️ 相变区间 [0.10, 0.14] 内密集区数量骤变，务必避开。σ=0.08×ATR，日终微量模糊，10 天后累积≈5 桶 |
| `asr_k`                    | **1.0**     | ±1×ATR 覆盖典型日内波动。扩大到 2.0 会纳入过多远端筹码，稀释信号                                                                |
