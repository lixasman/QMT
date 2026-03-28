from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Any

from strategy_config import StrategyConfig, derive_position_sizing_from_cash

from .logging_utils import setup_backtest_logging
from .main import (
    _preflight_chip_coverage,
    _read_codes_file,
    _to_ymd8,
    build_arg_parser,
)
from .runner import BacktestEngine, write_backtest_result
from .store import MarketDataStore
from .universe import DEFAULT_UNIVERSE_CODES, parse_codes

logger = logging.getLogger("backtest.single_etf_batch")


def build_batch_arg_parser() -> argparse.ArgumentParser:
    p = build_arg_parser()
    p.prog = "python -m backtest.single_etf_batch"
    p.description = "Run one isolated backtest per ETF and aggregate long-term results."
    p.set_defaults(watch_auto=False, watch_auto_no_filter=False)
    for action in p._actions:
        if action.dest == "out_dir":
            action.default = "output/backtest_single_etf"
            action.help = "output root for per-ETF runs and aggregate summary"
            break
    p.add_argument("--max-codes", type=int, default=0, help="limit number of ETF runs (0=all)")
    p.add_argument("--per-etf-subdir", default="per_etf", help="subdir name under out-dir that stores each ETF run")
    return p


def _resolve_codes(ns: argparse.Namespace) -> list[str]:
    if ns.codes_file:
        codes = _read_codes_file(ns.codes_file)
    elif str(ns.codes).strip():
        codes = parse_codes(str(ns.codes))
    else:
        codes = list(DEFAULT_UNIVERSE_CODES)
    if not codes:
        raise RuntimeError("empty codes list")
    if int(getattr(ns, "max_codes", 0) or 0) > 0:
        codes = list(codes[: int(ns.max_codes)])
    return list(codes)


def _resolve_tick_root(ns: argparse.Namespace) -> Path:
    data_root = Path(str(ns.data_root))
    tick_root = str(getattr(ns, "tick_root", "") or "").strip()
    if tick_root:
        return Path(tick_root)
    if (data_root / "tick").exists():
        return data_root / "tick"
    if (Path("etf_chip_engine") / "data" / "l1_snapshots").exists():
        return Path("etf_chip_engine") / "data" / "l1_snapshots"
    return data_root / "tick"


