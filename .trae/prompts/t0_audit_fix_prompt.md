# T+0 模块代码审计修复指令

> **角色**：你是严谨的量化系统工程师。你的任务是根据本审计报告修复 `t0/` 模块中的全部缺陷。
> **原则**：每个修复必须标注对应的缺陷编号（如 P0-1），修复后需在代码注释中注明 `# FIX P0-1`。
> **配对文档**：修复前必须阅读 `t0_strategy_specification.md` v1.4 和 `t0_implementation_verification.md` v1.0。

---

## 修复顺序

```
阶段 A: P0-1 ~ P0-8（致命缺陷 → 直接导致资金风险）
阶段 B: P1-1 ~ P1-7（逻辑错误 → 策略行为偏离规格书）
阶段 C: P2-1 ~ P2-6（代码质量 → 按需清理）
```

---

## 阶段 A：P0 致命缺陷（8 项）

---

### P0-1 `t0_fsm.py` — Breaker 四层熔断使用硬编码哑值，全部失效

**现状**（L86-96）：
```python
_ = evaluate_breakers(
    inp=BreakerInputs(
        now=now,
        etf_code=code,
        nav=1.0,                    # ← 硬编码！必须用真实 NAV
        t0_daily_pnl=0.0,          # ← 永远 0 → 日熔断永不触发
        pnl_5d=[],                  # ← 空 → 周熔断永不触发
        pnl_30d=[],                 # ← 空 → 月熔断永不触发
        consecutive_loss_count=0,   # ← 永远 0 → 连续亏损永不触发
    )
)
```

**修复要求**：

1. T0Engine 必须新增以下运行时状态字段：
```python
self._t0_daily_pnl: float = 0.0            # 当日 T+0 累计 PnL（CNY）
self._consecutive_loss_count: int = 0       # 连续亏损计数
self._pnl_history: list[float] = []         # 历史每日 PnL，用于滚动熔断
```

2. `evaluate_tick()` 中使用真实值：
```python
_ = evaluate_breakers(
    inp=BreakerInputs(
        now=now,
        etf_code=code,
        nav=float(portfolio_state.nav),     # 真实 NAV
        t0_daily_pnl=self._t0_daily_pnl,
        pnl_5d=self._pnl_history[-5:],
        pnl_30d=self._pnl_history[-30:],
        consecutive_loss_count=self._consecutive_loss_count,
    )
)
```

3. 每次 Round-Trip 闭环后更新：
```python
self._t0_daily_pnl += rt.net_pnl_cny
self._consecutive_loss_count = update_consecutive_loss_count(
    prev_count=self._consecutive_loss_count, net_pnl=rt.net_pnl_cny
)
```

4. 每日盘后 `reset_daily()` 中：
```python
self._pnl_history.append(self._t0_daily_pnl)
self._t0_daily_pnl = 0.0
self._consecutive_loss_count = 0
```

---

### P0-2 `t0_fsm.py` — T0Engine 未集成 OrderManager，信号生成后无法下单

**现状**：T0Engine 中无 `OrderManager` 实例化或调用。`evaluate_tick()` 返回 `T0Signal` 后直接结束，不下单。

**修复要求**：

1. T0Engine `__init__` 新增参数和字段：
```python
from .order_manager import OrderManager, ManagedOrder

def __init__(self, *, ...) -> None:
    ...
    self._order_manager = OrderManager()
```

2. 信号评估后执行下单：
```python
def _execute_signal(self, *, signal: T0Signal, now: datetime) -> Optional[OrderResult]:
    self._order_manager.assert_can_operate()
    
    # 计算下单量（P0-7 / P0-8 一并修复）
    amount = float(signal.amount)
    qty = (int(amount / signal.target_price) // 100) * 100
    if qty <= 0:
        return None
    
    req = OrderRequest(
        etf_code=signal.etf_code,
        side=OrderSide.BUY if signal.signal_type == "VWAP_BUY" else OrderSide.SELL,
        price=signal.target_price,
        quantity=qty,
        order_type=OrderType.LIMIT,
        time_in_force=OrderTimeInForce.DAY,
    )
    return self._order_manager.place_limit_order(
        trading=self._trading, req=req, now=now
    )
```

