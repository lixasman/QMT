from __future__ import annotations

import numpy as np


class MaxEntSolver:
    def __init__(self, max_iter: int = 20, tol: float = 1e-8):
        self.max_iter = int(max_iter)
        self.tol = float(tol)

    def solve(
        self,
        price_grid: np.ndarray,
        vwap: float,
        *,
        premium_rate: float = 0.0,
        delta_max: float = 2.0,
        premium_sensitivity: float = 0.0005,
        # ---- 向后兼容旧参数名（忽略）----
        k_gamma: float = 500.0,
        gamma_max: float = 2.0,
    ) -> np.ndarray:
        """三约束 MaxEnt：归一化 + VWAP + 溢折价偏度（奇函数 cubic 映射）

        偏度项使用围绕中点的奇函数 δ·(p'-0.5)^3，真正表达左/右偏度。
        premium → δ 使用 tanh 映射，在 ±premium_sensitivity 内自然衰减为零，
        避免小噪声导致 δ 符号频繁翻转。

        溢价(premium>0) → δ>0 → 右偏(高价端权重增大)
        折价(premium<0) → δ<0 → 左偏(低价端权重增大)
        """
        grid = np.asarray(price_grid, dtype=np.float64)
        n = grid.size
        if n <= 0:
            return np.zeros(0, dtype=np.float64)
        if n == 1:
            return np.ones(1, dtype=np.float64)

        p_min = float(grid.min())
        p_max = float(grid.max())
        p_range = p_max - p_min
        if p_range < 1e-12:
            return np.ones(n, dtype=np.float64) / n

        p_norm = (grid - p_min) / p_range
        vwap_norm = (float(vwap) - p_min) / p_range
        vwap_norm = float(np.clip(vwap_norm, 0.0, 1.0))

        # v2 审计 P1a: VWAP=High/Low 极端边界直接返回 one-hot
        if vwap_norm > 1.0 - 1e-6:
            out = np.zeros(n, dtype=np.float64)
            out[-1] = 1.0
            return out
        if vwap_norm < 1e-6:
            out = np.zeros(n, dtype=np.float64)
            out[0] = 1.0
            return out

        # δ 偏度映射：tanh 自然提供死区 + 饱和
        pr = float(premium_rate)
        sens = float(premium_sensitivity)
        if sens <= 0:
            sens = 0.0005
        delta = float(delta_max) * float(np.tanh(pr / sens))

        # 奇函数偏度基底：围绕 p'=0.5 的三次函数
        p_centered = p_norm - 0.5

        beta = 0.0
        for _ in range(self.max_iter):
            log_v = -beta * p_norm + delta * (p_centered ** 3)
            log_v -= float(log_v.max())
            v = np.exp(log_v)
            denom = float(v.sum())
            if denom <= 0:
                v = np.ones(n, dtype=np.float64) / n
            else:
                v /= denom

            mean_p = float(np.dot(v, p_norm))
            residual = mean_p - vwap_norm
            if abs(residual) < self.tol:
                break

            var_p = float(np.dot(v, p_norm**2) - mean_p**2)
            if var_p < 1e-12:
                break
            beta += residual / var_p

        log_v = -beta * p_norm + delta * (p_centered ** 3)
        log_v -= float(log_v.max())
        v = np.exp(log_v)
        s = float(v.sum())
        if s > 0:
            v /= s
        else:
            v = np.ones(n, dtype=np.float64) / n
        return v

