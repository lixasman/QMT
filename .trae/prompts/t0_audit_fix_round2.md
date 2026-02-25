# T+0 模块二次审计 — 残留缺陷修复指令

> **角色**：你是严谨的量化系统工程师。本文档基于首轮修复后的二次审计。
> **原则**：每个修复标注对应缺陷编号，代码注释 `# FIX P0-x` 或 `# FIX NEW-x`。
> **配对文档**：`t0_strategy_specification.md` v1.4 + `t0_implementation_verification.md` v1.0。

---

## 修复总览

首轮 21 项中已修复 10 项（✅），残留 11 项 + 新发现 4 项 = **15 项待修**。

```
紧急（P0）: NEW-1, P0-1, P0-2, P0-3, P0-4, P0-5, P0-6, P0-8
重要（P1）: NEW-2, NEW-3, NEW-4
清理（P2）: P2-1, P2-2, P2-4, P2-5, P2-6
```

---

## 一、编排器重写 — `t0_fsm.py`（涵盖 NEW-1 + P0-1 ~ P0-6 + P0-8）

这是**最高优先级**。当前 `t0_fsm.py` 仍为骨架（120 行），需要重写为完整编排器。

### 1.1 必须新增的导入

```python
from .breaker import (
    BreakerInputs,
    evaluate_breakers,
    forbid_forward_buy_by_extreme,
    forbid_reverse_sell_by_extreme,
    update_consecutive_loss_count,
)
from .order_manager import OrderManager, ManagedOrder
from .reconciliation import confirm_or_reconcile, ReconcileInput
from .sweeper import execute_sweep
from .t0_logger import log_breaker, log_reconciliation, log_regime, log_signal

from core.cash_manager import CashManager
from core.enums import OrderSide, OrderStatus
from core.interfaces import OrderRequest
```

### 1.2 T0Engine `__init__` 完整签名

```python
def __init__(
    self,
    *,
    data: DataAdapter,
    trading: TradingAdapter,
    cash_manager: CashManager,          # FIX P0-3: 新增
    log_path: str,
    position_port: Optional[PositionPort] = None,
) -> None:
    self._data = data
    self._trading = trading
    self._cash_manager = cash_manager   # FIX P0-3
    self._position = position_port
    self._log_path = str(log_path)

    # 子模块
    self._vwap = VwapEngine()
    self._signals = SignalEngine()
    self._order_manager = OrderManager()  # FIX P0-2

    # 日级缓存
    self._regime: dict[str, RegimeResult] = {}
    self._kde: dict[str, KdeZones] = {}

    # 运行时风控状态  # FIX P0-1 / NEW-1
    self._t0_daily_pnl: float = 0.0
    self._consecutive_loss_count: int = 0
    self._pnl_history: list[float] = []
    self._frozen_today: bool = False
```

### 1.3 `evaluate_tick()` 完整 9 层门控链

```python
def evaluate_tick(
    self,
    *,
    etf_code: str,
    now: datetime,
    nav: float,                        # FIX P0-1: 真实 NAV
    t0_quota: float = 0.0,
    has_t0_long_position: bool = False,
    t0_long_qty: int = 0,
) -> Optional[T0Signal]:
    code = str(etf_code)

    # ===== Step 0: 超时对账 ===== # FIX P0-6
    self._check_order_timeouts(now=now)

    # ===== Step 1: 清道夫 ===== # FIX P0-5
    canceled = execute_sweep(now=now, trading=self._trading, om=self._order_manager)
    if now.time() >= time(14, 55):
        return None

    # ===== Step 2: 部分成交检查 =====
    self._order_manager.check_partial_fills(now=now, trading=self._trading)

    # ===== Layer 0: 日内冻结 =====
    if self._frozen_today:
        return None

    # ===== Layer 1: FSM =====
    snap = self._data.get_snapshot(code)
    inst = self._data.get_instrument_info(code)
    pos = self._position
    if pos is not None:
        st = pos.get_position_state(code)
        if st not in (FSMState.S2_BASE, FSMState.S3_SCALED, FSMState.S4_FULL):
            return None
        if pos.get_t0_frozen(code):
            return None

    # ===== Layer 2: Regime =====
    r = self._regime.get(code)
    if r is None or not r.regime_active:
        return None

    # ===== Layer 3-9: Breaker ===== # FIX NEW-1 + P0-1（恢复 + 真实值）
    bd = evaluate_breakers(
        inp=BreakerInputs(
            now=now,
            etf_code=code,
            nav=float(nav),                                  # 真实 NAV
            t0_daily_pnl=self._t0_daily_pnl,                # 真实累计 PnL
            pnl_5d=self._pnl_history[-5:],                   # 近 5 日
            pnl_30d=self._pnl_history[-30:],                 # 近 30 日
            consecutive_loss_count=self._consecutive_loss_count,
        )
    )
    if bd is not None:
        log_breaker(log_path=self._log_path, d=bd)
        self._frozen_today = True
        return None

    # ===== VWAP =====
    v = self._vwap.update(snapshot=snap)
    if v.data_quality.value != "OK":
        return None

    # ===== 信号 =====
    kz = self._kde.get(code)
    zones = kz.dense_zones if kz is not None else None
    s = self._signals.evaluate(
        etf_code=code, now=now, instrument=inst, snapshot=snap,
        vwap=float(v.vwap), sigma=float(v.sigma),
        regime_active=True, t0_quota=float(t0_quota),
        has_t0_long_position=has_t0_long_position,
        t0_long_qty=t0_long_qty, kde_zones=zones,
    )
    if s is None:
        return None

    # ===== 极端行情过滤 =====
    daily_change = 0.0
    if float(inst.prev_close) > 0:
        daily_change = (float(snap.last_price) - float(inst.prev_close)) / float(inst.prev_close)
    if s.signal_type == "VWAP_BUY" and forbid_forward_buy_by_extreme(daily_change=daily_change):
        return None
    if s.signal_type == "VWAP_SELL" and (not has_t0_long_position) and forbid_reverse_sell_by_extreme(daily_change=daily_change):
        return None

    log_signal(log_path=self._log_path, signal=s)

    # ===== 下单 ===== # FIX P0-2 + P0-3 + P0-8
    self._execute_signal(signal=s, now=now)

    return s
```

