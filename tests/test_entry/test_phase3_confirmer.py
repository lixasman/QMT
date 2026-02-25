from __future__ import annotations

from datetime import datetime

from core.interfaces import InstrumentInfo, TickSnapshot

from entry.phase3_confirmer import Phase3Confirmer, Phase3Context
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