def _apply_runtime_overrides(*, ns: argparse.Namespace) -> None:
    if bool(getattr(ns, "bt_adaptive_params", False)):
        raise RuntimeError(
            "--bt-adaptive-params is not supported by backtest.single_etf_batch in this checkout; "
            "use backtest.main after adaptive helpers are restored"
        )

    from core.buy_order_config import set_aggressive_buy_pricing
    from entry.pathb_config import (
        set_pathb_atr_mult,
        set_pathb_chip_min,
        set_pathb_require_trend,
        set_pathb_require_vwap_strict,
    )
    from entry.phase2_config import set_phase2_continuation_config, set_phase2_score_threshold
    from exit.exit_config import (
        set_exit_atr_pct_bounds,
        set_exit_k,
        set_exit_k_accel,
        set_exit_layer1_order_pricing,
        set_exit_layer2_score_log,
        set_exit_layer2_threshold,
    )

    phase2_score_threshold = float(ns.phase2_score_threshold)
    phase2_continuation_enabled = bool(ns.phase2_continuation_entry)
    phase2_continuation_chip_min = float(ns.phase2_continuation_chip_min)
    phase2_continuation_micro_min = float(ns.phase2_continuation_micro_min)
    phase2_continuation_lookback = int(max(2, int(ns.phase2_continuation_lookback)))
    phase2_continuation_expire_days = int(max(1, int(ns.phase2_continuation_expire_days)))
    phase2_continuation_min_close_breakout_pct = float(max(0.0, float(ns.phase2_continuation_min_close_breakout_pct)))
    phase2_continuation_mature_block_enabled = bool(ns.phase2_continuation_mature_block)
    phase2_continuation_mature_leg_days = int(max(1, int(ns.phase2_continuation_mature_leg_days)))
    phase2_continuation_mature_bias_atr = float(max(0.0, float(ns.phase2_continuation_mature_bias_atr)))
    phase2_continuation_mature_near_high_atr = float(max(0.0, float(ns.phase2_continuation_mature_near_high_atr)))
    phase2_continuation_mature_pullback_lookback = int(max(1, int(ns.phase2_continuation_mature_pullback_lookback)))
    phase2_continuation_mature_min_pullback_bias = float(ns.phase2_continuation_mature_min_pullback_bias)
    pathb_atr_mult = float(ns.phase3_pathb_atr_mult)
    pathb_chip_min = float(ns.phase3_pathb_chip_min)
    pathb_require_trend = bool(getattr(ns, "phase3_pathb_require_trend", True))
    pathb_require_vwap_strict = bool(getattr(ns, "phase3_pathb_require_vwap_strict", True))
    exit_k_normal = float(ns.exit_k_normal) if float(ns.exit_k_normal) > 0 else None
    exit_k_chip_decay = float(ns.exit_k_chip_decay) if float(ns.exit_k_chip_decay) > 0 else None
    exit_k_reduced = float(ns.exit_k_reduced) if float(ns.exit_k_reduced) > 0 else None
    exit_layer1_sell_discount = float(ns.exit_layer1_sell_discount)
    exit_layer1_use_stop_price = bool(ns.exit_layer1_use_stop_price)
    buy_aggressive_multiplier = float(ns.buy_aggressive_multiplier)
    buy_use_ask1 = bool(ns.buy_use_ask1)
    exit_layer2_threshold = float(ns.exit_layer2_threshold)
    cfg_defaults = StrategyConfig()
    exit_atr_pct_min = (
        float(cfg_defaults.exit_atr_pct_min)
        if ns.exit_atr_pct_min is None
        else (float(ns.exit_atr_pct_min) if float(ns.exit_atr_pct_min) > 0 else None)
    )
    exit_atr_pct_max = (
        float(cfg_defaults.exit_atr_pct_max)
        if ns.exit_atr_pct_max is None
        else (float(ns.exit_atr_pct_max) if float(ns.exit_atr_pct_max) > 0 else None)
    )
    exit_k_accel_enabled = bool(cfg_defaults.exit_k_accel_enabled) if ns.exit_k_accel is None else bool(ns.exit_k_accel)
    exit_k_accel_step_pct = (
        float(cfg_defaults.exit_k_accel_step_pct)
        if ns.exit_k_accel_step_pct is None
        else float(ns.exit_k_accel_step_pct)
    )
    exit_k_accel_step_k = (
        float(cfg_defaults.exit_k_accel_step_k)
        if ns.exit_k_accel_step_k is None
        else float(ns.exit_k_accel_step_k)
    )
    exit_k_accel_k_min = (
        float(cfg_defaults.exit_k_accel_k_min)
        if ns.exit_k_accel_kmin is None
        else float(ns.exit_k_accel_kmin)
    )
    if exit_layer1_sell_discount <= 0.0:
        raise RuntimeError(f"--exit-layer1-sell-discount must be > 0, got {exit_layer1_sell_discount}")
    if buy_aggressive_multiplier <= 0.0:
        raise RuntimeError(f"--buy-aggressive-multiplier must be > 0, got {buy_aggressive_multiplier}")
    if exit_atr_pct_min is not None and exit_atr_pct_max is not None and exit_atr_pct_min > exit_atr_pct_max:
        logger.warning(
            "exit atr pct bounds inverted | min=%.4f max=%.4f (swap)",
            float(exit_atr_pct_min),
            float(exit_atr_pct_max),
        )
        exit_atr_pct_min, exit_atr_pct_max = exit_atr_pct_max, exit_atr_pct_min

    set_phase2_score_threshold(float(phase2_score_threshold))
    set_phase2_continuation_config(
        enabled=bool(phase2_continuation_enabled),
        chip_min=float(phase2_continuation_chip_min),
        micro_min=float(phase2_continuation_micro_min),
        lookback=int(phase2_continuation_lookback),
        expire_days=int(phase2_continuation_expire_days),
        min_close_breakout_pct=float(phase2_continuation_min_close_breakout_pct),
        mature_block_enabled=bool(phase2_continuation_mature_block_enabled),
        mature_leg_days=int(phase2_continuation_mature_leg_days),
        mature_bias_atr=float(phase2_continuation_mature_bias_atr),
        mature_near_high_atr=float(phase2_continuation_mature_near_high_atr),
        mature_pullback_lookback=int(phase2_continuation_mature_pullback_lookback),
        mature_min_pullback_bias=float(phase2_continuation_mature_min_pullback_bias),
    )
    set_pathb_atr_mult(float(pathb_atr_mult))
    set_pathb_chip_min(float(pathb_chip_min))
    set_pathb_require_trend(bool(pathb_require_trend))
    set_pathb_require_vwap_strict(bool(pathb_require_vwap_strict))
    set_aggressive_buy_pricing(multiplier=buy_aggressive_multiplier, use_ask1=buy_use_ask1)
    set_exit_k(
        k_normal=exit_k_normal,
        k_chip_decay=exit_k_chip_decay,
        k_reduced=exit_k_reduced,
    )
    set_exit_k_accel(
        enabled=exit_k_accel_enabled,
        step_pct=exit_k_accel_step_pct,
        step_k=exit_k_accel_step_k,
        k_min=exit_k_accel_k_min,
    )
    set_exit_layer1_order_pricing(
        sell_discount=exit_layer1_sell_discount,
        use_stop_price=exit_layer1_use_stop_price,
    )
    set_exit_atr_pct_bounds(min_pct=exit_atr_pct_min, max_pct=exit_atr_pct_max)
    set_exit_layer2_threshold(float(exit_layer2_threshold))
    set_exit_layer2_score_log(bool(ns.exit_layer2_score_log))


