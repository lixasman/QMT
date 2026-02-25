from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.models import PendingSell, PositionState


@dataclass(frozen=True)
class MutexOutcome:
    action: str
    wait_s: float = 0.0
    reason: str = ""


def should_wait_for_t0_before_layer2(*, t0_order_submitted: bool) -> MutexOutcome:
    if bool(t0_order_submitted):
        return MutexOutcome(action="WAIT", wait_s=10.0, reason="T0_SUBMITTED")
    return MutexOutcome(action="PROCEED", wait_s=0.0, reason="")


def lock_with_timeout(*, mutex: threading.Lock, timeout_s: float) -> bool:
    return bool(mutex.acquire(timeout=float(timeout_s)))


def ensure_hold_time_under(*, hold_started_at: datetime, max_s: float) -> None:
    if (datetime.now() - hold_started_at).total_seconds() > float(max_s):
        raise AssertionError(f"Mutex 持锁 {float(max_s)}s 上限已超时")


def append_pending_sell_locked(*, ps: PositionState, locked_qty: int, now: datetime, lock_reason: str) -> None:
    q = int(locked_qty)
    if q <= 0:
        return
    item = PendingSell(
        etf_code=str(ps.etf_code),
        locked_qty=int(q),
        lock_reason=str(lock_reason),
        sell_at="0930",
        sell_price_type="LAYER1",
        created_time=now.isoformat(timespec="seconds"),
    )
    ps.pending_sell_locked.append(item)


def cancel_rebuy_order_if_any(*, trading: Any, order_id: int) -> bool:
    oid = int(order_id)
    if oid <= 0:
        return False
    try:
        return bool(trading.cancel_order(int(oid)))
    except Exception:
        return False
