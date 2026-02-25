from __future__ import annotations

import math

from position.atr_sizing import compute_position_sizing
from position.correlation import is_mutually_exclusive, pearson_corr_20d


def test_atr_sizing_scenarios_1_6() -> None:
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


def test_correlation_scenario_25() -> None:
    closes = [100.0 + float(i) * 0.1 for i in range(21)]
    r = pearson_corr_20d(x=closes, y=closes)
    assert r > 0.999
    assert is_mutually_exclusive(held_etf="512480", new_etf="588000", corr=0.72) is True
    assert is_mutually_exclusive(held_etf=None, new_etf="588000", corr=0.99) is False
