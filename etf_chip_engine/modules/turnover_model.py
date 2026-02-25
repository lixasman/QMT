from __future__ import annotations

import numpy as np


class TurnoverModel:
    def __init__(self, *, alpha: float = 0.5, kappa1: float = 1.5, kappa2: float = 1.5, kappa3: float = 0.5):
        self.alpha = float(alpha)
        self.kappa1 = float(kappa1)
        self.kappa2 = float(kappa2)
        self.kappa3 = float(kappa3)

    def prospect_factor(self, returns: np.ndarray) -> np.ndarray:
        r = np.asarray(returns, dtype=np.float64)
        f = np.ones_like(r)

        mask_profit = r > 0
        if mask_profit.any():
            f[mask_profit] = 1.0 + self.kappa1 * np.log1p(r[mask_profit])

        mask_shallow = (r >= -0.2) & (r <= 0)
        if mask_shallow.any():
            f[mask_shallow] = np.maximum(0.1, 1.0 - self.kappa2 * np.sqrt(np.abs(r[mask_shallow])))

        mask_deep = r < -0.2
        if mask_deep.any():
            f[mask_deep] = 0.1 + self.kappa3 * (np.abs(r[mask_deep]) - 0.2)

        return f

    def calculate_turnover(self, price_grid: np.ndarray, current_price: float, base_turnover: float) -> np.ndarray:
        grid = np.asarray(price_grid, dtype=np.float64)
        r = (float(current_price) - grid) / np.maximum(grid, 1e-6)
        f = self.alpha * self.prospect_factor(r) + (1.0 - self.alpha)
        return float(base_turnover) * f

