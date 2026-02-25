
> **角色**：你是一位严谨的量化系统工程师。你的任务是根据退出策略规格书和验证套件，在已有 `core/` 合约层之上实现 `exit/` 模块。
> **核心原则**：规格书是唯一真相源。任何逻辑不在规格书中 = 不实现。任何规格书中的逻辑 = 必须实现，不可省略。

---

## 1. 你必须阅读的文档（按优先级）

请在写任何代码之前，**逐字阅读**以下文档：

1. **策略规格书**：`.trae/quantitative strategy/exit_strategy_specification.md` v4.1（736 行）
2. **实现验证套件**：`.trae/quantitative strategy/exit_implementation_verification.md` v1.0（647 行）
3. **已有合约层**：`core/` 目录下所有文件（不可修改，只消费）
4. **参考实现**：`entry/` 目录（同项目的入场策略，参考其代码风格和模式）

---

## 2. 你需要创建的文件（全部在 `exit/` 目录下）

遵循验证文档 §5.2 的 5 阶段编码顺序。每个文件的职责已明确定义，不要合并、拆分或重命名。

### 阶段 1 — 信号计算（离线，可独立测试）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `signals/s_chip.py` | DPC 3-Tier 渐进式筹码恶化 [0,1] | §4.1 | 1-7 |
| `signals/s_sentiment.py` | LLM 情绪二值信号 {0,1} | §4.2 | 1-7 |
| `signals/s_diverge.py` | RSI背离 + ADX拐头 + 缩量新高 {0,1} | §4.3 | 1-7 |
| `signals/s_time.py` | 时间停滞二值信号 {0,1} | §4.4 | 1-7 |
| `scoring.py` | Score_soft = 4维加权求和 | §3.1-3.2 | 1-7 |
| `constants.py` | 全部硬编码常量 | 全文 | — |
| `types.py` | 数据类型定义 | — | — |

### 阶段 2 — Chandelier Stop

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `chandelier.py` | ATR Wilder EMA(12) + Stop = HH − k×ATR + k 三档收紧 | §2.1, §2.5 | 8-11 |

### 阶段 3 — 救生衣机制

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `lifeboat.py` | 70/30 分仓 + 冷却期(30交易分钟,排除午休) + 6条件回补 + 超紧止损 | §2.1 | 12-17 |

### 阶段 4 — Layer 1 / Layer 2 判定 + 执行

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `layer1.py` | 硬止损判定(price < Stop) + 跳空保护(§2.2) + 死水区(§2.3) + T+0熔断(§2.4) | §2 | 18-22 |
| `layer2.py` | 预警减仓(Score_soft ≥ 0.9) → REDUCE_50 + k收紧至1.5 | §3 | 3-6 |
| `exit_fsm.py` | 退出状态机 + Mutex + 崩溃恢复 + pending队列 | §5, §8 | 全部 |

### 阶段 5 — 日志与监控

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `exit_logger.py` | JSONL 决策日志(Layer1/Layer2/Lifeboat/Rejected) | V-§4 | — |
| `data_health.py` | 外部数据时效性校验 + 3态降级 + 告警 | §3.3 | 11 |

---

## 4. 代码风格要求（必须与 entry/ 一致）

```python
# 1. 每个文件第一行
from __future__ import annotations

# 2. 类型注解：全部使用，包括返回值
def compute_s_chip(dpc_window: list[float], profit_ratio: float) -> float:

# 3. 防御性编程：所有外部输入显式转型
close_t = float(bars[t].close)  # 不是 bars[t].close

# 4. 断言使用 AssertionError，不用 assert 语句（生产环境 -O 会跳过 assert）
if score < 0:
    raise AssertionError(f"score negative: {score}")

# 5. 不可变数据类型使用 frozen=True
@dataclass(frozen=True)
class ExitSignals:
    s_chip: float
    s_sentiment: float
    ...

# 6. 使用 core/ 的工具，不要自己实现
from core.price_utils import tick_floor, tick_ceil, align_order_price
from core.state_manager import StateManager

# 7. 导入入口
from .constants import LAYER2_THRESHOLD, K_NORMAL, K_CHIP_DECAY, K_REDUCED
```

---

## 6. 验收场景（22 个，必须全部通过）

从验证套件 Part 2 提取。写完代码后，用这些场景自检。

### Layer 2 评分（场景 1-7）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 1 | S_chip=0, S_sent=0, S_div=0, S_time=1 | Score=0.4, 不触发 |
| 2 | S_chip=0.5, S_sent=0, S_div=0, S_time=1 | Score=0.75, 不触发 |
| 3 | S_chip=0.5, S_sent=1, S_div=0, S_time=0 | Score=1.05, ✅触发 |
| 4 | S_chip=0, S_sent=1, S_div=1, S_time=0 | Score=1.20, ✅触发 |
| 5 | S_chip=0.5, S_sent=0, S_div=1, S_time=1 | Score=1.25, ✅触发 |
| 6 | S_chip=1.0, S_sent=1, S_div=1, S_time=1 | Score=2.30, ✅触发 |
| 7 | 全为 0 | Score=0.00 (精确等于0) |