3. 每日重置：
```python
def reset_daily(self) -> None:
    self._order_manager.reset_daily()
    ...
```

---

### P0-3 `t0_fsm.py` — T0Engine 未集成 CashManager，无资金锁定

**现状**：无 `cash_manager` 参数/属性，下单无资金预留。

**修复要求**：

1. T0Engine 构造函数新增 `cash_manager`:
```python
from core.cash_manager import CashManager
from core.constants import RESERVE_CASH_CAP

def __init__(self, *, ..., cash_manager: CashManager) -> None:
    self._cash_manager = cash_manager
```

2. 下单前锁定资金（先锁后下，失败取消）：
```python
def _execute_signal(self, *, signal: T0Signal, now: datetime) -> Optional[OrderResult]:
    amount = float(signal.amount)
    
    # 1) 检查可用储备金
    available = self._cash_manager.available_reserve(
        reserve_cap=RESERVE_CASH_CAP, safety_buffer=0
    )
    if available < amount:
        return None
    
    # 2) 锁定资金（priority=4, T0_SCALP 最低优先级）
    lock_ok = self._cash_manager.lock_cash(
        priority=4, strategy_name="t0", amount=amount
    )
    if not lock_ok:
        return None
    
    # 3) 下单
    result = self._order_manager.place_limit_order(...)
    
    # 4) 下单失败 → 释放锁定
    if result.order_id <= 0:
        self._cash_manager.release_cash(order_id=0)
        return None
    
    return result
```

3. 订单成交/撤单后释放资金：
```python
self._cash_manager.release_cash(order_id=result.order_id)
```

---

### P0-4 `t0_fsm.py` — 无 Mutex 实现，与 Exit 策略资源竞争

**现状**：未引用 `EXIT_MUTEX`，sellable_qty/locked_qty 操作无锁保护。

**修复要求**：

1. 引入 EXIT_MUTEX：
```python
from exit.exit_fsm import EXIT_MUTEX
```

2. 所有涉及 sellable_qty/locked_qty 的操作必须在锁内：
```python
with EXIT_MUTEX:
    sellable = pos.get_sellable_qty(etf_code)
    # ... 下单操作
```

3. 实现 4 个 Mutex 场景处理方法：
```python
def _handle_mutex_scenario_1(self, *, etf_code: str):
    """正T买入已成交 → Layer1 止损触发"""
    # 市价清 sellable，locked → pending_sell_locked
    # 次日 09:30 强平
    pass

def _handle_mutex_scenario_2(self, *, etf_code: str):
    """反T挂单未成交 → Layer1 止损触发"""
    # 撤接回买单
    pass

def _handle_mutex_scenario_3(self, *, etf_code: str, wait_s: float = 10.0):
    """T+0 传输中 → Layer2 止盈"""
    # 等待 10s 后执行
    pass

def _handle_mutex_scenario_4(self, *, etf_code: str):
    """反T接回已成交 → Layer1 止损"""
    # 同场景 1，lock_reason="reverse_t_buyback_during_stop"
    pass
```

4. T0Engine 需要暴露方法供 Exit 策略调用以通知 Layer1/Layer2 触发：
```python
def on_layer1_stop_triggered(self, *, etf_code: str) -> None:
    """Exit 策略调用此方法通知 T+0 模块 Layer1 已触发"""
    with EXIT_MUTEX:
        orders = self._order_manager.list_orders()
        buy_orders = [o for o in orders if o.side == OrderSide.BUY]
        for o in buy_orders:
            self._order_manager.cancel_order(trading=self._trading, order_id=o.order_id)
        # 检查是否有已成交正T → 场景 1
        # 检查是否有反T接回 → 场景 4
```

