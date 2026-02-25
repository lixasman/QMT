from __future__ import annotations

from core.enums import FSMState

from .types import ScalePrerequisites
from .constants import (
    SCALE_MIN_INTERVAL_DAYS,
    SCALE_PROFIT_ATR14_MULT,
)
from .types import ScalePrereqItem


def evaluate_scale_prerequisites(
    *,
    position_state: FSMState,
    unrealized_profit_atr14_multiple: float,
    circuit_breaker_triggered: bool,
    intraday_freeze: bool,
    score_soft: float,
    days_since_last_scale: int,
    projected_total_value: float,
    effective_slot: float,
) -> ScalePrerequisites:
    st_ok = position_state in (FSMState.S2_BASE, FSMState.S3_SCALED)
    pnl_ok = float(unrealized_profit_atr14_multiple) >= float(SCALE_PROFIT_ATR14_MULT)
    breaker_ok = (not bool(circuit_breaker_triggered)) and (not bool(intraday_freeze))
    score_ok = float(score_soft) < 0.5
    interval_ok = int(days_since_last_scale) >= int(SCALE_MIN_INTERVAL_DAYS)
    slot_ok = float(projected_total_value) <= float(effective_slot)

    items = {
        "a_state": ScalePrereqItem(passed=bool(st_ok), value=str(position_state.value)),
        "b_profit": ScalePrereqItem(
            passed=bool(pnl_ok), value=float(unrealized_profit_atr14_multiple), threshold=float(SCALE_PROFIT_ATR14_MULT)
        ),
        "c_breaker": ScalePrereqItem(passed=bool(breaker_ok), value={"triggered": bool(circuit_breaker_triggered), "freeze": bool(intraday_freeze)}),
        "d_score_soft": ScalePrereqItem(passed=bool(score_ok), value=float(score_soft), threshold=0.5),
        "e_interval": ScalePrereqItem(passed=bool(interval_ok), value=int(days_since_last_scale), threshold=int(SCALE_MIN_INTERVAL_DAYS)),
        "f_slot_cap": ScalePrereqItem(passed=bool(slot_ok), value=float(projected_total_value), threshold=float(effective_slot)),
    }
    passed = all(bool(v.passed) for v in items.values())
    return ScalePrerequisites(passed=bool(passed), items=items)
