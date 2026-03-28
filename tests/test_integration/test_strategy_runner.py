from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from backtest.adapters import BacktestTradingAdapter
from backtest.clock import SimulatedClock
from core.enums import DataQuality, FSMState, OrderSide, OrderStatus, OrderType
from core.interfaces import Bar, InstrumentInfo, OrderRequest, OrderResult, TickSnapshot
from core.models import PendingEntry, PortfolioState, PositionState
from core.state_manager import StateManager

from strategy_config import StrategyConfig
from strategy_runner import StrategyRunner


class _NowSeq:
    def __init__(self, seq: list[datetime]) -> None:
        self._seq = list(seq)
        self._current = self._seq[0] if self._seq else datetime.now()

    def peek(self) -> datetime:
        return self._current

    def __call__(self) -> datetime:
        if not self._seq:
            self._current = datetime.now()
            return self._current
        if len(self._seq) == 1:
            self._current = self._seq[0]
            return self._current
        self._current = self._seq.pop(0)
        return self._current


@dataclass
class _FakeT0Engine:
    def compute_daily_regime(self, **kwargs: Any) -> Any:
        _ = kwargs
        return None

    def load_daily_kde(self, **kwargs: Any) -> Any:
        _ = kwargs
        return None

    def evaluate_tick(self, **kwargs: Any) -> Any:
        _ = kwargs
        return None


class _FakeData:
    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        instrument: InstrumentInfo,
        bars: list[Bar],
        price_fn: Optional[Callable[[datetime, str], float]] = None,
        quality: DataQuality = DataQuality.OK,
        iopv: Optional[float] = 1.0,
        divid_factors: Any = None,
    ) -> None:
        self._now = now
        self._inst = instrument
        self._bars = list(bars)
        self._price_fn = price_fn
        self._quality = quality
        self._iopv = iopv
        self._divid_factors = divid_factors
        self._tick_idx: dict[str, int] = {}

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        now = self._now()
        idx = 1 + int(self._tick_idx.get(etf_code, 0))
        self._tick_idx[etf_code] = idx
        last = float(self._price_fn(now, etf_code)) if self._price_fn is not None else 1.0
        vol = idx * 100
        amt = float(last) * float(vol)
        return TickSnapshot(
            timestamp=now,
            last_price=float(last),
            volume=int(vol),
            amount=float(amt),
            ask1_price=float(last) * 1.001,
            bid1_price=float(last) * 0.999,
            ask1_vol=100,
            bid1_vol=100,
            iopv=self._iopv,
            data_quality=self._quality,
        )

    def get_bars(self, etf_code: str, period: str, count: int) -> list[Bar]:
        _ = etf_code
        _ = period
        _ = count
        return list(self._bars)

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

    def get_divid_factors(self, etf_code: str, start_time: str = "", end_time: str = "") -> Any:
        _ = etf_code
        _ = start_time
        _ = end_time
        return self._divid_factors


class _FakeTrading:
    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []
        self._orders: dict[int, OrderRequest] = {}
        self._applied: set[int] = set()
        self._positions: dict[str, dict[str, int]] = {}
        self._next_oid = 1

    def place_order(self, req: OrderRequest) -> OrderResult:
        oid = int(self._next_oid)
        self._next_oid += 1
        self.placed.append(req)
        self._orders[oid] = req
        return OrderResult(order_id=oid, status=OrderStatus.SUBMITTED, filled_qty=0, avg_price=None)

    def cancel_order(self, order_id: int) -> bool:
        _ = order_id
        return True

    def query_positions(self) -> list[Any]:
        out: list[Any] = []
        for code, p in self._positions.items():
            row = {"stock_code": code, "total_amount": int(p["total"]), "can_use_volume": int(p["sellable"])}
            if "avg_cost" in p:
                row["cost_price"] = float(p["avg_cost"])
            out.append(row)
        return out

    def query_orders(self) -> list[Any]:
        out: list[Any] = []
        for oid, req in self._orders.items():
            out.append({"order_id": int(oid), "status": "FILLED", "etf_code": req.etf_code})
        return out

    def query_asset(self) -> dict[str, Any]:
        return {"cash": 100000.0, "nav": 100000.0}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        _ = timeout_s
        oid = int(order_id)
        req = self._orders[oid]
        if oid not in self._applied:
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
        return OrderResult(order_id=oid, status=OrderStatus.FILLED, filled_qty=int(req.quantity), avg_price=float(req.price), raw={"filled_qty": int(req.quantity), "avg_price": float(req.price)})

    def force_reconcile(self) -> dict[str, Any]:
        return {}

    def enter_freeze_mode(self, reason: str) -> None:
        _ = reason
        return None

    def exit_freeze_mode(self) -> None:
        return None


def _basic_bars() -> list[Bar]:
    return [
        Bar(time=datetime(2026, 2, 20, 15, 0), open=1.0, high=1.02, low=0.99, close=1.0, volume=1.0, amount=1.0),
        Bar(time=datetime(2026, 2, 21, 15, 0), open=1.0, high=1.02, low=0.99, close=1.0, volume=1.0, amount=1.0),
        Bar(time=datetime(2026, 2, 22, 15, 0), open=1.0, high=1.02, low=0.99, close=1.0, volume=1.0, amount=1.0),
    ]


def test_runner_full_day_cycle_persists_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    now = _NowSeq(
        [
            datetime(2026, 2, 23, 9, 20, 0),
            datetime(2026, 2, 23, 9, 20, 0),
            datetime(2026, 2, 23, 9, 31, 0),
            datetime(2026, 2, 23, 9, 31, 3),
            datetime(2026, 2, 23, 9, 31, 6),
            datetime(2026, 2, 23, 15, 5, 0),
        ]
    )

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=now.peek, instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0, enable_t0=True)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    runner.run_day(wait_for_market=False, max_ticks=3, now_provider=now, sleep_fn=lambda _: None)

    assert Path("data/state/portfolio.json").exists()
    st2 = sm.load()
    assert isinstance(st2, PortfolioState)


