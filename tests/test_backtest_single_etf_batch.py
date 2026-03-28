from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import backtest.single_etf_batch as single_etf_batch
from backtest.runner import BacktestEngine
from backtest.single_etf_batch import _build_strategy_config, build_batch_arg_parser, write_batch_summary
from core.buy_order_config import (
    get_aggressive_buy_multiplier,
    get_aggressive_buy_use_ask1,
    set_aggressive_buy_pricing,
)
from entry.pathb_config import (
    get_pathb_atr_mult,
    get_pathb_chip_min,
    get_pathb_require_trend,
    get_pathb_require_vwap_strict,
    set_pathb_atr_mult,
    set_pathb_chip_min,
    set_pathb_require_trend,
    set_pathb_require_vwap_strict,
)
from entry.phase2_config import (
    get_phase2_continuation_config,
    get_phase2_score_threshold,
    set_phase2_continuation_config,
    set_phase2_score_threshold,
)
from exit.exit_config import (
    get_exit_k_chip_decay,
    get_exit_k_normal,
    get_exit_k_reduced,
    get_exit_layer1_sell_discount,
    get_exit_layer1_use_stop_price,
    get_exit_layer2_score_log,
    get_exit_layer2_threshold,
    set_exit_k,
    set_exit_layer1_order_pricing,
    set_exit_layer2_score_log,
    set_exit_layer2_threshold,
)

def test_write_single_etf_batch_summary_outputs_sorted_files(tmp_path: Path) -> None:
    rows = [
        {
            "code": "B.SH",
            "start_date": "20250304",
            "end_date": "20260227",
            "days": 240,
            "final_nav": 1010000.0,
            "total_return": 0.01,
            "annualized_return": 0.02,
            "max_drawdown": -0.10,
            "trade_count": 10,
            "buy_count": 5,
            "sell_count": 5,
            "commission_total": 10.0,
            "out_dir": "x",
            "summary_path": "x/summary.json",
            "daily_equity_path": "x/daily.csv",
            "fills_path": "x/fills.csv",
            "log_path": "x/log.txt",
        },
        {
            "code": "A.SH",
            "start_date": "20250304",
            "end_date": "20260227",
            "days": 240,
            "final_nav": 1020000.0,
            "total_return": 0.02,
            "annualized_return": 0.05,
            "max_drawdown": -0.08,
            "trade_count": 8,
            "buy_count": 4,
            "sell_count": 4,
            "commission_total": 8.0,
            "out_dir": "y",
            "summary_path": "y/summary.json",
            "daily_equity_path": "y/daily.csv",
            "fills_path": "y/fills.csv",
            "log_path": "y/log.txt",
        },
    ]

    paths = write_batch_summary(rows=rows, out_dir=tmp_path)
    csv_path = Path(paths["summary_csv"])
    json_path = Path(paths["summary_json"])
    overview_path = Path(paths["overview_json"])

    assert csv_path.exists()
    assert json_path.exists()
    assert overview_path.exists()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        csv_rows = list(csv.DictReader(f))
    assert [row["code"] for row in csv_rows] == ["A.SH", "B.SH"]
    assert [row["rank_annualized_return"] for row in csv_rows] == ["1", "2"]

    json_rows = json.loads(json_path.read_text(encoding="utf-8"))
    assert [row["code"] for row in json_rows] == ["A.SH", "B.SH"]

    overview = json.loads(overview_path.read_text(encoding="utf-8"))
    assert overview["count"] == 2
    assert overview["positive_total_return_count"] == 2
    assert overview["best_code_by_annualized_return"] == "A.SH"
    assert overview["worst_code_by_annualized_return"] == "B.SH"