### 1.4 新增方法

```python
def _execute_signal(self, *, signal: T0Signal, now: datetime) -> Optional[OrderResult]:
    """下单：资金锁定 → 手数取整 → 限价委托"""
    self._order_manager.assert_can_operate()

    amount = float(signal.amount)
    price = float(signal.target_price)
    side = OrderSide.BUY if signal.signal_type == "VWAP_BUY" else OrderSide.SELL

    # FIX P0-8: 买入取整到 100 份
    if side == OrderSide.BUY:
        qty = (int(amount / price) // 100) * 100
        if qty <= 0:
            return None
        assert qty % 100 == 0, f"买入量 {qty} 未取整到 100 份"
    else:
        qty = int(amount / price)
        if qty <= 0:
            return None

    order_amount = qty * price

    # FIX P0-3: 先锁后下
    lock_ok = self._cash_manager.lock_cash(
        priority=4, strategy_name="t0", amount=order_amount
    )
    if not lock_ok:
        return None

    req = OrderRequest(
        etf_code=signal.etf_code,
        side=side,
        price=price,
        quantity=qty,
    )
    result = self._order_manager.place_limit_order(
        trading=self._trading, req=req, now=now,
    )
    if result.order_id <= 0:
        self._cash_manager.release_cash(order_id=0)
        return None
    return result


def _check_order_timeouts(self, *, now: datetime) -> None:
    """FIX P0-6: 10s 超时对账"""
    from .constants import T0_TIMEOUT_CONFIRM_S
    for order in self._order_manager.list_orders():
        if order.status != OrderStatus.SUBMITTED:
            continue
        elapsed = (now - order.submitted_at).total_seconds()
        if elapsed < T0_TIMEOUT_CONFIRM_S:
            continue
        inp = ReconcileInput(now=now, order_id=order.order_id, memory_status=order.status)
        result = confirm_or_reconcile(trading=self._trading, inp=inp)
        log_reconciliation(log_path=self._log_path, r=result)


def on_round_trip_closed(self, *, rt: RoundTripResult) -> None:
    """Round-Trip 闭环回调"""
    self._t0_daily_pnl += rt.net_pnl_cny
    self._consecutive_loss_count = update_consecutive_loss_count(
        prev_count=self._consecutive_loss_count, net_pnl=rt.net_pnl_cny
    )
    self._order_manager.mark_round_trip_closed()


def reset_daily(self) -> None:
    """日终重置"""
    self._pnl_history.append(self._t0_daily_pnl)
    self._t0_daily_pnl = 0.0
    self._consecutive_loss_count = 0
    self._frozen_today = False
    self._order_manager.reset_daily()
    self._vwap = VwapEngine()
    self._signals = SignalEngine()
```

### 1.5 Mutex（P0-4）

```python
# 此部分需要与 Exit 模块协同设计。
# T0Engine 需暴露以下方法：
def on_layer1_stop_triggered(self, *, etf_code: str) -> None: ...
def on_layer2_takeprofit_triggered(self, *, etf_code: str) -> None: ...
```