def _build_strategy_config(*, code: str, out_dir: Path, ns: argparse.Namespace) -> StrategyConfig:
    logs_dir = out_dir / "logs"
    entry_log_path = "" if bool(ns.light_logs) else str(logs_dir / "entry_decisions.jsonl")
    exit_log_path = "" if bool(ns.light_logs) else str(logs_dir / "exit_decisions.jsonl")
    position_log_path = "" if bool(ns.light_logs) else str(logs_dir / "position_decisions.jsonl")
    t0_log_path = "" if bool(ns.light_logs) else str(logs_dir / "t0_decisions.jsonl")
    require_hot_csv = bool(ns.require_hot_csv)
    phase2_min_chip_days = int(max(0, int(ns.phase2_min_chip_days)))
    phase2_open_cov_win = int(max(0, int(ns.phase2_open_coverage_window)))
    phase2_min_open_cov = float(max(0.0, min(1.0, float(ns.phase2_min_open_coverage))))
    phase2_micro_cov_win = int(max(0, int(ns.phase2_micro_coverage_window)))
    phase2_min_micro_cov = float(max(0.0, min(1.0, float(ns.phase2_min_micro_coverage))))
    if bool(ns.conservative_degrade_mode):
        require_hot_csv = True
        if phase2_min_chip_days <= 0:
            phase2_min_chip_days = 60
        if phase2_open_cov_win <= 0:
            phase2_open_cov_win = 20
        if phase2_min_open_cov <= 0:
            phase2_min_open_cov = 0.80
        if phase2_micro_cov_win <= 0:
            phase2_micro_cov_win = 20
        if phase2_min_micro_cov <= 0:
            phase2_min_micro_cov = 0.60

    cfg_defaults = StrategyConfig()
    position_sizing_cash = float(ns.position_sizing_cash) if ns.position_sizing_cash is not None else None
    position_slot_cap = float(ns.position_slot_cap)
    position_risk_budget_min = float(ns.position_risk_budget_min)
    position_risk_budget_max = float(ns.position_risk_budget_max)
    if position_sizing_cash is not None:
        position_slot_cap, position_risk_budget_min, position_risk_budget_max = derive_position_sizing_from_cash(
            float(position_sizing_cash)
        )
    phase2_no_reentry_after_confirm = (
        bool(cfg_defaults.phase2_no_reentry_after_confirm)
        if ns.bt_no_reentry_after_confirm is None
        else bool(ns.bt_no_reentry_after_confirm)
    )
    phase2_skip_high_chase_after_first_signal = (
        bool(cfg_defaults.phase2_skip_high_chase_after_first_signal)
        if ns.bt_skip_high_chase_after_first_signal is None
        else bool(ns.bt_skip_high_chase_after_first_signal)
    )
    phase2_high_chase_signal_source = (
        str(cfg_defaults.phase2_high_chase_signal_source)
        if ns.bt_high_chase_signal_source is None
        else str(ns.bt_high_chase_signal_source)
    )
    phase2_high_chase_lookback_days = (
        int(cfg_defaults.phase2_high_chase_lookback_days)
        if ns.bt_high_chase_lookback_days is None
        else int(max(1, int(ns.bt_high_chase_lookback_days)))
    )
    phase2_high_chase_max_rise = (
        float(cfg_defaults.phase2_high_chase_max_rise)
        if ns.bt_high_chase_max_rise is None
        else float(ns.bt_high_chase_max_rise)
    )
    exit_atr_pct_min = (
        float(cfg_defaults.exit_atr_pct_min)
        if ns.exit_atr_pct_min is None
        else (float(ns.exit_atr_pct_min) if float(ns.exit_atr_pct_min) > 0 else None)
    )
    exit_atr_pct_max = (
        float(cfg_defaults.exit_atr_pct_max)
        if ns.exit_atr_pct_max is None
        else (float(ns.exit_atr_pct_max) if float(ns.exit_atr_pct_max) > 0 else None)
    )
    exit_k_accel_enabled = bool(cfg_defaults.exit_k_accel_enabled) if ns.exit_k_accel is None else bool(ns.exit_k_accel)
    exit_k_accel_step_pct = (
        float(cfg_defaults.exit_k_accel_step_pct)
        if ns.exit_k_accel_step_pct is None
        else float(ns.exit_k_accel_step_pct)
    )
    exit_k_accel_step_k = (
        float(cfg_defaults.exit_k_accel_step_k)
        if ns.exit_k_accel_step_k is None
        else float(ns.exit_k_accel_step_k)
    )
    exit_k_accel_k_min = (
        float(cfg_defaults.exit_k_accel_k_min)
        if ns.exit_k_accel_kmin is None
        else float(ns.exit_k_accel_kmin)
    )

    return StrategyConfig(
        state_path=str(out_dir / "state" / "portfolio.json"),
        entry_log_path=str(entry_log_path),
        exit_log_path=str(exit_log_path),
        position_log_path=str(position_log_path),
        t0_log_path=str(t0_log_path),
        watchlist_etf_codes=(str(code),),
        tick_interval_s=float(ns.tick_seconds),
        trading_adapter_type="xt",
        auto_prep=False,
        watch_auto=bool(ns.watch_auto),
        watch_auto_no_filter=bool(ns.watch_auto_no_filter),
        watch_auto_require_hot_csv=bool(require_hot_csv),
        phase2_s_micro_missing=float(ns.phase2_s_micro_missing),
        phase2_min_chip_days=int(phase2_min_chip_days),
        phase2_open_coverage_window=int(phase2_open_cov_win),
        phase2_min_open_coverage=float(phase2_min_open_cov),
        phase2_micro_coverage_window=int(phase2_micro_cov_win),
        phase2_min_micro_coverage=float(phase2_min_micro_cov),
        phase2_no_reentry_after_confirm=bool(phase2_no_reentry_after_confirm),
        phase2_skip_high_chase_after_first_signal=bool(phase2_skip_high_chase_after_first_signal),
        phase2_high_chase_signal_source=str(phase2_high_chase_signal_source),
        phase2_high_chase_lookback_days=int(phase2_high_chase_lookback_days),
        phase2_high_chase_max_rise=float(phase2_high_chase_max_rise),
        position_sizing_cash=position_sizing_cash,
        position_slot_cap=float(position_slot_cap),
        position_risk_budget_min=float(position_risk_budget_min),
        position_risk_budget_max=float(position_risk_budget_max),
        exit_atr_pct_min=exit_atr_pct_min,
        exit_atr_pct_max=exit_atr_pct_max,
        exit_k_accel_enabled=bool(exit_k_accel_enabled),
        exit_k_accel_step_pct=float(exit_k_accel_step_pct),
        exit_k_accel_step_k=float(exit_k_accel_step_k),
        exit_k_accel_k_min=float(exit_k_accel_k_min),
    )