def test_runner_entry_to_confirm_then_layer1_full_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.pending_entries.append(
        PendingEntry(
            etf_code="512480",
            signal_date="20260223",
            score_entry=0.6,
            phase="phase3",
            h_signal=0.995,
            l_signal=0.98,
            close_signal_day=1.0,
            atr_20=0.02,
            is_strong=False,
            expire_date="20260228",
            status="PENDING_TRIAL",
        )
    )
    sm.save(pf)

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        if dt.time() == datetime(2026, 2, 23, 13, 0, 0).time():
            return 0.8
        return 1.0

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=2.0, limit_up=2.2, limit_down=1.8)
    current = {"now": datetime(2026, 2, 23, 9, 20, 0)}
    data = _FakeData(now=lambda: current["now"], instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0, enable_t0=True)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    runner._pre_open(now=current["now"])
    current["now"] = datetime(2026, 2, 23, 9, 31, 0)
    runner._tick_cycle(now=current["now"])
    current["now"] = datetime(2026, 2, 23, 9, 32, 0)
    runner._tick_cycle(now=current["now"])
    current["now"] = datetime(2026, 2, 23, 13, 0, 0)
    runner._tick_cycle(now=current["now"])
    current["now"] = datetime(2026, 2, 23, 15, 5, 0)
    runner._post_close(now=current["now"])

    st2 = sm.load()
    ps = st2.positions.get("512480")
    assert ps is not None
    assert ps.state == FSMState.S0_IDLE
    assert int(ps.total_qty) == 0


def test_runner_entry_to_confirm_then_locked_layer1_exit_schedules_next_day_sell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.pending_entries.append(
        PendingEntry(
            etf_code="512480",
            signal_date="20260223",
            score_entry=0.6,
            phase="phase3",
            h_signal=0.995,
            l_signal=0.98,
            close_signal_day=1.0,
            atr_20=0.02,
            is_strong=False,
            expire_date="20260228",
            status="PENDING_TRIAL",
        )
    )
    sm.save(pf)

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        if dt.time() == datetime(2026, 2, 23, 13, 0, 0).time():
            return 0.8
        return 1.0

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=2.0, limit_up=2.2, limit_down=1.8)
    current = {"now": datetime(2026, 2, 23, 9, 20, 0)}
    data = _FakeData(now=lambda: current["now"], instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    runner._pre_open(now=current["now"])
    current["now"] = datetime(2026, 2, 23, 9, 31, 0)
    runner._tick_cycle(now=current["now"])
    current["now"] = datetime(2026, 2, 23, 9, 32, 0)
    runner._tick_cycle(now=current["now"])
    current["now"] = datetime(2026, 2, 23, 13, 0, 0)
    runner._tick_cycle(now=current["now"])

    st2 = sm.load()
    ps = st2.positions.get("512480")
    assert ps is not None
    assert ps.state == FSMState.S2_BASE
    assert ps.t0_frozen is True
    assert int(ps.total_qty) == 25300
    assert len(ps.pending_sell_locked) == 1
    assert int(ps.pending_sell_locked[0].locked_qty) == 25300


def test_runner_handle_exit_sell_full_exit_with_locked_residual_reduces_to_s5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        same_day_buy_qty=400,
        entry_date="2026-02-20",
    )
    sm.save(pf)

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        _ = dt
        return 0.895

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 10, 0, 0), instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _FakeTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    ps = runner.state.positions["512480"]
    oid = runner._exit_fsm.apply_layer1_checks(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=1,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )

    assert oid is not None
    assert ps.state == FSMState.S2_BASE
    runner._handle_exit_sell(now=datetime(2026, 2, 23, 10, 0, 0), etf_code="512480", order_id=int(oid), ps=ps)

    st2 = sm.load()
    ps2 = st2.positions["512480"]
    assert ps2.state == FSMState.S5_REDUCED
    assert int(ps2.total_qty) == 400
    assert int(ps2.base_qty) == 400
    assert int(ps2.same_day_buy_qty) == 400
    assert len(ps2.pending_sell_locked) == 1
    assert int(ps2.pending_sell_locked[0].locked_qty) == 400


def test_runner_startup_replays_persisted_full_exit_intent_with_locked_residual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        same_day_buy_qty=400,
        entry_date="2026-02-20",
    )
    sm.save(pf)

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        _ = dt
        return 0.895

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 10, 0, 0), instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _FakeTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner1 = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    oid = runner1._exit_fsm.apply_layer1_checks(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=1,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )

    assert oid is not None

    runner2 = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    st3 = sm.load()
    ps3 = st3.positions["512480"]
    assert ps3.state == FSMState.S5_REDUCED
    assert int(ps3.total_qty) == 400
    assert int(ps3.base_qty) == 400
    assert int(ps3.same_day_buy_qty) == 400
    assert len(ps3.pending_sell_locked) == 1
    assert int(ps3.pending_sell_locked[0].locked_qty) == 400
    assert st3.exit_order_intents == {}