---

### P0-5 `t0_fsm.py` — 无 Sweeper 集成，尾盘清理不执行

**现状**：`sweeper.py` 已实现但 T0Engine 从未调用。

**修复要求**：

在 `evaluate_tick()` 的**最开头**检查清道夫：
```python
from .sweeper import execute_sweep

def evaluate_tick(self, *, etf_code: str, now: datetime) -> Optional[T0Signal]:
    # ========= 清道夫优先（最高优先级检查） =========
    canceled = execute_sweep(now=now, trading=self._trading, om=self._order_manager)
    if canceled > 0:
        log_signal(...)  # 日志记录撤单事件
    
    # 14:15 后禁止新买入信号（清道夫已撤完买单）
    # 14:55 后禁止一切新信号
    if now.time() >= time(14, 55):
        return None
    
    # ... 后续信号评估逻辑
```

---

### P0-6 `t0_fsm.py` — 无 Reconciliation 集成，超时订单无对账

**现状**：`reconciliation.py` 已实现但 T0Engine 从未调用。

**修复要求**：

1. T0Engine 新增超时检查方法：
```python
from .reconciliation import confirm_or_reconcile, ReconcileInput
from .t0_logger import log_reconciliation

def _check_order_timeouts(self, *, now: datetime) -> None:
    for order in self._order_manager.list_orders():
        if order.status != OrderStatus.SUBMITTED:
            continue
        elapsed = (now - order.submitted_at).total_seconds()
        if elapsed < T0_TIMEOUT_CONFIRM_S:
            continue
        
        inp = ReconcileInput(now=now, order_id=order.order_id, memory_status=order.status)
        result = confirm_or_reconcile(trading=self._trading, inp=inp)
        log_reconciliation(log_path=self._log_path, r=result)
        
        # 根据 CASE 处理后续：
        # CASE A (FILLED): 更新仓位 + PnL
        # CASE B (PARTIAL): 微型仓位处理
        # CASE C (REJECTED): 释放资金锁定
```

2. 在 `evaluate_tick()` 中每次调用前检查超时：
```python
def evaluate_tick(self, *, etf_code: str, now: datetime) -> Optional[T0Signal]:
    self._check_order_timeouts(now=now)  # 超时对账检查
    execute_sweep(...)                    # 清道夫检查
    # ... 后续逻辑
```

---

### P0-7 `signal_engine.py` — 信号 amount 字段永远 = 0.0

**现状**（L139, L162）：
```python
amount=0.0,  # ← 永远 0！下游不知道下多少钱
```

**修复要求**：

1. `evaluate()` 方法新增 `t0_quota` 参数：
```python
def evaluate(
    self,
    *,
    ...
    t0_quota: float,              # 新增：可用额度
) -> Optional[T0Signal]:
```

2. 计算 amount 并填充：
```python
from .constants import T0_ORDER_AMOUNT_MIN, T0_ORDER_AMOUNT_MAX

amount = min(float(t0_quota), float(T0_ORDER_AMOUNT_MAX))
amount = max(amount, float(T0_ORDER_AMOUNT_MIN))
if amount > float(t0_quota):
    return None  # 额度不足最低门槛
```

3. 额度计算公式（在 T0Engine 中）：
```python
from .constants import T0_QUOTA_BASE_RATIO

base_value = portfolio_state.nav  # 或 etf 持仓市值
t0_quota = min(
    base_value * T0_QUOTA_BASE_RATIO,    # base × 20%
    available_reserve                      # CashManager 可用储备
)
# 仅两项！不要加第三项
```

---

### P0-8 `signal_engine.py` / `t0_fsm.py` — 无买入量手数取整

**规格书 §7.3**：买入量取整到 100 份（手），A 股 ETF 规则。卖出量不取整。

**修复要求**：

