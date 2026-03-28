from __future__ import annotations

import argparse
import csv
import logging
import re
from pathlib import Path

from strategy_config import StrategyConfig, derive_position_sizing_from_cash

from .fail_fast_warn import warn_once
from .logging_utils import setup_backtest_logging
from .runner import BacktestEngine, write_backtest_result
from .store import MarketDataStore
from .universe import DEFAULT_UNIVERSE_CODES, parse_codes

logger = logging.getLogger("backtest.main")
# Legacy threshold retained for other entry points; main backtest no longer warns on this boundary
# because local l1/tick coverage has been backfilled earlier.
BACKTEST_RELIABLE_TICK_START = "20250301"


def _read_codes_file(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"codes file not found: {p}")
    out: list[str] = []
    seen: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        for raw in line.replace("\t", ",").replace(" ", ",").split(","):
            code = str(raw).strip().upper()
            if not code:
                continue
            if code in seen:
                continue
            seen.add(code)
            out.append(code)
    return out


def _float01(v: str) -> float:
    x = float(v)
    if not (0.0 <= x <= 1.0):
        raise argparse.ArgumentTypeError(f"must be in [0,1], got {x}")
    return float(x)


def _positive_float(v: str) -> float:
    x = float(v)
    if not (x > 0.0):
        raise argparse.ArgumentTypeError(f"must be > 0, got {x}")
    return float(x)


def _to_ymd8(v: str) -> str:
    s = str(v or "").strip()
    if len(s) == 8 and s.isdigit():
        return s
    if len(s) == 10 and "-" in s:
        return s.replace("-", "")
    raise RuntimeError(f"invalid date, expect YYYYMMDD or YYYY-MM-DD: {v}")


def _norm_code(code: str) -> str:
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        return s
    if len(s) == 6 and s.isdigit():
        if s.startswith(("5", "6", "9")):
            return f"{s}.SH"
        return f"{s}.SZ"
    return s


