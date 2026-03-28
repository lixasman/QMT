from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
from pathlib import Path
from typing import Callable, Optional

from core.cash_manager import CashManager
from core.enums import ActionType, FSMState, OrderStatus
from core.models import PortfolioState
from core.time_utils import is_trading_time
from core.validators import assert_action_allowed
from .corporate_actions import apply_price_factor_to_pending_entries, apply_price_factor_to_position_state
from .fail_fast_warn import degrade_once, warn_once
from entry.high_chase import normalize_high_chase_signal_source, phase2_signal_reference_price, scale_high_chase_signal_rows
from entry.phase2 import evaluate_phase2
from entry.phase3_confirmer import Phase3Confirmer, Phase3Context
from entry.types import ConfirmActionType, WatchlistItem
from entry.vwap_tracker import VwapTracker
from strategy_config import StrategyConfig
from strategy_runner import StrategyRunner, _extract_fill

from .adapters import BacktestDataAdapter, BacktestTradingAdapter
from .clock import SimulatedClock
from .sentiment_proxy import compute_sentiment_proxy
from .state_manager import InMemoryStateManager
from .store import MarketDataStore

logger = logging.getLogger("backtest.runner")


@dataclass(frozen=True)
class DailyEquity:
    trade_date: str
    cash: float
    market_value: float
    nav: float
    hwm: float
    drawdown: float


@dataclass(frozen=True)
class BacktestResult:
    daily_curve: list[DailyEquity]
    fills: list[dict[str, object]]
    summary: dict[str, object]


