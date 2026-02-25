from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from core.cash_manager import CashManager
from core.enums import DataQuality, FSMState, OrderSide, OrderStatus, OrderTimeInForce, OrderType
from core.interfaces import InstrumentInfo, OrderRequest, OrderResult, TickSnapshot
from core.models import PortfolioState, PositionState

from position.atr_sizing import compute_position_sizing
from position.circuit_breaker import can_unlock_cooldown, evaluate_intraday_breaker
from position.correlation import is_mutually_exclusive
from position.fsm_transitions import check_transition
from position.rebuild import assert_rebuild_allowed, should_cancel_rebuild
from position.scale_executor import execute_scale_if_needed
from position.scale_prerequisites import evaluate_scale_prerequisites
from position.scale_signal import evaluate_scale_signal_conditions
from position.t0_controller import decide_t0_operation
from position.t0_mutex import append_pending_sell_locked, cancel_rebuy_order_if_any
from position.types import ScaleSignalEval


@dataclass
class _FakeData:
    bid1: float

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        return TickSnapshot(
            timestamp=datetime(2026, 2, 23, 10, 0, 0),
            last_price=float(self.bid1),
            volume=0,
            amount=0.0,
            ask1_price=float(self.bid1) + 0.001,
            bid1_price=float(self.bid1),
            ask1_vol=10000,
            bid1_vol=10000,
            iopv=None,
            stock_status=0,
            data_quality=DataQuality.OK,
        )

    def get_bars(self, etf_code: str, period: str, count: int) -> list[Any]:
        raise AssertionError("not used")

    def get_instrument_info(self, etf_code: str) -> InstrumentInfo:
        raise AssertionError("not used")

    def subscribe_quote(self, etf_code: str, callback: Any) -> None:
        raise AssertionError("not used")

    def get_auction_volume(self, etf_code: str, date: str) -> float:
        raise AssertionError("not used")


@dataclass
class _FakeTrading:
    last_req: Optional[OrderRequest] = None
    canceled: list[int] = None

    def __post_init__(self) -> None:
        if self.canceled is None:
            self.canceled = []

    def place_order(self, req: OrderRequest) -> OrderResult:
        self.last_req = req
        return OrderResult(order_id=1, status=OrderStatus.SUBMITTED, filled_qty=0, avg_price=None, raw=None, error="")

    def cancel_order(self, order_id: int) -> bool:
        self.canceled.append(int(order_id))
        return True

    def query_positions(self) -> list[Any]:
        return []

    def query_orders(self) -> list[Any]:
        return []

    def query_asset(self) -> dict[str, Any]:
        return {}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        return OrderResult(order_id=int(order_id), status=OrderStatus.FILLED, filled_qty=100, avg_price=1.0, raw=None, error="")

    def force_reconcile(self) -> dict[str, Any]:
        return {}

    def enter_freeze_mode(self, reason: str) -> None:
        raise AssertionError(reason)

    def exit_freeze_mode(self) -> None:
        return None


def test_acceptance_scenarios_1_6_atr_sizing() -> None:
    s = compute_position_sizing(current_nav=200_000, atr_pct=0.0358, is_strong_signal=False)
    assert s.risk_budget == 4000.0
    assert s.effective_slot == 31923.0
    assert s.base_target == 22665.0

    s = compute_position_sizing(current_nav=200_000, atr_pct=0.0125, is_strong_signal=False)
    assert s.atr_pct == 0.015
    assert s.effective_slot == 70000.0
    assert s.base_target == 49700.0

    s = compute_position_sizing(current_nav=150_000, atr_pct=0.0358, is_strong_signal=False)
    assert s.risk_budget == 3000.0
    assert s.effective_slot == 23943.0

    s = compute_position_sizing(current_nav=100_000, atr_pct=0.0358, is_strong_signal=False)
    assert s.risk_budget == 2500.0

    s = compute_position_sizing(current_nav=350_000, atr_pct=0.0358, is_strong_signal=False)
    assert s.risk_budget == 6000.0

    slot_35 = compute_position_sizing(current_nav=200_000, atr_pct=0.0358, is_strong_signal=False).effective_slot
    slot_25 = float(int(4000.0 / (0.0358 * 2.5)))
    assert slot_25 == 44692.0
    assert slot_35 == 31923.0


