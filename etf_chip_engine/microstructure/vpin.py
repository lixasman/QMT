"""M3 — VPIN Calculator (ADV-dynamic buckets).

V2.1 changes (compliance patch)
--------------------------------
- Bucket size uses ADV_60 / n_target (cross-day comparable).
- AP filter applied **pre-bucket** to v_buy/v_sell (not post-hoc on VPIN).
- Bucket gate: ``n < min_buckets`` only (window does NOT raise the gate).
- ``vpin_window_used`` — can shrink when ``n < window`` but ``n >= min_buckets``.
- ``delta_vpin`` uses **vpin_raw** (avoid double filtering), returns 0.0
  (not NaN) when not comparable, with ``delta_vpin_valid = False``.
- ``VPINResult`` extended with vpin_window_used, adv_days_used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# ── Constants ──────────────────────────────────────────────────────────────
MIN_BUCKETS: int = 30   # Below this, VPIN statistics are unreliable


@dataclass(frozen=True)
class VPINResult:
    """VPIN computation output (V2.1 compliant)."""
    vpin_raw: float              # Raw VPIN (no AP filter, NaN if buckets < MIN)
    vpin_filtered: float         # AP-filtered VPIN
    vpin_max: float              # Max of filtered series
    vpin_series: np.ndarray      # Full filtered VPIN time-series
    n_buckets_actual: int        # Actual number of buckets generated
    # V2.1 fields
    delta_vpin: float = 0.0      # Day-over-day change (0.0 if not comparable)
    bucket_size: float = 0.0     # Bucket size used today
    delta_vpin_valid: bool = False  # Whether delta_vpin is comparable
    vpin_window_used: int = 0    # Actual sliding window used (may shrink)
    adv_days_used: int = 0       # ADV history days (0 = intraday fallback)
    ap_filtered_ratio: float = 0.0  # Proportion of volume affected by AP filter


def _ap_filter_weight(
    premium_rate: float,
    threshold: float = 0.003,
    saturate: float = 0.01,
) -> float:
    """AP arbitrage filter weight (V2.1 explicit formula).

    w = 1 - clip((|premium| - threshold) / (saturate - threshold), 0, 1)

    Applied **pre-bucket** to v_buy and v_sell before filling.
    Only affects vpin_filtered; vpin_raw stays unmodified.
    """
    excess = abs(premium_rate) - threshold
    if excess <= 0:
        return 1.0
    denom = saturate - threshold
    if denom <= 0:
        return 0.0
    return float(max(0.0, 1.0 - excess / denom))


class VPINCalculator:
    """VPIN calculator with ADV-dynamic buckets (V2.1 compliant).

    Parameters
    ----------
    n_buckets : int
        Target number of buckets (default 50).
    window : int
        Target sliding window for VPIN averaging (default 50).
        When actual buckets < window but >= min_buckets, window shrinks.
    min_buckets : int
        Minimum bucket count for valid VPIN (default 30).
    premium_threshold, premium_saturate : float
        AP filter parameters.
    """

    def __init__(
        self,
        *,
        n_buckets: int = 50,
        window: int = 50,
        min_buckets: int = MIN_BUCKETS,
        premium_threshold: float = 0.003,
        premium_saturate: float = 0.01,
    ):
        self.n_buckets = int(n_buckets)
        self.window = int(window)
        self.min_buckets = int(min_buckets)
        self.premium_threshold = float(premium_threshold)
        self.premium_saturate = float(premium_saturate)

    @staticmethod
    def _fill_buckets(
        vol_vals: np.ndarray,
        buy_vals: np.ndarray,
        sell_vals: np.ndarray,
        bucket_size: float,
    ) -> list[dict]:
        """Fill volume buckets of fixed size from v_buy/v_sell arrays."""
        if bucket_size <= 0:
            return []

        buckets: list[dict] = []
        current_buy = 0.0
        current_sell = 0.0
        accumulated = 0.0

        for i in range(len(vol_vals)):
            remaining = float(vol_vals[i])
            if remaining <= 0:
                continue
            snap_total = float(buy_vals[i]) + float(sell_vals[i])
            buy_ratio = float(buy_vals[i]) / max(snap_total, 1e-12)

            while remaining > 0:
                space = bucket_size - accumulated
                fill = min(remaining, space)
                current_buy += fill * buy_ratio
                current_sell += fill * (1.0 - buy_ratio)
                accumulated += fill
                remaining -= fill

                if accumulated >= bucket_size - 1e-6:
                    buckets.append({
                        "v_buy": current_buy,
                        "v_sell": current_sell,
                        "volume": current_buy + current_sell,
                    })
                    current_buy = 0.0
                    current_sell = 0.0
                    accumulated = 0.0
        return buckets

    def compute(
        self,
        snapshots: pd.DataFrame,
        *,
        premium_rates: Optional[pd.Series] = None,
        adv_60: Optional[float] = None,
        vpin_yesterday: Optional[float] = None,
        n_buckets_yesterday: Optional[int] = None,
        bucket_size_yesterday: Optional[float] = None,
    ) -> VPINResult:
        """Compute VPIN with V2.1 compliant logic.

        Parameters
        ----------
        snapshots : pd.DataFrame
            Must contain v_buy, v_sell, volume columns (from BVC output).
        premium_rates : pd.Series or None
            Premium rates aligned to snapshots.
        adv_60 : float or None
            60-day average daily volume.  If *None*, falls back to
            intraday total / n_buckets (V1 behaviour).
        vpin_yesterday : float or None
            Yesterday's VPIN **raw** for delta calculation.
        n_buckets_yesterday, bucket_size_yesterday : int/float or None
            Yesterday's bucket metadata for comparability check.
        """
        nan_result = VPINResult(
            vpin_raw=float("nan"), vpin_filtered=float("nan"),
            vpin_max=float("nan"),
            vpin_series=np.array([], dtype=np.float64),
            n_buckets_actual=0, delta_vpin=0.0,
            bucket_size=0.0, delta_vpin_valid=False,
        )

        if snapshots is None or snapshots.empty:
            return nan_result

        total_volume = float(snapshots["volume"].sum())
        if total_volume <= 0:
            return nan_result

        # Bucket size: prefer ADV_60-based (V2.1), fallback to intraday
        adv_days = 0
        if adv_60 is not None and adv_60 > 0:
            bucket_size = adv_60 / float(self.n_buckets)
            adv_days = 60
        else:
            bucket_size = total_volume / float(self.n_buckets)

        # ── Extract arrays ─────────────────────────────────────────────
        vol_vals = snapshots["volume"].to_numpy(dtype=np.float64, copy=False)
        buy_vals = snapshots["v_buy"].to_numpy(dtype=np.float64, copy=False)
        sell_vals = snapshots["v_sell"].to_numpy(dtype=np.float64, copy=False)
        prem_vals = (
            premium_rates.to_numpy(dtype=np.float64, copy=False)
            if premium_rates is not None
            else None
        )

        # ── Build RAW buckets (no AP filter) ───────────────────────────
        raw_buckets = self._fill_buckets(vol_vals, buy_vals, sell_vals, bucket_size)
        n = len(raw_buckets)

        # ── Gate: min_buckets ONLY (not window) ────────────────────────
        if n < self.min_buckets:
            return VPINResult(
                vpin_raw=float("nan"), vpin_filtered=float("nan"),
                vpin_max=float("nan"),
                vpin_series=np.array([], dtype=np.float64),
                n_buckets_actual=n, delta_vpin=0.0,
                bucket_size=bucket_size, delta_vpin_valid=False,
                adv_days_used=adv_days,
            )

        # ── Raw VPIN (from raw buckets, no AP filter) ──────────────────
        raw_oi = np.array(
            [abs(b["v_buy"] - b["v_sell"]) / max(b["volume"], 1e-8)
             for b in raw_buckets],
            dtype=np.float64,
        )
        # Window shrinkage: use min(window, n) for valid stats
        w = min(self.window, n)
        kernel = np.ones(w, dtype=np.float64) / float(w)
        vpin_raw_series = np.convolve(raw_oi, kernel, mode="valid")
        vpin_raw = float(vpin_raw_series[-1])

        # ── Build FILTERED buckets (AP filter pre-bucket) ──────────────
        ap_affected_volume = 0.0
        if prem_vals is not None:
            filt_buy = np.array(buy_vals, dtype=np.float64, copy=True)
            filt_sell = np.array(sell_vals, dtype=np.float64, copy=True)
            for i in range(len(prem_vals)):
                weight = _ap_filter_weight(
                    float(prem_vals[i]),
                    self.premium_threshold,
                    self.premium_saturate,
                )
                if weight < 1.0:
                    ap_affected_volume += float(vol_vals[i]) * (1.0 - weight)
                filt_buy[i] *= weight
                filt_sell[i] *= weight
            filt_buckets = self._fill_buckets(vol_vals, filt_buy, filt_sell, bucket_size)
        else:
            filt_buckets = raw_buckets

        # Build filtered VPIN series
        if len(filt_buckets) >= self.min_buckets:
            filt_oi = np.array(
                [abs(b["v_buy"] - b["v_sell"]) / max(b["volume"], 1e-8)
                 for b in filt_buckets],
                dtype=np.float64,
            )
            w_filt = min(self.window, len(filt_buckets))
            kernel_f = np.ones(w_filt, dtype=np.float64) / float(w_filt)
            vpin_filt_series = np.convolve(filt_oi, kernel_f, mode="valid")
            vpin_filt = float(vpin_filt_series[-1])
            vpin_max = float(np.max(vpin_filt_series))
        else:
            vpin_filt_series = vpin_raw_series
            vpin_filt = vpin_raw
            vpin_max = float(np.max(vpin_raw_series))

        ap_ratio = ap_affected_volume / total_volume if total_volume > 0 else 0.0

        # ── Delta VPIN (V2.1: uses vpin_raw, returns 0.0 when incomparable)
        delta_vpin = 0.0
        delta_valid = False
        if vpin_yesterday is not None and np.isfinite(vpin_yesterday):
            comparable = True
            if bucket_size_yesterday is not None and bucket_size_yesterday > 0:
                ratio = abs(bucket_size - bucket_size_yesterday) / bucket_size_yesterday
                if ratio > 0.3:
                    comparable = False
            if n_buckets_yesterday is not None and n_buckets_yesterday < self.min_buckets:
                comparable = False
            if comparable:
                delta_vpin = vpin_raw - vpin_yesterday  # V2.1: use raw
                delta_valid = True
            # else: delta_vpin stays 0.0, delta_valid stays False

        return VPINResult(
            vpin_raw=vpin_raw,
            vpin_filtered=vpin_filt,
            vpin_max=vpin_max,
            vpin_series=vpin_filt_series,
            n_buckets_actual=n,
            delta_vpin=delta_vpin,
            bucket_size=bucket_size,
            delta_vpin_valid=delta_valid,
            vpin_window_used=w,
            adv_days_used=adv_days,
            ap_filtered_ratio=ap_ratio,
        )