在订单执行路径中（建议在 `_execute_signal` 方法内）：
```python
if side == OrderSide.BUY:
    qty = (int(amount / price) // 100) * 100
    if qty <= 0:
        return None  # 金额不足一手
else:
    qty = int(amount / price)  # 卖出不取整
```

**断言**：
```python
if side == OrderSide.BUY and qty % 100 != 0:
    raise AssertionError(f"买入量 {qty} 未取整到 100 份")
```

---

## 阶段 B：P1 逻辑错误（7 项）

---

### P1-1 `signal_engine.py` L146 — 卖出信号不区分平仓卖与反T卖

**现状**：
```python
if px >= float(bands.sell_trigger) and is_reverse_sell_allowed(now=now):
```

**问题**：`is_reverse_sell_allowed` 在 close-only 窗口（11:25-13:15, 14:15-14:30）返回 False，导致持仓平仓卖也被阻止。

**修复要求**：

1. `evaluate()` 新增参数来区分当前是否有正T在手：
```python
def evaluate(
    self,
    *,
    ...
    has_t0_long_position: bool = False,   # 是否有正T持仓等待平仓
) -> Optional[T0Signal]:
```

2. 分两条路径：
```python
# 路径 A：持仓平仓卖（close-only 窗口也允许）
if has_t0_long_position and px >= float(bands.sell_trigger) and is_sell_allowed(now=now):
    return T0Signal(signal_type="VWAP_SELL", action="PLACE_LIMIT_SELL", ...)

# 路径 B：反T新开卖（close-only 窗口禁止）
if not has_t0_long_position and px >= float(bands.sell_trigger) and is_reverse_sell_allowed(now=now):
    return T0Signal(signal_type="VWAP_SELL", action="PLACE_LIMIT_SELL", ...)
```

---

### P1-2 `reconciliation.py` L50 — CASE A 分类过宽

**现状**：
```python
if r.status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED):
    return ... case="A" ...
```

**问题**：规格书 §7.5 定义：
- CASE A = 内存 SUBMITTED，券商 **FILLED** → 更正为 FILLED
- CASE B = 内存 SUBMITTED，券商 **已报/部分成交** → 撤单
- CASE C = 内存 SUBMITTED，券商 **无此委托** → 更正为 REJECTED

当前将 CANCELED 和 REJECTED 也归为 CASE A。

**修复要求**：
```python
if r.status == OrderStatus.FILLED:
    return ... case="A", action="CORRECT_TO_FILLED" ...
elif r.status == OrderStatus.CANCELED:
    return ... case="B", action="ALREADY_CANCELED" ...
elif r.status == OrderStatus.REJECTED:
    return ... case="C", action="CORRECT_TO_REJECTED" ...
```

---

### P1-3 `time_window.py` L24 — 反T卖出截止时间 off-by-one

**现状**：
```python
if time(13, 15) <= t <= T0_REVERSE_SELL_CUTOFF:  # cutoff = 14:00
```

**问题**：`<= 14:00` 允许 14:00:00 执行反T。规格书 §8.2 行为矩阵：14:00-14:15 行 → 反T新开卖 = ❌。

**修复**：
```python
if time(13, 15) <= t < T0_REVERSE_SELL_CUTOFF:  # < 而非 <=
```

**同理 L22 也需检查**：
```python
if time(10, 0) <= t <= time(11, 25):  # 这个 <= 11:25 是对的，因为 11:25 仍在主动窗口
    return True
```
→ L22 无需修改。

---

### P1-4 `order_manager.py` — 无部分成交处理

**现状**：常量 `T0_PARTIAL_FILL_RATIO_THRESHOLD=0.80` 和 `T0_PARTIAL_FILL_TOLERANCE_S=180` 已定义但未使用。

**修复要求**：

