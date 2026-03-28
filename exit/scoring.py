from __future__ import annotations

from typing import Mapping, Optional

from core.enums import DataQuality

from .constants import SCORE_WEIGHTS, VALID_SIGNAL_KEYS
from .exit_config import get_exit_layer2_threshold
from .types import SoftScoreResult


def compute_score_soft(
    signals: Mapping[str, float],
    *,
    data_health: Optional[Mapping[str, DataQuality]] = None,
    threshold: float | None = None,
) -> SoftScoreResult:
    keys = set(signals.keys())
    illegal = keys - VALID_SIGNAL_KEYS
    if illegal:
        raise AssertionError(f"illegal signals: {sorted(illegal)}")

    used: dict[str, float] = {}
    raw = 0.0
    for k, w in SCORE_WEIGHTS.items():
        v = float(signals.get(k, 0.0))
        if data_health is not None and data_health.get(k) == DataQuality.UNAVAILABLE:
            v = 0.5
        used[str(k)] = float(v)
        raw += float(v) * float(w)

    score = round(float(raw), 2)
    if not (0.0 <= float(score) <= 2.3):
        raise AssertionError(f"Score_soft out of range: {score}")
    gate = float(get_exit_layer2_threshold()) if threshold is None else float(threshold)
    triggered = bool(float(score) >= float(gate))
    return SoftScoreResult(score_soft=float(score), triggered=triggered, used_signals=used)
