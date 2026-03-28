from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import etf_chip_engine.service as svc_mod


def _empty_snapshots() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["time", "open", "high", "low", "close", "volume", "amount", "bid1", "bid1_vol", "ask1", "ask1_vol"]
    )


def _ok_snapshots() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": [1.0],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [10.0],
            "amount": [10.0],
            "bid1": [0.0],
            "bid1_vol": [0.0],
            "ask1": [0.0],
            "ask1_vol": [0.0],
        }
    )


def _ok_snapshots_with_invalid_iopv() -> pd.DataFrame:
    df = _ok_snapshots().copy()
    df.loc[:, "close"] = [1.2]
    df.loc[:, "iopv"] = [0.0]
    df.loc[:, "premium_rate"] = [0.0]
    return df


def test_load_daily_history_before_trade_date_raises_when_prev_day_missing(monkeypatch) -> None:
    monkeypatch.setattr(svc_mod, "ensure_daily_history_downloaded", lambda codes, trade_date, *, timeout_sec=20: False)
    monkeypatch.setattr(
        svc_mod,
        "get_daily_bars",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "time": ["20260314", "20260317", "20260318"],
                "close": [1.0, 1.1, 1.2],
            }
        ),
    )

    with pytest.raises(RuntimeError, match="stale daily history"):
        svc_mod._load_daily_history_before_trade_date(
            code="510300.SH",
            trade_date="20260320",
            count=2,
            expected_last_trade_date="20260319",
            context="test:cold_start",
        )


def test_load_daily_history_before_trade_date_auto_downloads_and_retries_once(monkeypatch) -> None:
    calls = {"daily": 0, "download": 0}

    def _fake_get_daily_bars(*_args, **_kwargs):
        calls["daily"] += 1
        if calls["daily"] == 1:
            return pd.DataFrame(
                {
                    "time": ["20260314", "20260317", "20260318"],
                    "close": [1.0, 1.1, 1.2],
                }
            )
        return pd.DataFrame(
            {
                "time": ["20260318", "20260319", "20260320"],
                "close": [1.1, 1.2, 1.3],
            }
        )

    def _fake_download(codes, trade_date, *, timeout_sec=20):
        calls["download"] += 1
        assert codes == ["510300.SH"]
        assert trade_date == "20260319"
        return True

    monkeypatch.setattr(svc_mod, "get_daily_bars", _fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "ensure_daily_history_downloaded", _fake_download)

    hist = svc_mod._load_daily_history_before_trade_date(
        code="510300.SH",
        trade_date="20260320",
        count=2,
        expected_last_trade_date="20260319",
        context="test:cold_start",
    )

    assert calls["download"] == 1
    assert calls["daily"] == 2
    assert str(hist["time"].iloc[-1]) == "20260319"


def test_load_daily_history_before_trade_date_still_raises_when_retry_stays_stale(monkeypatch) -> None:
    calls = {"download": 0}

    monkeypatch.setattr(
        svc_mod,
        "get_daily_bars",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "time": ["20260314", "20260317", "20260318"],
                "close": [1.0, 1.1, 1.2],
            }
        ),
    )

    def _fake_download(codes, trade_date, *, timeout_sec=20):
        calls["download"] += 1
        assert codes == ["510300.SH"]
        assert trade_date == "20260319"
        return False

    monkeypatch.setattr(svc_mod, "ensure_daily_history_downloaded", _fake_download)

    with pytest.raises(RuntimeError, match="stale daily history"):
        svc_mod._load_daily_history_before_trade_date(
            code="510300.SH",
            trade_date="20260320",
            count=2,
            expected_last_trade_date="20260319",
            context="test:cold_start",
        )

    assert calls["download"] == 1


