from __future__ import annotations

from core.adapters.xt_trading_adapter import XtTradingAdapter


class _TraderNeedsAccount:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def query_stock_orders(self, account: object) -> list[object]:
        self.calls.append(("orders", account))
        return [object()]


class _TraderNoAccount:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def query_stock_orders(self) -> list[object]:
        self.calls.append(("orders",))
        return [object(), object()]


def test_xt_trading_adapter_query_orders_passes_account_when_needed() -> None:
    acc = object()
    t = _TraderNeedsAccount()
    a = XtTradingAdapter(t, account=acc)
    r = a.query_orders()
    assert len(r) == 1
    assert t.calls == [("orders", acc)]


def test_xt_trading_adapter_query_orders_falls_back_without_account() -> None:
    acc = object()
    t = _TraderNoAccount()
    a = XtTradingAdapter(t, account=acc)
    r = a.query_orders()
    assert len(r) == 2
    assert t.calls == [("orders",)]
