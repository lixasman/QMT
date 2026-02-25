"""M2 — Bulk Volume Classifier (three-level degradation compensation).

V2.1 changes
-------------
- Three-level signal: Level 1 (close diff), Level 2 (microprice diff with
  min threshold), Level 3 (historical lookback).
- Per-level rolling σ to avoid magnitude mixing.
- Returns ``BVCResult`` dataclass with ``bvc_quality`` diagnostic.
- New ``microprice`` parameter required from M1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ── Constants ──────────────────────────────────────────────────────────────
TICK_SIZE: float = 0.001
BVC_LEVEL2_MIN_DM: float = 2 * TICK_SIZE  # 0.002, V2.1


@dataclass(frozen=True)
class BVCResult:
    """Output of :meth:`BulkVolumeClassifier.classify`."""
    v_buy: np.ndarray
    v_sell: np.ndarray
    bvc_quality: float                       # [0,1], 1 = all Level 1
    level_counts: dict = field(default_factory=dict)  # {"L1":n, "L2":n, ...}


# ── Signal computation ────────────────────────────────────────────────────

def _compute_signal(
    close: np.ndarray,
    microprice: np.ndarray,
    *,
    k_max: int = 10,
    level2_min_dm: float = BVC_LEVEL2_MIN_DM,
) -> tuple[np.ndarray, np.ndarray]:
    """Three-level degradation compensation (V2.1).

    Returns
    -------
    signal : np.ndarray   — the price-change signal for each snapshot
    levels : np.ndarray   — 0=N/A, 1=L1, 2=L2, 3=L3, 4=fallback
    """
    n = len(close)
    signal = np.zeros(n, dtype=np.float64)
    levels = np.zeros(n, dtype=np.int32)  # t=0 stays 0 (no prior)

    for t in range(1, n):
        # Level 1: close diff
        dp = close[t] - close[t - 1]
        if abs(dp) > 1e-9:
            signal[t] = dp
            levels[t] = 1
            continue

        # Level 2: microprice diff (needs > 2×TICK_SIZE, V2.1)
        dm = microprice[t] - microprice[t - 1]
        if abs(dm) > level2_min_dm:
            signal[t] = dm
            levels[t] = 2
            continue

        # Level 3: historical lookback
        found = False
        for k in range(2, min(k_max + 1, t + 1)):
            dp_k = close[t] - close[t - k]
            if abs(dp_k) > 1e-9:
                signal[t] = dp_k
                levels[t] = 3
                found = True
                break

        if not found:
            signal[t] = 0.0  # Φ(0) = 0.5
            levels[t] = 4

    return signal, levels


def _compute_per_level_sigma(
    signal: np.ndarray,
    levels: np.ndarray,
    lookback: int = 100,
) -> np.ndarray:
    """Per-level rolling σ (V2.1 fix for magnitude mixing)."""
    n = len(signal)
    sigma = np.full(n, 1e-8, dtype=np.float64)

    # Global fallback σ
    all_series = pd.Series(signal[1:])  # exclude t=0
    global_sigma = all_series.expanding(min_periods=5).std().values
    if len(global_sigma) > 0:
        sigma[1:] = np.maximum(global_sigma, 1e-8)

    for lv in (1, 2, 3):
        lv_mask = levels == lv
        lv_indices = np.where(lv_mask)[0]
        if len(lv_indices) < 5:
            continue
        lv_signals = signal[lv_mask]
        lv_series = pd.Series(lv_signals)
        rolling_std = lv_series.rolling(lookback, min_periods=5).std()
        expanding_std = lv_series.expanding(min_periods=5).std()
        lv_std = rolling_std.fillna(expanding_std).values
        sigma[lv_indices] = np.maximum(lv_std, 1e-8)

    return sigma


def _phi(z: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ── Classifier ─────────────────────────────────────────────────────────────

class BulkVolumeClassifier:
    """Three-level BVC (V2.1).

    Parameters
    ----------
    lookback : int
        Rolling window for σ computation (default 100).
    min_periods : int
        Minimum periods for rolling σ (default 20).
    k_max : int
        Level 3 max lookback steps (default 10, ≈ 30 seconds at 3s).
    level2_min_dm : float
        Level 2 minimum Microprice diff threshold (default 0.002).
    """

    def __init__(
        self,
        *,
        lookback: int = 100,
        min_periods: int = 20,
        k_max: int = 10,
        level2_min_dm: float = BVC_LEVEL2_MIN_DM,
    ):
        self.lookback = int(lookback)
        self.min_periods = int(min_periods)
        self.k_max = int(k_max)
        self.level2_min_dm = float(level2_min_dm)

    def classify(
        self,
        snapshots: pd.DataFrame,
        microprice: np.ndarray | None = None,
    ) -> BVCResult:
        """Classify each snapshot's volume into buy/sell.

        Parameters
        ----------
        snapshots : pd.DataFrame
            Must contain ``close`` and ``volume`` columns.
        microprice : np.ndarray or None
            Microprice series from M1.  If *None*, falls back to V1
            behaviour (close-only CDF).

        Returns
        -------
        BVCResult
        """
        if snapshots.empty:
            return BVCResult(
                v_buy=np.array([], dtype=np.float64),
                v_sell=np.array([], dtype=np.float64),
                bvc_quality=0.0,
                level_counts={},
            )

        close = snapshots["close"].to_numpy(dtype=np.float64, copy=False)
        vol = snapshots["volume"].to_numpy(dtype=np.float64, copy=False)
        n = len(close)

        if microprice is not None and len(microprice) == n:
            mp = np.asarray(microprice, dtype=np.float64)
            signal, levels = _compute_signal(
                close, mp,
                k_max=self.k_max,
                level2_min_dm=self.level2_min_dm,
            )
            sigma = _compute_per_level_sigma(signal, levels, lookback=self.lookback)
        else:
            # V1 fallback: close-open diff with rolling σ
            if "open" in snapshots.columns:
                dp = (snapshots["close"].astype(float) - snapshots["open"].astype(float)).to_numpy(dtype=np.float64)
            else:
                dp = np.diff(close, prepend=close[0])
            sigma_series = pd.Series(dp).rolling(self.lookback, min_periods=self.min_periods).std()
            sigma = sigma_series.fillna(1e-8).to_numpy(dtype=np.float64)
            sigma = np.maximum(sigma, 1e-8)
            signal = dp
            levels = np.ones(n, dtype=np.int32)  # all "Level 1" equivalent

        # Compute buy_pct via Φ(z)
        z = np.zeros(n, dtype=np.float64)
        mask = sigma > 1e-8
        z[mask] = signal[mask] / sigma[mask]

        erf = np.vectorize(math.erf, otypes=[np.float64])
        buy_pct = 0.5 * (1.0 + erf(z / math.sqrt(2.0)))

        v_buy = vol * buy_pct
        v_sell = vol - v_buy

        # Also write back to df for backward compatibility (BVC consumers
        # that still read df["v_buy"], df["v_sell"])
        # Note: we don't mutate the input — just return BVCResult
        # Callers can do snapshots["v_buy"] = result.v_buy if needed.

        # Quality: 1 - fallback_ratio
        n_classified = max(int(np.sum(levels > 0)), 1)
        n_fallback = int(np.sum(levels == 4))
        quality = 1.0 - n_fallback / n_classified

        lc = {
            "L1": int(np.sum(levels == 1)),
            "L2": int(np.sum(levels == 2)),
            "L3": int(np.sum(levels == 3)),
            "fallback": n_fallback,
        }

        return BVCResult(
            v_buy=v_buy,
            v_sell=v_sell,
            bvc_quality=float(quality),
            level_counts=lc,
        )
