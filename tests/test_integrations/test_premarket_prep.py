from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import core.time_utils as time_utils
from integrations.premarket_prep import ensure_tminus1_ready, finintel_integration_path, prev_trading_date


def test_prev_trading_date_uses_calendar_provider(monkeypatch) -> None:
    def provider(start: str, end: str) -> list[str]:
        return ["20260221", "20260224"]

    monkeypatch.setattr(time_utils, "_calendar_provider", provider)
    now = datetime.fromisoformat("2026-02-24T08:30:00")
    assert prev_trading_date(now) == "20260221"


def test_ensure_tminus1_ready_triggers_missing_prep(monkeypatch, tmp_path: Path) -> None:
    def provider(start: str, end: str) -> list[str]:
        return ["20260221", "20260224"]

    monkeypatch.setattr(time_utils, "_calendar_provider", provider)

    integration_dir = tmp_path / "integration"
    chip_dir = integration_dir / "chip"
    fin_dir = integration_dir / "finintel"
    chip_dir.mkdir(parents=True, exist_ok=True)
    fin_dir.mkdir(parents=True, exist_ok=True)

    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    calls = SimpleNamespace(chip=0, hot=0, signal=0)

    import etf_chip_engine.daily_batch as daily_batch

    def fake_run_daily_batch(*, trade_date: str, **kwargs):
        calls.chip += 1
        (chip_dir / f"batch_results_{trade_date}.csv").write_text("code,profit_ratio\n512480.SH,80\n", encoding="utf-8")
        return tmp_path / "dummy.csv"

    monkeypatch.setattr(daily_batch, "run_daily_batch", fake_run_daily_batch)

    import finintel.main as fin_main_mod

    def fake_fin_main(argv):
        argv2 = list(argv or [])
        if "--signal-hot-top" in argv2:
            calls.hot += 1
            (out_dir / "finintel_signal_hot_20260221.csv").write_text("code\n512480.SH\n", encoding="utf-8")
            (fin_dir / "sentiment_512480_20260221.json").write_text(
                '{"sentiment_score_01":0.65,"sentiment_score_100":70}',
                encoding="utf-8",
            )
            return 0
        if "--signal-etf" in argv2:
            calls.signal += 1
            idx = argv2.index("--signal-etf")
            c6 = argv2[idx + 1]
            (fin_dir / f"sentiment_{c6}_20260221.json").write_text(
                '{"sentiment_score_01":0.5,"sentiment_score_100":50}',
                encoding="utf-8",
            )
            return 0
        return 0

    monkeypatch.setattr(fin_main_mod, "main", fake_fin_main)

    now = datetime.fromisoformat("2026-02-24T08:30:00")
    r = ensure_tminus1_ready(
        now=now,
        watch_codes=["512480.SH"],
        position_codes=["159107.SZ"],
        hot_top=15,
        integration_dir=integration_dir,
        out_dir=out_dir,
    )

    assert r.t_minus_1 == "20260221"
    assert calls.chip == 1
    assert calls.hot == 1
    assert calls.signal == 1
    assert finintel_integration_path(code6="159107", day="20260221", integration_dir=integration_dir).exists()

