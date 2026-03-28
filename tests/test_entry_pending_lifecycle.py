from __future__ import annotations

import logging

import pytest
from datetime import date, datetime

from backtest.runner import BacktestStrategyRunner
from backtest.state_manager import InMemoryStateManager
from core.cash_manager import CashManager
from core.enums import FSMState, OrderStatus
from core.interfaces import OrderResult
from core.models import PendingEntry, PortfolioState, PositionState
from entry.entry_fsm import EntryFSM
import strategy_runner as strategy_runner_module
from strategy_config import StrategyConfig
from strategy_runner import StrategyRunner
from entry.types import SignalFired, WatchlistItem


class _DummyTrading:
    def place_order(self, req):
        raise AssertionError("place_order should not be called in this test")

    def cancel_order(self, order_id: int) -> bool:
        _ = order_id
        return False

    def query_positions(self) -> list[object]:
        return []

    def query_orders(self) -> list[object]:
        return []

    def query_asset(self) -> dict[str, object]:
        return {}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        _ = timeout_s
        return OrderResult(order_id=int(order_id), status=OrderStatus.FILLED, filled_qty=47300, avg_price=0.738)


class _DummyPositionFsm:
    def __init__(self) -> None:
        self.confirm_calls: list[tuple[str, int, float]] = []
        self.failed_calls: list[str] = []

    def on_confirm_filled(self, etf_code: str, qty: int, price: float) -> None:
        self.confirm_calls.append((str(etf_code), int(qty), float(price)))

    def on_trial_filled(self, etf_code: str, qty: int, price: float) -> None:
        raise AssertionError("trial fill path should not be used in this test")

    def on_entry_failed(self, etf_code: str) -> None:
        self.failed_calls.append(str(etf_code))


def _make_signal(*, code: str = "159755.SZ", signal_day: date = date(2025, 10, 29)) -> SignalFired:
    watch = WatchlistItem(
        etf_code=code,
        sentiment_score=100,
        profit_ratio=88.0,
        micro_caution=False,
        vpin_rank=0.4,
        ofi_daily=1.2,
        vs_max=1.1,
    )
    return SignalFired(
        etf_code=code,
        score=0.53,
        is_strong=False,
        h_signal=1.108,
        l_signal=1.058,
        close_signal_day=1.108,
        atr_20=0.02125,
        signal_date=signal_day,
        expire_date=signal_day,
        signals={"S_volume": 1.0, "S_chip_pr": 0.3, "S_trend": 1.0, "S_micro": 0.7},
        watchlist=watch,
    )


def _make_pending_entry(*, code: str = "159755.SZ", status: str, signal_date_s: str = "20250722") -> PendingEntry:
    return PendingEntry(
        etf_code=code,
        signal_date=signal_date_s,
        score_entry=0.45,
        phase="phase3",
        h_signal=0.777,
        l_signal=0.761,
        close_signal_day=0.769,
        atr_20=0.0147,
        is_strong=False,
        expire_date="20250821",
        status=status,
        confirm_qty=47300,
        confirm_price=0.738,
        confirm_order_id=153,
        sentiment_score=0,
        profit_ratio=97.27,
        micro_caution=True,
        vpin_rank=0.84,
        ofi_daily=0.37,
        vs_max=1.84,
        signals={"S_chip_pr": 1.0, "S_trend": 1.0, "S_micro": 1.0},
    )


def test_entry_fsm_add_pending_entry_replaces_same_code_confirm_filled() -> None:
    state = PortfolioState(pending_entries=[_make_pending_entry(status="CONFIRM_FILLED", signal_date_s="20250722")])
    sm = InMemoryStateManager(initial_state=state)
    fsm = EntryFSM(state_manager=sm, data=object(), trading=_DummyTrading(), state=state)

    fsm.add_pending_entry(fired=_make_signal(signal_day=date(2025, 10, 29)))

    same_code = [pe for pe in state.pending_entries if pe.etf_code == "159755.SZ"]
    assert len(same_code) == 1
    assert same_code[0].signal_date == "20251029"
    assert same_code[0].status == "PENDING_TRIAL"


