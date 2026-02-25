from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional


@dataclass(frozen=True)
class StrategyConfig:
    state_path: str = "data/state/portfolio.json"

    entry_log_path: str = "data/logs/entry_decisions.jsonl"
    exit_log_path: str = "data/logs/exit_decisions.jsonl"
    position_log_path: str = "data/logs/position_decisions.jsonl"
    t0_log_path: str = "data/logs/t0_decisions.jsonl"

    watchlist_etf_codes: tuple[str, ...] = ("512480.SH", "159107.SZ")
    tick_interval_s: float = 3.0

    trading_adapter_type: str = "xt"

    auto_prep: bool = False
    watch_auto: bool = False
    hot_top: int = 15

    xt_session_id: str = ""
    xt_trader_path: str = ""
    xt_account_id: str = ""

    easytrader_broker: str = "ths"
    gui_ops_limit: int = 20
    gui_freeze_threshold: int = 15


def normalize_watchlist(codes: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for c in codes:
        s = str(c).strip()
        if not s:
            continue
        out.append(s)
    return tuple(out)


def with_overrides(
    cfg: StrategyConfig,
    *,
    state_path: Optional[str] = None,
    watchlist_etf_codes: Optional[Iterable[str]] = None,
    tick_interval_s: Optional[float] = None,
    trading_adapter_type: Optional[str] = None,
    auto_prep: Optional[bool] = None,
    watch_auto: Optional[bool] = None,
    hot_top: Optional[int] = None,
    xt_session_id: Optional[str] = None,
    xt_trader_path: Optional[str] = None,
    xt_account_id: Optional[str] = None,
    easytrader_broker: Optional[str] = None,
    gui_ops_limit: Optional[int] = None,
    gui_freeze_threshold: Optional[int] = None,
) -> StrategyConfig:
    updates: dict[str, object] = {}
    if state_path is not None:
        updates["state_path"] = str(state_path)
    if watchlist_etf_codes is not None:
        updates["watchlist_etf_codes"] = normalize_watchlist(watchlist_etf_codes)
    if tick_interval_s is not None:
        updates["tick_interval_s"] = float(tick_interval_s)
    if trading_adapter_type is not None:
        updates["trading_adapter_type"] = str(trading_adapter_type)
    if auto_prep is not None:
        updates["auto_prep"] = bool(auto_prep)
    if watch_auto is not None:
        updates["watch_auto"] = bool(watch_auto)
    if hot_top is not None:
        updates["hot_top"] = int(hot_top)
    if xt_session_id is not None:
        updates["xt_session_id"] = str(xt_session_id)
    if xt_trader_path is not None:
        updates["xt_trader_path"] = str(xt_trader_path)
    if xt_account_id is not None:
        updates["xt_account_id"] = str(xt_account_id)
    if easytrader_broker is not None:
        updates["easytrader_broker"] = str(easytrader_broker)
    if gui_ops_limit is not None:
        updates["gui_ops_limit"] = int(gui_ops_limit)
    if gui_freeze_threshold is not None:
        updates["gui_freeze_threshold"] = int(gui_freeze_threshold)
    return replace(cfg, **updates)


def parse_strategy_config(argv: Optional[list[str]] = None, *, base: Optional[StrategyConfig] = None) -> StrategyConfig:
    import argparse

    b = StrategyConfig() if base is None else base
    p = argparse.ArgumentParser(prog="main.py")
    p.add_argument("--state-path", default=None)
    p.add_argument("--tick-interval", type=float, default=None)
    p.add_argument("--watch", action="append", default=None)
    p.add_argument("--auto-prep", action="store_true")
    p.add_argument("--watch-auto", action="store_true")
    p.add_argument("--hot-top", type=int, default=None)

    p.add_argument("--adapter", choices=["xt", "gui"], default=None)

    p.add_argument("--xt-session", default=None)
    p.add_argument("--xt-path", default=None)
    p.add_argument("--xt-account", default=None)

    p.add_argument("--broker", default=None)
    p.add_argument("--gui-ops-limit", type=int, default=None)
    p.add_argument("--gui-freeze-threshold", type=int, default=None)

    ns = p.parse_args(argv)
    wl = ns.watch
    if bool(ns.watch_auto) and wl is None:
        wl = []
    return with_overrides(
        b,
        state_path=ns.state_path,
        tick_interval_s=ns.tick_interval,
        watchlist_etf_codes=wl,
        trading_adapter_type=ns.adapter,
        auto_prep=bool(ns.auto_prep),
        watch_auto=bool(ns.watch_auto),
        hot_top=ns.hot_top,
        xt_session_id=ns.xt_session,
        xt_trader_path=ns.xt_path,
        xt_account_id=ns.xt_account,
        easytrader_broker=ns.broker,
        gui_ops_limit=ns.gui_ops_limit,
        gui_freeze_threshold=ns.gui_freeze_threshold,
    )