def test_load_daily_history_before_trade_date_polls_after_download_until_data_visible(monkeypatch) -> None:
    calls = {"daily": 0, "download": 0}

    def _fake_get_daily_bars(*_args, **_kwargs):
        calls["daily"] += 1
        if calls["daily"] <= 2:
            return pd.DataFrame(
                {
                    "time": ["20260314", "20260317", "20260318"],
                    "close": [1.0, 1.1, 1.2],
                }
            )
        return pd.DataFrame(
            {
                "time": ["20260318", "20260319", "20260320"],
                "close": [1.1, 1.2, 1.3],
            }
        )

    def _fake_download(codes, trade_date, *, timeout_sec=20):
        calls["download"] += 1
        assert codes == ["510300.SH"]
        assert trade_date == "20260319"
        return True

    monkeypatch.setattr(svc_mod, "get_daily_bars", _fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "ensure_daily_history_downloaded", _fake_download)
    monkeypatch.setattr(svc_mod.time, "sleep", lambda _sec: None)

    hist = svc_mod._load_daily_history_before_trade_date(
        code="510300.SH",
        trade_date="20260320",
        count=2,
        expected_last_trade_date="20260319",
        context="test:cold_start",
    )

    assert calls["download"] == 1
    assert calls["daily"] >= 3
    assert str(hist["time"].iloc[-1]) == "20260319"


def test_load_trade_date_daily_bars_polls_after_download_until_data_visible(monkeypatch) -> None:
    calls = {"daily": 0, "download": 0}

    def _fake_get_daily_bars(*_args, **_kwargs):
        calls["daily"] += 1
        if calls["daily"] <= 2:
            return pd.DataFrame(
                {
                    "time": ["20260314", "20260317", "20260318"],
                    "close": [1.0, 1.1, 1.2],
                }
            )
        return pd.DataFrame(
            {
                "time": ["20260318", "20260319", "20260320"],
                "close": [1.1, 1.2, 1.3],
            }
        )

    def _fake_download(codes, trade_date, *, timeout_sec=20):
        calls["download"] += 1
        assert codes == ["510300.SH"]
        assert trade_date == "20260320"
        return True

    monkeypatch.setattr(svc_mod, "get_daily_bars", _fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "ensure_daily_history_downloaded", _fake_download)
    monkeypatch.setattr(svc_mod.time, "sleep", lambda _sec: None)

    daily_df = svc_mod._load_trade_date_daily_bars(
        code="510300.SH",
        trade_date="20260320",
        count=11,
        snapshots=pd.DataFrame(),
        context="test:atr",
    )

    assert calls["download"] == 1
    assert calls["daily"] >= 3
    assert str(daily_df["time"].iloc[-1]) == "20260320"


def test_load_trade_date_daily_bars_still_raises_when_retry_stays_stale(monkeypatch) -> None:
    calls = {"download": 0}

    monkeypatch.setattr(
        svc_mod,
        "get_daily_bars",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "time": ["20260314", "20260317", "20260318"],
                "close": [1.0, 1.1, 1.2],
            }
        ),
    )

    def _fake_download(codes, trade_date, *, timeout_sec=20):
        calls["download"] += 1
        assert codes == ["510300.SH"]
        assert trade_date == "20260320"
        return False

    monkeypatch.setattr(svc_mod, "ensure_daily_history_downloaded", _fake_download)

    with pytest.raises(RuntimeError, match="stale daily bar"):
        svc_mod._load_trade_date_daily_bars(
            code="510300.SH",
            trade_date="20260320",
            count=11,
            snapshots=pd.DataFrame(),
            context="test:atr",
        )

    assert calls["download"] == 1


def test_compute_adv_60_uses_previous_60_days_when_trade_date_bar_is_stale(monkeypatch) -> None:
    monkeypatch.setattr(
        svc_mod,
        "get_daily_bars",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "time": [f"d{i:02d}" for i in range(61)],
                "open": np.arange(1.0, 62.0, dtype=np.float64),
                "high": np.arange(1.0, 62.0, dtype=np.float64),
                "low": np.arange(1.0, 62.0, dtype=np.float64),
                "close": np.arange(1.0, 62.0, dtype=np.float64),
                "volume": np.arange(1.0, 62.0, dtype=np.float64),
                "amount": np.arange(1.0, 62.0, dtype=np.float64),
            }
        ),
    )

    adv = svc_mod._compute_adv_60(code="510300.SH", trade_date="20260320", prev_trade_date="")

    assert adv == pytest.approx(float(np.arange(2.0, 62.0, dtype=np.float64).mean()))


