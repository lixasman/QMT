from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from typing import Any

from core.cash_manager import CashManager
from core.constants import TICK_SIZE
from core.enums import ActionType, FSMState, OrderStatus
from core.interfaces import Bar, DataAdapter, OrderResult, TradingAdapter
from core.state_manager import StateManager
from core.time_utils import is_trading_time
from core.warn_utils import warn_once
from core.validators import assert_action_allowed
from entry.entry_fsm import EntryFSM
from entry.phase2 import evaluate_phase2
from entry.phase3_confirmer import Phase3Confirmer, Phase3Context
from entry.types import WatchlistItem
from entry.vwap_tracker import VwapTracker
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
        )
        self._pos_fsm = PositionFSM(
            state_manager=self._sm,
            data=self._data,
            trading=self._trading,
            state=self._state,
            log_path=self._cfg.position_log_path,
            t0_log_path=self._cfg.t0_log_path,
            t0_engine=t0_engine,
        )

        self._vwap: dict[str, VwapTracker] = {}
        self._prev_snap: dict[str, object] = {}
        self._ext_factors: dict[str, dict[str, object]] = {}
        from integrations.chip_history import ChipDPCHistory

        self._dpc_history = ChipDPCHistory(history_dir=Path("data/state/dpc"))

        self._entry_fsm.recover_on_startup()
        self._exit_fsm.recover_on_startup()

    @property
    def state_manager(self) -> StateManager:
        return self._sm

    @property
    def state(self):
        return self._state

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

        self._pre_open(now=now_provider())
        self._intraday_loop(now_provider=now_provider, sleep_fn=sleep_fn, max_ticks=max_ticks)
        self._post_close(now=now_provider())
        self._logger.info("strategy day end | now=%s", now_provider().isoformat(timespec="seconds"))

    def _build_data_adapter(self) -> DataAdapter:
        from core.adapters.data_adapter import XtDataAdapter

        return XtDataAdapter()

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
            except Exception:
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

        return XtTradingAdapter(trader, account=acc)

    def _sync_asset(self) -> None:
        raw = self._trading.query_asset()
        if isinstance(raw, dict):
            cash = raw.get("cash") if raw.get("cash") is not None else raw.get("available_cash")
            nav = raw.get("nav") if raw.get("nav") is not None else raw.get("total_asset") or raw.get("asset")
            if cash is not None:
                self._state.cash = float(_safe_float(cash))
            if nav is not None:
                self._state.nav = float(_safe_float(nav))
        self._sm.save(self._state)

    def _resolve_watch_codes(self, *, now: datetime) -> list[str]:
        raw = [str(x).strip() for x in self._cfg.watchlist_etf_codes if str(x).strip()]
        if not bool(self._cfg.watch_auto):
            return raw
        try:
            from integrations.premarket_prep import finintel_hot_csv_path, prev_trading_date
        except Exception:
            return raw
        t1 = prev_trading_date(now)
        if not t1:
            return raw
        p = finintel_hot_csv_path(day=t1)
        if not p.exists():
            return raw
        hot: list[str] = []
        try:
            import csv

            with p.open("r", encoding="utf-8-sig", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    c = str(row.get("code") or "").strip()
                    if c:
                        hot.append(c)
        except Exception:
            hot = []
        out: list[str] = []
        seen: set[str] = set()
        for c in list(hot) + list(raw):
            s = str(c).strip()
            if not s or s in seen:
                continue
            out.append(s)
            seen.add(s)
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

        res = load_watchlist_items(etf_codes=all_codes, now=now)
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
            out: list[WatchlistItem] = []
            for code in self._cfg.watchlist_etf_codes:
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
            return 50.0
        atr5s = [float(sum(trs[i - 4 : i + 1]) / 5.0) for i in range(4, len(trs))]
        if not atr5s:
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
                    watch_codes=self._cfg.watchlist_etf_codes,
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

        wl: list[WatchlistItem] = []
        try:
            wl = self._build_watchlist(now=now)
            if bool(self._cfg.watch_auto):
                from entry.watchlist import filter_watchlist

                wl = filter_watchlist(wl)
        except Exception as e:
            self._logger.error("build_watchlist failed: %s", e)

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

    def _intraday_loop(
        self,
        *,
        now_provider: Callable[[], datetime],
        sleep_fn: Callable[[float], None],
        max_ticks: Optional[int],
    ) -> None:
        self._logger.info("intraday start")
        n = 0
        while True:
            now = now_provider()
            if now.time() >= datetime.strptime("15:01", "%H:%M").time():
                break
            if not is_trading_time(now):
                sleep_fn(min(30.0, float(self._cfg.tick_interval_s)))
                continue
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
            for it in wl:
                bars = self._data.get_bars(it.etf_code, "1d", 60)
                res = evaluate_phase2(etf_code=it.etf_code, bars=bars, watch=it, signal_date=now.date())
                self._entry_fsm.record_phase2_result(timestamp=now, etf_code=it.etf_code, watch=it, res=res)
                if res.signal_fired is not None:
                    self._entry_fsm.add_pending_entry(fired=res.signal_fired)
        except Exception as e:
            self._logger.error("post_close entry scan failed: %s", e)

        try:
            from core.adapters.gui_trading_adapter import GuiTradingAdapter

            if isinstance(self._trading, GuiTradingAdapter):
                self._trading.exit_freeze_mode()
        except Exception as e:
            self._logger.error("post_close gui reset failed: %s", e)

        self._sm.save(self._state)
        self._logger.info("post_close end | now=%s", now.isoformat(timespec="seconds"))

    def _tick_cycle(self, *, now: datetime) -> None:
        for pe in list(self._state.pending_entries):
            self._process_pending_entry(now=now, pe=pe)

        for code, ps in list(self._state.positions.items()):
            if int(ps.total_qty) <= 0:
                continue
            self._process_position_tick(now=now, etf_code=str(code))

        self._process_placed_orders(now=now)

    def _nav_estimate(self) -> float:
        nav = float(self._state.nav)
        if nav > 0:
            return float(nav)
        est = float(self._state.cash)
        for code, ps in self._state.positions.items():
            if int(ps.total_qty) <= 0:
                continue
            try:
                snap = self._data.get_snapshot(str(code))
                est += float(snap.last_price) * int(ps.total_qty)
            except Exception:
                continue
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
            except Exception:
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
        except Exception:
            return 0

    def _t0_realized_loss_pct(self, *, t0_daily_pnl: float, effective_slot: float) -> float:
        if float(t0_daily_pnl) >= 0 or float(effective_slot) <= 0:
            return 0.0
        return float(min(1.0, abs(float(t0_daily_pnl)) / float(effective_slot)))

    def _compute_stop(self, *, etf_code: str, ps, now: datetime, last_price: float) -> tuple[float, float, float, float]:
        try:
            from exit.chandelier import compute_chandelier_state
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
            except Exception:
                dpc_5d = None
            s_chip = 0.0
            if dpc_5d is not None and len(dpc_5d) >= 5 and chip_days >= 10:
                try:
                    s_chip = float(compute_s_chip(dpc_5d, pr))
                except Exception:
                    s_chip = 0.0
            st = compute_chandelier_state(bars=bars, prev_hh=float(ps.highest_high), reduced=reduced, s_chip=float(s_chip))
            ps.highest_high = max(float(ps.highest_high), float(st.hh))
            self._sm.save(self._state)
            return float(st.stop), float(st.k), float(st.hh), float(st.atr)
        except Exception:
            if float(ps.avg_cost) > 0:
                return float(ps.avg_cost) * 0.97, 0.0, float(ps.highest_high), 0.0
            return float(last_price) * 0.97, 0.0, float(ps.highest_high), 0.0

    def _process_position_tick(self, *, now: datetime, etf_code: str) -> None:
        try:
            snap = self._data.get_snapshot(etf_code)
            assert_action_allowed(snap.data_quality, ActionType.T0_SIGNAL)
        except AssertionError:
            return
        except Exception as e:
            self._logger.error("snapshot failed for %s: %s", etf_code, e)
            return

        ps = self._pos_fsm.upsert_position(etf_code=etf_code)
        ps.highest_high = max(float(ps.highest_high), float(snap.last_price))
        self._sm.save(self._state)

        try:
            _ = self._pos_fsm.evaluate_circuit_breaker(now=now, nav_estimate=self._nav_estimate())
        except Exception as e:
            self._logger.error("circuit breaker failed: %s", e)

        stop_price, k, hh, atr = self._compute_stop(etf_code=etf_code, ps=ps, now=now, last_price=float(snap.last_price))
        days_held = self._days_held(str(ps.entry_date), now)
        cur_ret = self._current_return(last_price=float(snap.last_price), avg_cost=float(ps.avg_cost))
        t0_loss_pct = self._t0_realized_loss_pct(t0_daily_pnl=float(ps.t0_daily_pnl), effective_slot=float(ps.effective_slot))
        data_health = {"L1": snap.data_quality}
        score_soft_layer1 = 0.0

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

        # --- Layer 2 评分 (per-signal degraded scoring, exit spec §3.3) ---
        _DEGRADED = 0.5  # 缺失信号贡献 = 权重 × 0.5
        score_soft_layer2 = 0.0
        bars: list[Bar] = []
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

            # S_diverge — fallback 0.5 per spec §3.3
            try:
                s_diverge = float(compute_s_diverge(bars)) if bars else _DEGRADED
            except Exception:
                s_diverge = _DEGRADED

            # S_time — fallback 0.5 per spec §3.3
            try:
                days_since_high = self._days_since_high_from_bars(bars)
                s_time = float(compute_s_time(days_held=int(days_held), days_since_high=int(days_since_high), current_return=float(cur_ret)))
            except Exception:
                s_time = _DEGRADED

            # S_chip — 冷启动 = 0.0 (设计约定), 计算异常 = 0.5 per spec §3.3
            ext = self._ext_factors.get(str(etf_code)) or {}
            dpc_5d = None
            try:
                dpc_5d = self._dpc_history.get_5d(str(etf_code))
            except Exception:
                dpc_5d = None
            pr = float(ext.get("profit_ratio", 0.0) or 0.0)
            chip_days = int(ext.get("chip_engine_days", 0) or 0)
            s_chip = 0.0
            if dpc_5d is not None and len(dpc_5d) >= 5 and chip_days >= 10:
                try:
                    s_chip = float(compute_s_chip(dpc_5d, pr))
                except Exception:
                    s_chip = _DEGRADED

            # S_sentiment — fallback 0.5 per spec §3.3
            try:
                sent_01 = float(ext.get("sentiment_score_01", 0.5) or 0.5)
                s_sentiment = float(compute_s_sentiment(sent_01))
            except Exception:
                s_sentiment = _DEGRADED

            signals: dict[str, float] = {"S_chip": float(s_chip), "S_sentiment": float(s_sentiment), "S_diverge": float(s_diverge), "S_time": float(s_time)}
            score_soft_layer2 = float(compute_score_soft(signals).score_soft)
        except Exception as e:
            # catastrophic: imports or compute_score_soft itself failed
            # all signals degraded → 0.7*0.5 + 0.7*0.5 + 0.5*0.5 + 0.4*0.5 = 1.15
            score_soft_layer2 = float(0.7 * _DEGRADED + 0.7 * _DEGRADED + 0.5 * _DEGRADED + 0.4 * _DEGRADED)
            self._logger.error("Layer2 scoring catastrophic failure for %s, using full degraded score %.2f: %s", etf_code, score_soft_layer2, e)

        try:
            oid2 = self._exit_fsm.apply_layer2_if_needed(now=now, etf_code=etf_code, score_soft=float(score_soft_layer2))
            if oid2 is not None:
                self._handle_exit_sell(now=now, etf_code=etf_code, order_id=int(oid2), ps=ps)
        except Exception as e:
            self._logger.error("layer2 failed for %s: %s", etf_code, e)

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

        if int(ps.total_qty) <= 0:
            self._pos_fsm.on_layer1_clear(etf_code=str(ps.etf_code), sold_qty=int(prev_total))

    def _handle_exit_sell(self, *, now: datetime, etf_code: str, order_id: int, ps) -> None:
        try:
            res = self._trading.confirm_order(int(order_id), timeout_s=10.0)
        except Exception as e:
            self._logger.error("confirm sell failed for %s: %s", etf_code, e)
            return
        if res.status != OrderStatus.FILLED:
            return
        before_total = int(ps.total_qty)
        exit_state = ps.state
        fill = _extract_fill(res, fallback_qty=before_total)
        sold_qty = int(min(int(fill.filled_qty), int(before_total))) if int(before_total) > 0 else int(fill.filled_qty)
        if sold_qty <= 0:
            return
        if float(fill.avg_price) > 0:
            self._state.cash = float(self._state.cash) + float(fill.avg_price) * int(sold_qty)
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
        except Exception:
            support_price = None
        mv = ext.get("ms_vs_max_logz")
        try:
            ms_vs_max_logz = float(mv) if mv is not None else None
        except Exception:
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
        except AssertionError:
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

                sizing = compute_position_sizing(current_nav=nav, atr_pct_raw=float(atr_pct), strong=bool(getattr(pe, "is_strong", False)))
                amt = float(sizing.trial_amt) if str(pe.status) == "PENDING_TRIAL" else float(sizing.confirm_amt)
                desired_qty = int(amt / last_price / 100.0) * 100
        except Exception:
            desired_qty = 0

        ctx = Phase3Context(
            etf_code=code,
            h_signal=float(getattr(pe, "h_signal", 0.0) or 0.0),
            l_signal=float(getattr(pe, "l_signal", 0.0) or 0.0),
            close_signal_day=float(getattr(pe, "close_signal_day", 0.0) or 0.0),
            atr_20=float(getattr(pe, "atr_20", 0.0) or 0.0),
            expire_yyyymmdd=str(getattr(pe, "expire_date", "") or ""),
            strong=bool(getattr(pe, "is_strong", False)),
        )
        confirmer = Phase3Confirmer(ctx, self._vwap[code])
        act = confirmer.decide(
            now=now,
            snapshot=snap,
            instrument=inst,
            desired_qty=int(desired_qty),
            is_trial=bool(str(pe.status) == "PENDING_TRIAL"),
        )
        try:
            self._entry_fsm.apply_confirm_action(pe=pe, act=act)
        except Exception as e:
            self._logger.error("apply_confirm_action failed for %s: %s", code, e)

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

    def _confirm_entry_order(self, *, now: datetime, pe, order_id: int, is_trial: bool, cash_manager: CashManager) -> None:
        try:
            res = self._trading.confirm_order(int(order_id), timeout_s=10.0)
        except Exception as e:
            self._logger.error("confirm_order failed for %s: %s", getattr(pe, "etf_code", ""), e)
            return

        if res.status not in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED):
            return

        qty_fallback = int(getattr(pe, "trial_qty" if is_trial else "confirm_qty", 0) or 0)
        fill = _extract_fill(res, fallback_qty=qty_fallback)
        code = str(getattr(pe, "etf_code", "") or "")

        if res.status == OrderStatus.FILLED and int(fill.filled_qty) > 0 and float(fill.avg_price) > 0:
            spent = float(fill.avg_price) * int(fill.filled_qty)
            self._state.cash = max(0.0, float(self._state.cash) - float(spent))
            _ = cash_manager.release_cash(int(order_id))
            if is_trial:
                pe.status = "PENDING_CONFIRM"
                self._pos_fsm.on_trial_filled(code, int(fill.filled_qty), float(fill.avg_price))
            else:
                pe.status = "CONFIRM_FILLED"
                self._pos_fsm.on_confirm_filled(code, int(fill.filled_qty), float(fill.avg_price))
        else:
            pe.status = "FAILED"
            _ = cash_manager.release_cash(int(order_id))
            self._pos_fsm.on_entry_failed(code)

        self._sm.save(self._state)
