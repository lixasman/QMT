from __future__ import annotations

from datetime import datetime

from core.enums import DataQuality
from core.interfaces import InstrumentInfo, TickSnapshot
from core.time_utils import set_trading_calendar_provider

from exit.layer1 import check_deadwater, check_gap_protection, decide_full_exit, decide_layer1_on_trigger, should_freeze_t0
from exit.lifeboat import evaluate_buyback, plan_lifeboat_buyback
from exit.scoring import compute_score_soft


def _inst() -> InstrumentInfo:
    return InstrumentInfo(
        etf_code="159915",
        instrument_name="TEST",
        prev_close=1.000,
        limit_up=1.100,
        limit_down=0.900,
        price_tick=0.001,
    )


def _snap(*, ts: datetime, last: float, bid1: float, ask1: float, quality: DataQuality = DataQuality.OK) -> TickSnapshot:
    return TickSnapshot(
        timestamp=ts,
        last_price=float(last),
        volume=0,
        amount=0.0,
        ask1_price=float(ask1),
        bid1_price=float(bid1),
        ask1_vol=10000,
        bid1_vol=10000,
        iopv=None,
        stock_status=0,
        data_quality=quality,
    )


def test_layer2_scoring_scenarios_1_7() -> None:
    r = compute_score_soft({"S_chip": 0.0, "S_sentiment": 0.0, "S_diverge": 0.0, "S_time": 1.0})
    assert r.score_soft == 0.40
    assert r.triggered is False

    r = compute_score_soft({"S_chip": 0.5, "S_sentiment": 0.0, "S_diverge": 0.0, "S_time": 1.0})
    assert r.score_soft == 0.75
    assert r.triggered is False

    r = compute_score_soft({"S_chip": 0.5, "S_sentiment": 1.0, "S_diverge": 0.0, "S_time": 0.0})
    assert r.score_soft == 1.05
    assert r.triggered is True

    r = compute_score_soft({"S_chip": 0.0, "S_sentiment": 1.0, "S_diverge": 1.0, "S_time": 0.0})
    assert r.score_soft == 1.20
    assert r.triggered is True

    r = compute_score_soft({"S_chip": 0.5, "S_sentiment": 0.0, "S_diverge": 1.0, "S_time": 1.0})
    assert r.score_soft == 1.25
    assert r.triggered is True

    r = compute_score_soft({"S_chip": 1.0, "S_sentiment": 1.0, "S_diverge": 1.0, "S_time": 1.0})
    assert r.score_soft == 2.30
    assert r.triggered is True

    r = compute_score_soft({"S_chip": 0.0, "S_sentiment": 0.0, "S_diverge": 0.0, "S_time": 0.0})
    assert r.score_soft == 0.00


def test_layer1_triggered_scenarios_8_11() -> None:
    inst = _inst()
    now = datetime(2026, 2, 23, 10, 0, 0)
    snap = _snap(ts=now, last=0.895, bid1=0.894, ask1=0.896)
    stop = 0.900
    data_ok = {"S_chip": DataQuality.OK, "S_sentiment": DataQuality.OK, "S_diverge": DataQuality.OK, "S_time": DataQuality.OK}

    d = decide_layer1_on_trigger(
        etf_code=inst.etf_code,
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.7,
        data_health=data_ok,
        lifeboat_used=False,
        total_qty=1050,
        sellable_qty=1050,
        now=now,
    )
    assert d.action == "FULL_EXIT"
    assert d.order is not None
    assert d.order.quantity == 1050

    d = decide_layer1_on_trigger(
        etf_code=inst.etf_code,
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=False,
        total_qty=1500,
        sellable_qty=1500,
        now=now,
    )
    assert d.action == "LIFEBOAT_70_30"
    assert d.order is not None
    assert d.order.quantity == 1050
    assert int(d.extra.get("retain_qty") or 0) == 450

    d = decide_layer1_on_trigger(
        etf_code=inst.etf_code,
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=True,
        total_qty=10050,
        sellable_qty=3050,
        now=now,
    )
    assert d.action == "FULL_EXIT"
    assert d.order is not None
    assert d.order.quantity == 3050
    assert int(d.extra.get("locked_qty") or 0) == 7000

    data_bad = dict(data_ok)
    data_bad["S_chip"] = DataQuality.UNAVAILABLE
    d = decide_layer1_on_trigger(
        etf_code=inst.etf_code,
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_bad,
        lifeboat_used=False,
        total_qty=1050,
        sellable_qty=1050,
        now=now,
    )
    assert d.action == "FULL_EXIT"
    assert d.order is not None
    assert d.order.quantity == 1050


