from __future__ import annotations

from datetime import datetime

from .constants import RESERVE_CASH_CAP
from .models import LockedOrder, PortfolioState


class CashManager:
    def __init__(self, state: PortfolioState) -> None:
        self._state = state

    @property
    def state(self) -> PortfolioState:
        return self._state

    def available_cash(self) -> float:
        return max(0.0, float(self._state.cash) - float(self._state.frozen_cash))

    def available_reserve(self, *, reserve_cap: float = RESERVE_CASH_CAP, safety_buffer: float = 0.0) -> float:
        cap = float(reserve_cap)
        if cap <= 0:
            return 0.0
        usable = self.available_cash() - float(safety_buffer)
        if usable <= 0:
            return 0.0
        return min(cap, usable)

    def lock_cash(self, *, order_id: int, etf_code: str, side: str, amount: float, priority: int, strategy_name: str) -> None:
        amt = float(amount)
        if amt <= 0:
            raise AssertionError(f"lock amount must be positive: {amount}")
        if amt > self.available_cash():
            raise AssertionError(f"insufficient cash: need={amt} available={self.available_cash()}")
        rec = LockedOrder(
            order_id=int(order_id),
            etf_code=etf_code,
            side=side,
            amount=amt,
            priority=int(priority),
            strategy_name=strategy_name,
            lock_time=datetime.now().isoformat(timespec="seconds"),
        )
        self._state.locked_orders.append(rec)
        self._state.frozen_cash = float(self._state.frozen_cash) + amt

    def release_cash(self, order_id: int) -> float:
        oid = int(order_id)
        released = 0.0
        keep: list[LockedOrder] = []
        for rec in self._state.locked_orders:
            if int(rec.order_id) == oid:
                released += float(rec.amount)
            else:
                keep.append(rec)
        self._state.locked_orders = keep
        self._state.frozen_cash = max(0.0, float(self._state.frozen_cash) - released)
        return released

    def reset_locks(self) -> None:
        self._state.locked_orders = []
        self._state.frozen_cash = 0.0
