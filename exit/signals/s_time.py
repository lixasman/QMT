from __future__ import annotations

from ..constants import S_TIME_MIN_DAYS_HELD, S_TIME_MIN_DAYS_SINCE_HIGH, S_TIME_MIN_RETURN


def compute_s_time(*, days_held: int, days_since_high: int, current_return: float) -> float:
    dh = int(days_held)
    dsh = int(days_since_high)
    r = float(current_return)
    if dh > int(S_TIME_MIN_DAYS_HELD) and dsh >= int(S_TIME_MIN_DAYS_SINCE_HIGH) and r >= float(S_TIME_MIN_RETURN):
        return 1.0
    return 0.0

