from __future__ import annotations

from backtest.main import build_arg_parser
from exit.exit_config import (
    get_exit_atr_pct_max,
    get_exit_atr_pct_min,
    get_exit_k_accel,
    get_exit_layer2_threshold,
)
from strategy_config import parse_strategy_config


def test_strategy_config_defaults_match_best_strategy() -> None:
    cfg = parse_strategy_config([])

    assert cfg.phase2_no_reentry_after_confirm is True
    assert cfg.phase2_skip_high_chase_after_first_signal is True
    assert cfg.phase2_high_chase_signal_source == "all_signals"
    assert cfg.phase2_high_chase_lookback_days == 60
    assert cfg.phase2_high_chase_max_rise == 0.15
    assert cfg.exit_atr_pct_min == 0.025
    assert cfg.exit_atr_pct_max == 0.04
    assert cfg.exit_k_accel_enabled is True
    assert cfg.exit_k_accel_step_pct == 0.05
    assert cfg.exit_k_accel_step_k == 0.2
    assert cfg.exit_k_accel_k_min == 1.0
    assert cfg.exit_layer2_threshold == 0.7


def test_exit_config_defaults_match_best_strategy() -> None:
    assert get_exit_atr_pct_min() == 0.025
    assert get_exit_atr_pct_max() == 0.04
    assert get_exit_k_accel() == (True, 0.05, 0.2, 1.0)
    assert get_exit_layer2_threshold() == 0.7


def test_backtest_cli_defaults_inherit_shared_strategy_defaults() -> None:
    ns = build_arg_parser().parse_args(["--start", "20260101", "--end", "20260131"])

    assert ns.exit_atr_pct_min is None
    assert ns.exit_atr_pct_max is None
    assert ns.exit_k_accel is None
    assert ns.bt_no_reentry_after_confirm is None
    assert ns.bt_skip_high_chase_after_first_signal is None
    assert ns.bt_high_chase_signal_source is None
    assert ns.bt_high_chase_lookback_days is None
    assert ns.bt_high_chase_max_rise is None
    assert ns.exit_layer1_use_stop_price is False
    assert ns.buy_use_ask1 is False


def test_parse_strategy_config_position_sizing_overrides() -> None:
    cfg = parse_strategy_config(
        [
            "--position-slot-cap",
            "35000",
            "--position-risk-budget-min",
            "1250",
            "--position-risk-budget-max",
            "3000",
        ]
    )

    assert cfg.position_slot_cap == 35000.0
    assert cfg.position_risk_budget_min == 1250.0
    assert cfg.position_risk_budget_max == 3000.0


def test_parse_strategy_config_position_sizing_cash_scales_triplet() -> None:
    cfg = parse_strategy_config(["--position-sizing-cash", "50000"])

    assert cfg.position_sizing_cash == 50000.0
    assert cfg.position_slot_cap == 8750.0
    assert cfg.position_risk_budget_min == 312.5
    assert cfg.position_risk_budget_max == 750.0


def test_parse_strategy_config_exit_layer2_threshold_override() -> None:
    cfg = parse_strategy_config(["--exit-layer2-threshold", "0.65"])

    assert cfg.exit_layer2_threshold == 0.65
