from __future__ import annotations

from datetime import time

TICK_SIZE = 0.001

ETF_LIMIT_UP_PCT = 0.10
ETF_LIMIT_DOWN_PCT = 0.10

L1_STALE_THRESHOLD_SEC = 15.0

ENTRY_CUTOFF_TIME = time(14, 30)
EXIT_BUYBACK_CUTOFF_TIME = time(14, 30)

T0_REGIME_DECISION_TIME = time(9, 26)

RESERVE_CASH_CAP = 60000.0

TRADING_SESSIONS = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)