def _run_single_etf_backtest(*, code: str, ns: argparse.Namespace, out_dir: Path, tick_root_path: Path) -> dict[str, Any]:
    start_ymd = _to_ymd8(str(ns.start))
    end_ymd = _to_ymd8(str(ns.end))
    if start_ymd > end_ymd:
        raise RuntimeError(f"start date must be <= end date: {start_ymd} > {end_ymd}")

    chip_file, chip_missing = _preflight_chip_coverage(codes=[str(code)], start_date=str(start_ymd))
    if chip_file is None and not bool(ns.allow_missing_chip):
        raise RuntimeError("chip integration csv not found before start date; pass --allow-missing-chip to force run")
    if chip_missing and not bool(ns.allow_missing_chip):
        raise RuntimeError(f"chip csv coverage incomplete for {code}; pass --allow-missing-chip to force run")

    run_tag = str(code).replace(".", "_")
    log_meta = setup_backtest_logging(out_dir=out_dir, run_tag=run_tag)
    cfg = _build_strategy_config(code=str(code), out_dir=out_dir, ns=ns)
    _apply_runtime_overrides(ns=ns)
    fee_rate = float(ns.fee_bps) / 10000.0
    store = MarketDataStore(
        data_root=str(ns.data_root),
        codes=[str(code)],
        tick_root=str(tick_root_path),
        load_minute=bool(ns.load_1m),
    )
    engine = BacktestEngine(
        store=store,
        config=cfg,
        start_date=str(start_ymd),
        end_date=str(end_ymd),
        initial_cash=float(ns.initial_cash),
        fee_rate=float(fee_rate),
        enable_t0=bool(ns.enable_t0),
        disable_t0_ops=not bool(ns.enable_t0_exec),
    )
    result = engine.run()
    paths = write_backtest_result(result=result, out_dir=out_dir)

    row: dict[str, Any] = {
        "code": str(code),
        "start_date": str(result.summary.get("start_date") or start_ymd),
        "end_date": str(result.summary.get("end_date") or end_ymd),
        "days": int(result.summary.get("days") or 0),
        "final_nav": float(result.summary.get("final_nav") or 0.0),
        "total_return": float(result.summary.get("total_return") or 0.0),
        "annualized_return": float(result.summary.get("annualized_return") or 0.0),
        "max_drawdown": float(result.summary.get("max_drawdown") or 0.0),
        "trade_count": int(result.summary.get("trade_count") or 0),
        "buy_count": int(result.summary.get("buy_count") or 0),
        "sell_count": int(result.summary.get("sell_count") or 0),
        "commission_total": float(result.summary.get("commission_total") or 0.0),
        "out_dir": str(out_dir),
        "summary_path": str(paths["summary"]),
        "daily_equity_path": str(paths["daily_equity"]),
        "fills_path": str(paths["fills"]),
        "log_path": str(log_meta["log_path"]),
    }
    return row


