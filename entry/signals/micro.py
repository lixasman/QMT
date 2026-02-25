from __future__ import annotations


def compute_micro(ofi_daily: float, vpin_rank: float, vs_max: float) -> float:
    score = 0.0
    if float(ofi_daily) > 0:
        score += 0.4
    vr = float(vpin_rank)
    if 0.3 <= vr <= 0.85:
        score += 0.3
    if float(vs_max) >= 1.5:
        score += 0.3
    return min(score, 1.0)

