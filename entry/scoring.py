from __future__ import annotations

from typing import Mapping

from .constants import SCORE_THRESHOLD, STRONG_SIGNAL_THRESHOLD

WEIGHTS: dict[str, float] = {
    "S_squeeze": 0.30,
    "S_volume": 0.25,
    "S_chip_pr": 0.20,
    "S_trend": 0.15,
    "S_micro": 0.10,
}

VALID_SIGNAL_KEYS = set(WEIGHTS.keys())


def compute_entry_score(signals: Mapping[str, float]) -> tuple[float, bool, bool]:
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
    diversity_gate = bool(vol > 0.0 or chip > 0.0)
    is_triggered = bool(score >= float(SCORE_THRESHOLD) and diversity_gate)
    is_strong = bool(score >= float(STRONG_SIGNAL_THRESHOLD))

    if is_triggered:
        assert diversity_gate, "diversity_gate must hold when triggered"

    return score, is_triggered, is_strong

