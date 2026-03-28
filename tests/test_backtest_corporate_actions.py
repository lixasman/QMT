from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from backtest.corporate_actions import apply_price_factor_to_pending_entries, apply_price_factor_to_position_state
from backtest.store import MarketDataStore
from core.enums import FSMState
from core.models import PendingEntry, PendingSell, PositionState


def _write_daily_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        [datetime(2025, 7, 17, 15, 0).timestamp() * 1000.0, 1.2, 1.224, 1.186, 1.191, 2_923_752.0, 352_321_408.0],
        [datetime(2025, 7, 21, 15, 0).timestamp() * 1000.0, 0.595, 0.6, 0.584, 0.594, 2_782_828.0, 164_521_500.0],
        [datetime(2025, 7, 22, 15, 0).timestamp() * 1000.0, 0.594, 0.604, 0.585, 0.59, 3_460_172.0, 205_360_376.0],
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume", "amount"])
        w.writerows(rows)


def test_market_store_applies_split_factor_to_prev_close_and_daily_bars(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_daily_csv(data_root / "1d" / "159363_SZ.csv")

    store = MarketDataStore(data_root=data_root, codes=["159363.SZ"], tick_root=tmp_path / "tick_missing", load_minute=False)

    ev = store.corporate_action_on_day(code="159363.SZ", day=date(2025, 7, 21))
    assert ev is not None
    assert abs(float(ev.price_factor) - 0.5) < 1e-9
    assert abs(float(ev.quantity_factor) - 2.0) < 1e-9

    prev_close = store.previous_close(code="159363.SZ", day=date(2025, 7, 21))
    assert prev_close is not None
    assert abs(float(prev_close) - 0.5955) < 1e-6

    bars = store.daily_bars(code="159363.SZ", now=datetime(2025, 7, 21, 9, 25), count=5, include_today=False)
    assert len(bars) == 1
    assert abs(float(bars[0].high) - 0.612) < 1e-6
    assert abs(float(bars[0].close) - 0.5955) < 1e-6
    assert abs(float(bars[0].volume) - 5_847_504.0) < 1e-3


def test_apply_price_factor_to_state_and_pending_entries() -> None:
    ps = PositionState(
        etf_code="159363.SZ",
        state=FSMState.S2_BASE,
        base_qty=42_700,
        scale_1_qty=0,
        scale_2_qty=0,
        total_qty=42_700,
        avg_cost=1.165,
        highest_high=1.224,
        lifeboat_tight_stop=1.1348,
    )
    ps.pending_sell_locked = [PendingSell(etf_code="159363.SZ", locked_qty=12_900, lock_reason="X", sell_at="", sell_price_type="", created_time="")]
    ps.pending_sell_unfilled = [PendingSell(etf_code="159363.SZ", locked_qty=2_000, lock_reason="Y", sell_at="", sell_price_type="", created_time="")]
    ps.auction_volume_history = [1000.0, 2000.0]

    pe = PendingEntry(
        etf_code="159363.SZ",
        signal_date="20250715",
        score_entry=0.64,
        phase="phase3",
        h_signal=1.20,
        l_signal=1.10,
        close_signal_day=1.151,
        atr_20=0.03,
        trial_qty=12_800,
        trial_price=1.164,
        confirm_qty=29_900,
        confirm_price=1.165,
    )

    apply_price_factor_to_position_state(ps=ps, price_factor=0.5)
    changed = apply_price_factor_to_pending_entries(pending_entries=[pe], etf_code="159363.SZ", price_factor=0.5)

    assert changed == 1
    assert ps.total_qty == 85_400
    assert ps.base_qty == 85_400
    assert abs(float(ps.avg_cost) - 0.5825) < 1e-9
    assert abs(float(ps.highest_high) - 0.612) < 1e-9
    assert abs(float(ps.lifeboat_tight_stop) - 0.5674) < 1e-9
    assert [p.locked_qty for p in ps.pending_sell_locked] == [25_800]
    assert [p.locked_qty for p in ps.pending_sell_unfilled] == [4_000]
    assert ps.auction_volume_history == [2000.0, 4000.0]

    assert pe.trial_qty == 25_600
    assert pe.confirm_qty == 59_800
    assert abs(float(pe.h_signal) - 0.6) < 1e-9
    assert abs(float(pe.close_signal_day) - 0.5755) < 1e-9
    assert abs(float(pe.trial_price or 0.0) - 0.582) < 1e-9
