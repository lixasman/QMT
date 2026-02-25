from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from core.cash_manager import CashManager
from core.enums import DataQuality, FSMState, OrderSide, OrderStatus, OrderTimeInForce, OrderType
from core.interfaces import InstrumentInfo, OrderRequest, OrderResult, TickSnapshot
from core.models import PortfolioState

from position.scale_executor import execute_scale_if_needed
from position.scale_prerequisites import evaluate_scale_prerequisites
from position.scale_signal import evaluate_scale_signal_conditions
from position.types import ScalePrerequisites, ScaleSignalConditions, ScaleSignalEval


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

    def place_order(self, req: OrderRequest) -> OrderResult:
        self.last_req = req
        return OrderResult(order_id=1, status=OrderStatus.SUBMITTED, filled_qty=0, avg_price=None, raw=None, error="")

    def cancel_order(self, order_id: int) -> bool:
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


def test_scale_scenarios_12_16() -> None:
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
    assert prereq.items["b_profit"].passed is False

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
    assert prereq.items["d_score_soft"].passed is False

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
    assert prereq.items["e_interval"].passed is False

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
    assert trading.last_req.side == OrderSide.BUY
    assert trading.last_req.tif == OrderTimeInForce.DAY
    assert trading.last_req.price == 1.053
    assert trading.last_req.quantity % 100 == 0
    assert any(int(o.order_id) == 1 for o in state.locked_orders)