def test_run_daily_cold_start_uses_prev_history_and_atr_uses_trade_date_bar(tmp_path, monkeypatch) -> None:
    calls = SimpleNamespace(cold_start_last_time=None, atr_last_times=[])
    trade_date = "20260320"
    prev_date = "20260319"

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
            calls.cold_start_last_time = str(daily_df["time"].iloc[-1]) if daily_df is not None and not daily_df.empty else ""
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            return None

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_get_daily_bars(stock_list, *, end_time: str, count: int):
        code0 = stock_list[0]
        if int(count) >= 60:
            return pd.DataFrame(
                {
                    "time": ["20260318", prev_date, trade_date],
                    "open": [1.0, 1.1, 1.2],
                    "high": [1.0, 1.1, 1.2],
                    "low": [1.0, 1.1, 1.2],
                    "close": [1.0, 1.1, 1.2],
                    "volume": [100.0, 100.0, 100.0],
                    "amount": [100.0, 100.0, 100.0],
                }
            )
        if code0 == "510300.SH" and int(count) == 11:
            return pd.DataFrame(
                {
                    "time": [f"202603{d:02d}" for d in range(7, 17)] + [prev_date],
                    "open": np.full(11, 1.0, dtype=np.float64),
                    "high": np.full(11, 1.1, dtype=np.float64),
                    "low": np.full(11, 0.9, dtype=np.float64),
                    "close": np.full(11, 1.0, dtype=np.float64),
                    "volume": np.full(11, 100.0, dtype=np.float64),
                    "amount": np.full(11, 100.0, dtype=np.float64),
                }
            )
        raise AssertionError(f"unexpected get_daily_bars request: code={code0} count={count} end={end_time}")

    def fake_calc_atr_10(daily_df):
        calls.atr_last_times.append(str(daily_df["time"].iloc[-1]) if daily_df is not None and not daily_df.empty else "")
        return 0.1

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(
        svc_mod,
        "ensure_tick_data_downloaded",
        lambda codes, trade_date, *, force=False, chunk_size=200, timeout_sec=0, state_dir=None: {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": 0,
            "downloaded_now": 0,
            "failed_count": 0,
            "skipped_by_cache": len(codes),
            "chunks": 0,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        },
    )
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None, timeout_sec=0: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", fake_calc_atr_10)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "degraded": False, "reason": ""},
    )
    monkeypatch.setattr(svc_mod, "get_etf_info", lambda code: {})
    monkeypatch.setattr(svc_mod, "get_market_tick_data", lambda code, trade_date, *, count=-1: "ok")
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(
        svc_mod,
        "ticks_to_snapshots",
        lambda raw: pd.DataFrame(
            {
                "time": [93000.0, 150000.0],
                "open": [1.0, 1.0],
                "high": [1.0, 1.2],
                "low": [1.0, 0.95],
                "close": [1.0, 1.15],
                "volume": [10.0, 20.0],
                "amount": [10.0, 20.0],
                "bid1": [0.0, 0.0],
                "bid1_vol": [0.0, 0.0],
                "ask1": [0.0, 0.0],
                "ask1_vol": [0.0, 0.0],
            }
        ),
    )
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: prev_date)
    monkeypatch.setattr(svc_mod, "_compute_adv_60", lambda **_kwargs: None)

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
            "liquidity_prefilter_enabled": 0,
            "cold_start_lookback": 60,
        }
    )
    df = svc.run_daily(trade_date, codes=["510300.SH"], force_download=False)

    assert len(df) == 1
    assert calls.cold_start_last_time == prev_date
    assert trade_date in calls.atr_last_times


