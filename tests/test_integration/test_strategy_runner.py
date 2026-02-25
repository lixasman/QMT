from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

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
    ) -> None:
        self._now = now
        self._inst = instrument
        self._bars = list(bars)
        self._price_fn = price_fn
        self._quality = quality
        self._iopv = iopv
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
            out.append({"stock_code": code, "total_amount": int(p["total"]), "can_use_volume": int(p["sellable"])})
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
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

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

    now = _NowSeq(
        [
            datetime(2026, 2, 23, 9, 20, 0),
            datetime(2026, 2, 23, 9, 20, 0),
            datetime(2026, 2, 23, 9, 31, 0),
            datetime(2026, 2, 23, 9, 32, 0),
            datetime(2026, 2, 23, 13, 0, 0),
            datetime(2026, 2, 23, 15, 5, 0),
        ]
    )

    def price_fn(dt: datetime, code: str) -> float:
        _ = code
        if dt.time() == datetime(2026, 2, 23, 13, 0, 0).time():
            return 0.8
        return 1.0

    inst = InstrumentInfo(etf_code="512480", instrument_name="x", prev_close=2.0, limit_up=2.2, limit_down=1.8)
    data = _FakeData(now=now.peek, instrument=inst, bars=_basic_bars(), price_fn=price_fn)
    trading = _FakeTrading()
    cfg = StrategyConfig(state_path="data/state/portfolio.json", watchlist_etf_codes=("512480",), tick_interval_s=0.0)

    runner = StrategyRunner(cfg, data=data, trading=trading, state_manager=sm, t0_engine=_FakeT0Engine())
    runner.run_day(wait_for_market=False, max_ticks=3, now_provider=now, sleep_fn=lambda _: None)

    st2 = sm.load()
    ps = st2.positions.get("512480")
    assert ps is not None
    assert ps.state == FSMState.S0_IDLE
    assert int(ps.total_qty) == 0


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
