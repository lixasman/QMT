from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from core.constants import TICK_SIZE
from core.interfaces import InstrumentInfo, TickSnapshot

from .constants import KDE_MERGE_TICK_TOLERANCE, T0_ORDER_AMOUNT_MAX, T0_ORDER_AMOUNT_MIN, T0_TRADABILITY_MIN_BPS
from .iopv_premium import compute_iopv_confidence
from .kde_support import find_nearest_support
from .types import T0Signal, TrendState, VwapBands
from .vwap_engine import normalize_passive_price
from .time_window import is_buy_allowed, is_reverse_sell_allowed, is_sell_allowed


def _kama(values: list[float], *, period: int = 10, fast: float = 2.0 / 3.0, slow: float = 2.0 / 31.0) -> list[float]:
    if len(values) == 0:
        return []
    out: list[float] = [float(values[0])]
    for i in range(1, len(values)):
        if i < period:
            out.append(float(values[i]))
            continue
        change = abs(float(values[i]) - float(values[i - period]))
        volatility = 0.0
        for j in range(i - period + 1, i + 1):
            volatility += abs(float(values[j]) - float(values[j - 1]))
        er = (change / volatility) if volatility > 0 else 0.0
        sc = (er * (fast - slow) + slow) ** 2
        prev = out[-1]
        out.append(prev + sc * (float(values[i]) - prev))
    return out


def compute_trend_state(*, price: float, vwap: float, prices_3s: list[float]) -> TrendState:
    px = float(price)
    vw = float(vwap)
    if len(prices_3s) < 61:
        return "RANGE"
    kama = _kama(list(prices_3s), period=10)
    if len(kama) < 61:
        return "RANGE"
    if px > vw and float(kama[-1]) > float(kama[-61]):
        return "UP"
    if px < vw and float(kama[-1]) < float(kama[-61]):
        return "DOWN"
    return "RANGE"


def compute_k_values(*, trend_state: TrendState) -> tuple[float, float]:
    st = str(trend_state)
    if st == "UP":
        return 1.8, 2.8
    if st == "DOWN":
        return 2.2, 2.6
    return 2.0, 2.8


def compute_vwap_bands(*, price: float, vwap: float, sigma: float, prices_3s: list[float]) -> VwapBands:
    st = compute_trend_state(price=float(price), vwap=float(vwap), prices_3s=list(prices_3s))
    k_buy, k_sell = compute_k_values(trend_state=st)
    buy_trigger = float(vwap) - float(k_buy) * float(sigma)
    sell_trigger = float(vwap) + float(k_sell) * float(sigma)
    return VwapBands(
        vwap=float(vwap),
        sigma=float(sigma),
        k_buy=float(k_buy),
        k_sell=float(k_sell),
        buy_trigger=float(buy_trigger),
        sell_trigger=float(sell_trigger),
        trend_state=st,
    )


