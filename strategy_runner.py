from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from typing import Any

from core.buy_order_config import get_aggressive_buy_multiplier, get_aggressive_buy_use_ask1
from core.cash_manager import CashManager
from core.constants import TICK_SIZE
from core.enums import ActionType, DataQuality, FSMState, OrderStatus
from core.interfaces import Bar, DataAdapter, OrderResult, TradingAdapter
from backtest.sentiment_proxy import compute_sentiment_proxy
from backtest.corporate_actions import apply_price_factor_to_pending_entries, apply_price_factor_to_position_state, infer_split_price_factor
from backtest.universe import DEFAULT_UNIVERSE_CODES
from core.state_manager import StateManager
from core.time_utils import is_trading_time
from core.warn_utils import alert_once, degrade_once, warn_once
from core.validators import assert_action_allowed
from entry.entry_fsm import EntryFSM
from entry.high_chase import (
    decode_high_chase_signal_rows,
    encode_high_chase_signal_rows,
    normalize_high_chase_signal_source,
    phase2_signal_reference_price,
    remember_high_chase_signal,
    scale_high_chase_signal_rows,
    should_block_high_chase_signal,
)
from entry.pathb_config import (
    get_pathb_atr_mult,
    get_pathb_chip_min,
    get_pathb_require_trend,
    get_pathb_require_vwap_strict,
)
from entry.phase2 import evaluate_phase2
from entry.phase2_config import get_phase2_continuation_config, get_phase2_score_threshold
from entry.phase3_confirmer import Phase3Confirmer, Phase3Context
from entry.types import ConfirmActionType, WatchlistItem
from entry.vwap_tracker import VwapTracker
from exit.exit_config import (
    get_exit_k_chip_decay,
    get_exit_k_normal,
    get_exit_k_reduced,
    get_exit_layer1_sell_discount,
    get_exit_layer1_use_stop_price,
    get_exit_layer2_score_log,
    get_exit_layer2_threshold,
)
from exit.exit_fsm import ExitFSM
from position.position_fsm import PositionFSM
from strategy_config import StrategyConfig


@dataclass(frozen=True)
class _Fill:
    filled_qty: int
    avg_price: float


def _safe_float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except Exception:
        return 0.0