def test_runner_runtime_replays_persisted_full_exit_intent_after_delayed_fill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        same_day_buy_qty=400,
        entry_date="2026-02-20",
    )
    sm.save(pf)

    class _DelayedExitTrading(_FakeTrading):
        def __init__(self) -> None:
            super().__init__()
            self.confirm_status = OrderStatus.UNKNOWN

        def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
            _ = timeout_s
            oid = int(order_id)
            req = self._orders[oid]
            if self.confirm_status == OrderStatus.FILLED and oid not in self._applied:
                self._applied.add(oid)
                code = str(req.etf_code)
                self._positions[code] = {"total": 400, "sellable": 400}
            if self.confirm_status == OrderStatus.FILLED:
                return OrderResult(order_id=oid, status=OrderStatus.FILLED, filled_qty=int(req.quantity), avg_price=float(req.price), raw={"filled_qty": int(req.quantity), "avg_price": float(req.price)})
            return OrderResult(order_id=oid, status=OrderStatus.UNKNOWN, filled_qty=0, avg_price=None, raw={})

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        if dt.time() == datetime(2026, 2, 23, 10, 0, 0).time():
            return 0.895
        return 1.0

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    current = {"now": datetime(2026, 2, 23, 10, 0, 0)}
    data = _FakeData(now=lambda: current["now"], instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _DelayedExitTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner1 = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    oid = runner1._exit_fsm.apply_layer1_checks(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=1,
        current_return=-0.01,
        t0_realized_loss_pct=0.0,
    )
    assert oid is not None

    runner2 = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    st2 = sm.load()
    assert "512480" in st2.positions
    assert st2.exit_order_intents != {}

    trading.confirm_status = OrderStatus.FILLED
    current["now"] = datetime(2026, 2, 23, 10, 1, 0)
    runner2._tick_cycle(now=datetime(2026, 2, 23, 10, 1, 0))

    st3 = sm.load()
    ps3 = st3.positions["512480"]
    assert ps3.state == FSMState.S5_REDUCED
    assert int(ps3.total_qty) == 400
    assert len(ps3.pending_sell_locked) == 1
    assert int(ps3.pending_sell_locked[0].locked_qty) == 400
    assert st3.exit_order_intents == {}


def test_runner_runtime_replays_persisted_full_exit_intent_to_flat_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        entry_date="2026-02-20",
    )
    sm.save(pf)

    class _DelayedFlatExitTrading(_FakeTrading):
        def __init__(self) -> None:
            super().__init__()
            self.confirm_status = OrderStatus.UNKNOWN

        def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
            _ = timeout_s
            oid = int(order_id)
            req = self._orders[oid]
            if self.confirm_status == OrderStatus.FILLED and oid not in self._applied:
                self._applied.add(oid)
                code = str(req.etf_code)
                self._positions[code] = {"total": 0, "sellable": 0}
            if self.confirm_status == OrderStatus.FILLED:
                return OrderResult(order_id=oid, status=OrderStatus.FILLED, filled_qty=int(req.quantity), avg_price=float(req.price), raw={"filled_qty": int(req.quantity), "avg_price": float(req.price)})
            return OrderResult(order_id=oid, status=OrderStatus.UNKNOWN, filled_qty=0, avg_price=None, raw={})

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        if dt.time() == datetime(2026, 2, 23, 10, 0, 0).time():
            return 0.895
        return 1.0

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    current = {"now": datetime(2026, 2, 23, 10, 0, 0)}
    data = _FakeData(now=lambda: current["now"], instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _DelayedFlatExitTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner1 = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    oid = runner1._exit_fsm.apply_layer1_checks(
        now=datetime(2026, 2, 23, 10, 0, 0),
        etf_code="512480",
        stop_price=0.900,
        score_soft=0.7,
        data_health={"L1": DataQuality.OK},
        days_held=2,
        current_return=-0.02,
        t0_realized_loss_pct=0.0,
    )
    assert oid is not None

    runner2 = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    trading.confirm_status = OrderStatus.FILLED
    current["now"] = datetime(2026, 2, 23, 10, 1, 0)
    runner2._tick_cycle(now=datetime(2026, 2, 23, 10, 1, 0))

    st2 = sm.load()
    ps2 = st2.positions["512480"]
    assert ps2.state == FSMState.S0_IDLE
    assert int(ps2.total_qty) == 0
    assert int(ps2.base_qty) == 0
    assert st2.exit_order_intents == {}


def test_runner_startup_drops_terminal_nonfill_exit_intent_with_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import strategy_runner as strategy_runner_module

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        entry_date="2026-02-20",
    )
    pf.exit_order_intents["7"] = {
        "action": "FULL_EXIT",
        "etf_code": "512480",
        "locked_qty": 400,
        "expected_remaining_qty": 400,
    }
    sm.save(pf)

    class _RejectedExitTrading(_FakeTrading):
        def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
            _ = order_id
            _ = timeout_s
            return OrderResult(order_id=7, status=OrderStatus.REJECTED, filled_qty=0, avg_price=None, raw={}, error="rejected")

    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        strategy_runner_module,
        "alert_once",
        lambda key, message: alerts.append((str(key), str(message))),
    )

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 10, 0, 0), instrument=inst, bars=_basic_bars())
    trading = _RejectedExitTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    _ = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    st2 = sm.load()
    assert st2.exit_order_intents == {}
    assert len(alerts) == 1
    assert alerts[0][0] == "startup_exit_intent_terminal_without_fill:512480:7"
    assert "terminal non-fill status" in alerts[0][1]


def test_runner_handle_exit_sell_missing_intent_emits_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import strategy_runner as strategy_runner_module

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        entry_date="2026-02-20",
    )
    sm.save(pf)

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 10, 0, 0), instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0, enable_t0=True)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        strategy_runner_module,
        "alert_once",
        lambda key, message: alerts.append((str(key), str(message))),
    )

    req = OrderRequest(
        etf_code="512480",
        side=OrderSide.SELL,
        quantity=1000,
        order_type=OrderType.LIMIT,
        price=0.98,
        strategy_name="exit",
        remark="LAYER1",
    )
    res = trading.place_order(req)
    ps = runner.state.positions["512480"]
    runner._handle_exit_sell(now=datetime(2026, 2, 23, 10, 0, 0), etf_code="512480", order_id=int(res.order_id), ps=ps)

    assert len(alerts) == 1
    assert alerts[0][0] == "exit_missing_order_intent:512480:1"
    assert "missing persisted exit order intent" in alerts[0][1]


def test_runner_startup_reconciles_stale_local_position_against_flat_broker_for_no_reentry_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        entry_date="2026-02-20",
    )
    sm.save(pf)

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 9, 20, 0), instrument=inst, bars=_basic_bars())
    class _FlatTrading(_FakeTrading):
        def query_positions(self) -> list[Any]:
            return [{"stock_code": "512480", "total_amount": 0, "can_use_volume": 0}]

    trading = _FlatTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    blocked, reason = runner._should_block_phase2_entry_after_signal(now=datetime(2026, 2, 23, 15, 5, 0), etf_code="512480")

    assert blocked is False
    assert reason == ""
    assert "512480" not in runner.state.positions


