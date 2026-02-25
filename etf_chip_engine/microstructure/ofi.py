"""M4 — Continuous Stoikov OFI (Order Flow Imbalance).

V2.1 extensions
----------------
- Tick integerization for price comparison (avoids float == pitfalls).
- Market-maker symmetric quote filter.
- Shape features: ofi_skew, ofi_95th, ofi_price_divergence, ofi_am_sum,
  ofi_pm_sum, ofi_pos_ratio.
- Quality diagnostics: gap_count, mm_filtered_ratio, limit_locked_ratio.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ── Constants ─────────────────────────────────────────────────────────────
TICK_SIZE: float = 0.001
MM_DEPTH_EPSILON: float = 0.05
OFI_DIVERGENCE_MIN_SAMPLES: int = 500
OFI_DIVERGENCE_MIN_MOVES: int = 50
GAP_THRESHOLD_MS: int = 6000  # Snapshot gap threshold (milliseconds)

# AM/PM session boundaries (seconds since midnight)
_AM_END: int = 11 * 3600 + 30 * 60      # 11:30
_PM_START: int = 13 * 3600               # 13:00


# ── Helpers ───────────────────────────────────────────────────────────────

def tick_integerize(price: float, tick_size: float = TICK_SIZE) -> int:
    """Convert float price to tick integer for safe comparison."""
    return int(round(price / tick_size))


def _is_mm_symmetric(
    bid_prev: float, bid_curr: float,
    ask_prev: float, ask_curr: float,
    bid_vol_prev: float, bid_vol_curr: float,
    ask_vol_prev: float, ask_vol_curr: float,
) -> bool:
    """Detect market-maker symmetric quote adjustment."""
    d_bid = bid_curr - bid_prev
    d_ask = ask_curr - ask_prev
    # Condition 1: same-direction, same-distance shift
    if abs(d_bid) < 1e-9 or abs(d_bid - d_ask) > 1e-9:
        return False
    # Condition 2: depth rigidity
    if bid_vol_prev > 0 and abs(bid_vol_curr - bid_vol_prev) / bid_vol_prev > MM_DEPTH_EPSILON:
        return False
    if ask_vol_prev > 0 and abs(ask_vol_curr - ask_vol_prev) / ask_vol_prev > MM_DEPTH_EPSILON:
        return False
    return True


# ── OFIResult ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OFIResult:
    """Extended OFI output (V2.1)."""
    ofi_daily: float
    ofi_20: float
    ofi_60: float
    ofi_240: float
    ofi_series: np.ndarray
    available: bool
    # Shape features (V2.1)
    ofi_skew: float = 0.0
    ofi_95th: float = 0.0
    ofi_price_divergence: float = 0.0
    ofi_am_sum: float = 0.0
    ofi_pm_sum: float = 0.0
    ofi_pos_ratio: float = 0.0
    queue_pressure: float = 0.0
    # Quality diagnostics
    gap_count: int = 0
    mm_filtered_ratio: float = 0.0
    limit_locked_ratio: float = 0.0


def _nan_ofi_result() -> OFIResult:
    return OFIResult(
        ofi_daily=float("nan"), ofi_20=float("nan"),
        ofi_60=float("nan"), ofi_240=float("nan"),
        ofi_series=np.array([], dtype=np.float64), available=False,
        ofi_skew=float("nan"), ofi_95th=float("nan"),
        ofi_price_divergence=float("nan"),
        ofi_am_sum=float("nan"), ofi_pm_sum=float("nan"),
        ofi_pos_ratio=float("nan"),
        queue_pressure=float("nan"),
    )


# ── Main class ────────────────────────────────────────────────────────────

class ContStoikovOFI:
    """Continuous Stoikov OFI with V2.1 extensions."""

    def compute(
        self,
        snapshots: pd.DataFrame,
        *,
        microprice: np.ndarray | None = None,
        limit_locked_mask: pd.Series | None = None,
        valid_mask: pd.Series | None = None,
    ) -> OFIResult:
        """Compute OFI with shape features and quality diagnostics.

        Parameters
        ----------
        snapshots : pd.DataFrame
            Must contain bid1, bid1_vol, ask1, ask1_vol columns.
        microprice : np.ndarray or None
            Microprice series for divergence calculation.
        limit_locked_mask : pd.Series or None
            Boolean mask from M0 preprocessor.
        valid_mask : pd.Series or None
            V2.1: valid_continuous_mask from M0. When False, OFI is set to
            NaN (excluding non-auction snapshots like lunch break).
        """
        if snapshots is None or getattr(snapshots, "empty", True):
            return _nan_ofi_result()
        required = {"bid1", "bid1_vol", "ask1", "ask1_vol"}
        if not required.issubset(set(snapshots.columns)):
            return _nan_ofi_result()

        bid_p = snapshots["bid1"].to_numpy(dtype=np.float64, copy=False)
        bid_v = snapshots["bid1_vol"].to_numpy(dtype=np.float64, copy=False)
        ask_p = snapshots["ask1"].to_numpy(dtype=np.float64, copy=False)
        ask_v = snapshots["ask1_vol"].to_numpy(dtype=np.float64, copy=False)

        if not (np.any(bid_p > 0) and np.any(ask_p > 0)):
            return _nan_ofi_result()

        n = len(snapshots)
        ofi = np.zeros(n, dtype=np.float64)
        mm_filtered_count = 0
        locked_count = 0
        gap_count = 0
        queue_pressure = 0.0

        # Pre-compute time gaps for gap detection (V2.1: >6s → NaN)
        gap_mask = np.zeros(n, dtype=bool)
        if "time" in snapshots.columns:
            times = snapshots["time"].to_numpy(copy=False)
            for t in range(1, n):
                try:
                    diff_ms = abs(int(times[t]) - int(times[t - 1]))
                except (TypeError, ValueError):
                    diff_ms = 0
                if diff_ms > GAP_THRESHOLD_MS:
                    gap_mask[t] = True

        for t in range(1, n):
            # Gap detection: >6s between snapshots → NaN
            if gap_mask[t]:
                ofi[t] = float("nan")
                gap_count += 1
                continue

            # Limit-locked snapshots: compute OFI contribution, but do NOT
            # include in ofi_daily aggregation; accumulate into queue_pressure.
            if limit_locked_mask is not None and limit_locked_mask.iloc[t]:
                bp_prev = tick_integerize(bid_p[t - 1])
                bp_curr = tick_integerize(bid_p[t])
                ap_prev = tick_integerize(ask_p[t - 1])
                ap_curr = tick_integerize(ask_p[t])

                if bp_curr > bp_prev:
                    e_bid = bid_v[t]
                elif bp_curr == bp_prev:
                    e_bid = bid_v[t] - bid_v[t - 1]
                else:
                    e_bid = -bid_v[t - 1]

                if ap_curr < ap_prev:
                    e_ask = -ask_v[t]
                elif ap_curr == ap_prev:
                    e_ask = -(ask_v[t] - ask_v[t - 1])
                else:
                    e_ask = ask_v[t - 1]

                ofi_val = float(e_bid + e_ask)
                queue_pressure += ofi_val
                ofi[t] = float("nan")
                locked_count += 1
                continue

            # V2.1: valid_continuous_mask — exclude non-auction snapshots
            if valid_mask is not None and not valid_mask.iloc[t]:
                ofi[t] = float("nan")
                gap_count += 1
                continue

            # Market-maker symmetric filter
            if _is_mm_symmetric(
                bid_p[t - 1], bid_p[t], ask_p[t - 1], ask_p[t],
                bid_v[t - 1], bid_v[t], ask_v[t - 1], ask_v[t],
            ):
                ofi[t] = 0.0
                mm_filtered_count += 1
                continue

            # Stoikov OFI with tick-integerized price comparison
            bp_prev = tick_integerize(bid_p[t - 1])
            bp_curr = tick_integerize(bid_p[t])
            ap_prev = tick_integerize(ask_p[t - 1])
            ap_curr = tick_integerize(ask_p[t])

            if bp_curr > bp_prev:
                e_bid = bid_v[t]
            elif bp_curr == bp_prev:
                e_bid = bid_v[t] - bid_v[t - 1]
            else:
                e_bid = -bid_v[t - 1]

            if ap_curr < ap_prev:
                e_ask = -ask_v[t]
            elif ap_curr == ap_prev:
                e_ask = -(ask_v[t] - ask_v[t - 1])
            else:
                e_ask = ask_v[t - 1]

            ofi[t] = e_bid + e_ask

        # ── Sub-window aggregations (V2.1: nansum to exclude gap NaN) ──
        total = float(np.nansum(ofi))
        ofi_20 = float(np.nansum(ofi[-20:])) if n >= 20 else total
        ofi_60 = float(np.nansum(ofi[-60:])) if n >= 60 else total
        ofi_240 = float(np.nansum(ofi[-240:])) if n >= 240 else total

        # ── AM / PM split ──────────────────────────────────────────────
        am_mask = np.zeros(n, dtype=bool)
        pm_mask = np.zeros(n, dtype=bool)
        if "time" in snapshots.columns:
            from etf_chip_engine.microstructure.preprocessor import _time_to_seconds
            times = snapshots["time"].apply(_time_to_seconds).values
            am_mask = times <= _AM_END
            pm_mask = times >= _PM_START
        # Fallback: first half / second half
        if not np.any(am_mask) and not np.any(pm_mask):
            mid = n // 2
            am_mask[:mid] = True
            pm_mask[mid:] = True

        ofi_am_sum = float(np.nansum(ofi[am_mask]))
        ofi_pm_sum = float(np.nansum(ofi[pm_mask]))

        # ── Shape features ─────────────────────────────────────────────
        finite_nonzero = ofi[np.isfinite(ofi) & (ofi != 0)]

        # Skewness
        if len(finite_nonzero) >= 3:
            mu = float(np.mean(finite_nonzero))
            std = float(np.std(finite_nonzero))
            ofi_skew = float(np.mean(((finite_nonzero - mu) / max(std, 1e-8)) ** 3)) if std > 1e-8 else 0.0
        else:
            ofi_skew = 0.0

        # 95th percentile of |OFI|
        abs_ofi = np.abs(ofi[1:])  # skip t=0
        abs_ofi = abs_ofi[np.isfinite(abs_ofi)]
        ofi_95th = float(np.percentile(abs_ofi, 95)) if len(abs_ofi) > 0 else 0.0

        # Positive ratio
        ofi_tail = ofi[1:]
        tail_finite = np.isfinite(ofi_tail)
        pos_count = int(np.sum(ofi_tail[tail_finite] > 0))
        total_nonzero = max(int(np.sum(ofi_tail[tail_finite] != 0)), 1)
        ofi_pos_ratio = pos_count / total_nonzero

        # ── Price divergence (V2.1 stationary) ─────────────────────────
        ofi_price_div = 0.0
        if microprice is not None and len(microprice) == n:
            mp = np.asarray(microprice, dtype=np.float64)
            dm = np.diff(mp)  # ΔMicroprice, length n-1
            ofi_for_corr = ofi[1:]  # align with dm

            # Finite mask
            finite = np.isfinite(ofi_for_corr) & np.isfinite(dm)
            n_finite = int(finite.sum())

            if n_finite >= OFI_DIVERGENCE_MIN_SAMPLES:
                dm_finite = dm[finite]
                # Second gate: minimum price moves
                n_moves = int(np.sum(np.abs(dm_finite) >= TICK_SIZE))
                if n_moves >= OFI_DIVERGENCE_MIN_MOVES:
                    ofi_f = ofi_for_corr[finite]
                    rho = np.corrcoef(ofi_f, dm_finite)
                    ofi_price_div = float(rho[0, 1]) if np.isfinite(rho[0, 1]) else 0.0

        # ── Quality diagnostics ────────────────────────────────────────
        mm_ratio = mm_filtered_count / max(n - 1, 1)
        ll_ratio = locked_count / max(n - 1, 1)

        return OFIResult(
            ofi_daily=total,
            ofi_20=ofi_20,
            ofi_60=ofi_60,
            ofi_240=ofi_240,
            ofi_series=ofi,
            available=True,
            ofi_skew=ofi_skew,
            ofi_95th=ofi_95th,
            ofi_price_divergence=ofi_price_div,
            ofi_am_sum=ofi_am_sum,
            ofi_pm_sum=ofi_pm_sum,
            ofi_pos_ratio=ofi_pos_ratio,
            queue_pressure=float(queue_pressure),
            gap_count=gap_count,
            mm_filtered_ratio=mm_ratio,
            limit_locked_ratio=ll_ratio,
        )