def _to_trade_day_yyyymmdd(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y%m%d")
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return s
    if len(s) >= 10 and "-" in s:
        s2 = s[:10].replace("-", "")
        if len(s2) == 8 and s2.isdigit():
            return s2
    try:
        num = float(s)
    except Exception:
        return ""
    if num <= 0:
        return ""
    try:
        if num >= 1_000_000_000_000:
            return datetime.fromtimestamp(num / 1000.0).strftime("%Y%m%d")
        if num >= 1_000_000_000:
            return datetime.fromtimestamp(num).strftime("%Y%m%d")
    except Exception:
        return ""
    return ""


def _iter_divid_factor_rows(raw: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    if raw is None:
        return out
    if hasattr(raw, "iterrows"):
        try:
            for idx, row in raw.iterrows():  # type: ignore[attr-defined]
                if hasattr(row, "to_dict"):
                    rec = dict(row.to_dict())
                else:
                    rec = dict(row)
                rec["_index"] = idx
                out.append(rec)
            return out
        except Exception:
            return out
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(dict(item))
        return out
    if isinstance(raw, dict):
        out.append(dict(raw))
    return out


def _extract_split_price_factor_from_divid_factors(raw: object, *, trade_day: str) -> tuple[float, float] | None:
    for rec in _iter_divid_factor_rows(raw):
        day = _to_trade_day_yyyymmdd(rec.get("_index") or rec.get("date") or rec.get("time"))
        if str(day) != str(trade_day):
            continue
        dr = _safe_float(rec.get("dr"))
        if float(dr) <= 0:
            continue
        price_factor = infer_split_price_factor(prev_close=1.0, next_open=float(1.0 / float(dr)))
        if price_factor is None:
            continue
        return float(price_factor), float(dr)
    return None


def _extract_fill(res: OrderResult, *, fallback_qty: int) -> _Fill:
    qty = int(res.filled_qty)
    px = 0.0 if res.avg_price is None else float(res.avg_price)
    if qty > 0 and px > 0:
        return _Fill(filled_qty=int(qty), avg_price=float(px))

    raw = res.raw
    if isinstance(raw, dict):
        qty2 = raw.get("filled_qty") or raw.get("filled") or raw.get("deal_amount") or raw.get("成交数量") or 0
        px2 = raw.get("avg_price") or raw.get("deal_price") or raw.get("成交均价") or raw.get("price") or 0.0
        q = int(_safe_float(qty2))
        p = float(_safe_float(px2))
        if q > 0 and p > 0:
            return _Fill(filled_qty=int(q), avg_price=float(p))
    else:
        qty3 = getattr(raw, "filled_qty", None) or getattr(raw, "deal_amount", None) or getattr(raw, "filled", None)
        px3 = getattr(raw, "avg_price", None) or getattr(raw, "deal_price", None) or getattr(raw, "price", None)
        q = int(_safe_float(qty3))
        p = float(_safe_float(px3))
        if q > 0 and p > 0:
            return _Fill(filled_qty=int(q), avg_price=float(p))

    degrade_once(
        f"extract_fill_fallback:{int(getattr(res, 'order_id', 0) or 0)}",
        (
            "order fill fields are missing/unparseable, using fallback fill "
            f"qty={int(max(0, int(fallback_qty)))} avg_price={float(max(0.0, px))} "
            f"status={getattr(res, 'status', '')}"
        ),
    )
    return _Fill(filled_qty=int(max(0, int(fallback_qty))), avg_price=float(max(0.0, px)))


class StrategyRunner:
    def __init__(
        self,
        config: StrategyConfig,
        *,
        data: Optional[DataAdapter] = None,
        trading: Optional[TradingAdapter] = None,
        state_manager: Optional[StateManager] = None,
        t0_engine: Optional[Any] = None,
    ) -> None:
        self._cfg = config
        self._logger = logging.getLogger("strategy")

        Path("data/state").mkdir(parents=True, exist_ok=True)
        Path("data/logs").mkdir(parents=True, exist_ok=True)

        self._data = data or self._build_data_adapter()
        self._trading = trading or self._build_trading_adapter()

        self._sm = state_manager or StateManager(self._cfg.state_path)
        self._state = self._sm.load()
        self._apply_shared_strategy_config()

        self._entry_fsm = EntryFSM(
            state_manager=self._sm,
            data=self._data,
            trading=self._trading,
            state=self._state,
            log_path=self._cfg.entry_log_path,
        )
        self._exit_fsm = ExitFSM(
            state_manager=self._sm,
            data=self._data,
            trading=self._trading,
            state=self._state,
            log_path=self._cfg.exit_log_path,
            layer1_sell_discount=getattr(self, "_exit_layer1_sell_discount", None),
            layer1_use_stop_price=getattr(self, "_exit_layer1_use_stop_price", None),
            layer2_threshold=getattr(self, "_exit_layer2_threshold", None),
            layer2_score_log=getattr(self, "_exit_layer2_score_log", None),
            aggressive_buy_multiplier=getattr(self, "_aggressive_buy_multiplier", None),
            aggressive_buy_use_ask1=getattr(self, "_aggressive_buy_use_ask1", None),
            enable_t0=bool(getattr(self._cfg, "enable_t0", False)),
        )
        self._pos_fsm = PositionFSM(
            state_manager=self._sm,
            data=self._data,
            trading=self._trading,
            state=self._state,
            log_path=self._cfg.position_log_path,
            t0_log_path=self._cfg.t0_log_path,
            t0_engine=t0_engine,
            enable_t0=bool(getattr(self._cfg, "enable_t0", False)),
        )

        self._vwap: dict[str, VwapTracker] = {}
        self._prev_snap: dict[str, object] = {}
        self._ext_factors: dict[str, dict[str, object]] = {}
        self._day_watch_codes: list[str] = []
        from integrations.chip_history import ChipDPCHistory

        self._dpc_history = ChipDPCHistory(history_dir=Path("data/state/dpc"))
        self._nav_estimate_cache_key: object | None = None
        self._nav_estimate_cache_value: float | None = None

        self._pos_fsm.recover_on_startup()
        self._entry_fsm.recover_on_startup()
        self._exit_fsm.recover_on_startup()
        self._replay_persisted_exit_intents(now=datetime.now(), source="startup")
        self._startup_recovered_codes = set(getattr(self._pos_fsm, "_startup_recovered_codes", set()) or set())
        self._startup_recovered_cost_codes = set(getattr(self._pos_fsm, "_startup_recovered_cost_codes", set()) or set())

    @property
    def state_manager(self) -> StateManager:
        return self._sm

    @property
    def state(self):
        return self._state

    def _apply_shared_strategy_config(self) -> None:
        self._phase2_score_threshold = float(get_phase2_score_threshold())
        self._phase2_continuation_cfg = dict(get_phase2_continuation_config())
        self._pathb_atr_mult = float(get_pathb_atr_mult())
        self._pathb_chip_min = float(get_pathb_chip_min())
        self._pathb_require_trend = bool(get_pathb_require_trend())
        self._pathb_require_vwap_strict = bool(get_pathb_require_vwap_strict())
        min_pct_raw = getattr(self._cfg, "exit_atr_pct_min", None)
        max_pct_raw = getattr(self._cfg, "exit_atr_pct_max", None)
        min_pct = float(min_pct_raw) if min_pct_raw is not None and float(min_pct_raw) > 0 else None
        max_pct = float(max_pct_raw) if max_pct_raw is not None and float(max_pct_raw) > 0 else None
        if min_pct is not None and max_pct is not None and float(min_pct) > float(max_pct):
            min_pct, max_pct = max_pct, min_pct
        self._exit_atr_pct_min = min_pct
        self._exit_atr_pct_max = max_pct
        self._exit_k_normal = float(get_exit_k_normal())
        self._exit_k_chip_decay = float(get_exit_k_chip_decay())
        self._exit_k_reduced = float(get_exit_k_reduced())
        self._exit_layer1_sell_discount = float(get_exit_layer1_sell_discount())
        self._exit_layer1_use_stop_price = bool(get_exit_layer1_use_stop_price())
        exit_layer2_threshold = getattr(self._cfg, "exit_layer2_threshold", None)
        self._exit_layer2_threshold = (
            float(get_exit_layer2_threshold())
            if exit_layer2_threshold is None
            else float(exit_layer2_threshold)
        )
        self._exit_layer2_score_log = bool(get_exit_layer2_score_log())
        self._aggressive_buy_multiplier = float(get_aggressive_buy_multiplier())
        self._aggressive_buy_use_ask1 = bool(get_aggressive_buy_use_ask1())
        self._exit_k_accel_enabled = bool(getattr(self._cfg, "exit_k_accel_enabled", False))
        self._exit_k_accel_step_pct = float(getattr(self._cfg, "exit_k_accel_step_pct", 0.05) or 0.05)
        self._exit_k_accel_step_k = float(getattr(self._cfg, "exit_k_accel_step_k", 0.2) or 0.2)
        self._exit_k_accel_k_min = float(getattr(self._cfg, "exit_k_accel_k_min", 1.0) or 1.0)
        self._enable_t0 = bool(getattr(self._cfg, "enable_t0", False))
        self._trade_fee_rate = 0.000085

    def _exit_k_accel(self) -> tuple[bool, float, float, float]:
        return (
            bool(getattr(self, "_exit_k_accel_enabled", False)),
            float(getattr(self, "_exit_k_accel_step_pct", 0.05) or 0.05),
            float(getattr(self, "_exit_k_accel_step_k", 0.2) or 0.2),
            float(getattr(self, "_exit_k_accel_k_min", 1.0) or 1.0),
        )

    def _t0_enabled(self) -> bool:
        return bool(getattr(self, "_enable_t0", False))

    def _trade_fee(self, *, price: float, qty: int) -> float:
        if int(qty) <= 0 or float(price) <= 0:
            return 0.0
        return float(float(price) * int(qty) * float(getattr(self, "_trade_fee_rate", 0.0) or 0.0))

    def _sync_asset_after_trade_fill(self, *, fallback_cash: float, context: str) -> None:
        try:
            self._sync_asset()
        except Exception as e:
            degrade_once(
                f"trade_fill_asset_sync_failed:{context}",
                f"trade fill asset sync failed; fallback to local cash. context={context} err={repr(e)}",
            )
            self._state.cash = float(max(0.0, float(fallback_cash)))
            self._sm.save(self._state)

    def _confirm_persisted_exit_intent(self, *, order_id: int, etf_code: str, source: str) -> OrderResult | None:
        try:
            res = self._trading.confirm_order(int(order_id), timeout_s=0.0)
        except Exception as e:
            alert_once(
                f"{source}_exit_intent_confirm_failed:{str(etf_code)}:{int(order_id)}",
                (
                    "persisted exit order intent reconciliation failed to confirm broker order status; "
                    "keep intent for later reconciliation. "
                    f"source={source} etf={etf_code} order_id={int(order_id)} err={repr(e)}"
                ),
            )
            return None
        return res

    def _replay_persisted_exit_intents(self, *, now: datetime, source: str) -> None:
        intents = dict(getattr(self._state, "exit_order_intents", {}) or {})
        if not intents:
            return
        replayed = 0
        dropped = 0
        for oid_raw, intent in list(intents.items()):
            try:
                oid = int(oid_raw)
            except Exception:
                self._state.exit_order_intents.pop(str(oid_raw), None)
                alert_once(
                    f"{source}_exit_intent_invalid:{str(oid_raw)}",
                    f"{source} dropped invalid persisted exit intent key. raw_order_id={repr(oid_raw)}",
                )
                dropped += 1
                continue
            if not isinstance(intent, dict):
                self._state.exit_order_intents.pop(str(oid), None)
                alert_once(
                    f"{source}_exit_intent_invalid:{int(oid)}",
                    f"{source} dropped invalid persisted exit intent payload. order_id={int(oid)}",
                )
                dropped += 1
                continue
            action = str(intent.get("action") or "").strip()
            etf_code = str(intent.get("etf_code") or "").strip()
            locked_qty = max(0, int(intent.get("locked_qty") or 0))
            expected_remaining_qty = max(0, int(intent.get("expected_remaining_qty") or 0))
            if not action or not etf_code:
                self._state.exit_order_intents.pop(str(oid), None)
                alert_once(
                    f"{source}_exit_intent_invalid:{int(oid)}",
                    (
                        f"{source} dropped persisted exit intent missing required fields. "
                        f"order_id={int(oid)} action={action or '<empty>'} etf={etf_code or '<empty>'}"
                    ),
                )
                dropped += 1
                continue

            def _query_broker_total() -> int | None:
                try:
                    broker_total, _broker_sellable, _broker_locked = self._pos_fsm.query_balances(etf_code=str(etf_code))
                except Exception as e:
                    alert_once(
                        f"{source}_exit_intent_query_failed:{str(etf_code)}:{int(oid)}",
                        (
                            "persisted exit order intent reconciliation failed to query broker balances; "
                            "keep intent for later reconciliation. "
                            f"source={source} etf={etf_code} order_id={int(oid)} action={action} err={repr(e)}"
                        ),
                    )
                    return None
                return max(0, int(broker_total))

            current_total = _query_broker_total()
            if current_total is None:
                continue
            if int(current_total) != int(expected_remaining_qty):
                confirm_res = self._confirm_persisted_exit_intent(order_id=int(oid), etf_code=str(etf_code), source=str(source))
                if confirm_res is None:
                    continue
                if confirm_res.status in (OrderStatus.CANCELED, OrderStatus.REJECTED):
                    alert_once(
                        f"{source}_exit_intent_terminal_without_fill:{str(etf_code)}:{int(oid)}",
                        (
                            "persisted exit order intent reached terminal non-fill status; dropping stale intent without local replay. "
                            f"source={source} etf={etf_code} order_id={int(oid)} action={action} "
                            f"status={str(confirm_res.status)} expected_remaining_qty={int(expected_remaining_qty)}"
                        ),
                    )
                    self._state.exit_order_intents.pop(str(oid), None)
                    dropped += 1
                    continue
                current_total = _query_broker_total()
                if current_total is None:
                    continue
            try:
                current_total_int = int(current_total)
            except Exception:
                current_total_int = max(0, int(current_total or 0))
            if int(current_total_int) != int(expected_remaining_qty):
                alert_once(
                    f"{source}_exit_intent_unresolved:{str(etf_code)}:{int(oid)}",
                    (
                        "persisted exit order intent remains unresolved after broker recheck; keep intent for later reconciliation. "
                        f"source={source} etf={etf_code} order_id={int(oid)} action={action} "
                        f"current_total={int(current_total_int)} expected_remaining_qty={int(expected_remaining_qty)}"
                    ),
                )
                continue

            ps = self._state.positions.get(str(etf_code))
            if ps is None and int(current_total_int) > 0:
                ps = self._pos_fsm.upsert_position(etf_code=str(etf_code))
            if ps is not None:
                ps.total_qty = int(current_total_int)
                ps.same_day_buy_qty = min(max(0, int(getattr(ps, "same_day_buy_qty", 0) or 0)), int(current_total_int))

            if action == "FULL_EXIT":
                if ps is not None:
                    if int(locked_qty) > 0 and int(current_total_int) > 0:
                        self._exit_fsm._append_pending_sell_locked(ps=ps, locked_qty=int(locked_qty), now=now)
                        ps.t0_frozen = True
                    if ps.state == FSMState.S0_IDLE:
                        ps.state = FSMState.S2_BASE
                    self._pos_fsm.on_layer1_clear(
                        etf_code=str(etf_code),
                        sold_qty=max(0, int(current_total_int) - int(locked_qty)),
                    )
            elif action == "LAYER2_REDUCE":
                if ps is not None:
                    if ps.state == FSMState.S0_IDLE:
                        ps.state = FSMState.S2_BASE
                    if int(current_total_int) > 0:
                        self._pos_fsm.on_layer2_reduce(etf_code=str(etf_code), sold_qty=0)
                    else:
                        self._pos_fsm.on_layer1_clear(etf_code=str(etf_code), sold_qty=0)
            else:
                alert_once(
                    f"{source}_exit_intent_invalid_action:{str(etf_code)}:{int(oid)}",
                    f"{source} dropped unsupported persisted exit intent action. etf={etf_code} order_id={int(oid)} action={action}",
                )
                self._state.exit_order_intents.pop(str(oid), None)
                dropped += 1
                continue

            self._state.exit_order_intents.pop(str(oid), None)
            replayed += 1

        if replayed > 0:
            try:
                self._sync_asset()
            except Exception as e:
                degrade_once(
                    f"{source}_exit_intent_asset_sync_failed",
                    f"{source} replayed exit intents but asset sync failed. err={repr(e)}",
                )
                self._sm.save(self._state)
        elif dropped > 0:
            self._sm.save(self._state)

    def _phase2_no_reentry_after_confirm_enabled(self) -> bool:
        return bool(getattr(self._cfg, "phase2_no_reentry_after_confirm", False))

    def _phase2_skip_high_chase_after_first_signal_enabled(self) -> bool:
        return bool(getattr(self._cfg, "phase2_skip_high_chase_after_first_signal", False))

    def _phase2_high_chase_signal_source(self) -> str:
        return normalize_high_chase_signal_source(getattr(self._cfg, "phase2_high_chase_signal_source", "all_signals"))

    def _phase2_high_chase_lookback_days(self) -> int:
        return int(max(1, int(getattr(self._cfg, "phase2_high_chase_lookback_days", 60) or 60)))

    def _phase2_high_chase_max_rise(self) -> float:
        return float(max(0.0, float(getattr(self._cfg, "phase2_high_chase_max_rise", 0.15) or 0.15)))

    def _phase2_high_chase_uses_all_signals(self) -> bool:
        return self._phase2_skip_high_chase_after_first_signal_enabled() and self._phase2_high_chase_signal_source() == "all_signals"

    def _phase2_high_chase_uses_missed_executable(self) -> bool:
        return self._phase2_skip_high_chase_after_first_signal_enabled() and self._phase2_high_chase_signal_source() == "missed_executable"

    def _phase2_high_chase_logger(self) -> logging.Logger:
        return getattr(self, "_bt_logger", getattr(self, "_logger", logging.getLogger("strategy")))

    def _get_phase2_high_chase_signal_rows(self, *, code: str) -> list[tuple[date, float]]:
        key = str(code or "").strip().upper()
        if not key:
            return []
        raw_map = getattr(self._state, "phase2_high_chase_signals", None)
        if not isinstance(raw_map, dict):
            raw_map = {}
            self._state.phase2_high_chase_signals = raw_map
        return decode_high_chase_signal_rows(raw_map.get(key))

    def _set_phase2_high_chase_signal_rows(self, *, code: str, rows: list[tuple[date, float]]) -> None:
        key = str(code or "").strip().upper()
        if not key:
            return
        raw_map = getattr(self._state, "phase2_high_chase_signals", None)
        if not isinstance(raw_map, dict):
            raw_map = {}
            self._state.phase2_high_chase_signals = raw_map
        if rows:
            raw_map[key] = encode_high_chase_signal_rows(rows)
        else:
            raw_map.pop(key, None)

    def _rescale_phase2_high_chase_signal_rows(self, *, code: str, price_factor: float) -> int:
        rows = self._get_phase2_high_chase_signal_rows(code=code)
        if not rows:
            return 0
        scaled = scale_high_chase_signal_rows(rows=rows, price_factor=float(price_factor))
        self._set_phase2_high_chase_signal_rows(code=code, rows=scaled)
        return 1

    def _remember_phase2_high_chase_signal(self, *, now: datetime, etf_code: str, ref_price: float) -> bool:
        if not self._phase2_skip_high_chase_after_first_signal_enabled():
            return False
        code = str(etf_code or "").strip().upper()
        px = float(ref_price)
        if not code or px <= 0:
            return False
        rows, added = remember_high_chase_signal(
            rows=self._get_phase2_high_chase_signal_rows(code=code),
            now_day=now.date(),
            ref_price=float(px),
            lookback_days=self._phase2_high_chase_lookback_days(),
        )
        self._set_phase2_high_chase_signal_rows(code=code, rows=rows)
        return bool(added)

    def _remember_phase2_missed_executable_signal(self, *, now: datetime, pe, act) -> bool:
        if not self._phase2_high_chase_uses_missed_executable():
            return False
        if getattr(act, "action", None) != ConfirmActionType.CONFIRM_ENTRY:
            return False
        order = getattr(act, "order", None)
        if order is None:
            return False
        qty = int(getattr(order, "quantity", 0) or 0)
        price = float(getattr(order, "price", 0.0) or 0.0)
        amount = float(price) * int(qty)
        available_cash = float(self._entry_fsm.cash.available_cash())
        if qty <= 0 or price <= 0 or amount <= 0:
            return False
        if float(amount) <= float(available_cash) + 1e-12:
            return False
        code = str(getattr(pe, "etf_code", "") or "")
        ref_price = phase2_signal_reference_price(
            close_signal_day=float(getattr(pe, "close_signal_day", 0.0) or 0.0),
            h_signal=float(getattr(pe, "h_signal", 0.0) or 0.0),
        )
        added = self._remember_phase2_high_chase_signal(now=now, etf_code=code, ref_price=float(ref_price))
        if added:
            self._phase2_high_chase_logger().info(
                "phase3 missed-executable signal remembered | etf=%s needed=%.6f available=%.6f ref_price=%.6f",
                str(code),
                float(amount),
                float(available_cash),
                float(ref_price),
            )
        return bool(added)

    def _remember_phase2_blocked_continuation_signal(
        self,
        *,
        now: datetime,
        etf_code: str,
        close_signal_day: float,
        h_signal: float,
        note: str,
    ) -> bool:
        if not self._phase2_high_chase_uses_all_signals():
            return False
        note_text = str(note or "").strip()
        if not note_text.startswith("continuation_blocked"):
            return False
        ref_price = phase2_signal_reference_price(
            close_signal_day=float(close_signal_day),
            h_signal=float(h_signal),
        )
        added = self._remember_phase2_high_chase_signal(
            now=now,
            etf_code=str(etf_code),
            ref_price=float(ref_price),
        )
        if added:
            self._phase2_high_chase_logger().info(
                "phase2 high-chase seed remembered | etf=%s source=blocked_continuation ref_price=%.6f note=%s",
                str(etf_code),
                float(ref_price),
                note_text,
            )
        return bool(added)

    def _should_block_phase2_high_chase_signal(self, *, now: datetime, etf_code: str, ref_price: float) -> tuple[bool, str]:
        if not self._phase2_skip_high_chase_after_first_signal_enabled():
            return False, ""
        code = str(etf_code or "").strip().upper()
        px = float(ref_price)
        if not code or px <= 0:
            return False, ""
        rows, blocked, reason = should_block_high_chase_signal(
            rows=self._get_phase2_high_chase_signal_rows(code=code),
            now_day=now.date(),
            ref_price=float(px),
            lookback_days=self._phase2_high_chase_lookback_days(),
            max_rise=self._phase2_high_chase_max_rise(),
        )
        self._set_phase2_high_chase_signal_rows(code=code, rows=rows)
        return bool(blocked), str(reason)

    def _should_block_phase2_entry_after_signal(self, *, now: datetime, etf_code: str) -> tuple[bool, str]:
        _ = now
        code = str(etf_code or "").strip().upper()
        if not code or not self._phase2_no_reentry_after_confirm_enabled():
            return False, ""
        ps = self._state.positions.get(code)
        if ps is None:
            return False, ""
        qty = int(getattr(ps, "total_qty", 0) or 0)
        if qty <= 0:
            return False, ""
        st = ps.state
        if st in (FSMState.S2_BASE, FSMState.S3_SCALED, FSMState.S4_FULL, FSMState.S5_REDUCED):
            return True, "no_reentry_after_confirm"
        return False, ""

    def run_day(
        self,
        *,
        wait_for_market: bool = True,
        max_ticks: Optional[int] = None,
        now_provider: Callable[[], datetime] = datetime.now,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        now = now_provider()
        self._logger.info("strategy day start | now=%s", now.isoformat(timespec="seconds"))
        if wait_for_market:
            while not is_trading_time(now) and now.time() < datetime.strptime("15:10", "%H:%M").time():
                sleep_fn(min(5.0, float(self._cfg.tick_interval_s)))
                now = now_provider()

        pre_open_now = now_provider()
        self._pre_open(now=pre_open_now)
        self._run_opening_gap_checks(now=pre_open_now)
        self._intraday_loop(now_provider=now_provider, sleep_fn=sleep_fn, max_ticks=max_ticks)
        self._post_close(now=now_provider())
        self._logger.info("strategy day end | now=%s", now_provider().isoformat(timespec="seconds"))

    def _build_data_adapter(self) -> DataAdapter:
        from core.adapters.data_adapter import XtDataAdapter

        return XtDataAdapter()

    def _log_watchlist_snapshot(self, *, now: datetime, watchlist: list[WatchlistItem]) -> None:
        if not watchlist:
            self._logger.warning("watchlist open snapshot | date=%s count=0 codes=", now.date().isoformat())
            return

        codes = ",".join(str(it.etf_code) for it in watchlist)
        self._logger.warning("watchlist open snapshot | date=%s count=%s codes=%s", now.date().isoformat(), len(watchlist), codes)
        for i, it in enumerate(watchlist, start=1):
            self._logger.info(
                "watchlist item %02d | code=%s sentiment=%s profit_ratio=%.2f micro_caution=%s",
                i,
                str(it.etf_code),
                int(it.sentiment_score),
                float(it.profit_ratio),
                bool(it.micro_caution),
            )

    def _heartbeat_monitor_codes(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def _add(code: object) -> None:
            s = str(code or "").strip()
            if not s or s in seen:
                return
            out.append(s)
            seen.add(s)

        for c in list(self._day_watch_codes):
            _add(c)
        for c in list(self._state.positions.keys()):
            _add(c)
        for pe in list(self._state.pending_entries):
            _add(getattr(pe, "etf_code", ""))
        return out

    def _log_heartbeat_prices(self, *, now: datetime) -> None:
        codes = self._heartbeat_monitor_codes()
        if not codes:
            self._logger.warning("heartbeat | now=%s monitor_count=0 prices=", now.isoformat(timespec="seconds"))
            return

        parts: list[str] = []
        for code in codes:
            try:
                snap = self._data.get_snapshot(str(code))
                parts.append(f"{str(code)}:{float(snap.last_price):.3f}")
            except Exception as e:
                parts.append(f"{str(code)}:ERR")
                self._logger.warning("heartbeat snapshot failed | code=%s err=%s", str(code), repr(e))
        self._logger.warning(
            "heartbeat | now=%s monitor_count=%s prices=%s",
            now.isoformat(timespec="seconds"),
            len(codes),
            ",".join(parts),
        )

    def _build_trading_adapter(self) -> TradingAdapter:
        if self._cfg.trading_adapter_type == "gui":
            from core.adapters.gui_trading_adapter import GuiTradingAdapter

            import easytrader  # type: ignore

            client = easytrader.use(self._cfg.easytrader_broker)
            prep = getattr(client, "prepare", None)
            if callable(prep):
                cfg_path = str(Path("data") / "easytrader.json")
                try:
                    prep(config_path=cfg_path)
                except Exception as e:
                    warn_once("gui_prepare_failed", f"GUI: easytrader.prepare 失败，已降级继续运行: {cfg_path} err={repr(e)}")
            return GuiTradingAdapter(
                client,
                gui_ops_limit=int(self._cfg.gui_ops_limit),
                freeze_threshold=int(self._cfg.gui_freeze_threshold),
            )

        from core.adapters.xt_trading_adapter import XtTradingAdapter

        try:
            from xtquant import xttrader  # type: ignore
        except Exception as e:
            raise RuntimeError("xtquant.xttrader is not available") from e

        path = str(self._cfg.xt_trader_path).strip()
        account_id = str(self._cfg.xt_account_id).strip()
        session = str(self._cfg.xt_session_id).strip()
        if not path or not account_id or not session:
            raise RuntimeError("xt trading requires xt_path, xt_account, xt_session")

        trader_cls = getattr(xttrader, "XtQuantTrader", None)
        if not callable(trader_cls):
            raise RuntimeError("xttrader missing XtQuantTrader")
        trader = trader_cls(path, int(session))

        start = getattr(trader, "start", None)
        connect = getattr(trader, "connect", None)
        if callable(start):
            start()
        if callable(connect):
            connect()
        acc = None
        acct_cls = getattr(xttrader, "StockAccount", None)
        if callable(acct_cls):
            acc = acct_cls(account_id)
            sub = getattr(trader, "subscribe", None)
            if callable(sub):
                try:
                    sub(acc)
                except Exception as e:
                    warn_once("xt_subscribe_failed", f"XT: subscribe 失败，已降级继续: account={account_id} err={repr(e)}")
        else:
            acct2 = None
            try:
                from xtquant import xttype  # type: ignore
            except Exception as e:
                degrade_once("xt_stock_account_import_failed", f"XT fallback account class import failed; account binding may be skipped. err={repr(e)}")
                xttype = None  # type: ignore[assignment]
            if xttype is not None:
                acct2 = getattr(xttype, "StockAccount", None)
            if callable(acct2):
                acc = acct2(str(account_id))
                sub = getattr(trader, "subscribe", None)
                if callable(sub):
                    try:
                        sub(acc)
                    except Exception as e:
                        warn_once("xt_subscribe_failed", f"XT: subscribe 失败，已降级继续: account={account_id} err={repr(e)}")
            else:
                degrade_once(
                    "xt_stock_account_missing",
                    "XT StockAccount class not found in xttrader/xttype; trading adapter will run without explicit account binding",
                )

        return XtTradingAdapter(trader, account=acc)

    def _sync_asset(self) -> None:
        raw = self._trading.query_asset()
        if not isinstance(raw, dict):
            degrade_once("asset_query_non_dict", f"query_asset returned non-dict payload: type={type(raw).__name__}")
        if isinstance(raw, dict):
            cash = raw.get("cash") if raw.get("cash") is not None else raw.get("available_cash")
            nav = raw.get("nav") if raw.get("nav") is not None else raw.get("total_asset") or raw.get("asset")
            if cash is None:
                degrade_once("asset_cash_missing", f"cash field missing in query_asset payload keys={sorted(list(raw.keys()))[:20]}")
            if nav is None:
                degrade_once("asset_nav_missing", f"nav/asset field missing in query_asset payload keys={sorted(list(raw.keys()))[:20]}")
            if cash is not None:
                self._state.cash = float(_safe_float(cash))
            if nav is not None:
                self._state.nav = float(_safe_float(nav))
        self._sm.save(self._state)

    def _merge_watch_codes(self, *groups: list[str] | tuple[str, ...]) -> list[str]:
        from integrations.watchlist_loader import normalize_etf_code

        out: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for raw in list(group):
                s = str(raw or "").strip()
                if not s:
                    continue
                try:
                    code = str(normalize_etf_code(s))
                except Exception:
                    code = s.upper()
                if not code or code in seen:
                    continue
                seen.add(code)
                out.append(code)
        return out

    def _default_watch_auto_codes(self) -> list[str]:
        return self._merge_watch_codes(list(DEFAULT_UNIVERSE_CODES), list(self._cfg.watchlist_etf_codes))

    def _resolve_watch_codes(self, *, now: datetime) -> list[str]:
        raw = [str(x).strip() for x in self._cfg.watchlist_etf_codes if str(x).strip()]
        if not bool(self._cfg.watch_auto):
            return self._merge_watch_codes(raw)
        baseline_codes = self._default_watch_auto_codes()
        require_hot = bool(getattr(self._cfg, "watch_auto_require_hot_csv", False))
        try:
            from integrations.premarket_prep import finintel_hot_csv_path, prev_trading_date
        except Exception as e:
            if require_hot:
                degrade_once(
                    "watch_auto_import_failed_required_hot",
                    f"watch_auto import failed and hot csv is required; return empty watchlist. err={repr(e)}",
                )
                return []
            degrade_once("watch_auto_import_failed", f"watch_auto import failed; fallback to baseline universe. err={repr(e)}")
            return baseline_codes
        t1 = prev_trading_date(now)
        if not t1:
            if require_hot:
                degrade_once(
                    "watch_auto_prev_trade_date_missing_required_hot",
                    "watch_auto cannot resolve T-1 trading date and hot csv is required; return empty watchlist",
                )
                return []
            degrade_once("watch_auto_prev_trade_date_missing", "watch_auto cannot resolve T-1 trading date; fallback to baseline universe")
            return baseline_codes
        p = finintel_hot_csv_path(day=t1)
        if not p.exists():
            if require_hot:
                degrade_once(
                    "watch_auto_hot_csv_missing_required_hot",
                    f"watch_auto hot csv missing and required; return empty watchlist. path={p}",
                )
                return []
            degrade_once("watch_auto_hot_csv_missing", f"watch_auto hot csv missing; fallback to baseline universe. path={p}")
            return baseline_codes
        self._logger.info("watch_auto hot list selected | t_minus_1=%s path=%s", str(t1), str(p))
        hot: list[str] = []
        try:
            import csv

            with p.open("r", encoding="utf-8-sig", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    c = str(row.get("code") or "").strip()
                    if c:
                        hot.append(c)
        except Exception as e:
            if require_hot:
                degrade_once(
                    "watch_auto_hot_csv_parse_failed_required_hot",
                    f"watch_auto hot csv parse failed and hot csv is required; return empty watchlist. path={p} err={repr(e)}",
                )
                return []
            degrade_once("watch_auto_hot_csv_parse_failed", f"watch_auto hot csv parse failed; ignore hot list. path={p} err={repr(e)}")
            hot = []
        return self._merge_watch_codes(baseline_codes, hot)

    def _allow_phase2_candidate(self, *, now: datetime, item: WatchlistItem) -> tuple[bool, str]:
        _ = now
        _ = item
        return True, ""

    def _apply_sentiment_proxy_for_code(self, *, code: str) -> tuple[int, float]:
        from integrations.watchlist_loader import normalize_etf_code

        score100, score01 = 50, 0.5
        bars_count = 0
        try:
            bars = self._data.get_bars(str(code), "1d", 8)
            bars_count = len(bars)
            score100, score01 = compute_sentiment_proxy(bars)
            if int(bars_count) < 6:
                degrade_once(
                    f"live_sentiment_proxy_short_bars:{str(code)}",
                    (
                        "Live sentiment proxy fallback due to insufficient daily bars. "
                        f"etf={code} bars={bars_count} fallback=50/0.5"
                    ),
                )
        except Exception as e:
            degrade_once(
                f"live_sentiment_proxy_failed:{str(code)}",
                f"Live sentiment proxy failed; fallback=50/0.5. etf={code} err={repr(e)}",
            )
            self._logger.error("sentiment proxy failed | etf=%s err=%r", str(code), e)

        try:
            cn = str(normalize_etf_code(str(code)))
        except Exception as e:
            cn = str(code)
            warn_once(
                f"live_norm_code_failed:{str(code)}",
                f"Live normalize_etf_code failed; fallback raw code. etf={code} err={repr(e)}",
            )
        for key in {str(code), cn}:
            ext = self._ext_factors.get(key) or {}
            ext["sentiment_score_01"] = float(score01)
            ext["sentiment_score_100"] = int(score100)
            self._ext_factors[key] = ext
        self._logger.debug(
            "sentiment proxy applied | etf=%s normalized=%s bars=%s score100=%s score01=%.3f",
            str(code),
            str(cn),
            int(bars_count),
            int(score100),
            float(score01),
        )
        return int(score100), float(score01)

    def _inject_sentiment_proxy(self, *, watchlist: list[WatchlistItem]) -> list[WatchlistItem]:
        self._logger.debug("inject sentiment start | watchlist=%s held_positions=%s", len(watchlist), len(self._state.positions))
        out: list[WatchlistItem] = []
        for it in watchlist:
            score100, score01 = self._apply_sentiment_proxy_for_code(code=str(it.etf_code))
            extra = dict(it.extra)
            extra["sentiment_score_01"] = float(score01)
            out_item = WatchlistItem(
                etf_code=str(it.etf_code),
                sentiment_score=int(score100),
                profit_ratio=float(it.profit_ratio),
                nearest_resistance=it.nearest_resistance,
                micro_caution=bool(it.micro_caution),
                vpin_rank=it.vpin_rank,
                ofi_daily=it.ofi_daily,
                vs_max=it.vs_max,
                extra=extra,
            )
            out.append(out_item)

        wl_codes = {str(it.etf_code) for it in out}
        position_only = 0
        for code in list(self._state.positions.keys()):
            c = str(code)
            if c in wl_codes:
                continue
            self._apply_sentiment_proxy_for_code(code=c)
            position_only += 1
        self._logger.debug(
            "inject sentiment done | watchlist=%s position_only=%s",
            len(out),
            int(position_only),
        )
        return out

    def _load_external_factors_for_watchlist(self, *, watch_codes: list[str], now: datetime) -> list[WatchlistItem]:
        from integrations.watchlist_loader import load_watchlist_items, normalize_etf_code

        wl_codes = [str(x).strip() for x in list(watch_codes) if str(x).strip()]
        seen = {str(x) for x in wl_codes}
        extra_codes: list[str] = []
        for k in list(self._state.positions.keys()):
            s = str(k).strip()
            if not s or s in seen:
                continue
            extra_codes.append(s)
            seen.add(s)
        all_codes = wl_codes + extra_codes

        res = load_watchlist_items(etf_codes=all_codes, now=now, load_sentiment=False)
        self._ext_factors.update(res.ext_factors)

        for code in all_codes:
            cn = normalize_etf_code(str(code))
            ext = self._ext_factors.get(str(cn)) or self._ext_factors.get(str(code)) or {}
            td = str(ext.get("chip_trade_date") or "")
            dpc = ext.get("dpc_peak_density")
            if dpc is None:
                continue
            try:
                self._dpc_history.upsert(etf_code=str(cn), trade_date=str(td), dpc_peak_density=float(dpc))
            except Exception as e:
                warn_once(f"dpc_history_upsert_failed:{cn}", f"State: DPC 历史写入失败，已降级跳过: etf={cn} trade_date={td} err={repr(e)}")
                continue

        by_code: dict[str, WatchlistItem] = {}
        for it in list(res.items):
            by_code[str(it.etf_code)] = it
        out_items: list[WatchlistItem] = []
        for c in wl_codes:
            cn = normalize_etf_code(str(c))
            it = by_code.get(str(cn))
            if it is not None:
                out_items.append(it)
        return out_items

    def _build_watchlist(self, *, now: Optional[datetime] = None) -> list[WatchlistItem]:
        ts = datetime.now() if now is None else now
        try:
            wl_codes = self._resolve_watch_codes(now=ts)
            return self._load_external_factors_for_watchlist(watch_codes=wl_codes, now=ts)
        except Exception as e:
            warn_once("refresh_external_factors_failed", f"Integration: 外部因子刷新失败，已降级为默认因子: err={repr(e)}")
            fallback_codes = self._default_watch_auto_codes() if bool(getattr(self._cfg, "watch_auto", False)) else list(self._cfg.watchlist_etf_codes)
            out: list[WatchlistItem] = []
            for code in fallback_codes:
                from integrations.watchlist_loader import normalize_etf_code

                out.append(
                    WatchlistItem(
                        etf_code=str(normalize_etf_code(str(code))),
                        sentiment_score=50,
                        profit_ratio=0.0,
                        extra={"sentiment_score_01": 0.5},
                    )
                )
            return out

    def _compute_atr5_percentile(self, etf_code: str) -> float:
        try:
            bars = self._data.get_bars(str(etf_code), "1d", 125)
        except Exception as e:
            warn_once(f"atr5_bars_fetch_failed:{str(etf_code)}", f"Data: ATR5 分位计算取 K 线失败，已降级为 50: etf={etf_code} err={repr(e)}")
            return 50.0
        if len(bars) < 10:
            warn_once(f"atr5_bars_too_short:{str(etf_code)}", f"Data: ATR5 分位计算 K 线不足，已降级为 50: etf={etf_code} bars={len(bars)}")
            return 50.0
        closes = [float(b.close) for b in bars]
        highs = [float(b.high) for b in bars]
        lows = [float(b.low) for b in bars]
        trs: list[float] = []
        for i in range(1, len(bars)):
            trs.append(
                max(
                    float(highs[i]) - float(lows[i]),
                    abs(float(highs[i]) - float(closes[i - 1])),
                    abs(float(lows[i]) - float(closes[i - 1])),
                )
            )
        if len(trs) < 5:
            degrade_once(f"atr5_tr_count_too_short:{str(etf_code)}", f"ATR5 TR length too short; fallback percentile=50. etf={etf_code} trs={len(trs)}")
            return 50.0
        atr5s = [float(sum(trs[i - 4 : i + 1]) / 5.0) for i in range(4, len(trs))]
        if not atr5s:
            degrade_once(f"atr5_series_empty:{str(etf_code)}", f"ATR5 series empty; fallback percentile=50. etf={etf_code}")
            return 50.0
        current = float(atr5s[-1])
        rank = float(sum(1 for x in atr5s if float(x) <= float(current)) / len(atr5s))
        return float(round(100.0 * rank, 1))

    def _pre_open(self, *, now: datetime) -> None:
        self._logger.info("pre_open start | now=%s", now.isoformat(timespec="seconds"))
        try:
            self._sync_asset()
        except Exception as e:
            self._logger.error("asset sync failed: %s", e)

        if bool(self._cfg.auto_prep):
            try:
                from integrations.premarket_prep import ensure_tminus1_ready

                r = ensure_tminus1_ready(
                    now=now,
                    watch_codes=(self._default_watch_auto_codes() if bool(getattr(self._cfg, "watch_auto", False)) else self._cfg.watchlist_etf_codes),
                    position_codes=self._state.positions.keys(),
                    hot_top=int(self._cfg.hot_top),
                )
                self._logger.warning(
                    "pre_open auto_prep | t-1=%s chip=%s hot_csv=%s hot=%s sentiment=%s",
                    str(r.t_minus_1),
                    "OK" if bool(r.chip_ready) else "MISS",
                    str(r.hot_csv or ""),
                    len(r.hot_codes),
                    len(r.sentiment_ready_codes),
                )
            except Exception as e:
                warn_once("premarket_prep_failed", f"PreMarket: 自动补齐失败，已降级继续: err={repr(e)}")

        try:
            self._sync_corporate_actions(now=now)
        except Exception as e:
            self._logger.error("sync_corporate_actions failed: %s", e)

        wl: list[WatchlistItem] = []
        try:
            if bool(getattr(self._cfg, "watch_auto_no_filter", False)) and not bool(self._cfg.watch_auto):
                self._logger.warning("watch_auto_no_filter is set but watch_auto is disabled; no_filter flag is ignored")
            wl = self._build_watchlist(now=now)
            inject_sentiment = getattr(self, "_inject_sentiment_proxy", None)
            if callable(inject_sentiment):
                try:
                    wl = inject_sentiment(watchlist=wl)
                except Exception as e:
                    degrade_once(
                        "pre_open_sentiment_inject_failed",
                        f"pre_open sentiment injection failed; fallback to raw watchlist. err={repr(e)}",
                    )
            if bool(self._cfg.watch_auto):
                if bool(getattr(self._cfg, "watch_auto_no_filter", False)):
                    self._logger.warning(
                        "watch_auto no_filter enabled | all candidates added to watchlist without threshold filtering | count=%s",
                        len(wl),
                    )
                else:
                    from entry.watchlist import filter_watchlist

                    wl = filter_watchlist(wl, min_sentiment=int(getattr(self._cfg, "min_sentiment_threshold", 60)))
        except Exception as e:
            self._logger.error("build_watchlist failed: %s", e)
        self._day_watch_codes = [str(it.etf_code) for it in wl if str(it.etf_code).strip()]
        self._log_watchlist_snapshot(now=now, watchlist=wl)

        for code in list(self._state.positions.keys()):
            ps = self._pos_fsm.upsert_position(etf_code=code)
            ps.same_day_buy_qty = 0

        try:
            _ = self._exit_fsm.execute_pending_locked(now=now)
        except Exception as e:
            self._logger.error("execute_pending_locked failed: %s", e)

        try:
            self._pos_fsm.reset_t0_daily()
        except Exception as e:
            self._logger.error("reset_t0_daily failed: %s", e)

        for code in list(self._state.positions.keys()):
            ps = self._pos_fsm.upsert_position(etf_code=code)
            ps.t0_daily_pnl = 0.0

        if self._t0_enabled():
            for code in list(self._state.positions.keys()):
                ps = self._pos_fsm.upsert_position(etf_code=code)
                if int(ps.total_qty) <= 0:
                    continue
                try:
                    dstr = now.strftime("%Y%m%d")
                    today_vol = float(self._data.get_auction_volume(code, dstr))
                    hist = [float(x) for x in (ps.auction_volume_history or []) if float(x) > 0]
                    avg = float(sum(hist) / len(hist)) if hist else 0.0
                    denom = avg if avg > 0 else (today_vol if today_vol > 0 else 1.0)
                    ratio = float(today_vol) / float(denom)
                    ps.auction_volume_history = (hist + [today_vol])[-20:]
                    self._pos_fsm.t0_prepare_day(
                        etf_code=code,
                        now=now,
                        trade_date=now,
                        auction_vol_ratio=float(ratio),
                        atr5_percentile=float(self._compute_atr5_percentile(code)),
                    )
                except Exception as e:
                    self._logger.error("t0_prepare_day failed for %s: %s", code, e)

        try:
            self._entry_fsm.upsert_watchlist(d=now, watchlist=wl)
        except Exception as e:
            self._logger.error("upsert_watchlist failed: %s", e)

        self._sm.save(self._state)
        self._logger.info("pre_open end | now=%s", now.isoformat(timespec="seconds"))

    def _sync_corporate_actions(self, *, now: datetime) -> None:
        get_divid_factors = getattr(self._data, "get_divid_factors", None)
        if not callable(get_divid_factors):
            return

        trade_day = now.strftime("%Y%m%d")
        changed = 0
        tracked_codes: set[str] = {str(code) for code in self._state.positions.keys() if str(code or "").strip()}
        tracked_codes.update(str(getattr(pe, "etf_code", "") or "").strip() for pe in self._state.pending_entries)
        raw_high_chase = getattr(self._state, "phase2_high_chase_signals", None)
        if isinstance(raw_high_chase, dict):
            tracked_codes.update(str(code or "").strip() for code in raw_high_chase.keys())
        raw_markers = getattr(self._state, "corporate_action_markers", None)
        if not isinstance(raw_markers, dict):
            raw_markers = {}
            self._state.corporate_action_markers = raw_markers
        tracked_codes.update(str(code or "").strip() for code in raw_markers.keys())
        startup_recovered_cost_codes = set(getattr(self, "_startup_recovered_cost_codes", set()) or set())
        for code in sorted(c for c in tracked_codes if c):
            etf_code = str(code)
            total, sellable, locked = self._pos_fsm.query_balances(etf_code=etf_code)
            ps = self._state.positions.get(etf_code)
            pending_exists = any(str(getattr(pe, "etf_code", "") or "") == etf_code for pe in self._state.pending_entries)
            high_chase_exists = bool(self._get_phase2_high_chase_signal_rows(code=etf_code))
            local_total = int(getattr(ps, "total_qty", 0) or 0)
            if int(total) <= 0 and int(local_total) <= 0 and not pending_exists and not high_chase_exists:
                raw_markers.pop(etf_code, None)
                continue
            applied_date = str(getattr(ps, "last_corporate_action_date", "") or "") if ps is not None else str(raw_markers.get(etf_code) or "")
            if applied_date == str(trade_day):
                continue

            try:
                raw = get_divid_factors(etf_code, start_time=trade_day, end_time=trade_day)
                extracted = _extract_split_price_factor_from_divid_factors(raw, trade_day=trade_day)
            except Exception as e:
                degrade_once(
                    f"live_corp_action_query_failed:{etf_code}:{trade_day}",
                    f"corporate action query failed; skip live rescale. etf={etf_code} trade_day={trade_day} err={repr(e)}",
                )
                continue

            if extracted is None:
                continue

            price_factor, raw_dr = extracted
            prev_total = int(getattr(ps, "total_qty", 0) or 0)
            prev_avg = float(getattr(ps, "avg_cost", 0.0) or 0.0)
            position_changed = False
            if ps is None and int(total) > 0:
                ps = self._pos_fsm.upsert_position(etf_code=etf_code)
            skip_position_rescale = bool(ps is not None and int(total) > 0 and etf_code in startup_recovered_cost_codes)
            if ps is not None and not skip_position_rescale:
                apply_price_factor_to_position_state(ps=ps, price_factor=float(price_factor))
                position_changed = True
            pending_changed = int(
                apply_price_factor_to_pending_entries(
                    pending_entries=self._state.pending_entries,
                    etf_code=etf_code,
                    price_factor=float(price_factor),
                )
            )
            high_chase_changed = int(self._rescale_phase2_high_chase_signal_rows(code=etf_code, price_factor=float(price_factor)))
            if ps is not None and int(total) > 0 and not skip_position_rescale:
                scaled_total = int(getattr(ps, "total_qty", 0) or 0)
                ps.total_qty = int(total)
                delta = int(total) - int(scaled_total)
                if delta != 0:
                    ps.base_qty = max(0, int(getattr(ps, "base_qty", 0) or 0) + int(delta))
            if ps is not None:
                ps.last_corporate_action_date = str(trade_day)
                raw_markers.pop(etf_code, None)
            else:
                raw_markers[etf_code] = str(trade_day)
            changed += 1
            alert_once(
                f"live_corp_action_applied:{etf_code}:{trade_day}",
                (
                    "Live corporate action rescaled local position state. "
                    f"etf={etf_code} trade_day={trade_day} price_factor={float(price_factor):.6f} "
                    f"dr={float(raw_dr):.6f} prev_total={int(prev_total)} broker_total={int(total)} "
                    f"sellable={int(sellable)} locked={int(locked)} prev_avg={float(prev_avg):.6f} new_avg={float(getattr(ps, 'avg_cost', 0.0) or 0.0):.6f} "
                    f"pending_entries={int(pending_changed)} high_chase_signals={int(high_chase_changed)} position={bool(position_changed)} skip_position_rescale={bool(skip_position_rescale)}"
                ),
            )

        if changed > 0:
            self._sm.save(self._state)
        self._startup_recovered_codes = set()
        self._startup_recovered_cost_codes = set()


    def _run_opening_gap_checks(self, *, now: datetime) -> None:
        # Exit spec requires a dedicated 09:25 gap check outside trading sessions.
        if not (int(now.time().hour) == 9 and int(now.time().minute) == 25):
            return

        checked = 0
        triggered = 0
        self._logger.info("opening gap checks start | now=%s", now.isoformat(timespec="seconds"))
        for code, ps in list(self._state.positions.items()):
            etf_code = str(code)
            if int(getattr(ps, "total_qty", 0) or 0) <= 0:
                continue
            checked += 1
            try:
                snap = self._data.get_snapshot(etf_code)
                assert_action_allowed(snap.data_quality, ActionType.EXIT_LAYER1_TRIGGER_CHECK)
            except AssertionError as e:
                dq = "UNKNOWN"
                try:
                    dq = str(snap.data_quality.value)  # type: ignore[name-defined]
                except Exception:
                    dq = "UNKNOWN"
                degrade_once(
                    f"opening_gap_check_blocked_by_data_quality:{etf_code}:{dq}",
                    f"opening gap check skipped by data quality gate. etf={etf_code} data_quality={dq} reason={str(e)}",
                )
                continue
            except Exception as e:
                self._logger.error("opening gap snapshot failed for %s: %s", etf_code, e)
                continue

            pstate = self._pos_fsm.upsert_position(etf_code=etf_code)
            pstate.highest_high = max(float(pstate.highest_high), float(snap.last_price))
            stop_price, k, hh, atr = self._compute_stop(etf_code=etf_code, ps=pstate, now=now, last_price=float(snap.last_price))
            try:
                oid = self._exit_fsm.apply_gap_check_only(
                    now=now,
                    etf_code=etf_code,
                    stop_price=float(stop_price),
                    chandelier_k=float(k) if k > 0 else None,
                    chandelier_hh=float(hh) if hh > 0 else None,
                    chandelier_atr=float(atr) if atr > 0 else None,
                )
                if oid is not None:
                    triggered += 1
                    self._handle_exit_sell(now=now, etf_code=etf_code, order_id=int(oid), ps=pstate)
            except Exception as e:
                self._logger.error("opening gap check failed for %s: %s", etf_code, e)

        self._sm.save(self._state)
        self._logger.info("opening gap checks end | checked=%s triggered=%s", int(checked), int(triggered))

    def _intraday_loop(
        self,
        *,
        now_provider: Callable[[], datetime],
        sleep_fn: Callable[[float], None],
        max_ticks: Optional[int],
    ) -> None:
        self._logger.info("intraday start")
        n = 0
        next_heartbeat_at: Optional[datetime] = None
        while True:
            now = now_provider()
            if now.time() >= datetime.strptime("15:01", "%H:%M").time():
                break
            if not is_trading_time(now):
                sleep_fn(min(30.0, float(self._cfg.tick_interval_s)))
                continue
            if next_heartbeat_at is None or now >= next_heartbeat_at:
                try:
                    self._log_heartbeat_prices(now=now)
                except Exception as e:
                    self._logger.error("heartbeat failed: %s", e)
                if next_heartbeat_at is None:
                    next_heartbeat_at = now + timedelta(minutes=10)
                else:
                    while next_heartbeat_at <= now:
                        next_heartbeat_at = next_heartbeat_at + timedelta(minutes=10)
            self._tick_cycle(now=now)
            n += 1
            if max_ticks is not None and n >= int(max_ticks):
                break
            sleep_fn(float(self._cfg.tick_interval_s))
        self._logger.info("intraday end | ticks=%s", n)

    def _post_close(self, *, now: datetime) -> None:
        self._logger.info("post_close start | now=%s", now.isoformat(timespec="seconds"))

        current_nav = float(self._state.nav) if float(self._state.nav) > 0 else float(self._state.cash)
        try:
            _ = self._pos_fsm.on_post_close(now=now, current_nav=float(current_nav))
        except Exception as e:
            self._logger.error("on_post_close failed: %s", e)

        for code in list(self._state.positions.keys()):
            ps = self._pos_fsm.upsert_position(etf_code=code)
            pnl = float(ps.t0_daily_pnl)
            ps.t0_pnl_5d = (list(ps.t0_pnl_5d) + [pnl])[-5:]
            ps.t0_pnl_30d = (list(ps.t0_pnl_30d) + [pnl])[-30:]

        try:
            wl = self._build_watchlist(now=now)
            inject_sentiment = getattr(self, "_inject_sentiment_proxy", None)
            if callable(inject_sentiment):
                try:
                    wl = inject_sentiment(watchlist=wl)
                except Exception as e:
                    degrade_once(
                        "post_close_sentiment_inject_failed",
                        f"post_close sentiment injection failed; fallback to raw watchlist. err={repr(e)}",
                    )
            if bool(self._cfg.watch_auto):
                if bool(getattr(self._cfg, "watch_auto_no_filter", False)):
                    self._logger.warning(
                        "post_close watch_auto no_filter enabled | all candidates used for phase2 scan | count=%s",
                        len(wl),
                    )
                else:
                    from entry.watchlist import filter_watchlist

                    wl_before = len(wl)
                    wl = filter_watchlist(wl, min_sentiment=int(getattr(self._cfg, "min_sentiment_threshold", 60)))
                    self._logger.info(
                        "post_close watchlist filtered | before=%s after=%s",
                        int(wl_before),
                        int(len(wl)),
                    )
            blocked = 0
            passed = 0
            entry_blocked = 0
            high_chase_blocked = 0
            for it in wl:
                ok, reason = self._allow_phase2_candidate(now=now, item=it)
                if not bool(ok):
                    blocked += 1
                    degrade_once(
                        f"phase2_gate_blocked:{str(it.etf_code)}:{now.strftime('%Y%m%d')}",
                        f"post_close phase2 candidate blocked by quality gate. etf={it.etf_code} reason={reason}",
                    )
                    continue
                passed += 1
                bars = self._data.get_bars(it.etf_code, "1d", 60)
                res = evaluate_phase2(
                    etf_code=it.etf_code,
                    bars=bars,
                    watch=it,
                    signal_date=now.date(),
                    s_micro_missing=self._cfg.phase2_s_micro_missing,
                    score_threshold=getattr(self, "_phase2_score_threshold", None),
                    continuation_cfg=getattr(self, "_phase2_continuation_cfg", None),
                )
                self._entry_fsm.record_phase2_result(timestamp=now, etf_code=it.etf_code, watch=it, res=res)
                if res.signal_fired is None:
                    self._remember_phase2_blocked_continuation_signal(
                        now=now,
                        etf_code=str(it.etf_code),
                        close_signal_day=float(getattr(res, "close_signal_day", 0.0) or 0.0),
                        h_signal=float(getattr(res, "h_signal", 0.0) or 0.0),
                        note=str(getattr(res, "note", "") or ""),
                    )
                    continue
                signal_price = phase2_signal_reference_price(
                    close_signal_day=float(getattr(res.signal_fired, "close_signal_day", 0.0) or 0.0),
                    h_signal=float(getattr(res.signal_fired, "h_signal", 0.0) or 0.0),
                )
                should_block_high_chase, high_chase_reason = self._should_block_phase2_high_chase_signal(
                    now=now,
                    etf_code=str(it.etf_code),
                    ref_price=float(signal_price),
                )
                if self._phase2_high_chase_uses_all_signals():
                    self._remember_phase2_high_chase_signal(
                        now=now,
                        etf_code=str(it.etf_code),
                        ref_price=float(signal_price),
                    )
                should_block_entry, entry_reason = self._should_block_phase2_entry_after_signal(
                    now=now,
                    etf_code=str(it.etf_code),
                )
                if should_block_high_chase:
                    high_chase_blocked += 1
                    self._logger.info(
                        "phase2 high-chase blocked | etf=%s reason=%s",
                        str(it.etf_code),
                        str(high_chase_reason),
                    )
                    continue
                if should_block_entry:
                    entry_blocked += 1
                    self._logger.info(
                        "phase2 entry blocked after signal | etf=%s reason=%s",
                        str(it.etf_code),
                        str(entry_reason),
                    )
                    continue
                self._entry_fsm.add_pending_entry(fired=res.signal_fired)
            self._logger.info(
                "post_close phase2 gate summary | candidates=%s passed=%s blocked=%s entry_blocked=%s high_chase_blocked=%s",
                int(len(wl)),
                int(passed),
                int(blocked),
                int(entry_blocked),
                int(high_chase_blocked),
            )
        except Exception as e:
            self._logger.error("post_close entry scan failed: %s", e)

        try:
            self._trading.exit_freeze_mode()
        except Exception as e:
            self._logger.error("post_close trading freeze reset failed: %s", e)

        self._sm.save(self._state)
        self._logger.info("post_close end | now=%s", now.isoformat(timespec="seconds"))

    def _tick_cycle(self, *, now: datetime) -> None:
        try:
            self._replay_persisted_exit_intents(now=now, source="runtime")
        except Exception as e:
            self._logger.error("runtime exit intent replay failed: %s", e)
        for pe in list(self._state.pending_entries):
            self._process_pending_entry(now=now, pe=pe)

        for code, ps in list(self._state.positions.items()):
            if int(ps.total_qty) <= 0:
                continue
            self._process_position_tick(now=now, etf_code=str(code))

        self._process_placed_orders(now=now)

    def _nav_estimate(self, *, now: datetime | None = None) -> float:
        nav = float(self._state.nav)
        if nav > 0:
            self._nav_estimate_cache_key = None
            self._nav_estimate_cache_value = None
            return float(nav)

        cache_key = None
        if now is not None:
            positions_sig = tuple(
                (str(code), int(ps.total_qty))
                for code, ps in sorted(self._state.positions.items())
                if int(ps.total_qty) > 0
            )
            cache_key = (now, float(self._state.cash), positions_sig)
            if self._nav_estimate_cache_key == cache_key and self._nav_estimate_cache_value is not None:
                return float(self._nav_estimate_cache_value)

        est = float(self._state.cash)
        for code, ps in self._state.positions.items():
            if int(ps.total_qty) <= 0:
                continue
            try:
                snap = self._data.get_snapshot(str(code))
                est += float(snap.last_price) * int(ps.total_qty)
            except Exception as e:
                degrade_once(f"nav_estimate_snapshot_failed:{str(code)}", f"NAV estimate skipped one position due to snapshot failure. etf={code} err={repr(e)}")
                continue

        if cache_key is not None:
            self._nav_estimate_cache_key = cache_key
            self._nav_estimate_cache_value = float(est)
        return float(est)

    def _days_held(self, entry_date: str, now: datetime) -> int:
        s = str(entry_date or "")
        if not s:
            return 0
        try:
            d0 = datetime.fromisoformat(s).date()
            return int((now.date() - d0).days)
        except Exception:
            try:
                d1 = datetime.strptime(s, "%Y-%m-%d").date()
                return int((now.date() - d1).days)
            except Exception as e:
                degrade_once("days_held_parse_failed", f"days_held date parse failed; fallback=0. raw={s} err={repr(e)}")
                return 0

    def _current_return(self, *, last_price: float, avg_cost: float) -> float:
        if float(avg_cost) <= 0:
            return 0.0
        return float((float(last_price) - float(avg_cost)) / float(avg_cost))

    def _days_since_high_from_bars(self, bars) -> int:
        try:
            highs = [float(b.high) for b in list(bars)]
            if not highs:
                return 0
            m = max(highs)
            idx = 0
            for i, h in enumerate(highs):
                if float(h) == float(m):
                    idx = int(i)
            return int(len(highs) - 1 - int(idx))
        except Exception as e:
            degrade_once("days_since_high_compute_failed", f"days_since_high compute failed; fallback=0. err={repr(e)}")
            return 0

    def _t0_realized_loss_pct(self, *, t0_daily_pnl: float, effective_slot: float) -> float:
        if float(t0_daily_pnl) >= 0 or float(effective_slot) <= 0:
            return 0.0
        return float(min(1.0, abs(float(t0_daily_pnl)) / float(effective_slot)))

    def _compute_stop(self, *, etf_code: str, ps, now: datetime, last_price: float) -> tuple[float, float, float, float]:
        try:
            from exit.chandelier import compute_chandelier_state
            from exit.accel import compute_accel_k
            from exit.signals.s_chip import compute_s_chip

            bars = self._data.get_bars(str(etf_code), "1d", 40)
            if not bars:
                raise RuntimeError("bars empty")
            reduced = bool(getattr(ps, "state", FSMState.S0_IDLE) == FSMState.S5_REDUCED)
            ext = self._ext_factors.get(str(etf_code)) or {}
            chip_days = int(ext.get("chip_engine_days", 0) or 0)
            pr = float(ext.get("profit_ratio", 0.0) or 0.0)
            dpc_5d = None
            try:
                dpc_5d = self._dpc_history.get_5d(str(etf_code))
            except Exception as e:
                degrade_once(f"chandelier_dpc_5d_failed:{str(etf_code)}", f"chandelier S_chip input load failed; fallback S_chip=0. etf={etf_code} err={repr(e)}")
                dpc_5d = None
            s_chip = 0.0
            if dpc_5d is not None and len(dpc_5d) >= 5 and chip_days >= 10:
                try:
                    s_chip = float(compute_s_chip(dpc_5d, pr))
                except Exception as e:
                    degrade_once(
                        f"chandelier_s_chip_compute_failed:{str(etf_code)}",
                        f"chandelier S_chip compute failed; fallback S_chip=0. etf={etf_code} err={repr(e)}",
                    )
                    s_chip = 0.0
            st = compute_chandelier_state(
                bars=bars,
                prev_hh=float(ps.highest_high),
                reduced=reduced,
                s_chip=float(s_chip),
                atr_pct_min=getattr(self, "_exit_atr_pct_min", None),
                atr_pct_max=getattr(self, "_exit_atr_pct_max", None),
                k_normal=getattr(self, "_exit_k_normal", None),
                k_chip_decay=getattr(self, "_exit_k_chip_decay", None),
                k_reduced=getattr(self, "_exit_k_reduced", None),
            )
            stop = float(st.stop)
            k = float(st.k)
            accel_enabled, step_pct, step_k, k_min = self._exit_k_accel()
            if bool(accel_enabled):
                pnl_pct = self._current_return(last_price=float(last_price), avg_cost=float(ps.avg_cost))
                k_adj = compute_accel_k(float(k), float(pnl_pct), float(step_pct), float(step_k), float(k_min))
                if float(k_adj) != float(k):
                    stop = float(st.hh) - float(k_adj) * float(st.atr)
                k = float(k_adj)
            ps.highest_high = max(float(ps.highest_high), float(st.hh))
            self._sm.save(self._state)
            return float(stop), float(k), float(st.hh), float(st.atr)
        except Exception as e:
            fb = float(ps.avg_cost) * 0.97 if float(ps.avg_cost) > 0 else float(last_price) * 0.97
            degrade_once(
                f"chandelier_fallback_stop:{str(etf_code)}",
                f"chandelier stop compute failed; fallback stop={fb:.4f} (97% ref). etf={etf_code} err={repr(e)}",
            )
            if float(ps.avg_cost) > 0:
                return float(ps.avg_cost) * 0.97, 0.0, float(ps.highest_high), 0.0
            return float(last_price) * 0.97, 0.0, float(ps.highest_high), 0.0

    def _process_position_tick(self, *, now: datetime, etf_code: str) -> None:
        try:
            snap = self._data.get_snapshot(etf_code)
            assert_action_allowed(snap.data_quality, ActionType.T0_SIGNAL)
        except AssertionError as e:
            dq = "UNKNOWN"
            try:
                dq = str(snap.data_quality.value)  # type: ignore[name-defined]
            except Exception:
                dq = "UNKNOWN"
            degrade_once(
                f"position_tick_blocked_by_data_quality:{etf_code}:{dq}",
                f"position tick skipped by data quality gate. etf={etf_code} data_quality={dq} reason={str(e)}",
            )
            return
        except Exception as e:
            self._logger.error("snapshot failed for %s: %s", etf_code, e)
            return

        ps = self._pos_fsm.upsert_position(etf_code=etf_code)
        ps.highest_high = max(float(ps.highest_high), float(snap.last_price))
        self._sm.save(self._state)

        try:
            _ = self._pos_fsm.evaluate_circuit_breaker(now=now, nav_estimate=self._nav_estimate(now=now))
        except Exception as e:
            self._logger.error("circuit breaker failed: %s", e)

        stop_price, k, hh, atr = self._compute_stop(etf_code=etf_code, ps=ps, now=now, last_price=float(snap.last_price))
        days_held = self._days_held(str(ps.entry_date), now)
        cur_ret = self._current_return(last_price=float(snap.last_price), avg_cost=float(ps.avg_cost))
        t0_loss_pct = self._t0_realized_loss_pct(t0_daily_pnl=float(ps.t0_daily_pnl), effective_slot=float(ps.effective_slot))
        # --- Layer 2 评分 (per-signal degraded scoring, exit spec §3.3) ---
        _DEGRADED = 0.5  # 缺失信号贡献 = 权重 × 0.5
        score_soft_layer2 = 0.0
        bars: list[Bar] = []
        signals: dict[str, float] = {}
        data_health_signals: dict[str, DataQuality] = {
            "S_chip": DataQuality.OK,
            "S_sentiment": DataQuality.OK,
            "S_diverge": DataQuality.OK,
            "S_time": DataQuality.OK,
        }
        try:
            from exit.scoring import compute_score_soft
            from exit.signals.s_chip import compute_s_chip
            from exit.signals.s_diverge import compute_s_diverge
            from exit.signals.s_sentiment import compute_s_sentiment
            from exit.signals.s_time import compute_s_time

            try:
                bars = self._data.get_bars(str(etf_code), "1d", 60)
            except Exception as e:
                self._logger.warning("Layer2 get_bars failed for %s, all bar-dependent signals degraded: %s", etf_code, e)
                bars = []

            # S_diverge — missing => UNAVAILABLE (score uses 0.5 fallback)
            try:
                if bars:
                    s_diverge = float(compute_s_diverge(bars))
                else:
                    data_health_signals["S_diverge"] = DataQuality.UNAVAILABLE
                    s_diverge = 0.0
            except Exception as e:
                data_health_signals["S_diverge"] = DataQuality.UNAVAILABLE
                degrade_once(
                    f"layer2_s_diverge_degraded:{str(etf_code)}",
                    f"Layer2 S_diverge failed; fallback={_DEGRADED}. etf={etf_code} err={repr(e)}",
                )
                s_diverge = 0.0

            # S_time — missing => UNAVAILABLE (score uses 0.5 fallback)
            try:
                if bars:
                    days_since_high = self._days_since_high_from_bars(bars)
                    s_time = float(compute_s_time(days_held=int(days_held), days_since_high=int(days_since_high), current_return=float(cur_ret)))
                else:
                    data_health_signals["S_time"] = DataQuality.UNAVAILABLE
                    s_time = 0.0
            except Exception as e:
                data_health_signals["S_time"] = DataQuality.UNAVAILABLE
                degrade_once(
                    f"layer2_s_time_degraded:{str(etf_code)}",
                    f"Layer2 S_time failed; fallback={_DEGRADED}. etf={etf_code} err={repr(e)}",
                )
                s_time = 0.0

            # S_chip — 冷启动/缺失 => UNAVAILABLE (score uses 0.5 fallback)
            ext = self._ext_factors.get(str(etf_code)) or {}
            dpc_5d = None
            try:
                dpc_5d = self._dpc_history.get_5d(str(etf_code))
            except Exception as e:
                degrade_once(
                    f"layer2_s_chip_history_failed:{str(etf_code)}",
                    f"Layer2 S_chip history load failed; mark UNAVAILABLE. etf={etf_code} err={repr(e)}",
                )
                dpc_5d = None
            pr = float(ext.get("profit_ratio", 0.0) or 0.0)
            chip_days = int(ext.get("chip_engine_days", 0) or 0)
            s_chip = 0.0
            if dpc_5d is not None and len(dpc_5d) >= 5 and chip_days >= 10:
                try:
                    s_chip = float(compute_s_chip(dpc_5d, pr))
                except Exception as e:
                    data_health_signals["S_chip"] = DataQuality.UNAVAILABLE
                    degrade_once(
                        f"layer2_s_chip_compute_degraded:{str(etf_code)}",
                        f"Layer2 S_chip compute failed; fallback={_DEGRADED}. etf={etf_code} err={repr(e)}",
                    )
                    s_chip = 0.0
            else:
                data_health_signals["S_chip"] = DataQuality.UNAVAILABLE
                degrade_once(
                    f"layer2_s_chip_coldstart:{str(etf_code)}",
                    (
                        "Layer2 S_chip cold-start/unavailable; mark UNAVAILABLE. "
                        f"etf={etf_code} chip_days={chip_days} dpc_points={0 if dpc_5d is None else len(dpc_5d)}"
                    ),
                )

            # S_sentiment — missing => UNAVAILABLE (score uses 0.5 fallback)
            try:
                if "sentiment_score_01" in ext and ext.get("sentiment_score_01") is not None:
                    sent_01 = float(ext.get("sentiment_score_01"))
                    s_sentiment = float(compute_s_sentiment(sent_01))
                else:
                    data_health_signals["S_sentiment"] = DataQuality.UNAVAILABLE
                    s_sentiment = 0.0
            except Exception as e:
                data_health_signals["S_sentiment"] = DataQuality.UNAVAILABLE
                degrade_once(
                    f"layer2_s_sentiment_degraded:{str(etf_code)}",
                    f"Layer2 S_sentiment failed; fallback={_DEGRADED}. etf={etf_code} err={repr(e)}",
                )
                s_sentiment = 0.0

            signals = {
                "S_chip": float(s_chip),
                "S_sentiment": float(s_sentiment),
                "S_diverge": float(s_diverge),
                "S_time": float(s_time),
            }
            score_soft_layer2 = float(compute_score_soft(signals, data_health=data_health_signals, threshold=getattr(self, "_exit_layer2_threshold", None)).score_soft)
        except Exception as e:
            # catastrophic: imports or compute_score_soft itself failed
            # all signals degraded → 0.7*0.5 + 0.7*0.5 + 0.5*0.5 + 0.4*0.5 = 1.15
            score_soft_layer2 = float(0.7 * _DEGRADED + 0.7 * _DEGRADED + 0.5 * _DEGRADED + 0.4 * _DEGRADED)
            data_health_signals = {k: DataQuality.UNAVAILABLE for k in ("S_chip", "S_sentiment", "S_diverge", "S_time")}
            self._logger.error("Layer2 scoring catastrophic failure for %s, using full degraded score %.2f: %s", etf_code, score_soft_layer2, e)

        data_health = {"L1": snap.data_quality, **data_health_signals}
        score_soft_layer1 = float(score_soft_layer2)

        try:
            oid1 = self._exit_fsm.apply_layer1_checks(
                now=now,
                etf_code=etf_code,
                stop_price=float(stop_price),
                score_soft=float(score_soft_layer1),
                data_health=data_health,
                days_held=int(days_held),
                current_return=float(cur_ret),
                t0_realized_loss_pct=float(t0_loss_pct),
                chandelier_k=float(k) if k > 0 else None,
                chandelier_hh=float(hh) if hh > 0 else None,
                chandelier_atr=float(atr) if atr > 0 else None,
            )
            if oid1 is not None:
                self._handle_exit_sell(now=now, etf_code=etf_code, order_id=int(oid1), ps=ps)
        except Exception as e:
            self._logger.error("layer1 failed for %s: %s", etf_code, e)

        try:
            oid2 = self._exit_fsm.apply_layer2_if_needed(now=now, etf_code=etf_code, score_soft=float(score_soft_layer2), signals=signals)
            if oid2 is not None:
                self._handle_exit_sell(now=now, etf_code=etf_code, order_id=int(oid2), ps=ps)
        except Exception as e:
            self._logger.error("layer2 failed for %s: %s", etf_code, e)

        if self._t0_enabled():
            try:
                _ = self._pos_fsm.execute_t0_live(now=now, etf_code=etf_code)
            except Exception as e:
                self._logger.error("t0 failed for %s: %s", etf_code, e)

        try:
            eval_res = self._evaluate_scale(now=now, etf_code=etf_code, ps=ps, stop_price=float(stop_price), score_soft=float(score_soft_layer2), last_price=float(snap.last_price), bars=bars)
            _ = self._pos_fsm.execute_scale(now=now, etf_code=etf_code, eval_result=eval_res)
        except Exception as e:
            self._logger.error("scale failed for %s: %s", etf_code, e)

        try:
            oid3 = self._exit_fsm.apply_lifeboat_buyback_check(
                now=now,
                etf_code=etf_code,
                stop_price=float(stop_price),
                score_soft=float(score_soft_layer2),
                data_health=data_health,
                chandelier_k=float(k) if k > 0 else None,
                chandelier_hh=float(hh) if hh > 0 else None,
                chandelier_atr=float(atr) if atr > 0 else None,
            )
            if oid3 is not None:
                self._reconcile_lifeboat_buyback(now=now, etf_code=etf_code, last_price=float(snap.last_price))
        except Exception as e:
            self._logger.error("lifeboat buyback failed for %s: %s", etf_code, e)

        self._sm.save(self._state)

    def _apply_partial_sell(self, *, ps, sold_qty: int) -> None:
        q = int(sold_qty)
        if q <= 0:
            return
        prev_total = int(ps.total_qty)
        new_total = max(0, int(prev_total) - int(q))

        s2 = max(0, int(ps.scale_2_qty))
        s1 = max(0, int(ps.scale_1_qty))
        base = max(0, int(ps.base_qty))

        left = int(q)
        take2 = min(int(s2), int(left))
        s2 -= int(take2)
        left -= int(take2)

        take1 = min(int(s1), int(left))
        s1 -= int(take1)
        left -= int(take1)

        take0 = min(int(base), int(left))
        base -= int(take0)
        left -= int(take0)

        ps.scale_2_qty = int(s2)
        ps.scale_1_qty = int(s1)
        ps.base_qty = int(base)
        ps.total_qty = int(new_total)
        ps.same_day_buy_qty = min(max(0, int(getattr(ps, "same_day_buy_qty", 0) or 0)), int(new_total))

        if int(ps.total_qty) <= 0:
            self._pos_fsm.on_layer1_clear(etf_code=str(ps.etf_code), sold_qty=int(prev_total))

    def _handle_exit_sell(self, *, now: datetime, etf_code: str, order_id: int, ps) -> None:
        try:
            res = self._trading.confirm_order(int(order_id), timeout_s=10.0)
        except Exception as e:
            self._logger.error("confirm sell failed for %s: %s", etf_code, e)
            return
        if res.status != OrderStatus.FILLED:
            self._logger.warning("exit sell not filled | etf=%s order_id=%s status=%s error=%s", etf_code, int(order_id), str(res.status), str(res.error or ""))
            return
        before_total = int(ps.total_qty)
        exit_state = ps.state
        exit_intent = None
        try:
            exit_intent = self._exit_fsm.pop_order_intent(order_id=int(order_id))
        except Exception:
            exit_intent = None
        exit_action = str((exit_intent or {}).get("action") or "")
        exit_locked_qty = int((exit_intent or {}).get("locked_qty") or 0)
        if not exit_action:
            alert_once(
                f"exit_missing_order_intent:{etf_code}:{int(order_id)}",
                (
                    "exit sell missing persisted exit order intent; fallback path engaged. "
                    f"etf={etf_code} order_id={int(order_id)} state={str(exit_state.value)}"
                ),
            )
        fill = _extract_fill(res, fallback_qty=before_total)
        sold_qty = int(min(int(fill.filled_qty), int(before_total))) if int(before_total) > 0 else int(fill.filled_qty)
        if sold_qty <= 0:
            return
        if float(fill.avg_price) > 0:
            proceeds = float(fill.avg_price) * int(sold_qty)
            fee = self._trade_fee(price=float(fill.avg_price), qty=int(sold_qty))
            self._state.cash = float(self._state.cash) + float(proceeds) - float(fee)
        if exit_action == "FULL_EXIT":
            layer1_cleared_qty = int(sold_qty)
            if int(exit_locked_qty) > 0:
                self._exit_fsm._append_pending_sell_locked(ps=ps, locked_qty=int(exit_locked_qty), now=now)
                ps.t0_frozen = True
                layer1_cleared_qty = max(0, int(before_total) - int(exit_locked_qty))
            if ps.state == FSMState.S0_IDLE:
                ps.state = FSMState.S2_BASE
            self._pos_fsm.on_layer1_clear(etf_code=etf_code, sold_qty=int(layer1_cleared_qty))
        elif exit_action == "LAYER2_REDUCE":
            if ps.state == FSMState.S5_REDUCED:
                ps.state = FSMState.S2_BASE
            self._pos_fsm.on_layer2_reduce(etf_code=etf_code, sold_qty=int(sold_qty))
        elif exit_state == FSMState.S0_IDLE:
            if ps.state == FSMState.S0_IDLE:
                ps.state = FSMState.S2_BASE
            self._pos_fsm.on_layer1_clear(etf_code=etf_code, sold_qty=int(sold_qty))
        elif exit_state == FSMState.S5_REDUCED:
            if ps.state == FSMState.S5_REDUCED:
                ps.state = FSMState.S2_BASE
            self._pos_fsm.on_layer2_reduce(etf_code=etf_code, sold_qty=int(sold_qty))
        else:
            self._apply_partial_sell(ps=ps, sold_qty=int(sold_qty))
        self._sync_asset_after_trade_fill(
            fallback_cash=float(self._state.cash),
            context=f"exit:{etf_code}:{int(order_id)}",
        )
        self._sm.save(self._state)

    def _reconcile_lifeboat_buyback(self, *, now: datetime, etf_code: str, last_price: float) -> None:
        try:
            total, _sellable, _locked = self._pos_fsm.query_balances(etf_code=str(etf_code))
        except Exception as e:
            self._logger.error("buyback reconcile failed for %s: %s", etf_code, e)
            return
        ps = self._pos_fsm.upsert_position(etf_code=str(etf_code))
        prev_total = int(ps.total_qty)
        delta = int(total) - int(prev_total)
        if delta <= 0:
            return
        est_spent = float(last_price) * int(delta) if float(last_price) > 0 else 0.0
        if est_spent > 0:
            self._state.cash = max(0.0, float(self._state.cash) - float(est_spent))
        self._pos_fsm.on_lifeboat_rebuy(etf_code=str(etf_code), rebuy_qty=int(delta), rebuy_price=float(last_price))
        self._sm.save(self._state)

    def _evaluate_scale(
        self,
        *,
        now: datetime,
        etf_code: str,
        ps,
        stop_price: float,
        score_soft: float,
        last_price: float,
        bars: list[Bar],
    ):
        from integrations.scale_features import aggregate_scale_features
        from position.constants import SCALE_1_RATIO, SCALE_2_RATIO

        scale_number = 1 if int(ps.scale_count) <= 0 else 2
        if float(ps.effective_slot) <= 0:
            target_amount = 0.0
        else:
            target_amount = float(ps.effective_slot) * (float(SCALE_1_RATIO) if int(scale_number) == 1 else float(SCALE_2_RATIO))
        days_since_last_scale = self._days_held(str(ps.last_scale_date), now) if getattr(ps, "last_scale_date", "") else 999
        proj = float(ps.total_qty) * float(last_price)
        above_stop = bool(float(last_price) > float(stop_price))

        ext = self._ext_factors.get(str(etf_code)) or {}
        dense_zones_json = str(ext.get("dense_zones_json") or "[]")
        support_px = ext.get("support_price_max_density")
        try:
            support_price = float(support_px) if support_px is not None else None
        except Exception as e:
            degrade_once(
                f"scale_support_price_parse_failed:{str(etf_code)}",
                f"scale support_price_max_density parse failed; fallback=None. etf={etf_code} raw={repr(support_px)} err={repr(e)}",
            )
            support_price = None
        mv = ext.get("ms_vs_max_logz")
        try:
            ms_vs_max_logz = float(mv) if mv is not None else None
        except Exception as e:
            degrade_once(
                f"scale_ms_vs_max_logz_parse_failed:{str(etf_code)}",
                f"scale ms_vs_max_logz parse failed; fallback=None. etf={etf_code} raw={repr(mv)} err={repr(e)}",
            )
            ms_vs_max_logz = None

        feats = aggregate_scale_features(
            etf_code=str(etf_code),
            bars=list(bars),
            last_price=float(last_price),
            avg_cost=float(ps.avg_cost),
            highest_high=float(ps.highest_high),
            dense_zones_json=str(dense_zones_json),
            support_price=support_price,
            ms_vs_max_logz=ms_vs_max_logz,
            score_soft=float(score_soft),
            tick_size=float(TICK_SIZE),
        )

        return self._pos_fsm.evaluate_scale_signal(
            now=now,
            etf_code=etf_code,
            scale_number=int(scale_number),
            target_amount=float(target_amount),
            position_state=ps.state,
            unrealized_profit_atr14_multiple=float(feats.unrealized_profit_atr14_multiple),
            circuit_breaker_triggered=bool(self._state.circuit_breaker.triggered),
            intraday_freeze=bool(self._state.circuit_breaker.intraday_freeze),
            score_soft=float(feats.score_soft),
            days_since_last_scale=int(days_since_last_scale),
            projected_total_value=float(proj),
            effective_slot=float(ps.effective_slot),
            kama_rising_days=int(feats.kama_rising_days),
            elder_impulse_green=bool(feats.elder_impulse_green),
            pullback_atr14_multiple=float(feats.pullback_atr14_multiple),
            above_chandelier_stop=bool(above_stop),
            chip_density_rank=float(feats.chip_density_rank),
            chip_touch_distance_atr14=float(feats.chip_touch_distance_atr14),
            micro_vol_ratio=float(feats.micro_vol_ratio),
            micro_support_held=bool(feats.micro_support_held),
            micro_bullish_close=bool(feats.micro_bullish_close),
        )

    def _process_pending_entry(self, *, now: datetime, pe) -> None:
        if str(getattr(pe, "status", "")) not in ("PENDING_TRIAL", "PENDING_CONFIRM"):
            return

        code = str(getattr(pe, "etf_code", "") or "")
        if not code:
            return
        if str(getattr(pe, "status", "")) == "PENDING_CONFIRM":
            ps0 = self._state.positions.get(code)
            if ps0 is None or int(getattr(ps0, "total_qty", 0) or 0) <= 0:
                degrade_once(
                    f"pending_confirm_missing_trial_position:{code}",
                    f"pending confirm dropped because no trial position exists. etf={code}",
                )
                pe.status = "FAILED"
                _ = self._entry_fsm.remove_pending_entry(pe=pe)
                self._sm.save(self._state)
                return

        try:
            snap = self._data.get_snapshot(code)
            inst = self._data.get_instrument_info(code)
            v = self._vwap.get(code)
            if v is None:
                v = VwapTracker()
                self._vwap[code] = v
            prev = self._prev_snap.get(code)
            v.update(snap, prev if prev is None or hasattr(prev, "timestamp") else None)  # type: ignore[arg-type]
            self._prev_snap[code] = snap
            assert_action_allowed(snap.data_quality, ActionType.ENTRY_CONFIRM)
        except AssertionError as e:
            dq = "UNKNOWN"
            try:
                dq = str(snap.data_quality.value)  # type: ignore[name-defined]
            except Exception:
                dq = "UNKNOWN"
            degrade_once(
                f"entry_confirm_blocked_by_data_quality:{code}:{dq}",
                f"entry confirm skipped by data quality gate. etf={code} data_quality={dq} reason={str(e)}",
            )
            return
        except Exception as e:
            self._logger.error("entry snapshot failed for %s: %s", code, e)
            return

        desired_qty = 0
        try:
            last_price = float(snap.last_price)
            if last_price > 0:
                nav = float(self._state.nav) if float(self._state.nav) > 0 else float(self._state.cash)
                atr_abs = float(getattr(pe, "atr_20", 0.0) or 0.0)
                atr_pct = float(atr_abs) / float(last_price) if last_price > 0 else 0.0
                from core.validators import compute_position_sizing

                sizing = compute_position_sizing(
                    current_nav=nav,
                    atr_pct_raw=float(atr_pct),
                    strong=bool(getattr(pe, "is_strong", False)),
                    slot_cap=float(self._cfg.position_slot_cap),
                    risk_budget_min=float(self._cfg.position_risk_budget_min),
                    risk_budget_max=float(self._cfg.position_risk_budget_max),
                )
                amt = float(sizing.trial_amt) if str(pe.status) == "PENDING_TRIAL" else float(sizing.confirm_amt)

                ps = self._pos_fsm.upsert_position(etf_code=str(code))
                current_notional = float(ps.total_qty) * float(last_price)
                remaining_slot = max(float(sizing.effective_slot) - float(current_notional), 0.0)
                amt = min(float(amt), float(remaining_slot))

                desired_qty = int(amt / last_price / 100.0) * 100
        except Exception as e:
            degrade_once(
                f"entry_desired_qty_fallback_zero:{code}",
                f"entry desired_qty compute failed; fallback desired_qty=0. etf={code} err={repr(e)}",
            )
            desired_qty = 0

        ctx = Phase3Context(
            etf_code=code,
            h_signal=float(getattr(pe, "h_signal", 0.0) or 0.0),
            l_signal=float(getattr(pe, "l_signal", 0.0) or 0.0),
            close_signal_day=float(getattr(pe, "close_signal_day", 0.0) or 0.0),
            atr_20=float(getattr(pe, "atr_20", 0.0) or 0.0),
            expire_yyyymmdd=str(getattr(pe, "expire_date", "") or ""),
            strong=bool(getattr(pe, "is_strong", False)),
            s_trend=float(getattr(pe, "signals", {}).get("S_trend", 0.0) or 0.0),
            s_chip_pr=float(getattr(pe, "signals", {}).get("S_chip_pr", 0.0) or 0.0),
        )
        confirmer = Phase3Confirmer(
            ctx,
            self._vwap[code],
            aggressive_buy_multiplier=getattr(self, "_aggressive_buy_multiplier", None),
            aggressive_buy_use_ask1=getattr(self, "_aggressive_buy_use_ask1", None),
            pathb_atr_mult=getattr(self, "_pathb_atr_mult", None),
            pathb_chip_min=getattr(self, "_pathb_chip_min", None),
            pathb_require_trend=getattr(self, "_pathb_require_trend", None),
            pathb_require_vwap_strict=getattr(self, "_pathb_require_vwap_strict", None),
        )
        act = confirmer.decide(
            now=now,
            snapshot=snap,
            instrument=inst,
            desired_qty=int(desired_qty),
            is_trial=bool(str(pe.status) == "PENDING_TRIAL"),
        )
        remembered_missed_executable = self._remember_phase2_missed_executable_signal(now=now, pe=pe, act=act)
        try:
            self._entry_fsm.apply_confirm_action(pe=pe, act=act)
        except Exception as e:
            self._logger.error("apply_confirm_action failed for %s: %s", code, e)
        if remembered_missed_executable:
            self._sm.save(self._state)

    def _process_placed_orders(self, *, now: datetime) -> None:
        cm = CashManager(self._state)
        for pe in list(self._state.pending_entries):
            st = str(getattr(pe, "status", "") or "")
            if st == "TRIAL_PLACED" and getattr(pe, "trial_order_id", None) is not None:
                oid = int(getattr(pe, "trial_order_id"))
                self._confirm_entry_order(now=now, pe=pe, order_id=oid, is_trial=True, cash_manager=cm)
            if st == "CONFIRM_PLACED" and getattr(pe, "confirm_order_id", None) is not None:
                oid = int(getattr(pe, "confirm_order_id"))
                self._confirm_entry_order(now=now, pe=pe, order_id=oid, is_trial=False, cash_manager=cm)

    def _apply_confirm_fill_fallback(
        self,
        *,
        now: datetime,
        etf_code: str,
        filled_qty: int,
        avg_price: float,
        cause: str,
    ) -> None:
        code = str(etf_code)
        q = int(filled_qty)
        px = float(avg_price)
        if q <= 0 or px <= 0:
            return
        ps = self._pos_fsm.upsert_position(etf_code=code)
        prev_state = ps.state
        prev_qty = int(ps.total_qty)
        prev_avg = float(ps.avg_cost)
        new_qty = int(prev_qty) + int(q)
        if new_qty <= 0:
            degrade_once(
                f"live_confirm_fill_fallback_invalid_qty:{code}",
                (
                    "Live confirm-fill fallback computed non-positive quantity; "
                    f"position unchanged. etf={code} prev_qty={prev_qty} fill_qty={q} cause={cause}"
                ),
            )
            return
        new_avg = (
            (float(prev_avg) * float(prev_qty) + float(px) * float(q)) / float(new_qty)
            if int(prev_qty) > 0
            else float(px)
        )

        ps.total_qty = int(new_qty)
        ps.avg_cost = float(new_avg)
        ps.same_day_buy_qty = int(getattr(ps, "same_day_buy_qty", 0) or 0) + int(q)
        if prev_state == FSMState.S5_REDUCED:
            ps.state = FSMState.S4_FULL
            ps.base_qty = int(new_qty)
            ps.scale_1_qty = 0
            ps.scale_2_qty = 0
            ps.scale_count = max(int(ps.scale_count), 2)
        elif prev_state in (FSMState.S0_IDLE, FSMState.S1_TRIAL, FSMState.S2_BASE):
            ps.state = FSMState.S2_BASE
            ps.base_qty = int(new_qty)
        else:
            ps.base_qty = min(int(new_qty), max(int(ps.base_qty), int(prev_qty)))
        if not str(ps.entry_date or "").strip():
            ps.entry_date = now.strftime("%Y-%m-%d")

        degrade_once(
            f"live_confirm_fill_fsm_fallback:{code}",
            (
                "Live confirm fill hit FSM transition conflict; fallback position reconcile applied. "
                f"etf={code} cause={cause} prev_state={prev_state} new_state={ps.state} "
                f"prev_qty={prev_qty} fill_qty={q} new_qty={new_qty}"
            ),
        )
        alert_once(
            f"live_confirm_fill_fallback_alert:{code}:{now.strftime('%Y%m%d')}:{str(cause).split(':', 1)[0]}",
            (
                "Live confirm fill fallback triggered. "
                f"etf={code} now={now.isoformat(timespec='seconds')} cause={cause} "
                f"prev_state={prev_state} new_state={ps.state} prev_qty={prev_qty} fill_qty={q} new_qty={new_qty}"
            ),
        )
        self._logger.warning(
            "confirm fill fallback applied | etf=%s cause=%s prev_state=%s new_state=%s prev_qty=%s fill_qty=%s new_qty=%s avg=%.6f",
            str(code),
            str(cause),
            str(prev_state),
            str(ps.state),
            int(prev_qty),
            int(q),
            int(new_qty),
            float(new_avg),
        )

    def _confirm_entry_order(self, *, now: datetime, pe, order_id: int, is_trial: bool, cash_manager: CashManager) -> None:
        try:
            res = self._trading.confirm_order(int(order_id), timeout_s=10.0)
        except Exception as e:
            self._logger.error("confirm_order failed for %s: %s", getattr(pe, "etf_code", ""), e)
            return

        if res.status not in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED):
            if res.status == OrderStatus.UNKNOWN:
                degrade_once(
                    f"entry_confirm_unknown:{int(order_id)}",
                    (
                        "entry order confirm returned UNKNOWN; will retry on next tick. "
                        f"order_id={int(order_id)} etf={str(getattr(pe, 'etf_code', '') or '')}"
                    ),
                )
            return

        qty_fallback = int(getattr(pe, "trial_qty" if is_trial else "confirm_qty", 0) or 0)
        fill = _extract_fill(res, fallback_qty=qty_fallback)
        code = str(getattr(pe, "etf_code", "") or "")

        if res.status == OrderStatus.FILLED and int(fill.filled_qty) > 0 and float(fill.avg_price) > 0:
            spent = float(fill.avg_price) * int(fill.filled_qty)
            fee = self._trade_fee(price=float(fill.avg_price), qty=int(fill.filled_qty))
            self._state.cash = max(0.0, float(self._state.cash) - float(spent) - float(fee))
            _ = cash_manager.release_cash(int(order_id))
            if is_trial:
                pe.status = "PENDING_CONFIRM"
                try:
                    self._pos_fsm.on_trial_filled(code, int(fill.filled_qty), float(fill.avg_price))
                except AssertionError as e:
                    self._apply_confirm_fill_fallback(
                        now=now,
                        etf_code=code,
                        filled_qty=int(fill.filled_qty),
                        avg_price=float(fill.avg_price),
                        cause=f"trial_filled_assert:{repr(e)}",
                    )
            else:
                pe.status = "CONFIRM_FILLED"
                try:
                    self._pos_fsm.on_confirm_filled(code, int(fill.filled_qty), float(fill.avg_price))
                except AssertionError as e:
                    self._apply_confirm_fill_fallback(
                        now=now,
                        etf_code=code,
                        filled_qty=int(fill.filled_qty),
                        avg_price=float(fill.avg_price),
                        cause=f"confirm_filled_assert:{repr(e)}",
                    )
                self._entry_fsm.remove_pending_entry(pe=pe)
            self._sync_asset_after_trade_fill(
                fallback_cash=float(self._state.cash),
                context=f"entry:{code}:{'trial' if is_trial else 'confirm'}:{int(order_id)}",
            )
        else:
            pe.status = "FAILED"
            _ = cash_manager.release_cash(int(order_id))
            self._pos_fsm.on_entry_failed(code)

        self._sm.save(self._state)