def test_runner_startup_recovers_confirm_entry_filled_during_downtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.pending_entries.append(
        PendingEntry(
            etf_code="512480",
            signal_date="20260223",
            score_entry=0.6,
            phase="phase3",
            h_signal=0.995,
            l_signal=0.98,
            close_signal_day=1.0,
            atr_20=0.02,
            is_strong=False,
            expire_date="20260228",
            status="CONFIRM_PLACED",
            confirm_qty=1000,
            confirm_price=1.23,
            confirm_order_id=88,
        )
    )
    sm.save(pf)

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 9, 20, 0), instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000, "avg_cost": 1.23}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    assert runner.state.pending_entries == []
    ps = runner.state.positions["512480"]
    assert ps.state == FSMState.S2_BASE
    assert int(ps.total_qty) == 1000
    assert int(ps.base_qty) == 1000
    assert abs(float(ps.avg_cost) - 1.23) < 1e-9


def test_runner_startup_recovers_trial_entry_filled_during_downtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.pending_entries.append(
        PendingEntry(
            etf_code="512480",
            signal_date="20260223",
            score_entry=0.6,
            phase="phase3",
            h_signal=0.995,
            l_signal=0.98,
            close_signal_day=1.0,
            atr_20=0.02,
            is_strong=False,
            expire_date="20260228",
            status="TRIAL_PLACED",
            trial_qty=1000,
            trial_price=1.23,
            trial_order_id=77,
        )
    )
    sm.save(pf)

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 9, 20, 0), instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000, "avg_cost": 1.23}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    assert len(runner.state.pending_entries) == 1
    assert str(runner.state.pending_entries[0].status) == "PENDING_CONFIRM"
    ps = runner.state.positions["512480"]
    assert ps.state == FSMState.S1_TRIAL
    assert int(ps.total_qty) == 1000
    assert int(ps.base_qty) == 0
    assert abs(float(ps.avg_cost) - 1.23) < 1e-9


def test_runner_startup_recovers_broker_only_position_into_manageable_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 9, 20, 0), instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000, "avg_cost": 1.11}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    ps = runner.state.positions["512480"]
    assert ps.state == FSMState.S2_BASE
    assert int(ps.total_qty) == 1000
    assert int(ps.base_qty) == 1000
    assert abs(float(ps.avg_cost) - 1.11) < 1e-9


def test_runner_startup_keeps_local_position_when_broker_snapshot_is_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
        entry_date="2026-02-20",
    )
    sm.save(pf)

    class _PartialTrading(_FakeTrading):
        def query_positions(self) -> list[Any]:
            return [{"stock_code": "159915", "total_amount": 500, "can_use_volume": 500, "cost_price": 0.95}]

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 9, 20, 0), instrument=inst, bars=_basic_bars())
    trading = _PartialTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    ps = runner.state.positions["512480"]
    assert ps.state == FSMState.S2_BASE
    assert int(ps.total_qty) == 1000
    assert int(ps.base_qty) == 1000


def test_runner_skips_actions_when_data_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
    )
    sm.save(pf)

    now = _NowSeq(
        [
            datetime(2026, 2, 23, 9, 20, 0),
            datetime(2026, 2, 23, 9, 20, 0),
            datetime(2026, 2, 23, 9, 31, 0),
            datetime(2026, 2, 23, 15, 5, 0),
        ]
    )

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=now.peek, instrument=inst, bars=_basic_bars(), quality=DataQuality.STALE)
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    runner.run_day(wait_for_market=False, max_ticks=1, now_provider=now, sleep_fn=lambda _: None)

    assert trading.placed == []


def test_runner_opening_gap_check_is_gap_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["512480"] = PositionState(
        etf_code="512480",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
    )
    sm.save(pf)

    now = _NowSeq(
        [
            datetime(2026, 2, 23, 9, 25, 0),  # run_day start
            datetime(2026, 2, 23, 9, 25, 0),  # pre_open + opening gap checks
            datetime(2026, 2, 23, 9, 30, 0),  # first intraday tick
            datetime(2026, 2, 23, 15, 5, 0),
        ]
    )

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        if dt.time() == datetime(2026, 2, 23, 9, 25, 0).time():
            return 0.99  # below stop but above stop*0.97 -> should NOT trigger GAP_PROTECTION
        return 1.05

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=now.peek, instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _FakeTrading()
    trading._positions["512480"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    monkeypatch.setattr(runner, "_compute_stop", lambda **kwargs: (1.0, 0.0, 0.0, 0.0))
    runner.run_day(wait_for_market=False, max_ticks=1, now_provider=now, sleep_fn=lambda _: None)

    assert trading.placed == []


def test_gui_ops_freeze_and_post_close_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    easytrader = pytest.importorskip("easytrader")
    _ = easytrader

    from core.adapters.gui_trading_adapter import GuiTradingAdapter

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    now = _NowSeq([datetime(2026, 2, 23, 15, 5, 0)])

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=now.peek, instrument=inst, bars=_basic_bars())

    class _FakeClient:
        def __init__(self) -> None:
            self._oid = 1

        def buy(self, code: str, price: float, amount: int) -> Any:
            _ = code
            _ = price
            _ = amount
            oid = self._oid
            self._oid += 1
            return {"order_id": int(oid)}

        def sell(self, code: str, price: float, amount: int) -> Any:
            return self.buy(code, price, amount)

        def cancel_entrust(self, order_id: int) -> bool:
            _ = order_id
            return True

        def position(self) -> list[Any]:
            return []

        def today_entrusts(self) -> list[Any]:
            return []

        def balance(self) -> dict[str, Any]:
            return {"cash": 100000.0, "nav": 100000.0}

    adapter = GuiTradingAdapter(_FakeClient(), gui_ops_limit=20, freeze_threshold=15)

    req = OrderRequest(etf_code="512480", side=OrderSide.BUY, quantity=100, order_type=OrderType.LIMIT, price=1.0)
    last = None
    for _i in range(16):
        last = adapter.place_order(req)
    assert last is not None
    assert last.status == OrderStatus.REJECTED

    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0, trading_adapter_type="gui")
    runner = StrategyRunner(cfg, data=data, trading=adapter, state_manager=sm, t0_engine=_FakeT0Engine())
    runner._post_close(now=now.peek())

    ok = adapter.place_order(req)
    assert ok.status != OrderStatus.REJECTED


