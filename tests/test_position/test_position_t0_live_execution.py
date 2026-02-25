from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.enums import DataQuality, FSMState, OrderSide, OrderStatus, OrderTimeInForce, OrderType
from core.interfaces import InstrumentInfo, OrderRequest, OrderResult, TickSnapshot
from core.models import PortfolioState, PositionState
from core.state_manager import StateManager

from position.position_fsm import PositionFSM
from t0.types import T0Signal


@dataclass
class _FakeT0Engine:
    signal: Optional[T0Signal] = None

    def compute_daily_regime(self, **kwargs: Any) -> Any:
        _ = kwargs
        return None

    def load_daily_kde(self, **kwargs: Any) -> Any:
        _ = kwargs
        return None

    def evaluate_tick(self, **kwargs: Any) -> Optional[T0Signal]:
        _ = kwargs
        return self.signal


class _FakeData:
    def __init__(self, *, snapshot: TickSnapshot, instrument: InstrumentInfo) -> None:
        self._snap = snapshot
        self._inst = instrument

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        _ = etf_code
        return self._snap

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
    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []
        self.canceled: list[int] = []
        self._next_oid = 1

    def place_order(self, req: OrderRequest) -> OrderResult:
        self.placed.append(req)
        oid = self._next_oid
        self._next_oid += 1
        return OrderResult(order_id=int(oid), status=OrderStatus.SUBMITTED, filled_qty=0, avg_price=None)

    def cancel_order(self, order_id: int) -> bool:
        self.canceled.append(int(order_id))
        return True

    def query_positions(self) -> list[Any]:
        return [{"stock_code": "512480", "total_amount": 50000, "can_use_volume": 50000}]

    def query_orders(self) -> list[Any]:
        return []

    def query_asset(self) -> dict[str, Any]:
        return {}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        _ = timeout_s
        req = self.placed[int(order_id) - 1]
        return OrderResult(order_id=int(order_id), status=OrderStatus.FILLED, filled_qty=int(req.quantity), avg_price=float(req.price))

    def force_reconcile(self) -> dict[str, Any]:
        return {}

    def enter_freeze_mode(self, reason: str) -> None:
        _ = reason
        return None

    def exit_freeze_mode(self) -> None:
        return None


def test_t0_live_order_round_trip_forward_buy_then_sell(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    sm = StateManager(state_path)
    pf = PortfolioState(nav=200000.0, cash=200000.0, frozen_cash=0.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=50000,
        base_qty=50000,
        avg_cost=1.0,
    )

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    snap = TickSnapshot(
        timestamp=datetime(2026, 2, 23, 10, 0, 3),
        last_price=1.0,
        volume=1_000_000,
        amount=1_000_000.0,
        ask1_price=1.001,
        bid1_price=0.999,
        ask1_vol=100,
        bid1_vol=100,
        iopv=1.0,
        data_quality=DataQuality.OK,
    )
    data = _FakeData(snapshot=snap, instrument=inst)
    trading = _FakeTrading()

    buy_sig = T0Signal(
        etf_code="512480",
        timestamp=snap.timestamp,
        signal_type="VWAP_BUY",
        vwap=1.055,
        sigma=0.0042,
        k_value=2.0,
        trend_state="RANGE",
        target_price=1.047,
        amount=14000.0,
        confidence="NORMAL",
        kde_support=False,
        kde_zone_price=None,
        action="PLACE_LIMIT_BUY",
    )
    engine = _FakeT0Engine(signal=buy_sig)

    fsm = PositionFSM(state_manager=sm, data=data, trading=trading, state=pf, t0_engine=engine, t0_log_path=str(tmp_path / "t0.jsonl"))
    oid = fsm.execute_t0_live(now=snap.timestamp, etf_code="512480")
    assert oid == 1
    assert len(pf.positions["512480"].t0_trades) == 1
    assert pf.positions["512480"].t0_trades[0].status == "OPEN"
    assert pf.positions["512480"].t0_trades[0].direction == "FORWARD_T"

    sell_sig = T0Signal(
        etf_code="512480",
        timestamp=snap.timestamp,
        signal_type="VWAP_SELL",
        vwap=1.055,
        sigma=0.0042,
        k_value=2.8,
        trend_state="RANGE",
        target_price=1.055,
        amount=14000.0,
        confidence="NORMAL",
        kde_support=False,
        kde_zone_price=None,
        action="PLACE_LIMIT_SELL",
    )
    engine.signal = sell_sig
    oid2 = fsm.execute_t0_live(now=snap.timestamp, etf_code="512480")
    assert oid2 == 2
    assert pf.positions["512480"].t0_trades[0].status == "CLOSED"
    assert pf.positions["512480"].t0_trades[0].close_order_id == 2
