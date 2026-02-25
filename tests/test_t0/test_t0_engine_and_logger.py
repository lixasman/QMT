from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.enums import DataQuality, OrderSide, OrderStatus
from core.interfaces import InstrumentInfo, TickSnapshot

from t0.t0_fsm import T0Engine
from t0.t0_logger import log_breaker, log_reconciliation, log_regime, log_round_trip, log_signal
from t0.types import BreakerDecision, ReconciliationResult, RegimeResult, RoundTripResult, T0Signal


class _FakeData:
    def __init__(self, *, snaps: list[TickSnapshot], inst: InstrumentInfo) -> None:
        self._snaps = list(snaps)
        self._inst = inst
        self._i = 0

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        _ = etf_code
        i = self._i
        self._i = min(self._i + 1, len(self._snaps) - 1)
        return self._snaps[i]

    def get_bars(self, etf_code: str, period: str, count: int) -> list[Any]:
        _ = etf_code
        _ = period
        _ = count
        return []

    def get_instrument_info(self, etf_code: str) -> InstrumentInfo:
        _ = etf_code
        return self._inst

    def subscribe_quote(self, etf_code: str, callback: Any) -> None:
        _ = etf_code
        _ = callback
        return None

    def get_auction_volume(self, etf_code: str, date: str) -> float:
        _ = etf_code
        _ = date
        return 0.0


class _FakeTrading:
    def place_order(self, req: Any) -> Any:
        _ = req
        raise AssertionError("not used")

    def cancel_order(self, order_id: int) -> bool:
        _ = order_id
        return True

    def query_positions(self) -> list[Any]:
        return []

    def query_orders(self) -> list[Any]:
        return []

    def query_asset(self) -> dict[str, Any]:
        return {}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> Any:
        _ = order_id
        _ = timeout_s
        return {"order_id": int(order_id), "status": "SUBMITTED"}

    def force_reconcile(self) -> dict[str, Any]:
        return {}

    def enter_freeze_mode(self, reason: str) -> None:
        _ = reason
        return None

    def exit_freeze_mode(self) -> None:
        return None


def test_t0_logger_writes_all_types(tmp_path: Path) -> None:
    p = tmp_path / "t0.jsonl"
    ts = datetime(2026, 2, 23, 10, 0, 0)

    log_regime(
        log_path=p,
        result=RegimeResult(regime_active=True, reason="auction_vol", auction_vol_ratio=1.8, atr5_percentile=50.0, computed_at=ts),
        etf_code="512480",
    )
    log_signal(
        log_path=p,
        signal=T0Signal(
            etf_code="512480",
            timestamp=ts,
            signal_type="VWAP_BUY",
            vwap=1.055,
            sigma=0.0042,
            k_value=2.0,
            trend_state="RANGE",
            target_price=1.047,
            amount=14000.0,
            confidence="NORMAL",
            kde_support=True,
            kde_zone_price=1.047,
            action="PLACE_LIMIT_BUY",
        ),
    )
    log_round_trip(
        log_path=p,
        rt=RoundTripResult(
            timestamp=ts,
            etf_code="512480",
            direction="FORWARD_T",
            buy_price=1.047,
            sell_price=1.055,
            quantity=13000,
            commission=10.0,
            net_pnl_cny=73.6,
            net_pnl_bps=57.3,
            actual_be_bps=19.1,
            daily_round_trip_count=1,
            consecutive_loss_count=0,
            t0_daily_pnl=73.6,
        ),
    )
    log_breaker(
        log_path=p,
        d=BreakerDecision(
            timestamp=ts,
            etf_code="512480",
            breaker_layer="LAYER_9_CONSECUTIVE",
            trigger_value=3,
            threshold=3,
            action="FREEZE_UNTIL_NEXT_DAY",
            note="连续 3 笔亏损",
        ),
    )
    log_reconciliation(
        log_path=p,
        r=ReconciliationResult(
            timestamp=ts,
            trigger="TIMEOUT_10S",
            order_id=1,
            case="A",
            memory_status=OrderStatus.SUBMITTED,
            broker_status=OrderStatus.FILLED,
            action="CORRECT_TO_FILLED",
            position_sync=(),
        ),
    )

    lines = p.read_text(encoding="utf-8").splitlines()
    types = [json.loads(x)["type"] for x in lines]
    assert types == ["T0_REGIME", "T0_SIGNAL", "T0_ROUND_TRIP", "T0_BREAKER", "T0_RECONCILIATION"]


def test_t0_engine_smoke_writes_regime_log(tmp_path: Path) -> None:
    p = tmp_path / "t0.jsonl"
    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    snaps = [
        TickSnapshot(
            timestamp=datetime(2026, 2, 23, 10, 0, 0),
            last_price=1.0,
            volume=1_000_000,
            amount=1_000_000.0,
            ask1_price=1.001,
            bid1_price=0.999,
            ask1_vol=100,
            bid1_vol=100,
            iopv=1.0,
            data_quality=DataQuality.OK,
        ),
        TickSnapshot(
            timestamp=datetime(2026, 2, 23, 10, 0, 3),
            last_price=1.0,
            volume=1_050_000,
            amount=1_050_000.0,
            ask1_price=1.001,
            bid1_price=0.999,
            ask1_vol=100,
            bid1_vol=100,
            iopv=1.0,
            data_quality=DataQuality.OK,
        ),
    ]
    data = _FakeData(snaps=snaps, inst=inst)
    trading = _FakeTrading()
    eng = T0Engine(data=data, trading=trading, log_path=str(p))
    _ = eng.compute_daily_regime(etf_code="512480", now=datetime(2026, 2, 23, 9, 26, 0), auction_vol_ratio=1.8, atr5_percentile=50.0)
    _ = eng.evaluate_tick(etf_code="512480", now=snaps[0].timestamp)
    _ = eng.evaluate_tick(etf_code="512480", now=snaps[1].timestamp)

    lines = p.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "T0_REGIME"
