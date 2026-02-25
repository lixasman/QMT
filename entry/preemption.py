from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from core.time_utils import get_trading_dates_strict

from .constants import PREEMPTION_THRESHOLD, WEAK_ATR_FACTOR, WEAK_MIN_HOLD_DAYS, WEAK_PROFIT_PROTECTION


@dataclass(frozen=True)
class PositionView:
    etf_code: str
    confirmed: bool
    unrealized_pnl_pct: float
    atr_20_pct: float
    entry_date: date


@dataclass(frozen=True)
class PreemptionPlan:
    new_etf_code: str
    new_score: float
    weak_etf_code: str
    reason: str


def _is_weak(pos: PositionView, *, today: date) -> bool:
    if pos.confirmed and float(pos.unrealized_pnl_pct) > float(WEAK_PROFIT_PROTECTION):
        return False
    start = pos.entry_date.strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    cal = get_trading_dates_strict(start, end)
    held_trading_days = len(cal)
    if held_trading_days < 2:
        return False
    if not pos.confirmed:
        return True
    if held_trading_days < int(WEAK_MIN_HOLD_DAYS):
        return False
    return bool(float(pos.unrealized_pnl_pct) < float(WEAK_ATR_FACTOR) * float(pos.atr_20_pct) and float(pos.unrealized_pnl_pct) <= float(WEAK_PROFIT_PROTECTION))


def evaluate_preemption(*, new_etf_code: str, score: float, positions: list[PositionView], today: Optional[date] = None) -> Optional[PreemptionPlan]:
    td = today or date.today()
    if len(positions) != 2:
        return None
    if float(score) < float(PREEMPTION_THRESHOLD):
        return None
    weak = [p for p in positions if _is_weak(p, today=td)]
    if not weak:
        return None
    weak_sorted = sorted(weak, key=lambda p: (p.confirmed, p.unrealized_pnl_pct))
    w = weak_sorted[0]
    if w.confirmed and float(w.unrealized_pnl_pct) > float(WEAK_PROFIT_PROTECTION):
        raise AssertionError("preemption cannot target protected profit position")
    return PreemptionPlan(new_etf_code=new_etf_code, new_score=float(score), weak_etf_code=w.etf_code, reason="WEAK_POSITION")