新增部分成交检查方法：
```python
def check_partial_fills(self, *, now: datetime, trading: TradingAdapter) -> list[ManagedOrder]:
    """检查部分成交订单，超过 3 分钟则撤销剩余"""
    micro_positions: list[ManagedOrder] = []
    for order in list(self._orders.values()):
        if order.status != OrderStatus.PARTIALLY_FILLED:
            continue
        elapsed = (now - order.submitted_at).total_seconds()
        if elapsed < T0_PARTIAL_FILL_TOLERANCE_S:
            continue
        
        # 3 分钟已过，撤销剩余
        self.cancel_order(trading=trading, order_id=order.order_id)
        # 已成交部分 → 作为微型仓位
        micro_positions.append(order)
    return micro_positions
```

微型仓位后续处理：
```
14:15 前仍未闭环 → 纳入底仓核算
```

---

### P1-5 `t0_fsm.py` — 极端行情检查未嵌入门控链

**现状**：`breaker.py` 实现了 `forbid_reverse_sell_by_extreme()` 和 `forbid_forward_buy_by_extreme()`，但 `evaluate_tick()` 未调用。

**修复要求**：

在信号评估前增加极端行情检查：
```python
from .breaker import forbid_forward_buy_by_extreme, forbid_reverse_sell_by_extreme

def evaluate_tick(self, *, etf_code: str, now: datetime) -> Optional[T0Signal]:
    ...
    # 获取当日涨跌幅
    daily_change = (snap.last_price - snap.prev_close) / snap.prev_close
    
    signal = self._signals.evaluate(...)
    if signal is None:
        return None
    
    # 极端行情过滤
    if signal.signal_type == "VWAP_BUY" and forbid_forward_buy_by_extreme(daily_change=daily_change):
        return None  # 跌幅 < -5%，禁止正T买入
    if signal.signal_type == "VWAP_SELL" and forbid_reverse_sell_by_extreme(daily_change=daily_change):
        return None  # 涨幅 > +6%，禁止反T卖出
    
    ...
```

**断言**：
```python
if daily_change < EXTREME_DOWN_PCT and signal.signal_type == "VWAP_BUY":
    raise AssertionError(f"跌幅{daily_change:.1%}<-5%，禁止正T买入")
if daily_change > EXTREME_UP_PCT and signal.signal_type == "VWAP_SELL":
    raise AssertionError(f"涨幅{daily_change:.1%}>6%，禁止反T卖出")
```

---

### P1-6 `t0_logger.py` L22-31 — T0_REGIME 日志缺少 fsm_state 字段

**现状**：T0_REGIME 日志无 FSM 状态记录。

**修复要求**：

`log_regime()` 新增 `fsm_state` 参数：
```python
def log_regime(*, log_path: str | Path, result: RegimeResult, etf_code: str, fsm_state: str = "") -> None:
    _append_jsonl(
        log_path=log_path,
        obj={
            "type": "T0_REGIME",
            ...
            "fsm_state": str(fsm_state),       # 新增
        },
    )
```

调用处也需传入：
```python
log_regime(log_path=self._log_path, result=r, etf_code=code, fsm_state=str(fsm_state.value))
```

---

### P1-7 `iopv_premium.py` + `signal_engine.py` — IOPV 方向不敏感

**现状**：premium ≥ 0.15% → HIGH（市场溢价）。但买入信号中，HIGH 的含义应该是"谨慎"而非"自信"。

**修复要求**：

方案 A（推荐）— 在 `signal_engine.py` 中修正方向逻辑：
```python
conf = compute_iopv_confidence(price=px, iopv=snapshot.iopv)

# 买入信号：高溢价 = 不利 → 降级
if signal_type == "VWAP_BUY" and conf == "HIGH":
    conf = "NORMAL"

# 卖出信号：高溢价 = 有利 → 保持 HIGH
```

方案 B — 修改 `compute_iopv_confidence` 增加方向参数（改动更大，不推荐）。

---

## 阶段 C：P2 代码质量（6 项）

