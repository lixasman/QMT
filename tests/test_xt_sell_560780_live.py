from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

import pytest

from core.adapters.data_adapter import XtDataAdapter
from core.adapters.xt_trading_adapter import XtTradingAdapter
from core.enums import OrderSide, OrderType
from core.interfaces import OrderRequest
from core.price_utils import align_order_price
from core.time_utils import is_trading_time
from exit.constants import LAYER1_SELL_DISCOUNT
from exit.exit_fsm import _extract_etf_code, _extract_sellable_qty, _extract_total_qty
from integrations.watchlist_loader import code6, normalize_etf_code


def _env_required(name: str) -> str:
    return str(os.getenv(name, "")).strip()


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        return int(raw)
    except Exception as e:
        raise AssertionError(f"invalid env {name}={raw!r}") from e


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        return float(raw)
    except Exception as e:
        raise AssertionError(f"invalid env {name}={raw!r}") from e


def _build_xt_trading_adapter(*, xt_path: str, xt_account: str, xt_session: str) -> XtTradingAdapter:
    try:
        from xtquant import xttrader  # type: ignore
    except Exception as e:
        pytest.skip(f"xtquant.xttrader not available: {repr(e)}")

    trader_cls = getattr(xttrader, "XtQuantTrader", None)
    if not callable(trader_cls):
        raise AssertionError("xttrader missing XtQuantTrader")
    trader = trader_cls(str(xt_path), int(xt_session))

    start = getattr(trader, "start", None)
    connect = getattr(trader, "connect", None)
    if callable(start):
        start()
    if callable(connect):
        connect()

    acc = None
    acct_cls = getattr(xttrader, "StockAccount", None)
    if callable(acct_cls):
        acc = acct_cls(str(xt_account))
        sub = getattr(trader, "subscribe", None)
        if callable(sub):
            try:
                sub(acc)
            except Exception:
                pass
    else:
        acct2 = None
        try:
            from xtquant import xttype  # type: ignore
        except Exception:
            xttype = None  # type: ignore[assignment]
        if xttype is not None:
            acct2 = getattr(xttype, "StockAccount", None)
        if callable(acct2):
            acc = acct2(str(xt_account))
            sub = getattr(trader, "subscribe", None)
            if callable(sub):
                try:
                    sub(acc)
                except Exception:
                    pass

    return XtTradingAdapter(trader, account=acc)


def _query_position_balances(*, trading: XtTradingAdapter, target_code6: str) -> tuple[int, int, list[Any]]:
    raw_positions = trading.query_positions()
    total = 0
    sellable = 0
    for p in raw_positions:
        c = code6(_extract_etf_code(p))
        if c != target_code6:
            continue
        total = int(_extract_total_qty(p))
        sellable = int(_extract_sellable_qty(p))
        break
    return int(total), int(sellable), list(raw_positions)


@pytest.mark.skipif(os.getenv("QMT_LIVE") != "1", reason="requires QMT_LIVE=1 and a running MiniQMT connection")
def test_xt_live_sell_560780_and_verify_position_decrease() -> None:
    now = datetime.now()
    if not is_trading_time(now):
        pytest.skip(f"not in trading session now={now.isoformat(timespec='seconds')}")

    xt_path = _env_required("QMT_XT_PATH")
    xt_account = _env_required("QMT_XT_ACCOUNT")
    xt_session = _env_required("QMT_XT_SESSION")
    missing = [k for k, v in (("QMT_XT_PATH", xt_path), ("QMT_XT_ACCOUNT", xt_account), ("QMT_XT_SESSION", xt_session)) if not v]
    if missing:
        pytest.skip(f"missing required env: {', '.join(missing)}")

    raw_code = str(os.getenv("QMT_SELL_CODE", "560780")).strip() or "560780"
    code = normalize_etf_code(raw_code)
    code_6 = code6(code)
    if not code_6:
        raise AssertionError(f"invalid QMT_SELL_CODE={raw_code!r}")

    qty = _env_int("QMT_SELL_QTY", 100)
    if qty <= 0 or qty % 100 != 0:
        raise AssertionError(f"QMT_SELL_QTY must be positive and round-lot(100), got {qty}")

    wait_s = _env_float("QMT_SELL_WAIT_S", 45.0)
    poll_s = _env_float("QMT_SELL_POLL_S", 1.0)
    if wait_s <= 0 or poll_s <= 0:
        raise AssertionError(f"QMT_SELL_WAIT_S and QMT_SELL_POLL_S must be >0, got wait={wait_s}, poll={poll_s}")

    trading = _build_xt_trading_adapter(xt_path=xt_path, xt_account=xt_account, xt_session=xt_session)
    data = XtDataAdapter()

    before_total, before_sellable, _before_positions = _query_position_balances(trading=trading, target_code6=code_6)
    if int(before_sellable) < int(qty):
        pytest.skip(f"insufficient sellable qty for test: code={code} sellable={before_sellable} required={qty}")

    snap = data.get_snapshot(code)
    inst = data.get_instrument_info(code)
    if float(snap.bid1_price) <= 0:
        raise AssertionError(f"invalid bid1_price={snap.bid1_price} for {code}")

    raw_sell_price = float(snap.bid1_price) * float(LAYER1_SELL_DISCOUNT)
    sell_price = align_order_price(
        price=float(raw_sell_price),
        side="SELL",
        lower_limit=float(inst.limit_down),
        upper_limit=float(inst.limit_up),
        tick_size=float(inst.price_tick),
    )
    req = OrderRequest(
        etf_code=str(code),
        side=OrderSide.SELL,
        quantity=int(qty),
        order_type=OrderType.LIMIT,
        price=float(sell_price),
        strategy_name="exit",
        remark="LAYER1",
    )
    res = trading.place_order(req)
    if int(res.order_id) <= 0:
        raise AssertionError(f"place_order failed: order_id={res.order_id}, error={res.error!r}, raw={res.raw!r}")

    deadline = time.time() + float(wait_s)
    last_after_total = int(before_total)
    last_positions: list[Any] = []
    target_total = int(before_total) - int(qty)
    while time.time() < deadline:
        after_total, _after_sellable, positions = _query_position_balances(trading=trading, target_code6=code_6)
        last_after_total = int(after_total)
        last_positions = list(positions)
        if int(after_total) <= int(target_total):
            return
        time.sleep(float(poll_s))

    try:
        trading.cancel_order(int(res.order_id))
    except Exception:
        pass

    pytest.fail(
        (
            "position decrease timeout after sell; "
            f"code={code} order_id={int(res.order_id)} qty={int(qty)} "
            f"before_total={int(before_total)} after_total={int(last_after_total)} target_total={int(target_total)} "
            f"positions={last_positions!r}"
        )
    )
