
> **角色**：你是一位严谨的量化系统工程师。你的任务是根据仓位管理策略规格书和验证套件，在已有 `core/` 合约层之上实现 `position/` 模块。
> **核心原则**：规格书是唯一真相源。任何逻辑不在规格书中 = 不实现。任何规格书中的逻辑 = 必须实现，不可省略。

---

## 1. 你必须阅读的文档（按优先级）

请在写任何代码之前，**逐字阅读**以下文档：

1. **策略规格书**：`.trae/quantitative strategy/position_management_specification.md` v2.1（1205 行）
2. **实现验证套件**：`.trae/quantitative strategy/position_management_verification.md` v1.1（694 行）
3. **已有合约层**：`core/` 目录下所有文件（**不可修改**，只消费）
4. **已有退出模块**：`exit/` 目录（已测试通过，需与之联动。参考其代码风格和模式）
5. **已有入场模块**：`entry/` 目录（参考代码风格，不可修改）

---

## 2. 你需要创建的文件（全部在 `position/` 目录下）

遵循验证文档 §5.2 的 6 阶段编码顺序。每个文件的职责已明确定义，不要合并、拆分或重命名。

### 阶段 1 — 核心计算（无实时依赖，可独立测试）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `constants.py` | 全部硬编码常量（ATR下限、stop_multiplier、pyramid比例、熔断阈值、相关性阈值等） | 全文 | — |
| `types.py` | 数据类型定义（PositionSizing, ScaleSignal, ScalePrerequisites, T0Decision 等） | V-§5.3 | — |
| `atr_sizing.py` | ATR 风险平价仓位计算器: effective_slot / base_target / scale_amt / trial_amt | §3.4 | 1-6 |
| `correlation.py` | 相关性过滤器: 20日 Pearson ρ ≥ 0.60 互斥 | §3.3 | 25 |

### 阶段 2 — FSM 状态机（依赖阶段 1）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `fsm_transitions.py` | 6 态跃迁矩阵 + 跃迁合法性校验 + S0 清理规则 | §4 | 7-11 |

### 阶段 3 — 加仓引擎（依赖 FSM + L1 数据引擎）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `scale_prerequisites.py` | 6 项加仓前提条件检查（AND 关系） | §5.1 | 12-14 |
| `scale_signal.py` | 4 条件共振判定（趋势+回调+筹码+微观止跌） | §5.2 | 15-16 |
| `scale_executor.py` | 加仓执行（Bid1 限价单 + 收盘撤单 + 成交后状态更新） | §5.3 | 16 |

### 阶段 4 — T+0 子系统（依赖 FSM + CashManager）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `t0_controller.py` | T+0 激活/冻结状态机 + 额度计算 + VWAP σ带方向决策 + 极端行情冻结 | §7.2-7.6 | 17-19 |
| `t0_mutex.py` | T+0 竞态 Mutex 三场景处理 | §7.7 | 20-21 |

### 阶段 5 — 风控层（依赖全部前置模块）

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `circuit_breaker.py` | 盘中软熔断(HWM×0.92) + 盘中硬熔断(HWM×0.90) + 盘后硬熔断 + 冷静期 + 解锁 | §3.2 | 22-24 |
| `rebuild.py` | 减仓后回补逻辑: 6条件判定(AND) + 全新建仓 + 最多1次/波段 | §6 | 11, 27 |

### 阶段 6 — 日志与主编排

| 文件 | 职责 | Spec 章节 | 验证场景 |
|:---|:---|:---:|:---:|
| `position_logger.py` | JSONL 决策日志: FSM跃迁 + 加仓信号评估 + T+0操作 + 熔断事件 | V-§4 | — |
| `position_fsm.py` | 仓位管理主状态机（编排所有子模块，对外暴露统一 API） | §12.3 | 全部 |
| `__init__.py` | 包初始化 + `__all__` | — | — |

---

## 4. 代码风格要求（必须与 exit/ 一致）

