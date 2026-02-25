from __future__ import annotations

from .constants import (
    ATR_PCT_FLOOR,
    BASE_TARGET_RATIO,
    RISK_BUDGET_MAX,
    RISK_BUDGET_MIN,
    RISK_BUDGET_PCT,
    SCALE_1_RATIO,
    SCALE_2_RATIO,
    SLOT_MAX,
    STOP_MULTIPLIER,
    TRIAL_RATIO_NORMAL,
    TRIAL_RATIO_STRONG,
)
from .types import PositionSizing


def compute_position_sizing(
    *,
    current_nav: float,
    atr_pct: float,
    is_strong_signal: bool,
) -> PositionSizing:
    nav = float(current_nav)
    atr_raw = float(atr_pct)
    stop_multiplier = float(STOP_MULTIPLIER)

    if stop_multiplier < 3.0:
        raise AssertionError(f"stop_multiplier={stop_multiplier} < 3.0，方向性错误")

    risk_budget_raw = nav * float(RISK_BUDGET_PCT)
    risk_budget = float(max(float(RISK_BUDGET_MIN), min(float(risk_budget_raw), float(RISK_BUDGET_MAX))))
    if not (float(RISK_BUDGET_MIN) <= risk_budget <= float(RISK_BUDGET_MAX)):
        raise AssertionError(f"risk_budget={risk_budget} 越界")

    atr_eff = float(max(float(atr_raw), float(ATR_PCT_FLOOR)))
    if atr_eff < float(ATR_PCT_FLOOR):
        raise AssertionError(f"ATR_pct 下限保护未生效: raw={atr_raw}, eff={atr_eff}")

    effective_slot_raw = float(risk_budget) / (float(atr_eff) * float(stop_multiplier))
    effective_slot_capped = float(min(float(effective_slot_raw), float(SLOT_MAX)))
    effective_slot = float(int(round(effective_slot_capped)))
    if effective_slot > float(SLOT_MAX):
        raise AssertionError(f"effective_slot={effective_slot} 超过7万硬上限")

    base_target = float(int(round(float(effective_slot) * float(BASE_TARGET_RATIO))))
    scale_1_amt = float(int(round(float(effective_slot) * float(SCALE_1_RATIO))))
    scale_2_amt = float(int(round(float(effective_slot) * float(SCALE_2_RATIO))))

    trial_ratio = float(TRIAL_RATIO_STRONG) if bool(is_strong_signal) else float(TRIAL_RATIO_NORMAL)
    trial_amt = float(int(round(float(base_target) * float(trial_ratio))))
    confirm_amt = float(int(round(float(base_target) - float(trial_amt))))

    return PositionSizing(
        effective_slot=float(effective_slot),
        base_target=float(base_target),
        scale_1_amt=float(scale_1_amt),
        scale_2_amt=float(scale_2_amt),
        trial_amt=float(trial_amt),
        confirm_amt=float(confirm_amt),
        risk_budget=float(risk_budget),
        atr_pct=float(atr_eff),
    )