@dataclass
class SignalEngine:
    _prices_3s: list[float]

    def __init__(self) -> None:
        self._prices_3s = []

    def update_price(self, *, price: float) -> None:
        self._prices_3s.append(float(price))
        if len(self._prices_3s) > 240:
            self._prices_3s = self._prices_3s[-240:]

    def evaluate(
        self,
        *,
        etf_code: str,
        now: datetime,
        instrument: InstrumentInfo,
        snapshot: TickSnapshot,
        vwap: float,
        sigma: float,
        regime_active: bool,
        t0_quota: float = 0.0,
        has_t0_long_position: bool = False,
        t0_long_qty: int = 0,
        kde_zones: Optional[list] = None,
    ) -> Optional[T0Signal]:
        if bool(regime_active) is False:
            return None
        if now.time() < time(10, 0):
            return None

        px = float(snapshot.last_price)
        self.update_price(price=px)

        bands = compute_vwap_bands(price=px, vwap=float(vwap), sigma=float(sigma), prices_3s=list(self._prices_3s))

        if float(bands.sigma) <= 0:
            return None

        kde_support = False
        kde_price = None
        if kde_zones is not None:
            kde_price = find_nearest_support(zones=list(kde_zones), price=float(px))

        if px <= float(bands.buy_trigger) and is_buy_allowed(now=now):
            quota = float(t0_quota)
            if quota < float(T0_ORDER_AMOUNT_MIN):
                return None
            amount = min(float(quota), float(T0_ORDER_AMOUNT_MAX))
            if amount < float(T0_ORDER_AMOUNT_MIN):
                return None
            tradability_bps = (float(bands.k_buy) * float(bands.sigma) / float(px)) * 10000.0
            if tradability_bps < float(T0_TRADABILITY_MIN_BPS):
                return None
            target_raw = float(bands.buy_trigger)
            target = normalize_passive_price(price=target_raw, lower_limit=float(instrument.limit_down), upper_limit=float(instrument.limit_up))
            if kde_price is not None:
                tol = float(KDE_MERGE_TICK_TOLERANCE) * float(TICK_SIZE)
                kde_support = abs(float(target) - float(kde_price)) <= tol
            conf = compute_iopv_confidence(price=float(px), iopv=snapshot.iopv)
            if conf == "HIGH":
                conf = "NORMAL"
            if kde_support and conf == "NORMAL":
                conf = "HIGH"
            return T0Signal(
                etf_code=str(etf_code),
                timestamp=now,
                signal_type="VWAP_BUY",
                vwap=float(bands.vwap),
                sigma=float(bands.sigma),
                k_value=float(bands.k_buy),
                trend_state=bands.trend_state,
                target_price=float(target),
                amount=float(amount),
                confidence=conf,
                kde_support=bool(kde_support),
                kde_zone_price=float(kde_price) if kde_price is not None else None,
                action="PLACE_LIMIT_BUY",
            )

        if bool(has_t0_long_position) and px >= float(bands.sell_trigger) and is_sell_allowed(now=now):
            tradability_bps = (float(bands.k_sell) * float(bands.sigma) / float(px)) * 10000.0
            if tradability_bps < float(T0_TRADABILITY_MIN_BPS):
                return None
            target_raw = float(bands.sell_trigger)
            target = normalize_passive_price(price=target_raw, lower_limit=float(instrument.limit_down), upper_limit=float(instrument.limit_up))
            conf = compute_iopv_confidence(price=float(px), iopv=snapshot.iopv)
            sell_qty = int(max(0, int(t0_long_qty)))
            amount = float(sell_qty) * float(target)
            return T0Signal(
                etf_code=str(etf_code),
                timestamp=now,
                signal_type="VWAP_SELL",
                vwap=float(bands.vwap),
                sigma=float(bands.sigma),
                k_value=float(bands.k_sell),
                trend_state=bands.trend_state,
                target_price=float(target),
                amount=float(amount),
                quantity=int(sell_qty),
                confidence=conf,
                kde_support=False,
                kde_zone_price=None,
                action="PLACE_LIMIT_SELL",
            )

        if (not bool(has_t0_long_position)) and px >= float(bands.sell_trigger) and is_reverse_sell_allowed(now=now):
            quota = float(t0_quota)
            if quota < float(T0_ORDER_AMOUNT_MIN):
                return None
            amount = min(float(quota), float(T0_ORDER_AMOUNT_MAX))
            if amount < float(T0_ORDER_AMOUNT_MIN):
                return None
            tradability_bps = (float(bands.k_sell) * float(bands.sigma) / float(px)) * 10000.0
            if tradability_bps < float(T0_TRADABILITY_MIN_BPS):
                return None
            target_raw = float(bands.sell_trigger)
            target = normalize_passive_price(price=target_raw, lower_limit=float(instrument.limit_down), upper_limit=float(instrument.limit_up))
            conf = compute_iopv_confidence(price=float(px), iopv=snapshot.iopv)
            return T0Signal(
                etf_code=str(etf_code),
                timestamp=now,
                signal_type="VWAP_SELL",
                vwap=float(bands.vwap),
                sigma=float(bands.sigma),
                k_value=float(bands.k_sell),
                trend_state=bands.trend_state,
                target_price=float(target),
                amount=float(amount),
                confidence=conf,
                kde_support=False,
                kde_zone_price=None,
                action="PLACE_LIMIT_SELL",
            )

        return None
