from __future__ import annotations

from datetime import datetime

from core.interfaces import TickSnapshot

from entry.vwap_tracker import VwapTracker


def _snap(ts: str, *, vol: int, amt: float) -> TickSnapshot:
    return TickSnapshot(
        timestamp=datetime.fromisoformat(ts),
        last_price=1.0,
        volume=int(vol),
        amount=float(amt),
        ask1_price=1.0,
        bid1_price=1.0,
        ask1_vol=100,
        bid1_vol=100,
        iopv=None,
    )


def test_vwap_ignores_auction_ticks_scenario_18() -> None:
    v = VwapTracker(anchor_every_n=2)
    s1 = _snap("2026-03-16T09:22:15", vol=1000, amt=1000.0)
    v.update(s1, None)
    assert v.total_volume == 0.0
    assert v.total_amount == 0.0

    s2 = _snap("2026-03-16T09:30:00", vol=1200, amt=1300.0)
    v.update(s2, s1)
    assert v.total_volume == 0.0
    assert v.total_amount == 0.0

    s3 = _snap("2026-03-16T09:30:03", vol=1500, amt=1600.0)
    v.update(s3, s2)
    assert v.total_volume == 300.0
    assert v.total_amount == 300.0

