from __future__ import annotations

from datetime import date, timedelta

from entry.preemption import PositionView, evaluate_preemption


def test_preemption_profit_protection_scenario_14() -> None:
    today = date(2026, 3, 16)
    pos_a = PositionView(etf_code="AAA", confirmed=True, unrealized_pnl_pct=0.03, atr_20_pct=0.02, entry_date=today - timedelta(days=4))
    pos_b = PositionView(etf_code="BBB", confirmed=True, unrealized_pnl_pct=0.005, atr_20_pct=0.02, entry_date=today - timedelta(days=4))
    plan = evaluate_preemption(new_etf_code="NEW", score=0.90, positions=[pos_a, pos_b], today=today)
    assert plan is not None
    assert plan.weak_etf_code == "BBB"


def test_preemption_atr_weak_scenario_15() -> None:
    today = date(2026, 3, 16)
    pos_a = PositionView(etf_code="AAA", confirmed=True, unrealized_pnl_pct=0.015, atr_20_pct=0.02, entry_date=today - timedelta(days=4))
    pos_b = PositionView(etf_code="BBB", confirmed=True, unrealized_pnl_pct=0.008, atr_20_pct=0.02, entry_date=today - timedelta(days=4))
    plan = evaluate_preemption(new_etf_code="NEW", score=0.90, positions=[pos_a, pos_b], today=today)
    assert plan is not None
    assert plan.weak_etf_code == "BBB"


def test_preemption_position_count_change_scenario_19() -> None:
    today = date(2026, 3, 16)
    pos_a = PositionView(etf_code="AAA", confirmed=True, unrealized_pnl_pct=0.0, atr_20_pct=0.02, entry_date=today - timedelta(days=4))
    plan = evaluate_preemption(new_etf_code="NEW", score=0.90, positions=[pos_a], today=today)
    assert plan is None

