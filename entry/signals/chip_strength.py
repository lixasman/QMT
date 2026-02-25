from __future__ import annotations


def compute_chip_strength(profit_ratio: float) -> float:
    pr = float(profit_ratio)
    if pr < 75.0:
        return 0.0
    if pr < 80.0:
        return 0.3
    if pr < 85.0:
        return 0.6
    if pr < 90.0:
        return 0.85
    return 1.0