```python
# 1. 每个文件第一行
from __future__ import annotations

# 2. 类型注解：全部使用，包括返回值
def compute_effective_slot(*, current_nav: float, atr_pct: float) -> float:

# 3. 防御性编程：所有外部输入显式转型
nav = float(current_nav)  # 不是直接用 current_nav

# 4. 断言使用 AssertionError，不用 assert 语句（生产环境 -O 会跳过 assert）
if effective_slot > 70000:
    raise AssertionError(f"effective_slot={effective_slot} 超过7万硬上限")

# 5. 不可变数据类型使用 frozen=True
@dataclass(frozen=True)
class PositionSizing:
    effective_slot: float
    base_target: float
    ...

# 6. 使用 core/ 的工具，不要自己实现
from core.price_utils import tick_ceil, align_order_price
from core.cash_manager import CashManager
from core.enums import FSMState

# 7. 导入入口
from .constants import STOP_MULTIPLIER, ATR_PCT_FLOOR, SLOT_MAX
```

---

## 5. 核心公式与阈值速查

### 5.1 ATR 风险平价（§3.4）— 最关键的公式

```python
# ⚠️ 所有金额的源头。算错 = 全线偏移。
risk_budget = clamp(current_nav * 0.02, 2500, 6000)
atr_pct = max(ATR_20 / close_price_T_minus_1, 0.015)  # 下限 1.5%
effective_slot = min(risk_budget / (atr_pct * 3.5), 70000)  # stop_multiplier=3.5 固定

# 联动金额
base_target  = effective_slot * 0.71   # 底仓目标
scale_1_amt  = effective_slot * 0.19   # 加仓 #1
scale_2_amt  = effective_slot * 0.10   # 加仓 #2
trial_amt    = base_target * 0.30      # 普通试探仓（强信号用 0.50）
confirm_amt  = base_target - trial_amt # 确认仓
```

### 5.2 FSM 合法跃迁矩阵（§4）— 仅以下 13 条合法

```python
LEGAL_TRANSITIONS = {
    FSMState.S0_IDLE:    [FSMState.S1_TRIAL],
    FSMState.S1_TRIAL:   [FSMState.S2_BASE, FSMState.S0_IDLE],
    FSMState.S2_BASE:    [FSMState.S3_SCALED, FSMState.S5_REDUCED, FSMState.S0_IDLE],
    FSMState.S3_SCALED:  [FSMState.S4_FULL, FSMState.S5_REDUCED, FSMState.S0_IDLE],
    FSMState.S4_FULL:    [FSMState.S5_REDUCED, FSMState.S0_IDLE],
    FSMState.S5_REDUCED: [FSMState.S0_IDLE, FSMState.S4_FULL],  # S4 仅限回补，最多1次
}
```

### 5.3 T+0 激活矩阵（§7.2）

```
S0/S1/S5 → T+0 OFF（无条件）
S2/S3/S4 → T+0 ON 仅当: 浮盈 > 1% AND 日亏 < 0.3% AND t0_frozen == False
T+0 额度 = min(底仓市值 × 20%, available_reserve)
```

### 5.4 加仓前提条件（§5.1）— 全部 6 项 AND

```
a) state ∈ {S2, S3}
b) 浮盈 ≥ 1.5 × ATR_14（注意：这里是 ATR_14，不是 ATR_20）
c) circuit_breaker.triggered == False AND intraday_freeze == False
d) Score_soft < 0.5
e) 上次加仓 ≥ 3 个交易日
f) 加仓后总仓位 ≤ effective_slot
```

### 5.5 加仓信号（§5.2）— 4 条件共振 AND

```
1) KAMA(10) 连续上升 ≥ 2 日 AND Elder Impulse = Green
2) 回调 ≥ 1.0 × ATR_14 且未破 Chandelier Stop
3) 筹码支撑区确认（density 前 30%，价格 ± 0.3×ATR_14 内）
4) 微观止跌: 近30min 缩量30%+ AND 未破支撑下沿 AND 阳线收盘
```

### 5.6 熔断器三级（§3.2）

