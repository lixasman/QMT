"""M8 — Feature standardization pipeline.

V2.1 changes
-------------
- ``normalize_rank`` cold-start → NaN (was 0.5).
- ``normalize_robust_zscore`` (MAD-based) replaces ``safe_zscore``.
- ``normalize_log_robust_zscore`` for right-skewed distributions.
- ``cross_sectional_rank`` (NaN-aware, average-ties) — called in
  aggregation layer, NOT per-ETF.
- OFI-Price Divergence passed through directly (already [-1,1]).
- ``build_daily_features`` expanded with V2.1 factor map.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

try:
    from scipy.stats import rankdata as _scipy_rankdata
except ImportError:  # pragma: no cover
    _scipy_rankdata = None  # type: ignore[assignment]


class FeaturePipeline:
    """Standardize raw factors into ML-ready features (V2.1).

    Parameters
    ----------
    history_days : int
        Rolling history window for time-series z-scores (default 60).
    """

    def __init__(self, *, history_days: int = 60):
        self.history_days = int(history_days)

    # ── Normalization primitives ──────────────────────────────────────

    def normalize_rank(self, current: float, history: np.ndarray) -> float:
        """Rolling rank → [0, 1].

        V2.1: cold-start → NaN (not 0.5).
        """
        h = np.asarray(history, dtype=np.float64)
        finite = h[np.isfinite(h)]
        if len(finite) < 5 or not np.isfinite(current):
            return float("nan")
        return float(np.sum(finite < current) / len(finite))

    def normalize_robust_zscore(
        self, current: float, history: np.ndarray, *, clip_range: float = 3.0,
    ) -> float:
        """MAD-based robust z-score → clipped to [-clip_range, clip_range].

        V2.1: cold-start → NaN (not 0.0).
        V2.1 fix (third-round P0-1): MAD ≈ 0 + diff → ±clip (not 0.0).
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
            diff = float(current) - med
            if abs(diff) < 1e-12:
                return 0.0
            return float(np.sign(diff)) * clip_range

        z = (float(current) - med) / (1.4826 * mad)
        return float(np.clip(z, -clip_range, clip_range))

    def normalize_log_robust_zscore(
        self, current: float, history: np.ndarray,
    ) -> float:
        """Log1p + Robust z-score (for right-skewed distributions)."""
        if not np.isfinite(current) or current < 0:
            log_c = float("nan")
        else:
            log_c = float(np.log1p(current))
        h = np.asarray(history, dtype=np.float64)
        finite = h[np.isfinite(h)]
        log_h = np.log1p(np.maximum(finite, 0.0))
        return self.normalize_robust_zscore(log_c, log_h)

    def cross_sectional_rank(self, all_etf_values: dict[str, float]) -> dict[str, float]:
        """Cross-sectional rank → [-0.5, 0.5] (V2.1).

        NaN values are excluded from ranking and remain NaN in output.
        Ties use average rank (scipy.stats.rankdata).

        ★ Call this in the aggregation layer (daily_batch.py), NOT per-ETF.
        """
        codes = list(all_etf_values.keys())
        values = np.array([float(all_etf_values[c]) for c in codes], dtype=np.float64)
        valid_mask = np.isfinite(values)
        result: dict[str, float] = {c: float("nan") for c in codes}

        valid_codes = [c for c, v in zip(codes, valid_mask) if v]
        valid_vals = values[valid_mask]

        if len(valid_vals) < 2:
            return result

        if _scipy_rankdata is not None:
            ranks = _scipy_rankdata(valid_vals, method="average")
        else:
            # Fallback: simple rank (no tie handling)
            order = np.argsort(valid_vals)
            ranks = np.empty_like(valid_vals)
            ranks[order] = np.arange(1, len(valid_vals) + 1, dtype=np.float64)

        ranks = (ranks - 1) / max(len(ranks) - 1, 1) - 0.5

        for c, r in zip(valid_codes, ranks):
            result[c] = float(r)
        return result

    # ── Legacy compatibility wrappers ─────────────────────────────────

    def normalize_vpin_rank(self, current: float, history: np.ndarray) -> float:
        """V1 compat alias for :meth:`normalize_rank`."""
        return self.normalize_rank(current, history)

    def normalize_z(self, current: float, history: np.ndarray) -> float:
        """V1 compat alias → now uses robust z-score."""
        return self.normalize_robust_zscore(current, history)

    def normalize_log_z(self, current: float, history: np.ndarray) -> float:
        """V1 compat alias → now uses log + robust z-score."""
        return self.normalize_log_robust_zscore(current, history)

    # ── Main builder ─────────────────────────────────────────────────

    def build_daily_features(
        self,
        today_raw: dict[str, float],
        history: dict[str, np.ndarray],
    ) -> dict[str, float]:
        """Build per-ETF time-series standardized features (V2.1).

        Downstream backward-compatible keys are preserved:
        ``vpin_rank``, ``vpin_max_rank``, ``ofi_daily_z``,
        ``kyle_lambda_z``, ``vs_max_logz``.
        """
        def _tail(key: str) -> np.ndarray:
            arr = history.get(key)
            if arr is None:
                return np.array([], dtype=np.float64)
            out = np.asarray(arr, dtype=np.float64)
            return out[-self.history_days:] if out.size else out

        def _get(key: str) -> float:
            return float(today_raw.get(key, float("nan")))

        features: dict[str, float] = {}

        # ── VPIN (rank, V2.1 uses orthogonalized VPIN if available)
        vpin_key = "vpin_orthogonalized" if "vpin_orthogonalized" in today_raw else "vpin_filtered"
        features["vpin_rank"] = self.normalize_rank(_get(vpin_key), _tail(vpin_key))
        features["vpin_max_rank"] = self.normalize_rank(_get("vpin_max"), _tail("vpin_max"))

        # ── ΔVPIN (robust z)
        features["delta_vpin_z"] = self.normalize_robust_zscore(
            _get("delta_vpin"), _tail("delta_vpin"),
        )

        # ── OFI (robust z for all OFI features)
        features["ofi_daily_z"] = self.normalize_robust_zscore(_get("ofi_daily"), _tail("ofi_daily"))
        features["ofi_240_z"] = self.normalize_robust_zscore(_get("ofi_240"), _tail("ofi_240"))
        features["ofi_skew_z"] = self.normalize_robust_zscore(_get("ofi_skew"), _tail("ofi_skew"))
        features["ofi_95th_z"] = self.normalize_robust_zscore(_get("ofi_95th"), _tail("ofi_95th"))

        # ── OFI-Price Divergence: pass-through (already [-1,1])
        features["ofi_price_divergence"] = _get("ofi_price_divergence")

        # ── Kyle's Lambda (log + robust z for right-skewed)
        features["kyle_lambda_z"] = self.normalize_log_robust_zscore(
            _get("kyle_lambda"), _tail("kyle_lambda"),
        )

        # ── RV Bipower (log + robust z)
        features["rv_bipower_z"] = self.normalize_log_robust_zscore(
            _get("rv_bipower"), _tail("rv_bipower"),
        )

        # ── VWAP deviation (robust z, symmetric)
        features["vwap_dev_z"] = self.normalize_robust_zscore(
            _get("vwap_dev"), _tail("vwap_dev"),
        )

        # ── Volume Surprise (robust z)
        features["vs_z"] = self.normalize_robust_zscore(
            _get("volume_surprise"), _tail("volume_surprise"),
        )
        # Backward-compat alias
        features["vs_max_logz"] = features["vs_z"]

        return features