def test_run_daily_triggers_empty_retry_once_and_produces_row(tmp_path, monkeypatch) -> None:
    calls = SimpleNamespace(pre_download=0, retry=0, market=0)

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
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            return None

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_get_daily_bars(stock_list, *, end_time: str, count: int):
        n = max(int(count), 1)
        return pd.DataFrame(
            {
                "time": list(range(n)),
                "open": [1.0] * n,
                "high": [1.0] * n,
                "low": [1.0] * n,
                "close": [1.0] * n,
                "volume": [100.0] * n,
                "amount": [100.0] * n,
            }
        )

    def fake_market_tick(code: str, trade_date: str, *, count: int = -1):
        calls.market += 1
        return "ok" if calls.market >= 2 else None

    def fake_ticks_to_snapshots(raw):
        return _ok_snapshots() if raw == "ok" else _empty_snapshots()

    def fake_pre_download(codes, trade_date: str, *, force: bool = False, chunk_size: int = 200, timeout_sec: int = 0, state_dir=None):
        calls.pre_download += 1
        return {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": len(codes),
            "downloaded_now": len(codes),
            "failed_count": 0,
            "skipped_by_cache": 0,
            "chunks": 1,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        }

    def fake_retry(code: str, trade_date: str, *, state_dir=None, timeout_sec: int = 0):
        calls.retry += 1
        return True

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(svc_mod, "ensure_tick_data_downloaded", fake_pre_download)
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", fake_retry)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "degraded": False, "reason": ""},
    )
    monkeypatch.setattr(svc_mod, "get_etf_info", lambda code: {})
    monkeypatch.setattr(svc_mod, "get_market_tick_data", fake_market_tick)
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(svc_mod, "ticks_to_snapshots", fake_ticks_to_snapshots)
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: "")

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
        }
    )
    df = svc.run_daily("20260224", codes=["510300.SH"], force_download=True)

    assert calls.pre_download == 1
    assert calls.retry == 1
    assert len(df) == 1
    assert df.iloc[0]["code"] == "510300.SH"


def test_run_daily_fallbacks_to_iopv_when_tick_iopv_invalid(tmp_path, monkeypatch) -> None:
    calls = SimpleNamespace(premium_in_snapshots=None, premium_in_micro=None)

    class _DummyChip:
        def __init__(self) -> None:
            self.total_shares = 1000.0

    class _DummyCalc:
        def calculate_iopv(self) -> float:
            return 1.0

        def get_coverage(self) -> float:
            return 1.0

    class _DummyEngine:
        def __init__(self, config):
            self.config = config
            self.chips: dict[str, _DummyChip] = {}
            self.iopv = {}

        def load_state(self, code: str, path: str) -> None:
            self.chips[code] = _DummyChip()

        def cold_start(self, code: str, daily_df, total_shares: float, atr: float) -> None:
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            self.iopv[code] = _DummyCalc()

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            calls.premium_in_snapshots = float(snapshots["premium_rate"].iloc[0]) if "premium_rate" in snapshots.columns else None
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            premium_rates = kwargs.get("premium_rates")
            if premium_rates is not None and len(premium_rates):
                calls.premium_in_micro = float(premium_rates.iloc[0])
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_get_daily_bars(stock_list, *, end_time: str, count: int):
        n = max(int(count), 1)
        return pd.DataFrame(
            {
                "time": list(range(n)),
                "open": [1.0] * n,
                "high": [1.0] * n,
                "low": [1.0] * n,
                "close": [1.0] * n,
                "volume": [100.0] * n,
                "amount": [100.0] * n,
            }
        )

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(
        svc_mod,
        "ensure_tick_data_downloaded",
        lambda codes, trade_date, *, force=False, chunk_size=200, timeout_sec=0, state_dir=None: {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": 0,
            "downloaded_now": 0,
            "failed_count": 0,
            "skipped_by_cache": len(codes),
            "chunks": 0,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        },
    )
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None, timeout_sec=0: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "degraded": False, "reason": ""},
    )
    monkeypatch.setattr(svc_mod, "get_etf_info", lambda code: {"reportUnit": 100.0, "stocks": {}})
    monkeypatch.setattr(svc_mod, "get_market_tick_data", lambda code, trade_date, *, count=-1: "ok")
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(svc_mod, "ticks_to_snapshots", lambda raw: _ok_snapshots_with_invalid_iopv() if raw == "ok" else _empty_snapshots())
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: "")

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
        }
    )
    df = svc.run_daily("20260224", codes=["510300.SH"], force_download=False)

    assert len(df) == 1
    assert calls.premium_in_snapshots is not None
    assert calls.premium_in_micro is not None
    assert abs(calls.premium_in_snapshots - 0.2) < 1e-9
    assert abs(calls.premium_in_micro - 0.2) < 1e-9


