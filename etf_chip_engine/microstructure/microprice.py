"""M1 — Microprice computation.

Microprice is the volume-weighted equilibrium price from the Level-1 order
book.  It serves as the price anchor for BVC (M2) and Kyle's Lambda (M5).

V2.1: Includes limit-locked protection — when ask1 ≤ 0 (limit-up) or
bid1 ≤ 0 (limit-down), Microprice degenerates to half the remaining side
price (a "price halving" artifact).  The fix uses ``close`` instead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_microprice(snapshots: pd.DataFrame) -> np.ndarray:
    """Compute the Microprice series with limit-locked protection.

    Formula
    -------
    M_t = (Bid1 × AskVol1 + Ask1 × BidVol1) / (BidVol1 + AskVol1)

    When BidVol1 + AskVol1 == 0, falls back to midprice.
    When bid1 ≤ 0 or ask1 ≤ 0 (limit-locked), falls back to ``close``.

    Parameters
    ----------
    snapshots : pd.DataFrame
        Must contain columns: bid1, bid1_vol, ask1, ask1_vol, close.

    Returns
    -------
    np.ndarray
        Microprice array, same length as *snapshots*.
    """
    bid_p = snapshots["bid1"].to_numpy(dtype=np.float64, copy=False)
    bid_v = snapshots["bid1_vol"].to_numpy(dtype=np.float64, copy=False)
    ask_p = snapshots["ask1"].to_numpy(dtype=np.float64, copy=False)
    ask_v = snapshots["ask1_vol"].to_numpy(dtype=np.float64, copy=False)
    close = snapshots["close"].to_numpy(dtype=np.float64, copy=False)

    total_vol = bid_v + ask_v

    # Normal microprice
    safe_total = np.where(total_vol > 0, total_vol, 1.0)
    microprice = (bid_p * ask_v + ask_p * bid_v) / safe_total

    # Fall back to midprice when total_vol == 0
    midprice = (bid_p + ask_p) / 2.0
    microprice = np.where(total_vol > 0, microprice, midprice)

    # Limit-locked protection (V2.1): use close instead of degenerate price
    limit_mask = (ask_p <= 0) | (bid_p <= 0)
    microprice[limit_mask] = close[limit_mask]

    return microprice
