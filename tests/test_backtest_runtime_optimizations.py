from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

import pytest

from backtest.adapters import BacktestDataAdapter, BacktestTradingAdapter
from backtest.clock import SimulatedClock
from backtest.runner import BacktestStrategyRunner
from backtest.state_manager import InMemoryStateManager
from core.enums import OrderSide, OrderStatus, OrderType
from core.interfaces import OrderRequest
from backtest.store import MarketDataStore, _TickDayCache, _TickPoint
from core.models import PortfolioState
from strategy_config import StrategyConfig


def _write_daily_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        [datetime(2025, 3, 5, 15, 0).timestamp() * 1000.0, 1.0, 1.02, 0.99, 1.01, 1_000_000.0, 1_010_000.0],
        [datetime(2025, 3, 6, 15, 0).timestamp() * 1000.0, 1.01, 1.03, 1.0, 1.02, 1_100_000.0, 1_122_000.0],
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume", "amount"])
        w.writerows(rows)


def _write_tick_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ["2025-03-06 09:30:00", 1.23, 100, 123.0, 1.229, 10, 1.231, 12, "", 0],
        ["2025-03-06 09:35:00", 1.25, 200, 250.0, 1.249, 11, 1.251, 13, "", 0],
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "lastprice", "volume", "amount", "bidprice1", "bidvol1", "askprice1", "askvol1", "iopv", "stockstatus"])
        w.writerows(rows)


def _write_xt_like_tick_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ["512480", "样例ETF", "20250306093000", "1.230", "1.234", "1.235", "1.229", "1000", "1234.0", "100", "123.4", "300", "1.238", "250", "1.237", "200", "1.236", "150", "1.235", "120", "1.234", "110", "1.233", "90", "1.232", "80", "1.231", "70", "1.230", "60", "1.229", "b"],
        ["512480", "样例ETF", "20250306093003", "1.230", "1.236", "1.236", "1.230", "1200", "1480.0", "200", "245.6", "320", "1.239", "260", "1.238", "210", "1.237", "160", "1.236", "130", "1.235", "120", "1.234", "95", "1.233", "85", "1.232", "75", "1.231", "65", "1.230", "e"],
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "code", "display_name", "time", "open", "current", "high", "low", "total_volume", "total_money", "volume", "money",
            "a5_v", "a5_p", "a4_v", "a4_p", "a3_v", "a3_p", "a2_v", "a2_p", "a1_v", "a1_p",
            "b1_v", "b1_p", "b2_v", "b2_p", "b3_v", "b3_p", "b4_v", "b4_p", "b5_v", "b5_p", "b/s",
        ])
        w.writerows(rows)


