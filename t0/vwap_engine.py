from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from core.enums import DataQuality
from core.constants import TICK_SIZE
from core.interfaces import TickSnapshot
from core.price_utils import clamp, round_to_tick

from .constants import VWAP_SIGMA_FLOOR_BPS, VWAP_SIGMA_WINDOW_SNAPSHOTS
from .types import VwapSnapshot


@dataclass(frozen=True)
class VwapState:
    cum_volume: int
    cum_amount: float
    deviations: list[float]


def apply_sigma_floor(*, raw_sigma: float, price: float) -> float:
    px = float(price)
    sg_floor = float(px) * float(VWAP_SIGMA_FLOOR_BPS)
    sg = float(max(float(raw_sigma), float(sg_floor)))
    if sg < sg_floor:
        raise AssertionError(f"sigma {sg} 低于 floor {sg_floor}")
    return float(sg)


def normalize_passive_price(*, price: float, lower_limit: float, upper_limit: float) -> float:
    p = round_to_tick(float(price), tick_size=float(TICK_SIZE))
    p = clamp(float(p), float(lower_limit), float(upper_limit))
    if p != round(float(p), 3):
        raise AssertionError(f"挂单价 {p} 未 tick 对齐（0.001）")
    if float(p) < float(lower_limit) or float(p) > float(upper_limit):
        raise AssertionError(f"挂单价 {p} 超出涨跌停范围 [{lower_limit}, {upper_limit}]")
    return float(p)


class VwapEngine:
    def __init__(self, *, start_time: time = time(9, 30)) -> None:
        self._start_time = start_time
        self._prev: Optional[TickSnapshot] = None
        self._cum_volume = 0
        self._cum_amount = 0.0
        self._deviations: list[float] = []

    @property
    def state(self) -> VwapState:
        return VwapState(cum_volume=int(self._cum_volume), cum_amount=float(self._cum_amount), deviations=list(self._deviations))

    def update(self, *, snapshot: TickSnapshot) -> VwapSnapshot:
        snap = snapshot
        ts = snap.timestamp
        px = float(snap.last_price)

        if ts.time() < self._start_time:
            self._prev = snap
            return VwapSnapshot(
                timestamp=ts,
                price=px,
                vwap=0.0,
                sigma=0.0,
                delta_volume=0,
                delta_amount=0.0,
                data_quality=DataQuality.STALE,
            )

        dv, da = snap.diff(self._prev)
        self._prev = snap

        if dv < 0:
            raise AssertionError(f"增量 Δvolume 为负: {dv}（可能用了累计量）")
        if da < 0:
            raise AssertionError(f"增量 Δamount 为负: {da}（可能用了累计量）")

        if dv <= 0 or da <= 0:
            return VwapSnapshot(
                timestamp=ts,
                price=px,
                vwap=float(self._cum_amount / self._cum_volume) if self._cum_volume > 0 else 0.0,
                sigma=apply_sigma_floor(raw_sigma=0.0, price=px) if self._cum_volume > 0 else 0.0,
                delta_volume=int(dv),
                delta_amount=float(da),
                data_quality=DataQuality.STALE,
            )

        self._cum_volume += int(dv)
        self._cum_amount += float(da)

        vwap = float(self._cum_amount) / float(self._cum_volume)
        self._deviations.append(float(px) - float(vwap))
        if len(self._deviations) > int(VWAP_SIGMA_WINDOW_SNAPSHOTS):
            self._deviations = self._deviations[-int(VWAP_SIGMA_WINDOW_SNAPSHOTS) :]
        if len(self._deviations) > int(VWAP_SIGMA_WINDOW_SNAPSHOTS):
            raise AssertionError(f"sigma 窗口 {len(self._deviations)} 超过 60 快照")

        raw_sigma = 0.0
        if len(self._deviations) >= 2:
            raw_sigma = float(statistics.pstdev(self._deviations))
        sigma = apply_sigma_floor(raw_sigma=raw_sigma, price=px)

        return VwapSnapshot(
            timestamp=ts,
            price=px,
            vwap=float(vwap),
            sigma=float(sigma),
            delta_volume=int(dv),
            delta_amount=float(da),
            data_quality=DataQuality.OK,
        )
