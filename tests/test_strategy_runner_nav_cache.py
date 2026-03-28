from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from core.models import PortfolioState, PositionState
from strategy_runner import StrategyRunner


class _FakeData:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = dict(prices)
        self.calls: list[str] = []

    def get_snapshot(self, code: str):
        self.calls.append(str(code))
        return SimpleNamespace(last_price=float(self._prices[str(code)]))


def _make_runner(*, cash: float = 100.0) -> tuple[StrategyRunner, _FakeData]:
    data = _FakeData({"510050.SH": 1.2, "512480.SH": 0.8})
    runner = StrategyRunner.__new__(StrategyRunner)
    runner._data = data  # type: ignore[attr-defined]
    runner._state = PortfolioState(
        nav=0.0,
        cash=float(cash),
        positions={
            "510050.SH": PositionState(etf_code="510050.SH", total_qty=100),
            "512480.SH": PositionState(etf_code="512480.SH", total_qty=200),
        },
    )
    runner._nav_estimate_cache_key = None  # type: ignore[attr-defined]
    runner._nav_estimate_cache_value = None  # type: ignore[attr-defined]
    return runner, data


def test_nav_estimate_caches_same_tick_for_same_portfolio_signature() -> None:
    runner, data = _make_runner()
    now = datetime(2025, 3, 6, 10, 0, 0)

    first = runner._nav_estimate(now=now)
    second = runner._nav_estimate(now=now)

    assert first == second == 380.0
    assert data.calls == ["510050.SH", "512480.SH"]


def test_nav_estimate_invalidates_cache_when_portfolio_signature_changes() -> None:
    runner, data = _make_runner()
    now = datetime(2025, 3, 6, 10, 0, 0)

    first = runner._nav_estimate(now=now)
    runner._state.cash = 120.0  # type: ignore[attr-defined]
    second = runner._nav_estimate(now=now)

    assert first == 380.0
    assert second == 400.0
    assert data.calls == ["510050.SH", "512480.SH", "510050.SH", "512480.SH"]


def test_nav_estimate_invalidates_cache_when_tick_changes() -> None:
    runner, data = _make_runner()

    first = runner._nav_estimate(now=datetime(2025, 3, 6, 10, 0, 0))
    second = runner._nav_estimate(now=datetime(2025, 3, 6, 10, 0, 3))

    assert first == second == 380.0
    assert data.calls == ["510050.SH", "512480.SH", "510050.SH", "512480.SH"]
