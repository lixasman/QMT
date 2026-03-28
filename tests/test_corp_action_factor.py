from __future__ import annotations

from stock_chip_engine.modules.corp_actions import is_boundary_adjustment_significant


def test_corp_action_factor_threshold_is_tick_scaled() -> None:
    close_prev = 10.0
    tick = 0.01

    # rel_eps = 0.5 * 0.01 / 10 = 0.0005
    assert is_boundary_adjustment_significant(factor=0.9990, close_none_prev=close_prev, tick_size=tick) is True
    assert is_boundary_adjustment_significant(factor=0.9996, close_none_prev=close_prev, tick_size=tick) is False


def test_corp_action_factor_invalid_inputs() -> None:
    assert is_boundary_adjustment_significant(factor=float("nan"), close_none_prev=10.0, tick_size=0.01) is False
    assert is_boundary_adjustment_significant(factor=1.0, close_none_prev=0.0, tick_size=0.01) is False
    assert is_boundary_adjustment_significant(factor=1.0, close_none_prev=10.0, tick_size=0.0) is False

