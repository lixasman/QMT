from __future__ import annotations

from pathlib import Path

import etf_chip_engine.local_backfill as local_backfill


def test_run_local_backfill_workers_1_runs_sequentially(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(local_backfill, "_load_codes", lambda path: ["512480.SH"])
    monkeypatch.setattr(local_backfill, "_collect_trade_date_dirs", lambda root: {"20260306": tmp_path / "tick_data" / "2026-03-06"})
    monkeypatch.setattr(
        local_backfill,
        "_process_code",
        lambda args: [{"trade_date": "20260306", "code": "512480.SH", "profit_ratio": 81.2}],
    )
    monkeypatch.setattr(local_backfill, "_count_factor_history_days", lambda code: 9)
    monkeypatch.setitem(local_backfill.CONFIG, "l1_snapshot_dir", str(tmp_path / "l1_snapshots"))
    monkeypatch.setitem(local_backfill.CONFIG, "chip_snapshot_dir", str(tmp_path / "chip_snapshots"))

    class _UnexpectedPool:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("ProcessPoolExecutor should not be used when workers=1")

    monkeypatch.setattr(local_backfill, "ProcessPoolExecutor", _UnexpectedPool)

    result = local_backfill.run_local_backfill(
        tick_root=tmp_path / "tick_data",
        codes_file=tmp_path / "codes.txt",
        start_date="20260306",
        end_date="20260306",
        workers=1,
        assumed_daily_turnover=0.05,
        tick_size=0.001,
        l1_csv_only=False,
        max_history_days=120,
    )

    assert result["trade_dates_written"] == 1
    assert (tmp_path / "etf_chip_engine" / "data" / "batch_results_20260306.csv").exists()
    assert (tmp_path / "output" / "integration" / "chip" / "batch_results_20260306.csv").exists()
