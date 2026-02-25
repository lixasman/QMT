from __future__ import annotations

from datetime import datetime

from core.enums import DataQuality
from core.interfaces import InstrumentInfo, TickSnapshot

from t0.signal_engine import SignalEngine, compute_vwap_bands
from t0.time_window import is_buy_allowed, is_close_only, is_reverse_sell_allowed, is_sell_allowed
from t0.vwap_engine import VwapEngine, apply_sigma_floor, normalize_passive_price


def test_vwap_acceptance_scenarios_5_7_6_10_and_gate_9() -> None:
    prices_3s = [1.0] * 61
    bands = compute_vwap_bands(price=1.0466, vwap=1.055, sigma=0.0042, prices_3s=prices_3s)
    assert bands.buy_trigger == 1.055 - 2.0 * 0.0042
    assert 1.0466 <= bands.buy_trigger

    sigma = apply_sigma_floor(raw_sigma=0.00001, price=2.0)
    assert sigma == 0.001

    bands = compute_vwap_bands(price=1.0662, vwap=1.055, sigma=0.004, prices_3s=prices_3s)
    assert bands.sell_trigger == 1.055 + 2.8 * 0.004
    assert 1.0662 >= bands.sell_trigger

    p = normalize_passive_price(price=1.04661, lower_limit=0.9, upper_limit=1.2)
    assert p == 1.047

    engine = SignalEngine()
    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    snap = TickSnapshot(
        timestamp=datetime(2026, 2, 23, 9, 55, 0),
        last_price=1.0466,
        volume=1_000_000,
        amount=1_000_000.0,
        ask1_price=1.047,
        bid1_price=1.046,
        ask1_vol=100,
        bid1_vol=100,
        iopv=1.045,
        data_quality=DataQuality.OK,
    )
    s = engine.evaluate(
        etf_code="512480",
        now=snap.timestamp,
        instrument=inst,
        snapshot=snap,
        vwap=1.055,
        sigma=0.0042,
        regime_active=True,
        t0_quota=14000.0,
        kde_zones=None,
    )
    assert s is None


def test_vwap_acceptance_scenario_8_delta_volume() -> None:
    eng = VwapEngine()
    s1 = TickSnapshot(
        timestamp=datetime(2026, 2, 23, 9, 30, 3),
        last_price=1.0,
        volume=1_000_000,
        amount=1_000_000.0,
        ask1_price=1.001,
        bid1_price=0.999,
        ask1_vol=100,
        bid1_vol=100,
        data_quality=DataQuality.OK,
    )
    r1 = eng.update(snapshot=s1)
    assert r1.data_quality == DataQuality.STALE

    s2 = TickSnapshot(
        timestamp=datetime(2026, 2, 23, 9, 30, 6),
        last_price=1.0,
        volume=1_050_000,
        amount=1_050_000.0,
        ask1_price=1.001,
        bid1_price=0.999,
        ask1_vol=100,
        bid1_vol=100,
        data_quality=DataQuality.OK,
    )
    r2 = eng.update(snapshot=s2)
    assert r2.data_quality == DataQuality.OK
    assert r2.delta_volume == 50_000


def test_time_window_acceptance_scenarios_11_15() -> None:
    assert is_buy_allowed(now=datetime(2026, 2, 23, 9, 58, 0)) is False
    assert is_sell_allowed(now=datetime(2026, 2, 23, 9, 58, 0)) is False

    assert is_buy_allowed(now=datetime(2026, 2, 23, 11, 25, 0)) is False
    assert is_close_only(now=datetime(2026, 2, 23, 11, 25, 0)) is True

    assert is_buy_allowed(now=datetime(2026, 2, 23, 11, 26, 0)) is False
    assert is_sell_allowed(now=datetime(2026, 2, 23, 11, 26, 0)) is True
    assert is_close_only(now=datetime(2026, 2, 23, 11, 26, 0)) is True

    assert is_buy_allowed(now=datetime(2026, 2, 23, 12, 0, 0)) is False
    assert is_sell_allowed(now=datetime(2026, 2, 23, 12, 0, 0)) is True
    assert is_close_only(now=datetime(2026, 2, 23, 12, 0, 0)) is True

    assert is_reverse_sell_allowed(now=datetime(2026, 2, 23, 14, 2, 0)) is False

    assert is_buy_allowed(now=datetime(2026, 2, 23, 14, 5, 0)) is True
    assert is_buy_allowed(now=datetime(2026, 2, 23, 14, 15, 0)) is False
    assert is_reverse_sell_allowed(now=datetime(2026, 2, 23, 14, 0, 0)) is False
