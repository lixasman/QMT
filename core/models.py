from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .enums import FSMState
from .warn_utils import warn_once


@dataclass
class PendingSell:
    etf_code: str
    locked_qty: int
    lock_reason: str
    sell_at: str
    sell_price_type: str
    created_time: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "etf_code": self.etf_code,
            "locked_qty": int(self.locked_qty),
            "lock_reason": self.lock_reason,
            "sell_at": self.sell_at,
            "sell_price_type": self.sell_price_type,
            "created_time": self.created_time,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PendingSell:
        return cls(
            etf_code=str(d.get("etf_code") or ""),
            locked_qty=int(d.get("locked_qty") or 0),
            lock_reason=str(d.get("lock_reason") or ""),
            sell_at=str(d.get("sell_at") or ""),
            sell_price_type=str(d.get("sell_price_type") or ""),
            created_time=str(d.get("created_time") or ""),
        )


@dataclass
class T0TradeRecord:
    trade_id: str
    direction: str
    engine: str
    open_qty: int
    open_price: float
    open_time: str
    status: str
    open_order_id: Optional[int] = None
    close_qty: int = 0
    close_price: Optional[float] = None
    close_order_id: Optional[int] = None
    close_time: str = ""
    pnl: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "direction": self.direction,
            "engine": self.engine,
            "open_qty": int(self.open_qty),
            "open_price": float(self.open_price),
            "open_time": self.open_time,
            "status": self.status,
            "open_order_id": (None if self.open_order_id is None else int(self.open_order_id)),
            "close_qty": int(self.close_qty),
            "close_price": (None if self.close_price is None else float(self.close_price)),
            "close_order_id": (None if self.close_order_id is None else int(self.close_order_id)),
            "close_time": self.close_time,
            "pnl": float(self.pnl),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> T0TradeRecord:
        close_price = d.get("close_price")
        open_order_id = d.get("open_order_id")
        close_order_id = d.get("close_order_id")
        return cls(
            trade_id=str(d.get("trade_id") or ""),
            direction=str(d.get("direction") or ""),
            engine=str(d.get("engine") or ""),
            open_qty=int(d.get("open_qty") or 0),
            open_price=float(d.get("open_price") or 0.0),
            open_time=str(d.get("open_time") or ""),
            status=str(d.get("status") or ""),
            open_order_id=(None if open_order_id is None else int(open_order_id)),
            close_qty=int(d.get("close_qty") or 0),
            close_price=(None if close_price is None else float(close_price)),
            close_order_id=(None if close_order_id is None else int(close_order_id)),
            close_time=str(d.get("close_time") or ""),
            pnl=float(d.get("pnl") or 0.0),
        )


@dataclass
class CircuitBreakerInfo:
    triggered: bool = False
    trigger_date: str = ""
    trigger_nav: float = 0.0
    hwm_at_trigger: float = 0.0
    cooldown_expire: str = ""
    unlocked: bool = False
    intraday_freeze: bool = False
    intraday_freeze_time: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": bool(self.triggered),
            "trigger_date": self.trigger_date,
            "trigger_nav": float(self.trigger_nav),
            "hwm_at_trigger": float(self.hwm_at_trigger),
            "cooldown_expire": self.cooldown_expire,
            "unlocked": bool(self.unlocked),
            "intraday_freeze": bool(self.intraday_freeze),
            "intraday_freeze_time": self.intraday_freeze_time,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CircuitBreakerInfo:
        return cls(
            triggered=bool(d.get("triggered") or False),
            trigger_date=str(d.get("trigger_date") or ""),
            trigger_nav=float(d.get("trigger_nav") or 0.0),
            hwm_at_trigger=float(d.get("hwm_at_trigger") or 0.0),
            cooldown_expire=str(d.get("cooldown_expire") or ""),
            unlocked=bool(d.get("unlocked") or False),
            intraday_freeze=bool(d.get("intraday_freeze") or False),
            intraday_freeze_time=str(d.get("intraday_freeze_time") or ""),
        )


