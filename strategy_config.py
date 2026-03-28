from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional

POSITION_SIZING_BASE_CASH = 400000.0
POSITION_SIZING_BASE_SLOT_CAP = 70000.0
POSITION_SIZING_BASE_RISK_BUDGET_MIN = 2500.0
POSITION_SIZING_BASE_RISK_BUDGET_MAX = 6000.0


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
    watch_auto_no_filter: bool = False
    # If True, watch_auto requires hot csv; when missing, no fallback to static list.
    watch_auto_require_hot_csv: bool = False
    hot_top: int = 10
    # Shared best-strategy baseline: when micro factors are missing, use fixed Phase2 S_micro (0~1).
    phase2_s_micro_missing: Optional[float] = 0.1
    # Optional Phase2 quality gates (0 means disabled).
    phase2_min_chip_days: int = 0
    phase2_open_coverage_window: int = 0
    phase2_min_open_coverage: float = 0.0
    phase2_micro_coverage_window: int = 0
    phase2_min_micro_coverage: float = 0.0
    phase2_no_reentry_after_confirm: bool = True
    phase2_skip_high_chase_after_first_signal: bool = True
    phase2_high_chase_signal_source: str = "all_signals"
    phase2_high_chase_lookback_days: int = 60
    phase2_high_chase_max_rise: float = 0.15
    enable_t0: bool = False
    position_sizing_cash: Optional[float] = None
    position_slot_cap: float = 70000.0
    position_risk_budget_min: float = 2500.0
    position_risk_budget_max: float = 6000.0
    exit_atr_pct_min: Optional[float] = 0.025
    exit_atr_pct_max: Optional[float] = 0.04
    exit_layer2_threshold: float = 0.7
    exit_k_accel_enabled: bool = True
    exit_k_accel_step_pct: float = 0.05
    exit_k_accel_step_k: float = 0.2
    exit_k_accel_k_min: float = 1.0

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


def derive_position_sizing_from_cash(
    account_cash: float,
    *,
    base_cash: float = POSITION_SIZING_BASE_CASH,
    base_slot_cap: float = POSITION_SIZING_BASE_SLOT_CAP,
    base_risk_budget_min: float = POSITION_SIZING_BASE_RISK_BUDGET_MIN,
    base_risk_budget_max: float = POSITION_SIZING_BASE_RISK_BUDGET_MAX,
) -> tuple[float, float, float]:
    cash = float(account_cash)
    if cash <= 0.0:
        raise AssertionError(f"account_cash must be > 0, got {account_cash}")
    scale = float(cash) / float(base_cash)
    return (
        float(base_slot_cap) * float(scale),
        float(base_risk_budget_min) * float(scale),
        float(base_risk_budget_max) * float(scale),
    )