```
盘中每 3 秒:
  nav_estimate = 持仓市值 + 可用现金
  ≤ HWM × 0.92 → 冻结新开仓（试探/加仓/做T买入），允许卖出
  ≤ HWM × 0.90 → 调用 Layer 1 执行器全清，不可撤销

盘后 15:30:
  current_nav ≤ HWM × 0.90 → 全清 + 5 日冷静期

HWM 更新 = max(HWM, current_nav)，仅盘后 15:30 更新
HWM 任何时候不可被下调
```

### 5.7 回补条件（§6）— 6 项 AND

```
a) S5 减仓后 ≥ 5 个交易日
b) 新筹码密集区形成（≥ 5 日横盘，density 前 20%）
c) 新密集区经受 ≥ 1 次大盘下杀压力测试未破
d) 放量突破上沿（vol_ratio ≥ 1.5）
e) Score_soft == 0（所有预警归零）
f) LLM 情绪 > 50

回补 = 全新建仓：重新计算 ATR / 止损
回补最多 1 次/波段。第二次 L2 → 直接 S5→S0
```

---

## 6. 验收场景（27 个，必须全部通过）

从验证套件 Part 2 提取：

### ATR 风险平价（场景 1-6）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 1 | NAV=200k, ATR_pct=3.58%, stop_mult=3.5 | risk_budget=4000, effective_slot=31,923, base=22,665 |
| 2 | NAV=200k, ATR_pct=1.25%→强制1.5% | effective_slot=76,190→截断70,000, base=49,700 |
| 3 | NAV=150k | risk_budget=3000, 半导体 slot=23,943 |
| 4 | NAV=100k | risk_budget=clamp(2000,2500,6000)=**2500** |
| 5 | NAV=350k | risk_budget=clamp(7000,2500,6000)=**6000** |
| 6 | 比较 mult=2.5 vs 3.5 | 2.5→44,692（偏大！危险） vs 3.5→31,923（正确） |

### FSM 跃迁（场景 7-11）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 7 | 完整生命周期 | S0→S1→S2→S3→S4→S5→S0 |
| 8 | S1 窗口到期 | S1→S0, T+0 不受影响（本来就 OFF） |
| 9 | S1 尝试加仓 | 拒绝，保持 S1 |
| 10 | S4 收到加仓信号 | 拒绝，保持 S4（已满仓） |
| 11 | S5→S4 回补后再次 L2 | 第二次 L2 直接走 S5→S0，不允许再回补 |

### 加仓信号（场景 12-16）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 12 | S2, 浮盈=1.0×ATR_14 | 不评估（前提 b 不满足） |
| 13 | S2, Score_soft=0.55 | 不评估（前提 d 不满足） |
| 14 | S2, 上次加仓 2 天前 | 不评估（前提 e 不满足） |
| 15 | 6 前提✅ + 3/4 条件✅ | 不触发（缺一不可） |
| 16 | 6 前提✅ + 4 条件✅ | 挂 Bid1 限价买单，收盘自动撤 |

### T+0 联动（场景 17-21）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 17 | S2:BASE, 浮盈 1.5%, 日亏 0% | T+0=ON, 额度=min(5万×20%, Reserve) |
| 18 | Layer 2 触发减仓 | T+0 立即 OFF, 撤销所有 T+0 挂单 |
| 19 | 日涨+7%, price > vwap+1.5σ | 冻结反T（HOLD），日志记原因 |
| 20 | 正T 成交 1万 → 5秒后止损触发 | Layer 1 清 sellable, locked写pending, 次日强平 |
| 21 | 反T 卖出 → 止损触发 | 撤接回买单, Layer 1 清剩余, 标良性异常 |

### 熔断器（场景 22-25）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 22 | HWM=200k, nav=183k | 183k ≤ 200k×0.92=184k → 冻结新开仓 |
| 23 | HWM=200k, nav=179k | 179k ≤ 200k×0.90=180k → Layer 1 全清 |
| 24 | 熔断 3 天, 大盘>MA20, 人工 ACK | 拒绝解锁（3<5 天） |
| 25 | 持有 512480, 新标的 588000, ρ=0.72 | 禁止建仓（ρ≥0.60） |

### 边界场景（场景 26-27）

