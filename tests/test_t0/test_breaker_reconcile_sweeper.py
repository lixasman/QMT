from __future__ import annotations

from datetime import datetime
from typing import Any

from core.enums import OrderSide, OrderStatus
from core.interfaces import OrderRequest, OrderResult

from t0.breaker import (
    BreakerInputs,
    evaluate_breakers,
    forbid_forward_buy_by_extreme,
    forbid_reverse_sell_by_extreme,
    update_consecutive_loss_count,
)
from t0.order_manager import ManagedOrder, OrderManager
from t0.reconciliation import ReconcileInput, confirm_or_reconcile
from t0.sweeper import execute_sweep


class _FakeTrading:
    def __init__(self, *, broker_orders: list[dict[str, Any]]) -> None:
        self._broker_orders = list(broker_orders)
        self.canceled: list[int] = []
        self.freeze_on: list[str] = []
        self.freeze_off = 0

    def place_order(self, req: OrderRequest) -> OrderResult:
        _ = req
        raise AssertionError("not used")

    def cancel_order(self, order_id: int) -> bool:
        self.canceled.append(int(order_id))
        return True

    def query_positions(self) -> list[Any]:
        return []

    def query_orders(self) -> list[Any]:
        return list(self._broker_orders)

    def query_asset(self) -> dict[str, Any]:
        return {}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        _ = order_id
        _ = timeout_s
        return OrderResult(order_id=int(order_id), status=OrderStatus.SUBMITTED)

    def force_reconcile(self) -> dict[str, Any]:
        return {}

    def enter_freeze_mode(self, reason: str) -> None:
        self.freeze_on.append(str(reason))

    def exit_freeze_mode(self) -> None:
        self.freeze_off += 1


def test_breaker_acceptance_scenarios_17_21() -> None:
    now = datetime(2026, 2, 23, 14, 2, 15)
    d = evaluate_breakers(
        inp=BreakerInputs(
            now=now,
            etf_code="512480",
            nav=200000.0,
            t0_daily_pnl=-610.0,
            pnl_5d=[],
            pnl_30d=[],
            consecutive_loss_count=0,
        )
    )
    assert d is not None
    assert d.breaker_layer == "LAYER_5_DAILY"

    c = 0
    c = update_consecutive_loss_count(prev_count=c, net_pnl=-15.0)
    c = update_consecutive_loss_count(prev_count=c, net_pnl=-20.0)
    c = update_consecutive_loss_count(prev_count=c, net_pnl=-18.0)
    d2 = evaluate_breakers(
        inp=BreakerInputs(
            now=now,
            etf_code="512480",
            nav=200000.0,
            t0_daily_pnl=0.0,
            pnl_5d=[],
            pnl_30d=[],
            consecutive_loss_count=c,
        )
    )
    assert d2 is not None
    assert d2.breaker_layer == "LAYER_9_CONSECUTIVE"

    c = 0
    c = update_consecutive_loss_count(prev_count=c, net_pnl=-10.0)
    c = update_consecutive_loss_count(prev_count=c, net_pnl=-20.0)
    c = update_consecutive_loss_count(prev_count=c, net_pnl=+5.0)
    c = update_consecutive_loss_count(prev_count=c, net_pnl=-8.0)
    assert c == 1

    assert forbid_reverse_sell_by_extreme(daily_change=0.065) is True
    assert forbid_forward_buy_by_extreme(daily_change=-0.055) is True


def test_reconciliation_acceptance_scenarios_22_24() -> None:
    now = datetime(2026, 2, 23, 10, 32, 15)

    tr = _FakeTrading(broker_orders=[{"order_id": 1, "status": "FILLED"}])
    rr = confirm_or_reconcile(trading=tr, inp=ReconcileInput(now=now, order_id=1, memory_status=OrderStatus.SUBMITTED))
    assert rr.case == "A"
    assert rr.action == "CORRECT_TO_FILLED"

    tr = _FakeTrading(broker_orders=[{"order_id": 2, "status": "ACCEPTED"}])
    rr = confirm_or_reconcile(trading=tr, inp=ReconcileInput(now=now, order_id=2, memory_status=OrderStatus.SUBMITTED))
    assert rr.case == "B"
    assert 2 in tr.canceled

    tr = _FakeTrading(broker_orders=[])
    rr = confirm_or_reconcile(trading=tr, inp=ReconcileInput(now=now, order_id=3, memory_status=OrderStatus.SUBMITTED))
    assert rr.case == "C"
    assert rr.action == "CORRECT_TO_REJECTED"


def test_sweeper_acceptance_scenario_16() -> None:
    om = OrderManager()
    now = datetime(2026, 2, 23, 14, 15, 0)

    om.register_order(
        order=ManagedOrder(order_id=11, etf_code="512480", side=OrderSide.BUY, price=1.0, quantity=100, submitted_at=now)
    )
    om.register_order(
        order=ManagedOrder(order_id=12, etf_code="512480", side=OrderSide.BUY, price=1.0, quantity=100, submitted_at=now)
    )
    om.register_order(
        order=ManagedOrder(order_id=13, etf_code="512480", side=OrderSide.BUY, price=1.0, quantity=100, submitted_at=now)
    )
    om.register_order(
        order=ManagedOrder(order_id=21, etf_code="512480", side=OrderSide.SELL, price=1.0, quantity=100, submitted_at=now)
    )

    tr = _FakeTrading(broker_orders=[])
    n = execute_sweep(now=now, trading=tr, om=om)
    assert n == 3
    assert set(tr.canceled) == {11, 12, 13}
    assert len(om.list_orders()) == 1
    assert om.list_orders()[0].order_id == 21

