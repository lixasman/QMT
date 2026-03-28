from __future__ import annotations

from datetime import datetime

from core.buy_order_config import set_aggressive_buy_pricing
from core.interfaces import InstrumentInfo, TickSnapshot

from entry.phase3_confirmer import Phase3Confirmer, Phase3Context
from entry.pathb_config import (
    set_pathb_atr_mult,
    set_pathb_chip_min,
    set_pathb_require_trend,
    set_pathb_require_vwap_strict,
)
from entry.vwap_tracker import VwapTracker


def _snap(*, ts: str, last: float, ask1: float, bid1: float, vol: int = 0, amt: float = 0.0, iopv: float | None = None) -> TickSnapshot:
    return TickSnapshot(
        timestamp=datetime.fromisoformat(ts),
        last_price=float(last),
        volume=int(vol),
        amount=float(amt),
        ask1_price=float(ask1),
        bid1_price=float(bid1),
        ask1_vol=100,
        bid1_vol=100,
        iopv=iopv,
    )


def test_phase3_gap_protection_scenario_8() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.0, 1.0, 1.0]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:52:03")
    snap = _snap(ts="2026-03-16T09:52:01", last=1.035, ask1=1.035, bid1=1.034, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "REJECT"
    assert act.reason == "GAP_TOO_LARGE"


def test_phase3_normal_breakout_scenario_9() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:52:03")
    snap = _snap(ts="2026-03-16T09:52:01", last=1.005, ask1=1.005, bid1=1.004, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "CONFIRM_ENTRY"
    assert act.order is not None


def test_phase3_iopv_premium_block_scenario_10() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:52:03")
    snap = _snap(ts="2026-03-16T09:52:01", last=1.010, ask1=1.010, bid1=1.009, iopv=1.003)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "REJECT"
    assert act.reason == "IOPV_PREMIUM_TOO_HIGH"


def test_phase3_iopv_missing_allows_scenario_11() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:52:03")
    snap = _snap(ts="2026-03-16T09:52:01", last=1.005, ask1=1.005, bid1=1.004, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "CONFIRM_ENTRY"


def test_phase3_vwap_warmup_scenario_12() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:40:00")
    snap = _snap(ts="2026-03-16T09:39:58", last=1.005, ask1=1.005, bid1=1.004, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "CONFIRM_ENTRY"
    assert act.used_vwap_slope is False


def test_phase3_time_cutoff_scenario_13() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T14:35:00")
    snap = _snap(ts="2026-03-16T14:34:58", last=1.005, ask1=1.005, bid1=1.004, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "REJECT"
    assert act.reason == "TIME_CUTOFF"


def test_phase3_buy_price_tick_and_clamp_scenario_16_17() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:52:03")
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)

    snap1 = _snap(ts="2026-03-16T09:52:01", last=1.005, ask1=1.095, bid1=1.094)
    act1 = c.decide(now=now, snapshot=snap1, instrument=inst, desired_qty=1000, is_trial=False)
    assert act1.action.value == "CONFIRM_ENTRY"
    assert act1.order is not None
    assert act1.order.price == 1.099

    snap2 = _snap(ts="2026-03-16T09:52:01", last=1.005, ask1=1.098, bid1=1.097)
    act2 = c.decide(now=now, snapshot=snap2, instrument=inst, desired_qty=1000, is_trial=False)
    assert act2.action.value == "CONFIRM_ENTRY"
    assert act2.order is not None
    assert act2.order.price == 1.1


def test_phase3_buy_price_can_use_ask1_directly() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:52:03")
    snap = _snap(ts="2026-03-16T09:52:01", last=1.005, ask1=1.005, bid1=1.004, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    set_aggressive_buy_pricing(multiplier=1.003, use_ask1=True)
    try:
        act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
        assert act.action.value == "CONFIRM_ENTRY"
        assert act.order is not None
        assert act.order.price == 1.005
    finally:
        set_aggressive_buy_pricing(multiplier=1.003, use_ask1=False)


def test_phase3_gap_epsilon_boundary_does_not_assert() -> None:
    ctx = Phase3Context(etf_code="159915", h_signal=1.00, l_signal=0.98, close_signal_day=1.00, atr_20=0.015, expire_yyyymmdd="20991231", strong=False)
    vwap = VwapTracker()
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T09:40:00")
    last = 1.0 + 0.01 + 0.5e-12
    snap = _snap(ts="2026-03-16T09:39:58", last=last, ask1=last, bid1=1.009999, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)

    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)

    assert act.action.value == "CONFIRM_ENTRY"
    assert act.order is not None


def test_phase3_pathb_allows_trend_confirm() -> None:
    set_pathb_atr_mult(0.5)
    set_pathb_chip_min(0.85)
    set_pathb_require_trend(True)
    set_pathb_require_vwap_strict(True)
    ctx = Phase3Context(
        etf_code="159915",
        h_signal=1.00,
        l_signal=0.98,
        close_signal_day=0.99,
        atr_20=0.04,
        expire_yyyymmdd="20991231",
        strong=False,
        s_trend=1.0,
        s_chip_pr=0.85,
    )
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T10:05:00")
    snap = _snap(ts="2026-03-16T10:04:58", last=0.99, ask1=0.99, bid1=0.989, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "CONFIRM_ENTRY"


def test_phase3_pathb_rejects_when_below_floor() -> None:
    set_pathb_atr_mult(0.5)
    set_pathb_chip_min(0.85)
    set_pathb_require_trend(True)
    set_pathb_require_vwap_strict(True)
    ctx = Phase3Context(
        etf_code="159915",
        h_signal=1.00,
        l_signal=0.98,
        close_signal_day=0.99,
        atr_20=0.04,
        expire_yyyymmdd="20991231",
        strong=False,
        s_trend=1.0,
        s_chip_pr=0.85,
    )
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T10:05:00")
    snap = _snap(ts="2026-03-16T10:04:58", last=0.97, ask1=0.97, bid1=0.969, iopv=None)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "REJECT"
    assert act.reason == "NO_BREAKOUT"


def test_phase3_pathb_iopv_reject_reports_iopv_reason() -> None:
    set_pathb_atr_mult(0.5)
    set_pathb_chip_min(0.85)
    set_pathb_require_trend(True)
    set_pathb_require_vwap_strict(True)
    ctx = Phase3Context(
        etf_code="159915",
        h_signal=1.00,
        l_signal=0.98,
        close_signal_day=0.99,
        atr_20=0.04,
        expire_yyyymmdd="20991231",
        strong=False,
        s_trend=1.0,
        s_chip_pr=0.85,
    )
    vwap = VwapTracker()
    vwap.anchor_vwaps = [1.001, 1.002, 1.003]
    c = Phase3Confirmer(ctx, vwap)
    now = datetime.fromisoformat("2026-03-16T10:05:00")
    snap = _snap(ts="2026-03-16T10:04:58", last=0.99, ask1=0.99, bid1=0.989, iopv=0.97)
    inst = InstrumentInfo(etf_code="159915", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    act = c.decide(now=now, snapshot=snap, instrument=inst, desired_qty=1000, is_trial=False)
    assert act.action.value == "REJECT"
    assert act.conditions["a_price_breakout"]["path_b"] is True
    assert act.reason == "IOPV_PREMIUM_TOO_HIGH"