| # | 位置 | 修复 |
|:---|:---|:---|
| P2-1 | `types.py` VwapSnapshot.delta_volume | 确认 `diff()` 返回 int；若返回 float 需 `int()` 转换 |
| P2-2 | `types.py` ReconciliationResult.position_sync | 改为 `tuple[tuple[str, Any], ...]` 或保持 dict 但文档注明"构造后不可修改" |
| P2-3 | `constants.py` VWAP_TICK_SIZE | 改为 `from core.constants import TICK_SIZE`，删除 `VWAP_TICK_SIZE`，全局替换引用 |
| P2-4 | `vwap_engine.py` L102 | `pstdev` vs `stdev` — 保持 `pstdev` 即可（滚动窗口为已知总体），但需在代码注释中说明选择理由 |
| P2-5 | 全局 | 清理冗余 `float()` 包装：对已知 float 类型的变量无需再包 `float()`。保留类型转换处（如从 `Any` 或 `int` 转换时）|
| P2-6 | `regime.py` L11 | 时间守卫仅允许 09:00-09:26 → 添加注释说明设计意图，或放宽为 `ts.hour <= 9 and ts.minute <= 26` |

---

## T0Engine 完整重构蓝图

修复 P0-1 ~ P0-6 后，`t0_fsm.py` 的 `T0Engine` 应具备以下完整结构：

```python
class T0Engine:
    def __init__(
        self,
        *,
        data: DataAdapter,
        trading: TradingAdapter,
        cash_manager: CashManager,
        log_path: str,
        position_port: Optional[PositionPort] = None,
    ) -> None:
        self._data = data
        self._trading = trading
        self._cash_manager = cash_manager
        self._position = position_port
        self._log_path = str(log_path)
        
        # 子模块
        self._vwap = VwapEngine()
        self._signals = SignalEngine()
        self._order_manager = OrderManager()
        
        # 日级缓存
        self._regime: dict[str, RegimeResult] = {}
        self._kde: dict[str, KdeZones] = {}
        
        # 运行时风控状态
        self._t0_daily_pnl: float = 0.0
        self._consecutive_loss_count: int = 0
        self._pnl_history: list[float] = []     # 每日PnL历史
        self._frozen_today: bool = False
    
    # ========= 日级初始化 =========
    def compute_daily_regime(self, ...) -> RegimeResult: ...
    def load_daily_kde(self, ...) -> KdeZones: ...
    
    # ========= 盘中主循环 =========
    def evaluate_tick(self, *, etf_code: str, now: datetime) -> Optional[T0Signal]:
        """9 层门控链 + 信号评估 + 下单"""
        
        # Step 0: 超时对账
        self._check_order_timeouts(now=now)
        
        # Step 1: 清道夫（14:15 / 14:55）
        execute_sweep(now=now, trading=self._trading, om=self._order_manager)
        
        # Step 2: 部分成交检查
        self._order_manager.check_partial_fills(now=now, trading=self._trading)
        
        # Layer 0: circuit_breaker / intraday_freeze
        if self._frozen_today:
            return None
        
        # Layer 1: FSM state
        if self._position is not None:
            st = self._position.get_position_state(etf_code)
            if st not in (FSMState.S2_BASE, FSMState.S3_SCALED, FSMState.S4_FULL):
                return None
            if self._position.get_t0_frozen(etf_code):
                return None
        
        # Layer 2: Regime
        r = self._regime.get(etf_code)
        if r is None or not r.regime_active:
            return None
        
        # Layer 3-9: Breaker
        bd = evaluate_breakers(inp=BreakerInputs(
            now=now, etf_code=etf_code,
            nav=...,  # 真实 NAV
            t0_daily_pnl=self._t0_daily_pnl,
            pnl_5d=self._pnl_history[-5:],
            pnl_30d=self._pnl_history[-30:],
            consecutive_loss_count=self._consecutive_loss_count,
        ))
        if bd is not None:
            log_breaker(log_path=self._log_path, d=bd)
            self._frozen_today = True
            return None
        
        # VWAP 更新 + 信号评估
        snap = self._data.get_snapshot(etf_code)
        v = self._vwap.update(snapshot=snap)
        if v.data_quality.value != "OK":
            return None
        
        # 极端行情过滤
        daily_change = (snap.last_price - snap.prev_close) / snap.prev_close
        
        # 计算额度
        t0_quota = min(nav * T0_QUOTA_BASE_RATIO, available_reserve)
        
        signal = self._signals.evaluate(
            ..., t0_quota=t0_quota, has_t0_long_position=...
        )
        if signal is None:
            return None
        
        # 极端行情断言
        if signal.signal_type == "VWAP_BUY" and forbid_forward_buy_by_extreme(daily_change=daily_change):
            return None
        if signal.signal_type == "VWAP_SELL" and forbid_reverse_sell_by_extreme(daily_change=daily_change):
            return None
        
        log_signal(log_path=self._log_path, signal=signal)
        
        # 下单（含资金锁定）
        self._execute_signal(signal=signal, now=now)
        
        return signal
    
    # ========= 订单执行 =========
    def _execute_signal(self, *, signal: T0Signal, now: datetime) -> Optional[OrderResult]: ...
    
    # ========= 超时对账 =========
    def _check_order_timeouts(self, *, now: datetime) -> None: ...
    
    # ========= Mutex 接口 =========
    def on_layer1_stop_triggered(self, *, etf_code: str) -> None: ...
    
    # ========= 日终 =========
    def reset_daily(self) -> None:
        self._pnl_history.append(self._t0_daily_pnl)
        self._t0_daily_pnl = 0.0
        self._consecutive_loss_count = 0
        self._frozen_today = False
        self._order_manager.reset_daily()
        self._vwap = VwapEngine()
        self._signals = SignalEngine()
    
    # ========= 每日审计 =========
    def emit_daily_audit(self, *, etf_code: str, now: datetime) -> None:
        log_audit(log_path=self._log_path, timestamp=now, etf_code=etf_code, items={
            "regime_computed": etf_code in self._regime,
            "kde_loaded": etf_code in self._kde,
            "round_trips": self._order_manager.daily_round_trip_count,
            "gui_ops": self._order_manager.gui_ops,
            "t0_daily_pnl": self._t0_daily_pnl,
            "consecutive_loss_count": self._consecutive_loss_count,
            "frozen_today": self._frozen_today,
        })
```

