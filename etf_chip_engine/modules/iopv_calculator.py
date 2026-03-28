from __future__ import annotations

from typing import Dict

import numpy as np

from core.warn_utils import info_once, warn_once


class IOPVCalculator:
    def __init__(self, etf_info: dict, *, etf_code: str = ""):
        self.etf_code = str(etf_code or "").strip().upper()
        self.stocks: dict = etf_info.get("stocks") or {}
        self.cash_balance: float = float(etf_info.get("cashBalance") or 0.0)
        self.report_unit: float = float(etf_info.get("reportUnit") or 0.0)
        self.nav_per_cu: float = float(etf_info.get("navPerCU") or 0.0)
        self.nav: float = float(etf_info.get("nav") or 0.0)
        self.stock_prices: Dict[str, float] = {}

    def update_stock_price(self, stock_code: str, price: float) -> None:
        px = float(price)
        s = str(stock_code or "").strip().upper()
        if not s:
            return
        self.stock_prices[s] = px
        if "." in s:
            self.stock_prices[s.split(".", 1)[0]] = px
            return
        if len(s) == 6 and s.isdigit():
            if s.startswith(("5", "6", "9")):
                self.stock_prices[f"{s}.SH"] = px
            else:
                self.stock_prices[f"{s}.SZ"] = px

    def calculate_iopv(self) -> float:
        if self.report_unit <= 0:
            warn_once(f"iopv_report_unit_invalid:{self.etf_code}", f"IOPV: report_unit 无效，已降级为 NaN: etf={self.etf_code} report_unit={self.report_unit}", logger_name=__name__)
            return float("nan")

        basket_nav = 0.0
        total_components = 0
        covered = 0
        non_dict = 0
        missing_vol = 0
        bad_vol = 0

        for stock_code, stock_info in self.stocks.items():
            if not isinstance(stock_code, str) or not isinstance(stock_info, dict):
                non_dict += 1
                continue
            vol = stock_info.get("componentVolume")
            if vol is None:
                missing_vol += 1
                continue
            try:
                vol_f = float(vol)
            except Exception:
                bad_vol += 1
                continue
            if not np.isfinite(vol_f) or vol_f <= 0:
                continue

            total_components += 1
            if stock_code in self.stock_prices:
                basket_nav += self.stock_prices[stock_code] * vol_f
                covered += 1

        if total_components <= 0:
            if self.stocks:
                warn_once(
                    f"iopv_components_empty:{self.etf_code}",
                    f"IOPV: 可用成分股为 0，返回 NaN（不使用 nav 兜底）: etf={self.etf_code} stocks={len(self.stocks)} non_dict={non_dict} missing_vol={missing_vol} bad_vol={bad_vol}",
                    logger_name=__name__,
                )
            return float("nan")
        if covered < total_components * 0.5:
            info_once(
                f"iopv_low_coverage:{self.etf_code}",
                f"IOPV: 成分股覆盖率过低（{covered}/{total_components}），返回 NaN（不使用 nav 兜底）: etf={self.etf_code} non_dict={non_dict} missing_vol={missing_vol} bad_vol={bad_vol}",
                logger_name=__name__,
            )
            return float("nan")

        if covered > 0 and covered < total_components:
            basket_nav *= float(total_components) / float(covered)
            info_once(
                f"iopv_partial_coverage:{self.etf_code}",
                f"IOPV: 成分股覆盖不全，已启用覆盖率缩放: etf={self.etf_code} covered={covered}/{total_components} non_dict={non_dict} missing_vol={missing_vol} bad_vol={bad_vol}",
                logger_name=__name__,
            )

        basket_nav += self.cash_balance
        return basket_nav / self.report_unit

    def get_premium_rate(self, etf_price: float) -> float:
        iopv = self.calculate_iopv()
        if np.isnan(iopv) or iopv <= 0:
            return 0.0
        return (float(etf_price) - iopv) / iopv

    def get_coverage(self) -> float:
        """返回已覆盖成分股占比 [0, 1]，用于衰减 premium 可信度"""
        total = 0
        covered = 0
        for stock_code, stock_info in self.stocks.items():
            if not isinstance(stock_code, str) or not isinstance(stock_info, dict):
                continue
            vol = stock_info.get("componentVolume")
            if vol is None:
                continue
            try:
                vol_f = float(vol)
            except Exception:
                continue
            if not np.isfinite(vol_f) or vol_f <= 0:
                continue
            total += 1
            if stock_code in self.stock_prices:
                covered += 1
        return covered / max(total, 1)