def with_overrides(
    cfg: StrategyConfig,
    *,
    state_path: Optional[str] = None,
    watchlist_etf_codes: Optional[Iterable[str]] = None,
    tick_interval_s: Optional[float] = None,
    trading_adapter_type: Optional[str] = None,
    auto_prep: Optional[bool] = None,
    watch_auto: Optional[bool] = None,
    watch_auto_no_filter: Optional[bool] = None,
    watch_auto_require_hot_csv: Optional[bool] = None,
    hot_top: Optional[int] = None,
    phase2_s_micro_missing: Optional[float] = None,
    phase2_min_chip_days: Optional[int] = None,
    phase2_open_coverage_window: Optional[int] = None,
    phase2_min_open_coverage: Optional[float] = None,
    phase2_micro_coverage_window: Optional[int] = None,
    phase2_min_micro_coverage: Optional[float] = None,
    phase2_no_reentry_after_confirm: Optional[bool] = None,
    phase2_skip_high_chase_after_first_signal: Optional[bool] = None,
    phase2_high_chase_signal_source: Optional[str] = None,
    phase2_high_chase_lookback_days: Optional[int] = None,
    phase2_high_chase_max_rise: Optional[float] = None,
    enable_t0: Optional[bool] = None,
    position_sizing_cash: Optional[float] = None,
    position_slot_cap: Optional[float] = None,
    position_risk_budget_min: Optional[float] = None,
    position_risk_budget_max: Optional[float] = None,
    exit_atr_pct_min: Optional[float] = None,
    exit_atr_pct_max: Optional[float] = None,
    exit_layer2_threshold: Optional[float] = None,
    exit_k_accel_enabled: Optional[bool] = None,
    exit_k_accel_step_pct: Optional[float] = None,
    exit_k_accel_step_k: Optional[float] = None,
    exit_k_accel_k_min: Optional[float] = None,
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
    if watch_auto_no_filter is not None:
        updates["watch_auto_no_filter"] = bool(watch_auto_no_filter)
    if watch_auto_require_hot_csv is not None:
        updates["watch_auto_require_hot_csv"] = bool(watch_auto_require_hot_csv)
    if hot_top is not None:
        updates["hot_top"] = int(hot_top)
    if phase2_s_micro_missing is not None:
        updates["phase2_s_micro_missing"] = float(phase2_s_micro_missing)
    if phase2_min_chip_days is not None:
        updates["phase2_min_chip_days"] = int(phase2_min_chip_days)
    if phase2_open_coverage_window is not None:
        updates["phase2_open_coverage_window"] = int(phase2_open_coverage_window)
    if phase2_min_open_coverage is not None:
        updates["phase2_min_open_coverage"] = float(phase2_min_open_coverage)
    if phase2_micro_coverage_window is not None:
        updates["phase2_micro_coverage_window"] = int(phase2_micro_coverage_window)
    if phase2_min_micro_coverage is not None:
        updates["phase2_min_micro_coverage"] = float(phase2_min_micro_coverage)
    if phase2_no_reentry_after_confirm is not None:
        updates["phase2_no_reentry_after_confirm"] = bool(phase2_no_reentry_after_confirm)
    if phase2_skip_high_chase_after_first_signal is not None:
        updates["phase2_skip_high_chase_after_first_signal"] = bool(phase2_skip_high_chase_after_first_signal)
    if phase2_high_chase_signal_source is not None:
        updates["phase2_high_chase_signal_source"] = str(phase2_high_chase_signal_source)
    if phase2_high_chase_lookback_days is not None:
        updates["phase2_high_chase_lookback_days"] = int(max(1, int(phase2_high_chase_lookback_days)))
    if phase2_high_chase_max_rise is not None:
        updates["phase2_high_chase_max_rise"] = float(max(0.0, float(phase2_high_chase_max_rise)))
    if enable_t0 is not None:
        updates["enable_t0"] = bool(enable_t0)
    if position_sizing_cash is not None:
        updates["position_sizing_cash"] = float(position_sizing_cash)
    if position_slot_cap is not None:
        updates["position_slot_cap"] = float(position_slot_cap)
    if position_risk_budget_min is not None:
        updates["position_risk_budget_min"] = float(position_risk_budget_min)
    if position_risk_budget_max is not None:
        updates["position_risk_budget_max"] = float(position_risk_budget_max)
    if exit_atr_pct_min is not None:
        updates["exit_atr_pct_min"] = float(exit_atr_pct_min) if float(exit_atr_pct_min) > 0 else None
    if exit_atr_pct_max is not None:
        updates["exit_atr_pct_max"] = float(exit_atr_pct_max) if float(exit_atr_pct_max) > 0 else None
    if exit_layer2_threshold is not None:
        updates["exit_layer2_threshold"] = float(max(0.0, min(1.0, float(exit_layer2_threshold))))
    if exit_k_accel_enabled is not None:
        updates["exit_k_accel_enabled"] = bool(exit_k_accel_enabled)
    if exit_k_accel_step_pct is not None:
        updates["exit_k_accel_step_pct"] = float(exit_k_accel_step_pct)
    if exit_k_accel_step_k is not None:
        updates["exit_k_accel_step_k"] = float(exit_k_accel_step_k)
    if exit_k_accel_k_min is not None:
        updates["exit_k_accel_k_min"] = float(exit_k_accel_k_min)
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

    def _float01(s: str) -> float:
        try:
            v = float(s)
        except Exception as e:
            raise argparse.ArgumentTypeError(f"invalid float: {s!r}") from e
        if not (0.0 <= float(v) <= 1.0):
            raise argparse.ArgumentTypeError(f"must be within [0, 1], got {v}")
        return float(v)

    def _positive_float(s: str) -> float:
        try:
            v = float(s)
        except Exception as e:
            raise argparse.ArgumentTypeError(f"invalid float: {s!r}") from e
        if float(v) <= 0.0:
            raise argparse.ArgumentTypeError(f"must be > 0, got {v}")
        return float(v)

    p.add_argument("--state-path", default=None)
    p.add_argument("--tick-interval", type=float, default=None)
    p.add_argument("--watch", action="append", default=None)
    p.add_argument("--auto-prep", action="store_true")
    p.add_argument("--watch-auto", action="store_true")
    p.add_argument("--watch-auto-no-filter", action="store_true")
    p.add_argument("--hot-top", type=int, default=None)
    p.add_argument(
        "--phase2-s-micro-missing",
        type=_float01,
        default=None,
        help="When micro factors are missing, use fixed Phase2 S_micro (0~1). Default: 0.1",
    )
    p.add_argument(
        "--phase2-no-reentry-after-confirm",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Disallow new Phase2 entry once the ETF has reached S2+ until flat (default: enabled)",
    )
    p.add_argument(
        "--phase2-skip-high-chase-after-first-signal",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Skip Phase2 entry when the current signal is too far above the first recent signal (default: enabled)",
    )
    p.add_argument(
        "--phase2-high-chase-signal-source",
        choices=["all_signals", "missed_executable"],
        default=None,
        help="Signal source used to seed the high-chase window (default: all_signals)",
    )
    p.add_argument("--phase2-high-chase-lookback-days", type=int, default=None)
    p.add_argument("--phase2-high-chase-max-rise", type=_float01, default=None)
    p.add_argument(
        "--enable-t0",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow same-day sell / T0 execution. Default: disabled to match best backtest baseline",
    )
    p.add_argument(
        "--position-sizing-cash",
        type=_positive_float,
        default=None,
        help="Derive slot/risk sizing from account cash using the 400000 baseline",
    )
    p.add_argument("--position-slot-cap", type=_positive_float, default=None)
    p.add_argument("--position-risk-budget-min", type=_positive_float, default=None)
    p.add_argument("--position-risk-budget-max", type=_positive_float, default=None)
    p.add_argument("--exit-atr-pct-min", type=float, default=None)
    p.add_argument("--exit-atr-pct-max", type=float, default=None)
    p.add_argument(
        "--exit-layer2-threshold",
        type=_float01,
        default=None,
        help="Exit Layer2 score threshold (default: 0.7)",
    )
    p.add_argument(
        "--exit-k-accel",
        dest="exit_k_accel_enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable profit-accelerating Chandelier K (default: enabled)",
    )
    p.add_argument("--exit-k-accel-step-pct", type=_positive_float, default=None)
    p.add_argument("--exit-k-accel-step-k", type=_positive_float, default=None)
    p.add_argument("--exit-k-accel-k-min", type=_positive_float, default=None)

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
    position_sizing_cash = float(ns.position_sizing_cash) if ns.position_sizing_cash is not None else None
    position_slot_cap = ns.position_slot_cap
    position_risk_budget_min = ns.position_risk_budget_min
    position_risk_budget_max = ns.position_risk_budget_max
    if position_sizing_cash is not None:
        position_slot_cap, position_risk_budget_min, position_risk_budget_max = derive_position_sizing_from_cash(
            float(position_sizing_cash)
        )
    return with_overrides(
        b,
        state_path=ns.state_path,
        tick_interval_s=ns.tick_interval,
        watchlist_etf_codes=wl,
        trading_adapter_type=ns.adapter,
        auto_prep=bool(ns.auto_prep),
        watch_auto=bool(ns.watch_auto),
        watch_auto_no_filter=bool(ns.watch_auto_no_filter),
        hot_top=ns.hot_top,
        phase2_s_micro_missing=ns.phase2_s_micro_missing,
        phase2_no_reentry_after_confirm=ns.phase2_no_reentry_after_confirm,
        phase2_skip_high_chase_after_first_signal=ns.phase2_skip_high_chase_after_first_signal,
        phase2_high_chase_signal_source=ns.phase2_high_chase_signal_source,
        phase2_high_chase_lookback_days=ns.phase2_high_chase_lookback_days,
        phase2_high_chase_max_rise=ns.phase2_high_chase_max_rise,
        enable_t0=ns.enable_t0,
        position_sizing_cash=position_sizing_cash,
        position_slot_cap=position_slot_cap,
        position_risk_budget_min=position_risk_budget_min,
        position_risk_budget_max=position_risk_budget_max,
        exit_atr_pct_min=ns.exit_atr_pct_min,
        exit_atr_pct_max=ns.exit_atr_pct_max,
        exit_layer2_threshold=ns.exit_layer2_threshold,
        exit_k_accel_enabled=ns.exit_k_accel_enabled,
        exit_k_accel_step_pct=ns.exit_k_accel_step_pct,
        exit_k_accel_step_k=ns.exit_k_accel_step_k,
        exit_k_accel_k_min=ns.exit_k_accel_k_min,
        xt_session_id=ns.xt_session,
        xt_trader_path=ns.xt_path,
        xt_account_id=ns.xt_account,
        easytrader_broker=ns.broker,
        gui_ops_limit=ns.gui_ops_limit,
        gui_freeze_threshold=ns.gui_freeze_threshold,
    )
