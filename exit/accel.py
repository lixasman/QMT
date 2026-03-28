from __future__ import annotations


def compute_accel_k(k_base: float, pnl_pct: float, step_pct: float, step_k: float, k_min: float) -> float:
    k = float(k_base)
    if float(k) <= 0:
        return float(k)
    if float(pnl_pct) <= 0:
        return float(k)
    if float(step_pct) <= 0 or float(step_k) <= 0:
        return float(k)
    steps = int(float(pnl_pct) // float(step_pct))
    if steps <= 0:
        return float(k)
    k_adj = float(k) - float(steps) * float(step_k)
    k_adj = max(float(k_min), float(k_adj))
    return float(min(float(k), float(k_adj)))
