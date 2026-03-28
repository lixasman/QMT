from __future__ import annotations

import time
from typing import Any, Optional

from ..enums import OrderStatus
from ..interfaces import OrderRequest, OrderResult, TradingAdapter
from ..warn_utils import degrade_once


class XtTradingAdapter(TradingAdapter):
    def __init__(self, trader: Any, *, account: Any = None) -> None:
        self._trader = trader
        self._account = account
        self._frozen = False
        self._freeze_reason = ""

    def _call(self, fn: Any, *args: Any) -> Any:
        if self._account is None:
            return fn(*args)
        try:
            return fn(self._account, *args)
        except TypeError as e1:
            try:
                return fn(*args)
            except TypeError:
                raise e1

    @staticmethod
    def _as_int(v: Any) -> Optional[int]:
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return int(v)
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            s = str(v).strip()
            if not s:
                return None
            try:
                return int(float(s))
            except Exception:
                return None
        return None

    @classmethod
    def _extract_order_id(cls, raw: Any) -> int:
        if raw is None:
            return 0
        if isinstance(raw, dict):
            for k in ("order_id", "entrust_no", "entrustNo", "id"):
                iv = cls._as_int(raw.get(k))
                if iv is not None:
                    return int(iv)
            return 0
        iv0 = cls._as_int(raw)
        if iv0 is not None:
            return int(iv0)
        for k2 in ("order_id", "entrust_no", "entrustNo", "id"):
            iv = cls._as_int(getattr(raw, k2, None))
            if iv is not None:
                return int(iv)
        return 0

    @staticmethod
    def _xt_order_side_code(side_value: Any) -> Any:
        iv = XtTradingAdapter._as_int(side_value)
        if iv is not None:
            return int(iv)
        s = str(side_value).strip().upper()
        try:
            from xtquant import xtconstant  # type: ignore

            if s == "BUY":
                return int(getattr(xtconstant, "STOCK_BUY"))
            if s == "SELL":
                return int(getattr(xtconstant, "STOCK_SELL"))
        except Exception:
            pass
        if s == "BUY":
            return 23
        if s == "SELL":
            return 24
        return side_value

    @staticmethod
    def _xt_price_type_code(order_type_value: Any) -> Any:
        iv = XtTradingAdapter._as_int(order_type_value)
        if iv is not None:
            return int(iv)
        s = str(order_type_value).strip().upper()
        if s != "LIMIT":
            return order_type_value
        try:
            from xtquant import xtconstant  # type: ignore

            return int(getattr(xtconstant, "FIX_PRICE"))
        except Exception:
            return 11

    def place_order(self, req: OrderRequest) -> OrderResult:
        if self._frozen:
            return OrderResult(order_id=0, status=OrderStatus.REJECTED, error=f"frozen: {self._freeze_reason}")
        fn = getattr(self._trader, "order_stock", None)
        if not callable(fn):
            raise RuntimeError("xt trader missing order_stock")

        order_side_code = self._xt_order_side_code(req.side.value)
        price_type_code = self._xt_price_type_code(req.order_type.value)

        candidates: list[tuple[Any, ...]] = [
            (
                req.etf_code,
                order_side_code,
                int(req.quantity),
                price_type_code,
                float(req.price),
                str(req.strategy_name),
                str(req.remark),
            ),
            (
                req.etf_code,
                order_side_code,
                int(req.quantity),
                price_type_code,
                float(req.price),
            ),
            (
                req.etf_code,
                req.side.value,
                int(req.quantity),
                float(req.price),
                req.order_type.value,
            ),
        ]

        raw: Any = None
        last_type_error: Optional[TypeError] = None
        for args in candidates:
            try:
                raw = self._call(fn, *args)
                last_type_error = None
                break
            except TypeError as e:
                last_type_error = e
                continue
        if last_type_error is not None:
            raise last_type_error

        oid = self._extract_order_id(raw)
        return OrderResult(order_id=oid, status=OrderStatus.SUBMITTED, raw=raw)

    def cancel_order(self, order_id: int) -> bool:
        fn = getattr(self._trader, "cancel_order", None)
        if not callable(fn):
            raise RuntimeError("xt trader missing cancel_order")
        r = self._call(fn, int(order_id))
        return bool(r)

    def query_positions(self) -> list[Any]:
        fn = getattr(self._trader, "query_stock_positions", None)
        if not callable(fn):
            fn = getattr(self._trader, "query_positions", None)
        if not callable(fn):
            raise RuntimeError("xt trader missing query positions")
        r = self._call(fn)
        return list(r or [])

    def query_orders(self) -> list[Any]:
        fn = getattr(self._trader, "query_stock_orders", None)
        if not callable(fn):
            fn = getattr(self._trader, "query_orders", None)
        if not callable(fn):
            raise RuntimeError("xt trader missing query orders")
        r = self._call(fn)
        return list(r or [])

    def query_asset(self) -> dict[str, Any]:
        fn = getattr(self._trader, "query_stock_asset", None)
        if not callable(fn):
            fn = getattr(self._trader, "query_asset", None)
        if not callable(fn):
            raise RuntimeError("xt trader missing query asset")
        r = self._call(fn)
        if isinstance(r, dict):
            return r
        return {"raw": r}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        oid = int(order_id)
        t0 = time.time()
        last: Optional[Any] = None
        while time.time() - t0 <= float(timeout_s):
            orders = self.query_orders()
            for o in orders:
                if int(getattr(o, "order_id", 0) or 0) != oid:
                    continue
                last = o
                st = str(getattr(o, "status", "") or "").upper()
                if "FILLED" in st or "DONE" in st or "成交" in st:
                    return OrderResult(order_id=oid, status=OrderStatus.FILLED, raw=o)
                if "CANCEL" in st or "撤" in st:
                    return OrderResult(order_id=oid, status=OrderStatus.CANCELED, raw=o)
                if "REJECT" in st or "拒" in st:
                    return OrderResult(order_id=oid, status=OrderStatus.REJECTED, raw=o)
                degrade_once(
                    f"xt_confirm_unparsed_status:{int(oid)}",
                    f"XT confirm_order cannot map broker status; waiting timeout fallback. order_id={int(oid)} raw_status={st}",
                )
                break
            time.sleep(0.2)
        degrade_once(
            f"xt_confirm_timeout:{int(oid)}",
            f"XT confirm_order timeout; returning UNKNOWN. order_id={int(oid)} timeout_s={float(timeout_s)}",
        )
        return OrderResult(order_id=oid, status=OrderStatus.UNKNOWN, raw=last, error="confirm timeout")

    def force_reconcile(self) -> dict[str, Any]:
        return {"positions": self.query_positions(), "orders": self.query_orders(), "asset": self.query_asset()}

    def enter_freeze_mode(self, reason: str) -> None:
        self._frozen = True
        self._freeze_reason = str(reason)

    def exit_freeze_mode(self) -> None:
        self._frozen = False
        self._freeze_reason = ""
