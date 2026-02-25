from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.enums import DataQuality, FSMState, OrderStatus
from core.interfaces import InstrumentInfo, OrderRequest, OrderResult, TickSnapshot
from core.models import PortfolioState, PositionState
from core.state_manager import StateManager

from position.position_fsm import PositionFSM
from position.scale_executor import execute_scale_if_needed
from position.types import ScaleSignalConditions, ScaleSignalEval, ScalePrereqItem, ScalePrerequisites


@dataclass
class _FakeData:
    bid1: float

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        return TickSnapshot(
            timestamp=datetime(2026, 2, 23, 10, 0, 0),
            last_price=float(self.bid1),
            volume=0,
            amount=0.0,
            ask1_price=float(self.bid1) + 0.001,
            bid1_price=float(self.bid1),
            ask1_vol=10000,
            bid1_vol=10000,
            iopv=None,
            stock_status=0,
            data_quality=DataQuality.OK,
        )

    def get_bars(self, etf_code: str, period: str, count: int) -> list[Any]:
        raise AssertionError("not used")

    def get_instrument_info(self, etf_code: str) -> InstrumentInfo:
        raise AssertionError("not used")

    def subscribe_quote(self, etf_code: str, callback: Any) -> None:
        raise AssertionError("not used")

    def get_auction_volume(self, etf_code: str, date: str) -> float:
        raise AssertionError("not used")


@dataclass
class _FakeTrading:
    filled_qty: int
    avg_price: float
    place_calls: int = 0
    canceled: list[int] = None
    freeze_reason: str = ""

    def __post_init__(self) -> None:
        if self.canceled is None:
            self.canceled = []

    def place_order(self, req: OrderRequest) -> OrderResult:
        self.place_calls += 1
        return OrderResult(order_id=1, status=OrderStatus.SUBMITTED, filled_qty=0, avg_price=None, raw=None, error="")

    def cancel_order(self, order_id: int) -> bool:
        self.canceled.append(int(order_id))
        return True

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        return OrderResult(
            order_id=int(order_id),
            status=OrderStatus.FILLED if self.filled_qty > 0 else OrderStatus.SUBMITTED,
            filled_qty=int(self.filled_qty),
            avg_price=float(self.avg_price) if self.filled_qty > 0 else None,
            raw=None,
            error="",
        )

    def query_positions(self) -> list[Any]:
        return []

    def query_orders(self) -> list[Any]:
        return []

    def query_asset(self) -> dict[str, Any]:
        return {}

    def force_reconcile(self) -> dict[str, Any]:
        return {}

    def enter_freeze_mode(self, reason: str) -> None:
        self.freeze_reason = str(reason)

    def exit_freeze_mode(self) -> None:
        return None


def _make_eval(*, etf_code: str, target_amount: float, scale_number: int) -> ScaleSignalEval:
    prereq = ScalePrerequisites(
        passed=True,
        items={"ok": ScalePrereqItem(passed=True, value=True, threshold=None)},
    )
    cond = ScaleSignalConditions(passed=True, items={"ok": ScalePrereqItem(passed=True, value=True, threshold=None)})
    return ScaleSignalEval(
        etf_code=str(etf_code),
        timestamp=datetime(2026, 2, 23, 10, 0, 0),
        prerequisites=prereq,
        conditions=cond,
        decision="SCALE_BUY",
        scale_number=int(scale_number),
        target_amount=float(target_amount),
        order=None,
    )


def test_p0_entry_date_cleared_on_s0(tmp_path: Path) -> None:
    sm = StateManager(tmp_path / "state.json")
    pf = PortfolioState()
    pf.positions["512480"] = PositionState(etf_code="512480", state=FSMState.S1_TRIAL, entry_date="2026-01-01")
    fsm = PositionFSM(
        state_manager=sm,
        data=_FakeData(bid1=1.0),
        trading=_FakeTrading(filled_qty=0, avg_price=0.0),
        state=pf,
        log_path=str(tmp_path / "log.jsonl"),
    )
    fsm.on_entry_failed("512480")
    assert pf.positions["512480"].entry_date == ""

    pf.positions["512480"].state = FSMState.S2_BASE
    pf.positions["512480"].entry_date = "2026-01-02"
    fsm.on_layer1_clear("512480", sold_qty=0)
    assert pf.positions["512480"].entry_date == ""


def test_p0_execute_scale_partial_fill_no_transition(tmp_path: Path) -> None:
    log_path = str(tmp_path / "pos_log.jsonl")
    state_path = tmp_path / "state.json"
    sm = StateManager(state_path)
    pf = PortfolioState(cash=200000.0, frozen_cash=0.0)
    pf.positions["512480"] = PositionState(etf_code="512480", state=FSMState.S2_BASE, total_qty=10000, avg_cost=1.0, base_qty=10000)
    fsm = PositionFSM(state_manager=sm, data=_FakeData(bid1=1.0), trading=_FakeTrading(filled_qty=100, avg_price=1.0), state=pf, log_path=log_path)

    ev = _make_eval(etf_code="512480", target_amount=10000.0, scale_number=1)
    oid = fsm.execute_scale(now=datetime(2026, 2, 23, 10, 0, 0), etf_code="512480", eval_result=ev)
    assert oid == 1
    assert pf.positions["512480"].state == FSMState.S2_BASE
    assert pf.positions["512480"].scale_1_qty == 100

    lines = Path(log_path).read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last["type"] == "FSM_TRANSITION"
    assert last["from_state"] == "S2"
    assert last["to_state"] == "S2"
    assert "PARTIAL" in last["trigger"]


def test_p0_execute_scale_full_fill_transition_and_log(tmp_path: Path) -> None:
    log_path = str(tmp_path / "pos_log.jsonl")
    state_path = tmp_path / "state.json"
    sm = StateManager(state_path)
    pf = PortfolioState(cash=200000.0, frozen_cash=0.0)
    pf.positions["512480"] = PositionState(etf_code="512480", state=FSMState.S2_BASE, total_qty=10000, avg_cost=1.0, base_qty=10000)
    fsm = PositionFSM(state_manager=sm, data=_FakeData(bid1=1.0), trading=_FakeTrading(filled_qty=100, avg_price=1.0), state=pf, log_path=log_path)

    ev = _make_eval(etf_code="512480", target_amount=100.0, scale_number=1)
    oid = fsm.execute_scale(now=datetime(2026, 2, 23, 10, 0, 0), etf_code="512480", eval_result=ev)
    assert oid == 1
    assert pf.positions["512480"].state == FSMState.S3_SCALED

    lines = Path(log_path).read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last["type"] == "FSM_TRANSITION"
    assert last["from_state"] == "S2"
    assert last["to_state"] == "S3"
    assert last["trigger"] == "SCALE_1_FILLED"


def test_p0_scale_executor_lock_cash_failure_cancels_order() -> None:
    class _BadCash:
        def available_cash(self) -> float:
            return 999999.0

        def lock_cash(self, **kwargs: Any) -> None:
            raise AssertionError("boom")

    data = _FakeData(bid1=1.0)
    trading = _FakeTrading(filled_qty=0, avg_price=0.0)
    ev = _make_eval(etf_code="512480", target_amount=1000.0, scale_number=1)
    oid = execute_scale_if_needed(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        cash_manager=_BadCash(),
        data=data,
        trading=trading,
        eval_result=ev,
        log_path="data/tmp_log_p0.jsonl",
    )
    assert oid is None
    assert trading.canceled == [1]
    assert trading.freeze_reason == "SCALE_LOCK_CASH_FAILED"