| # | 输入 | 预期 |
|:---:|:---|:---|
| 26 | 正T 买入 14:55, 未能收盘前卖出 | locked_qty→pending, 次日 09:30 跌停价强平 |
| 27 | S5 回补挂单已提交, Score_soft 从 0→0.6 | 立即撤单, 保持 S5, 日志记录 |

---

## 7. 运行时断言（必须嵌入代码）

以下断言是验证套件 Part 3 的核心子集：

```python
# ===== ATR 风险平价 =====
if stop_multiplier < 3.0:
    raise AssertionError(f"stop_multiplier={stop_multiplier} < 3.0，方向性错误")
if not (2500 <= risk_budget <= 6000):
    raise AssertionError(f"risk_budget={risk_budget} 越界")
if effective_slot > 70000:
    raise AssertionError(f"effective_slot={effective_slot} 超过7万硬上限")

# ===== FSM 跃迁合法性 =====
if new_state not in LEGAL_TRANSITIONS.get(current_state, []):
    raise AssertionError(f"非法跃迁: {current_state}→{new_state}")

# ===== 加仓前提校验 =====
# 触发加仓时, 以下断言全部嵌入
if current_state not in (FSMState.S2_BASE, FSMState.S3_SCALED):
    raise AssertionError(f"加仓状态非法: {current_state}")
if score_soft >= 0.5:
    raise AssertionError(f"安全阀: Score_soft={score_soft} ≥ 0.5，禁止加仓")
if days_since_last_scale < 3:
    raise AssertionError(f"加仓间隔仅 {days_since_last_scale} 天 < 3 天")
if projected_total > effective_slot:
    raise AssertionError(f"加仓后 {projected_total} > effective_slot {effective_slot}")

# ===== T+0 状态联动 =====
if position_state in (FSMState.S0_IDLE, FSMState.S1_TRIAL, FSMState.S5_REDUCED):
    if t0_enabled:
        raise AssertionError(f"T+0 在 {position_state} 不应激活")

# ===== 熔断器 =====
if hwm < prev_hwm:
    raise AssertionError(f"HWM 被下调: {hwm} < {prev_hwm}，违反单调递增")
if nav_estimate <= hwm * 0.90:
    if not clearing_in_progress:
        raise AssertionError("盘中熔断未触发清仓")

# ===== 回补限制 =====
if action == "REBUILD" and rebuild_count_this_wave > 0:
    raise AssertionError(f"回补次数={rebuild_count_this_wave}，本波段已用完回补机会")

# ===== 数据口径 =====
# 定仓/回补用 ATR_20，加仓前提浮盈检查用 ATR_14，不得混用
# 加仓挂单价必须 = tick_ceil(Bid1)，不追 Ask
```

---

## 8. 决策日志格式（JSONL）

每次 FSM 跃迁、加仓决策、T+0 操作、熔断事件都必须写日志。格式详见验证套件 §4.1-4.4。

### 8.1 四种日志类型速查

| type | 触发时机 | 关键字段 |
|:---|:---|:---|
| `FSM_TRANSITION` | 每次状态跃迁 | from_state, to_state, trigger, effective_slot, new_avg_cost |
| `SCALE_SIGNAL_EVAL` | 每次加仓信号评估 | prerequisites(6项), signal_conditions(4项), decision, order |
| `T0_OPERATION` | 每次 T+0 操作 | direction, trigger(price/vwap/sigma), order, constraints |
| `CIRCUIT_BREAKER` | 每次熔断事件 | trigger_type(INTRADAY_SOFT/INTRADAY_HARD/POST_CLOSE), hwm, nav, action |

---

## 9. 关键易错点（从退出策略审计中总结的教训）

> ⚠️ 以下每项都是退出模块审计中实际发现的 P0/P1 级错误，必须在仓位管理中规避。