class BacktestStrategyRunner(StrategyRunner):
    def __init__(
        self,
        config: StrategyConfig,
        *,
        data: BacktestDataAdapter,
        trading: BacktestTradingAdapter,
        state_manager: InMemoryStateManager,
        fee_rate: float,
        disable_t0_ops: bool = True,
        bt_no_reentry_after_confirm: bool | None = None,
        bt_skip_high_chase_after_first_signal: bool | None = None,
        bt_high_chase_signal_source: str | None = None,
        bt_high_chase_lookback_days: int | None = None,
        bt_high_chase_max_rise: float | None = None,
        bt_high_chase_signals: Optional[dict[str, list[tuple[date, float]]]] = None,
    ) -> None:
        self._bt_fee_rate = float(max(0.0, fee_rate))
        self._store = getattr(data, "_store", None)
        if self._store is None:
            raise RuntimeError("BacktestStrategyRunner requires BacktestDataAdapter with store")
        super().__init__(config=config, data=data, trading=trading, state_manager=state_manager)
        self._bt_logger = logging.getLogger("backtest.runner")
        self._phase2_recent_days_cache: dict[tuple[str, int], list[date]] = {}
        self._phase2_open_ok_cache: dict[tuple[str, str], bool] = {}
        self._phase2_open_cov_cache: dict[tuple[str, str, int], float] = {}
        self._phase2_chip_rows_cache: dict[str, dict[str, dict[str, str]]] = {}
        self._phase2_micro_cov_cache: dict[tuple[str, str, int], float] = {}
        self._disable_t0_ops = bool(disable_t0_ops)
        resolved_no_reentry_after_confirm = (
            getattr(config, "phase2_no_reentry_after_confirm", False)
            if bt_no_reentry_after_confirm is None
            else bt_no_reentry_after_confirm
        )
        resolved_skip_high_chase_after_first_signal = (
            getattr(config, "phase2_skip_high_chase_after_first_signal", False)
            if bt_skip_high_chase_after_first_signal is None
            else bt_skip_high_chase_after_first_signal
        )
        resolved_signal_source = (
            getattr(config, "phase2_high_chase_signal_source", "all_signals")
            if bt_high_chase_signal_source is None
            else bt_high_chase_signal_source
        )
        resolved_lookback_days = (
            getattr(config, "phase2_high_chase_lookback_days", 60)
            if bt_high_chase_lookback_days is None
            else bt_high_chase_lookback_days
        )
        resolved_max_rise = (
            getattr(config, "phase2_high_chase_max_rise", 0.15)
            if bt_high_chase_max_rise is None
            else bt_high_chase_max_rise
        )
        self._bt_no_reentry_after_confirm = bool(resolved_no_reentry_after_confirm)
        self._bt_skip_high_chase_after_first_signal = bool(resolved_skip_high_chase_after_first_signal)
        self._bt_high_chase_signal_source = normalize_high_chase_signal_source(resolved_signal_source)
        self._bt_high_chase_lookback_days = int(max(1, int(resolved_lookback_days)))
        self._bt_high_chase_max_rise = float(max(0.0, float(resolved_max_rise)))
        self._bt_high_chase_signals = bt_high_chase_signals if bt_high_chase_signals is not None else {}
        if self._disable_t0_ops:
            execute_t0_live = getattr(self._pos_fsm, "execute_t0_live", None)
            if callable(execute_t0_live):
                self._pos_fsm.execute_t0_live = lambda *args, **kwargs: None
                warn_once(
                    "bt_t0_exec_disabled",
                    (
                        "Backtest disabled execute_t0_live by default to reduce intraday execution noise. "
                        "Pass --enable-t0-exec to restore original behavior."
                    ),
                    logger_name="backtest.runner",
                )
            else:
                degrade_once(
                    "bt_t0_exec_disable_missing_api",
                    "Backtest requested t0-exec disable but PositionFSM has no execute_t0_live callable.",
                    logger_name="backtest.runner",
                )
        self._bt_logger.debug(
            "runner init | fee_rate=%.8f disable_t0_ops=%s",
            float(self._bt_fee_rate),
            bool(self._disable_t0_ops),
        )

    @staticmethod
    def _advance_backtest_clock(
        *,
        now_provider: Callable[[], datetime],
        sleep_fn: Callable[[float], None],
        target: datetime,
    ) -> None:
        now = now_provider()
        delta_s = float((target - now).total_seconds())
        if delta_s > 0:
            sleep_fn(float(delta_s))

    @staticmethod
    def _next_session_boundary(now: datetime) -> datetime:
        day = now.date()
        morning_open = datetime.combine(day, time(9, 30))
        afternoon_open = datetime.combine(day, time(13, 0))
        close_time = datetime.combine(day, time(15, 1))
        if now < morning_open:
            return morning_open
        if datetime.combine(day, time(11, 30)) <= now < afternoon_open:
            return afternoon_open
        return close_time

    def _has_intraday_work(self) -> bool:
        pending_states = {"PENDING_TRIAL", "PENDING_CONFIRM", "TRIAL_PLACED", "CONFIRM_PLACED"}
        for pe in list(self._state.pending_entries):
            st = str(getattr(pe, "status", "") or "")
            if st in pending_states:
                return True
        for ps in list(self._state.positions.values()):
            if int(getattr(ps, "total_qty", 0) or 0) > 0:
                return True
        return False

    def _intraday_loop(
        self,
        *,
        now_provider: Callable[[], datetime],
        sleep_fn: Callable[[float], None],
        max_ticks: Optional[int],
    ) -> None:
        self._logger.info("intraday start")
        if not self._has_intraday_work():
            self._advance_backtest_clock(
                now_provider=now_provider,
                sleep_fn=sleep_fn,
                target=datetime.combine(now_provider().date(), time(15, 1)),
            )
            self._logger.info("intraday end | ticks=0")
            return

        n = 0
        while True:
            now = now_provider()
            if now.time() >= time(15, 1):
                break
            if not is_trading_time(now):
                self._advance_backtest_clock(
                    now_provider=now_provider,
                    sleep_fn=sleep_fn,
                    target=self._next_session_boundary(now),
                )
                continue
            self._tick_cycle(now=now)
            n += 1
            if max_ticks is not None and n >= int(max_ticks):
                break
            if not self._has_intraday_work():
                self._advance_backtest_clock(
                    now_provider=now_provider,
                    sleep_fn=sleep_fn,
                    target=datetime.combine(now.date(), time(15, 1)),
                )
                break
            sleep_fn(float(self._cfg.tick_interval_s))
        self._logger.info("intraday end | ticks=%s", n)

    def _recent_trade_days(self, *, end_day: date, window: int) -> list[date]:
        n = int(max(0, int(window)))
        if n <= 0:
            return []
        key = (end_day.strftime("%Y%m%d"), int(n))
        cached = self._phase2_recent_days_cache.get(key)
        if cached is not None:
            return list(cached)
        span_days = max(60, int(n) * 4)
        start_day = end_day - timedelta(days=int(span_days))
        days = self._store.available_days(start=start_day.strftime("%Y%m%d"), end=end_day.strftime("%Y%m%d"))
        if len(days) > n:
            days = days[-n:]
        self._phase2_recent_days_cache[key] = list(days)
        return list(days)

    def _open_tick_ok(self, *, code: str, day: date) -> bool:
        c = str(code or "").strip().upper()
        key = (c, day.strftime("%Y%m%d"))
        cached = self._phase2_open_ok_cache.get(key)
        if cached is not None:
            return bool(cached)
        dt = datetime.combine(day, time(9, 30))
        snap = self._store.tick_snapshot(code=c, now=dt)
        ok = bool(snap is not None)
        self._phase2_open_ok_cache[key] = bool(ok)
        return bool(ok)

    def _open_tick_coverage(self, *, code: str, end_day: date, window: int) -> float:
        c = str(code or "").strip().upper()
        n = int(max(0, int(window)))
        if n <= 0:
            return 1.0
        key = (c, end_day.strftime("%Y%m%d"), int(n))
        cached = self._phase2_open_cov_cache.get(key)
        if cached is not None:
            return float(cached)
        days = self._recent_trade_days(end_day=end_day, window=n)
        if not days:
            self._phase2_open_cov_cache[key] = 0.0
            return 0.0
        ok = 0
        for d in days:
            if self._open_tick_ok(code=c, day=d):
                ok += 1
        cov = float(ok) / float(len(days))
        self._phase2_open_cov_cache[key] = float(cov)
        return float(cov)

    def _load_chip_rows_for_day(self, *, day: date) -> dict[str, dict[str, str]]:
        day_s = day.strftime("%Y%m%d")
        cached = self._phase2_chip_rows_cache.get(day_s)
        if cached is not None:
            return cached
        path = Path("output") / "integration" / "chip" / f"batch_results_{day_s}.csv"
        rows: dict[str, dict[str, str]] = {}
        if not path.exists():
            self._phase2_chip_rows_cache[day_s] = rows
            return rows
        try:
            from integrations.watchlist_loader import normalize_etf_code
        except Exception:
            normalize_etf_code = lambda x: str(x or "").strip().upper()  # type: ignore[assignment]
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    code = normalize_etf_code(str(row.get("code") or ""))
                    if not code:
                        continue
                    rows[str(code)] = {str(k): ("" if v is None else str(v)) for k, v in row.items()}
        except Exception as e:
            degrade_once(
                f"bt_phase2_chip_rows_load_failed:{day_s}",
                f"Backtest phase2 chip rows load failed; treated as empty. day={day_s} err={repr(e)}",
                logger_name="backtest.runner",
            )
            rows = {}
        self._phase2_chip_rows_cache[day_s] = rows
        return rows

    @staticmethod
    def _row_has_micro_fields(row: dict[str, str]) -> bool:
        def _ok(keys: tuple[str, ...]) -> bool:
            for k in keys:
                v = str(row.get(k) or "").strip()
                if v and v.lower() not in {"nan", "none", "null"}:
                    return True
            return False

        has_vpin = _ok(("ms_vpin_rank", "vpin_rank"))
        has_ofi = _ok(("ms_ofi_daily_z", "ofi_daily_z"))
        return bool(has_vpin and has_ofi)

    def _micro_coverage(self, *, code: str, end_day: date, window: int) -> float:
        c = str(code or "").strip().upper()
        n = int(max(0, int(window)))
        if n <= 0:
            return 1.0
        key = (c, end_day.strftime("%Y%m%d"), int(n))
        cached = self._phase2_micro_cov_cache.get(key)
        if cached is not None:
            return float(cached)
        days = self._recent_trade_days(end_day=end_day, window=n)
        if not days:
            self._phase2_micro_cov_cache[key] = 0.0
            return 0.0
        ok = 0
        for d in days:
            row = self._load_chip_rows_for_day(day=d).get(c) or {}
            if row and self._row_has_micro_fields(row):
                ok += 1
        cov = float(ok) / float(len(days))
        self._phase2_micro_cov_cache[key] = float(cov)
        return float(cov)

    def _allow_phase2_candidate(self, *, now: datetime, item: WatchlistItem) -> tuple[bool, str]:
        code = str(item.etf_code or "").strip().upper()
        reasons: list[str] = []

        min_chip_days = int(max(0, int(getattr(self._cfg, "phase2_min_chip_days", 0) or 0)))
        if min_chip_days > 0:
            ext = self._ext_factors.get(code) or {}
            chip_days = int(float(ext.get("chip_engine_days", 0) or 0))
            if int(chip_days) < int(min_chip_days):
                reasons.append(f"chip_days<{int(min_chip_days)} ({int(chip_days)})")

        open_win = int(max(0, int(getattr(self._cfg, "phase2_open_coverage_window", 0) or 0)))
        min_open_cov = float(getattr(self._cfg, "phase2_min_open_coverage", 0.0) or 0.0)
        if open_win > 0 and min_open_cov > 0:
            cov = float(self._open_tick_coverage(code=code, end_day=now.date(), window=int(open_win)))
            if float(cov) + 1e-12 < float(min_open_cov):
                reasons.append(f"open_cov<{float(min_open_cov):.2f} ({float(cov):.2f})")

        micro_win = int(max(0, int(getattr(self._cfg, "phase2_micro_coverage_window", 0) or 0)))
        min_micro_cov = float(getattr(self._cfg, "phase2_min_micro_coverage", 0.0) or 0.0)
        if micro_win > 0 and min_micro_cov > 0:
            cov = float(self._micro_coverage(code=code, end_day=now.date(), window=int(micro_win)))
            if float(cov) + 1e-12 < float(min_micro_cov):
                reasons.append(f"micro_cov<{float(min_micro_cov):.2f} ({float(cov):.2f})")

        if reasons:
            return False, "; ".join(reasons)
        return True, ""

    @staticmethod
    def _bt_signal_reference_price(*, close_signal_day: float, h_signal: float) -> float:
        return phase2_signal_reference_price(close_signal_day=float(close_signal_day), h_signal=float(h_signal))

    @staticmethod
    def _normalize_bt_high_chase_signal_source(value: object) -> str:
        return normalize_high_chase_signal_source(value)

    def _phase2_no_reentry_after_confirm_enabled(self) -> bool:
        return bool(self._bt_no_reentry_after_confirm)

    def _phase2_skip_high_chase_after_first_signal_enabled(self) -> bool:
        return bool(self._bt_skip_high_chase_after_first_signal)

    def _phase2_high_chase_signal_source(self) -> str:
        return str(self._bt_high_chase_signal_source)

    def _phase2_high_chase_lookback_days(self) -> int:
        return int(self._bt_high_chase_lookback_days)

    def _phase2_high_chase_max_rise(self) -> float:
        return float(self._bt_high_chase_max_rise)

    def _get_phase2_high_chase_signal_rows(self, *, code: str) -> list[tuple[date, float]]:
        key = str(code or "").strip().upper()
        raw = list(self._bt_high_chase_signals.get(key) or [])
        out: list[tuple[date, float]] = []
        for signal_day, ref_price in raw:
            if isinstance(signal_day, datetime):
                signal_day = signal_day.date()
            if not isinstance(signal_day, date):
                continue
            px = float(ref_price)
            if px <= 0:
                continue
            out.append((signal_day, px))
        out.sort(key=lambda x: x[0])
        return out

    def _set_phase2_high_chase_signal_rows(self, *, code: str, rows: list[tuple[date, float]]) -> None:
        key = str(code or "").strip().upper()
        if not key:
            return
        if rows:
            self._bt_high_chase_signals[key] = list(rows)
        else:
            self._bt_high_chase_signals.pop(key, None)

    def _bt_high_chase_uses_all_signals(self) -> bool:
        return self._phase2_high_chase_uses_all_signals()

    def _bt_high_chase_uses_missed_executable(self) -> bool:
        return self._phase2_high_chase_uses_missed_executable()

    def _prune_bt_high_chase_signals(self, *, code: str, now_day: date) -> list[tuple[date, float]]:
        code2 = str(code or "").strip().upper()
        kept, _, _ = should_block_high_chase_signal(
            rows=self._get_phase2_high_chase_signal_rows(code=code2),
            now_day=now_day,
            ref_price=0.0,
            lookback_days=self._phase2_high_chase_lookback_days(),
            max_rise=self._phase2_high_chase_max_rise(),
        )
        self._set_phase2_high_chase_signal_rows(code=code2, rows=kept)
        return list(kept)

    def _remember_bt_high_chase_signal(self, *, now: datetime, etf_code: str, ref_price: float) -> bool:
        return self._remember_phase2_high_chase_signal(now=now, etf_code=etf_code, ref_price=ref_price)

    def _remember_bt_missed_executable_signal(self, *, now: datetime, pe, act) -> None:
        _ = self._remember_phase2_missed_executable_signal(now=now, pe=pe, act=act)

    def _remember_bt_blocked_continuation_signal(
        self,
        *,
        now: datetime,
        etf_code: str,
        close_signal_day: float,
        h_signal: float,
        note: str,
    ) -> bool:
        return self._remember_phase2_blocked_continuation_signal(
            now=now,
            etf_code=etf_code,
            close_signal_day=close_signal_day,
            h_signal=h_signal,
            note=note,
        )

    def _should_block_bt_high_chase_signal(self, *, now: datetime, etf_code: str, ref_price: float) -> tuple[bool, str]:
        return self._should_block_phase2_high_chase_signal(now=now, etf_code=etf_code, ref_price=ref_price)

    def _fee(self, *, price: float, qty: int) -> float:
        if int(qty) <= 0 or float(price) <= 0:
            return 0.0
        return float(float(price) * int(qty) * float(self._bt_fee_rate))

    def _sync_state_cash_from_trader(self) -> None:
        cash_before = float(self._state.cash)
        cash_val: Optional[float] = None
        source = ""
        try:
            cash_val = float(getattr(self._trading, "cash"))
            source = "trading.cash"
        except Exception as e:
            self._bt_logger.debug("cash sync read trading.cash failed | err=%r", e)
            cash_val = None
        if cash_val is None:
            try:
                raw = self._trading.query_asset()
                if isinstance(raw, dict):
                    c = raw.get("cash") if raw.get("cash") is not None else raw.get("available_cash")
                    if c is not None:
                        cash_val = float(c)
                        source = "query_asset.cash"
                    else:
                        self._bt_logger.debug("cash sync query_asset missing keys | payload=%s", raw)
                else:
                    self._bt_logger.debug("cash sync query_asset non-dict payload | type=%s", type(raw).__name__)
            except Exception as e:
                self._bt_logger.debug("cash sync query_asset failed | err=%r", e)
                cash_val = None
        if cash_val is not None:
            self._state.cash = float(cash_val)
            self._bt_logger.debug(
                "cash sync success | source=%s before=%.6f after=%.6f",
                str(source or "unknown"),
                float(cash_before),
                float(self._state.cash),
            )
            return
        degrade_once(
            "bt_cash_sync_failed",
            "Backtest cash sync failed from trading adapter; state.cash keeps previous value.",
            logger_name="backtest.runner",
        )
        self._bt_logger.warning("cash sync failed | before=%.6f source=none", float(cash_before))

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
                    f"bt_sentiment_proxy_short_bars:{str(code)}",
                    (
                        "Backtest sentiment proxy fallback due to insufficient daily bars. "
                        f"etf={code} bars={bars_count} fallback=50/0.5"
                    ),
                    logger_name="backtest.runner",
                )
        except Exception as e:
            degrade_once(
                f"bt_sentiment_proxy_failed:{str(code)}",
                f"Backtest sentiment proxy failed; fallback=50/0.5. etf={code} err={repr(e)}",
                logger_name="backtest.runner",
            )
            self._bt_logger.error("sentiment proxy failed | etf=%s err=%r", str(code), e)

        try:
            cn = str(normalize_etf_code(str(code)))
        except Exception as e:
            cn = str(code)
            warn_once(
                f"bt_norm_code_failed:{str(code)}",
                f"Backtest normalize_etf_code failed; fallback raw code. etf={code} err={repr(e)}",
                logger_name="backtest.runner",
            )
        for key in {str(code), cn}:
            ext = self._ext_factors.get(key) or {}
            ext["sentiment_score_01"] = float(score01)
            ext["sentiment_score_100"] = int(score100)
            self._ext_factors[key] = ext
        self._bt_logger.debug(
            "sentiment proxy applied | etf=%s normalized=%s bars=%s score100=%s score01=%.3f",
            str(code),
            str(cn),
            int(bars_count),
            int(score100),
            float(score01),
        )
        return int(score100), float(score01)

    def _inject_sentiment_proxy(self, *, watchlist: list[WatchlistItem]) -> list[WatchlistItem]:
        self._bt_logger.debug("inject sentiment start | watchlist=%s held_positions=%s", len(watchlist), len(self._state.positions))
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
        self._bt_logger.debug(
            "inject sentiment done | watchlist=%s position_only=%s",
            len(out),
            int(position_only),
        )
        return out

    def _pre_open(self, *, now: datetime) -> None:
        self._logger.info("backtest pre_open start | now=%s", now.isoformat(timespec="seconds"))
        self._sync_state_cash_from_trader()
        wl: list[WatchlistItem] = []
        wl_raw_count = 0
        wl_proxy_count = 0
        filter_mode = "unknown"
        try:
            watch_auto = bool(getattr(self._cfg, "watch_auto", False))
            watch_auto_no_filter = bool(getattr(self._cfg, "watch_auto_no_filter", False))
            if watch_auto_no_filter and not watch_auto:
                self._logger.warning("watch_auto_no_filter is set but watch_auto is disabled; no_filter flag is ignored")
            wl_raw = self._build_watchlist(now=now)
            wl_raw_count = len(wl_raw)
            wl_proxy = self._inject_sentiment_proxy(watchlist=wl_raw)
            wl_proxy_count = len(wl_proxy)
            wl = list(wl_proxy)
            if watch_auto:
                if watch_auto_no_filter:
                    filter_mode = "watch_auto_no_filter"
                    self._logger.warning(
                        "backtest watch_auto no_filter enabled | all candidates kept before entry FSM | count=%s",
                        len(wl),
                    )
                else:
                    from entry.watchlist import filter_watchlist

                    wl = filter_watchlist(wl, min_sentiment=int(getattr(self._cfg, "min_sentiment_threshold", 60)))
                    filter_mode = "watch_auto_filter"
            else:
                filter_mode = "watch_auto_disabled"
            self._logger.info(
                "backtest watchlist prepared | raw=%s proxy=%s final=%s mode=%s",
                int(wl_raw_count),
                int(wl_proxy_count),
                int(len(wl)),
                str(filter_mode),
            )
        except Exception as e:
            self._logger.error("backtest build_watchlist failed: %s", e)
        if not wl:
            warn_once(
                f"bt_empty_watchlist:{now.strftime('%Y%m%d')}",
                (
                    "Backtest pre_open watchlist is empty after preprocessing; no new entries will be attempted. "
                    f"date={now.strftime('%Y-%m-%d')}"
                ),
                logger_name="backtest.runner",
            )

        self._day_watch_codes = [str(it.etf_code) for it in wl if str(it.etf_code).strip()]
        self._bt_logger.debug(
            "backtest watchlist codes | date=%s count=%s sample=%s",
            now.date().isoformat(),
            len(self._day_watch_codes),
            ",".join(self._day_watch_codes[:12]),
        )
        self._log_watchlist_snapshot(now=now, watchlist=wl)

        for code in list(self._state.positions.keys()):
            ps = self._pos_fsm.upsert_position(etf_code=code)
            ps.same_day_buy_qty = 0

        try:
            _ = self._exit_fsm.execute_pending_locked(now=now)
        except Exception as e:
            self._logger.error("execute_pending_locked failed: %s", e)

        for code in list(self._state.positions.keys()):
            ps = self._pos_fsm.upsert_position(etf_code=code)
            ps.t0_daily_pnl = 0.0
        self._bt_logger.debug("pre_open reset t0 daily pnl | positions=%s", len(self._state.positions))

        try:
            self._entry_fsm.upsert_watchlist(d=now, watchlist=wl)
        except Exception as e:
            self._logger.error("upsert_watchlist failed: %s", e)

        self._sm.save(self._state)
        self._logger.info("backtest pre_open end | now=%s", now.isoformat(timespec="seconds"))

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
                    self._remember_bt_blocked_continuation_signal(
                        now=now,
                        etf_code=str(it.etf_code),
                        close_signal_day=float(getattr(res, "close_signal_day", 0.0) or 0.0),
                        h_signal=float(getattr(res, "h_signal", 0.0) or 0.0),
                        note=str(getattr(res, "note", "") or ""),
                    )
                    continue
                signal_price = self._bt_signal_reference_price(
                    close_signal_day=float(getattr(res.signal_fired, "close_signal_day", 0.0) or 0.0),
                    h_signal=float(getattr(res.signal_fired, "h_signal", 0.0) or 0.0),
                )
                should_block, block_reason = self._should_block_bt_high_chase_signal(
                    now=now,
                    etf_code=str(it.etf_code),
                    ref_price=float(signal_price),
                )
                if self._bt_high_chase_uses_all_signals():
                    self._remember_bt_high_chase_signal(
                        now=now,
                        etf_code=str(it.etf_code),
                        ref_price=float(signal_price),
                    )
                should_block_entry, entry_reason = self._should_block_phase2_entry_after_signal(
                    now=now,
                    etf_code=str(it.etf_code),
                )
                if should_block:
                    high_chase_blocked += 1
                    self._bt_logger.info(
                        "phase2 high-chase blocked | etf=%s reason=%s",
                        str(it.etf_code),
                        str(block_reason),
                    )
                    continue
                if should_block_entry:
                    entry_blocked += 1
                    self._bt_logger.info(
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

    def _process_pending_entry(self, *, now: datetime, pe) -> None:
        if str(getattr(pe, "status", "")) not in ("PENDING_TRIAL", "PENDING_CONFIRM"):
            return

        code = str(getattr(pe, "etf_code", "") or "")
        if not code:
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
        self._remember_bt_missed_executable_signal(now=now, pe=pe, act=act)
        try:
            self._entry_fsm.apply_confirm_action(pe=pe, act=act)
        except Exception as e:
            self._logger.error("apply_confirm_action failed for %s: %s", code, e)

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
                f"bt_confirm_fill_fallback_invalid_qty:{code}",
                (
                    "Backtest confirm-fill fallback computed non-positive quantity; "
                    f"position unchanged. etf={code} prev_qty={prev_qty} fill_qty={q} cause={cause}"
                ),
                logger_name="backtest.runner",
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
            f"bt_confirm_fill_fsm_fallback:{code}",
            (
                "Backtest confirm fill hit FSM transition conflict; fallback position reconcile applied. "
                f"etf={code} cause={cause} prev_state={prev_state} new_state={ps.state} "
                f"prev_qty={prev_qty} fill_qty={q} new_qty={new_qty}"
            ),
            logger_name="backtest.runner",
        )
        self._bt_logger.warning(
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
        code = str(getattr(pe, "etf_code", "") or "")
        self._bt_logger.debug(
            "confirm entry start | now=%s etf=%s order_id=%s is_trial=%s pe_status=%s",
            now.isoformat(timespec="seconds"),
            str(code),
            int(order_id),
            bool(is_trial),
            str(getattr(pe, "status", "") or ""),
        )
        try:
            res = self._trading.confirm_order(int(order_id), timeout_s=10.0)
        except Exception as e:
            self._logger.error("confirm_order failed for %s: %s", getattr(pe, "etf_code", ""), e)
            return
        self._bt_logger.debug(
            "confirm entry result | etf=%s order_id=%s status=%s error=%s",
            str(code),
            int(order_id),
            str(res.status.value),
            str(res.error or ""),
        )

        if res.status not in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED):
            if res.status == OrderStatus.UNKNOWN:
                degrade_once(
                    f"bt_entry_confirm_unknown:{int(order_id)}",
                    (
                        "Backtest entry confirm returned UNKNOWN; this order will be retried next tick. "
                        f"etf={code} order_id={int(order_id)}"
                    ),
                    logger_name="backtest.runner",
                )
                self._logger.warning(
                    "entry confirm unknown | etf=%s order_id=%s",
                    str(code),
                    int(order_id),
                )
            else:
                self._bt_logger.debug(
                    "entry confirm pending | etf=%s order_id=%s status=%s",
                    str(code),
                    int(order_id),
                    str(res.status.value),
                )
            return

        qty_fallback = int(getattr(pe, "trial_qty" if is_trial else "confirm_qty", 0) or 0)
        fill = _extract_fill(res, fallback_qty=qty_fallback)

        if res.status == OrderStatus.FILLED and int(fill.filled_qty) > 0 and float(fill.avg_price) > 0:
            spent = float(fill.avg_price) * int(fill.filled_qty)
            fee = self._fee(price=float(fill.avg_price), qty=int(fill.filled_qty))
            cash_before = float(self._state.cash)
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
            self._bt_logger.info(
                "entry filled | etf=%s order_id=%s is_trial=%s qty=%s avg_price=%.6f spent=%.6f fee=%.6f cash_before=%.6f cash_after=%.6f",
                str(code),
                int(order_id),
                bool(is_trial),
                int(fill.filled_qty),
                float(fill.avg_price),
                float(spent),
                float(fee),
                float(cash_before),
                float(self._state.cash),
            )
        else:
            pe.status = "FAILED"
            _ = cash_manager.release_cash(int(order_id))
            self._pos_fsm.on_entry_failed(code)
            self._logger.warning(
                "entry failed | etf=%s order_id=%s is_trial=%s status=%s filled_qty=%s avg_price=%s error=%s",
                str(code),
                int(order_id),
                bool(is_trial),
                str(res.status.value),
                int(fill.filled_qty),
                fill.avg_price,
                str(res.error or ""),
            )

        self._sync_state_cash_from_trader()
        self._sm.save(self._state)
        self._bt_logger.debug(
            "confirm entry end | etf=%s order_id=%s pe_status=%s state_cash=%.6f",
            str(code),
            int(order_id),
            str(getattr(pe, "status", "") or ""),
            float(self._state.cash),
        )

    def _handle_exit_sell(self, *, now: datetime, etf_code: str, order_id: int, ps) -> None:
        self._bt_logger.debug(
            "exit confirm start | now=%s etf=%s order_id=%s total_qty=%s state=%s",
            now.isoformat(timespec="seconds"),
            str(etf_code),
            int(order_id),
            int(getattr(ps, "total_qty", 0) or 0),
            str(getattr(ps, "state", "") or ""),
        )
        try:
            res = self._trading.confirm_order(int(order_id), timeout_s=10.0)
        except Exception as e:
            self._logger.error("confirm sell failed for %s: %s", etf_code, e)
            return
        if res.status != OrderStatus.FILLED:
            self._logger.warning(
                "exit sell not filled | etf=%s order_id=%s status=%s error=%s",
                str(etf_code),
                int(order_id),
                str(res.status.value),
                str(res.error or ""),
            )
            return

        before_total = int(ps.total_qty)
        exit_state = ps.state
        fill = _extract_fill(res, fallback_qty=before_total)
        sold_qty = int(min(int(fill.filled_qty), int(before_total))) if int(before_total) > 0 else int(fill.filled_qty)
        if sold_qty <= 0:
            degrade_once(
                f"bt_exit_sold_qty_non_positive:{str(etf_code)}",
                (
                    "Backtest exit confirm filled but sold_qty computed <= 0; position sync may drift. "
                    f"etf={etf_code} order_id={int(order_id)} raw_filled={int(fill.filled_qty)} before_total={int(before_total)}"
                ),
                logger_name="backtest.runner",
            )
            self._logger.warning(
                "exit sold qty invalid | etf=%s order_id=%s raw_filled=%s before_total=%s",
                str(etf_code),
                int(order_id),
                int(fill.filled_qty),
                int(before_total),
            )
            return
        cash_before = float(self._state.cash)
        if float(fill.avg_price) > 0:
            proceeds = float(fill.avg_price) * int(sold_qty)
            fee = self._fee(price=float(fill.avg_price), qty=int(sold_qty))
            self._state.cash = float(self._state.cash) + float(proceeds) - float(fee)
            self._bt_logger.info(
                "exit filled | etf=%s order_id=%s sold_qty=%s avg_price=%.6f proceeds=%.6f fee=%.6f cash_before=%.6f cash_after=%.6f",
                str(etf_code),
                int(order_id),
                int(sold_qty),
                float(fill.avg_price),
                float(proceeds),
                float(fee),
                float(cash_before),
                float(self._state.cash),
            )
        else:
            warn_once(
                f"bt_exit_avg_price_non_positive:{str(etf_code)}",
                (
                    "Backtest exit filled with non-positive avg_price; cash update skipped for this fill. "
                    f"etf={etf_code} order_id={int(order_id)} avg_price={float(fill.avg_price)}"
                ),
                logger_name="backtest.runner",
            )
            self._logger.warning(
                "exit filled with invalid avg_price | etf=%s order_id=%s sold_qty=%s avg_price=%s",
                str(etf_code),
                int(order_id),
                int(sold_qty),
                fill.avg_price,
            )

        if exit_state == FSMState.S0_IDLE:
            if ps.state == FSMState.S0_IDLE:
                ps.state = FSMState.S2_BASE
            self._pos_fsm.on_layer1_clear(etf_code=etf_code, sold_qty=int(sold_qty))
        elif exit_state == FSMState.S5_REDUCED:
            if ps.state == FSMState.S5_REDUCED:
                ps.state = FSMState.S2_BASE
            self._pos_fsm.on_layer2_reduce(etf_code=etf_code, sold_qty=int(sold_qty))
        else:
            self._apply_partial_sell(ps=ps, sold_qty=int(sold_qty))
        self._sync_state_cash_from_trader()
        self._sm.save(self._state)
        self._bt_logger.debug(
            "exit confirm end | etf=%s order_id=%s sold_qty=%s state=%s cash=%.6f",
            str(etf_code),
            int(order_id),
            int(sold_qty),
            str(getattr(ps, "state", "") or ""),
            float(self._state.cash),
        )

    def _reconcile_lifeboat_buyback(self, *, now: datetime, etf_code: str, last_price: float) -> None:
        try:
            total, _sellable, _locked = self._pos_fsm.query_balances(etf_code=str(etf_code))
        except Exception as e:
            self._logger.error("buyback reconcile failed for %s: %s", etf_code, e)
            return
        ps = self._pos_fsm.upsert_position(etf_code=str(etf_code))
        prev_total = int(ps.total_qty)
        delta = int(total) - int(prev_total)
        self._bt_logger.debug(
            "buyback reconcile | now=%s etf=%s prev_total=%s target_total=%s delta=%s last_price=%.6f",
            now.isoformat(timespec="seconds"),
            str(etf_code),
            int(prev_total),
            int(total),
            int(delta),
            float(last_price),
        )
        if delta <= 0:
            self._bt_logger.debug("buyback reconcile skip | etf=%s delta=%s", str(etf_code), int(delta))
            return
        if float(last_price) <= 0:
            degrade_once(
                f"bt_buyback_non_positive_price:{str(etf_code)}",
                (
                    "Backtest lifeboat buyback delta>0 but last_price<=0; cash impact may be underestimated. "
                    f"etf={etf_code} delta={int(delta)} last_price={float(last_price)}"
                ),
                logger_name="backtest.runner",
            )
        self._pos_fsm.on_lifeboat_rebuy(etf_code=str(etf_code), rebuy_qty=int(delta), rebuy_price=float(last_price))
        self._bt_logger.info(
            "buyback reconciled | etf=%s rebuy_qty=%s rebuy_price=%.6f",
            str(etf_code),
            int(delta),
            float(last_price),
        )
        self._sync_state_cash_from_trader()
        self._sm.save(self._state)