def test_run_daily_downgrades_iopv_fallback_when_coverage_below_threshold(tmp_path, monkeypatch, capsys) -> None:
    calls = SimpleNamespace(premium_in_snapshots=None, premium_in_micro=None)

    class _DummyChip:
        def __init__(self) -> None:
            self.total_shares = 1000.0

    class _DummyCalc:
        def calculate_iopv(self) -> float:
            return 1.0

        def get_coverage(self) -> float:
            return 0.90

    class _DummyEngine:
        def __init__(self, config):
            self.config = config
            self.chips: dict[str, _DummyChip] = {}
            self.iopv = {}

        def load_state(self, code: str, path: str) -> None:
            self.chips[code] = _DummyChip()

        def cold_start(self, code: str, daily_df, total_shares: float, atr: float) -> None:
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            self.iopv[code] = _DummyCalc()

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            calls.premium_in_snapshots = float(snapshots["premium_rate"].iloc[0]) if "premium_rate" in snapshots.columns else None
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            premium_rates = kwargs.get("premium_rates")
            if premium_rates is not None and len(premium_rates):
                calls.premium_in_micro = float(premium_rates.iloc[0])
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_get_daily_bars(stock_list, *, end_time: str, count: int):
        n = max(int(count), 1)
        return pd.DataFrame(
            {
                "time": list(range(n)),
                "open": [1.0] * n,
                "high": [1.0] * n,
                "low": [1.0] * n,
                "close": [1.0] * n,
                "volume": [100.0] * n,
                "amount": [100.0] * n,
            }
        )

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(
        svc_mod,
        "ensure_tick_data_downloaded",
        lambda codes, trade_date, *, force=False, chunk_size=200, timeout_sec=0, state_dir=None: {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": 0,
            "downloaded_now": 0,
            "failed_count": 0,
            "skipped_by_cache": len(codes),
            "chunks": 0,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        },
    )
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None, timeout_sec=0: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "degraded": False, "reason": ""},
    )
    monkeypatch.setattr(
        svc_mod,
        "get_etf_info",
        lambda code: {"name": "示例ETF", "reportUnit": 100.0, "stocks": {}},
    )
    monkeypatch.setattr(svc_mod, "get_market_tick_data", lambda code, trade_date, *, count=-1: "ok")
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(svc_mod, "ticks_to_snapshots", lambda raw: _ok_snapshots_with_invalid_iopv() if raw == "ok" else _empty_snapshots())
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: "")

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
            "premium_iopv_min_coverage": 0.95,
        }
    )
    df = svc.run_daily("20260224", codes=["510300.SH"], force_download=False)
    out = capsys.readouterr().out

    assert len(df) == 1
    assert calls.premium_in_snapshots is not None
    assert calls.premium_in_micro is not None
    assert abs(calls.premium_in_snapshots - 0.0) < 1e-12
    assert abs(calls.premium_in_micro - 0.0) < 1e-12
    assert "iopv_coverage_downgrade_summary" in out
    assert "510300.SH" in out
    assert "示例ETF" in out


