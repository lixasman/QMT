from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from core.cash_manager import CashManager
from core.enums import FSMState, OrderSide, OrderStatus, OrderTimeInForce, OrderType
from core.interfaces import DataAdapter, OrderRequest, TradingAdapter
from core.models import PortfolioState, PositionState, T0TradeRecord
from core.state_manager import StateManager
from core.warn_utils import degrade_once

from exit.exit_fsm import EXIT_MUTEX, _extract_avg_cost, _extract_etf_code, _extract_sellable_qty, _extract_total_qty
from t0 import T0Engine
from t0.breaker import BreakerInputs, evaluate_breakers, update_consecutive_loss_count
from t0.constants import T0_ORDER_AMOUNT_MAX, T0_ORDER_AMOUNT_MIN, T0_QUOTA_BASE_RATIO
from t0.order_manager import OrderManager
from t0.reconciliation import ReconcileInput, confirm_or_reconcile
from t0.sweeper import execute_sweep
from t0.t0_logger import log_breaker, log_reconciliation, log_round_trip
from t0.types import RoundTripResult

from .circuit_breaker import evaluate_intraday_breaker, evaluate_post_close_breaker, update_hwm_post_close
from .fsm_transitions import check_transition
from .position_logger import log_circuit_breaker, log_fsm_transition, log_scale_signal_eval, log_t0_operation
from .rebuild import assert_rebuild_allowed, can_rebuild, plan_rebuild_order, rebuild_wave_key, should_cancel_rebuild
from .scale_executor import execute_scale_if_needed
from .scale_prerequisites import evaluate_scale_prerequisites
from .scale_signal import evaluate_scale_signal_conditions
from .t0_controller import decide_t0_operation
from .types import CircuitBreakerDecision, ScaleSignalEval, T0Decision