def test_single_etf_batch_engine_inherits_shared_entry_guard_defaults(tmp_path: Path) -> None:
    ns = build_batch_arg_parser().parse_args(["--start", "20260101", "--end", "20260131"])
    cfg = _build_strategy_config(code="512480.SH", out_dir=tmp_path, ns=ns)

    engine = BacktestEngine(
        store=object(),  # type: ignore[arg-type]
        config=cfg,
        start_date="20260101",
        end_date="20260131",
        initial_cash=1_000_000.0,
        fee_rate=0.0,
        enable_t0=False,
    )

    assert engine._bt_no_reentry_after_confirm is True
    assert engine._bt_skip_high_chase_after_first_signal is True
    assert engine._bt_high_chase_signal_source == "all_signals"
    assert engine._bt_high_chase_lookback_days == 60
    assert engine._bt_high_chase_max_rise == 0.15


def test_single_etf_batch_allows_pre_reliable_tick_start_ranges(tmp_path: Path, monkeypatch) -> None:
    ns = build_batch_arg_parser().parse_args(
        [
            "--start",
            "20240102",
            "--end",
            "20240110",
            "--data-root",
            "backtest/data",
            "--codes",
            "512480.SH",
            "--allow-missing-chip",
        ]
    )
    tick_root = tmp_path / "ticks"
    tick_root.mkdir(parents=True, exist_ok=True)

    calls: dict[str, object] = {}

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            calls.update(kwargs)

        def run(self):
            return SimpleNamespace(
                summary={
                    "start_date": "20240102",
                    "end_date": "20240110",
                    "days": 5,
                    "final_nav": 1_000_000.0,
                    "total_return": 0.0,
                    "annualized_return": 0.0,
                    "max_drawdown": 0.0,
                    "trade_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "commission_total": 0.0,
                }
            )

    monkeypatch.setattr(single_etf_batch, "_preflight_chip_coverage", lambda **kwargs: (None, []))
    monkeypatch.setattr(single_etf_batch, "setup_backtest_logging", lambda **kwargs: {"log_path": str(tmp_path / "run.log")})
    monkeypatch.setattr(single_etf_batch, "MarketDataStore", lambda **kwargs: object())
    monkeypatch.setattr(single_etf_batch, "BacktestEngine", _FakeEngine)
    monkeypatch.setattr(
        single_etf_batch,
        "write_backtest_result",
        lambda **kwargs: {
            "summary": str(tmp_path / "summary.json"),
            "daily_equity": str(tmp_path / "daily_equity.csv"),
            "fills": str(tmp_path / "fills.csv"),
        },
    )

    row = single_etf_batch._run_single_etf_backtest(
        code="512480.SH",
        ns=ns,
        out_dir=tmp_path / "out",
        tick_root_path=tick_root,
    )

    assert calls["start_date"] == "20240102"
    assert row["start_date"] == "20240102"
    assert row["end_date"] == "20240110"

