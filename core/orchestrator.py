from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .interfaces import DataAdapter, TradingAdapter
from .state_manager import StateManager


@dataclass(frozen=True)
class ScheduledEvent:
    name: str
    run_at: datetime
    handler: Callable[[], None]


class Orchestrator:
    def __init__(self, *, data: DataAdapter, trading: TradingAdapter, state_manager: StateManager) -> None:
        self._data = data
        self._trading = trading
        self._state_manager = state_manager
        self._events: list[ScheduledEvent] = []

    @property
    def events(self) -> list[ScheduledEvent]:
        return list(self._events)

    def add_event(self, event: ScheduledEvent) -> None:
        self._events.append(event)
        self._events.sort(key=lambda e: e.run_at)

    def run_due_events(self, now: Optional[datetime] = None) -> int:
        t = now or datetime.now()
        due = [e for e in self._events if e.run_at <= t]
        self._events = [e for e in self._events if e.run_at > t]
        for e in due:
            e.handler()
        return len(due)

