from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .enums import DataQuality, OrderSide, OrderStatus, OrderTimeInForce, OrderType


@dataclass(frozen=True)
class InstrumentInfo:
    etf_code: str
    instrument_name: str
    prev_close: float
    limit_up: float
    limit_down: float
    price_tick: float = 0.001


@dataclass(frozen=True)
class TickSnapshot:
    timestamp: datetime
    last_price: float
    volume: int
    amount: float
    ask1_price: float
    bid1_price: float
    ask1_vol: int
    bid1_vol: int
    iopv: Optional[float] = None
    stock_status: int = 0
    data_quality: DataQuality = DataQuality.OK

    def diff(self, prev: Optional[TickSnapshot]) -> tuple[int, float]:
        if prev is None:
            return 0, 0.0
        dv = int(self.volume) - int(prev.volume)
        da = float(self.amount) - float(prev.amount)
        return dv, da


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass(frozen=True)
class OrderRequest:
    etf_code: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    price: float
    tif: OrderTimeInForce = OrderTimeInForce.DAY
    strategy_name: str = ""
    remark: str = ""


@dataclass(frozen=True)
class OrderResult:
    order_id: int
    status: OrderStatus
    filled_qty: int = 0
    avg_price: Optional[float] = None
    raw: Optional[Any] = None
    error: str = ""


class TradingAdapter(ABC):
    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: int) -> bool: ...

    @abstractmethod
    def query_positions(self) -> list[Any]: ...

    @abstractmethod
    def query_orders(self) -> list[Any]: ...

    @abstractmethod
    def query_asset(self) -> dict[str, Any]: ...

    @abstractmethod
    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult: ...

    @abstractmethod
    def force_reconcile(self) -> dict[str, Any]: ...

    @abstractmethod
    def enter_freeze_mode(self, reason: str) -> None: ...

    @abstractmethod
    def exit_freeze_mode(self) -> None: ...


class DataAdapter(ABC):
    @abstractmethod
    def get_snapshot(self, etf_code: str) -> TickSnapshot: ...

    @abstractmethod
    def get_bars(self, etf_code: str, period: str, count: int) -> list[Bar]: ...

    @abstractmethod
    def get_instrument_info(self, etf_code: str) -> InstrumentInfo: ...

    @abstractmethod
    def subscribe_quote(self, etf_code: str, callback: Any) -> None: ...

    @abstractmethod
    def get_auction_volume(self, etf_code: str, date: str) -> float: ...
