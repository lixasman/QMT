from __future__ import annotations

from datetime import date
from pathlib import Path

import etf_chip_engine.daily_batch as daily_batch
import etf_chip_engine.data.xtdata_provider as xtp


def _touch_text(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_apply_data_retention_prunes_project_files(tmp_path: Path, monkeypatch) -> None:
    old_day = "20240220"
    keep_day = "20250225"

    _touch_text(tmp_path / "etf_chip_engine" / "data" / "l1_snapshots" / old_day / "a.csv")
    _touch_text(tmp_path / "etf_chip_engine" / "data" / "l1_snapshots" / keep_day / "a.csv")

    _touch_text(tmp_path / "etf_chip_engine" / "data" / "chip_snapshots" / f"510300_SH_{old_day}.npz")
    _touch_text(tmp_path / "etf_chip_engine" / "data" / "chip_snapshots" / f"510300_SH_{keep_day}.npz")

    _touch_text(tmp_path / "etf_chip_engine" / "data" / f"batch_results_{old_day}.csv")
    _touch_text(tmp_path / "etf_chip_engine" / "data" / f"batch_results_{keep_day}.csv")

    _touch_text(tmp_path / "output" / "integration" / "chip" / f"batch_results_{old_day}.csv")
    _touch_text(tmp_path / "output" / "integration" / "chip" / f"batch_results_{keep_day}.csv")

    _touch_text(tmp_path / "output" / "cache" / "chip_tick_download" / f"tick_{old_day}.json")
    _touch_text(tmp_path / "output" / "cache" / "chip_tick_download" / f"tick_{keep_day}.json")

    monkeypatch.setattr(
        daily_batch,
        "cleanup_xtdata_dated_files",
        lambda keep_days=365, today=None: {"enabled": True, "keep_days": keep_days, "removed_files": 0, "removed_bytes": 0},
    )

    stats = daily_batch._apply_data_retention(keep_days=365, today=date(2026, 2, 25), base_dir=tmp_path)

    assert stats["enabled"] is True
    assert not (tmp_path / "etf_chip_engine" / "data" / "l1_snapshots" / old_day).exists()
    assert (tmp_path / "etf_chip_engine" / "data" / "l1_snapshots" / keep_day).exists()
    assert not (tmp_path / "etf_chip_engine" / "data" / "chip_snapshots" / f"510300_SH_{old_day}.npz").exists()
    assert (tmp_path / "etf_chip_engine" / "data" / "chip_snapshots" / f"510300_SH_{keep_day}.npz").exists()
    assert not (tmp_path / "etf_chip_engine" / "data" / f"batch_results_{old_day}.csv").exists()
    assert (tmp_path / "etf_chip_engine" / "data" / f"batch_results_{keep_day}.csv").exists()
    assert not (tmp_path / "output" / "integration" / "chip" / f"batch_results_{old_day}.csv").exists()
    assert (tmp_path / "output" / "integration" / "chip" / f"batch_results_{keep_day}.csv").exists()
    assert not (tmp_path / "output" / "cache" / "chip_tick_download" / f"tick_{old_day}.json").exists()
    assert (tmp_path / "output" / "cache" / "chip_tick_download" / f"tick_{keep_day}.json").exists()


def test_cleanup_xtdata_dated_files_prunes_old_dat(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "datadir"
    old_file = data_dir / "SH" / "0" / "510300" / "20240220.dat"
    keep_file = data_dir / "SH" / "0" / "510300" / "20250225.dat"
    _touch_text(old_file, "old")
    _touch_text(keep_file, "new")

    monkeypatch.setattr(xtp, "_resolve_xtdata_data_dir", lambda: data_dir)

    stats = xtp.cleanup_xtdata_dated_files(keep_days=365, today=date(2026, 2, 25))

    assert stats["enabled"] is True
    assert stats["removed_files"] == 1
    assert not old_file.exists()
    assert keep_file.exists()