def test_backtest_confirm_fill_removes_pending_entry_after_confirm() -> None:
    state = PortfolioState(cash=100000.0, nav=100000.0, hwm=100000.0)
    pe = _make_pending_entry(status="CONFIRM_PLACED")
    state.pending_entries.append(pe)
    sm = InMemoryStateManager(initial_state=state)
    entry_fsm = EntryFSM(state_manager=sm, data=object(), trading=_DummyTrading(), state=state)
    pos_fsm = _DummyPositionFsm()

    runner = BacktestStrategyRunner.__new__(BacktestStrategyRunner)
    runner._trading = _DummyTrading()
    runner._state = state
    runner._sm = sm
    runner._entry_fsm = entry_fsm
    runner._pos_fsm = pos_fsm
    runner._bt_fee_rate = 0.0
    runner._logger = logging.getLogger("tests.pending.lifecycle.strategy")
    runner._bt_logger = logging.getLogger("tests.pending.lifecycle.backtest")
    runner._sync_state_cash_from_trader = lambda: None

    runner._confirm_entry_order(
        now=datetime(2025, 7, 22, 9, 43, 24),
        pe=pe,
        order_id=153,
        is_trial=False,
        cash_manager=CashManager(state),
    )

    assert state.pending_entries == []
    assert pos_fsm.confirm_calls == [("159755.SZ", 47300, 0.738)]
    assert pos_fsm.failed_calls == []



def test_backtest_blocked_continuation_still_seeds_high_chase_memory() -> None:
    runner = BacktestStrategyRunner.__new__(BacktestStrategyRunner)
    runner._bt_skip_high_chase_after_first_signal = True
    runner._bt_high_chase_signal_source = "all_signals"
    runner._bt_high_chase_signals = {}
    runner._bt_high_chase_lookback_days = 60
    runner._bt_high_chase_max_rise = 0.15
    runner._bt_logger = logging.getLogger("tests.pending.lifecycle.high_chase_seed")

    added = runner._remember_bt_blocked_continuation_signal(
        now=datetime(2024, 9, 30, 15, 1, 0),
        etf_code="159811.SZ",
        close_signal_day=0.708,
        h_signal=0.720,
        note="continuation_blocked mature_leg>=5",
    )

    assert added is True
    assert runner._bt_high_chase_signals == {"159811.SZ": [(date(2024, 9, 30), 0.708)]}


def test_live_phase2_entry_gate_blocks_reentry_after_confirm() -> None:
    runner = StrategyRunner.__new__(StrategyRunner)
    runner._cfg = StrategyConfig()
    runner._state = PortfolioState(
        positions={
            "159755.SZ": PositionState(
                etf_code="159755.SZ",
                state=FSMState.S2_BASE,
                base_qty=1000,
                total_qty=1000,
            )
        }
    )
    runner._logger = logging.getLogger("tests.pending.lifecycle.live.phase2_gate")

    blocked, reason = runner._should_block_phase2_entry_after_signal(
        now=datetime(2025, 7, 22, 15, 1, 0),
        etf_code="159755.SZ",
    )

    assert blocked is True
    assert reason == "no_reentry_after_confirm"


def test_live_blocked_continuation_still_seeds_persisted_high_chase_memory() -> None:
    runner = StrategyRunner.__new__(StrategyRunner)
    runner._cfg = StrategyConfig()
    runner._state = PortfolioState()
    runner._logger = logging.getLogger("tests.pending.lifecycle.live.high_chase_seed")

    added = runner._remember_phase2_blocked_continuation_signal(
        now=datetime(2024, 9, 30, 15, 1, 0),
        etf_code="159811.SZ",
        close_signal_day=0.708,
        h_signal=0.720,
        note="continuation_blocked mature_leg>=5",
    )

    restored = PortfolioState.from_dict(runner._state.to_dict())

    assert added is True
    assert restored.phase2_high_chase_signals == {
        "159811.SZ": [{"signal_date": "20240930", "ref_price": 0.708}]
    }


def test_live_high_chase_memory_persists_and_blocks_followup_signal() -> None:
    runner = StrategyRunner.__new__(StrategyRunner)
    runner._cfg = StrategyConfig()
    runner._state = PortfolioState(
        phase2_high_chase_signals={
            "159811.SZ": [{"signal_date": "20240930", "ref_price": 0.708}]
        }
    )
    runner._logger = logging.getLogger("tests.pending.lifecycle.live.high_chase_block")

    should_block, reason = runner._should_block_phase2_high_chase_signal(
        now=datetime(2024, 10, 8, 15, 1, 0),
        etf_code="159811.SZ",
        ref_price=0.815,
    )

    assert should_block is True
    assert "first_price=0.708000" in reason
    assert "threshold=0.1500" in reason


class _AssertingReducedPositionFsm(_DummyPositionFsm):
    def __init__(self, state: PortfolioState, *, fail_on: str) -> None:
        super().__init__()
        self._state = state
        self._fail_on = str(fail_on)

    def upsert_position(self, etf_code: str) -> PositionState:
        code = str(etf_code)
        ps = self._state.positions.get(code)
        if ps is None:
            ps = PositionState(etf_code=code)
            self._state.positions[code] = ps
        return ps

    def on_confirm_filled(self, etf_code: str, qty: int, price: float) -> None:
        if self._fail_on == "confirm":
            raise AssertionError("illegal transition from reduced state")
        super().on_confirm_filled(etf_code, qty, price)

    def on_trial_filled(self, etf_code: str, qty: int, price: float) -> None:
        if self._fail_on == "trial":
            raise AssertionError("illegal transition from reduced state")
        super().on_trial_filled(etf_code, qty, price)