def test_backtest_freeze_and_post_close_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    now = _NowSeq([datetime(2026, 2, 23, 15, 5, 0)])

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9)
    data = _FakeData(now=now.peek, instrument=inst, bars=_basic_bars())
    adapter = BacktestTradingAdapter(
        clock=SimulatedClock(datetime(2026, 2, 23, 15, 5, 0)),
        initial_cash=100000.0,
        fee_rate=0.0,
        enable_t0=False,
    )
    adapter.enter_freeze_mode("manual freeze")

    req = OrderRequest(etf_code="512480", side=OrderSide.BUY, quantity=100, order_type=OrderType.LIMIT, price=1.0)
    frozen = adapter.place_order(req)
    assert frozen.status == OrderStatus.REJECTED

    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)
    runner = StrategyRunner(cfg, data=data, trading=adapter, state_manager=sm, t0_engine=_FakeT0Engine())
    runner._post_close(now=now.peek())

    ok = adapter.place_order(req)
    assert ok.status != OrderStatus.REJECTED


def test_runner_pre_open_rescales_state_on_corporate_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["159363.SZ"] = PositionState(
        etf_code="159363.SZ",
        state=FSMState.S2_BASE,
        base_qty=42700,
        total_qty=42700,
        avg_cost=1.165,
        highest_high=1.224,
        lifeboat_tight_stop=1.1348,
    )
    pf.pending_entries.append(
        PendingEntry(
            etf_code="159363.SZ",
            signal_date="20250715",
            score_entry=0.64,
            phase="phase3",
            h_signal=1.20,
            l_signal=1.10,
            close_signal_day=1.151,
            atr_20=0.03,
            trial_qty=12800,
            trial_price=1.164,
            confirm_qty=29900,
            confirm_price=1.165,
        )
    )
    pf.phase2_high_chase_signals["159363.SZ"] = [{"signal_date": "20250715", "ref_price": 1.151}]
    sm.save(pf)

    now = _NowSeq([datetime(2025, 7, 21, 9, 25, 0)])
    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=0.595, limit_up=0.655, limit_down=0.535, price_tick=0.001)
    data = _FakeData(
        now=now.peek,
        instrument=inst,
        bars=_basic_bars(),
        divid_factors={"_index": "unused"},
    )
    data._divid_factors = [{"_index": "20250721", "dr": 1.985}]
    trading = _FakeTrading()
    trading._positions["159363.SZ"] = {"total": 85400, "sellable": 85400}

    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("159363.SZ",), tick_interval_s=0.0)
    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    runner._pre_open(now=now.peek())

    st2 = sm.load()
    ps = st2.positions["159363.SZ"]
    assert ps.total_qty == 85400
    assert abs(float(ps.avg_cost) - 0.5825) < 1e-9
    assert abs(float(ps.highest_high) - 0.612) < 1e-9
    assert abs(float(ps.lifeboat_tight_stop) - 0.5674) < 1e-9
    assert ps.last_corporate_action_date == "20250721"
    assert st2.pending_entries[0].trial_qty == 25600
    assert st2.pending_entries[0].confirm_qty == 59800
    assert abs(float(st2.phase2_high_chase_signals["159363.SZ"][0]["ref_price"]) - 0.5755) < 1e-9


def test_runner_pre_open_rescales_pending_only_etf_on_corporate_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.pending_entries.append(
        PendingEntry(
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
    )
    pf.phase2_high_chase_signals["159363.SZ"] = [{"signal_date": "20250715", "ref_price": 1.151}]
    sm.save(pf)

    now = _NowSeq([datetime(2025, 7, 21, 9, 25, 0)])
    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=0.595, limit_up=0.655, limit_down=0.535, price_tick=0.001)
    data = _FakeData(
        now=now.peek,
        instrument=inst,
        bars=_basic_bars(),
        divid_factors=[{"_index": "20250721", "dr": 1.985}],
    )
    trading = _FakeTrading()

    cfg = StrategyConfig(
        state_path="data/state/portfolio.json",
        watchlist_etf_codes=("159363.SZ",),
        tick_interval_s=0.0,
        exit_layer2_threshold=0.60,
    )
    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    runner._pre_open(now=now.peek())

    st2 = sm.load()
    assert st2.pending_entries[0].trial_qty == 25600
    assert st2.pending_entries[0].confirm_qty == 59800
    assert abs(float(st2.pending_entries[0].close_signal_day) - 0.5755) < 1e-9
    assert abs(float(st2.phase2_high_chase_signals["159363.SZ"][0]["ref_price"]) - 0.5755) < 1e-9
    assert "159363.SZ" not in st2.positions
    assert st2.corporate_action_markers["159363.SZ"] == "20250721"

    runner2 = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    runner2._pre_open(now=now.peek())

    st3 = sm.load()
    assert st3.pending_entries[0].trial_qty == 25600
    assert st3.pending_entries[0].confirm_qty == 59800
    assert abs(float(st3.pending_entries[0].close_signal_day) - 0.5755) < 1e-9
    assert abs(float(st3.phase2_high_chase_signals["159363.SZ"][0]["ref_price"]) - 0.5755) < 1e-9
    assert "159363.SZ" not in st3.positions