1. **ATR_14 vs ATR_20 混用**：定仓/回补用 `ATR_20`，加仓前提浮盈检查用 `ATR_14`。验证套件 Part 3 有专门断言。
2. **stop_multiplier 方向**：值越大 → 仓位越小 → 越保守。`3.5` 是最终值，不得用 `2.5`。
3. **risk_budget 不可硬编码**：必须从 `current_nav × 0.02` 动态计算。
4. **卖出不取整**：A 股允许零股卖出（odd lot selling），`_round_down_lot` 仅用于**买入**（必须整手）和 Layer 2 主动减仓 50%。全清/止损/救生衣卖出不做取整。
5. **T+0 额度公式**：`min(底仓市值×20%, available_reserve)`，仅两项，**不要**加 `slot_target - current_value`（在 S4 时=0 会导致额度归零）。
6. **日志不可遗漏**：所有 FSM 方法必须在真正执行动作前/后写日志。上一轮审计发现 exit_fsm.py 3 个方法完全没写日志导致影子模式无法运行。
7. **Mutex 范围**：持锁仅保护「写状态 + 提交委托」瞬间（< 1秒），等待确认必须在锁外进行。
8. **pending 重试安全检查**：重试前必须重新验证 FSM 状态/熔断器/前提条件/Slot 空位（验证套件 Part 3 有 5 项断言）。
9. **HWM 单调递增**：任何时候 `HWM = max(HWM, new_nav)`，永远不可下调。
10. **盘中熔断不可撤销**：即使 V 反转导致收盘 nav > HWM×0.90，冷静期照算。但「不可撤销」不等于「不可重试卖出」—— pending_sell_unfilled 当日持续重试。

---

## 10. 与现有模块的接口（§12.3 — 必须实现）

```python
# 入场策略 → 仓位管理
def on_trial_filled(etf_code: str, qty: int, price: float) -> None
def on_confirm_filled(etf_code: str, qty: int, price: float) -> None
def on_entry_failed(etf_code: str) -> None

# 仓位管理 → 退出策略
def get_position_state(etf_code: str) -> FSMState
def get_total_qty(etf_code: str) -> int
def get_sellable_qty(etf_code: str) -> int
def get_t0_frozen(etf_code: str) -> bool

# 退出策略 → 仓位管理
def on_layer2_reduce(etf_code: str, sold_qty: int) -> None
def on_layer1_clear(etf_code: str, sold_qty: int) -> None
def on_lifeboat_rebuy(etf_code: str, rebuy_qty: int) -> None

# 仓位管理 → CashManager
# 使用已有 CashManager.lock_cash(priority=2) / release_cash()
```

---

## 11. 最终交付检查清单

完成编码后，请逐一确认：

- [ ] 27 个验收场景全部通过
- [ ] 所有运行时断言已嵌入
- [ ] `effective_slot` 公式 3 个组件（risk_budget / atr_pct / stop_multiplier）全部正确
- [ ] risk_budget 使用 `clamp(nav×0.02, 2500, 6000)` 动态计算
- [ ] stop_multiplier = 3.5（固定值，不可参数化）
- [ ] ATR_20 用于定仓/回补，ATR_14 用于加仓前提，不混用
- [ ] FSM 跃迁全部经过 `LEGAL_TRANSITIONS` 校验
- [ ] S0 清理保留 `pending_sell_locked`，清除其他临时状态
- [ ] 加仓挂单价 = `tick_ceil(Bid1)`，不追 Ask
- [ ] 加仓挂单收盘自动撤单，不隔夜
- [ ] T+0 在 S0/S1/S5 状态无条件 OFF
- [ ] T+0 额度 = `min(底仓市值×20%, available_reserve)` 仅两项
- [ ] 盘中熔断不可撤销（冷静期照算）但 pending_sell_unfilled 持续重试
- [ ] HWM 单调递增，不可下调
- [ ] 熔断清仓复用退出策略 Layer 1 执行器，不另写
- [ ] 正T locked_qty 遇止损 → pending_sell_locked，次日 09:30 强平
- [ ] 回补最多 1 次/波段，第二次 L2 → S5→S0
- [ ] 买入量取整到 100 份，卖出量不取整（A 股规则）
- [ ] 相关性过滤 ρ ≥ 0.60 互斥
- [ ] 每个文件第一行 `from __future__ import annotations`
- [ ] `position/__init__.py` 包含 `__all__` 列表
- [ ] 全部 4 种 JSONL 日志格式实现且每个决策路径都有日志输出
