from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from core.enums import DataQuality, FSMState, OrderSide, OrderStatus, OrderType
from core.interfaces import InstrumentInfo, OrderRequest, OrderResult, TickSnapshot
from core.models import PendingSell, PortfolioState, PositionState
from core.state_manager import StateManager
from exit.exit_fsm import ExitFSM


class _FakeData:
    def __init__(self, *, inst: InstrumentInfo, last: float, quality: DataQuality = DataQuality.OK) -> None:
        self._inst = inst
        self._last = float(last)
        self._quality = quality

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        _ = etf_code
        now = datetime(2026, 2, 23, 10, 0, 0)
        last = float(self._last)
        return TickSnapshot(
            timestamp=now,
            last_price=float(last),
            volume=0,
            amount=0.0,
            ask1_price=float(last) * 1.001,
            bid1_price=float(last) * 0.999,
            ask1_vol=100,
            bid1_vol=100,
            iopv=None,
            data_quality=self._quality,
        )

    def get_instrument_info(self, etf_code: str) -> InstrumentInfo:
        _ = etf_code
        return self._inst


class _FakeTrading:
    def __init__(self, *, confirm_status: OrderStatus = OrderStatus.FILLED) -> None:
        self._positions: dict[str, dict[str, int]] = {}
        self._orders: dict[int, OrderRequest] = {}
        self._next_oid = 1
        self._applied: set[int] = set()
        self._confirm_status = confirm_status
        self.placed: list[OrderRequest] = []

    def place_order(self, req: OrderRequest) -> OrderResult:
        oid = int(self._next_oid)
        self._next_oid += 1
        self._orders[oid] = req
        self.placed.append(req)
        return OrderResult(order_id=oid, status=OrderStatus.SUBMITTED, filled_qty=0, avg_price=None)

    def query_positions(self) -> list[Any]:
        out: list[Any] = []
        for code, p in self._positions.items():
            out.append({"stock_code": code, "total_amount": int(p["total"]), "can_use_volume": int(p["sellable"])})
        return out

    def query_orders(self) -> list[Any]:
        out: list[Any] = []
        for oid, req in self._orders.items():
            out.append({"order_id": int(oid), "status": "FILLED", "etf_code": req.etf_code})
        return out

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        _ = timeout_s
        oid = int(order_id)
        req = self._orders[oid]
        if self._confirm_status == OrderStatus.FILLED and oid not in self._applied:
            self._applied.add(oid)
            code = str(req.etf_code)
            ps = self._positions.get(code) or {"total": 0, "sellable": 0}
            if req.side == OrderSide.BUY:
                ps["total"] = int(ps["total"]) + int(req.quantity)
                ps["sellable"] = int(ps["sellable"]) + int(req.quantity)
            else:
                ps["total"] = max(0, int(ps["total"]) - int(req.quantity))
                ps["sellable"] = max(0, int(ps["sellable"]) - int(req.quantity))
            self._positions[code] = ps
        return OrderResult(
            order_id=oid,
            status=self._confirm_status,
            filled_qty=(int(req.quantity) if self._confirm_status == OrderStatus.FILLED else 0),
            avg_price=(float(req.price) if self._confirm_status == OrderStatus.FILLED else None),
            raw={"filled_qty": int(req.quantity), "avg_price": float(req.price)},
        )

    def enter_freeze_mode(self, reason: str) -> None:
        _ = reason
        return None


