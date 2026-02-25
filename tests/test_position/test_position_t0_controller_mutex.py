from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.cash_manager import CashManager
from core.models import PortfolioState

from position.t0_controller import decide_t0_operation
from position.t0_mutex import ensure_hold_time_under, lock_with_timeout, should_wait_for_t0_before_layer2


def test_t0_scenarios_17_19() -> None:
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


def test_t0_mutex_scenarios_20_21_and_26() -> None:
    out = should_wait_for_t0_before_layer2(t0_order_submitted=True)
    assert out.action == "WAIT"
    assert out.wait_s == 10.0

    mutex = __import__("threading").Lock()
    ok = lock_with_timeout(mutex=mutex, timeout_s=0.1)
    assert ok is True
    mutex.release()

    with pytest.raises(AssertionError):
        ensure_hold_time_under(hold_started_at=datetime.now() - timedelta(seconds=3), max_s=2.0)

