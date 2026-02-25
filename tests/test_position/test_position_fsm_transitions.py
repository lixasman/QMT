from __future__ import annotations

import pytest

from core.enums import FSMState

from position.fsm_transitions import check_transition


def test_fsm_scenarios_7_11_transitions() -> None:
    check_transition(current_state=FSMState.S0_IDLE, new_state=FSMState.S1_TRIAL, trigger="ENTRY")
    check_transition(current_state=FSMState.S1_TRIAL, new_state=FSMState.S2_BASE, trigger="CONFIRM")
    check_transition(current_state=FSMState.S2_BASE, new_state=FSMState.S3_SCALED, trigger="SCALE_1")
    check_transition(current_state=FSMState.S3_SCALED, new_state=FSMState.S4_FULL, trigger="SCALE_2")
    check_transition(current_state=FSMState.S4_FULL, new_state=FSMState.S5_REDUCED, trigger="LAYER2")
    check_transition(current_state=FSMState.S5_REDUCED, new_state=FSMState.S0_IDLE, trigger="LAYER1")

    check_transition(current_state=FSMState.S1_TRIAL, new_state=FSMState.S0_IDLE, trigger="EXPIRE")

    with pytest.raises(AssertionError):
        check_transition(current_state=FSMState.S1_TRIAL, new_state=FSMState.S3_SCALED, trigger="ILLEGAL_SCALE")

    with pytest.raises(AssertionError):
        check_transition(current_state=FSMState.S4_FULL, new_state=FSMState.S4_FULL, trigger="ILLEGAL_SCALE_SAME")

    check_transition(current_state=FSMState.S5_REDUCED, new_state=FSMState.S4_FULL, trigger="REBUILD")