def test_lifeboat_buyback_scenarios_12_17() -> None:
    set_trading_calendar_provider(lambda s, e: [s] if s == e else [s, e])
    inst = _inst()
    stop = 0.900
    data_ok = {"S_chip": DataQuality.OK, "S_sentiment": DataQuality.OK, "S_diverge": DataQuality.OK, "S_time": DataQuality.OK}

    sell_time = datetime(2026, 2, 23, 10, 0, 0)
    now = datetime(2026, 2, 23, 10, 20, 0)
    snap = _snap(ts=now, last=0.925, bid1=0.924, ask1=0.926)
    ev = evaluate_buyback(
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=False,
        lifeboat_sell_time=sell_time,
        current_total_qty=3000,
        now=now,
    )
    assert ev.passed is False
    assert ev.conditions["a_cooldown"]["pass"] is False

    now = datetime(2026, 2, 23, 10, 30, 0)
    snap = _snap(ts=now, last=0.925, bid1=0.924, ask1=0.926)
    ev = evaluate_buyback(
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=False,
        lifeboat_sell_time=sell_time,
        current_total_qty=3000,
        now=now,
    )
    assert ev.passed is True
    plan = plan_lifeboat_buyback(instrument=inst, snapshot=snap, current_total_qty=3000, trading_minutes_elapsed=30, now=now)
    assert plan.buy_qty == 7000

    sell_time = datetime(2026, 2, 23, 11, 20, 0)
    now = datetime(2026, 2, 23, 13, 15, 0)
    snap = _snap(ts=now, last=0.925, bid1=0.924, ask1=0.926)
    ev = evaluate_buyback(
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=False,
        lifeboat_sell_time=sell_time,
        current_total_qty=3000,
        now=now,
    )
    assert ev.passed is False
    assert ev.conditions["a_cooldown"]["minutes"] == 25

    now = datetime(2026, 2, 23, 13, 20, 0)
    snap = _snap(ts=now, last=0.925, bid1=0.924, ask1=0.926)
    ev = evaluate_buyback(
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=False,
        lifeboat_sell_time=sell_time,
        current_total_qty=3000,
        now=now,
    )
    assert ev.passed is True

    sell_time = datetime(2026, 2, 23, 14, 0, 0)
    now = datetime(2026, 2, 23, 14, 35, 0)
    snap = _snap(ts=now, last=0.925, bid1=0.924, ask1=0.926)
    ev = evaluate_buyback(
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=False,
        lifeboat_sell_time=sell_time,
        current_total_qty=3000,
        now=now,
    )
    assert ev.passed is False
    assert ev.conditions["f_before_cutoff"]["pass"] is False

    sell_time = datetime(2026, 2, 23, 10, 0, 0)
    now = datetime(2026, 2, 23, 10, 30, 0)
    snap = _snap(ts=now, last=inst.limit_down * 1.01, bid1=0.900, ask1=0.901)
    ev = evaluate_buyback(
        instrument=inst,
        snapshot=snap,
        stop_price=stop,
        score_soft=0.0,
        data_health=data_ok,
        lifeboat_used=False,
        lifeboat_sell_time=sell_time,
        current_total_qty=3000,
        now=now,
    )
    assert ev.passed is False
    assert ev.conditions["e_not_dead_cat"]["pass"] is False


def test_gap_deadwater_t0_scenarios_18_22() -> None:
    inst = _inst()
    stop = 1.000

    now = datetime(2026, 2, 23, 9, 25, 0)
    snap = _snap(ts=now, last=stop * 0.96, bid1=0.959, ask1=0.961)
    trig = check_gap_protection(now_time=now.time(), last_price=snap.last_price, stop_price=stop)
    assert trig.triggered is True
    d = decide_full_exit(
        etf_code=inst.etf_code,
        instrument=inst,
        snapshot=snap,
        reason="GAP_PROTECTION",
        sellable_qty=10000,
        total_qty=10000,
        locked_qty=0,
    )
    assert d.action == "FULL_EXIT"

    now = datetime(2026, 2, 23, 13, 0, 0)
    snap = _snap(ts=now, last=stop * 0.95, bid1=0.949, ask1=0.951)
    trig = check_gap_protection(now_time=now.time(), last_price=snap.last_price, stop_price=stop)
    assert trig.triggered is True

    trig = check_deadwater(days_held=12, current_return=0.008)
    assert trig.triggered is True
    trig = check_deadwater(days_held=8, current_return=0.008)
    assert trig.triggered is False

    assert should_freeze_t0(t0_realized_loss_pct=0.003) is True

    now = datetime(2026, 2, 23, 10, 0, 0)
    snap = _snap(ts=now, last=0.895, bid1=0.894, ask1=0.896)
    data_ok = {"S_chip": DataQuality.OK, "S_sentiment": DataQuality.OK, "S_diverge": DataQuality.OK, "S_time": DataQuality.OK}
    d = decide_layer1_on_trigger(
        etf_code=inst.etf_code,
        instrument=inst,
        snapshot=snap,
        stop_price=0.900,
        score_soft=0.7,
        data_health=data_ok,
        lifeboat_used=False,
        total_qty=10000,
        sellable_qty=10000,
        now=now,
    )
    assert d.action == "FULL_EXIT"
