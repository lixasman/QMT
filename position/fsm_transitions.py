from __future__ import annotations

from core.enums import FSMState

from .types import TransitionDecision

LEGAL_TRANSITIONS: dict[FSMState, tuple[FSMState, ...]] = {
    FSMState.S0_IDLE: (FSMState.S1_TRIAL,),
    FSMState.S1_TRIAL: (FSMState.S2_BASE, FSMState.S0_IDLE),
    FSMState.S2_BASE: (FSMState.S3_SCALED, FSMState.S5_REDUCED, FSMState.S0_IDLE),
    FSMState.S3_SCALED: (FSMState.S4_FULL, FSMState.S5_REDUCED, FSMState.S0_IDLE),
    FSMState.S4_FULL: (FSMState.S5_REDUCED, FSMState.S0_IDLE),
    FSMState.S5_REDUCED: (FSMState.S0_IDLE, FSMState.S4_FULL),
}


def check_transition(*, current_state: FSMState, new_state: FSMState, trigger: str) -> TransitionDecision:
    cs = FSMState(str(current_state.value))
    ns = FSMState(str(new_state.value))
    allowed = LEGAL_TRANSITIONS.get(cs, ())
    if ns not in allowed:
        raise AssertionError(f"非法跃迁: {cs}→{ns}")
    return TransitionDecision(allowed=True, from_state=cs, to_state=ns, reason=str(trigger))


def should_clear_state_on_s0(*, key: str) -> bool:
    k = str(key)
    if k in ("pending_sell_locked", "pending_sell_unfilled"):
        return False
    return True
