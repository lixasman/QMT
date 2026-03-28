from __future__ import annotations

from core.enums import FSMState
from core.models import (
    CircuitBreakerInfo,
    LockedOrder,
    PendingEntry,
    PendingSell,
    PortfolioState,
    PositionState,
    T0TradeRecord,
)


def test_models_roundtrip() -> None:
    ps = PortfolioState(
        nav=200000.0,
        hwm=210000.0,
        cash=50000.0,
        frozen_cash=12000.0,
        correlation_matrix_date="20260221",
        circuit_breaker=CircuitBreakerInfo(triggered=True, trigger_date="20260221", trigger_nav=190000.0, hwm_at_trigger=210000.0),
        pending_entries=[
            PendingEntry(etf_code="159915", signal_date="20260221", score_entry=0.79, phase="trial_pending", trial_qty=1000, trial_price=1.234),
        ],
        locked_orders=[
            LockedOrder(order_id=1, etf_code="159915", side="BUY", amount=14000.0, priority=1, strategy_name="T0", lock_time="2026-02-22T09:30:00"),
        ],
        exit_order_intents={
            "101": {
                "action": "FULL_EXIT",
                "etf_code": "159915",
                "locked_qty": 7000,
                "expected_remaining_qty": 7000,
            }
        },
    )
    pos = PositionState(
        etf_code="159915",
        state=FSMState.S3_SCALED,
        base_qty=10000,
        total_qty=12000,
        avg_cost=1.111,
        effective_slot=70000.0,
        t0_frozen=False,
        t0_max_exposure=14000.0,
        pending_sell_locked=[
            PendingSell(
                etf_code="159915",
                locked_qty=7000,
                lock_reason="lifeboat_t1",
                sell_at="next_day_09:30",
                sell_price_type="limit",
                created_time="2026-02-22T10:05:03",
            )
        ],
        t0_trades=[
            T0TradeRecord(
                trade_id="T0_001",
                direction="FORWARD_BUY",
                engine="vwap_sigma",
                open_qty=13000,
                open_price=1.047,
                open_time="2026-02-22T10:25:03",
                status="OPEN",
            )
        ],
        auction_volume_history=[100.0, 120.0, 90.0],
    )
    ps.positions[pos.etf_code] = pos

    d = ps.to_dict()
    ps2 = PortfolioState.from_dict(d)
    assert ps2.to_dict() == d