def test_run_daily_prints_warn_for_caught_exception(tmp_path, monkeypatch, capsys) -> None:
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
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            return None

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_get_daily_bars(stock_list, *, end_time: str, count: int):
        n = max(int(count), 1)
        return pd.DataFrame(
            {
                "time": list(range(n)),
                "open": [1.0] * n,
                "high": [1.0] * n,
                "low": [1.0] * n,
                "close": [1.0] * n,
                "volume": [100.0] * n,
                "amount": [100.0] * n,
            }
        )

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(
        svc_mod,
        "ensure_tick_data_downloaded",
        lambda codes, trade_date, *, force=False, chunk_size=200, timeout_sec=0, state_dir=None: {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": 0,
            "downloaded_now": 0,
            "failed_count": 0,
            "skipped_by_cache": len(codes),
            "chunks": 0,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        },
    )
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None, timeout_sec=0: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "xtdata_totalvolume", "degraded": True, "reason": "official_unavailable"},
    )
    monkeypatch.setattr(svc_mod, "get_etf_info", lambda code: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(svc_mod, "get_market_tick_data", lambda code, trade_date, *, count=-1: "ok")
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(svc_mod, "ticks_to_snapshots", lambda raw: _ok_snapshots() if raw == "ok" else _empty_snapshots())
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: "")

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
        }
    )
    _ = svc.run_daily("20260225", codes=["159915.SZ"], force_download=False)
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "get_etf_info" in out
    assert "官方份额不可用" in out


def test_run_daily_admission_filter_skips_large_constituent_etf(tmp_path, monkeypatch) -> None:
    calls = SimpleNamespace(pre_download_count=-1, market=0)

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
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            return None

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_pre_download(codes, trade_date: str, *, force: bool = False, chunk_size: int = 200, timeout_sec: int = 0, state_dir=None):
        calls.pre_download_count = len(codes)
        return {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": 0,
            "downloaded_now": 0,
            "failed_count": 0,
            "skipped_by_cache": len(codes),
            "chunks": 0,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        }

    def fake_get_etf_info(code: str) -> dict:
        return {
            "reportUnit": 100.0,
            "stocks": {f"{i:06d}.SZ": {"componentVolume": 1.0} for i in range(201)},
        }

    def fake_market_tick(code: str, trade_date: str, *, count: int = -1):
        calls.market += 1
        return "ok"

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(svc_mod, "ensure_tick_data_downloaded", fake_pre_download)
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None, timeout_sec=0: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", lambda stock_list, *, end_time, count: pd.DataFrame())
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "degraded": False, "reason": ""},
    )
    monkeypatch.setattr(svc_mod, "get_etf_info", fake_get_etf_info)
    monkeypatch.setattr(svc_mod, "get_market_tick_data", fake_market_tick)
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(svc_mod, "ticks_to_snapshots", lambda raw: _ok_snapshots() if raw == "ok" else _empty_snapshots())
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: "")

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
            "industry_etf_max_constituents": 200,
        }
    )
    df = svc.run_daily("20260225", codes=["560510.SH"], force_download=False)

    assert calls.pre_download_count == 0
    assert calls.market == 0
    assert df.empty


