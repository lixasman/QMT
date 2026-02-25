"""M7 — Factor Orthogonalizer.

Remove structural co-linearity between VPIN and its mechanical drivers
(Volume Surprise and Realized Volatility) via OLS regression.

    VPIN = β₀ + β₁ × VS + β₂ × RV + ε

The residual ε is the orthogonalized VPIN — the "pure" informed-trading
toxicity component that is not explained by overall volume or volatility.
"""

from __future__ import annotations

import numpy as np


class FactorOrthogonalizer:
    """Orthogonalize VPIN against VS and RV using rolling OLS."""

    def __init__(self, min_history: int = 20):
        self.min_history = int(min_history)

    def orthogonalize_vpin(
        self,
        vpin_history: np.ndarray,
        vs_history: np.ndarray,
        rv_history: np.ndarray,
        vpin_today: float,
        vs_today: float,
        rv_today: float,
    ) -> float:
        """Orthogonalize today's VPIN against VS and RV.

        Cold-start rule (V2.1): when any history array has fewer than
        ``min_history`` *finite* values, return ``vpin_today`` unchanged.

        Parameters
        ----------
        vpin_history, vs_history, rv_history : np.ndarray
            Rolling history arrays (may contain NaN).
        vpin_today, vs_today, rv_today : float
            Today's raw factor values.

        Returns
        -------
        float
            Orthogonalized VPIN residual, or raw ``vpin_today`` on cold-start.
        """
        if not np.isfinite(vpin_today):
            return float("nan")

        # Build aligned finite mask across all three
        vpin_h = np.asarray(vpin_history, dtype=np.float64)
        vs_h = np.asarray(vs_history, dtype=np.float64)
        rv_h = np.asarray(rv_history, dtype=np.float64)

        min_len = min(len(vpin_h), len(vs_h), len(rv_h))
        if min_len < self.min_history:
            return float(vpin_today)

        # Align to same length (use tail)
        vpin_h = vpin_h[-min_len:]
        vs_h = vs_h[-min_len:]
        rv_h = rv_h[-min_len:]

        finite_mask = np.isfinite(vpin_h) & np.isfinite(vs_h) & np.isfinite(rv_h)
        n_finite = int(finite_mask.sum())
        if n_finite < self.min_history:
            return float(vpin_today)

        y = vpin_h[finite_mask]
        X = np.column_stack([
            np.ones(n_finite, dtype=np.float64),
            vs_h[finite_mask],
            rv_h[finite_mask],
        ])

        # OLS: β = (X'X)^{-1} X'y
        try:
            XtX = X.T @ X
            Xty = X.T @ y
            beta = np.linalg.solve(XtX, Xty)
        except np.linalg.LinAlgError:
            return float(vpin_today)

        # Predict today's VPIN from VS and RV, take residual
        x_today = np.array([1.0, float(vs_today), float(rv_today)], dtype=np.float64)
        if not (np.isfinite(vs_today) and np.isfinite(rv_today)):
            return float(vpin_today)

        predicted = float(x_today @ beta)
        residual = float(vpin_today) - predicted
        return float(residual)

    def check_redundancy(
        self,
        factor_matrix: dict[str, np.ndarray],
        threshold: float = 0.85,
    ) -> list[tuple[str, str, float]]:
        """Identify factor pairs with |ρ_Spearman| > threshold.

        Uses Spearman rank correlation (V2.1 spec requirement) for
        robustness to non-linear relationships and outliers.
        """
        names = list(factor_matrix.keys())
        result: list[tuple[str, str, float]] = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a = np.asarray(factor_matrix[names[i]], dtype=np.float64)
                b = np.asarray(factor_matrix[names[j]], dtype=np.float64)
                min_len = min(len(a), len(b))
                if min_len < 5:
                    continue
                a = a[-min_len:]
                b = b[-min_len:]
                mask = np.isfinite(a) & np.isfinite(b)
                if mask.sum() < 5:
                    continue
                # Spearman: rank then Pearson on ranks
                try:
                    from scipy.stats import spearmanr
                    rho, _ = spearmanr(a[mask], b[mask])
                    rho = float(rho)
                except ImportError:
                    # Fallback: manual rank-based correlation
                    from scipy.stats import rankdata
                    ra = rankdata(a[mask])
                    rb = rankdata(b[mask])
                    rho = float(np.corrcoef(ra, rb)[0, 1])
                if np.isfinite(rho) and abs(rho) > threshold:
                    result.append((names[i], names[j], rho))
        return result
