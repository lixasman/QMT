from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

from core.cash_manager import CashManager
from core.enums import OrderSide
from core.interfaces import DataAdapter, OrderRequest, TradingAdapter
from core.models import LockedOrder, PendingEntry, PortfolioState
from core.state_manager import StateManager
from core.warn_utils import warn_once

from .archiver import archive_near_miss, archive_signal_fired, archive_watchlist
from .entry_logger import log_phase2_score, log_phase3_decision, log_phase3_rejected
from .phase2 import Phase2Result
from .types import ConfirmActionType, SignalFired, WatchlistItem

ENTRY_MUTEX = threading.Lock()

PRIORITY_T0 = 1
PRIORITY_TRIAL = 2
PRIORITY_PREEMPTION = 3


def _extract_order_id(o: Any) -> Optional[int]:
    if isinstance(o, dict):
        for k in ("order_id", "entrust_no", "entrustNo", "id"):
            v = o.get(k)
            if v is None:
                continue
            try:
                return int(v)
            except Exception:
                continue
        return None
    v2 = getattr(o, "order_id", None)
    if v2 is not None:
        try:
            return int(v2)
        except Exception:
            return None
    v3 = getattr(o, "entrust_no", None)
    if v3 is not None:
        try:
            return int(v3)
        except Exception:
            return None
    return None


def _order_ids(raw_orders: list[Any]) -> set[int]:
    out: set[int] = set()
    bad = 0
    for o in raw_orders:
        oid = _extract_order_id(o)
        if oid is None:
            bad += 1
        if oid is not None and oid > 0:
            out.add(int(oid))
    if bad:
        warn_once("entry_orders_unparsed", f"Trading: query_orders 返回的订单中存在无法解析的订单号，已降级忽略: bad={bad}/{len(raw_orders)}")
    return out


def _ensure_pending_entry_shape(pe: PendingEntry) -> None:
    if not pe.etf_code:
        raise AssertionError("pending entry missing etf_code")
    if float(pe.score_entry) < 0:
        raise AssertionError(f"pending entry score negative: {pe.etf_code} {pe.score_entry}")