def test_live_confirm_fill_fallback_removes_pending_entry_after_reduced_state_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    state = PortfolioState(cash=100000.0, nav=100000.0, hwm=100000.0)
    pe = _make_pending_entry(status="CONFIRM_PLACED")
    state.pending_entries.append(pe)
    state.positions["159755.SZ"] = PositionState(
        etf_code="159755.SZ",
        state=FSMState.S5_REDUCED,
        base_qty=6900,
        total_qty=6900,
        avg_cost=0.996475,
        scale_count=2,
        entry_date="2025-09-08",
    )
    sm = InMemoryStateManager(initial_state=state)
    entry_fsm = EntryFSM(state_manager=sm, data=object(), trading=_DummyTrading(), state=state)
    pos_fsm = _AssertingReducedPositionFsm(state, fail_on="confirm")

    runner = StrategyRunner.__new__(StrategyRunner)
    runner._trading = _DummyTrading()
    runner._state = state
    runner._sm = sm
    runner._entry_fsm = entry_fsm
    runner._pos_fsm = pos_fsm
    runner._logger = logging.getLogger("tests.pending.lifecycle.live")

    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        strategy_runner_module,
        "alert_once",
        lambda key, message: alerts.append((str(key), str(message))),
    )

    runner._confirm_entry_order(
        now=datetime(2025, 9, 25, 9, 30, 39),
        pe=pe,
        order_id=153,
        is_trial=False,
        cash_manager=CashManager(state),
    )

    ps = state.positions["159755.SZ"]
    assert state.pending_entries == []
    assert ps.state == FSMState.S4_FULL
    assert ps.total_qty == 54200
    assert ps.base_qty == 54200
    assert ps.scale_count == 2
    assert len(alerts) == 1
    assert alerts[0][0] == "live_confirm_fill_fallback_alert:159755.SZ:20250925:confirm_filled_assert"
    assert "Live confirm fill fallback triggered." in alerts[0][1]
    assert "etf=159755.SZ" in alerts[0][1]


def test_live_trial_fill_fallback_keeps_pending_entry_after_reduced_state_conflict() -> None:
    state = PortfolioState(cash=100000.0, nav=100000.0, hwm=100000.0)
    pe = _make_pending_entry(status="TRIAL_PLACED")
    pe.trial_qty = 47300
    pe.trial_price = 0.738
    pe.trial_order_id = 153
    pe.confirm_qty = 0
    pe.confirm_price = None
    pe.confirm_order_id = None
    state.pending_entries.append(pe)
    state.positions["159755.SZ"] = PositionState(
        etf_code="159755.SZ",
        state=FSMState.S5_REDUCED,
        base_qty=6900,
        total_qty=6900,
        avg_cost=0.996475,
        scale_count=2,
        entry_date="2025-09-08",
    )
    sm = InMemoryStateManager(initial_state=state)
    entry_fsm = EntryFSM(state_manager=sm, data=object(), trading=_DummyTrading(), state=state)
    pos_fsm = _AssertingReducedPositionFsm(state, fail_on="trial")

    runner = StrategyRunner.__new__(StrategyRunner)
    runner._trading = _DummyTrading()
    runner._state = state
    runner._sm = sm
    runner._entry_fsm = entry_fsm
    runner._pos_fsm = pos_fsm
    runner._logger = logging.getLogger("tests.pending.lifecycle.live")

    runner._confirm_entry_order(
        now=datetime(2025, 9, 25, 9, 30, 36),
        pe=pe,
        order_id=153,
        is_trial=True,
        cash_manager=CashManager(state),
    )

    ps = state.positions["159755.SZ"]
    assert state.pending_entries == [pe]
    assert pe.status == "PENDING_CONFIRM"
    assert ps.state == FSMState.S4_FULL
    assert ps.total_qty == 54200
    assert ps.base_qty == 54200
    assert ps.scale_count == 2

def test_live_phase2_entry_gate_ignores_zero_qty_confirm_state() -> None:
    runner = StrategyRunner.__new__(StrategyRunner)
    runner._cfg = StrategyConfig()
    runner._state = PortfolioState(
        positions={
            "159755.SZ": PositionState(
                etf_code="159755.SZ",
                state=FSMState.S2_BASE,
                base_qty=0,
                total_qty=0,
            )
        }
    )
    runner._logger = logging.getLogger("tests.pending.lifecycle.live.phase2_gate.zero_qty")

    blocked, reason = runner._should_block_phase2_entry_after_signal(
        now=datetime(2025, 7, 22, 15, 1, 0),
        etf_code="159755.SZ",
    )

    assert blocked is False
    assert reason == ""