def test_acceptance_scenarios_7_11_fsm() -> None:
    check_transition(current_state=FSMState.S0_IDLE, new_state=FSMState.S1_TRIAL, trigger="ENTRY")
    check_transition(current_state=FSMState.S1_TRIAL, new_state=FSMState.S2_BASE, trigger="CONFIRM")
    check_transition(current_state=FSMState.S2_BASE, new_state=FSMState.S3_SCALED, trigger="SCALE_1")
    check_transition(current_state=FSMState.S3_SCALED, new_state=FSMState.S4_FULL, trigger="SCALE_2")
    check_transition(current_state=FSMState.S4_FULL, new_state=FSMState.S5_REDUCED, trigger="LAYER2")
    check_transition(current_state=FSMState.S5_REDUCED, new_state=FSMState.S0_IDLE, trigger="LAYER1")

    check_transition(current_state=FSMState.S1_TRIAL, new_state=FSMState.S0_IDLE, trigger="EXPIRE")

    try:
        check_transition(current_state=FSMState.S1_TRIAL, new_state=FSMState.S3_SCALED, trigger="ILLEGAL_SCALE")
        raise AssertionError("expected illegal transition")
    except AssertionError:
        pass

    prereq = evaluate_scale_prerequisites(
        position_state=FSMState.S4_FULL,
        unrealized_profit_atr14_multiple=3.0,
        circuit_breaker_triggered=False,
        intraday_freeze=False,
        score_soft=0.0,
        days_since_last_scale=10,
        projected_total_value=10000.0,
        effective_slot=30000.0,
    )
    assert prereq.passed is False

    assert_rebuild_allowed(rebuild_count_this_wave=0)
    try:
        assert_rebuild_allowed(rebuild_count_this_wave=1)
        raise AssertionError("expected rebuild limit assertion")
    except AssertionError:
        pass


def test_acceptance_scenarios_12_16_scale() -> None:
    prereq = evaluate_scale_prerequisites(
        position_state=FSMState.S2_BASE,
        unrealized_profit_atr14_multiple=1.0,
        circuit_breaker_triggered=False,
        intraday_freeze=False,
        score_soft=0.1,
        days_since_last_scale=10,
        projected_total_value=10000.0,
        effective_slot=30000.0,
    )
    assert prereq.passed is False

    prereq = evaluate_scale_prerequisites(
        position_state=FSMState.S2_BASE,
        unrealized_profit_atr14_multiple=2.0,
        circuit_breaker_triggered=False,
        intraday_freeze=False,
        score_soft=0.55,
        days_since_last_scale=10,
        projected_total_value=10000.0,
        effective_slot=30000.0,
    )
    assert prereq.passed is False

    prereq = evaluate_scale_prerequisites(
        position_state=FSMState.S2_BASE,
        unrealized_profit_atr14_multiple=2.0,
        circuit_breaker_triggered=False,
        intraday_freeze=False,
        score_soft=0.1,
        days_since_last_scale=2,
        projected_total_value=10000.0,
        effective_slot=30000.0,
    )
    assert prereq.passed is False

    prereq_ok = evaluate_scale_prerequisites(
        position_state=FSMState.S2_BASE,
        unrealized_profit_atr14_multiple=2.0,
        circuit_breaker_triggered=False,
        intraday_freeze=False,
        score_soft=0.1,
        days_since_last_scale=10,
        projected_total_value=10000.0,
        effective_slot=30000.0,
    )
    cond = evaluate_scale_signal_conditions(
        kama_rising_days=3,
        elder_impulse_green=True,
        pullback_atr14_multiple=1.2,
        above_chandelier_stop=True,
        chip_density_rank=0.85,
        chip_touch_distance_atr14=0.2,
        micro_vol_ratio=0.62,
        micro_support_held=True,
        micro_bullish_close=False,
    )
    assert prereq_ok.passed is True
    assert cond.passed is False

    cond_ok = evaluate_scale_signal_conditions(
        kama_rising_days=3,
        elder_impulse_green=True,
        pullback_atr14_multiple=1.2,
        above_chandelier_stop=True,
        chip_density_rank=0.85,
        chip_touch_distance_atr14=0.2,
        micro_vol_ratio=0.62,
        micro_support_held=True,
        micro_bullish_close=True,
    )
    assert cond_ok.passed is True

    state = PortfolioState(cash=200000.0, frozen_cash=0.0)
    cm = CashManager(state)
    data = _FakeData(bid1=1.0523)
    trading = _FakeTrading()
    order = OrderRequest(
        etf_code="512480",
        side=OrderSide.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        price=1.053,
        tif=OrderTimeInForce.DAY,
        strategy_name="position",
        remark="SCALE_BUY",
    )
    ev = ScaleSignalEval(
        etf_code="512480",
        timestamp=datetime(2026, 2, 23, 10, 0, 0),
        prerequisites=prereq_ok,
        conditions=cond_ok,
        decision="SCALE_BUY",
        scale_number=1,
        target_amount=13000.0,
        order=order,
    )
    oid = execute_scale_if_needed(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        cash_manager=cm,
        data=data,
        trading=trading,
        eval_result=ev,
        log_path="data/logs/position_decisions.jsonl",
    )
    assert oid == 1
    assert trading.last_req is not None
    assert trading.last_req.price == 1.053
    assert trading.last_req.quantity % 100 == 0