def _preflight_chip_coverage(*, codes: list[str], start_date: str) -> tuple[Path | None, list[str]]:
    chip_dir = Path("output") / "integration" / "chip"
    if not chip_dir.exists():
        return None, [_norm_code(x) for x in codes]

    files = sorted(chip_dir.glob("batch_results_*.csv"))
    selected: Path | None = None
    best = ""
    for p in files:
        m = re.search(r"batch_results_(\d{8})\.csv$", p.name)
        if not m:
            continue
        d = m.group(1)
        if d >= str(start_date):
            continue
        if d > best:
            best = d
            selected = p
    if selected is None:
        return None, [_norm_code(x) for x in codes]

    in_csv: set[str] = set()
    with selected.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            c = _norm_code(str(row.get("code") or ""))
            if c:
                in_csv.add(c)
    missing = [c for c in [_norm_code(x) for x in codes] if c and c not in in_csv]
    return selected, missing


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backtest.main")
    p.add_argument("--data-root", default="backtest/data", help="root path that contains tick/ and 1d/ csv folders")
    p.add_argument(
        "--tick-root",
        default="",
        help="tick root path (supports per-day folders). Default: auto-detect from data-root/tick, fallback to etf_chip_engine/data/l1_snapshots",
    )
    p.add_argument("--load-1m", action="store_true", help="load 1m bars under data-root/1m (default: disabled)")
    p.add_argument("--start", required=True, help="start date, YYYYMMDD")
    p.add_argument("--end", required=True, help="end date, YYYYMMDD")
    p.add_argument("--out-dir", default="output/backtest", help="output directory")
    p.add_argument("--initial-cash", type=float, default=1_000_000.0, help="initial cash")
    p.add_argument(
        "--position-sizing-cash",
        type=_positive_float,
        default=None,
        help="derive slot/risk sizing from account cash using the 400000 baseline (overrides the three position sizing args)",
    )
    p.add_argument("--position-slot-cap", type=_positive_float, default=70000.0, help="per-ETF notional slot cap for sizing (default: 70000)")
    p.add_argument("--position-risk-budget-min", type=_positive_float, default=2500.0, help="position sizing risk budget floor (default: 2500)")
    p.add_argument("--position-risk-budget-max", type=_positive_float, default=6000.0, help="position sizing risk budget cap (default: 6000)")
    p.add_argument("--fee-bps", type=float, default=0.85, help="commission in bps, default 0.85")
    p.add_argument("--tick-seconds", type=float, default=3.0, help="strategy loop interval in seconds (tick-level default: 3s)")
    p.add_argument("--fail-fast", action="store_true", help="abort on warn/degrade (default: log and continue)")
    p.add_argument("--phase2-s-micro-missing", type=_float01, default=0.1, help="fallback S_micro in [0,1]")
    p.add_argument(
        "--phase2-score-threshold",
        type=_float01,
        default=0.45,
        help="Phase2 score threshold (default: 0.45)",
    )
    p.add_argument(
        "--phase2-continuation-entry",
        action="store_true",
        help="enable Phase2 continuation fallback when squeeze path does not trigger (default: disabled)",
    )
    p.add_argument(
        "--phase2-continuation-chip-min",
        type=_float01,
        default=0.60,
        help="Phase2 continuation minimum S_chip_pr (default: 0.60)",
    )
    p.add_argument(
        "--phase2-continuation-micro-min",
        type=_float01,
        default=0.40,
        help="Phase2 continuation minimum S_micro (default: 0.40)",
    )
    p.add_argument(
        "--phase2-continuation-lookback",
        type=int,
        default=10,
        help="Phase2 continuation previous-high lookback in bars (default: 10)",
    )
    p.add_argument(
        "--phase2-continuation-expire-days",
        type=int,
        default=1,
        help="Phase2 continuation pending-entry expiry in trading days (default: 1)",
    )
    p.add_argument(
        "--phase2-continuation-min-close-breakout-pct",
        type=float,
        default=0.0,
        help="Phase2 continuation minimum close breakout vs previous high (default: 0.0)",
    )
    p.add_argument(
        "--phase2-continuation-mature-block",
        action="store_true",
        help="block late Phase2 continuation entries in already-extended mature trends (default: disabled)",
    )
    p.add_argument(
        "--phase2-continuation-mature-leg-days",
        type=int,
        default=5,
        help="Phase2 continuation mature-block minimum consecutive trend days (default: 5)",
    )
    p.add_argument(
        "--phase2-continuation-mature-bias-atr",
        type=float,
        default=2.0,
        help="Phase2 continuation mature-block minimum close-vs-EMA10 bias in ATR20 units (default: 2.0)",
    )
    p.add_argument(
        "--phase2-continuation-mature-near-high-atr",
        type=float,
        default=0.5,
        help="Phase2 continuation mature-block maximum distance to HH20 in ATR20 units (default: 0.5)",
    )
    p.add_argument(
        "--phase2-continuation-mature-pullback-lookback",
        type=int,
        default=4,
        help="Phase2 continuation mature-block recent pullback lookback bars inside the current leg (default: 4)",
    )
    p.add_argument(
        "--phase2-continuation-mature-min-pullback-bias",
        type=float,
        default=0.2,
        help="Phase2 continuation mature-block minimum recent (low-EMA10)/ATR20 to classify the trend as no-pullback (default: 0.2)",
    )
    p.add_argument("--exit-k-normal", type=float, default=0.0, help="Exit Chandelier K_NORMAL override (0=use default)")
    p.add_argument("--exit-k-chip-decay", type=float, default=0.0, help="Exit Chandelier K_CHIP_DECAY override (0=use default)")
    p.add_argument("--exit-k-reduced", type=float, default=0.0, help="Exit Chandelier K_REDUCED override (0=use default)")
    p.add_argument(
        "--exit-layer1-sell-discount",
        type=float,
        default=0.98,
        help="Exit Layer1/Lifeboat sell price multiplier vs bid1 (default: 0.98)",
    )
    p.add_argument(
        "--exit-layer1-use-stop-price",
        action="store_true",
        help="Price Layer1/Lifeboat exit sells at stop price instead of bid1*discount",
    )
    p.add_argument(
        "--buy-aggressive-multiplier",
        type=float,
        default=1.003,
        help="Aggressive buy price multiplier vs ask1 for entry/lifeboat buyback (default: 1.003)",
    )
    p.add_argument(
        "--buy-use-ask1",
        action="store_true",
        help="Price aggressive buys at ask1 instead of ask1*multiplier",
    )
    p.add_argument("--exit-layer2-threshold", type=_float01, default=0.7, help="Exit Layer2 score threshold (default: 0.7)")
    p.add_argument("--exit-layer2-score-log", action="store_true", help="Log Layer2 score at each evaluation (exit_decisions.jsonl)")
    p.add_argument(
        "--exit-atr-pct-min",
        type=float,
        default=None,
        help="Exit Chandelier ATR%% floor (omit to inherit shared strategy default)",
    )
    p.add_argument(
        "--exit-atr-pct-max",
        type=float,
        default=None,
        help="Exit Chandelier ATR%% cap (omit to inherit shared strategy default)",
    )
    p.add_argument(
        "--exit-k-accel",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Exit Chandelier profit-accelerating K (omit to inherit shared strategy default)",
    )
    p.add_argument(
        "--exit-k-accel-step-pct",
        type=float,
        default=None,
        help="Exit K accel profit step pct (omit to inherit shared strategy default)",
    )
    p.add_argument(
        "--exit-k-accel-step-k",
        type=float,
        default=None,
        help="Exit K accel decrease per step (omit to inherit shared strategy default)",
    )
    p.add_argument(
        "--exit-k-accel-kmin",
        type=float,
        default=None,
        help="Exit K accel minimum K (omit to inherit shared strategy default)",
    )
    p.add_argument(
        "--phase3-pathb-atr-mult",
        type=float,
        default=0.5,
        help="Phase3 Path-B ATR multiplier (default: 0.5)",
    )
    p.add_argument(
        "--phase3-pathb-chip-min",
        type=float,
        default=0.85,
        help="Phase3 Path-B minimum S_chip_pr (default: 0.85)",
    )
    p.add_argument(
        "--phase3-pathb-require-trend",
        action="store_true",
        help="Phase3 Path-B requires S_trend==1 (default: enabled)",
    )
    p.add_argument(
        "--phase3-pathb-no-require-trend",
        dest="phase3_pathb_require_trend",
        action="store_false",
        help="Phase3 Path-B does not require S_trend",
    )
    p.add_argument(
        "--phase3-pathb-require-vwap-strict",
        action="store_true",
        help="Phase3 Path-B requires VWAP slope and warmup finished (default: enabled)",
    )
    p.add_argument(
        "--phase3-pathb-no-require-vwap-strict",
        dest="phase3_pathb_require_vwap_strict",
        action="store_false",
        help="Phase3 Path-B allows warmup VWAP to pass",
    )
    p.add_argument("--codes", default="", help="comma-separated ETF codes, e.g. 512480.SH,159363.SZ")
    p.add_argument("--codes-file", default="", help="txt/csv file with one or more ETF codes")
    p.add_argument(
        "--watch-auto",
        dest="watch_auto",
        action="store_true",
        help="enable watch_auto (hot csv + static fallback), default: enabled",
    )
    p.add_argument(
        "--no-watch-auto",
        dest="watch_auto",
        action="store_false",
        help="disable watch_auto and use static codes only",
    )
    p.add_argument(
        "--watch-auto-no-filter",
        dest="watch_auto_no_filter",
        action="store_true",
        help="keep all candidates before entry FSM, default: enabled",
    )
    p.add_argument(
        "--watch-auto-filter",
        dest="watch_auto_no_filter",
        action="store_false",
        help="apply entry.watchlist.filter_watchlist before entry FSM",
    )
    p.add_argument("--enable-t0", action="store_true", help="allow same-day sell")
    p.add_argument(
        "--enable-t0-exec",
        action="store_true",
        help="enable T0 execute_t0_live in intraday loop (default: disabled in backtest)",
    )
    p.add_argument("--allow-missing-chip", action="store_true", help="allow run even if chip integration file is missing/incomplete")
    p.add_argument(
        "--require-hot-csv",
        action="store_true",
        help="conservative mode: watch_auto requires hot csv, no fallback to static list",
    )
    p.add_argument(
        "--light-logs",
        action="store_true",
        help="disable high-volume decision jsonl logs for faster backtest runs",
    )
    p.add_argument(
        "--bt-no-reentry-after-confirm",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="backtest only: disallow re-entry after confirm filled (S2+) until position is cleared; omit to inherit shared strategy default",
    )
    p.add_argument(
        "--bt-skip-high-chase-after-first-signal",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="backtest only: skip new Phase2 signal when price is too far above the first recent signal; omit to inherit shared strategy default",
    )
    p.add_argument(
        "--bt-high-chase-signal-source",
        choices=("all_signals", "missed_executable"),
        default=None,
        help="backtest only: source used to seed the high-chase block window; omit to inherit shared strategy default",
    )
    p.add_argument(
        "--bt-high-chase-lookback-days",
        type=int,
        default=None,
        help="backtest only: rolling lookback window in calendar days for recent first signal check; omit to inherit shared strategy default",
    )
    p.add_argument(
        "--bt-high-chase-max-rise",
        type=_float01,
        default=None,
        help="backtest only: max allowed rise vs first recent signal before skipping entry; omit to inherit shared strategy default",
    )
    p.add_argument(
        "--bt-adaptive-params",
        action="store_true",
        help="backtest only: pick entry/exit params based on pre-start regime metrics (single-code only)",
    )
    p.add_argument(
        "--bt-adaptive-lookback",
        type=int,
        default=120,
        help="backtest only: daily bars lookback for adaptive params (default: 120)",
    )
    p.add_argument(
        "--bt-adaptive-min-bars",
        type=int,
        default=120,
        help="backtest only: minimum bars required to apply adaptive params (default: 120)",
    )
    p.add_argument(
        "--bt-adaptive-low-vol-threshold",
        type=float,
        default=0.20,
        help="backtest only: annualized vol threshold for low-vol regime (default: 0.20)",
    )
    p.add_argument(
        "--bt-adaptive-vol-threshold",
        type=float,
        default=0.25,
        help="backtest only: annualized vol threshold for high-vol regime (default: 0.25)",
    )
    p.add_argument(
        "--bt-adaptive-amount-threshold",
        type=float,
        default=0.0,
        help="backtest only: average daily amount threshold (0=disabled, default: 0)",
    )
    p.add_argument(
        "--conservative-degrade-mode",
        action="store_true",
        help="preset conservative degrade controls (require hot csv + strict phase2 quality gates)",
    )
    p.add_argument("--phase2-min-chip-days", type=int, default=0, help="phase2 gate: minimum chip_engine_days (0=disabled)")
    p.add_argument(
        "--phase2-open-coverage-window",
        type=int,
        default=0,
        help="phase2 gate: lookback trading days for 09:30 tick coverage (0=disabled)",
    )
    p.add_argument(
        "--phase2-min-open-coverage",
        type=_float01,
        default=0.0,
        help="phase2 gate: minimum 09:30 tick coverage in [0,1] (0=disabled)",
    )
    p.add_argument(
        "--phase2-micro-coverage-window",
        type=int,
        default=0,
        help="phase2 gate: lookback trading days for micro-factor coverage (0=disabled)",
    )
    p.add_argument(
        "--phase2-min-micro-coverage",
        type=_float01,
        default=0.0,
        help="phase2 gate: minimum micro-factor coverage in [0,1] (0=disabled)",
    )
    p.set_defaults(
        watch_auto=True,
        watch_auto_no_filter=True,
        phase3_pathb_require_trend=True,
        phase3_pathb_require_vwap_strict=True,
    )
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_arg_parser().parse_args(argv)
    from .fail_fast_warn import set_fail_fast

    set_fail_fast(bool(ns.fail_fast))
    out_dir = Path(str(ns.out_dir))
    log_meta = setup_backtest_logging(out_dir=out_dir)
    logger.info(
        "backtest start | run_tag=%s out_dir=%s data_root=%s start=%s end=%s",
        log_meta["run_tag"],
        str(out_dir),
        str(ns.data_root),
        str(ns.start),
        str(ns.end),
    )

    start_ymd = _to_ymd8(str(ns.start))
    end_ymd = _to_ymd8(str(ns.end))
    if start_ymd > end_ymd:
        raise RuntimeError(f"start date must be <= end date: {start_ymd} > {end_ymd}")

    if ns.codes_file:
        codes = _read_codes_file(ns.codes_file)
    elif str(ns.codes).strip():
        codes = parse_codes(str(ns.codes))
    else:
        codes = list(DEFAULT_UNIVERSE_CODES)
    if not codes:
        raise RuntimeError("empty codes list")
    logger.info("universe prepared | count=%s sample=%s", len(codes), ",".join(codes[:10]))

    chip_file, chip_missing = _preflight_chip_coverage(codes=codes, start_date=str(start_ymd))
    if chip_file is None:
        msg = (
            "chip integration csv not found before start date. "
            "run `python -m etf_chip_engine.daily_batch --date auto` first, "
            "or pass --allow-missing-chip to force run."
        )
        if not bool(ns.allow_missing_chip):
            raise RuntimeError(msg)
        warn_once("bt_chip_file_missing", msg, logger_name="backtest.main")
    elif chip_missing:
        head = ",".join(chip_missing[:10])
        msg = (
            f"chip csv coverage incomplete: file={chip_file} missing={len(chip_missing)} sample={head}. "
            "this may lead to empty watchlist/trades."
        )
        if not bool(ns.allow_missing_chip):
            raise RuntimeError(msg + " pass --allow-missing-chip to force run.")
        warn_once("bt_chip_coverage_incomplete", msg, logger_name="backtest.main")
    else:
        logger.info("chip coverage check passed | file=%s", str(chip_file))

    logs_dir = out_dir / "logs"
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

    entry_log_path = "" if bool(ns.light_logs) else str(logs_dir / "entry_decisions.jsonl")
    exit_log_path = "" if bool(ns.light_logs) else str(logs_dir / "exit_decisions.jsonl")
    position_log_path = "" if bool(ns.light_logs) else str(logs_dir / "position_decisions.jsonl")
    t0_log_path = "" if bool(ns.light_logs) else str(logs_dir / "t0_decisions.jsonl")

    data_root = Path(str(ns.data_root))
    tick_root = str(getattr(ns, "tick_root", "") or "").strip()
    if tick_root:
        tick_root_path = Path(tick_root)
    elif (data_root / "tick").exists():
        tick_root_path = data_root / "tick"
    elif (Path("etf_chip_engine") / "data" / "l1_snapshots").exists():
        tick_root_path = Path("etf_chip_engine") / "data" / "l1_snapshots"
    else:
        tick_root_path = data_root / "tick"
    logger.info("tick root | path=%s", str(tick_root_path))

    store = MarketDataStore(
        data_root=str(ns.data_root),
        codes=codes,
        tick_root=str(tick_root_path),
        load_minute=bool(ns.load_1m),
    )

    cfg_defaults = StrategyConfig()
    position_sizing_cash = float(ns.position_sizing_cash) if ns.position_sizing_cash is not None else None
    position_slot_cap = float(ns.position_slot_cap)
    position_risk_budget_min = float(ns.position_risk_budget_min)
    position_risk_budget_max = float(ns.position_risk_budget_max)
    if position_sizing_cash is not None:
        position_slot_cap, position_risk_budget_min, position_risk_budget_max = derive_position_sizing_from_cash(
            float(position_sizing_cash)
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
    exit_k_accel_kmin = (
        float(cfg_defaults.exit_k_accel_k_min)
        if ns.exit_k_accel_kmin is None
        else float(ns.exit_k_accel_kmin)
    )
    bt_no_reentry_after_confirm = (
        bool(cfg_defaults.phase2_no_reentry_after_confirm)
        if ns.bt_no_reentry_after_confirm is None
        else bool(ns.bt_no_reentry_after_confirm)
    )
    bt_skip_high_chase_after_first_signal = (
        bool(cfg_defaults.phase2_skip_high_chase_after_first_signal)
        if ns.bt_skip_high_chase_after_first_signal is None
        else bool(ns.bt_skip_high_chase_after_first_signal)
    )
    bt_high_chase_signal_source = (
        str(cfg_defaults.phase2_high_chase_signal_source)
        if ns.bt_high_chase_signal_source is None
        else str(ns.bt_high_chase_signal_source)
    )
    bt_high_chase_lookback_days = (
        int(cfg_defaults.phase2_high_chase_lookback_days)
        if ns.bt_high_chase_lookback_days is None
        else int(max(1, int(ns.bt_high_chase_lookback_days)))
    )
    bt_high_chase_max_rise = (
        float(cfg_defaults.phase2_high_chase_max_rise)
        if ns.bt_high_chase_max_rise is None
        else float(ns.bt_high_chase_max_rise)
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

    adaptive_meta: dict[str, object] | None = None
    if bool(ns.bt_adaptive_params):
        if len(codes) != 1:
            raise RuntimeError("bt-adaptive-params requires single code in --codes")
        lookback = int(max(0, int(ns.bt_adaptive_lookback)))
        min_bars = int(max(0, int(ns.bt_adaptive_min_bars)))
        low_vol_threshold = float(ns.bt_adaptive_low_vol_threshold)
        vol_threshold = float(ns.bt_adaptive_vol_threshold)
        amount_threshold = float(ns.bt_adaptive_amount_threshold)
        if low_vol_threshold < 0.0:
            low_vol_threshold = 0.0
        if vol_threshold < 0.0:
            vol_threshold = 0.0
        if low_vol_threshold > vol_threshold:
            logger.warning(
                "adaptive params thresholds inverted | low_vol=%.4f high_vol=%.4f (clamp low to high)",
                float(low_vol_threshold),
                float(vol_threshold),
            )
            low_vol_threshold = float(vol_threshold)
        start_dt = datetime.strptime(str(start_ymd), "%Y%m%d")
        bars = store.daily_bars(code=codes[0], now=start_dt, count=lookback, include_today=False)
        metrics = _calc_regime_metrics(list(bars))
        bar_count = int(metrics.get("bars") or 0)
        if bar_count < min_bars:
            logger.warning(
                "adaptive params skipped | code=%s bars=%s min_bars=%s",
                codes[0],
                int(bar_count),
                int(min_bars),
            )
            adaptive_meta = {
                "enabled": True,
                "status": "insufficient_bars",
                "code": codes[0],
                "lookback": int(lookback),
                "min_bars": int(min_bars),
                "thresholds": {
                    "low_vol": float(low_vol_threshold),
                    "high_vol": float(vol_threshold),
                    "amount": float(amount_threshold),
                },
                "metrics": metrics,
            }
        else:
            regime, param_set, reason = _select_adaptive_params(
                metrics,
                low_vol_threshold=low_vol_threshold,
                vol_threshold=vol_threshold,
                amount_threshold=amount_threshold,
            )
            if param_set.get("phase2_score_threshold") is not None:
                phase2_score_threshold = float(param_set["phase2_score_threshold"])
            if param_set.get("pathb_chip_min") is not None:
                pathb_chip_min = float(param_set["pathb_chip_min"])
            if param_set.get("exit_k_normal") is not None:
                exit_k_normal = float(param_set["exit_k_normal"])
            if param_set.get("exit_k_chip_decay") is not None:
                exit_k_chip_decay = float(param_set["exit_k_chip_decay"])
            if param_set.get("exit_k_reduced") is not None:
                exit_k_reduced = float(param_set["exit_k_reduced"])
            if param_set.get("exit_layer2_threshold") is not None:
                exit_layer2_threshold = float(param_set["exit_layer2_threshold"])
            adaptive_meta = {
                "enabled": True,
                "status": "applied",
                "code": codes[0],
                "regime": regime,
                "reason": reason,
                "lookback": int(lookback),
                "min_bars": int(min_bars),
                "thresholds": {
                    "low_vol": float(low_vol_threshold),
                    "high_vol": float(vol_threshold),
                    "amount": float(amount_threshold),
                },
                "metrics": metrics,
                "params": {
                    "phase2_score_threshold": float(phase2_score_threshold),
                    "pathb_chip_min": float(pathb_chip_min),
                    "exit_k_normal": exit_k_normal,
                    "exit_k_chip_decay": exit_k_chip_decay,
                    "exit_k_reduced": exit_k_reduced,
                    "exit_layer2_threshold": float(exit_layer2_threshold),
                },
            }
            logger.info(
                "adaptive params applied | code=%s regime=%s reason=%s vol_ann=%.4f amount_mean=%.2f p2=%.2f chip_min=%.2f exit_k=(%s,%s,%s) layer2=%.2f",
                codes[0],
                regime,
                reason,
                float(metrics.get("vol_ann") or 0.0),
                float(metrics.get("amount_mean") or 0.0),
                float(phase2_score_threshold),
                float(pathb_chip_min),
                str(exit_k_normal),
                str(exit_k_chip_decay),
                str(exit_k_reduced),
                float(exit_layer2_threshold),
            )

    from core.buy_order_config import set_aggressive_buy_pricing
    from entry.phase2_config import set_phase2_continuation_config, set_phase2_score_threshold
    from exit.exit_config import (
        set_exit_atr_pct_bounds,
        set_exit_k,
        set_exit_k_accel,
        set_exit_layer1_order_pricing,
        set_exit_layer2_threshold,
        set_exit_layer2_score_log,
    )
    from entry.pathb_config import (
        set_pathb_atr_mult,
        set_pathb_chip_min,
        set_pathb_require_trend,
        set_pathb_require_vwap_strict,
    )
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
    set_aggressive_buy_pricing(
        multiplier=buy_aggressive_multiplier,
        use_ask1=buy_use_ask1,
    )
    set_exit_k(
        k_normal=exit_k_normal,
        k_chip_decay=exit_k_chip_decay,
        k_reduced=exit_k_reduced,
    )
    set_exit_k_accel(
        enabled=exit_k_accel_enabled,
        step_pct=exit_k_accel_step_pct,
        step_k=exit_k_accel_step_k,
        k_min=exit_k_accel_kmin,
    )
    set_exit_layer1_order_pricing(
        sell_discount=exit_layer1_sell_discount,
        use_stop_price=exit_layer1_use_stop_price,
    )
    set_exit_atr_pct_bounds(min_pct=exit_atr_pct_min, max_pct=exit_atr_pct_max)
    set_exit_layer2_threshold(float(exit_layer2_threshold))
    set_exit_layer2_score_log(bool(ns.exit_layer2_score_log))

    cfg = StrategyConfig(
        state_path=str(out_dir / "state" / "portfolio.json"),
        entry_log_path=str(entry_log_path),
        exit_log_path=str(exit_log_path),
        position_log_path=str(position_log_path),
        t0_log_path=str(t0_log_path),
        watchlist_etf_codes=tuple(codes),
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
        phase2_no_reentry_after_confirm=bool(bt_no_reentry_after_confirm),
        phase2_skip_high_chase_after_first_signal=bool(bt_skip_high_chase_after_first_signal),
        phase2_high_chase_signal_source=str(bt_high_chase_signal_source),
        phase2_high_chase_lookback_days=int(bt_high_chase_lookback_days),
        phase2_high_chase_max_rise=float(bt_high_chase_max_rise),
        enable_t0=bool(ns.enable_t0),
        position_sizing_cash=position_sizing_cash,
        position_slot_cap=float(position_slot_cap),
        position_risk_budget_min=float(position_risk_budget_min),
        position_risk_budget_max=float(position_risk_budget_max),
        exit_atr_pct_min=exit_atr_pct_min,
        exit_atr_pct_max=exit_atr_pct_max,
        exit_k_accel_enabled=bool(exit_k_accel_enabled),
        exit_k_accel_step_pct=float(exit_k_accel_step_pct),
        exit_k_accel_step_k=float(exit_k_accel_step_k),
        exit_k_accel_k_min=float(exit_k_accel_kmin),
    )

    fee_rate = float(ns.fee_bps) / 10000.0
    logger.info(
        "engine config | initial_cash=%.2f fee_rate=%.8f tick_seconds=%.2f enable_t0=%s enable_t0_exec=%s watch_auto=%s watch_auto_no_filter=%s require_hot_csv=%s s_micro_missing=%.3f phase2_chip_min=%s phase2_open=(%s,%.2f) phase2_micro=(%s,%.2f) phase2_continuation=(%s,%.2f,%.2f,%s,%s,%.4f,mature=%s,%s,%.2f,%.2f,%s,%.2f) high_chase_skip=%s high_chase_source=%s high_chase_lookback=%s high_chase_max_rise=%.3f position_sizing=(slot_cap=%.2f,risk_min=%.2f,risk_max=%.2f)",
        float(ns.initial_cash),
        float(fee_rate),
        float(ns.tick_seconds),
        bool(ns.enable_t0),
        bool(ns.enable_t0_exec),
        bool(ns.watch_auto),
        bool(ns.watch_auto_no_filter),
        bool(require_hot_csv),
        float(ns.phase2_s_micro_missing),
        int(phase2_min_chip_days),
        int(phase2_open_cov_win),
        float(phase2_min_open_cov),
        int(phase2_micro_cov_win),
        float(phase2_min_micro_cov),
        bool(phase2_continuation_enabled),
        float(phase2_continuation_chip_min),
        float(phase2_continuation_micro_min),
        int(phase2_continuation_lookback),
        int(phase2_continuation_expire_days),
        float(phase2_continuation_min_close_breakout_pct),
        bool(phase2_continuation_mature_block_enabled),
        int(phase2_continuation_mature_leg_days),
        float(phase2_continuation_mature_bias_atr),
        float(phase2_continuation_mature_near_high_atr),
        int(phase2_continuation_mature_pullback_lookback),
        float(phase2_continuation_mature_min_pullback_bias),
        bool(bt_skip_high_chase_after_first_signal),
        str(bt_high_chase_signal_source),
        int(bt_high_chase_lookback_days),
        float(bt_high_chase_max_rise),
        float(position_slot_cap),
        float(position_risk_budget_min),
        float(position_risk_budget_max),
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
        bt_no_reentry_after_confirm=bool(bt_no_reentry_after_confirm),
        bt_skip_high_chase_after_first_signal=bool(bt_skip_high_chase_after_first_signal),
        bt_high_chase_signal_source=str(bt_high_chase_signal_source),
        bt_high_chase_lookback_days=int(bt_high_chase_lookback_days),
        bt_high_chase_max_rise=float(bt_high_chase_max_rise),
    )
    result = engine.run()
    if adaptive_meta is not None:
        result.summary["adaptive_params"] = adaptive_meta
    paths = write_backtest_result(result=result, out_dir=out_dir)
    logger.info(
        "backtest done | final_nav=%.4f total_return=%.6f max_drawdown=%.6f trades=%s",
        float(result.summary.get("final_nav") or 0.0),
        float(result.summary.get("total_return") or 0.0),
        float(result.summary.get("max_drawdown") or 0.0),
        int(result.summary.get("trade_count") or 0),
    )
    logger.info("artifacts | summary=%s daily=%s fills=%s", paths["summary"], paths["daily_equity"], paths["fills"])
    logger.info("log file | path=%s", log_meta["log_path"])

    print("backtest done")
    print("summary")
    for k, v in result.summary.items():
        print(f"  {k}: {v}")
    print("artifacts")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    print(f"log: {log_meta['log_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
