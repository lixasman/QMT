from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import backtest.main as backtest_main
import core.buy_order_config as buy_order_config
import entry.pathb_config as pathb_config
import entry.phase2_config as phase2_config
import exit.exit_config as exit_config
from strategy_config import StrategyConfig as RealStrategyConfig


def _stub_runtime_setters(monkeypatch) -> None:
    monkeypatch.setattr(phase2_config, "set_phase2_score_threshold", lambda *args, **kwargs: None)
    monkeypatch.setattr(phase2_config, "set_phase2_continuation_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(pathb_config, "set_pathb_atr_mult", lambda *args, **kwargs: None)
    monkeypatch.setattr(pathb_config, "set_pathb_chip_min", lambda *args, **kwargs: None)
    monkeypatch.setattr(pathb_config, "set_pathb_require_trend", lambda *args, **kwargs: None)
    monkeypatch.setattr(pathb_config, "set_pathb_require_vwap_strict", lambda *args, **kwargs: None)
    monkeypatch.setattr(buy_order_config, "set_aggressive_buy_pricing", lambda *args, **kwargs: None)
    monkeypatch.setattr(exit_config, "set_exit_k", lambda *args, **kwargs: None)
    monkeypatch.setattr(exit_config, "set_exit_k_accel", lambda *args, **kwargs: None)
    monkeypatch.setattr(exit_config, "set_exit_layer1_order_pricing", lambda *args, **kwargs: None)
    monkeypatch.setattr(exit_config, "set_exit_atr_pct_bounds", lambda *args, **kwargs: None)
    monkeypatch.setattr(exit_config, "set_exit_layer2_threshold", lambda *args, **kwargs: None)
    monkeypatch.setattr(exit_config, "set_exit_layer2_score_log", lambda *args, **kwargs: None)


def test_main_does_not_warn_for_start_before_old_reliable_tick_threshold(tmp_path: Path, monkeypatch) -> None:
    warnings: list[tuple[str, str]] = []

    _stub_runtime_setters(monkeypatch)
    monkeypatch.setattr(backtest_main, "warn_once", lambda key, msg, logger_name=None: warnings.append((str(key), str(msg))))
    monkeypatch.setattr(backtest_main, "setup_backtest_logging", lambda out_dir: {"run_tag": "t", "log_path": str(tmp_path / "run.log")})
    monkeypatch.setattr(backtest_main, "_preflight_chip_coverage", lambda **kwargs: (tmp_path / "batch_results_20240102.csv", []))
    monkeypatch.setattr(backtest_main, "StrategyConfig", lambda **kwargs: RealStrategyConfig() if not kwargs else SimpleNamespace(**kwargs))
    monkeypatch.setattr(backtest_main, "MarketDataStore", lambda **kwargs: SimpleNamespace(**kwargs))

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self):
            return SimpleNamespace(summary={"final_nav": 1_000_000.0, "total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0})

    monkeypatch.setattr(backtest_main, "BacktestEngine", _FakeEngine)
    monkeypatch.setattr(
        backtest_main,
        "write_backtest_result",
        lambda **kwargs: {
            "summary": str(tmp_path / "summary.json"),
            "daily_equity": str(tmp_path / "daily_equity.csv"),
            "fills": str(tmp_path / "fills.csv"),
        },
    )

    rc = backtest_main.main(
        argv=[
            "--start",
            "20240103",
            "--end",
            "20240110",
            "--codes",
            "512480.SH",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert rc == 0
    assert all(key != "bt_start_before_reliable_tick" for key, _ in warnings)


def test_main_light_logs_disables_decision_jsonl_paths(tmp_path: Path, monkeypatch) -> None:
    captured_cfg: dict[str, object] = {}

    _stub_runtime_setters(monkeypatch)
    monkeypatch.setattr(backtest_main, "warn_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_main, "setup_backtest_logging", lambda out_dir: {"run_tag": "t", "log_path": str(tmp_path / "run.log")})
    monkeypatch.setattr(backtest_main, "_preflight_chip_coverage", lambda **kwargs: (tmp_path / "batch_results_20240102.csv", []))

    def _fake_cfg(**kwargs):
        if not kwargs:
            return RealStrategyConfig()
        captured_cfg.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(backtest_main, "StrategyConfig", _fake_cfg)
    monkeypatch.setattr(backtest_main, "MarketDataStore", lambda **kwargs: SimpleNamespace(**kwargs))

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self):
            return SimpleNamespace(summary={"final_nav": 1_000_000.0, "total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0})

    monkeypatch.setattr(backtest_main, "BacktestEngine", _FakeEngine)
    monkeypatch.setattr(
        backtest_main,
        "write_backtest_result",
        lambda **kwargs: {
            "summary": str(tmp_path / "summary.json"),
            "daily_equity": str(tmp_path / "daily_equity.csv"),
            "fills": str(tmp_path / "fills.csv"),
        },
    )

    rc = backtest_main.main(
        argv=[
            "--start",
            "20240103",
            "--end",
            "20240110",
            "--codes",
            "512480.SH",
            "--light-logs",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert rc == 0
    assert captured_cfg["entry_log_path"] == ""
    assert captured_cfg["exit_log_path"] == ""
    assert captured_cfg["position_log_path"] == ""
    assert captured_cfg["t0_log_path"] == ""


def test_main_position_sizing_cash_scales_triplet(tmp_path: Path, monkeypatch) -> None:
    captured_cfg: dict[str, object] = {}

    _stub_runtime_setters(monkeypatch)
    monkeypatch.setattr(backtest_main, "warn_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_main, "setup_backtest_logging", lambda out_dir: {"run_tag": "t", "log_path": str(tmp_path / "run.log")})
    monkeypatch.setattr(backtest_main, "_preflight_chip_coverage", lambda **kwargs: (tmp_path / "batch_results_20240102.csv", []))

    def _fake_cfg(**kwargs):
        if not kwargs:
            return RealStrategyConfig()
        captured_cfg.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(backtest_main, "StrategyConfig", _fake_cfg)
    monkeypatch.setattr(backtest_main, "MarketDataStore", lambda **kwargs: SimpleNamespace(**kwargs))

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self):
            return SimpleNamespace(summary={"final_nav": 50_000.0, "total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0})

    monkeypatch.setattr(backtest_main, "BacktestEngine", _FakeEngine)
    monkeypatch.setattr(
        backtest_main,
        "write_backtest_result",
        lambda **kwargs: {
            "summary": str(tmp_path / "summary.json"),
            "daily_equity": str(tmp_path / "daily_equity.csv"),
            "fills": str(tmp_path / "fills.csv"),
        },
    )

    rc = backtest_main.main(
        argv=[
            "--start",
            "20240103",
            "--end",
            "20240110",
            "--codes",
            "512480.SH",
            "--initial-cash",
            "50000",
            "--position-sizing-cash",
            "50000",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert rc == 0
    assert captured_cfg["position_sizing_cash"] == 50000.0
    assert captured_cfg["position_slot_cap"] == 8750.0
    assert captured_cfg["position_risk_budget_min"] == 312.5
    assert captured_cfg["position_risk_budget_max"] == 750.0