def test_market_store_negative_caches_missing_tick_day_file(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    tick_root = tmp_path / "ticks"
    _write_daily_csv(data_root / "1d" / "512480_SH.csv")
    (tick_root / "20250306").mkdir(parents=True, exist_ok=True)

    store = MarketDataStore(data_root=data_root, codes=["512480.SH"], tick_root=tick_root, load_minute=False)

    calls = {"n": 0}
    orig = store._load_tick_day_cache

    def wrapped(*, code: str, day) -> object:
        calls["n"] += 1
        return orig(code=code, day=day)

    store._load_tick_day_cache = wrapped  # type: ignore[method-assign]

    now = datetime(2025, 3, 6, 9, 35, 0)
    assert store.tick_snapshot(code="512480.SH", now=now) is None
    assert store.tick_snapshot(code="512480.SH", now=now) is None
    assert calls["n"] == 1


def test_market_store_reads_nested_year_month_day_tick_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    tick_root = tmp_path / "ticks"
    _write_daily_csv(data_root / "1d" / "512480_SH.csv")
    _write_tick_csv(tick_root / "2025" / "03" / "2025-03-06" / "512480.csv")

    store = MarketDataStore(data_root=data_root, codes=["512480.SH"], tick_root=tick_root, load_minute=False)

    snap = store.tick_snapshot(code="512480.SH", now=datetime(2025, 3, 6, 9, 35, 0))

    assert snap is not None
    assert float(snap[0].last_price) == pytest.approx(1.25)


def test_market_store_parses_xt_like_tick_columns(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    tick_root = tmp_path / "ticks"
    _write_daily_csv(data_root / "1d" / "512480_SH.csv")
    _write_xt_like_tick_csv(tick_root / "2025" / "03" / "2025-03-06" / "512480.csv")

    store = MarketDataStore(data_root=data_root, codes=["512480.SH"], tick_root=tick_root, load_minute=False)

    snap = store.tick_snapshot(code="512480.SH", now=datetime(2025, 3, 6, 9, 30, 3))

    assert snap is not None
    assert float(snap[0].last_price) == pytest.approx(1.236)
    assert float(snap[0].ask1_price) == pytest.approx(1.235)
    assert float(snap[0].bid1_price) == pytest.approx(1.234)


def test_backtest_adapter_snapshot_fallback_avoids_second_tick_lookup() -> None:
    class _Store:
        def __init__(self) -> None:
            self.tick_calls = 0
            self.mark_price_prefer_tick: list[bool] = []

        def tick_snapshot(self, *, code: str, now: datetime):
            _ = code
            _ = now
            self.tick_calls += 1
            return None

        def mark_price(self, *, code: str, now: datetime, prefer_tick: bool = True) -> float:
            _ = code
            _ = now
            self.mark_price_prefer_tick.append(bool(prefer_tick))
            if prefer_tick:
                _ = self.tick_snapshot(code=code, now=now)
            return 1.23

    store = _Store()
    clock = SimulatedClock(datetime(2025, 3, 6, 10, 0, 0))
    adapter = BacktestDataAdapter(store=store, clock=clock)  # type: ignore[arg-type]

    snap = adapter.get_snapshot("512480.SH")

    assert float(snap.last_price) == pytest.approx(1.23)
    assert store.tick_calls == 1


def test_inmemory_state_manager_save_defers_rehydrate_until_load(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PortfolioState.from_dict.__func__
    calls = {"n": 0}

    def _spy(cls, payload):
        calls["n"] += 1
        return original(cls, payload)

    monkeypatch.setattr(PortfolioState, "from_dict", classmethod(_spy))

    sm = InMemoryStateManager(initial_state=PortfolioState(cash=1.0, nav=1.0, hwm=1.0))
    init_calls = int(calls["n"])

    st = PortfolioState(cash=123.0, nav=123.0, hwm=123.0)
    sm.save(st)
    st.cash = 999.0

    loaded = sm.load()

    assert int(calls["n"]) == int(init_calls) + 1
    assert float(loaded.cash) == pytest.approx(123.0)


def test_backtest_trading_query_positions_reuses_same_tick_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass
    class _LotLike:
        qty: int
        sellable_date: date

    clock = SimulatedClock(datetime(2026, 3, 6, 10, 0, 0))
    trading = BacktestTradingAdapter(clock=clock, initial_cash=100000.0, fee_rate=0.0, enable_t0=False)
    trading._lots = {
        "159997.SZ": [_LotLike(qty=200, sellable_date=date(2026, 3, 6))],
        "512480.SH": [_LotLike(qty=100, sellable_date=date(2026, 3, 6)), _LotLike(qty=300, sellable_date=date(2026, 3, 7))],
    }

    counts = {"total": 0, "sellable": 0}
    orig_total = trading._total_qty
    orig_sellable = trading._sellable_qty

    def _count_total(code: str) -> int:
        counts["total"] += 1
        return orig_total(code)

    def _count_sellable(code: str) -> int:
        counts["sellable"] += 1
        return orig_sellable(code)

    monkeypatch.setattr(trading, "_total_qty", _count_total)
    monkeypatch.setattr(trading, "_sellable_qty", _count_sellable)

    pos1 = trading.query_positions()
    pos2 = trading.query_positions()

    assert pos1 == pos2
    assert counts == {"total": 2, "sellable": 2}


def test_backtest_trading_skip_freeze_for_insufficient_cash_reason() -> None:
    clock = SimulatedClock(datetime(2026, 3, 6, 10, 0, 0))
    trading = BacktestTradingAdapter(clock=clock, initial_cash=1000.0, fee_rate=0.0, enable_t0=False)

    too_large = OrderRequest(etf_code="512480.SH", side=OrderSide.BUY, quantity=2000, order_type=OrderType.LIMIT, price=1.0)
    rejected = trading.place_order(too_large)

    assert rejected.status == OrderStatus.REJECTED
    assert rejected.error == "insufficient cash"

    trading.enter_freeze_mode(rejected.error)

    ok = trading.place_order(OrderRequest(etf_code="512480.SH", side=OrderSide.BUY, quantity=100, order_type=OrderType.LIMIT, price=1.0))

    assert ok.status == OrderStatus.SUBMITTED
    assert int(ok.order_id) > 0


def test_backtest_runner_intraday_loop_fast_forwards_idle_day_without_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    data_root = tmp_path / "data"
    tick_root = tmp_path / "ticks"
    _write_daily_csv(data_root / "1d" / "512480_SH.csv")
    tick_root.mkdir(parents=True, exist_ok=True)

    clock = SimulatedClock(datetime(2025, 3, 6, 9, 25, 0))
    store = MarketDataStore(data_root=data_root, codes=["512480.SH"], tick_root=tick_root, load_minute=False)
    data = BacktestDataAdapter(store=store, clock=clock)
    trading = BacktestTradingAdapter(clock=clock, initial_cash=100000.0, fee_rate=0.0, enable_t0=False)
    state_manager = InMemoryStateManager(initial_state=PortfolioState(nav=100000.0, cash=100000.0))
    cfg = StrategyConfig(
        state_path=str(tmp_path / "state" / "portfolio.json"),
        entry_log_path=str(tmp_path / "logs" / "entry.jsonl"),
        exit_log_path=str(tmp_path / "logs" / "exit.jsonl"),
        position_log_path=str(tmp_path / "logs" / "position.jsonl"),
        t0_log_path=str(tmp_path / "logs" / "t0.jsonl"),
        watchlist_etf_codes=("512480.SH",),
        tick_interval_s=3.0,
        watch_auto=False,
    )
    runner = BacktestStrategyRunner(
        config=cfg,
        data=data,
        trading=trading,
        state_manager=state_manager,
        fee_rate=0.0,
        disable_t0_ops=True,
    )

    counts = {"heartbeat": 0, "tick_cycle": 0}

    def _count_heartbeat(*, now: datetime) -> None:
        _ = now
        counts["heartbeat"] += 1

    def _count_tick_cycle(*, now: datetime) -> None:
        _ = now
        counts["tick_cycle"] += 1

    monkeypatch.setattr(runner, "_log_heartbeat_prices", _count_heartbeat)
    monkeypatch.setattr(runner, "_tick_cycle", _count_tick_cycle)

    runner._intraday_loop(now_provider=clock.now, sleep_fn=clock.sleep, max_ticks=None)

    assert counts["heartbeat"] == 0
    assert counts["tick_cycle"] == 0
    assert clock.now().time() == time(15, 1)


def test_backtest_runner_direct_init_inherits_shared_entry_guard_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    data_root = tmp_path / "data"
    tick_root = tmp_path / "ticks"
    _write_daily_csv(data_root / "1d" / "512480_SH.csv")
    tick_root.mkdir(parents=True, exist_ok=True)

    clock = SimulatedClock(datetime(2025, 3, 6, 9, 25, 0))
    store = MarketDataStore(data_root=data_root, codes=["512480.SH"], tick_root=tick_root, load_minute=False)
    data = BacktestDataAdapter(store=store, clock=clock)
    trading = BacktestTradingAdapter(clock=clock, initial_cash=100000.0, fee_rate=0.0, enable_t0=False)
    state_manager = InMemoryStateManager(initial_state=PortfolioState(nav=100000.0, cash=100000.0))
    cfg = StrategyConfig(
        state_path=str(tmp_path / "state" / "portfolio.json"),
        entry_log_path=str(tmp_path / "logs" / "entry.jsonl"),
        exit_log_path=str(tmp_path / "logs" / "exit.jsonl"),
        position_log_path=str(tmp_path / "logs" / "position.jsonl"),
        t0_log_path=str(tmp_path / "logs" / "t0.jsonl"),
        watchlist_etf_codes=("512480.SH",),
        tick_interval_s=3.0,
        watch_auto=False,
    )

    runner = BacktestStrategyRunner(
        config=cfg,
        data=data,
        trading=trading,
        state_manager=state_manager,
        fee_rate=0.0,
        disable_t0_ops=True,
    )

    assert runner._bt_no_reentry_after_confirm is True
    assert runner._bt_skip_high_chase_after_first_signal is True
    assert runner._bt_high_chase_signal_source == "all_signals"
    assert runner._bt_high_chase_lookback_days == 60
    assert runner._bt_high_chase_max_rise == 0.15


def test_market_store_available_days_reuses_sorted_trade_day_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import backtest.store as store_module

    store = MarketDataStore.__new__(MarketDataStore)
    store._trade_days = {
        datetime(2025, 3, 5).date(),
        datetime(2025, 3, 6).date(),
        datetime(2025, 3, 7).date(),
    }
    store._sorted_trade_days = None

    calls = {"n": 0}
    original_sorted = sorted

    def _spy_sorted(values):
        calls["n"] += 1
        return original_sorted(values)

    monkeypatch.setattr(store_module, "sorted", _spy_sorted, raising=False)

    days1 = store.available_days(start="20250305", end="20250306")
    days2 = store.available_days(start="20250306", end="20250307")

    assert days1 == [datetime(2025, 3, 5).date(), datetime(2025, 3, 6).date()]
    assert days2 == [datetime(2025, 3, 6).date(), datetime(2025, 3, 7).date()]
    assert calls["n"] == 1


def test_market_store_preload_tick_day_populates_cache_and_reuses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MarketDataStore.__new__(MarketDataStore)
    store._tick_mode = "by_day"
    store._active_tick_day = None
    store._tick_day_cache = {}
    store._tick_day_missing = set()

    trade_day = datetime(2025, 3, 6).date()
    tick_time = datetime(2025, 3, 6, 9, 30, 0)
    point = _TickPoint(
        time=tick_time,
        last_price=1.23,
        volume=100.0,
        amount=123.0,
        bid1_price=1.229,
        bid1_vol=10,
        ask1_price=1.231,
        ask1_vol=12,
        iopv=None,
        stock_status=0,
    )
    cache = _TickDayCache(points=[point], times=[tick_time], cum_volume=[100], cum_amount=[123.0])

    calls: list[str] = []

    def _fake_loader(*, code: str, day) -> object:
        assert day == trade_day
        calls.append(str(code))
        if str(code) == "MISSING.SH":
            return None
        return cache

    monkeypatch.setattr(store, "_load_tick_day_cache", _fake_loader)

    store.preload_tick_day(day=trade_day, codes=["512480.SH", "159997.SZ", "512480.SH", "MISSING.SH"], workers=2)

    assert set(calls) == {"512480.SH", "159997.SZ", "MISSING.SH"}
    assert store._tick_day_missing == {"MISSING.SH"}
    assert set(store._tick_day_cache.keys()) == {"512480.SH", "159997.SZ"}

    def _fail_loader(*, code: str, day) -> object:
        raise AssertionError(f"unexpected reload for {code} {day}")

    monkeypatch.setattr(store, "_load_tick_day_cache", _fail_loader)

    snap = store.tick_snapshot(code="512480.SH", now=tick_time)
    assert snap is not None
    assert float(snap[0].last_price) == pytest.approx(1.23)

