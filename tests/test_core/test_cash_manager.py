from __future__ import annotations

import pytest

from core.cash_manager import CashManager
from core.models import PortfolioState


def test_cash_lock_release() -> None:
    st = PortfolioState(cash=1000.0, frozen_cash=0.0)
    cm = CashManager(st)
    assert cm.available_reserve() == 1000.0
    cm.lock_cash(order_id=1, etf_code="159915", side="BUY", amount=200.0, priority=1, strategy_name="T0")
    assert st.frozen_cash == 200.0
    assert len(st.locked_orders) == 1
    assert cm.available_reserve() == 800.0
    released = cm.release_cash(1)
    assert released == 200.0
    assert st.frozen_cash == 0.0
    assert len(st.locked_orders) == 0


def test_cash_lock_insufficient_raises() -> None:
    st = PortfolioState(cash=100.0, frozen_cash=0.0)
    cm = CashManager(st)
    with pytest.raises(AssertionError):
        cm.lock_cash(order_id=1, etf_code="159915", side="BUY", amount=200.0, priority=1, strategy_name="T0")
