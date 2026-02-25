from __future__ import annotations

from pathlib import Path


def main() -> int:
    try:
        from xtquant import xttrader  # type: ignore
    except Exception as e:
        print("IMPORT_FAIL", repr(e))
        return 2

    names = sorted([n for n in dir(xttrader) if ("Account" in n or "account" in n)])
    print("xttrader_account_names", names)

    cls = getattr(xttrader, "StockAccount", None)
    print("StockAccount", repr(cls), "callable", callable(cls))

    trader_cls = getattr(xttrader, "XtQuantTrader", None)
    print("XtQuantTrader", repr(trader_cls), "callable", callable(trader_cls))

    if callable(trader_cls):
        try:
            m = getattr(trader_cls, "query_stock_orders", None)
            print("XtQuantTrader.query_stock_orders", repr(m))
        except Exception as e:
            print("get_method_fail", repr(e))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
