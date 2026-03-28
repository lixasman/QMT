from __future__ import annotations

from typing import Mapping, Optional

from .constants import STRONG_SIGNAL_THRESHOLD
from .phase2_config import get_phase2_score_threshold

WEIGHTS: dict[str, float] = {
    "S_squeeze": 0.30,
    "S_volume": 0.25,
    "S_chip_pr": 0.20,
    "S_trend": 0.15,
    "S_micro": 0.10,
}

VALID_SIGNAL_KEYS = set(WEIGHTS.keys())


def compute_entry_score(signals: Mapping[str, float], *, score_threshold: Optional[float] = None) -> tuple[float, bool, bool]:
    keys = set(signals.keys())
    illegal = keys - VALID_SIGNAL_KEYS
    if illegal:
        raise AssertionError(f"illegal signals: {sorted(illegal)}")

    raw = 0.0
    for k, w in WEIGHTS.items():
        raw += float(signals.get(k, 0.0)) * float(w)
    score = round(raw, 2)
    if not (0.0 <= score <= 1.0):
        raise AssertionError(f"score out of range: {score}")

    vol = float(signals.get("S_volume", 0.0))
    chip = float(signals.get("S_chip_pr", 0.0))
    trend = float(signals.get("S_trend", 0.0))
    diversity_gate = bool(vol > 0.0 or chip > 0.0 or trend > 0.0)
    score_threshold = float(get_phase2_score_threshold()) if score_threshold is None else float(score_threshold)
    is_triggered = bool(score >= score_threshold and diversity_gate)
    is_strong = bool(score >= float(STRONG_SIGNAL_THRESHOLD))

    if is_triggered:
        assert diversity_gate, "diversity_gate must hold when triggered"

    return score, is_triggered, is_strong
