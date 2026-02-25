from __future__ import annotations

from typing import Optional

from core.enums import DataQuality

from .constants import IOPV_PREMIUM_HIGH_THRESHOLD
from .types import Confidence


def compute_iopv_confidence(*, price: float, iopv: Optional[float], iopv_quality: DataQuality = DataQuality.OK) -> Confidence:
    if iopv is None:
        return "NORMAL"
    if iopv_quality in (DataQuality.MISSING, DataQuality.STALE, DataQuality.UNAVAILABLE):
        return "NORMAL"
    ref = float(iopv)
    if ref <= 0:
        return "NORMAL"
    premium = (float(price) - ref) / ref
    return "HIGH" if premium >= float(IOPV_PREMIUM_HIGH_THRESHOLD) else "NORMAL"