def test_exit_fsm_lifeboat_sell_only_once_then_tight_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    # Position exists for logging/state, balances come from trading adapter.
    st.positions["159870.SZ"] = PositionState(etf_code="159870.SZ", total_qty=1000)
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=0.895)
    trading = _FakeTrading()
    trading._positions["159870.SZ"] = {"total": 1000, "sellable": 1000}

    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path="data/exit.jsonl")

    now = datetime(2026, 2, 23, 10, 0, 0)
    # First STOP_BREAK -> LIFEBOAT 70/30.
    oid1 = fsm.apply_layer1_checks(
        now=now,
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.0,
        data_health={"L1": DataQuality.OK},
        days_held=0,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )
    assert oid1 is not None

    # Still below stop but above tight_stop -> should not keep selling 70% repeatedly.
    oid2 = fsm.apply_layer1_checks(
        now=now,
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.0,
        data_health={"L1": DataQuality.OK},
        days_held=0,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )
    assert oid2 is None

    # Drop below tight_stop -> should clear the remaining sellable.
    data2 = _FakeData(inst=inst, last=0.880)
    fsm2 = ExitFSM(state_manager=sm, data=data2, trading=trading, state=st, log_path="data/exit.jsonl")
    oid3 = fsm2.apply_layer1_checks(
        now=now,
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.0,
        data_health={"L1": DataQuality.OK},
        days_held=0,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )
    assert oid3 is not None


def test_exit_fsm_lifeboat_buyback_only_once_per_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    st.positions["159870.SZ"] = PositionState(
        etf_code="159870.SZ",
        total_qty=3000,
        base_qty=3000,
        avg_cost=1.0,
        lifeboat_used=False,
        lifeboat_sell_time="2026-02-23T09:30:00",
        lifeboat_tight_stop=0.89,
        last_lifeboat_buyback_date="2026-02-23",
    )
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=0.925)
    trading = _FakeTrading()
    trading._positions["159870.SZ"] = {"total": 3000, "sellable": 3000}

    log_path = "data/exit.jsonl"
    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path=log_path)

    now = datetime(2026, 2, 23, 10, 30, 0)
    oid = fsm.apply_lifeboat_buyback_check(
        now=now,
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.0,
        data_health={"L1": DataQuality.OK},
    )
    assert oid is None
    assert len(trading._orders) == 0

    lines = Path(log_path).read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last["type"] == "LIFEBOAT_BUYBACK_REJECTED"
    assert last["reason"] == "ALREADY_BOUGHT_BACK_TODAY"


def test_exit_fsm_layer2_unknown_confirm_does_not_advance_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    st.positions["159870.SZ"] = PositionState(
        etf_code="159870.SZ",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
    )
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=1.0)
    trading = _FakeTrading(confirm_status=OrderStatus.UNKNOWN)
    trading._positions["159870.SZ"] = {"total": 1000, "sellable": 1000}

    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path="data/exit.jsonl")
    oid = fsm.apply_layer2_if_needed(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="159870.SZ",
        score_soft=1.0,
        signals={"S_chip": 1.0},
    )

    assert oid is not None
    ps = st.positions["159870.SZ"]
    assert ps.state == FSMState.S2_BASE
    assert ps.t0_frozen is False
    assert int(ps.total_qty) == 1000


def test_exit_fsm_layer1_unknown_confirm_does_not_advance_full_exit_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    st.positions["159870.SZ"] = PositionState(
        etf_code="159870.SZ",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
    )
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=0.895)
    trading = _FakeTrading(confirm_status=OrderStatus.UNKNOWN)
    trading._positions["159870.SZ"] = {"total": 1000, "sellable": 1000}

    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path="data/exit.jsonl")
    oid = fsm.apply_layer1_checks(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=0,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )

    assert oid is not None
    ps = st.positions["159870.SZ"]
    assert ps.state == FSMState.S2_BASE
    assert ps.pending_sell_locked == []
    assert int(ps.total_qty) == 1000


def test_exit_fsm_layer1_locked_only_full_exit_keeps_state_and_dedupes_pending_sell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    st.positions["159870.SZ"] = PositionState(
        etf_code="159870.SZ",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        same_day_buy_qty=1000,
    )
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=0.895)
    trading = _FakeTrading(confirm_status=OrderStatus.FILLED)
    trading._positions["159870.SZ"] = {"total": 1000, "sellable": 1000}

    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path="data/exit.jsonl", enable_t0=False)
    now = datetime(2026, 2, 23, 10, 0, 0)

    oid1 = fsm.apply_layer1_checks(
        now=now,
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=0,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )
    oid2 = fsm.apply_layer1_checks(
        now=now,
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=0,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )

    assert oid1 is None
    assert oid2 is None
    ps = st.positions["159870.SZ"]
    assert ps.state == FSMState.S2_BASE
    assert ps.t0_frozen is True
    assert int(ps.total_qty) == 1000
    assert len(ps.pending_sell_locked) == 1
    assert int(ps.pending_sell_locked[0].locked_qty) == 1000