### Layer 1 触发后行为（场景 8-11）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 8 | price < Stop, Score=0.7 | 卖 100% sellable_qty |
| 9 | price < Stop, Score=0, lifeboat_used=False | 卖 70%, 保留 30% |
| 10 | price < Stop, Score=0, lifeboat_used=True, sellable=30% | 卖 30%, pending 70% |
| 11 | price < Stop, S_chip=UNAVAILABLE | 100% 清仓，无救生衣 |

### 救生衣回补（场景 12-17）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 12 | sell_time=10:00, now=10:20, price>Stop | 不回补（20分钟 < 30分钟） |
| 13 | sell_time=10:00, now=10:30, price>Stop, Score=0 | ✅回补 |
| 14 | sell_time=11:20, now=13:15 | 交易分钟=10+15=25, 不回补 |
| 15 | sell_time=11:20, now=13:20 | 交易分钟=10+20=30, ✅可回补 |
| 16 | sell_time=14:00, now=14:35 | 不回补（14:35 > 14:30） |
| 17 | price=跌停价×1.01, 冷却满, Score=0 | 不回补（< 跌停价×1.02） |

### 跳空/死水区/熔断（场景 18-22）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 18 | 09:25 价格=Stop×0.96 | 立即清仓，无救生衣 |
| 19 | 13:00 价格=Stop×0.95 | 立即清仓，无救生衣 |
| 20 | days_held=12, return=+0.8% | 全部清仓（死水区） |
| 21 | days_held=8, return=+0.8% | 不触发（< 10天） |
| 22 | T+0 亏损≥0.3%, 同时 price<Stop | T+0暂停，Layer 1 正常 |

---

## 7. 运行时断言（必须嵌入代码）

以下断言是验证套件 Part 3 的核心子集，必须在对应逻辑点嵌入运行时检查：

```python
# Score_soft 值域
assert 0 <= score_soft <= 2.3

# S_chip 只能输出离散阶梯值
assert s_chip in (0, 0.3, 0.5, 0.7, 1.0)

# Layer 1 触发时价格必须破 Stop
if layer1_triggered:
    assert last_price < stop_price

# 救生衣启用时 Score 必须为 0、首次使用、数据完整
if lifeboat_activated:
    assert score_soft == 0
    assert not lifeboat_used
    assert all(h != "UNAVAILABLE" for h in data_health.values())

# 卖出数量不超过可用余额
assert sell_qty <= sellable_qty

# k 值只能是合法三档之一
assert k_value in (2.8, 2.38, 1.5)

# 已减仓则 k 必须为 1.5
if reduced:
    assert k_value == 1.5
```

---

## 8. 决策日志格式（JSONL）

每次 Layer 1 / Layer 2 / 救生衣 / 拒绝操作都必须写日志。格式参见验证套件 §4.1-4.4。

关键字段：
- `type`: `LAYER1_TRIGGERED` / `LAYER2_REDUCE` / `LIFEBOAT_BUYBACK` / `LIFEBOAT_BUYBACK_REJECTED`
- `trigger`: 触发条件快照 (price, stop, k, HH, ATR)
- `context`: 决策上下文 (score_soft, data_health, lifeboat_used)
- `decision`: 执行动作 (FULL_EXIT / LIFEBOAT_70_30 / REDUCE_50 / HOLD)
- `order`: 挂单详情 (qty, price)
- `conditions`: 回补条件逐项检查结果 (a_cooldown, b_price, c_score, d_data, e_dead_cat, f_cutoff)

---

## 9. 关键易错点（从入场策略审计中总结的教训）

1. **trial vs confirm 状态分派**：order.remark 区分 "TRIAL" / "CONFIRM"，不要写反
2. **交易日 vs 日历天**：冷却期、持仓天数全部使用 `core.time_utils` 的交易日函数
3. **数据可用性**：`snapshot.iopv is None` 表示降级，不要硬崩溃
4. **Mutex 范围**：`apply_confirm_action` 等状态修改操作必须在 `with self._mutex:` 内
5. **near_miss 归档**：传入真实值而非 0.0

---

## 10. 最终交付检查清单

完成编码后，请逐一确认：

- [ ] 22 个验收场景全部通过
- [ ] 所有运行时断言已嵌入
- [ ] Score_soft = 0 使用 `==` 精确比较（无浮点容差）
- [ ] Layer 1 卖出价用 `Bid1 × 0.98`，Layer 2 用 `Bid1`（无折扣）
- [ ] 冷却期计时排除了 11:30-13:00 午休
- [ ] k 值收紧是单调递减（2.8 → 2.38 → 1.5）
- [ ] 所有卖出限制在 sellable_qty
- [ ] locked_qty 写入 PendingSell，次日无条件执行
- [ ] UNAVAILABLE 信号在 Layer 1 触发时 → 无救生衣
- [ ] 每个文件第一行 `from __future__ import annotations`
- [ ] `exit/__init__.py` 包含 `__all__` 列表
- [ ] `exit/README.md` 描述模块结构和依赖
