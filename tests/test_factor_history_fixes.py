from __future__ import annotations

from pathlib import Path

import etf_chip_engine.daily_batch as daily_batch
from etf_chip_engine.microstructure.factor_engine import _read_history


def _write_history_csv(path: Path, trade_dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["trade_date,vpin_raw"]
    for d in trade_dates:
        lines.append(f"{d},0.1")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_history_parquet_path_falls_back_to_csv(tmp_path: Path) -> None:
    parquet_path = tmp_path / "159003_SZ.parquet"
    csv_path = parquet_path.with_suffix(".csv")
    _write_history_csv(csv_path, ["20260303", "20260304"])

    df = _read_history(parquet_path)
    assert list(df["trade_date"].astype(str)) == ["20260303", "20260304"]


def test_count_factor_history_days_prefers_configured_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    configured = tmp_path / "custom_factor_history"
    legacy = tmp_path / "etf_chip_engine" / "data" / "factor_history"
    _write_history_csv(configured / "159003_SZ.csv", ["20260303", "20260304"])
    _write_history_csv(legacy / "159003_SZ.csv", ["20260304"])

    ms_cfg = dict(daily_batch.CONFIG.get("microstructure") or {})
    ms_cfg["factor_history_dir"] = str(configured)
    monkeypatch.setitem(daily_batch.CONFIG, "microstructure", ms_cfg)

    assert daily_batch._count_factor_history_days(code="159003.SZ") == 2


def test_count_factor_history_days_falls_back_to_legacy_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    configured = tmp_path / "custom_factor_history"
    legacy = tmp_path / "etf_chip_engine" / "data" / "factor_history"
    _write_history_csv(legacy / "159003_SZ.csv", ["20260302", "20260303", "20260304"])

    ms_cfg = dict(daily_batch.CONFIG.get("microstructure") or {})
    ms_cfg["factor_history_dir"] = str(configured)
    monkeypatch.setitem(daily_batch.CONFIG, "microstructure", ms_cfg)

    assert daily_batch._count_factor_history_days(code="159003.SZ") == 3
