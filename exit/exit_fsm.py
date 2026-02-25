from __future__ import annotations

import threading
from datetime import datetime, time
from typing import Any, Optional

from core.enums import ActionType, DataQuality, FSMState, OrderSide, OrderType
from core.interfaces import DataAdapter, OrderRequest, TradingAdapter
from core.models import PendingSell, PortfolioState, PositionState
from core.state_manager import StateManager
from core.warn_utils import warn_once
from core.validators import assert_action_allowed

from .exit_logger import (
    log_layer1_triggered,
    log_layer2_reduce,
    log_lifeboat_buyback,
    log_lifeboat_buyback_rejected,
    serialize_data_health,
)
from .layer1 import (
    _layer1_sell_price,
    check_deadwater,
    check_gap_protection,
    check_stop_break,
    decide_full_exit,
    decide_layer1_on_trigger,
    should_freeze_t0,
)
from .layer2 import decide_layer2
from .lifeboat import evaluate_buyback, plan_lifeboat_buyback

EXIT_MUTEX = threading.Lock()


def _order_dict(req: Optional[OrderRequest]) -> Optional[dict[str, Any]]:
    if req is None:
        return None
    return {
        "price": float(req.price),
        "quantity": int(req.quantity),
        "amount": float(req.price) * int(req.quantity),
        "side": str(req.side.value),
        "remark": str(req.remark),
    }


def _extract_order_id(o: Any) -> Optional[int]:
    if isinstance(o, dict):
        for k in ("order_id", "entrust_no", "entrustNo", "id"):
            v = o.get(k)
            if v is None:
                continue
            try:
                return int(v)
            except Exception:
                continue
        return None
    v2 = getattr(o, "order_id", None)
    if v2 is not None:
        try:
            return int(v2)
        except Exception:
            return None
    v3 = getattr(o, "entrust_no", None)
    if v3 is not None:
        try:
            return int(v3)
        except Exception:
            return None
    return None


def _order_ids(raw_orders: list[Any]) -> set[int]:
    out: set[int] = set()
    bad = 0
    for o in raw_orders:
        oid = _extract_order_id(o)
        if oid is None:
            bad += 1
        if oid is not None and oid > 0:
            out.add(int(oid))
    if bad:
        warn_once("exit_orders_unparsed", f"Trading: query_orders 返回的订单中存在无法解析的订单号，已降级忽略: bad={bad}/{len(raw_orders)}")
    return out


def _extract_etf_code(p: Any) -> str:
    if isinstance(p, dict):
        for k in ("etf_code", "stock_code", "code", "symbol", "证券代码"):
            v = p.get(k)
            if v:
                return str(v)
        return ""
    for k2 in ("etf_code", "stock_code", "code", "symbol"):
        v2 = getattr(p, k2, None)
        if v2:
            return str(v2)
    return ""


def _extract_total_qty(p: Any) -> int:
    if isinstance(p, dict):
        for k in ("total_qty", "volume", "qty", "position", "current_amount", "total_amount", "持仓", "持仓数量"):
            v = p.get(k)
            if v is None:
                continue
            try:
                return int(v)
            except Exception:
                continue
        return 0
    for k2 in ("total_qty", "volume", "qty", "position", "current_amount", "total_amount"):
        v2 = getattr(p, k2, None)
        if v2 is None:
            continue
        try:
            return int(v2)
        except Exception:
            continue
    return 0


def _extract_sellable_qty(p: Any) -> int:
    if isinstance(p, dict):
        for k in ("sellable_qty", "can_use_volume", "can_use", "available", "enable_amount", "can_sell", "可用数量", "可卖数量"):
            v = p.get(k)
            if v is None:
                continue
            try:
                return int(v)
            except Exception:
                continue
        return 0
    for k2 in ("sellable_qty", "can_use_volume", "can_use", "available", "enable_amount", "can_sell"):
        v2 = getattr(p, k2, None)
        if v2 is None:
            continue
        try:
            return int(v2)
        except Exception:
            continue
    return 0


