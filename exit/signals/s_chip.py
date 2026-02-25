from __future__ import annotations

from typing import Sequence

from ..constants import (
    S_CHIP_ALLOWED_VALUES,
    S_CHIP_DPC_EPS,
    S_CHIP_PROFIT_RATIO_GATE,
    S_CHIP_TIER1_DAILY_DROP_THRESHOLD,
    S_CHIP_TIER2_DROP_DAYS_THRESHOLD,
    S_CHIP_TIER3_TOTAL_DROP_THRESHOLD,
)


def compute_s_chip(dpc_window: Sequence[float], profit_ratio: float) -> float:
    pr = float(profit_ratio)
    if pr < float(S_CHIP_PROFIT_RATIO_GATE):
        return 0.0

    if len(dpc_window) < 5:
        raise AssertionError(f"dpc_window too short: len={len(dpc_window)} need>=5")
    w = [float(x) for x in dpc_window[-5:]]

    dpc_t_4 = float(w[0])
    dpc_t_1 = float(w[3])
    dpc_t = float(w[4])

    tier1 = 0
    if float(dpc_t_1) >= float(S_CHIP_DPC_EPS):
        daily_drop = (float(dpc_t) - float(dpc_t_1)) / float(dpc_t_1)
        if float(daily_drop) <= float(S_CHIP_TIER1_DAILY_DROP_THRESHOLD):
            tier1 = 5

    drop_days = 0
    for i in range(1, 5):
        if float(w[i]) < float(w[i - 1]):
            drop_days += 1
    tier2 = 3 if int(drop_days) >= int(S_CHIP_TIER2_DROP_DAYS_THRESHOLD) else 0

    tier3 = 0
    if float(dpc_t_4) >= float(S_CHIP_DPC_EPS):
        total_drop = (float(dpc_t) - float(dpc_t_4)) / float(dpc_t_4)
        if float(total_drop) <= float(S_CHIP_TIER3_TOTAL_DROP_THRESHOLD) and int(drop_days) >= int(S_CHIP_TIER2_DROP_DAYS_THRESHOLD):
            tier3 = 2

    score_int = int(tier1) + int(tier2) + int(tier3)
    if score_int > 10:
        score_int = 10
    if score_int == 8:
        score_int = 7
    out = float(score_int) / 10.0

    if out not in S_CHIP_ALLOWED_VALUES:
        raise AssertionError(f"S_chip invalid value: {out}")
    return out