def test_single_etf_batch_applies_runtime_cli_overrides_and_light_logs(tmp_path: Path, monkeypatch) -> None:
    ns = build_batch_arg_parser().parse_args(
        [
            "--start",
            "20260101",
            "--end",
            "20260131",
            "--data-root",
            "backtest/data",
            "--codes",
            "512480.SH",
            "--allow-missing-chip",
            "--light-logs",
            "--phase2-score-threshold",
            "0.91",
            "--phase2-continuation-entry",
            "--phase2-continuation-chip-min",
            "0.77",
            "--phase2-continuation-micro-min",
            "0.66",
            "--phase2-continuation-lookback",
            "7",
            "--phase2-continuation-expire-days",
            "2",
            "--phase2-continuation-min-close-breakout-pct",
            "0.03",
            "--phase2-continuation-mature-block",
            "--phase2-continuation-mature-leg-days",
            "6",
            "--phase2-continuation-mature-bias-atr",
            "1.7",
            "--phase2-continuation-mature-near-high-atr",
            "0.4",
            "--phase2-continuation-mature-pullback-lookback",
            "5",
            "--phase2-continuation-mature-min-pullback-bias",
            "0.12",
            "--buy-use-ask1",
            "--buy-aggressive-multiplier",
            "1.009",
            "--exit-k-normal",
            "1.11",
            "--exit-k-chip-decay",
            "1.12",
            "--exit-k-reduced",
            "1.13",
            "--exit-layer1-sell-discount",
            "0.97",
            "--exit-layer1-use-stop-price",
            "--exit-layer2-threshold",
            "0.33",
            "--exit-layer2-score-log",
            "--phase3-pathb-atr-mult",
            "0.61",
            "--phase3-pathb-chip-min",
            "0.93",
            "--phase3-pathb-no-require-trend",
            "--phase3-pathb-no-require-vwap-strict",
        ]
    )
    tick_root = tmp_path / "ticks"
    tick_root.mkdir(parents=True, exist_ok=True)

    captured: dict[str, object] = {}

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self):
            return SimpleNamespace(
                summary={
                    "start_date": "20260101",
                    "end_date": "20260131",
                    "days": 5,
                    "final_nav": 1_000_000.0,
                    "total_return": 0.0,
                    "annualized_return": 0.0,
                    "max_drawdown": 0.0,
                    "trade_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "commission_total": 0.0,
                }
            )

    original_phase2_score = float(get_phase2_score_threshold())
    original_phase2_cont = dict(get_phase2_continuation_config())
    original_buy_multiplier = float(get_aggressive_buy_multiplier())
    original_buy_use_ask1 = bool(get_aggressive_buy_use_ask1())
    original_pathb_atr_mult = float(get_pathb_atr_mult())
    original_pathb_chip_min = float(get_pathb_chip_min())
    original_pathb_require_trend = bool(get_pathb_require_trend())
    original_pathb_require_vwap_strict = bool(get_pathb_require_vwap_strict())
    original_exit_k_normal = float(get_exit_k_normal())
    original_exit_k_chip_decay = float(get_exit_k_chip_decay())
    original_exit_k_reduced = float(get_exit_k_reduced())
    original_exit_layer1_sell_discount = float(get_exit_layer1_sell_discount())
    original_exit_layer1_use_stop_price = bool(get_exit_layer1_use_stop_price())
    original_exit_layer2_threshold = float(get_exit_layer2_threshold())
    original_exit_layer2_score_log = bool(get_exit_layer2_score_log())

    monkeypatch.setattr(single_etf_batch, "_preflight_chip_coverage", lambda **kwargs: (None, []))
    monkeypatch.setattr(single_etf_batch, "setup_backtest_logging", lambda **kwargs: {"log_path": str(tmp_path / "run.log")})
    monkeypatch.setattr(single_etf_batch, "MarketDataStore", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(single_etf_batch, "BacktestEngine", _FakeEngine)
    monkeypatch.setattr(
        single_etf_batch,
        "write_backtest_result",
        lambda **kwargs: {
            "summary": str(tmp_path / "summary.json"),
            "daily_equity": str(tmp_path / "daily_equity.csv"),
            "fills": str(tmp_path / "fills.csv"),
        },
    )

    try:
        row = single_etf_batch._run_single_etf_backtest(
            code="512480.SH",
            ns=ns,
            out_dir=tmp_path / "out",
            tick_root_path=tick_root,
        )

        cfg = captured["config"]
        assert cfg.entry_log_path == ""
        assert cfg.exit_log_path == ""
        assert cfg.position_log_path == ""
        assert cfg.t0_log_path == ""
        assert row["start_date"] == "20260101"

        assert float(get_phase2_score_threshold()) == 0.91
        assert get_phase2_continuation_config() == {
            "enabled": True,
            "chip_min": 0.77,
            "micro_min": 0.66,
            "lookback": 7,
            "expire_days": 2,
            "min_close_breakout_pct": 0.03,
            "mature_block_enabled": True,
            "mature_leg_days": 6,
            "mature_bias_atr": 1.7,
            "mature_near_high_atr": 0.4,
            "mature_pullback_lookback": 5,
            "mature_min_pullback_bias": 0.12,
        }
        assert float(get_aggressive_buy_multiplier()) == 1.009
        assert bool(get_aggressive_buy_use_ask1()) is True
        assert float(get_pathb_atr_mult()) == 0.61
        assert float(get_pathb_chip_min()) == 0.93
        assert bool(get_pathb_require_trend()) is False
        assert bool(get_pathb_require_vwap_strict()) is False
        assert float(get_exit_k_normal()) == 1.11
        assert float(get_exit_k_chip_decay()) == 1.12
        assert float(get_exit_k_reduced()) == 1.13
        assert float(get_exit_layer1_sell_discount()) == 0.97
        assert bool(get_exit_layer1_use_stop_price()) is True
        assert float(get_exit_layer2_threshold()) == 0.33
        assert bool(get_exit_layer2_score_log()) is True
    finally:
        set_phase2_score_threshold(original_phase2_score)
        set_phase2_continuation_config(**original_phase2_cont)
        set_aggressive_buy_pricing(multiplier=original_buy_multiplier, use_ask1=original_buy_use_ask1)
        set_pathb_atr_mult(original_pathb_atr_mult)
        set_pathb_chip_min(original_pathb_chip_min)
        set_pathb_require_trend(original_pathb_require_trend)
        set_pathb_require_vwap_strict(original_pathb_require_vwap_strict)
        set_exit_k(
            k_normal=original_exit_k_normal,
            k_chip_decay=original_exit_k_chip_decay,
            k_reduced=original_exit_k_reduced,
        )
        set_exit_layer1_order_pricing(
            sell_discount=original_exit_layer1_sell_discount,
            use_stop_price=original_exit_layer1_use_stop_price,
        )
        set_exit_layer2_threshold(original_exit_layer2_threshold)
        set_exit_layer2_score_log(original_exit_layer2_score_log)

def test_single_etf_batch_rejects_bt_adaptive_params_until_helpers_exist(tmp_path: Path) -> None:
    ns = build_batch_arg_parser().parse_args(
        [
            "--start",
            "20260101",
            "--end",
            "20260131",
            "--bt-adaptive-params",
        ]
    )

    try:
        single_etf_batch._apply_runtime_overrides(ns=ns)
    except RuntimeError as exc:
        assert "--bt-adaptive-params" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for unsupported bt-adaptive-params")

def test_single_etf_batch_main_applies_fail_fast_flag(tmp_path: Path, monkeypatch) -> None:
    from backtest.fail_fast_warn import is_fail_fast, set_fail_fast

    monkeypatch.setattr(
        single_etf_batch,
        "_run_single_etf_backtest",
        lambda **kwargs: {
            "code": kwargs["code"],
            "start_date": "20260101",
            "end_date": "20260131",
            "days": 1,
            "final_nav": 1_000_000.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "trade_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "commission_total": 0.0,
            "out_dir": str(tmp_path / "per_etf" / kwargs["code"]),
            "summary_path": str(tmp_path / "summary.json"),
            "daily_equity_path": str(tmp_path / "daily.csv"),
            "fills_path": str(tmp_path / "fills.csv"),
            "log_path": str(tmp_path / "run.log"),
        },
    )
    monkeypatch.setattr(
        single_etf_batch,
        "write_batch_summary",
        lambda **kwargs: {
            "summary_csv": str(tmp_path / "single_etf_summary.csv"),
            "summary_json": str(tmp_path / "single_etf_summary.json"),
            "overview_json": str(tmp_path / "single_etf_overview.json"),
        },
    )

    original_fail_fast = bool(is_fail_fast())
    try:
        set_fail_fast(False)
        rc = single_etf_batch.main(
            [
                "--start",
                "20260101",
                "--end",
                "20260131",
                "--codes",
                "512480.SH",
                "--out-dir",
                str(tmp_path),
                "--fail-fast",
            ]
        )
        assert rc == 0
        assert bool(is_fail_fast()) is True
    finally:
        set_fail_fast(original_fail_fast)