Mutex 的完整实现取决于 `exit/` 模块是否已定义 `EXIT_MUTEX`。如果尚未定义，可作为下一阶段任务。但 T0Engine 的接口（方法签名）必须现在就定义好。

---

## 二、信号引擎修复 — `signal_engine.py`

### NEW-2 平仓卖出 amount 计算修正

**当前** L165：
```python
amount = float(max(0, int(t0_long_qty))) * float(target)
```

**问题**：amount 可能远超 `T0_ORDER_AMOUNT_MAX`。平仓路径不应走 amount→qty 反算逻辑，应直接传 qty。

**修复**：
```python
# 平仓路径：amount 仅用于日志/下游参考，实际下单 qty = t0_long_qty
# 但 amount 字段仍需合理值
sell_qty = int(max(0, int(t0_long_qty)))
amount = float(sell_qty) * float(target)
# 注意：下游 _execute_signal 中平仓卖出的 qty 应直接使用 t0_long_qty，
# 而非 int(amount / price)。需要在 T0Signal 中新增 quantity 字段，
# 或在 _execute_signal 中对 VWAP_SELL + has_t0_long_position 做特判。
```

**推荐方案**：在 `T0Signal` 中新增可选字段 `quantity: Optional[int] = None`：
```python
@dataclass(frozen=True)
class T0Signal:
    ...
    quantity: Optional[int] = None   # 平仓卖出时直接指定份数
```

在 `_execute_signal` 中：
```python
if signal.quantity is not None:
    qty = signal.quantity   # 平仓卖出，直接用
else:
    qty = ...  # 正常 amount / price 计算
```

---

## 三、时间窗口边界修复 — `time_window.py`

### NEW-3 + NEW-4 `is_buy_allowed` 和 `is_reverse_sell_allowed` 排除了 11:25

**根因**：修 P1-3 时将所有 `<=` 改为 `<`，但只有 `T0_REVERSE_SELL_CUTOFF = 14:00` 需要 `<`，`11:25` 仍应 `<=`。

**修复 `is_buy_allowed`** L11：
```diff
-        if w[0] <= t < w[1]:
+        if w[0] <= t <= w[1]:
```

**修复 `is_reverse_sell_allowed`** L22：
```diff
-    if time(10, 0) <= t < time(11, 25):
+    if time(10, 0) <= t <= time(11, 25):
```

L24 保持 `<`（这是 P1-3 的正确修复）：
```python
if time(13, 15) <= t < T0_REVERSE_SELL_CUTOFF:  # < 14:00，正确
```

---

## 四、P2 代码质量清理

| # | 文件 | 修复 |
|:---|:---|:---|
| P2-2 | `types.py` L109 | `position_sync: dict` → 加文档注释 `# 构造后不可修改` 或改为 `tuple` |
| P2-4 | `vwap_engine.py` L103 | 在 `pstdev` 行上方加注释：`# 使用总体标准差：滚动窗口为已知有限总体，非抽样` |
| P2-5 | 全局 | 清理对已知 float 变量的冗余 `float()` 包装 |
| P2-6 | `regime.py` L11 | 在时间守卫上方加注释：`# 仅允许 09:00-09:26 调用，集合竞价后立即计算` |

---

## 最终交付检查清单

修复完成后逐项确认：

- [ ] NEW-1: `evaluate_breakers()` 调用已恢复，使用真实 NAV/PnL
- [ ] P0-2: `OrderManager` 在 T0Engine 中实例化，`_execute_signal` 下单
- [ ] P0-3: `CashManager` 先锁后下，失败释放
- [ ] P0-4: Mutex 接口方法已定义（`on_layer1_stop_triggered` 等）
- [ ] P0-5: `execute_sweep()` 在 `evaluate_tick()` 开头调用
- [ ] P0-6: `_check_order_timeouts()` 在 `evaluate_tick()` 开头调用
- [ ] P0-8: 买入 `(qty // 100) * 100`，卖出不取整
- [ ] NEW-2: 平仓卖出使用 `t0_long_qty` 直接指定份数
- [ ] NEW-3: `is_buy_allowed` 恢复 `<= w[1]`
- [ ] NEW-4: `is_reverse_sell_allowed` L22 恢复 `<= time(11, 25)`
- [ ] `on_round_trip_closed()` 方法更新 PnL + consecutive_loss
- [ ] `reset_daily()` 方法归档 PnL 历史并重置所有状态
- [ ] 每个修复在代码中注释 `# FIX P0-x` / `# FIX NEW-x`
