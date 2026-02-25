from __future__ import annotations

from dataclasses import dataclass

from .enums import ActionType, DataQuality, FSMState


def assert_action_allowed(quality: DataQuality, action_type: ActionType) -> None:
    if quality == DataQuality.OK:
        return

    if quality == DataQuality.STALE:
        blocked = {
            ActionType.ENTRY_CONFIRM,
            ActionType.SCALE_CONFIRM,
            ActionType.T0_SIGNAL,
            ActionType.EXIT_LAYER1_TRIGGER_CHECK,
            ActionType.EXIT_LIFEBOAT_BUYBACK_CHECK,
        }
        if action_type in blocked:
            raise AssertionError(f"action blocked by L1 stale: {action_type.value}")
        return

    if quality in (DataQuality.MISSING, DataQuality.UNAVAILABLE):
        blocked2 = {
            ActionType.ENTRY_CONFIRM,
            ActionType.SCALE_CONFIRM,
            ActionType.T0_SIGNAL,
            ActionType.EXIT_LAYER1_TRIGGER_CHECK,
            ActionType.EXIT_LIFEBOAT_BUYBACK_CHECK,
        }
        if action_type in blocked2:
            raise AssertionError(f"action blocked by data quality: {quality.value} {action_type.value}")
        return


LEGAL_TRANSITIONS: dict[FSMState, set[FSMState]] = {
    FSMState.S0_IDLE: {FSMState.S1_TRIAL},
    FSMState.S1_TRIAL: {FSMState.S2_BASE, FSMState.S0_IDLE},
    FSMState.S2_BASE: {FSMState.S3_SCALED, FSMState.S5_REDUCED, FSMState.S0_IDLE},
    FSMState.S3_SCALED: {FSMState.S4_FULL, FSMState.S5_REDUCED, FSMState.S0_IDLE},
    FSMState.S4_FULL: {FSMState.S5_REDUCED, FSMState.S0_IDLE},
    FSMState.S5_REDUCED: {FSMState.S0_IDLE, FSMState.S4_FULL},
}


def assert_fsm_transition_allowed(current: FSMState, new: FSMState) -> None:
    allowed = LEGAL_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise AssertionError(f"illegal transition: {current.value}->{new.value}")


def clamp(x: float, low: float, high: float) -> float:
    return min(max(float(x), float(low)), float(high))


@dataclass(frozen=True)
class PositionSizing:
    effective_slot: float
    base_target: float
    scale_1_amt: float
    scale_2_amt: float
    trial_amt: float
    confirm_amt: float
    risk_budget: float
    atr_pct: float
    stop_multiplier: float


def compute_position_sizing(
    *,
    current_nav: float,
    atr_pct_raw: float,
    stop_multiplier: float = 3.5,
    atr_pct_floor: float = 0.015,
    slot_cap: float = 70000.0,
    risk_budget_pct: float = 0.02,
    risk_budget_min: float = 2500.0,
    risk_budget_max: float = 6000.0,
    trial_ratio: float = 0.30,
    strong_trial_ratio: float = 0.50,
    strong: bool = False,
) -> PositionSizing:
    if float(stop_multiplier) < 3.0:
        raise AssertionError(f"stop_multiplier too small: {stop_multiplier}")
    risk_budget = clamp(float(current_nav) * float(risk_budget_pct), risk_budget_min, risk_budget_max)
    atr_pct = max(float(atr_pct_raw), float(atr_pct_floor))
    effective_slot = min(risk_budget / (atr_pct * float(stop_multiplier)), float(slot_cap))

    base_target = effective_slot * 0.71
    scale_1_amt = effective_slot * 0.19
    scale_2_amt = effective_slot * 0.10
    r = float(strong_trial_ratio if strong else trial_ratio)
    trial_amt = base_target * r
    confirm_amt = base_target - trial_amt
    return PositionSizing(
        effective_slot=effective_slot,
        base_target=base_target,
        scale_1_amt=scale_1_amt,
        scale_2_amt=scale_2_amt,
        trial_amt=trial_amt,
        confirm_amt=confirm_amt,
        risk_budget=risk_budget,
        atr_pct=atr_pct,
        stop_multiplier=float(stop_multiplier),
    )