class EntryFSM:
    def __init__(
        self,
        *,
        state_manager: StateManager,
        data: DataAdapter,
        trading: TradingAdapter,
        state: PortfolioState,
        archive_base_dir: str = "data",
        log_path: str = "data/logs/entry_decisions.jsonl",
        mutex: threading.Lock = ENTRY_MUTEX,
    ) -> None:
        self._sm = state_manager
        self._data = data
        self._trading = trading
        self._state = state
        self._cash = CashManager(state)
        self._archive_base_dir = archive_base_dir
        self._log_path = log_path
        self._mutex = mutex

    @property
    def state(self) -> PortfolioState:
        return self._state

    @property
    def cash(self) -> CashManager:
        return self._cash

    def save(self) -> None:
        self._sm.save(self._state)

    def upsert_watchlist(self, *, d: datetime, watchlist: list[WatchlistItem]) -> None:
        archive_watchlist(base_dir=self._archive_base_dir, d=d.date(), watchlist=watchlist)

    def record_phase2_result(self, *, timestamp: datetime, etf_code: str, watch: WatchlistItem, res: Phase2Result) -> None:
        diversity_gate = bool(res.signals.get("S_volume", 0.0) > 0.0 or res.signals.get("S_chip_pr", 0.0) > 0.0)
        decision = "SIGNAL_FIRED" if res.is_triggered else "NO_SIGNAL"
        note = "强信号 ≥0.70" if res.is_strong else ""
        signals_for_log = dict(res.signals)
        signals_for_log.update(
            {
                "sentiment_score": int(watch.sentiment_score),
                "profit_ratio": float(watch.profit_ratio),
                "vpin_rank": watch.vpin_rank,
                "ofi_daily": watch.ofi_daily,
                "vs_max": watch.vs_max,
            }
        )
        log_phase2_score(
            log_path=self._log_path,
            timestamp=timestamp,
            etf_code=etf_code,
            signals=signals_for_log,
            score=res.score,
            diversity_gate=diversity_gate,
            decision=decision,
            note=note,
        )
        if res.signal_fired is not None:
            archive_signal_fired(base_dir=self._archive_base_dir, fired=res.signal_fired)
        else:
            if 0.25 <= float(res.score) < 0.45:
                archive_near_miss(
                    base_dir=self._archive_base_dir,
                    d=timestamp.date(),
                    etf_code=etf_code,
                    score_entry=res.score,
                    signals=signals_for_log,
                    h_signal=float(res.h_signal),
                    l_signal=float(res.l_signal),
                    close_signal_day=float(res.close_signal_day),
                )

    def add_pending_entry(self, *, fired: SignalFired) -> None:
        with self._mutex:
            for pe in self._state.pending_entries:
                if pe.etf_code == fired.etf_code and pe.status != "FAILED":
                    return
            pe = PendingEntry(
                etf_code=fired.etf_code,
                signal_date=fired.signal_date.strftime("%Y%m%d"),
                score_entry=float(fired.score),
                phase="phase3",
                h_signal=float(fired.h_signal),
                l_signal=float(fired.l_signal),
                close_signal_day=float(fired.close_signal_day),
                atr_20=float(fired.atr_20),
                is_strong=bool(fired.is_strong),
                expire_date=fired.expire_date.strftime("%Y%m%d"),
                status="PENDING_TRIAL",
                sentiment_score=int(fired.watchlist.sentiment_score),
                profit_ratio=float(fired.watchlist.profit_ratio),
                micro_caution=bool(fired.watchlist.micro_caution),
                vpin_rank=fired.watchlist.vpin_rank,
                ofi_daily=fired.watchlist.ofi_daily,
                vs_max=fired.watchlist.vs_max,
                signals={k: float(v) for k, v in fired.signals.items()},
            )
            _ensure_pending_entry_shape(pe)
            self._state.pending_entries.append(pe)
            self.save()

    def recover_on_startup(self) -> None:
        with self._mutex:
            raw_orders = self._trading.query_orders()
            alive = _order_ids(raw_orders)

            for pe in self._state.pending_entries:
                _ensure_pending_entry_shape(pe)
                if pe.status in ("TRIAL_PLACED", "CONFIRM_PLACED"):
                    oid = pe.trial_order_id if pe.status == "TRIAL_PLACED" else pe.confirm_order_id
                    if oid is None:
                        pe.status = "FAILED"
                    elif int(oid) not in alive:
                        pe.status = "FAILED"

            keep_locks = []
            released_any = False
            for lo in list(self._state.locked_orders):
                if int(lo.order_id) in alive:
                    keep_locks.append(lo)
                else:
                    self._cash.release_cash(int(lo.order_id))
                    released_any = True
            if released_any:
                self._state.locked_orders = keep_locks

            self.save()

    def record_phase3_action(
        self,
        *,
        timestamp: datetime,
        etf_code: str,
        action: str,
        conditions: dict[str, Any],
        order: Optional[OrderRequest],
        rejected_reason: str = "",
    ) -> None:
        if rejected_reason:
            log_phase3_rejected(
                log_path=self._log_path,
                timestamp=timestamp,
                etf_code=etf_code,
                reason=rejected_reason,
                details={"action_taken": action, "conditions": conditions},
            )
            return
        od = None if order is None else {"price": float(order.price), "quantity": int(order.quantity), "amount": float(order.price) * int(order.quantity)}
        log_phase3_decision(
            log_path=self._log_path,
            timestamp=timestamp,
            etf_code=etf_code,
            action=action,
            conditions=conditions,
            order=od,
        )

    def apply_confirm_action(self, *, pe: PendingEntry, act: Any) -> None:
        with self._mutex:
            if act.action == ConfirmActionType.INVALIDATE:
                pe.status = "FAILED"
                self.save()
                return
            if act.action != ConfirmActionType.CONFIRM_ENTRY:
                self.record_phase3_action(
                    timestamp=datetime.now(),
                    etf_code=pe.etf_code,
                    action=act.action.value,
                    conditions=act.conditions,
                    order=None,
                    rejected_reason=act.reason,
                )
                return
            if act.order is None:
                raise AssertionError("confirm action missing order")
            req = act.order
            if req.side != OrderSide.BUY:
                raise AssertionError("entry confirm must be BUY")

            amount = float(req.price) * int(req.quantity)
            if amount <= 0:
                raise AssertionError(f"invalid order amount: {amount}")
            if amount > self._cash.available_cash():
                self.record_phase3_action(
                    timestamp=datetime.now(),
                    etf_code=pe.etf_code,
                    action="REJECTED",
                    conditions=act.conditions,
                    order=req,
                    rejected_reason="INSUFFICIENT_CASH",
                )
                return

            res = self._trading.place_order(req)
            if int(res.order_id) <= 0:
                self.record_phase3_action(
                    timestamp=datetime.now(),
                    etf_code=pe.etf_code,
                    action="REJECTED",
                    conditions=act.conditions,
                    order=req,
                    rejected_reason=(res.error or "PLACE_ORDER_FAILED"),
                )
                return

            oid = int(res.order_id)
            if str(req.remark).upper() == "TRIAL":
                pe.trial_order_id = oid
                pe.trial_qty = int(req.quantity)
                pe.trial_price = float(req.price)
                pe.status = "TRIAL_PLACED"
            else:
                pe.confirm_order_id = oid
                pe.confirm_qty = int(req.quantity)
                pe.confirm_price = float(req.price)
                pe.status = "CONFIRM_PLACED"

            self._state.locked_orders.append(
                LockedOrder(
                    order_id=oid,
                    etf_code=pe.etf_code,
                    side=req.side.value,
                    amount=amount,
                    priority=PRIORITY_TRIAL,
                    strategy_name=req.strategy_name,
                    lock_time=datetime.now().isoformat(timespec="seconds"),
                )
            )
            self._state.frozen_cash = float(self._state.frozen_cash) + amount
            self.save()
            self.record_phase3_action(
                timestamp=datetime.now(),
                etf_code=pe.etf_code,
                action=act.action.value,
                conditions=act.conditions,
                order=req,
                rejected_reason="",
            )
