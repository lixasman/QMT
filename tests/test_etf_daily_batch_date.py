from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import etf_chip_engine.daily_batch as daily_batch


def _patch_now(monkeypatch, now_dt: datetime) -> None:
    class _FakeDatetime:
        @staticmethod
        def now() -> datetime:
            return now_dt

    monkeypatch.setattr(daily_batch, "datetime", _FakeDatetime)


def test_resolve_trade_date_auto_trading_day_before_close(monkeypatch) -> None:
    _patch_now(monkeypatch, datetime(2026, 2, 24, 9, 0, 0))
    monkeypatch.setattr(daily_batch, "latest_trade_date", lambda trade_date: "20260224")
    monkeypatch.setattr(daily_batch, "prev_trade_date", lambda trade_date: "20260213")
    assert daily_batch._resolve_trade_date("auto") == "20260213"


def test_resolve_trade_date_auto_trading_day_after_close(monkeypatch) -> None:
    _patch_now(monkeypatch, datetime(2026, 2, 24, 16, 0, 0))
    monkeypatch.setattr(daily_batch, "latest_trade_date", lambda trade_date: "20260224")
    monkeypatch.setattr(daily_batch, "prev_trade_date", lambda trade_date: "20260213")
    assert daily_batch._resolve_trade_date("auto") == "20260224"


def test_resolve_trade_date_auto_non_trading_day(monkeypatch) -> None:
    _patch_now(monkeypatch, datetime(2026, 2, 22, 10, 0, 0))
    monkeypatch.setattr(daily_batch, "latest_trade_date", lambda trade_date: "20260221")
    monkeypatch.setattr(daily_batch, "prev_trade_date", lambda trade_date: "20260220")
    assert daily_batch._resolve_trade_date("auto") == "20260221"


def test_main_disables_retention_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["daily_batch.py"])
    monkeypatch.setattr(daily_batch, "_resolve_trade_date", lambda value: "20260224")

    def _fake_run_daily_batch(**kwargs):
        captured.update(kwargs)
        return Path("dummy.csv")

    monkeypatch.setattr(daily_batch, "run_daily_batch", _fake_run_daily_batch)

    assert daily_batch.main() == 0
    assert captured["retention_days"] == 0
