from __future__ import annotations

from pathlib import Path

import pandas as pd

import etf_chip_engine.daily_batch as daily_batch


class _DummyService:
    def __init__(self, *, config=None):
        self.config = config or {}

    def run_daily(self, trade_date: str, *, limit=None, codes=None, force_download=False):
        return pd.DataFrame(
            [
                {
                    "trade_date": str(trade_date),
                    "code": "159001.SZ",
                    "dense_zones": "[]",
                }
            ]
        )


def _patch_min_runtime(monkeypatch, tmp_path: Path, called: dict[str, int]) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daily_batch, "require_xtdata", lambda: None)
    monkeypatch.setattr(daily_batch, "_resolve_trade_date", lambda date_arg: "20260304")
    monkeypatch.setattr(daily_batch, "IndustryETFChipService", _DummyService)
    def _retention(*, keep_days=365, today=None, base_dir="."):
        called["retention"] = called.get("retention", 0) + 1
        return {
            "enabled": bool(int(keep_days) > 0),
            "keep_days": int(keep_days),
            "today": "20260304",
            "project": {},
            "xtdata": {},
        }

    monkeypatch.setattr(daily_batch, "_apply_data_retention", _retention)

    def _cleanup(*, trade_date: str):
        called["cleanup"] = called.get("cleanup", 0) + 1
        return {
            "enabled": True,
            "trade_date": str(trade_date),
            "removed_files": 1,
            "removed_bytes": 1,
        }

    monkeypatch.setattr(daily_batch, "cleanup_xtdata_trade_date_files", _cleanup)


def test_run_daily_batch_default_does_not_cleanup_trade_date_tick(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, int] = {}
    _patch_min_runtime(monkeypatch, tmp_path, called)
    out = daily_batch.run_daily_batch(trade_date="auto")
    assert out.exists()
    assert called.get("cleanup", 0) == 0
    assert called.get("retention", 0) == 0


def test_run_daily_batch_cleanup_trade_date_tick_enabled(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, int] = {}
    _patch_min_runtime(monkeypatch, tmp_path, called)
    out = daily_batch.run_daily_batch(trade_date="auto", cleanup_trade_date_tick=True, retention_days=0)
    assert out.exists()
    assert called.get("cleanup", 0) == 1


def test_run_daily_batch_calls_retention_only_when_enabled(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, int] = {}
    _patch_min_runtime(monkeypatch, tmp_path, called)
    out = daily_batch.run_daily_batch(trade_date="auto", retention_days=365)
    assert out.exists()
    assert called.get("retention", 0) == 1