class ExitFSM:
    def __init__(
        self,
        *,
        state_manager: StateManager,
        data: DataAdapter,
        trading: TradingAdapter,
        state: PortfolioState,
        log_path: str = "data/logs/exit_decisions.jsonl",
        mutex: threading.Lock = EXIT_MUTEX,
    ) -> None:
        self._sm = state_manager
        self._data = data
        self._trading = trading
        self._state = state
        self._mutex = mutex
        self._log_path = str(log_path)

    @property
    def state(self) -> PortfolioState:
        return self._state

    def save(self) -> None:
        self._sm.save(self._state)

    def upsert_position(self, *, etf_code: str) -> PositionState:
        code = str(etf_code)
        ps = self._state.positions.get(code)
        if ps is None:
            ps = PositionState(etf_code=code)
            self._state.positions[code] = ps
        return ps

    def query_balances(self, *, etf_code: str) -> tuple[int, int, int]:
        code = str(etf_code)
        raw = self._trading.query_positions()
        total = 0
        sellable = 0
        for p in raw:
            c = _extract_etf_code(p)
            if str(c) != code:
                continue
            total = int(_extract_total_qty(p))
            sellable = int(_extract_sellable_qty(p))
            break
        locked = int(total) - int(sellable)
        if locked < 0:
            locked = 0
        return int(total), int(sellable), int(locked)

    def recover_on_startup(self) -> None:
        with self._mutex:
            for code, ps in self._state.positions.items():
                ps.total_qty = int(ps.total_qty)
                if ps.pending_sell_locked:
                    ps.pending_sell_locked = [p for p in ps.pending_sell_locked if int(p.locked_qty) > 0]
            self.save()

    def _append_pending_sell_locked(self, *, ps: PositionState, locked_qty: int, now: datetime) -> None:
        q = int(locked_qty)
        if q <= 0:
            return
        item = PendingSell(
            etf_code=str(ps.etf_code),
            locked_qty=int(q),
            lock_reason="T1_LOCKED",
            sell_at="0930",
            sell_price_type="LAYER1",
            created_time=now.isoformat(timespec="seconds"),
        )
        ps.pending_sell_locked.append(item)

    def execute_pending_locked(self, *, now: datetime) -> int:
        t = now.time()
        if not (time(9, 30) <= t <= time(9, 35)):
            return 0
        executed = 0
        with self._mutex:
            for code, ps in list(self._state.positions.items()):
                if not ps.pending_sell_locked:
                    continue
                total, sellable, _locked = self.query_balances(etf_code=code)
                pending_qty = sum(int(p.locked_qty) for p in ps.pending_sell_locked)
                sell_qty = min(int(pending_qty), int(sellable))
                if int(sell_qty) <= 0:
                    continue
                try:
                    snap = self._data.get_snapshot(code)
                    inst = self._data.get_instrument_info(code)
                except Exception as e:
                    self._trading.enter_freeze_mode(f"pending snapshot/instrument failed: {e}")
                    continue

                try:
                    assert_action_allowed(snap.data_quality, ActionType.PENDING_EXECUTE)
                except Exception:
                    continue

                sell_price = _layer1_sell_price(instrument=inst, bid1=float(snap.bid1_price))
                req = OrderRequest(
                    etf_code=str(code),
                    side=OrderSide.SELL,
                    quantity=int(sell_qty),
                    order_type=OrderType.LIMIT,
                    price=float(sell_price),
                    strategy_name="exit",
                    remark="PENDING_LOCKED",
                )
                res = self._trading.place_order(req)
                if int(res.order_id) <= 0:
                    self._trading.enter_freeze_mode(res.error or "PENDING_PLACE_ORDER_FAILED")
                    continue
                _ = self._trading.confirm_order(int(res.order_id), timeout_s=10.0)
                executed += 1
                remaining = int(pending_qty) - int(sell_qty)
                ps.pending_sell_locked = []
                if remaining > 0:
                    self._append_pending_sell_locked(ps=ps, locked_qty=int(remaining), now=now)
                ps.total_qty = int(total)
            self.save()
        return int(executed)

    def apply_layer2_if_needed(
        self,
        *,
        now: datetime,
        etf_code: str,
        score_soft: float,
        signals: Optional[dict[str, float]] = None,
    ) -> Optional[int]:
        with self._mutex:
            ps = self.upsert_position(etf_code=etf_code)
            if ps.state == FSMState.S5_REDUCED:
                return None
            total, sellable, locked = self.query_balances(etf_code=etf_code)
            ps.total_qty = int(total)
            if int(sellable) <= 0:
                return None
            snap = self._data.get_snapshot(etf_code)
            inst = self._data.get_instrument_info(etf_code)
            dec = decide_layer2(etf_code=etf_code, instrument=inst, snapshot=snap, score_soft=float(score_soft), sellable_qty=int(sellable))
            if dec.action != "REDUCE_50" or dec.order is None:
                return None
            res = self._trading.place_order(dec.order)
            if int(res.order_id) <= 0:
                self._trading.enter_freeze_mode(res.error or "LAYER2_PLACE_ORDER_FAILED")
                return None
            _ = self._trading.confirm_order(int(res.order_id), timeout_s=10.0)
            log_layer2_reduce(
                log_path=self._log_path,
                timestamp=now,
                etf_code=etf_code,
                score_soft=float(score_soft),
                signals={} if signals is None else dict(signals),
                order=_order_dict(dec.order) or {},
                k_change={"from": None, "to": 1.5},
            )
            ps.state = FSMState.S5_REDUCED
            ps.t0_frozen = True
            self.save()
            return int(res.order_id)

    def apply_layer1_checks(
        self,
        *,
        now: datetime,
        etf_code: str,
        stop_price: float,
        score_soft: float,
        data_health: dict[str, DataQuality],
        days_held: int,
        current_return: float,
        t0_realized_loss_pct: float,
        chandelier_k: Optional[float] = None,
        chandelier_hh: Optional[float] = None,
        chandelier_atr: Optional[float] = None,
    ) -> Optional[int]:
        with self._mutex:
            ps = self.upsert_position(etf_code=etf_code)
            total, sellable, locked = self.query_balances(etf_code=etf_code)
            ps.total_qty = int(total)
            snap = self._data.get_snapshot(etf_code)
            inst = self._data.get_instrument_info(etf_code)

            if should_freeze_t0(t0_realized_loss_pct=float(t0_realized_loss_pct)):
                ps.t0_frozen = True

            if snap.data_quality != DataQuality.OK:
                return None

            gap = check_gap_protection(now_time=now.time(), last_price=float(snap.last_price), stop_price=float(stop_price))
            dead = check_deadwater(days_held=int(days_held), current_return=float(current_return))
            stopb = check_stop_break(last_price=float(snap.last_price), stop_price=float(stop_price))

            if not (gap.triggered or dead.triggered or stopb.triggered):
                self.save()
                return None

            if dead.triggered:
                dec = decide_full_exit(
                    etf_code=etf_code,
                    instrument=inst,
                    snapshot=snap,
                    reason="DEADWATER",
                    sellable_qty=int(sellable),
                    total_qty=int(total),
                    locked_qty=int(locked),
                    extra={"days_held": int(days_held), "return": float(current_return)},
                )
            elif gap.triggered:
                dec = decide_full_exit(
                    etf_code=etf_code,
                    instrument=inst,
                    snapshot=snap,
                    reason="GAP_PROTECTION",
                    sellable_qty=int(sellable),
                    total_qty=int(total),
                    locked_qty=int(locked),
                    extra={"stop_price": float(stop_price), "last_price": float(snap.last_price), "now_time": now.strftime("%H:%M")},
                )
            else:
                dec = decide_layer1_on_trigger(
                    etf_code=etf_code,
                    instrument=inst,
                    snapshot=snap,
                    stop_price=float(stop_price),
                    score_soft=float(score_soft),
                    data_health=data_health,
                    lifeboat_used=bool(ps.lifeboat_used),
                    total_qty=int(total),
                    sellable_qty=int(sellable),
                    now=now,
                )

            if dec.action == "LIFEBOAT_70_30":
                ps.lifeboat_sell_time = str(dec.extra.get("sell_time") or "")
                if not ps.lifeboat_sell_time:
                    raise AssertionError("lifeboat sell_time missing")

            if dec.order is not None:
                res = self._trading.place_order(dec.order)
                if int(res.order_id) <= 0:
                    self._trading.enter_freeze_mode(res.error or "LAYER1_PLACE_ORDER_FAILED")
                    return None
                _ = self._trading.confirm_order(int(res.order_id), timeout_s=10.0)
                oid = int(res.order_id)
            else:
                oid = 0

            if dec.action == "FULL_EXIT":
                locked_qty = int(dec.extra.get("locked_qty") or 0)
                if locked_qty > 0:
                    self._append_pending_sell_locked(ps=ps, locked_qty=int(locked_qty), now=now)
                ps.state = FSMState.S0_IDLE
            trigger: dict[str, Any] = {
                "last_price": float(snap.last_price),
                "stop_price": float(stop_price),
            }
            if chandelier_k is not None:
                trigger["k_value"] = float(chandelier_k)
            if chandelier_hh is not None:
                trigger["HH"] = float(chandelier_hh)
            if chandelier_atr is not None:
                trigger["ATR"] = float(chandelier_atr)
            context = {
                "score_soft": float(score_soft),
                "data_health": serialize_data_health(data_health),
                "lifeboat_used": bool(ps.lifeboat_used),
            }
            log_layer1_triggered(
                log_path=self._log_path,
                timestamp=now,
                etf_code=etf_code,
                trigger=trigger,
                context=context,
                decision=str(dec.action),
                order=_order_dict(dec.order),
            )
            self.save()
            return int(oid) if oid > 0 else None

    def apply_lifeboat_buyback_check(
        self,
        *,
        now: datetime,
        etf_code: str,
        stop_price: float,
        score_soft: float,
        data_health: dict[str, DataQuality],
        chandelier_k: Optional[float] = None,
        chandelier_hh: Optional[float] = None,
        chandelier_atr: Optional[float] = None,
    ) -> Optional[int]:
        with self._mutex:
            ps = self.upsert_position(etf_code=etf_code)
            if ps.lifeboat_used:
                return None
            if not ps.lifeboat_sell_time:
                return None
            try:
                sell_time = datetime.fromisoformat(str(ps.lifeboat_sell_time))
            except Exception:
                return None

            total, sellable, locked = self.query_balances(etf_code=etf_code)
            ps.total_qty = int(total)
            snap = self._data.get_snapshot(etf_code)
            inst = self._data.get_instrument_info(etf_code)
            if snap.data_quality != DataQuality.OK:
                return None

            ev = evaluate_buyback(
                instrument=inst,
                snapshot=snap,
                stop_price=float(stop_price),
                score_soft=float(score_soft),
                data_health=data_health,
                lifeboat_used=bool(ps.lifeboat_used),
                lifeboat_sell_time=sell_time,
                current_total_qty=int(total),
                now=now,
            )
            if not ev.passed:
                log_lifeboat_buyback_rejected(
                    log_path=self._log_path,
                    timestamp=now,
                    etf_code=etf_code,
                    reason="CONDITIONS_NOT_MET",
                    details={"conditions": ev.conditions, "score_soft": float(score_soft), "stop_price": float(stop_price)},
                )
                return None

            try:
                plan = plan_lifeboat_buyback(
                    instrument=inst,
                    snapshot=snap,
                    current_total_qty=int(total),
                    trading_minutes_elapsed=int(ev.trading_minutes_elapsed),
                    now=now,
                )
            except Exception as e:
                log_lifeboat_buyback_rejected(
                    log_path=self._log_path,
                    timestamp=now,
                    etf_code=etf_code,
                    reason="LOT_ROUNDING",
                    details={"error": str(e), "total_qty": int(total), "conditions": ev.conditions},
                )
                return None

            req = OrderRequest(
                etf_code=str(etf_code),
                side=OrderSide.BUY,
                quantity=int(plan.buy_qty),
                order_type=OrderType.LIMIT,
                price=float(plan.buy_price),
                strategy_name="exit",
                remark="LIFEBOAT_BUYBACK",
            )
            res = self._trading.place_order(req)
            if int(res.order_id) <= 0:
                self._trading.enter_freeze_mode(res.error or "BUYBACK_PLACE_ORDER_FAILED")
                return None
            _ = self._trading.confirm_order(int(res.order_id), timeout_s=10.0)
            ps.lifeboat_used = True
            conditions = dict(ev.conditions)
            trigger2: dict[str, Any] = {"last_price": float(snap.last_price), "stop_price": float(stop_price)}
            if chandelier_k is not None:
                trigger2["k_value"] = float(chandelier_k)
            if chandelier_hh is not None:
                trigger2["HH"] = float(chandelier_hh)
            if chandelier_atr is not None:
                trigger2["ATR"] = float(chandelier_atr)
            log_lifeboat_buyback(
                log_path=self._log_path,
                timestamp=now,
                etf_code=etf_code,
                sell_time=sell_time,
                trading_minutes_elapsed=int(ev.trading_minutes_elapsed),
                conditions=conditions,
                order={"buy_qty": int(plan.buy_qty), "buy_price": float(plan.buy_price)},
                post_state={"sellable_pct": "30%", "locked_pct": "70%", "lifeboat_used": True, "trigger": trigger2},
            )
            self.save()
            return int(res.order_id)
