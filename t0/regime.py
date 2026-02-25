from __future__ import annotations

from datetime import datetime, time

from .constants import REGIME_ATR5_PERCENTILE_THRESHOLD, REGIME_AUCTION_VOL_RATIO_THRESHOLD
from .types import RegimeResult


def compute_regime(*, auction_vol_ratio: float, atr5_percentile: float, computed_at: datetime) -> RegimeResult:
    ts = computed_at
    if ts.time() > time(9, 26):
        raise AssertionError(f"Regime 判定时间异常: {ts}（应在 09:26 前完成）")

    av = float(auction_vol_ratio)
    ap = float(atr5_percentile)

    by_auction = av > float(REGIME_AUCTION_VOL_RATIO_THRESHOLD)
    by_atr = ap > float(REGIME_ATR5_PERCENTILE_THRESHOLD)

    active = bool(by_auction or by_atr)

    reason = "none"
    if by_auction and by_atr:
        reason = "both"
    elif by_auction:
        reason = "auction_vol"
    elif by_atr:
        reason = "atr_pct"

    return RegimeResult(
        regime_active=bool(active),
        reason=str(reason),
        auction_vol_ratio=float(av),
        atr5_percentile=float(ap),
        computed_at=ts,
    )
