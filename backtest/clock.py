from __future__ import annotations

from datetime import datetime, timedelta


class SimulatedClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def reset(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=max(0.0, float(seconds)))