class PositionFSM:
    def __init__(
        self,
        *,
        state_manager: StateManager,
        data: DataAdapter,
        trading: TradingAdapter,
        state: PortfolioState,
        log_path: str = "data/logs/position_decisions.jsonl",
        mutex: threading.Lock = EXIT_MUTEX,
        t0_log_path: str = "data/logs/t0_decisions.jsonl",
        t0_engine: Optional[T0Engine] = None,
        enable_t0: bool = False,
    ) -> None:
        self._sm = state_manager
        self._data = data
        self._trading = trading
        self._state = state
        self._log_path = str(log_path)
        self._mutex = mutex
        self._t0_log_path = str(t0_log_path)
        self._t0_engine = t0_engine or T0Engine(data=data, trading=trading, log_path=str(t0_log_path), position_port=self)
        self._t0_orders = OrderManager()
        self._enable_t0 = bool(enable_t0)
        self._startup_recovered_codes: set[str] = set()
        self._startup_recovered_cost_codes: set[str] = set()

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

    @staticmethod
    def _normalized_same_day_buy_qty(*, ps: Optional[PositionState], total_qty: int) -> int:
        if ps is None:
            return 0
        total = max(0, int(total_qty))
        locked = max(0, int(getattr(ps, "same_day_buy_qty", 0) or 0))
        return int(min(int(total), int(locked)))

    def _effective_sellable_qty(self, *, ps: Optional[PositionState], total_qty: int, broker_sellable_qty: int) -> int:
        total = max(0, int(total_qty))
        broker_sellable = max(0, int(broker_sellable_qty))
        if self._enable_t0:
            return int(min(int(total), int(broker_sellable)))
        local_locked = self._normalized_same_day_buy_qty(ps=ps, total_qty=int(total))
        capped = max(0, int(total) - int(local_locked))
        return int(min(int(broker_sellable), int(capped)))

    def query_balances(self, *, etf_code: str) -> tuple[int, int, int]:
        code = str(etf_code)
        raw = self._trading.query_positions()
        total = 0
        broker_sellable = 0
        for p in raw:
            c = _extract_etf_code(p)
            if str(c) != code:
                continue
            total = int(_extract_total_qty(p))
            broker_sellable = int(_extract_sellable_qty(p))
            break
        ps = self._state.positions.get(code)
        sellable = self._effective_sellable_qty(ps=ps, total_qty=int(total), broker_sellable_qty=int(broker_sellable))
        locked = int(total) - int(sellable)
        if locked < 0:
            locked = 0
        return int(total), int(sellable), int(locked)

    def _normalize_position_lots(self, *, ps: PositionState, total_qty: int) -> None:
        total = max(0, int(total_qty))
        base = min(max(0, int(ps.base_qty)), int(total))
        rest = int(total) - int(base)
        s1 = min(max(0, int(ps.scale_1_qty)), int(rest))
        rest -= int(s1)
        s2 = min(max(0, int(ps.scale_2_qty)), int(rest))
        ps.base_qty = int(base)
        ps.scale_1_qty = int(s1)
        ps.scale_2_qty = int(s2)
        ps.total_qty = int(total)
        ps.same_day_buy_qty = self._normalized_same_day_buy_qty(ps=ps, total_qty=int(total))

    def _recover_position_from_broker(
        self,
        *,
        ps: PositionState,
        total_qty: int,
        avg_cost: float,
        pending_status: str,
        pending_price: float,
        recovered_missing_local: bool,
    ) -> None:
        total = max(0, int(total_qty))
        px = float(avg_cost) if float(avg_cost) > 0 else float(pending_price)
        prev_state = ps.state
        prev_total = int(ps.total_qty)
        ps.total_qty = int(total)
        if float(px) > 0:
            ps.avg_cost = float(px)

        if pending_status in ("TRIAL_PLACED", "PENDING_CONFIRM"):
            ps.state = FSMState.S1_TRIAL
            ps.base_qty = 0
            ps.scale_1_qty = 0
            ps.scale_2_qty = 0
            ps.same_day_buy_qty = int(total)
            return

        if pending_status == "CONFIRM_PLACED":
            ps.state = FSMState.S2_BASE
            ps.base_qty = int(total)
            ps.scale_1_qty = 0
            ps.scale_2_qty = 0
            ps.same_day_buy_qty = int(total)
            return

        if recovered_missing_local or prev_total <= 0 or prev_state == FSMState.S0_IDLE:
            ps.state = FSMState.S2_BASE
            ps.base_qty = int(total)
            ps.scale_1_qty = 0
            ps.scale_2_qty = 0
            ps.same_day_buy_qty = 0
            return

        self._normalize_position_lots(ps=ps, total_qty=int(total))
        if int(ps.total_qty) > 0 and ps.state == FSMState.S0_IDLE:
            ps.state = FSMState.S2_BASE
            ps.base_qty = int(total)
            ps.scale_1_qty = 0
            ps.scale_2_qty = 0
        ps.same_day_buy_qty = self._normalized_same_day_buy_qty(ps=ps, total_qty=int(total))

    def recover_on_startup(self) -> None:
        with self._mutex:
            self._startup_recovered_codes = set()
            self._startup_recovered_cost_codes = set()
            pending_by_code: dict[str, tuple[str, float]] = {}
            for pe in list(self._state.pending_entries):
                code = str(getattr(pe, "etf_code", "") or "")
                if not code:
                    continue
                st = str(getattr(pe, "status", "") or "")
                price = 0.0
                if st == "CONFIRM_PLACED":
                    price = float(getattr(pe, "confirm_price", 0.0) or 0.0)
                elif st in ("TRIAL_PLACED", "PENDING_CONFIRM"):
                    price = float(getattr(pe, "trial_price", 0.0) or 0.0)
                if st:
                    pending_by_code[code] = (st, float(price))

            try:
                raw_positions = list(self._trading.query_positions())
            except Exception as e:
                for code, ps in list(self._state.positions.items()):
                    ps.total_qty = int(ps.total_qty)
                    if ps.pending_sell_locked:
                        ps.pending_sell_locked = [p for p in ps.pending_sell_locked if int(p.locked_qty) > 0]
                    if ps.pending_sell_unfilled:
                        ps.pending_sell_unfilled = [p for p in ps.pending_sell_unfilled if int(p.locked_qty) > 0]
                    degrade_once(
                        f"startup_position_reconcile_failed:{str(code)}",
                        f"startup position reconcile failed; keep local state as-is. etf={code} err={repr(e)}",
                    )
                self.save()
                return

            broker_by_code: dict[str, tuple[int, int, float]] = {}
            for row in raw_positions:
                code = str(_extract_etf_code(row) or "")
                if not code:
                    continue
                broker_by_code[code] = (
                    int(_extract_total_qty(row)),
                    int(_extract_sellable_qty(row)),
                    float(_extract_avg_cost(row) or 0.0),
                )

            for code, ps in list(self._state.positions.items()):
                ps.total_qty = int(ps.total_qty)
                if ps.pending_sell_locked:
                    ps.pending_sell_locked = [p for p in ps.pending_sell_locked if int(p.locked_qty) > 0]
                if ps.pending_sell_unfilled:
                    ps.pending_sell_unfilled = [p for p in ps.pending_sell_unfilled if int(p.locked_qty) > 0]
                snap = broker_by_code.get(str(code))
                if snap is None:
                    degrade_once(
                        f"startup_position_snapshot_missing_keep_local:{str(code)}",
                        f"startup broker position snapshot missing code; keep local state as-is. etf={code}",
                    )
                    continue
                total, _sellable, avg_cost = snap
                if int(total) > 0:
                    pending_status, pending_price = pending_by_code.get(str(code), ("", 0.0))
                    self._recover_position_from_broker(
                        ps=ps,
                        total_qty=int(total),
                        avg_cost=float(avg_cost),
                        pending_status=str(pending_status),
                        pending_price=float(pending_price),
                        recovered_missing_local=False,
                    )
                    self._startup_recovered_codes.add(str(code))
                    if float(avg_cost) > 0:
                        self._startup_recovered_cost_codes.add(str(code))
                    continue
                if int(ps.total_qty) > 0 or ps.state != FSMState.S0_IDLE:
                    degrade_once(
                        f"startup_stale_local_position_cleared:{str(code)}",
                        f"startup cleared stale local position because broker reports flat. etf={code} local_state={ps.state.value} local_qty={int(ps.total_qty)}",
                    )
                self._state.positions.pop(str(code), None)
                self._startup_recovered_codes.discard(str(code))
                self._startup_recovered_cost_codes.discard(str(code))

            for code, snap in broker_by_code.items():
                if str(code) in self._state.positions:
                    continue
                total, _sellable, avg_cost = snap
                if int(total) <= 0:
                    continue
                ps = PositionState(etf_code=str(code))
                pending_status, pending_price = pending_by_code.get(str(code), ("", 0.0))
                self._recover_position_from_broker(
                    ps=ps,
                    total_qty=int(total),
                    avg_cost=float(avg_cost),
                    pending_status=str(pending_status),
                    pending_price=float(pending_price),
                    recovered_missing_local=True,
                )
                self._state.positions[str(code)] = ps
                self._startup_recovered_codes.add(str(code))
                if float(avg_cost) > 0:
                    self._startup_recovered_cost_codes.add(str(code))
            self.save()

    def on_trial_filled(self, etf_code: str, qty: int, price: float) -> None:
        code = str(etf_code)
        q = int(qty)
        px = float(price)
        now = datetime.now()
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            _ = check_transition(current_state=ps.state, new_state=FSMState.S1_TRIAL, trigger="TRIAL_FILLED")
            prev_qty = int(ps.total_qty)
            prev_avg = float(ps.avg_cost)
            new_qty = prev_qty + int(q)
            if new_qty <= 0:
                raise AssertionError(f"invalid total_qty after trial: {new_qty}")
            new_avg = (prev_avg * float(prev_qty) + float(px) * float(q)) / float(new_qty)
            ps.total_qty = int(new_qty)
            ps.avg_cost = float(new_avg)
            ps.state = FSMState.S1_TRIAL
            ps.same_day_buy_qty = int(ps.same_day_buy_qty) + int(q)
            if not ps.entry_date:
                ps.entry_date = now.strftime("%Y-%m-%d")
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={
                    "etf_code": code,
                    "from_state": "S0",
                    "to_state": "S1",
                    "trigger": "TRIAL_FILLED",
                    "details": {"fill_qty": int(q), "fill_price": float(px), "new_total_qty": int(new_qty), "new_avg_cost": float(new_avg)},
                },
            )
            self.save()

    def on_confirm_filled(self, etf_code: str, qty: int, price: float) -> None:
        code = str(etf_code)
        q = int(qty)
        px = float(price)
        now = datetime.now()
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            _ = check_transition(current_state=ps.state, new_state=FSMState.S2_BASE, trigger="CONFIRM_FILLED")
            prev_qty = int(ps.total_qty)
            prev_avg = float(ps.avg_cost)
            new_qty = prev_qty + int(q)
            if new_qty <= 0:
                raise AssertionError(f"invalid total_qty after confirm: {new_qty}")
            new_avg = (prev_avg * float(prev_qty) + float(px) * float(q)) / float(new_qty)
            ps.total_qty = int(new_qty)
            ps.base_qty = int(new_qty)
            ps.avg_cost = float(new_avg)
            ps.state = FSMState.S2_BASE
            ps.same_day_buy_qty = int(ps.same_day_buy_qty) + int(q)
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={
                    "etf_code": code,
                    "from_state": "S1",
                    "to_state": "S2",
                    "trigger": "CONFIRM_FILLED",
                    "details": {"fill_qty": int(q), "fill_price": float(px), "new_total_qty": int(new_qty), "new_avg_cost": float(new_avg)},
                },
            )
            self.save()

    def on_entry_failed(self, etf_code: str) -> None:
        code = str(etf_code)
        now = datetime.now()
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            if ps.state != FSMState.S1_TRIAL:
                return
            _ = check_transition(current_state=ps.state, new_state=FSMState.S0_IDLE, trigger="ENTRY_FAILED")
            ps.state = FSMState.S0_IDLE
            ps.base_qty = 0
            ps.scale_1_qty = 0
            ps.scale_2_qty = 0
            ps.total_qty = 0
            ps.avg_cost = 0.0
            ps.effective_slot = 0.0
            ps.scale_count = 0
            ps.last_scale_date = ""
            ps.t0_frozen = False
            ps.t0_max_exposure = 0.0
            ps.highest_high = 0.0
            ps.entry_date = ""
            ps.t0_trades = []
            ps.cooldown_until = ""
            ps.lifeboat_used = False
            ps.lifeboat_sell_time = ""
            ps.auction_volume_history = []
            ps.same_day_buy_qty = 0
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={"etf_code": code, "from_state": "S1", "to_state": "S0", "trigger": "ENTRY_FAILED", "details": {}},
            )
            self.save()

    def get_position_state(self, etf_code: str) -> FSMState:
        code = str(etf_code)
        ps = self._state.positions.get(code)
        if ps is None:
            return FSMState.S0_IDLE
        return ps.state

    def get_total_qty(self, etf_code: str) -> int:
        code = str(etf_code)
        ps = self._state.positions.get(code)
        if ps is None:
            return 0
        return int(ps.total_qty)

    def get_sellable_qty(self, etf_code: str) -> int:
        code = str(etf_code)
        total, sellable, _locked = self.query_balances(etf_code=code)
        if int(total) <= 0:
            return 0
        return int(sellable)

    def get_t0_frozen(self, etf_code: str) -> bool:
        code = str(etf_code)
        ps = self._state.positions.get(code)
        if ps is None:
            return False
        return bool(ps.t0_frozen)

    def on_layer2_reduce(self, etf_code: str, sold_qty: int) -> None:
        code = str(etf_code)
        q = int(sold_qty)
        now = datetime.now()
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            if ps.state == FSMState.S5_REDUCED:
                return
            _ = check_transition(current_state=ps.state, new_state=FSMState.S5_REDUCED, trigger="LAYER2_REDUCE")
            prev_state = ps.state
            ps.state = FSMState.S5_REDUCED
            ps.t0_frozen = True
            new_total = max(0, int(ps.total_qty) - int(q))
            base0 = max(0, int(ps.base_qty))
            s10 = max(0, int(ps.scale_1_qty))
            s20 = max(0, int(ps.scale_2_qty))
            base = min(base0, int(new_total))
            rest = int(new_total) - int(base)
            s1 = min(s10, int(rest))
            rest = int(rest) - int(s1)
            s2 = min(s20, int(rest))
            ps.base_qty = int(base)
            ps.scale_1_qty = int(s1)
            ps.scale_2_qty = int(s2)
            ps.total_qty = int(new_total)
            ps.same_day_buy_qty = self._normalized_same_day_buy_qty(ps=ps, total_qty=int(new_total))
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={
                    "etf_code": code,
                    "from_state": str(prev_state.value),
                    "to_state": "S5",
                    "trigger": "LAYER2_REDUCE",
                    "details": {"sold_qty": int(q), "new_total_qty": int(new_total)},
                },
            )
            self.save()

    def on_layer1_clear(self, etf_code: str, sold_qty: int) -> None:
        code = str(etf_code)
        q = int(sold_qty)
        now = datetime.now()
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            if ps.state == FSMState.S0_IDLE:
                return
            _ = check_transition(current_state=ps.state, new_state=FSMState.S0_IDLE, trigger="LAYER1_CLEAR")
            prev_state = ps.state
            new_total = max(0, int(ps.total_qty) - int(q))
            ps.total_qty = int(new_total)
            pending_locked = list(ps.pending_sell_locked)
            pending_unfilled = list(ps.pending_sell_unfilled)
            if int(new_total) > 0:
                # A Layer1 sell can still leave T+1-locked residual shares. Keep the economic
                # wave context so the remainder cannot re-enter a fresh lifeboat cycle intraday.
                ps.state = FSMState.S5_REDUCED
                ps.base_qty = int(new_total)
                ps.scale_1_qty = 0
                ps.scale_2_qty = 0
                ps.t0_frozen = True
                ps.t0_max_exposure = 0.0
            else:
                ps.state = FSMState.S0_IDLE
                ps.base_qty = 0
                ps.scale_1_qty = 0
                ps.scale_2_qty = 0
                ps.avg_cost = 0.0
                ps.effective_slot = 0.0
                ps.scale_count = 0
                ps.last_scale_date = ""
                ps.t0_frozen = False
                ps.t0_max_exposure = 0.0
                ps.highest_high = 0.0
                ps.entry_date = ""
                ps.cooldown_until = ""
                ps.lifeboat_used = False
                ps.lifeboat_sell_time = ""
                ps.lifeboat_tight_stop = 0.0
                ps.last_lifeboat_buyback_date = ""
                ps.auction_volume_history = []
                ps.t0_trades = []
                ps.same_day_buy_qty = 0
            ps.pending_sell_locked = pending_locked
            ps.pending_sell_unfilled = pending_unfilled
            ps.same_day_buy_qty = self._normalized_same_day_buy_qty(ps=ps, total_qty=int(new_total))
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={
                    "etf_code": code,
                    "from_state": str(prev_state.value),
                    "to_state": str(ps.state.value),
                    "trigger": "LAYER1_CLEAR",
                    "details": {"sold_qty": int(q), "remaining_qty": int(new_total)},
                },
            )
            self.save()

    def on_lifeboat_rebuy(self, etf_code: str, rebuy_qty: int, rebuy_price: float = 0.0) -> None:
        code = str(etf_code)
        q = int(rebuy_qty)
        px = float(rebuy_price)
        now = datetime.now()
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            prev_state = ps.state
            if ps.state == FSMState.S5_REDUCED:
                _ = check_transition(current_state=ps.state, new_state=FSMState.S4_FULL, trigger="LIFEBOAT_REBUY")
                ps.state = FSMState.S4_FULL
            prev_total = int(ps.total_qty)
            prev_avg = float(ps.avg_cost)
            ps.total_qty = int(prev_total) + int(q)
            # 回补股份计入底仓 (exit spec L92-94: 70% 新股属底仓)
            ps.base_qty = int(ps.base_qty) + int(q)
            ps.same_day_buy_qty = int(ps.same_day_buy_qty) + int(q)
            # 加权平均成本更新
            if px > 0 and int(ps.total_qty) > 0:
                old_cost = float(prev_avg) * int(prev_total) if prev_avg > 0 else 0.0
                new_cost = float(px) * int(q)
                ps.avg_cost = float((old_cost + new_cost) / int(ps.total_qty))
            ps.last_lifeboat_buyback_date = now.strftime("%Y-%m-%d")
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={
                    "etf_code": code,
                    "from_state": str(prev_state.value),
                    "to_state": str(ps.state.value),
                    "trigger": "LIFEBOAT_REBUY",
                    "details": {"rebuy_qty": int(q), "rebuy_price": float(px), "new_avg_cost": float(ps.avg_cost)},
                },
            )
            self.save()

    def evaluate_scale_signal(
        self,
        *,
        now: datetime,
        etf_code: str,
        scale_number: int,
        target_amount: float,
        position_state: FSMState,
        unrealized_profit_atr14_multiple: float,
        circuit_breaker_triggered: bool,
        intraday_freeze: bool,
        score_soft: float,
        days_since_last_scale: int,
        projected_total_value: float,
        effective_slot: float,
        kama_rising_days: int,
        elder_impulse_green: bool,
        pullback_atr14_multiple: float,
        above_chandelier_stop: bool,
        chip_density_rank: float,
        chip_touch_distance_atr14: float,
        micro_vol_ratio: float,
        micro_support_held: bool,
        micro_bullish_close: bool,
    ) -> ScaleSignalEval:
        prereq = evaluate_scale_prerequisites(
            position_state=position_state,
            unrealized_profit_atr14_multiple=float(unrealized_profit_atr14_multiple),
            circuit_breaker_triggered=bool(circuit_breaker_triggered),
            intraday_freeze=bool(intraday_freeze),
            score_soft=float(score_soft),
            days_since_last_scale=int(days_since_last_scale),
            projected_total_value=float(projected_total_value),
            effective_slot=float(effective_slot),
        )
        decision = "NO_EVAL"
        cond = evaluate_scale_signal_conditions(
            kama_rising_days=int(kama_rising_days),
            elder_impulse_green=bool(elder_impulse_green),
            pullback_atr14_multiple=float(pullback_atr14_multiple),
            above_chandelier_stop=bool(above_chandelier_stop),
            chip_density_rank=float(chip_density_rank),
            chip_touch_distance_atr14=float(chip_touch_distance_atr14),
            micro_vol_ratio=float(micro_vol_ratio),
            micro_support_held=bool(micro_support_held),
            micro_bullish_close=bool(micro_bullish_close),
        )
        if prereq.passed:
            decision = "SCALE_BUY" if cond.passed else "REJECT"

        ev = ScaleSignalEval(
            etf_code=str(etf_code),
            timestamp=now,
            prerequisites=prereq,
            conditions=cond,
            decision=decision,
            scale_number=int(scale_number),
            target_amount=float(target_amount),
            order=None,
        )
        log_scale_signal_eval(
            log_path=self._log_path,
            timestamp=now,
            payload={
                "etf_code": str(etf_code),
                "state": position_state.value,
                "scale_number": int(scale_number),
                "prerequisites": {k: v.__dict__ for k, v in prereq.items.items()},
                "signal_conditions": {k: v.__dict__ for k, v in cond.items.items()},
                "decision": str(decision),
            },
        )
        return ev

    def execute_scale(self, *, now: datetime, etf_code: str, eval_result: ScaleSignalEval) -> Optional[int]:
        cm = CashManager(self._state)
        with self._mutex:
            ps0 = self.upsert_position(etf_code=str(etf_code))
            pre_state = ps0.state
        oid = execute_scale_if_needed(
            now=now,
            etf_code=str(etf_code),
            cash_manager=cm,
            data=self._data,
            trading=self._trading,
            eval_result=eval_result,
            log_path=self._log_path,
        )
        if oid is None:
            with self._mutex:
                ps1 = self.upsert_position(etf_code=str(etf_code))
                log_fsm_transition(
                    log_path=self._log_path,
                    timestamp=now,
                    payload={
                        "etf_code": str(etf_code),
                        "from_state": str(pre_state.value),
                        "to_state": str(ps1.state.value),
                        "trigger": "SCALE_ATTEMPT_FAILED",
                        "details": {"scale_number": int(eval_result.scale_number), "target_amount": float(eval_result.target_amount)},
                    },
                )
                self.save()
            return None
        final = self._trading.confirm_order(int(oid), timeout_s=0.1)
        if final.filled_qty <= 0:
            return int(oid)
        with self._mutex:
            ps = self.upsert_position(etf_code=str(etf_code))
            prev_state = ps.state
            fill_qty = int(final.filled_qty)
            fill_price = float(final.avg_price or 0.0)
            if fill_price <= 0:
                raise AssertionError(f"scale fill avg_price invalid: {final.avg_price}")
            fill_amount = float(fill_price) * int(fill_qty)
            denom = float(eval_result.target_amount)
            fill_ratio = 1.0 if denom <= 0 else float(fill_amount) / float(denom)
            should_transition = float(fill_ratio) >= 0.50
            prev_qty = int(ps.total_qty)
            prev_avg = float(ps.avg_cost)
            new_qty = prev_qty + int(fill_qty)
            new_avg = (prev_avg * float(prev_qty) + float(fill_price) * float(fill_qty)) / float(new_qty)

            if int(eval_result.scale_number) == 1:
                ps.scale_1_qty += int(fill_qty)
                if should_transition:
                    _ = check_transition(current_state=prev_state, new_state=FSMState.S3_SCALED, trigger="SCALE_1_FILLED")
                    ps.state = FSMState.S3_SCALED
                    ps.scale_count = max(int(ps.scale_count), 1)
            else:
                ps.scale_2_qty += int(fill_qty)
                if should_transition:
                    _ = check_transition(current_state=prev_state, new_state=FSMState.S4_FULL, trigger="SCALE_2_FILLED")
                    ps.state = FSMState.S4_FULL
                    ps.scale_count = max(int(ps.scale_count), 2)

            ps.total_qty = int(new_qty)
            ps.avg_cost = float(new_avg)
            ps.last_scale_date = now.strftime("%Y-%m-%d")
            ps.same_day_buy_qty = int(ps.same_day_buy_qty) + int(fill_qty)
            trigger = f"SCALE_{int(eval_result.scale_number)}_FILLED" if should_transition else f"SCALE_{int(eval_result.scale_number)}_PARTIAL_FILL"
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={
                    "etf_code": str(etf_code),
                    "from_state": str(prev_state.value),
                    "to_state": str(ps.state.value),
                    "trigger": str(trigger),
                    "details": {
                        "filled_qty": int(fill_qty),
                        "avg_price": float(fill_price),
                        "fill_amount": float(fill_amount),
                        "target_amount": float(eval_result.target_amount),
                        "fill_ratio": float(fill_ratio),
                        "new_avg_cost": float(new_avg),
                    },
                },
            )
            self.save()
        return int(oid)

    def evaluate_t0(
        self,
        *,
        now: datetime,
        etf_code: str,
        position_state: FSMState,
        t0_frozen: bool,
        current_return: float,
        daily_t0_loss: float,
        base_value: float,
        available_reserve: float,
        price: float,
        vwap: float,
        sigma: float,
        daily_change: float,
    ) -> T0Decision:
        cm = CashManager(self._state)
        d = decide_t0_operation(
            now=now,
            etf_code=str(etf_code),
            position_state=str(position_state.value),
            t0_frozen=bool(t0_frozen),
            current_return=float(current_return),
            daily_t0_loss=float(daily_t0_loss),
            base_value=float(base_value),
            available_reserve=float(available_reserve),
            price=float(price),
            vwap=float(vwap),
            sigma=float(sigma),
            daily_change=float(daily_change),
            cash_manager=cm,
        )
        with self._mutex:
            ps = self.upsert_position(etf_code=str(etf_code))
            ps.t0_max_exposure = float(d.max_exposure)
            self.save()
        log_t0_operation(
            log_path=self._log_path,
            timestamp=now,
            payload={
                "etf_code": str(etf_code),
                "position_state": position_state.value,
                "direction": d.direction,
                "trigger": d.reason,
                "constraints": dict(d.constraints),
                "order": None,
            },
        )
        return d

    def t0_prepare_day(
        self,
        *,
        etf_code: str,
        now: datetime,
        trade_date: datetime,
        auction_vol_ratio: float,
        atr5_percentile: float,
    ) -> None:
        st = self.get_position_state(str(etf_code))
        _ = self._t0_engine.compute_daily_regime(
            etf_code=str(etf_code),
            now=now,
            auction_vol_ratio=float(auction_vol_ratio),
            atr5_percentile=float(atr5_percentile),
            fsm_state=str(st.value),
        )
        try:
            _ = self._t0_engine.load_daily_kde(etf_code=str(etf_code), trade_date=trade_date.date())
        except Exception as e:
            degrade_once(
                f"t0_kde_load_failed:{str(etf_code)}",
                (
                    "T0 KDE zones load failed; T0 signal will run without KDE support merge. "
                    f"etf={etf_code} trade_date={trade_date.date().isoformat()} err={repr(e)}"
                ),
            )
            return None

    def _t0_open_trade(self, *, ps: PositionState) -> Optional[T0TradeRecord]:
        for t in reversed(list(ps.t0_trades)):
            if str(t.status) in ("OPEN", "OPEN_MICRO"):
                return t
        return None

    def _t0_closed_today(self, *, ps: PositionState, now: datetime) -> int:
        day = now.strftime("%Y-%m-%d")
        n = 0
        for t in ps.t0_trades:
            if str(t.status) != "CLOSED":
                continue
            if str(t.open_time).startswith(day):
                n += 1
        return int(n)

    def _t0_next_trade_id(self, *, ps: PositionState, now: datetime) -> str:
        day = now.strftime("%Y%m%d")
        idx = 1 + sum(1 for t in ps.t0_trades if str(t.trade_id).startswith(f"T0_{day}_"))
        return f"T0_{day}_{idx:03d}"

    def execute_t0_live(self, *, now: datetime, etf_code: str) -> Optional[int]:
        code = str(etf_code)
        _ = execute_sweep(now=now, trading=self._trading, om=self._t0_orders)
        _ = self._t0_orders.check_partial_fills(now=now, trading=self._trading)

        cm = CashManager(self._state)
        snap = self._data.get_snapshot(code)
        px = float(snap.last_price)

        quota = 0.0
        has_t0_long_position = False
        t0_long_qty = 0
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            if ps.state not in (FSMState.S2_BASE, FSMState.S3_SCALED, FSMState.S4_FULL):
                return None
            if bool(ps.t0_frozen):
                for o in list(self._t0_orders.list_orders()):
                    _ = self._t0_orders.cancel_order(trading=self._trading, order_id=int(o.order_id))
                    if o.side == OrderSide.BUY:
                        _ = cm.release_cash(int(o.order_id))
                return None
            opened = self._t0_open_trade(ps=ps)
            closed_today = self._t0_closed_today(ps=ps, now=now)
            if opened is None and int(closed_today) >= 1:
                return None

            base_value = float(ps.base_qty) * float(px)
            available_reserve = float(cm.available_reserve())
            quota = min(float(base_value) * float(T0_QUOTA_BASE_RATIO), float(available_reserve))
            ps.t0_max_exposure = float(quota)

            b = evaluate_breakers(
                inp=BreakerInputs(
                    now=now,
                    etf_code=code,
                    nav=float(self._state.nav) if float(self._state.nav) > 0 else 1.0,
                    t0_daily_pnl=float(ps.t0_daily_pnl),
                    pnl_5d=list(ps.t0_pnl_5d),
                    pnl_30d=list(ps.t0_pnl_30d),
                    consecutive_loss_count=int(ps.t0_consecutive_loss_count),
                )
            )
            if b is not None:
                ps.t0_frozen = True
                self.save()
                log_breaker(log_path=self._t0_log_path, d=b)
                return None
            if opened is not None and str(opened.direction) == "FORWARD_T":
                has_t0_long_position = True
                t0_long_qty = int(opened.open_qty)
            self.save()

        s = self._t0_engine.evaluate_tick(
            etf_code=code,
            now=now,
            t0_quota=float(quota),
            has_t0_long_position=bool(has_t0_long_position),
            t0_long_qty=int(t0_long_qty),
        )
        if s is None:
            return None

        amount = float(s.amount)
        close_sell = bool(has_t0_long_position) and str(s.signal_type) == "VWAP_SELL"
        if not close_sell:
            if float(amount) < float(T0_ORDER_AMOUNT_MIN):
                return None

        order_id: Optional[int] = None
        side: Optional[OrderSide] = None
        qty = 0
        remark = ""
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            opened = self._t0_open_trade(ps=ps)
            if opened is not None:
                if str(opened.direction) == "FORWARD_T" and str(s.signal_type) != "VWAP_SELL":
                    return None
                if str(opened.direction) == "REVERSE_T" and str(s.signal_type) != "VWAP_BUY":
                    return None

        if str(s.signal_type) == "VWAP_BUY":
            side = OrderSide.BUY
            if float(s.target_price) <= 0:
                return None
            qty = (int(float(amount) / float(s.target_price)) // 100) * 100
            if qty <= 0:
                return None
            remark = "T0_BUY"
        else:
            side = OrderSide.SELL
            total, sellable, _locked = self.query_balances(etf_code=code)
            if int(sellable) <= 0:
                return None
            with self._mutex:
                ps = self.upsert_position(etf_code=code)
                opened = self._t0_open_trade(ps=ps)
                if opened is not None and str(opened.direction) == "FORWARD_T":
                    q = int(opened.open_qty) if s.quantity is None else int(s.quantity)
                    qty = min(int(sellable), int(q))
                else:
                    qty = min(int(sellable), int(float(amount) / float(s.target_price)))
            if qty <= 0:
                return None
            remark = "T0_SELL"

        req = OrderRequest(
            etf_code=code,
            side=side,
            quantity=int(qty),
            order_type=OrderType.LIMIT,
            price=float(s.target_price),
            tif=OrderTimeInForce.DAY,
            strategy_name="t0",
            remark=str(remark),
        )
        res = self._t0_orders.place_limit_order(trading=self._trading, req=req, now=now)
        oid = int(res.order_id)
        if oid <= 0:
            self._trading.enter_freeze_mode(res.error or "T0_PLACE_ORDER_FAILED")
            with self._mutex:
                ps = self.upsert_position(etf_code=code)
                ps.t0_frozen = True
                self.save()
            return None
        order_id = int(oid)

        if side == OrderSide.BUY:
            lock_amount = float(req.price) * float(req.quantity)
            try:
                cm.lock_cash(order_id=int(order_id), etf_code=code, side="BUY", amount=float(lock_amount), priority=4, strategy_name="t0")
            except AssertionError as e:
                degrade_once(
                    f"t0_lock_cash_failed:{str(code)}",
                    f"T0 BUY lock cash failed; order canceled and T0 frozen. etf={code} order_id={int(order_id)} err={str(e)}",
                )
                _ = self._trading.cancel_order(int(order_id))
                self._trading.enter_freeze_mode("T0_LOCK_CASH_FAILED")
                with self._mutex:
                    ps = self.upsert_position(etf_code=code)
                    ps.t0_frozen = True
                    self.save()
                return None

        final = self._trading.confirm_order(int(order_id), timeout_s=10.0)
        self._t0_orders.update_status(order_id=int(order_id), status=final.status)
        if final.status in (OrderStatus.CANCELED, OrderStatus.REJECTED):
            if side == OrderSide.BUY:
                _ = cm.release_cash(int(order_id))
            return None

        if final.status in (OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
            rr = confirm_or_reconcile(
                trading=self._trading,
                inp=ReconcileInput(now=now, order_id=int(order_id), memory_status=final.status),
            )
            log_reconciliation(log_path=self._t0_log_path, r=rr)
            if rr.action in ("CORRECT_TO_REJECTED", "UNKNOWN_BROKER_STATE"):
                if side == OrderSide.BUY:
                    _ = cm.release_cash(int(order_id))
                with self._mutex:
                    ps = self.upsert_position(etf_code=code)
                    ps.t0_frozen = True
                    self.save()
                return None

        fill_qty = int(final.filled_qty) if int(final.filled_qty) > 0 else int(req.quantity)
        fill_price = float(final.avg_price) if final.avg_price is not None else float(req.price)
        if fill_qty <= 0:
            return None

        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            opened = self._t0_open_trade(ps=ps)
            if opened is None:
                direction = "FORWARD_T" if side == OrderSide.BUY else "REVERSE_T"
                st = "OPEN_MICRO" if final.status == OrderStatus.PARTIALLY_FILLED else "OPEN"
                trade = T0TradeRecord(
                    trade_id=self._t0_next_trade_id(ps=ps, now=now),
                    direction=str(direction),
                    engine="t0",
                    open_qty=int(fill_qty),
                    open_price=float(fill_price),
                    open_time=now.isoformat(timespec="seconds"),
                    status=str(st),
                    open_order_id=int(order_id),
                )
                ps.t0_trades.append(trade)
                self.save()
                return int(order_id)

            if str(opened.direction) == "FORWARD_T" and side == OrderSide.SELL:
                pnl = (float(fill_price) - float(opened.open_price)) * float(fill_qty)
                opened.close_qty = int(fill_qty)
                opened.close_price = float(fill_price)
                opened.close_time = now.isoformat(timespec="seconds")
                opened.close_order_id = int(order_id)
                opened.status = "CLOSED"
                opened.pnl = float(pnl)
                ps.t0_daily_pnl = float(ps.t0_daily_pnl) + float(pnl)
                ps.t0_consecutive_loss_count = int(update_consecutive_loss_count(prev_count=int(ps.t0_consecutive_loss_count), net_pnl=float(pnl)))
                self._t0_orders.mark_round_trip_closed()
                self.save()
                log_round_trip(
                    log_path=self._t0_log_path,
                    rt=RoundTripResult(
                        timestamp=now,
                        etf_code=code,
                        direction="FORWARD_T",
                        buy_price=float(opened.open_price),
                        sell_price=float(fill_price),
                        quantity=int(fill_qty),
                        commission=0.0,
                        net_pnl_cny=float(pnl),
                        net_pnl_bps=(float(pnl) / (float(opened.open_price) * float(fill_qty))) * 10000.0
                        if float(opened.open_price) > 0
                        else 0.0,
                        actual_be_bps=0.0,
                        daily_round_trip_count=int(self._t0_orders.daily_round_trip_count),
                        consecutive_loss_count=int(ps.t0_consecutive_loss_count),
                        t0_daily_pnl=float(ps.t0_daily_pnl),
                    ),
                )
                return int(order_id)

            if str(opened.direction) == "REVERSE_T" and side == OrderSide.BUY:
                pnl = (float(opened.open_price) - float(fill_price)) * float(fill_qty)
                opened.close_qty = int(fill_qty)
                opened.close_price = float(fill_price)
                opened.close_time = now.isoformat(timespec="seconds")
                opened.close_order_id = int(order_id)
                opened.status = "CLOSED"
                opened.pnl = float(pnl)
                ps.t0_daily_pnl = float(ps.t0_daily_pnl) + float(pnl)
                ps.t0_consecutive_loss_count = int(update_consecutive_loss_count(prev_count=int(ps.t0_consecutive_loss_count), net_pnl=float(pnl)))
                self._t0_orders.mark_round_trip_closed()
                self.save()
                log_round_trip(
                    log_path=self._t0_log_path,
                    rt=RoundTripResult(
                        timestamp=now,
                        etf_code=code,
                        direction="REVERSE_T",
                        buy_price=float(fill_price),
                        sell_price=float(opened.open_price),
                        quantity=int(fill_qty),
                        commission=0.0,
                        net_pnl_cny=float(pnl),
                        net_pnl_bps=(float(pnl) / (float(opened.open_price) * float(fill_qty))) * 10000.0
                        if float(opened.open_price) > 0
                        else 0.0,
                        actual_be_bps=0.0,
                        daily_round_trip_count=int(self._t0_orders.daily_round_trip_count),
                        consecutive_loss_count=int(ps.t0_consecutive_loss_count),
                        t0_daily_pnl=float(ps.t0_daily_pnl),
                    ),
                )
                return int(order_id)

        return int(order_id)

    def reset_t0_daily(self) -> None:
        self._t0_orders.reset_daily()

    def evaluate_circuit_breaker(self, *, now: datetime, nav_estimate: float) -> Optional[CircuitBreakerDecision]:
        d = evaluate_intraday_breaker(now=now, state=self._state, nav_estimate=float(nav_estimate))
        if d is None:
            return None
        cb = self._state.circuit_breaker
        cb.intraday_freeze = bool(d.trigger_type in ("INTRADAY_SOFT", "INTRADAY_HARD"))
        cb.intraday_freeze_time = now.isoformat(timespec="seconds") if bool(cb.intraday_freeze) else ""
        if d.trigger_type == "INTRADAY_HARD" and not bool(cb.triggered):
            cb.triggered = True
            cb.trigger_date = now.strftime("%Y-%m-%d")
            cb.trigger_nav = float(d.nav)
            cb.hwm_at_trigger = float(d.hwm)
            cb.cooldown_expire = (now.date() + timedelta(days=5)).strftime("%Y-%m-%d")
            cb.unlocked = False
        log_circuit_breaker(
            log_path=self._log_path,
            timestamp=now,
            payload={
                "trigger_type": d.trigger_type,
                "hwm": float(d.hwm),
                "nav": float(d.nav),
                "action": str(d.action),
            },
        )
        self.save()
        return d

    def on_post_close(self, *, now: datetime, current_nav: float) -> Optional[CircuitBreakerDecision]:
        self._state.hwm = float(update_hwm_post_close(prev_hwm=float(self._state.hwm), current_nav=float(current_nav)))
        if bool(self._state.circuit_breaker.triggered) and str(self._state.circuit_breaker.trigger_date) == now.strftime("%Y-%m-%d"):
            self.save()
            return None
        d = evaluate_post_close_breaker(now=now, state=self._state, current_nav=float(current_nav))
        if d is None:
            self.save()
            return None
        cb = self._state.circuit_breaker
        cb.triggered = True
        cb.trigger_date = now.strftime("%Y-%m-%d")
        cb.trigger_nav = float(d.nav)
        cb.hwm_at_trigger = float(d.hwm)
        cb.cooldown_expire = (now.date() + timedelta(days=5)).strftime("%Y-%m-%d")
        cb.unlocked = False
        log_circuit_breaker(
            log_path=self._log_path,
            timestamp=now,
            payload={"trigger_type": d.trigger_type, "hwm": float(d.hwm), "nav": float(d.nav), "action": str(d.action)},
        )
        self.save()
        return d

    def evaluate_rebuild(
        self,
        *,
        now: datetime,
        etf_code: str,
        entry_date: str,
        rebuild_count_this_wave: int,
        conditions: dict[str, bool],
        score_soft: float,
        target_amount: float,
        bid1_price: float,
    ) -> Optional[OrderRequest]:
        wave_key = rebuild_wave_key(etf_code=str(etf_code), entry_date=str(entry_date))
        if should_cancel_rebuild(score_soft=float(score_soft)):
            return None
        if not can_rebuild(conditions=dict(conditions)):
            return None
        assert_rebuild_allowed(rebuild_count_this_wave=int(rebuild_count_this_wave))
        order = plan_rebuild_order(etf_code=str(etf_code), target_amount=float(target_amount), bid1_price=float(bid1_price))
        if order is None:
            return None
        log_fsm_transition(
            log_path=self._log_path,
            timestamp=now,
            payload={
                "etf_code": str(etf_code),
                "from_state": str(self.get_position_state(str(etf_code)).value),
                "to_state": str(self.get_position_state(str(etf_code)).value),
                "trigger": "REBUILD_EVAL",
                "details": {
                    "wave_key": str(wave_key),
                    "entry_date": str(entry_date),
                    "rebuild_count_this_wave": int(rebuild_count_this_wave),
                    "target_amount": float(target_amount),
                    "bid1_price": float(bid1_price),
                },
            },
        )
        return order

    def on_rebuild_filled(self, *, now: datetime, etf_code: str, qty: int, price: float) -> None:
        code = str(etf_code)
        q = int(qty)
        px = float(price)
        if q <= 0 or px <= 0:
            raise AssertionError(f"invalid rebuild fill: qty={qty} price={price}")
        with self._mutex:
            ps = self.upsert_position(etf_code=code)
            _ = check_transition(current_state=ps.state, new_state=FSMState.S4_FULL, trigger="REBUILD_FILLED")
            prev_state = ps.state
            prev_qty = int(ps.total_qty)
            prev_avg = float(ps.avg_cost)
            new_qty = prev_qty + int(q)
            new_avg = (prev_avg * float(prev_qty) + float(px) * float(q)) / float(new_qty)
            ps.total_qty = int(new_qty)
            ps.avg_cost = float(new_avg)
            ps.state = FSMState.S4_FULL
            ps.base_qty = int(new_qty)
            ps.scale_1_qty = 0
            ps.scale_2_qty = 0
            ps.scale_count = max(int(ps.scale_count), 2)
            ps.entry_date = now.strftime("%Y-%m-%d")
            log_fsm_transition(
                log_path=self._log_path,
                timestamp=now,
                payload={
                    "etf_code": code,
                    "from_state": str(prev_state.value),
                    "to_state": "S4",
                    "trigger": "REBUILD_FILLED",
                    "details": {"fill_qty": int(q), "fill_price": float(px), "new_total_qty": int(new_qty), "new_avg_cost": float(new_avg)},
                },
            )
            self.save()
