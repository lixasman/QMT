from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

from core.enums import FSMState
from core.interfaces import OrderRequest

ScaleEvalDecision = Literal["NO_EVAL", "REJECT", "SCALE_BUY"]
T0Direction = Literal["HOLD", "FORWARD_T_BUY", "REVERSE_T_SELL"]
CircuitTriggerType = Literal["INTRADAY_SOFT", "INTRADAY_HARD", "POST_CLOSE"]


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


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    from_state: FSMState
    to_state: FSMState
    reason: str = ""


@dataclass(frozen=True)
class ScalePrereqItem:
    passed: bool
    value: Any = None
    threshold: Any = None
    reason: str = ""


@dataclass(frozen=True)
class ScalePrerequisites:
    passed: bool
    items: dict[str, ScalePrereqItem]


@dataclass(frozen=True)
class ScaleSignalConditions:
    passed: bool
    items: dict[str, ScalePrereqItem]


@dataclass(frozen=True)
class ScaleSignalEval:
    etf_code: str
    timestamp: datetime
    prerequisites: ScalePrerequisites
    conditions: ScaleSignalConditions
    decision: ScaleEvalDecision
    scale_number: int
    target_amount: float
    order: Optional[OrderRequest]


@dataclass(frozen=True)
class T0Decision:
    etf_code: str
    timestamp: datetime
    enabled: bool
    direction: T0Direction
    max_exposure: float
    reason: str
    order: Optional[OrderRequest]
    constraints: dict[str, Any]


@dataclass(frozen=True)
class CircuitBreakerDecision:
    timestamp: datetime
    trigger_type: CircuitTriggerType
    hwm: float
    nav: float
    action: str
    frozen_operations: list[str]
    allowed_operations: list[str]