def test_runner_compute_stop_uses_runner_exit_config_not_latest_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sm1 = StateManager("data/state/portfolio1.json")
    sm2 = StateManager("data/state/portfolio2.json")
    base_state = PortfolioState(nav=100000.0, cash=100000.0)
    sm1.save(base_state)
    sm2.save(base_state)

    bars = [
        Bar(time=datetime(2026, 2, 20, 15, 0), open=1.0, high=1.01, low=0.99, close=1.0, volume=1.0, amount=1.0),
        Bar(time=datetime(2026, 2, 21, 15, 0), open=1.0, high=1.01, low=0.99, close=1.0, volume=1.0, amount=1.0),
        Bar(time=datetime(2026, 2, 22, 15, 0), open=1.0, high=1.01, low=0.99, close=1.0, volume=1.0, amount=1.0),
    ]
    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data1 = _FakeData(now=lambda: datetime(2026, 2, 23, 15, 1, 0), instrument=inst, bars=bars)
    data2 = _FakeData(now=lambda: datetime(2026, 2, 23, 15, 1, 0), instrument=inst, bars=bars)
    trading1 = _FakeTrading()
    trading2 = _FakeTrading()

    cfg1 = StrategyConfig(
        state_path="data/state/portfolio1.json",
        watchlist_etf_codes=("159363.SZ",),
        tick_interval_s=0.0,
        exit_atr_pct_min=0.02,
        exit_atr_pct_max=0.02,
        exit_k_accel_enabled=False,
    )
    cfg2 = StrategyConfig(
        state_path="data/state/portfolio2.json",
        watchlist_etf_codes=("159363.SZ",),
        tick_interval_s=0.0,
        exit_atr_pct_min=0.03,
        exit_atr_pct_max=0.03,
        exit_k_accel_enabled=True,
        exit_k_accel_step_pct=0.05,
        exit_k_accel_step_k=0.2,
        exit_k_accel_k_min=1.0,
    )

    runner1 = StrategyRunner(cfg1, data=data1, trading=trading1, state_manager=sm1, t0_engine=_FakeT0Engine())
    _ = StrategyRunner(cfg2, data=data2, trading=trading2, state_manager=sm2, t0_engine=_FakeT0Engine())

    ps = PositionState(
        etf_code="159363.SZ",
        state=FSMState.S2_BASE,
        base_qty=1000,
        total_qty=1000,
        avg_cost=1.0,
        highest_high=1.0,
    )
    stop, k, hh, atr = runner1._compute_stop(
        etf_code="159363.SZ",
        ps=ps,
        now=datetime(2026, 2, 23, 15, 1, 0),
        last_price=1.10,
    )

    assert abs(float(atr) - 0.02) < 1e-9
    assert abs(float(k) - 2.8) < 1e-9
    assert abs(float(hh) - 1.01) < 1e-9
    assert abs(float(stop) - 0.954) < 1e-9


def test_runner_compute_stop_uses_runner_k_snapshot_not_latest_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from exit.exit_config import get_exit_k_chip_decay, get_exit_k_normal, get_exit_k_reduced, set_exit_k

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    bars = [
        Bar(time=datetime(2026, 2, 20, 15, 0), open=1.0, high=1.01, low=0.99, close=1.0, volume=1.0, amount=1.0),
        Bar(time=datetime(2026, 2, 21, 15, 0), open=1.0, high=1.01, low=0.99, close=1.0, volume=1.0, amount=1.0),
        Bar(time=datetime(2026, 2, 22, 15, 0), open=1.0, high=1.01, low=0.99, close=1.0, volume=1.0, amount=1.0),
    ]
    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 15, 1, 0), instrument=inst, bars=bars)
    trading = _FakeTrading()
    cfg = StrategyConfig(
        state_path="data/state/portfolio.json",
        watchlist_etf_codes=("159363.SZ",),
        tick_interval_s=0.0,
        exit_atr_pct_min=0.02,
        exit_atr_pct_max=0.02,
        exit_k_accel_enabled=False,
    )

    original_k_normal = float(get_exit_k_normal())
    original_k_chip_decay = float(get_exit_k_chip_decay())
    original_k_reduced = float(get_exit_k_reduced())
    try:
        set_exit_k(k_normal=2.8, k_chip_decay=2.38, k_reduced=1.5)
        runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
        set_exit_k(k_normal=1.0, k_chip_decay=1.0, k_reduced=1.0)

        ps = PositionState(
            etf_code="159363.SZ",
            state=FSMState.S2_BASE,
            base_qty=1000,
            total_qty=1000,
            avg_cost=1.0,
            highest_high=1.0,
        )
        stop, k, hh, atr = runner._compute_stop(
            etf_code="159363.SZ",
            ps=ps,
            now=datetime(2026, 2, 23, 15, 1, 0),
            last_price=1.10,
        )
    finally:
        set_exit_k(
            k_normal=original_k_normal,
            k_chip_decay=original_k_chip_decay,
            k_reduced=original_k_reduced,
        )

    assert abs(float(atr) - 0.02) < 1e-9
    assert abs(float(k) - 2.8) < 1e-9
    assert abs(float(hh) - 1.01) < 1e-9
    assert abs(float(stop) - 0.954) < 1e-9

def test_runner_exit_fsm_uses_runner_exit_execution_snapshot_not_latest_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from exit.exit_config import (
        get_exit_layer1_sell_discount,
        get_exit_layer1_use_stop_price,
        get_exit_layer2_threshold,
        set_exit_layer1_order_pricing,
        set_exit_layer2_threshold,
    )

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["159363.SZ"] = PositionState(
        etf_code="159363.SZ",
        state=FSMState.S2_BASE,
        total_qty=1000,
        base_qty=1000,
        avg_cost=1.0,
    )
    sm.save(pf)

    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 10, 0, 0), instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    trading._positions["159363.SZ"] = {"total": 1000, "sellable": 1000}
    cfg = StrategyConfig(
        state_path="data/state/portfolio.json",
        watchlist_etf_codes=("159363.SZ",),
        tick_interval_s=0.0,
        exit_layer2_threshold=0.60,
    )

    original_sell_discount = float(get_exit_layer1_sell_discount())
    original_use_stop_price = bool(get_exit_layer1_use_stop_price())
    original_layer2_threshold = float(get_exit_layer2_threshold())
    try:
        set_exit_layer1_order_pricing(sell_discount=0.98, use_stop_price=False)
        runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

        set_exit_layer1_order_pricing(sell_discount=0.97, use_stop_price=True)
        set_exit_layer2_threshold(0.30)

        reduce_oid = runner._exit_fsm.apply_layer2_if_needed(
            now=datetime(2026, 2, 23, 10, 0, 0),
            etf_code="159363.SZ",
            score_soft=0.50,
            signals={"S_chip": 0.5},
        )
        assert reduce_oid is None
        assert trading.placed == []

        exit_oid = runner._exit_fsm.apply_layer1_checks(
            now=datetime(2026, 2, 23, 10, 0, 0),
            etf_code="159363.SZ",
            stop_price=1.01,
            score_soft=1.0,
            data_health={"L1": DataQuality.OK},
            days_held=0,
            current_return=-0.01,
            t0_realized_loss_pct=0.0,
        )
        assert exit_oid is not None
        assert len(trading.placed) == 1
        assert abs(float(trading.placed[0].price) - 0.979) < 1e-9
    finally:
        set_exit_layer1_order_pricing(
            sell_discount=original_sell_discount,
            use_stop_price=original_use_stop_price,
        )
        set_exit_layer2_threshold(original_layer2_threshold)

