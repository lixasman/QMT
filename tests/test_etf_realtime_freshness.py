from __future__ import annotations

from datetime import datetime

import pandas as pd

import etf_chip_engine.realtime as rt_mod


def test_run_realtime_once_cold_start_uses_history_before_trade_date(tmp_path, monkeypatch) -> None:
    calls: dict[str, object] = {"cold_start_last_time": None}

    class _FakeDateTime:
        @classmethod
        def now(cls) -> datetime:
            return datetime(2026, 3, 20, 15, 1, 0)

    class _DummyChip:
        def __init__(self) -> None:
            self.total_shares = 1000.0

    class _DummyEngine:
        def __init__(self, config):
            self.config = config
            self.chips: dict[str, _DummyChip] = {}
            self.iopv = {}

        def load_state(self, code: str, path: str) -> None:
            self.chips[code] = _DummyChip()

        def cold_start(self, code: str, daily_df, total_shares: float, atr: float) -> None:
            calls["cold_start_last_time"] = str(daily_df["time"].iloc[-1]) if daily_df is not None and not daily_df.empty else ""
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            return None

        def process_snapshot(self, code: str, snap):
            return {"profit_ratio": 0.1}

    class _DummyXtData:
        def subscribe_quote(self, code, period="tick", count=0, callback=None):
            return f"sid:{code}"

        def unsubscribe_quote(self, *args, **kwargs):
            return None

        def get_full_tick(self, stock_list):
            return {}

    monkeypatch.setattr(rt_mod, "datetime", _FakeDateTime)
    monkeypatch.setattr(rt_mod, "require_xtdata", lambda: _DummyXtData())
    monkeypatch.setattr(rt_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(rt_mod, "prev_trade_date", lambda trade_date: "20260319")
    monkeypatch.setattr(
        rt_mod,
        "get_daily_bars",
        lambda stock_list, *, end_time, count: pd.DataFrame(
            {
                "time": ["20260318", "20260319", "20260320"],
                "open": [1.0, 1.1, 1.2],
                "high": [1.0, 1.1, 1.2],
                "low": [1.0, 1.1, 1.2],
                "close": [1.0, 1.1, 1.2],
                "volume": [100.0, 100.0, 100.0],
                "amount": [100.0, 100.0, 100.0],
            }
        ),
    )
    monkeypatch.setattr(rt_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(
        rt_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "reason": "", "degraded": False},
    )
    monkeypatch.setattr(rt_mod, "get_etf_info", lambda code: {})
    monkeypatch.setattr(rt_mod, "download_tick_data", lambda stock_list, today: None)
    monkeypatch.setattr(
        rt_mod,
        "get_market_tick_data",
        lambda code, today, count=3000: pd.DataFrame(
            {
                "time": [93000.0, 150000.0],
                "lastPrice": [1.0, 1.01],
                "amount": [10.0, 20.0],
                "volume": [1.0, 2.0],
            }
        ),
    )

    result = rt_mod.run_realtime_once(etf_code="510300.SH", seconds=0.0, min_etf_ticks=1, top_components=0, config={"chip_snapshot_dir": str(tmp_path)})

    assert calls["cold_start_last_time"] == "20260319"
    assert result["etf_code"] == "510300.SH"
