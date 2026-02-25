from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

from core.interfaces import TickSnapshot

from .constants import VWAP_WARMUP_END, VWAP_WARMUP_EXTEND_END


@dataclass
class VwapTracker:
    anchor_every_n: int = 60
    avg_open_volume_ma20: Optional[float] = None

    total_volume: float = 0.0
    total_amount: float = 0.0
    anchor_vwaps: list[float] = field(default_factory=list)
    _n: int = 0
    _warmup_extended: Optional[bool] = None
    _volume_before_warmup_end: float = 0.0

    def update(self, snapshot: TickSnapshot, prev: Optional[TickSnapshot]) -> None:
        if snapshot.timestamp.time() < time(9, 30):
            return
        if prev is not None and prev.timestamp.time() < time(9, 30):
            prev = None
        dv, da = snapshot.diff(prev)
        if dv < 0 or da < 0:
            raise AssertionError(f"negative tick diff: dv={dv} da={da}")
        self.total_volume += float(dv)
        self.total_amount += float(da)
        self._n += 1
        if snapshot.timestamp.time() <= VWAP_WARMUP_END:
            self._volume_before_warmup_end = float(self.total_volume)
        if self._n % int(self.anchor_every_n) == 0:
            v = self.vwap()
            if v is not None:
                self.anchor_vwaps.append(float(v))
                if len(self.anchor_vwaps) > 20:
                    self.anchor_vwaps = self.anchor_vwaps[-20:]

    def vwap(self) -> Optional[float]:
        if self.total_volume <= 0:
            return None
        return float(self.total_amount / self.total_volume)

    def _decide_warmup_extension(self, now: datetime) -> None:
        if self._warmup_extended is not None:
            return
        if now.time() < VWAP_WARMUP_END:
            return
        if self.avg_open_volume_ma20 is None or float(self.avg_open_volume_ma20) <= 0:
            self._warmup_extended = False
            return
        self._warmup_extended = bool(self._volume_before_warmup_end < float(self.avg_open_volume_ma20) * 0.30)

    def is_warmup(self, now: datetime) -> bool:
        if now.time() < VWAP_WARMUP_END:
            return True
        self._decide_warmup_extension(now)
        if self._warmup_extended:
            return bool(now.time() < VWAP_WARMUP_EXTEND_END)
        return False

    def slope_positive(self) -> bool:
        if len(self.anchor_vwaps) < 3:
            return False
        a, b, c = self.anchor_vwaps[-3], self.anchor_vwaps[-2], self.anchor_vwaps[-1]
        return bool(c > b > a)