class BacktestEngine:
    def __init__(
        self,
        *,
        store: MarketDataStore,
        config: StrategyConfig,
        start_date: str,
        end_date: str,
        initial_cash: float,
        fee_rate: float,
        enable_t0: bool,
        disable_t0_ops: bool = True,
        bt_no_reentry_after_confirm: bool | None = None,
        bt_skip_high_chase_after_first_signal: bool | None = None,
        bt_high_chase_signal_source: str | None = None,
        bt_high_chase_lookback_days: int | None = None,
        bt_high_chase_max_rise: float | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._start_date = str(start_date)
        self._end_date = str(end_date)
        self._initial_cash = float(initial_cash)
        self._fee_rate = float(max(0.0, fee_rate))
        self._enable_t0 = bool(enable_t0)
        self._disable_t0_ops = bool(disable_t0_ops)
        self._bt_no_reentry_after_confirm = bool(getattr(config, "phase2_no_reentry_after_confirm", False)) if bt_no_reentry_after_confirm is None else bool(bt_no_reentry_after_confirm)
        self._bt_skip_high_chase_after_first_signal = bool(getattr(config, "phase2_skip_high_chase_after_first_signal", False)) if bt_skip_high_chase_after_first_signal is None else bool(bt_skip_high_chase_after_first_signal)
        resolved_signal_source = getattr(config, "phase2_high_chase_signal_source", "all_signals") if bt_high_chase_signal_source is None else bt_high_chase_signal_source
        resolved_lookback_days = getattr(config, "phase2_high_chase_lookback_days", 60) if bt_high_chase_lookback_days is None else bt_high_chase_lookback_days
        resolved_max_rise = getattr(config, "phase2_high_chase_max_rise", 0.15) if bt_high_chase_max_rise is None else bt_high_chase_max_rise
        self._bt_high_chase_signal_source = BacktestStrategyRunner._normalize_bt_high_chase_signal_source(resolved_signal_source)
        self._bt_high_chase_lookback_days = int(max(1, int(resolved_lookback_days)))
        self._bt_high_chase_max_rise = float(max(0.0, float(resolved_max_rise)))
        self._bt_high_chase_signals: dict[str, list[tuple[date, float]]] = {}
        self._logger = logging.getLogger("backtest.runner.engine")

    def _mark_market_value(self, *, state: PortfolioState, d: date) -> float:
        now = datetime.combine(d, time(15, 0))
        mv = 0.0
        for code, ps in state.positions.items():
            qty = int(ps.total_qty)
            if qty <= 0:
                continue
            px = self._store.close_on_day(code=str(code), day=d)
            if px is None:
                px = self._store.mark_price(code=str(code), now=now)
                self._logger.debug(
                    "mark to market fallback | date=%s etf=%s qty=%s fallback_price=%.6f",
                    d.isoformat(),
                    str(code),
                    int(qty),
                    float(px),
                )
            if px is None or float(px) <= 0:
                warn_once(
                    f"bt_mark_price_non_positive:{str(code)}:{d.strftime('%Y%m%d')}",
                    (
                        "Backtest market value uses non-positive price; NAV may be understated. "
                        f"date={d.isoformat()} etf={code} qty={qty} price={float(px or 0.0)}"
                    ),
                    logger_name="backtest.runner",
                )
            mv += float(px) * int(qty)
        return float(mv)

    def _apply_corporate_actions_for_day(
        self,
        *,
        d: date,
        state_manager: InMemoryStateManager,
        trading: BacktestTradingAdapter,
    ) -> None:
        st = state_manager.load()
        changed = False
        for code in self._store.codes:
            ev = self._store.corporate_action_on_day(code=str(code), day=d)
            if ev is None:
                continue
            trading_changed = bool(trading.apply_price_factor(etf_code=str(code), price_factor=float(ev.price_factor)))
            pos = st.positions.get(str(code))
            state_changed = False
            if pos is not None:
                apply_price_factor_to_position_state(ps=pos, price_factor=float(ev.price_factor))
                state_changed = True
            pending_changed = int(
                apply_price_factor_to_pending_entries(
                    pending_entries=st.pending_entries,
                    etf_code=str(code),
                    price_factor=float(ev.price_factor),
                )
            )
            high_chase_rows = list(self._bt_high_chase_signals.get(str(code)) or [])
            high_chase_changed = False
            if high_chase_rows:
                scaled_rows = scale_high_chase_signal_rows(rows=high_chase_rows, price_factor=float(ev.price_factor))
                self._bt_high_chase_signals[str(code)] = list(scaled_rows)
                high_chase_changed = bool(scaled_rows != high_chase_rows)
            if trading_changed or state_changed or pending_changed > 0 or high_chase_changed:
                changed = True
                self._logger.info(
                    "corporate action applied | date=%s etf=%s factor=%.6f qty_factor=%.6f trading=%s position=%s pending_entries=%s high_chase_signals=%s",
                    d.isoformat(),
                    str(code),
                    float(ev.price_factor),
                    float(ev.quantity_factor),
                    bool(trading_changed),
                    bool(state_changed),
                    int(pending_changed),
                    bool(high_chase_changed),
                )
        if changed:
            state_manager.save(st)

    def run(self) -> BacktestResult:
        self._logger.info(
            "engine run start | start=%s end=%s initial_cash=%.6f fee_rate=%.8f enable_t0=%s disable_t0_ops=%s no_reentry_after_confirm=%s high_chase_skip=%s high_chase_source=%s high_chase_lookback=%s high_chase_max_rise=%.4f codes=%s",
            str(self._start_date),
            str(self._end_date),
            float(self._initial_cash),
            float(self._fee_rate),
            bool(self._enable_t0),
            bool(self._disable_t0_ops),
            bool(self._bt_no_reentry_after_confirm),
            bool(self._bt_skip_high_chase_after_first_signal),
            str(self._bt_high_chase_signal_source),
            int(self._bt_high_chase_lookback_days),
            float(self._bt_high_chase_max_rise),
            len(self._store.codes),
        )
        days = self._store.available_days(start=self._start_date, end=self._end_date)
        if not days:
            degrade_once(
                f"bt_no_days_for_run:{self._start_date}:{self._end_date}",
                (
                    "Backtest engine found no market days in range; run will fail. "
                    f"start={self._start_date} end={self._end_date}"
                ),
                logger_name="backtest.runner.engine",
            )
            raise RuntimeError(f"no market days found in data range: {self._start_date} - {self._end_date}")

        init_state = PortfolioState(cash=float(self._initial_cash), nav=float(self._initial_cash), hwm=float(self._initial_cash))
        sm = InMemoryStateManager(initial_state=init_state)
        clock = SimulatedClock(datetime.combine(days[0], time(9, 25)))
        data = BacktestDataAdapter(store=self._store, clock=clock)
        trading = BacktestTradingAdapter(
            clock=clock,
            initial_cash=float(self._initial_cash),
            fee_rate=float(self._fee_rate),
            enable_t0=bool(self._enable_t0),
        )

        curve: list[DailyEquity] = []
        for d in days:
            fills_before = len(trading.fills())
            self._logger.info("day run start | date=%s", d.isoformat())
            clock.reset(datetime.combine(d, time(9, 25)))
            self._apply_corporate_actions_for_day(d=d, state_manager=sm, trading=trading)
            runner = BacktestStrategyRunner(
                config=self._config,
                data=data,
                trading=trading,
                state_manager=sm,
                fee_rate=float(self._fee_rate),
                disable_t0_ops=bool(self._disable_t0_ops),
                bt_no_reentry_after_confirm=bool(self._bt_no_reentry_after_confirm),
                bt_skip_high_chase_after_first_signal=bool(self._bt_skip_high_chase_after_first_signal),
                bt_high_chase_signal_source=str(self._bt_high_chase_signal_source),
                bt_high_chase_lookback_days=int(self._bt_high_chase_lookback_days),
                bt_high_chase_max_rise=float(self._bt_high_chase_max_rise),
                bt_high_chase_signals=self._bt_high_chase_signals,
            )
            runner.run_day(wait_for_market=False, now_provider=clock.now, sleep_fn=clock.sleep)

            st = sm.load()
            market_value = self._mark_market_value(state=st, d=d)
            nav = float(st.cash) + float(market_value)
            hwm = max(float(st.hwm), float(nav), float(self._initial_cash))
            st.nav = float(nav)
            st.hwm = float(hwm)
            sm.save(st)
            drawdown = 0.0 if hwm <= 0 else (float(nav) / float(hwm) - 1.0)
            fills_after = len(trading.fills())
            curve.append(
                DailyEquity(
                    trade_date=d.strftime("%Y%m%d"),
                    cash=float(st.cash),
                    market_value=float(market_value),
                    nav=float(nav),
                    hwm=float(hwm),
                    drawdown=float(drawdown),
                )
            )
            self._logger.info(
                "day run end | date=%s cash=%.6f mv=%.6f nav=%.6f hwm=%.6f dd=%.6f day_fills=%s",
                d.isoformat(),
                float(st.cash),
                float(market_value),
                float(nav),
                float(hwm),
                float(drawdown),
                int(max(0, fills_after - fills_before)),
            )

        if not curve:
            degrade_once(
                f"bt_curve_empty:{self._start_date}:{self._end_date}",
                "Backtest engine produced empty equity curve unexpectedly.",
                logger_name="backtest.runner.engine",
            )
            raise RuntimeError("backtest equity curve is empty")

        final_nav = float(curve[-1].nav)
        total_return = 0.0 if self._initial_cash <= 0 else (final_nav / float(self._initial_cash) - 1.0)
        max_drawdown = min((x.drawdown for x in curve), default=0.0)
        annualized = 0.0
        if len(curve) > 0 and total_return > -1.0:
            annualized = (1.0 + float(total_return)) ** (252.0 / float(len(curve))) - 1.0
        fills = trading.fills()
        summary = {
            "start_date": str(curve[0].trade_date),
            "end_date": str(curve[-1].trade_date),
            "days": int(len(curve)),
            "initial_cash": float(self._initial_cash),
            "final_nav": float(final_nav),
            "total_return": float(total_return),
            "annualized_return": float(annualized),
            "max_drawdown": float(max_drawdown),
            "trade_count": int(len(fills)),
            "buy_count": int(sum(1 for x in fills if str(x.get("side")) == "BUY")),
            "sell_count": int(sum(1 for x in fills if str(x.get("side")) == "SELL")),
            "commission_total": float(sum(float(x.get("fee") or 0.0) for x in fills)),
            "t0_enabled": bool(self._enable_t0),
            "t0_exec_enabled": bool(not self._disable_t0_ops),
            "fee_rate": float(self._fee_rate),
        }
        self._logger.info(
            "engine run done | days=%s final_nav=%.6f total_return=%.6f annualized=%.6f max_drawdown=%.6f trades=%s",
            int(len(curve)),
            float(final_nav),
            float(total_return),
            float(annualized),
            float(max_drawdown),
            int(len(fills)),
        )
        return BacktestResult(daily_curve=curve, fills=fills, summary=summary)


def write_backtest_result(*, result: BacktestResult, out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    daily_path = out / "daily_equity.csv"
    fills_path = out / "fills.csv"

    summary_path.write_text(json.dumps(result.summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with daily_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["trade_date", "cash", "market_value", "nav", "hwm", "drawdown"])
        writer.writeheader()
        for row in result.daily_curve:
            writer.writerow(
                {
                    "trade_date": str(row.trade_date),
                    "cash": float(row.cash),
                    "market_value": float(row.market_value),
                    "nav": float(row.nav),
                    "hwm": float(row.hwm),
                    "drawdown": float(row.drawdown),
                }
            )

    with fills_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "order_id", "etf_code", "side", "quantity", "price", "amount", "fee"],
        )
        writer.writeheader()
        for row in result.fills:
            writer.writerow(
                {
                    "timestamp": str(row.get("timestamp") or ""),
                    "order_id": int(row.get("order_id") or 0),
                    "etf_code": str(row.get("etf_code") or ""),
                    "side": str(row.get("side") or ""),
                    "quantity": int(row.get("quantity") or 0),
                    "price": float(row.get("price") or 0.0),
                    "amount": float(row.get("amount") or 0.0),
                    "fee": float(row.get("fee") or 0.0),
                }
            )

    return {
        "summary": str(summary_path),
        "daily_equity": str(daily_path),
        "fills": str(fills_path),
    }

