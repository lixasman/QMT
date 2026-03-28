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
from core.warn_utils import degrade_once, warn_once

from .archiver import archive_near_miss, archive_signal_fired, archive_watchlist
from .entry_logger import log_phase2_score, log_phase3_decision, log_phase3_rejected
from .phase2 import Phase2Result
from .types import ConfirmActionType, SignalFired, WatchlistItem

ENTRY_MUTEX = threading.Lock()

PRIORITY_T0 = 1
PRIORITY_TRIAL = 2
PRIORITY_PREEMPTION = 3
TERMINAL_PENDING_ENTRY_STATUSES = {"FAILED", "CONFIRM_FILLED"}


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


def _extract_position_code(p: Any) -> str:
    if isinstance(p, dict):
        for k in ("etf_code", "stock_code", "code", "symbol", "证券代码"):
            v = p.get(k)
            if v:
                return str(v)
        return ""
    for k2 in ("etf_code", "stock_code", "code", "symbol"):
        v2 = getattr(p, k2, None)
        if v2:
            return str(v2)
    return ""


def _extract_position_total(p: Any) -> int:
    if isinstance(p, dict):
        for k in ("total_qty", "volume", "qty", "position", "current_amount", "total_amount", "持仓", "持仓数量"):
            v = p.get(k)
            if v is None:
                continue
            try:
                return int(v)
            except Exception:
                continue
        return 0
    for k2 in ("total_qty", "volume", "qty", "position", "current_amount", "total_amount"):
        v2 = getattr(p, k2, None)
        if v2 is None:
            continue
        try:
            return int(v2)
        except Exception:
            continue
    return 0


def _position_totals(raw_positions: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in raw_positions:
        code = str(_extract_position_code(p) or "")
        if not code:
            continue
        out[code] = int(_extract_position_total(p))
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

    def _prune_terminal_pending_entries_locked(self, *, etf_code: str) -> bool:
        code = str(etf_code or "")
        before = len(self._state.pending_entries)
        self._state.pending_entries = [
            pe
            for pe in self._state.pending_entries
            if not (str(pe.etf_code or "") == code and str(pe.status or "") in TERMINAL_PENDING_ENTRY_STATUSES)
        ]
        return len(self._state.pending_entries) != before

    def remove_pending_entry(self, *, pe: PendingEntry) -> bool:
        with self._mutex:
            before = len(self._state.pending_entries)
            self._state.pending_entries = [cur for cur in self._state.pending_entries if cur is not pe]
            changed = len(self._state.pending_entries) != before
            if changed:
                self.save()
            return changed

    def upsert_watchlist(self, *, d: datetime, watchlist: list[WatchlistItem]) -> None:
        archive_watchlist(base_dir=self._archive_base_dir, d=d.date(), watchlist=watchlist)

    def record_phase2_result(self, *, timestamp: datetime, etf_code: str, watch: WatchlistItem, res: Phase2Result) -> None:
        diversity_gate = bool(res.signals.get("S_volume", 0.0) > 0.0 or res.signals.get("S_chip_pr", 0.0) > 0.0)
        decision = "SIGNAL_FIRED" if res.is_triggered else "NO_SIGNAL"
        notes: list[str] = []
        if str(getattr(res, "note", "") or "").strip():
            notes.append(str(getattr(res, "note", "")).strip())
        if res.is_strong:
            notes.append("强信号 ≥0.70")
        note = "; ".join(notes)
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
            pruned = self._prune_terminal_pending_entries_locked(etf_code=fired.etf_code)
            for pe in self._state.pending_entries:
                if pe.etf_code == fired.etf_code and pe.status != "FAILED":
                    if pruned:
                        self.save()
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
            try:
                live_totals = _position_totals(list(self._trading.query_positions()))
                positions_snapshot_ok = True
            except Exception:
                live_totals = {}
                positions_snapshot_ok = False
            kept_entries: list[PendingEntry] = []

            for pe in self._state.pending_entries:
                _ensure_pending_entry_shape(pe)
                code = str(getattr(pe, "etf_code", "") or "")
                ps = self._state.positions.get(code)
                local_total = int(getattr(ps, "total_qty", 0) or 0) if ps is not None else 0
                live_total = int(live_totals.get(code, 0)) if bool(positions_snapshot_ok) else 0
                if pe.status == "PENDING_CONFIRM":
                    if bool(positions_snapshot_ok) and int(live_total) <= 0 and int(local_total) <= 0:
                        degrade_once(
                            f"entry_recover_drop_pending_confirm_without_trial:{code}",
                            f"startup recovery marked pending confirm FAILED because no trial position exists. etf={code}",
                        )
                        pe.status = "FAILED"
                        continue
                if pe.status in ("TRIAL_PLACED", "CONFIRM_PLACED"):
                    oid = pe.trial_order_id if pe.status == "TRIAL_PLACED" else pe.confirm_order_id
                    if oid is None:
                        if bool(positions_snapshot_ok) and int(live_total) > 0:
                            if pe.status == "TRIAL_PLACED":
                                pe.status = "PENDING_CONFIRM"
                                kept_entries.append(pe)
                                continue
                            pe.status = "CONFIRM_FILLED"
                            continue
                        if not bool(positions_snapshot_ok):
                            kept_entries.append(pe)
                            continue
                        degrade_once(
                            f"entry_recover_missing_order_id:{str(pe.etf_code)}:{str(pe.status)}",
                            f"startup recovery marked pending entry FAILED due to missing order id. etf={pe.etf_code} status={pe.status}",
                        )
                        pe.status = "FAILED"
                    elif int(oid) not in alive:
                        if bool(positions_snapshot_ok) and int(live_total) > 0:
                            if pe.status == "TRIAL_PLACED":
                                pe.status = "PENDING_CONFIRM"
                                kept_entries.append(pe)
                                continue
                            degrade_once(
                                f"entry_recover_confirm_filled:{str(pe.etf_code)}:{int(oid)}",
                                f"startup recovery converted placed confirm entry to terminal filled because broker already shows position. etf={pe.etf_code} order_id={int(oid)}",
                            )
                            pe.status = "CONFIRM_FILLED"
                            continue
                        if not bool(positions_snapshot_ok):
                            kept_entries.append(pe)
                            continue
                        degrade_once(
                            f"entry_recover_order_not_alive:{str(pe.etf_code)}:{int(oid)}",
                            f"startup recovery marked pending entry FAILED because order is not alive. etf={pe.etf_code} order_id={int(oid)}",
                        )
                        pe.status = "FAILED"
                kept_entries.append(pe)

            self._state.pending_entries = kept_entries

            keep_locks = []
            released_any = False
            for lo in list(self._state.locked_orders):
                if int(lo.order_id) in alive:
                    keep_locks.append(lo)
                else:
                    degrade_once(
                        f"entry_recover_release_lock:{int(lo.order_id)}",
                        (
                            "startup recovery released stale locked cash because order is missing in broker alive set. "
                            f"order_id={int(lo.order_id)} etf={str(lo.etf_code)}"
                        ),
                    )
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