def test_exit_fsm_layer1_mixed_sellable_and_locked_full_exit_does_not_write_idle_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    st.positions["159870.SZ"] = PositionState(
        etf_code="159870.SZ",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        same_day_buy_qty=400,
        pending_sell_locked=[
            PendingSell(
                etf_code="159870.SZ",
                locked_qty=50,
                lock_reason="MANUAL_HOLD",
                sell_at="1000",
                sell_price_type="MANUAL",
                created_time="2026-02-23T09:55:00",
            )
        ],
    )
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=0.895)
    trading = _FakeTrading(confirm_status=OrderStatus.FILLED)
    trading._positions["159870.SZ"] = {"total": 1000, "sellable": 1000}

    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path="data/exit.jsonl", enable_t0=False)
    oid = fsm.apply_layer1_checks(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=0,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )

    assert oid is not None
    ps = st.positions["159870.SZ"]
    assert ps.state == FSMState.S2_BASE
    assert ps.t0_frozen is True
    assert int(ps.total_qty) == 1000
    assert len(ps.pending_sell_locked) == 2
    assert any(str(item.lock_reason) == "MANUAL_HOLD" and int(item.locked_qty) == 50 for item in ps.pending_sell_locked)
    assert any(str(item.lock_reason) == "T1_LOCKED" and int(item.locked_qty) == 400 for item in ps.pending_sell_locked)


def test_exit_fsm_lifeboat_buyback_unknown_confirm_does_not_mark_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    st.positions["159870.SZ"] = PositionState(
        etf_code="159870.SZ",
        total_qty=3000,
        base_qty=3000,
        avg_cost=1.0,
        lifeboat_used=False,
        lifeboat_sell_time="2026-02-23T09:30:00",
        lifeboat_tight_stop=0.89,
    )
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=0.925)
    trading = _FakeTrading(confirm_status=OrderStatus.UNKNOWN)
    trading._positions["159870.SZ"] = {"total": 3000, "sellable": 3000}

    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path="data/exit.jsonl")
    oid = fsm.apply_lifeboat_buyback_check(
        now=datetime(2026, 2, 23, 10, 30, 0),
        etf_code="159870.SZ",
        stop_price=0.900,
        score_soft=0.0,
        data_health={"L1": DataQuality.OK},
    )

    assert oid is not None
    assert st.positions["159870.SZ"].lifeboat_used is False


def test_execute_pending_locked_filled_clears_ghost_qty_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state.json")
    st = PortfolioState(nav=100000.0, cash=100000.0)
    st.positions["159870.SZ"] = PositionState(
        etf_code="159870.SZ",
        state=FSMState.S5_REDUCED,
        total_qty=300,
        base_qty=300,
        avg_cost=1.0,
        pending_sell_locked=[
            PendingSell(
                etf_code="159870.SZ",
                locked_qty=300,
                lock_reason="T1_LOCKED",
                sell_at="0930",
                sell_price_type="LAYER1",
                created_time="2026-02-23T10:00:00",
            )
        ],
    )
    sm.save(st)

    inst = InstrumentInfo(etf_code="159870.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(inst=inst, last=0.895)
    trading = _FakeTrading(confirm_status=OrderStatus.FILLED)
    trading._positions["159870.SZ"] = {"total": 300, "sellable": 300}

    fsm = ExitFSM(state_manager=sm, data=data, trading=trading, state=st, log_path="data/exit.jsonl")
    executed = fsm.execute_pending_locked(now=datetime(2026, 2, 24, 9, 31, 0))

    assert executed == 1
    ps = st.positions["159870.SZ"]
    assert ps.state == FSMState.S0_IDLE
    assert int(ps.total_qty) == 0
    assert int(ps.base_qty) == 0
    assert ps.pending_sell_locked == []