def test_runner_exit_fsm_uses_runner_lifeboat_buyback_pricing_snapshot_not_latest_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.buy_order_config import (
        get_aggressive_buy_multiplier,
        get_aggressive_buy_use_ask1,
        set_aggressive_buy_pricing,
    )

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.positions["159363.SZ"] = PositionState(
        etf_code="159363.SZ",
        state=FSMState.S2_BASE,
        total_qty=3000,
        base_qty=3000,
        avg_cost=1.0,
        lifeboat_used=False,
        lifeboat_sell_time="2026-02-23T09:30:00",
        lifeboat_tight_stop=0.89,
    )
    sm.save(pf)

    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 10, 30, 0), instrument=inst, bars=_basic_bars(), price_fn=lambda _dt, _code: 0.925)
    trading = _FakeTrading()
    trading._positions["159363.SZ"] = {"total": 3000, "sellable": 3000}
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("159363.SZ",), tick_interval_s=0.0)

    original_multiplier = float(get_aggressive_buy_multiplier())
    original_use_ask1 = bool(get_aggressive_buy_use_ask1())
    try:
        set_aggressive_buy_pricing(multiplier=1.003, use_ask1=False)
        runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

        set_aggressive_buy_pricing(multiplier=1.010, use_ask1=True)

        oid = runner._exit_fsm.apply_lifeboat_buyback_check(
            now=datetime(2026, 2, 23, 10, 30, 0),
            etf_code="159363.SZ",
            stop_price=0.90,
            score_soft=0.0,
            data_health={"L1": DataQuality.OK},
        )
        assert oid is not None
        assert len(trading.placed) == 1
        assert abs(float(trading.placed[0].price) - 0.929) < 1e-9
    finally:
        set_aggressive_buy_pricing(multiplier=original_multiplier, use_ask1=original_use_ask1)


def test_runner_pending_entry_phase3_uses_runner_entry_snapshot_not_latest_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.buy_order_config import (
        get_aggressive_buy_multiplier,
        get_aggressive_buy_use_ask1,
        set_aggressive_buy_pricing,
    )
    from entry.pathb_config import (
        get_pathb_atr_mult,
        get_pathb_chip_min,
        get_pathb_require_trend,
        get_pathb_require_vwap_strict,
        set_pathb_atr_mult,
        set_pathb_chip_min,
        set_pathb_require_trend,
        set_pathb_require_vwap_strict,
    )

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    pf = PortfolioState(nav=100000.0, cash=100000.0)
    pf.pending_entries.append(
        PendingEntry(
            etf_code="159363.SZ",
            signal_date="20260223",
            score_entry=0.64,
            phase="phase3",
            h_signal=1.0,
            l_signal=0.98,
            close_signal_day=0.99,
            atr_20=0.04,
            is_strong=False,
            expire_date="20260228",
            status="PENDING_TRIAL",
            signals={"S_trend": 0.0, "S_chip_pr": 0.6},
        )
    )
    sm.save(pf)

    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 9, 40, 0), instrument=inst, bars=_basic_bars(), price_fn=lambda _dt, _code: 0.99, iopv=None)
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("159363.SZ",), tick_interval_s=0.0)

    original_multiplier = float(get_aggressive_buy_multiplier())
    original_use_ask1 = bool(get_aggressive_buy_use_ask1())
    original_pathb_mult = float(get_pathb_atr_mult())
    original_pathb_chip_min = float(get_pathb_chip_min())
    original_pathb_require_trend = bool(get_pathb_require_trend())
    original_pathb_require_vwap_strict = bool(get_pathb_require_vwap_strict())
    try:
        set_aggressive_buy_pricing(multiplier=1.003, use_ask1=False)
        set_pathb_atr_mult(0.5)
        set_pathb_chip_min(0.5)
        set_pathb_require_trend(False)
        set_pathb_require_vwap_strict(False)
        runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

        set_aggressive_buy_pricing(multiplier=1.010, use_ask1=True)
        set_pathb_atr_mult(0.5)
        set_pathb_chip_min(0.95)
        set_pathb_require_trend(True)
        set_pathb_require_vwap_strict(True)

        pe = runner._state.pending_entries[0]
        runner._process_pending_entry(now=datetime(2026, 2, 23, 9, 40, 0), pe=pe)

        assert len(trading.placed) == 1
        assert abs(float(trading.placed[0].price) - 0.994) < 1e-9
        assert str(runner._state.pending_entries[0].status) == "TRIAL_PLACED"
    finally:
        set_aggressive_buy_pricing(multiplier=original_multiplier, use_ask1=original_use_ask1)
        set_pathb_atr_mult(original_pathb_mult)
        set_pathb_chip_min(original_pathb_chip_min)
        set_pathb_require_trend(original_pathb_require_trend)
        set_pathb_require_vwap_strict(original_pathb_require_vwap_strict)


