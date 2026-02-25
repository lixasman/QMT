from __future__ import annotations

from datetime import datetime

from core.models import PortfolioState

from position.circuit_breaker import can_unlock_cooldown, evaluate_intraday_breaker
from position.rebuild import can_rebuild, plan_rebuild_order, should_cancel_rebuild


def test_circuit_breaker_scenarios_22_24() -> None:
    st = PortfolioState(hwm=200000.0)
    d = evaluate_intraday_breaker(now=datetime(2026, 2, 23, 10, 0, 0), state=st, nav_estimate=183000.0)
    assert d is not None
    assert d.trigger_type == "INTRADAY_SOFT"
    assert d.action == "FREEZE_NEW_OPEN"

    d = evaluate_intraday_breaker(now=datetime(2026, 2, 23, 10, 0, 0), state=st, nav_estimate=179000.0)
    assert d is not None
    assert d.trigger_type == "INTRADAY_HARD"
    assert d.action == "CLEAR_ALL"

    assert can_unlock_cooldown(cooldown_days=3, market_above_ma20=True, manual_ack=True) is False


def test_rebuild_scenario_27() -> None:
    assert should_cancel_rebuild(score_soft=0.6) is True
    assert can_rebuild(conditions={"a": True, "b": True, "c": True}) is True
    assert can_rebuild(conditions={"a": True, "b": False}) is False

    o = plan_rebuild_order(etf_code="512480", target_amount=10000.0, bid1_price=1.0523)
    assert o is not None
    assert o.price == 1.053
    assert o.quantity % 100 == 0