---

## 最终交付检查清单

修复完成后，请逐项确认：

- [ ] P0-1: Breaker 使用真实 NAV/PnL/连续亏损计数
- [ ] P0-2: OrderManager 集成，信号→下单流程打通
- [ ] P0-3: CashManager 集成，先锁后下，失败释放
- [ ] P0-4: EXIT_MUTEX 锁保护，4 个 Mutex 场景有处理方法
- [ ] P0-5: Sweeper 在 evaluate_tick 开头被调用
- [ ] P0-6: 超时对账在 evaluate_tick 开头被调用
- [ ] P0-7: signal.amount 由 t0_quota 计算得出，非 0.0
- [ ] P0-8: 买入量 `(qty // 100) * 100`，卖出量不取整
- [ ] P1-1: 卖出信号区分平仓/反T，close-only 时段平仓允许
- [ ] P1-2: Reconciliation CASE A 仅匹配 FILLED
- [ ] P1-3: 反T截止 `< 14:00`（非 `<= 14:00`）
- [ ] P1-4: 部分成交 3 分钟倒计时 + 微型仓位处理
- [ ] P1-5: 极端行情 >6%禁反T，<-5%禁正T，嵌入信号路径
- [ ] P1-6: T0_REGIME 日志含 fsm_state 字段
- [ ] P1-7: 买入信号中 IOPV HIGH 降级为 NORMAL
- [ ] 每个修复在代码中注释 `# FIX P0-x` 或 `# FIX P1-x`
