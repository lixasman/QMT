from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Protocol

from core.enums import FSMState
from core.interfaces import DataAdapter, InstrumentInfo, TickSnapshot, TradingAdapter

from .breaker import forbid_forward_buy_by_extreme, forbid_reverse_sell_by_extreme
from .kde_support import KdeZones, load_kde_zones
from .regime import compute_regime
from .signal_engine import SignalEngine
from .t0_logger import log_regime, log_signal
from .types import RegimeResult, T0Signal
from .vwap_engine import VwapEngine


class PositionPort(Protocol):
    def get_position_state(self, etf_code: str) -> FSMState: ...

    def get_t0_frozen(self, etf_code: str) -> bool: ...


@dataclass
class T0Engine:
    _data: DataAdapter
    _trading: TradingAdapter
    _position: Optional[PositionPort]
    _log_path: str
    _vwap: VwapEngine
    _signals: SignalEngine
    _regime: dict[str, RegimeResult]
    _kde: dict[str, KdeZones]

    def __init__(
        self,
        *,
        data: DataAdapter,
        trading: TradingAdapter,
        log_path: str,
        position_port: Optional[PositionPort] = None,
    ) -> None:
        self._data = data
        self._trading = trading
        self._position = position_port
        self._log_path = str(log_path)
        self._vwap = VwapEngine()
        self._signals = SignalEngine()
        self._regime = {}
        self._kde = {}

    def compute_daily_regime(
        self, *, etf_code: str, now: datetime, auction_vol_ratio: float, atr5_percentile: float, fsm_state: str = ""
    ) -> RegimeResult:
        r = compute_regime(auction_vol_ratio=float(auction_vol_ratio), atr5_percentile=float(atr5_percentile), computed_at=now)
        self._regime[str(etf_code)] = r
        log_regime(log_path=self._log_path, result=r, etf_code=str(etf_code), fsm_state=str(fsm_state))
        return r

    def load_daily_kde(self, *, etf_code: str, trade_date: date, base_dir: str = "data/kde_zones") -> KdeZones:
        kz = load_kde_zones(etf_code=str(etf_code), trade_date=trade_date, base_dir=str(base_dir))
        self._kde[str(etf_code)] = kz
        return kz

    def evaluate_tick(
        self,
        *,
        etf_code: str,
        now: datetime,
        t0_quota: float = 0.0,
        has_t0_long_position: bool = False,
        t0_long_qty: int = 0,
    ) -> Optional[T0Signal]:
        code = str(etf_code)
        snap = self._data.get_snapshot(code)
        inst = self._data.get_instrument_info(code)
        v = self._vwap.update(snapshot=snap)
        if v.data_quality.value != "OK":
            return None

        pos = self._position
        if pos is not None:
            st = pos.get_position_state(code)
            if st not in (FSMState.S2_BASE, FSMState.S3_SCALED, FSMState.S4_FULL):
                return None
            if bool(pos.get_t0_frozen(code)):
                return None

        r = self._regime.get(code)
        if r is None or bool(r.regime_active) is False:
            return None

        kz = self._kde.get(code)
        zones = kz.dense_zones if kz is not None else None
        s = self._signals.evaluate(
            etf_code=code,
            now=now,
            instrument=inst,
            snapshot=snap,
            vwap=float(v.vwap),
            sigma=float(v.sigma),
            regime_active=True,
            t0_quota=float(t0_quota),
            has_t0_long_position=bool(has_t0_long_position),
            t0_long_qty=int(t0_long_qty),
            kde_zones=zones,
        )
        if s is None:
            return None
        daily_change = 0.0
        if float(inst.prev_close) > 0:
            daily_change = (float(snap.last_price) - float(inst.prev_close)) / float(inst.prev_close)
        if str(s.signal_type) == "VWAP_BUY" and forbid_forward_buy_by_extreme(daily_change=float(daily_change)):
            return None
        if str(s.signal_type) == "VWAP_SELL" and (not bool(has_t0_long_position)) and forbid_reverse_sell_by_extreme(daily_change=float(daily_change)):
            return None
        log_signal(log_path=self._log_path, signal=s)
        return s
