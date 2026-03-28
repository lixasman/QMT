from __future__ import annotations

import pytest

from core.enums import ActionType, DataQuality, FSMState
from core.validators import assert_action_allowed, assert_fsm_transition_allowed, compute_position_sizing


def test_stale_action_gate_blocks_price_driven() -> None:
    with pytest.raises(AssertionError):
        assert_action_allowed(DataQuality.STALE, ActionType.ENTRY_CONFIRM)
    assert_action_allowed(DataQuality.STALE, ActionType.PENDING_EXECUTE)


def test_fsm_transition_matrix() -> None:
    assert_fsm_transition_allowed(FSMState.S0_IDLE, FSMState.S1_TRIAL)
    with pytest.raises(AssertionError):
        assert_fsm_transition_allowed(FSMState.S0_IDLE, FSMState.S2_BASE)


def test_position_sizing_risk_parity() -> None:
    s = compute_position_sizing(current_nav=200000.0, atr_pct_raw=0.02)
    assert s.risk_budget == 4000.0
    assert round(s.effective_slot, 2) == round(4000.0 / (0.02 * 3.5), 2)
    assert round(s.base_target, 2) == round(s.effective_slot * 0.71, 2)
    s2 = compute_position_sizing(current_nav=200000.0, atr_pct_raw=0.02, strong=True)
    assert s2.trial_amt > s.trial_amt


def test_position_sizing_supports_scaled_account_caps() -> None:
    s = compute_position_sizing(
        current_nav=200000.0,
        atr_pct_raw=0.02,
        slot_cap=35000.0,
        risk_budget_min=1250.0,
        risk_budget_max=3000.0,
    )
    assert s.risk_budget == 3000.0
    assert round(s.effective_slot, 2) == 35000.0
    assert s.effective_slot < 70000.0