@dataclass
class LockedOrder:
    order_id: int
    etf_code: str
    side: str
    amount: float
    priority: int
    strategy_name: str
    lock_time: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": int(self.order_id),
            "etf_code": self.etf_code,
            "side": self.side,
            "amount": float(self.amount),
            "priority": int(self.priority),
            "strategy_name": self.strategy_name,
            "lock_time": self.lock_time,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LockedOrder:
        return cls(
            order_id=int(d.get("order_id") or 0),
            etf_code=str(d.get("etf_code") or ""),
            side=str(d.get("side") or ""),
            amount=float(d.get("amount") or 0.0),
            priority=int(d.get("priority") or 0),
            strategy_name=str(d.get("strategy_name") or ""),
            lock_time=str(d.get("lock_time") or ""),
        )


@dataclass
class PendingEntry:
    etf_code: str
    signal_date: str
    score_entry: float
    phase: str
    h_signal: float = 0.0
    l_signal: float = 0.0
    close_signal_day: float = 0.0
    atr_20: float = 0.0
    is_strong: bool = False
    expire_date: str = ""
    status: str = "PENDING_TRIAL"
    trial_qty: int = 0
    trial_price: Optional[float] = None
    trial_order_id: Optional[int] = None
    confirm_qty: int = 0
    confirm_price: Optional[float] = None
    confirm_order_id: Optional[int] = None
    confirm_deadline: str = ""
    sentiment_score: int = 0
    profit_ratio: float = 0.0
    micro_caution: bool = False
    vpin_rank: Optional[float] = None
    ofi_daily: Optional[float] = None
    vs_max: Optional[float] = None
    signals: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "etf_code": self.etf_code,
            "signal_date": self.signal_date,
            "score_entry": float(self.score_entry),
            "phase": self.phase,
            "h_signal": float(self.h_signal),
            "l_signal": float(self.l_signal),
            "close_signal_day": float(self.close_signal_day),
            "atr_20": float(self.atr_20),
            "is_strong": bool(self.is_strong),
            "expire_date": self.expire_date,
            "status": self.status,
            "trial_qty": int(self.trial_qty),
            "trial_price": (None if self.trial_price is None else float(self.trial_price)),
            "trial_order_id": (None if self.trial_order_id is None else int(self.trial_order_id)),
            "confirm_qty": int(self.confirm_qty),
            "confirm_price": (None if self.confirm_price is None else float(self.confirm_price)),
            "confirm_order_id": (None if self.confirm_order_id is None else int(self.confirm_order_id)),
            "confirm_deadline": self.confirm_deadline,
            "sentiment_score": int(self.sentiment_score),
            "profit_ratio": float(self.profit_ratio),
            "micro_caution": bool(self.micro_caution),
            "vpin_rank": (None if self.vpin_rank is None else float(self.vpin_rank)),
            "ofi_daily": (None if self.ofi_daily is None else float(self.ofi_daily)),
            "vs_max": (None if self.vs_max is None else float(self.vs_max)),
            "signals": {str(k): float(v) for k, v in (self.signals or {}).items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PendingEntry:
        trial_price = d.get("trial_price")
        confirm_price = d.get("confirm_price")
        trial_order_id = d.get("trial_order_id")
        confirm_order_id = d.get("confirm_order_id")
        vpin_rank = d.get("vpin_rank")
        ofi_daily = d.get("ofi_daily")
        vs_max = d.get("vs_max")
        raw_signals = d.get("signals") or {}
        signals: dict[str, float] = {}
        dropped = 0
        if isinstance(raw_signals, dict):
            for k, v in raw_signals.items():
                if v is None:
                    continue
                try:
                    signals[str(k)] = float(v)
                except Exception:
                    dropped += 1
                    continue
        if isinstance(raw_signals, dict) and dropped:
            warn_once(
                f"pending_entry_signals_dropped:{str(d.get('etf_code') or '')}",
                f"State: PendingEntry signals 存在不可解析字段，已丢弃并继续: etf={str(d.get('etf_code') or '')} dropped={dropped}/{len(raw_signals)}",
            )
        return cls(
            etf_code=str(d.get("etf_code") or ""),
            signal_date=str(d.get("signal_date") or ""),
            score_entry=float(d.get("score_entry") or 0.0),
            phase=str(d.get("phase") or ""),
            h_signal=float(d.get("h_signal") or 0.0),
            l_signal=float(d.get("l_signal") or 0.0),
            close_signal_day=float(d.get("close_signal_day") or 0.0),
            atr_20=float(d.get("atr_20") or 0.0),
            is_strong=bool(d.get("is_strong") or False),
            expire_date=str(d.get("expire_date") or ""),
            status=str(d.get("status") or "PENDING_TRIAL"),
            trial_qty=int(d.get("trial_qty") or 0),
            trial_price=(None if trial_price is None else float(trial_price)),
            trial_order_id=(None if trial_order_id is None else int(trial_order_id)),
            confirm_qty=int(d.get("confirm_qty") or 0),
            confirm_price=(None if confirm_price is None else float(confirm_price)),
            confirm_order_id=(None if confirm_order_id is None else int(confirm_order_id)),
            confirm_deadline=str(d.get("confirm_deadline") or ""),
            sentiment_score=int(d.get("sentiment_score") or 0),
            profit_ratio=float(d.get("profit_ratio") or 0.0),
            micro_caution=bool(d.get("micro_caution") or False),
            vpin_rank=(None if vpin_rank is None else float(vpin_rank)),
            ofi_daily=(None if ofi_daily is None else float(ofi_daily)),
            vs_max=(None if vs_max is None else float(vs_max)),
            signals=signals,
        )


@dataclass
class PositionState:
    etf_code: str
    state: FSMState = FSMState.S0_IDLE
    base_qty: int = 0
    scale_1_qty: int = 0
    scale_2_qty: int = 0
    total_qty: int = 0
    avg_cost: float = 0.0
    effective_slot: float = 0.0
    scale_count: int = 0
    last_scale_date: str = ""
    t0_frozen: bool = False
    t0_max_exposure: float = 0.0
    t0_daily_pnl: float = 0.0
    t0_pnl_5d: list[float] = field(default_factory=list)
    t0_pnl_30d: list[float] = field(default_factory=list)
    t0_consecutive_loss_count: int = 0
    highest_high: float = 0.0
    entry_date: str = ""
    pending_sell_locked: list[PendingSell] = field(default_factory=list)
    pending_sell_unfilled: list[PendingSell] = field(default_factory=list)
    t0_trades: list[T0TradeRecord] = field(default_factory=list)
    cooldown_until: str = ""
    lifeboat_used: bool = False
    lifeboat_sell_time: str = ""
    auction_volume_history: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "etf_code": self.etf_code,
            "state": self.state.value,
            "base_qty": int(self.base_qty),
            "scale_1_qty": int(self.scale_1_qty),
            "scale_2_qty": int(self.scale_2_qty),
            "total_qty": int(self.total_qty),
            "avg_cost": float(self.avg_cost),
            "effective_slot": float(self.effective_slot),
            "scale_count": int(self.scale_count),
            "last_scale_date": self.last_scale_date,
            "t0_frozen": bool(self.t0_frozen),
            "t0_max_exposure": float(self.t0_max_exposure),
            "t0_daily_pnl": float(self.t0_daily_pnl),
            "t0_pnl_5d": [float(x) for x in self.t0_pnl_5d],
            "t0_pnl_30d": [float(x) for x in self.t0_pnl_30d],
            "t0_consecutive_loss_count": int(self.t0_consecutive_loss_count),
            "highest_high": float(self.highest_high),
            "entry_date": self.entry_date,
            "pending_sell_locked": [p.to_dict() for p in self.pending_sell_locked],
            "pending_sell_unfilled": [p.to_dict() for p in self.pending_sell_unfilled],
            "t0_trades": [t.to_dict() for t in self.t0_trades],
            "cooldown_until": self.cooldown_until,
            "lifeboat_used": bool(self.lifeboat_used),
            "lifeboat_sell_time": self.lifeboat_sell_time,
            "auction_volume_history": [float(x) for x in self.auction_volume_history],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PositionState:
        raw_state = str(d.get("state") or FSMState.S0_IDLE.value)
        try:
            st = FSMState(raw_state)
        except Exception:
            st = FSMState.S0_IDLE
        ps = cls(
            etf_code=str(d.get("etf_code") or ""),
            state=st,
            base_qty=int(d.get("base_qty") or 0),
            scale_1_qty=int(d.get("scale_1_qty") or 0),
            scale_2_qty=int(d.get("scale_2_qty") or 0),
            total_qty=int(d.get("total_qty") or 0),
            avg_cost=float(d.get("avg_cost") or 0.0),
            effective_slot=float(d.get("effective_slot") or 0.0),
            scale_count=int(d.get("scale_count") or 0),
            last_scale_date=str(d.get("last_scale_date") or ""),
            t0_frozen=bool(d.get("t0_frozen") or False),
            t0_max_exposure=float(d.get("t0_max_exposure") or 0.0),
            t0_daily_pnl=float(d.get("t0_daily_pnl") or 0.0),
            t0_consecutive_loss_count=int(d.get("t0_consecutive_loss_count") or 0),
            highest_high=float(d.get("highest_high") or 0.0),
            entry_date=str(d.get("entry_date") or ""),
            cooldown_until=str(d.get("cooldown_until") or ""),
            lifeboat_used=bool(d.get("lifeboat_used") or False),
            lifeboat_sell_time=str(d.get("lifeboat_sell_time") or ""),
        )
        ps.pending_sell_locked = [
            PendingSell.from_dict(x) for x in (d.get("pending_sell_locked") or []) if isinstance(x, dict)
        ]
        ps.pending_sell_unfilled = [
            PendingSell.from_dict(x) for x in (d.get("pending_sell_unfilled") or []) if isinstance(x, dict)
        ]
        ps.t0_trades = [T0TradeRecord.from_dict(x) for x in (d.get("t0_trades") or []) if isinstance(x, dict)]
        ps.auction_volume_history = [float(x) for x in (d.get("auction_volume_history") or []) if x is not None]
        ps.t0_pnl_5d = [float(x) for x in (d.get("t0_pnl_5d") or []) if x is not None]
        ps.t0_pnl_30d = [float(x) for x in (d.get("t0_pnl_30d") or []) if x is not None]
        return ps


@dataclass
class PortfolioState:
    nav: float = 0.0
    hwm: float = 0.0
    cash: float = 0.0
    frozen_cash: float = 0.0
    positions: dict[str, PositionState] = field(default_factory=dict)
    circuit_breaker: CircuitBreakerInfo = field(default_factory=CircuitBreakerInfo)
    correlation_matrix_date: str = ""
    pending_entries: list[PendingEntry] = field(default_factory=list)
    locked_orders: list[LockedOrder] = field(default_factory=list)
    preemption_active: bool = False
    preempt_buy_order_id: Optional[int] = None
    preempt_weak_sell_order_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nav": float(self.nav),
            "hwm": float(self.hwm),
            "cash": float(self.cash),
            "frozen_cash": float(self.frozen_cash),
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "circuit_breaker": self.circuit_breaker.to_dict(),
            "correlation_matrix_date": self.correlation_matrix_date,
            "pending_entries": [p.to_dict() for p in self.pending_entries],
            "locked_orders": [o.to_dict() for o in self.locked_orders],
            "preemption_active": bool(self.preemption_active),
            "preempt_buy_order_id": (None if self.preempt_buy_order_id is None else int(self.preempt_buy_order_id)),
            "preempt_weak_sell_order_id": (None if self.preempt_weak_sell_order_id is None else int(self.preempt_weak_sell_order_id)),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PortfolioState:
        st = cls(
            nav=float(d.get("nav") or 0.0),
            hwm=float(d.get("hwm") or 0.0),
            cash=float(d.get("cash") or 0.0),
            frozen_cash=float(d.get("frozen_cash") or 0.0),
            correlation_matrix_date=str(d.get("correlation_matrix_date") or ""),
        )
        st.preemption_active = bool(d.get("preemption_active") or False)
        pb = d.get("preempt_buy_order_id")
        pw = d.get("preempt_weak_sell_order_id")
        st.preempt_buy_order_id = (None if pb is None else int(pb))
        st.preempt_weak_sell_order_id = (None if pw is None else int(pw))
        raw_positions = d.get("positions") or {}
        if isinstance(raw_positions, dict):
            for k, v in raw_positions.items():
                if not isinstance(v, dict):
                    continue
                st.positions[str(k)] = PositionState.from_dict(v)
        cb = d.get("circuit_breaker")
        if isinstance(cb, dict):
            st.circuit_breaker = CircuitBreakerInfo.from_dict(cb)
        st.pending_entries = [PendingEntry.from_dict(x) for x in (d.get("pending_entries") or []) if isinstance(x, dict)]
        st.locked_orders = [LockedOrder.from_dict(x) for x in (d.get("locked_orders") or []) if isinstance(x, dict)]
        return st
