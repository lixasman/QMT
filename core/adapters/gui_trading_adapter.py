from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from ..enums import OrderSide, OrderStatus
from ..interfaces import OrderRequest, OrderResult, TradingAdapter


try:
    import easytrader  # type: ignore
except Exception:
    easytrader = None


class GuiTradingAdapter(TradingAdapter):
    def __init__(self, client: Any, *, gui_ops_limit: int = 20, freeze_threshold: int = 15) -> None:
        if easytrader is None:
            raise RuntimeError("easytrader is not available")
        self._client = client
        self._gui_ops_limit = int(gui_ops_limit)
        self._freeze_threshold = int(freeze_threshold)
        self._gui_ops = 0
        self._frozen = False
        self._freeze_reason = ""
        self._last_position_query_ts: Optional[float] = None

    @property
    def gui_ops(self) -> int:
        return int(self._gui_ops)

    def _touch_op(self) -> None:
        self._gui_ops += 1
        if self._gui_ops >= self._freeze_threshold:
            self.enter_freeze_mode("gui_ops_near_limit")
        if self._gui_ops > self._gui_ops_limit:
            self.enter_freeze_mode("gui_ops_limit_exceeded")

    def place_order(self, req: OrderRequest) -> OrderResult:
        if self._frozen:
            return OrderResult(order_id=0, status=OrderStatus.REJECTED, error=f"frozen: {self._freeze_reason}")
        self._touch_op()
        try:
            if req.side == OrderSide.BUY:
                fn = getattr(self._client, "buy", None)
            else:
                fn = getattr(self._client, "sell", None)
            if not callable(fn):
                raise RuntimeError("easytrader client missing buy/sell")
            raw = fn(req.etf_code, price=req.price, amount=req.quantity)
            oid = int(getattr(raw, "order_id", 0) or 0)
            return OrderResult(order_id=oid, status=OrderStatus.SUBMITTED, raw=raw)
        except Exception as e:
            return OrderResult(order_id=0, status=OrderStatus.REJECTED, error=str(e))

    def cancel_order(self, order_id: int) -> bool:
        self._touch_op()
        fn = getattr(self._client, "cancel_entrust", None)
        if not callable(fn):
            fn = getattr(self._client, "cancel_order", None)
        if not callable(fn):
            raise RuntimeError("easytrader client missing cancel")
        return bool(fn(int(order_id)))

    def query_positions(self) -> list[Any]:
        now = time.time()
        if self._last_position_query_ts is not None and (now - self._last_position_query_ts) < 10.0:
            raise AssertionError(f"position query too frequent: {(now - self._last_position_query_ts):.3f}s")
        self._last_position_query_ts = now
        fn = getattr(self._client, "position", None)
        if not callable(fn):
            fn = getattr(self._client, "get_position", None)
        if not callable(fn):
            raise RuntimeError("easytrader client missing position query")
        r = fn()
        if isinstance(r, list):
            return r
        return list(r or [])

    def query_orders(self) -> list[Any]:
        fn = getattr(self._client, "today_entrusts", None)
        if not callable(fn):
            fn = getattr(self._client, "get_orders", None)
        if not callable(fn):
            raise RuntimeError("easytrader client missing order query")
        r = fn()
        if isinstance(r, list):
            return r
        return list(r or [])

    def query_asset(self) -> dict[str, Any]:
        fn = getattr(self._client, "balance", None)
        if not callable(fn):
            fn = getattr(self._client, "get_balance", None)
        if not callable(fn):
            raise RuntimeError("easytrader client missing asset query")
        r = fn()
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
                oids = o.get("entrust_no") if isinstance(o, dict) else getattr(o, "order_id", None)
                if oids is None:
                    continue
                try:
                    if int(oids) != oid:
                        continue
                except Exception:
                    continue
                last = o
                status = o.get("status") if isinstance(o, dict) else getattr(o, "status", "")
                st = str(status or "").upper()
                if "成" in st or "FILLED" in st:
                    return OrderResult(order_id=oid, status=OrderStatus.FILLED, raw=o)
                if "撤" in st or "CANCEL" in st:
                    return OrderResult(order_id=oid, status=OrderStatus.CANCELED, raw=o)
                if "废" in st or "REJECT" in st:
                    return OrderResult(order_id=oid, status=OrderStatus.REJECTED, raw=o)
                break
            time.sleep(0.2)
        return OrderResult(order_id=oid, status=OrderStatus.UNKNOWN, raw=last, error="confirm timeout")

    def force_reconcile(self) -> dict[str, Any]:
        return {"positions": self.query_positions(), "orders": self.query_orders(), "asset": self.query_asset(), "gui_ops": self.gui_ops}

    def enter_freeze_mode(self, reason: str) -> None:
        self._frozen = True
        self._freeze_reason = str(reason)

    def exit_freeze_mode(self) -> None:
        self._frozen = False
        self._freeze_reason = ""
        self._gui_ops = 0
        self._last_position_query_ts = None
