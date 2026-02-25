from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from core.constants import ENTRY_CUTOFF_TIME
from core.enums import ActionType, DataQuality, OrderSide, OrderType
from core.interfaces import InstrumentInfo, OrderRequest, TickSnapshot
from core.price_utils import align_order_price
from core.validators import assert_action_allowed

from .constants import GAP_ATR_FACTOR, GAP_THRESHOLD_MIN, IOPV_PREMIUM_CONFIRM, IOPV_PREMIUM_TRIAL
from .types import ConfirmAction, ConfirmActionType
from .vwap_tracker import VwapTracker


@dataclass(frozen=True)
class Phase3Context:
    etf_code: str
    h_signal: float
    l_signal: float
    close_signal_day: float
    atr_20: float
    expire_yyyymmdd: str
    strong: bool


class Phase3Confirmer:
    def __init__(self, ctx: Phase3Context, vwap: VwapTracker) -> None:
        self._ctx = ctx
        self._vwap = vwap

    def decide(
        self,
        *,
        now: datetime,
        snapshot: TickSnapshot,
        instrument: InstrumentInfo,
        desired_qty: int,
        is_trial: bool = False,
    ) -> ConfirmAction:
        if int(desired_qty) <= 0:
            return ConfirmAction(action=ConfirmActionType.NOOP, reason="NO_QTY")

        if now.strftime("%Y%m%d") > self._ctx.expire_yyyymmdd:
            return ConfirmAction(action=ConfirmActionType.INVALIDATE, reason="WINDOW_EXPIRED")

        if now.time() > ENTRY_CUTOFF_TIME:
            act = ConfirmAction(action=ConfirmActionType.REJECT, reason="TIME_CUTOFF", conditions={"d_time_cutoff": {"pass": False}})
            assert act.action != ConfirmActionType.CONFIRM_ENTRY
            return act

        staleness_sec = float((now - snapshot.timestamp).total_seconds())
        if snapshot.data_quality == DataQuality.STALE:
            return ConfirmAction(
                action=ConfirmActionType.REJECT,
                reason="STALE",
                conditions={"e_data_fresh": {"pass": False, "staleness_sec": staleness_sec}},
            )

        assert_action_allowed(snapshot.data_quality, ActionType.ENTRY_CONFIRM)

        last_price = float(snapshot.last_price)
        h_signal = float(self._ctx.h_signal)
        atr = float(self._ctx.atr_20)
        close_t = float(self._ctx.close_signal_day) if float(self._ctx.close_signal_day) > 0 else last_price

        gap_ratio = (last_price - h_signal) / h_signal if h_signal > 0 else 0.0
        gap_threshold = max(float(GAP_THRESHOLD_MIN), float(GAP_ATR_FACTOR) * atr / close_t) if close_t > 0 else float(GAP_THRESHOLD_MIN)

        a_breakout_pass = bool(last_price > h_signal)
        a_gap_pass = bool(gap_ratio <= (gap_threshold + 1e-12))

        warmup_active = self._vwap.is_warmup(now)
        if warmup_active:
            b_pass = True
            used_vwap_slope = False
            slope_vals = []
        else:
            b_pass = bool(self._vwap.slope_positive())
            used_vwap_slope = True
            slope_vals = list(self._vwap.anchor_vwaps[-3:]) if len(self._vwap.anchor_vwaps) >= 3 else list(self._vwap.anchor_vwaps)

        premium_threshold = float(IOPV_PREMIUM_TRIAL if is_trial else IOPV_PREMIUM_CONFIRM)
        if snapshot.iopv is None:
            c_pass = True
            premium = None
        else:
            iopv = float(snapshot.iopv)
            premium = (last_price - iopv) / iopv if iopv > 0 else 0.0
            c_pass = bool(premium <= premium_threshold)

        all_pass = bool(a_breakout_pass and a_gap_pass and b_pass and c_pass)

        conditions = {
            "a_price_breakout": {"pass": a_breakout_pass, "last_price": last_price, "H_signal": h_signal},
            "a_gap_check": {"pass": a_gap_pass, "gap_ratio": gap_ratio, "threshold": gap_threshold},
            "b_vwap_slope": {"pass": b_pass, "warmup_active": warmup_active, "slope_values": slope_vals},
            "c_iopv_premium": {"pass": c_pass, "premium": premium, "threshold": premium_threshold},
            "d_time_cutoff": {"pass": True, "current_time": now.strftime("%H:%M")},
            "e_data_fresh": {"pass": True, "staleness_sec": staleness_sec},
        }

        if not all_pass:
            if not a_breakout_pass:
                reason = "NO_BREAKOUT"
            elif not a_gap_pass:
                reason = "GAP_TOO_LARGE"
            elif not b_pass:
                reason = "VWAP_SLOPE_NOT_POSITIVE"
            else:
                reason = "IOPV_PREMIUM_TOO_HIGH"
            act = ConfirmAction(action=ConfirmActionType.REJECT, reason=reason, conditions=conditions, used_vwap_slope=used_vwap_slope)
            if gap_ratio > gap_threshold:
                assert act.action != ConfirmActionType.CONFIRM_ENTRY
            if now.time() < time(9, 50):
                assert not used_vwap_slope
            return act

        raw_price = float(snapshot.ask1_price) * 1.003
        buy_price = align_order_price(price=raw_price, side="BUY", lower_limit=float(instrument.limit_down), upper_limit=float(instrument.limit_up), tick_size=float(instrument.price_tick))
        order = OrderRequest(
            etf_code=self._ctx.etf_code,
            side=OrderSide.BUY,
            quantity=int(desired_qty),
            order_type=OrderType.LIMIT,
            price=float(buy_price),
            strategy_name="ENTRY",
            remark=("TRIAL" if is_trial else "CONFIRM"),
        )

        act2 = ConfirmAction(action=ConfirmActionType.CONFIRM_ENTRY, reason="", conditions=conditions, order=order, used_vwap_slope=used_vwap_slope)
        if gap_ratio > gap_threshold:
            assert act2.action != ConfirmActionType.CONFIRM_ENTRY
        if now.time() < time(9, 50):
            assert not used_vwap_slope
        if now.time() > ENTRY_CUTOFF_TIME:
            assert act2.action != ConfirmActionType.CONFIRM_ENTRY
        return act2
