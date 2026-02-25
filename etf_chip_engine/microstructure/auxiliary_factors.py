"""Auxiliary factor functions for the microstructure factor engine.

V2.1 changes
------------
- ``kyle_lambda``          → rewritten (Microprice, intercept, ddof=0, LambdaResult)
- ``volume_surprise``      → rewritten (log(V/ADV_60) cross-day formula)
- ``rv_bipower``           → NEW (Bipower Variation from close, zero-trade protection)
- ``vwap_deviation``       → NEW (VWAP dev with NaN for zero-volume intervals)
- ``normalize_robust_zscore`` → NEW (MAD-based robust z-score, cold-start → NaN)

Preserved unchanged (used by other modules / tests):
- ``safe_zscore``, ``safe_rank01`` — retained for backward compatibility
- ``latest_history``, ``premium_vpin_cross``, ``profit_ofi_divergence``, ``asr_velocity``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════
# M5 — Kyle's Lambda (V2.1 rewrite)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LambdaResult:
    """Return type for :func:`kyle_lambda` (V2.1).

    Attributes
    ----------
    lambda_val : float
        Price-impact coefficient λ (positive = normal market).
    intercept : float
        Regression intercept α (captures IOPV drift / basis reversion).
    r_squared : float
        R² of the regression (reliability indicator).
    n_obs : int
        Number of valid observations used.
    signed_vol_std : float
        Std of signed volume (degeneration detector).
    """
    lambda_val: float
    intercept: float
    r_squared: float
    n_obs: int
    signed_vol_std: float


# Minimum observations for a valid regression — frozen constant (V2.1)
_LAMBDA_MIN_OBS: int = 50


def kyle_lambda(
    microprice: np.ndarray,
    v_buy: np.ndarray,
    v_sell: np.ndarray,
    *,
    window: int = 1200,
) -> LambdaResult:
    """Compute Kyle's Lambda with V2.1 corrections.

    V2.1 changes vs V1
    -------------------
    1. Input is **Microprice** Δm (not close Δp).
    2. Includes intercept α to absorb IOPV drift.
    3. Manual population covariance (ddof=0) — fixes inconsistency where
       the old code mixed ``np.cov(ddof=1)`` with ``np.var(ddof=0)``.
    4. Returns ``LambdaResult`` (not float).
    5. Minimum observation gate ``n_obs < 50 → NaN``.

    Parameters
    ----------
    microprice : np.ndarray
        Microprice series from M1 (or close if microprice unavailable).
    v_buy, v_sell : np.ndarray
        BVC-classified buy/sell volume arrays (same length as *microprice*).
    window : int
        Tail window of snapshots to use (default 1200 ≈ 1 hour @ 3s).
    """
    mp = np.asarray(microprice, dtype=np.float64)
    vb = np.asarray(v_buy, dtype=np.float64)
    vs = np.asarray(v_sell, dtype=np.float64)

    n = min(int(window), len(mp))
    nan_result = LambdaResult(float("nan"), float("nan"), float("nan"), 0, 0.0)
    if n < _LAMBDA_MIN_OBS + 1:
        return nan_result

    dm = np.diff(mp[-n:])           # ΔM, length = n-1
    q = (vb[-n:] - vs[-n:])[1:]     # signed volume, aligned to dm

    n_obs = len(dm)
    sv_std = float(np.std(q)) if n_obs > 0 else 0.0

    if n_obs < _LAMBDA_MIN_OBS or sv_std < 1e-8:
        return LambdaResult(float("nan"), float("nan"), float("nan"), n_obs, sv_std)

    # Centered statistics (population, ddof=0)
    dm_mean = float(np.mean(dm))
    q_mean = float(np.mean(q))
    dm_c = dm - dm_mean
    q_c = q - q_mean

    pop_cov = float(np.mean(dm_c * q_c))
    pop_var = float(np.mean(q_c ** 2))

    if pop_var < 1e-12:
        return LambdaResult(float("nan"), float("nan"), float("nan"), n_obs, sv_std)

    lam = pop_cov / pop_var
    alpha = dm_mean - lam * q_mean

    # R² (with intercept)
    predicted = alpha + lam * q
    ss_res = float(np.sum((dm - predicted) ** 2))
    ss_tot = float(np.sum((dm - dm_mean) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else float("nan")

    return LambdaResult(
        lambda_val=float(lam),
        intercept=float(alpha),
        r_squared=float(r2),
        n_obs=n_obs,
        signed_vol_std=sv_std,
    )


# ═══════════════════════════════════════════════════════════════════════════
# M6 — Volume Surprise (V2.1 rewrite)
# ═══════════════════════════════════════════════════════════════════════════

def volume_surprise(total_volume: float, adv_60: float) -> float:
    """Compute Volume Surprise: log(V_today / ADV_60).

    V2.1 rewrite: the old version used intra-day EMA-based VS.
    The new version is a cross-day metric that compares today's total
    volume against the 60-day average daily volume.

    Used in M7 orthogonalization to remove volume-driven mechanical
    correlation from VPIN.

    Parameters
    ----------
    total_volume : float
        Today's total trading volume (shares).
    adv_60 : float
        60-day average daily volume (from factor_history).

    Returns
    -------
    float
        ``log(total_volume / adv_60)``, or ``NaN`` if either input is invalid.
    """
    if adv_60 <= 0 or total_volume <= 0:
        return float("nan")
    if not (np.isfinite(total_volume) and np.isfinite(adv_60)):
        return float("nan")
    return float(np.log(total_volume / adv_60))


# ═══════════════════════════════════════════════════════════════════════════
# M6 — RV Bipower (V2.1 new)
# ═══════════════════════════════════════════════════════════════════════════

# Minimum valid log-return samples — frozen constant (V2.1 fourth-round)
_MIN_RV_SAMPLES: int = 100


def rv_bipower(close: np.ndarray) -> dict[str, float | int]:
    """Compute Realized Volatility using Bipower Variation.

    Uses **close** price (not microprice) to avoid market-maker order-book
    noise generating phantom jumps (V2.1 P0-1 fix).

    Zero-trade protection (V2.1 fourth-round):
        Close stays flat during zero-trade periods, producing ``log_ret=0``.
        These are "no information", not "low volatility".  When fewer than
        ``_MIN_RV_SAMPLES`` non-zero log returns exist, output is NaN.

    Parameters
    ----------
    close : np.ndarray
        Close price snapshots (M0-preprocessed).

    Returns
    -------
    dict with keys:
        rv_bipower, rv_classical, jump_ratio, rv_valid_count
    """
    c = np.asarray(close, dtype=np.float64)
    nan_result: dict[str, float | int] = {
        "rv_bipower": float("nan"),
        "rv_classical": float("nan"),
        "jump_ratio": float("nan"),
        "rv_valid_count": 0,
    }
    if len(c) < 2:
        return nan_result

    log_ret = np.diff(np.log(np.maximum(c, 1e-8)))
    valid_count = int(np.sum(np.abs(log_ret) > 0))

    if valid_count < _MIN_RV_SAMPLES:
        nan_result["rv_valid_count"] = valid_count
        return nan_result

    rv_classical = float(np.sum(log_ret ** 2))
    bv = float((np.pi / 2) * np.sum(np.abs(log_ret[1:]) * np.abs(log_ret[:-1])))
    jump = max(0.0, 1.0 - bv / rv_classical) if rv_classical > 1e-12 else 0.0

    return {
        "rv_bipower": bv,
        "rv_classical": rv_classical,
        "jump_ratio": jump,
        "rv_valid_count": valid_count,
    }


# ═══════════════════════════════════════════════════════════════════════════
# M6 — VWAP Deviation (V2.1 new)
# ═══════════════════════════════════════════════════════════════════════════

def vwap_deviation(snapshots: pd.DataFrame) -> dict[str, float | int]:
    """Compute VWAP deviation with zero-volume NaN protection.

    V2.1 fix (third-round P0-2): the old ``clip(lower=1)`` created
    astronomical pseudo-deviations when ``cum_volume=0``.  Now outputs
    NaN for zero-volume intervals.

    Parameters
    ----------
    snapshots : pd.DataFrame
        Must contain columns: ``amount``, ``volume``, ``close``.

    Returns
    -------
    dict with keys:
        vwap_dev, vwap_dev_max, zero_volume_ratio, first_trade_index
    """
    nan_result: dict[str, float | int] = {
        "vwap_dev": float("nan"),
        "vwap_dev_max": float("nan"),
        "zero_volume_ratio": 1.0,
        "first_trade_index": -1,
    }
    if snapshots is None or snapshots.empty:
        return nan_result
    for col in ("amount", "volume", "close"):
        if col not in snapshots.columns:
            return nan_result

    total_amount = float(snapshots["amount"].sum())
    total_volume = float(snapshots["volume"].sum())
    zero_vol_ratio = float((snapshots["volume"] == 0).mean())
    first_trade_idx = int((snapshots["volume"] > 0).idxmax()) if total_volume > 0 else -1

    if total_volume < 1:
        nan_result["zero_volume_ratio"] = zero_vol_ratio
        nan_result["first_trade_index"] = first_trade_idx
        return nan_result

    vwap = total_amount / total_volume
    close_last = float(snapshots["close"].iloc[-1])
    dev = (close_last - vwap) / vwap if vwap > 1e-8 else 0.0

    # Running VWAP for intraday max deviation (NaN for zero-volume prefix)
    cum_amt = snapshots["amount"].cumsum()
    cum_vol = snapshots["volume"].cumsum()
    has_vol = cum_vol > 0
    running_vwap = pd.Series(float("nan"), index=snapshots.index)
    running_vwap[has_vol] = cum_amt[has_vol] / cum_vol[has_vol]
    running_dev = (snapshots["close"] - running_vwap) / running_vwap
    dev_max = float(running_dev.abs().max(skipna=True))
    if np.isnan(dev_max):
        dev_max = 0.0

    return {
        "vwap_dev": float(dev),
        "vwap_dev_max": dev_max,
        "zero_volume_ratio": zero_vol_ratio,
        "first_trade_index": first_trade_idx,
    }


# ═══════════════════════════════════════════════════════════════════════════
# M8 helper — Robust Z-Score (V2.1 new)
# ═══════════════════════════════════════════════════════════════════════════

def normalize_robust_zscore(
    current: float,
    history: np.ndarray,
    *,
    clip: float = 3.0,
) -> float:
    """MAD-based robust z-score normalization.

    V2.1 changes vs ``safe_zscore``
    --------------------------------
    1. Uses **Median Absolute Deviation** (MAD) instead of mean/std.
    2. Cold-start (``len(history) < 5``) → **NaN** (not 0.0).
    3. MAD ≈ 0 → ``sign(current - median) * clip`` (not 0.0).

    Parameters
    ----------
    current : float
        Today's raw factor value.
    history : np.ndarray
        Historical values (may contain NaN).
    clip : float
        Symmetric clip range for the output (default 3.0).
    """
    if not np.isfinite(current):
        return float("nan")

    h = np.asarray(history, dtype=np.float64)
    finite = h[np.isfinite(h)]
    if len(finite) < 5:
        return float("nan")

    med = float(np.median(finite))
    mad = float(np.median(np.abs(finite - med)))

    if mad < 1e-8:
        # Degenerate: all values are nearly identical
        diff = float(current) - med
        if abs(diff) < 1e-12:
            return 0.0
        return float(np.sign(diff)) * clip

    # Consistent estimator: σ ≈ 1.4826 × MAD
    sigma_mad = 1.4826 * mad
    z = (float(current) - med) / sigma_mad
    return float(np.clip(z, -clip, clip))


# ═══════════════════════════════════════════════════════════════════════════
# Legacy / utility functions (PRESERVED for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════

def safe_zscore(current: float, history: np.ndarray) -> float:
    """Mean/std z-score (V1 — DEPRECATED, use :func:`normalize_robust_zscore`)."""
    if history.size < 5 or not np.isfinite(current):
        return 0.0
    mu = float(np.nanmean(history))
    sigma = float(np.nanstd(history))
    if sigma < 1e-8:
        return 0.0
    z = (float(current) - mu) / sigma
    if z > 3:
        return 3.0
    if z < -3:
        return -3.0
    return float(z)


def safe_rank01(current: float, history: np.ndarray) -> float:
    """Percentile rank in [0, 1] (V1 — DEPRECATED)."""
    if history.size < 5 or not np.isfinite(current):
        return 0.5
    return float(np.sum(history < current) / max(len(history), 1))


def latest_history(arr: Optional[np.ndarray]) -> np.ndarray:
    """Extract finite values from a history array."""
    if arr is None:
        return np.array([], dtype=np.float64)
    out = np.asarray(arr, dtype=np.float64)
    out = out[np.isfinite(out)]
    return out


def premium_vpin_cross(premium_rate: float, vpin_raw: float) -> float:
    """Cross factor: premium × VPIN interaction.

    V2.1: uses vpin_raw (not vpin_rank), avoids double filtering.
    """
    if not (np.isfinite(premium_rate) and np.isfinite(vpin_raw)):
        return float("nan")
    if abs(float(premium_rate)) < 0.001:
        return float(vpin_raw)
    penalty = min(abs(float(premium_rate)) / 0.005, 1.0)
    return float(vpin_raw) * max(0.0, 1.0 - penalty)


def profit_ofi_divergence(profit_ratio: float, ofi_daily_z: float) -> float:
    """Cross factor: profit ratio × OFI divergence.

    V2.1: bidirectional — preserves sign of ofi_daily_z.
    Positive = high profit + positive OFI (chasing).
    Negative = high profit + negative OFI (sell pressure).
    """
    if not (np.isfinite(profit_ratio) and np.isfinite(ofi_daily_z)):
        return float("nan")
    profit_excess = max(0.0, (float(profit_ratio) - 50.0) / 50.0)
    return profit_excess * float(ofi_daily_z)


def asr_velocity(
    asr_today: float, asr_yesterday: float, asr_5d_ago: float | None = None,
) -> dict[str, float]:
    """Cross factor: ASR velocity (V2.1 multi-scale).

    Returns dict with asr_velocity_1d and asr_velocity_5d.
    """
    v1d = float("nan")
    if np.isfinite(asr_today) and np.isfinite(asr_yesterday):
        v1d = float(asr_today) - float(asr_yesterday)
    v5d = float("nan")
    if asr_5d_ago is not None and np.isfinite(asr_today) and np.isfinite(asr_5d_ago):
        v5d = (float(asr_today) - float(asr_5d_ago)) / 5.0
    return {"asr_velocity_1d": v1d, "asr_velocity_5d": v5d}