def test_runner_post_close_passes_entry_snapshot_not_latest_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import strategy_runner as strategy_runner_module

    from entry.phase2 import Phase2Result
    from entry.phase2_config import (
        get_phase2_continuation_config,
        get_phase2_score_threshold,
        set_phase2_continuation_config,
        set_phase2_score_threshold,
    )
    from entry.types import WatchlistItem

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    inst = InstrumentInfo(etf_code="159363.SZ", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 23, 15, 5, 0), instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("159363.SZ",), tick_interval_s=0.0)

    original_score_threshold = float(get_phase2_score_threshold())
    original_continuation_cfg = dict(get_phase2_continuation_config())
    captured: dict[str, Any] = {}

    def fake_evaluate_phase2(
        *,
        etf_code: str,
        bars: list[Bar],
        watch,
        signal_date,
        s_micro_missing=None,
        score_threshold=None,
        continuation_cfg=None,
    ) -> Phase2Result:
        captured["etf_code"] = str(etf_code)
        captured["score_threshold"] = score_threshold
        captured["continuation_cfg"] = continuation_cfg
        return Phase2Result(
            score=0.0,
            is_triggered=False,
            is_strong=False,
            signals={},
            signal_fired=None,
            h_signal=0.0,
            l_signal=0.0,
            close_signal_day=0.0,
            note="",
        )

    try:
        set_phase2_score_threshold(0.55)
        set_phase2_continuation_config(enabled=True, chip_min=0.61, micro_min=0.41, lookback=11, expire_days=2, min_close_breakout_pct=0.01)
        runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

        set_phase2_score_threshold(0.95)
        set_phase2_continuation_config(enabled=False, chip_min=0.99, micro_min=0.99, lookback=3, expire_days=1, min_close_breakout_pct=0.50)

        monkeypatch.setattr(strategy_runner_module, "evaluate_phase2", fake_evaluate_phase2)
        monkeypatch.setattr(
            runner,
            "_build_watchlist",
            lambda now: [WatchlistItem(etf_code="159363.SZ", sentiment_score=70, profit_ratio=82.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=1.6)],
        )

        runner._post_close(now=datetime(2026, 2, 23, 15, 5, 0))

        assert captured["etf_code"] == "159363.SZ"
        assert captured["score_threshold"] == 0.55
        assert captured["continuation_cfg"] is not None
        assert captured["continuation_cfg"]["enabled"] is True
        assert captured["continuation_cfg"]["lookback"] == 11
        assert captured["continuation_cfg"]["expire_days"] == 2
        assert abs(float(captured["continuation_cfg"]["chip_min"]) - 0.61) < 1e-9
    finally:
        set_phase2_score_threshold(original_score_threshold)
        set_phase2_continuation_config(**original_continuation_cfg)


def test_runner_pre_open_injects_sentiment_proxy_into_watchlist_and_ext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backtest.sentiment_proxy import compute_sentiment_proxy
    from entry.types import WatchlistItem

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    bars = [
        Bar(time=datetime(2026, 2, 18, 15, 0), open=1.00, high=1.01, low=0.99, close=1.00, volume=100000.0, amount=100000.0),
        Bar(time=datetime(2026, 2, 19, 15, 0), open=1.00, high=1.02, low=1.00, close=1.01, volume=110000.0, amount=111100.0),
        Bar(time=datetime(2026, 2, 20, 15, 0), open=1.01, high=1.03, low=1.01, close=1.02, volume=120000.0, amount=122400.0),
        Bar(time=datetime(2026, 2, 21, 15, 0), open=1.02, high=1.04, low=1.02, close=1.03, volume=130000.0, amount=133900.0),
        Bar(time=datetime(2026, 2, 22, 15, 0), open=1.03, high=1.05, low=1.03, close=1.04, volume=140000.0, amount=145600.0),
        Bar(time=datetime(2026, 2, 23, 15, 0), open=1.04, high=1.06, low=1.04, close=1.05, volume=150000.0, amount=157500.0),
    ]
    expected_score100, expected_score01 = compute_sentiment_proxy(bars)
    inst = InstrumentInfo(etf_code="512480.SH", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 24, 9, 20, 0), instrument=inst, bars=bars)
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480.SH",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    runner._ext_factors["512480.SH"] = {"sentiment_score_01": 0.10, "profit_ratio": 82.0}
    monkeypatch.setattr(
        runner,
        "_build_watchlist",
        lambda now: [
            WatchlistItem(
                etf_code="512480.SH",
                sentiment_score=10,
                profit_ratio=82.0,
                extra={"sentiment_score_01": 0.10},
            )
        ],
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        runner._entry_fsm,
        "upsert_watchlist",
        lambda d, watchlist: captured.setdefault("watchlist", list(watchlist)),
    )

    runner._pre_open(now=datetime(2026, 2, 24, 9, 20, 0))

    watchlist = captured["watchlist"]
    assert len(watchlist) == 1
    assert int(watchlist[0].sentiment_score) == int(expected_score100)
    assert abs(float(watchlist[0].extra["sentiment_score_01"]) - float(expected_score01)) < 1e-9
    assert abs(float(runner._ext_factors["512480.SH"]["sentiment_score_01"]) - float(expected_score01)) < 1e-9


def test_runner_resolve_watch_codes_watch_auto_unions_default_universe_and_hot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.premarket_prep as premarket_prep
    from backtest.universe import DEFAULT_UNIVERSE_CODES

    monkeypatch.chdir(tmp_path)
    sm = StateManager("data/state/portfolio.json")
    sm.save(PortfolioState(nav=100000.0, cash=100000.0))

    hot_csv = tmp_path / "finintel_signal_hot_20260223.csv"
    hot_csv.write_text("code\n159107.SZ\n512480.SH\n", encoding="utf-8")

    inst = InstrumentInfo(etf_code="512480.SH", instrument_name="x", prev_close=1.0, limit_up=1.1, limit_down=0.9, price_tick=0.001)
    data = _FakeData(now=lambda: datetime(2026, 2, 24, 9, 20, 0), instrument=inst, bars=_basic_bars())
    trading = _FakeTrading()
    cfg = StrategyConfig(
        state_path="data/state/portfolio.json",
        watchlist_etf_codes=(),
        tick_interval_s=0.0,
        watch_auto=True,
        watch_auto_no_filter=True,
    )
    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())

    monkeypatch.setattr(premarket_prep, "prev_trading_date", lambda now: "20260223")
    monkeypatch.setattr(premarket_prep, "finintel_hot_csv_path", lambda **kwargs: hot_csv)

    codes = runner._resolve_watch_codes(now=datetime(2026, 2, 24, 9, 20, 0))

    assert "159107.SZ" in codes
    assert "512480.SH" in codes
    assert "159825.SZ" in codes
    assert len(codes) == len(set(codes))
    assert len(codes) == len(DEFAULT_UNIVERSE_CODES) + 1