def write_batch_summary(*, rows: list[dict[str, Any]], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_csv = out / "single_etf_summary.csv"
    summary_json = out / "single_etf_summary.json"
    overview_json = out / "single_etf_overview.json"

    ordered = sorted(
        list(rows),
        key=lambda x: (
            float(x.get("annualized_return") or 0.0),
            float(x.get("total_return") or 0.0),
            -float(x.get("max_drawdown") or 0.0),
        ),
        reverse=True,
    )
    for idx, row in enumerate(ordered, start=1):
        row["rank_annualized_return"] = int(idx)

    fieldnames = [
        "rank_annualized_return",
        "code",
        "start_date",
        "end_date",
        "days",
        "final_nav",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "trade_count",
        "buy_count",
        "sell_count",
        "commission_total",
        "out_dir",
        "summary_path",
        "daily_equity_path",
        "fills_path",
        "log_path",
    ]
    with summary_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in ordered:
            writer.writerow({k: row.get(k) for k in fieldnames})

    summary_json.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    overview = {
        "count": int(len(ordered)),
        "positive_total_return_count": int(sum(1 for x in ordered if float(x.get("total_return") or 0.0) > 0.0)),
        "median_total_return": (
            0.0
            if not ordered
            else float(sorted(float(x.get("total_return") or 0.0) for x in ordered)[len(ordered) // 2])
        ),
        "best_code_by_annualized_return": ("" if not ordered else str(ordered[0].get("code") or "")),
        "worst_code_by_annualized_return": ("" if not ordered else str(ordered[-1].get("code") or "")),
    }
    overview_json.write_text(json.dumps(overview, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "summary_csv": str(summary_csv),
        "summary_json": str(summary_json),
        "overview_json": str(overview_json),
    }


def main(argv: list[str] | None = None) -> int:
    from .fail_fast_warn import set_fail_fast

    ns = build_batch_arg_parser().parse_args(argv)
    set_fail_fast(bool(ns.fail_fast))
    codes = _resolve_codes(ns)
    tick_root_path = _resolve_tick_root(ns)
    out_root = Path(str(ns.out_dir))
    per_etf_root = out_root / str(ns.per_etf_subdir)
    per_etf_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    total = len(codes)
    for idx, code in enumerate(codes, start=1):
        code_dir = per_etf_root / str(code)
        row = _run_single_etf_backtest(code=str(code), ns=ns, out_dir=code_dir, tick_root_path=tick_root_path)
        rows.append(row)
        print(
            f"[{idx:02d}/{total:02d}] {str(code)} done | annualized={float(row['annualized_return']):.6f} trades={int(row['trade_count'])} out={code_dir}"
        )

    paths = write_batch_summary(rows=rows, out_dir=out_root)
    print("single-etf batch done")
    print("artifacts")
    for key, value in paths.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