def test_run_daily_admission_filter_skips_low_a_share_ratio_etf(tmp_path, monkeypatch) -> None:
    calls = SimpleNamespace(pre_download_count=-1, market=0)

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
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            return None

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_pre_download(codes, trade_date: str, *, force: bool = False, chunk_size: int = 200, timeout_sec: int = 0, state_dir=None):
        calls.pre_download_count = len(codes)
        return {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": 0,
            "downloaded_now": 0,
            "failed_count": 0,
            "skipped_by_cache": len(codes),
            "chunks": 0,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        }

    def fake_get_etf_info(code: str) -> dict:
        return {
            "reportUnit": 100.0,
            "stocks": {f"{i:05d}.HK": {"componentVolume": 1.0} for i in range(1, 51)},
        }

    def fake_market_tick(code: str, trade_date: str, *, count: int = -1):
        calls.market += 1
        return "ok"

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(svc_mod, "ensure_tick_data_downloaded", fake_pre_download)
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None, timeout_sec=0: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", lambda stock_list, *, end_time, count: pd.DataFrame())
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "degraded": False, "reason": ""},
    )
    monkeypatch.setattr(svc_mod, "get_etf_info", fake_get_etf_info)
    monkeypatch.setattr(svc_mod, "get_market_tick_data", fake_market_tick)
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(svc_mod, "ticks_to_snapshots", lambda raw: _ok_snapshots() if raw == "ok" else _empty_snapshots())
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: "")

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
            "industry_etf_max_constituents": 200,
            "industry_etf_min_a_share_ratio": 0.95,
        }
    )
    df = svc.run_daily("20260225", codes=["513560.SH"], force_download=False)

    assert calls.pre_download_count == 0
    assert calls.market == 0
    assert df.empty


def test_run_daily_prints_progress_bar(tmp_path, monkeypatch, capsys) -> None:
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
            self.chips[code] = _DummyChip()

        def attach_iopv(self, code: str, etf_info: dict) -> None:
            return None

        def process_daily(self, code: str, snapshots: pd.DataFrame, *, shares_today: float, shares_yesterday: float, atr: float):
            return {"profit_ratio": 0.5, "asr": 0.5, "dense_zones": []}

        def save_state(self, code: str, path: str) -> None:
            return None

    class _DummyMicro:
        def __init__(self, config):
            self.config = config

        def process_daily(self, **kwargs):
            return {"raw": {}, "features": {}, "meta": {}}

    def fake_get_daily_bars(stock_list, *, end_time: str, count: int):
        n = max(int(count), 1)
        return pd.DataFrame(
            {
                "time": list(range(n)),
                "open": [1.0] * n,
                "high": [1.0] * n,
                "low": [1.0] * n,
                "close": [1.0] * n,
                "volume": [100.0] * n,
                "amount": [100.0] * n,
            }
        )

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(
        svc_mod,
        "ensure_tick_data_downloaded",
        lambda codes, trade_date, *, force=False, chunk_size=200, timeout_sec=0, state_dir=None: {
            "trade_date": trade_date,
            "input_count": len(codes),
            "pending_count": 0,
            "downloaded_now": 0,
            "failed_count": 0,
            "skipped_by_cache": len(codes),
            "chunks": 0,
            "chunk_size": chunk_size,
            "timeout_sec": timeout_sec,
            "force": force,
            "state_path": str(tmp_path / "tick_state.json"),
        },
    )
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None, timeout_sec=0: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
    monkeypatch.setattr(
        svc_mod,
        "get_total_shares_detail",
        lambda code, *, trade_date="": {"shares": 1000.0, "source": "official_sse", "degraded": False, "reason": ""},
    )
    monkeypatch.setattr(svc_mod, "get_etf_info", lambda code: {})
    monkeypatch.setattr(svc_mod, "get_market_tick_data", lambda code, trade_date, *, count=-1: "ok")
    monkeypatch.setattr(svc_mod, "get_local_tick_data", lambda code, trade_date: None)
    monkeypatch.setattr(svc_mod, "ticks_to_snapshots", lambda raw: _ok_snapshots() if raw == "ok" else _empty_snapshots())
    monkeypatch.setattr(svc_mod, "filter_etf_codes_by_keywords", lambda codes: codes)
    monkeypatch.setattr(svc_mod, "prev_trade_date", lambda trade_date: "")

    svc = svc_mod.IndustryETFChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
            "l1_fallback_csv": "0",
            "progress_update_sec": 0.0,
        }
    )
    _ = svc.run_daily("20260225", codes=["159915.SZ"], force_download=False)
    out = capsys.readouterr().out
    assert "progress [" in out
    assert "1/1" in out