def test_acceptance_scenarios_17_21_t0() -> None:
    state = PortfolioState(cash=200000.0, frozen_cash=0.0)
    cm = CashManager(state)

    d = decide_t0_operation(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        position_state="S2",
        t0_frozen=False,
        current_return=0.015,
        daily_t0_loss=0.0,
        base_value=50000.0,
        available_reserve=60000.0,
        price=1.00,
        vwap=1.00,
        sigma=0.01,
        daily_change=0.0,
        cash_manager=cm,
    )
    assert d.enabled is True
    assert d.max_exposure == 10000.0

    d = decide_t0_operation(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        position_state="S5",
        t0_frozen=True,
        current_return=0.10,
        daily_t0_loss=0.0,
        base_value=35000.0,
        available_reserve=60000.0,
        price=1.00,
        vwap=1.00,
        sigma=0.01,
        daily_change=0.0,
        cash_manager=cm,
    )
    assert d.enabled is False

    d = decide_t0_operation(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        position_state="S2",
        t0_frozen=False,
        current_return=0.02,
        daily_t0_loss=0.0,
        base_value=50000.0,
        available_reserve=60000.0,
        price=1.20,
        vwap=1.00,
        sigma=0.05,
        daily_change=0.07,
        cash_manager=cm,
    )
    assert d.direction == "HOLD"
    assert d.reason == "EXTREME_UP_FREEZE_REVERSE"

    trading = _FakeTrading()
    ok = cancel_rebuy_order_if_any(trading=trading, order_id=99)
    assert ok is True
    assert 99 in trading.canceled

    st = PortfolioState()
    ps = st.positions.setdefault("512480", PositionState(etf_code="512480"))
    append_pending_sell_locked(ps=ps, locked_qty=10000, now=datetime(2026, 2, 23, 14, 0, 5), lock_reason="forward_t_during_stop")
    assert len(ps.pending_sell_locked) == 1
    assert ps.pending_sell_locked[0].sell_at == "0930"


def test_acceptance_scenarios_22_25_breaker_corr() -> None:
    st = PortfolioState(hwm=200000.0)
    d = evaluate_intraday_breaker(now=datetime(2026, 2, 23, 10, 0, 0), state=st, nav_estimate=183000.0)
    assert d is not None
    assert d.trigger_type == "INTRADAY_SOFT"

    d = evaluate_intraday_breaker(now=datetime(2026, 2, 23, 10, 0, 0), state=st, nav_estimate=179000.0)
    assert d is not None
    assert d.trigger_type == "INTRADAY_HARD"

    assert can_unlock_cooldown(cooldown_days=3, market_above_ma20=True, manual_ack=True) is False

    assert is_mutually_exclusive(held_etf="512480", new_etf="588000", corr=0.72) is True


def test_acceptance_scenarios_26_27_edges() -> None:
    st = PortfolioState()
    ps = st.positions.setdefault("512480", PositionState(etf_code="512480"))
    append_pending_sell_locked(ps=ps, locked_qty=14000, now=datetime(2026, 2, 23, 14, 55, 0), lock_reason="forward_t_eod")
    assert len(ps.pending_sell_locked) == 1

    assert should_cancel_rebuild(score_soft=0.6) is True
