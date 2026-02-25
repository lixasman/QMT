from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

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

    def fake_retry(code: str, trade_date: str, *, state_dir=None):
        calls.retry += 1
        return True

    monkeypatch.setattr(svc_mod, "ETFChipEngine", _DummyEngine)
    monkeypatch.setattr(svc_mod, "MicrostructureEngine", _DummyMicro)
    monkeypatch.setattr(svc_mod, "ensure_tick_data_downloaded", fake_pre_download)
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", fake_retry)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
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
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
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
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
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
    monkeypatch.setattr(svc_mod, "retry_download_for_empty_tick_code_once", lambda code, trade_date, *, state_dir=None: False)
    monkeypatch.setattr(svc_mod, "get_daily_bars", fake_get_daily_bars)
    monkeypatch.setattr(svc_mod, "calc_atr_10", lambda daily_df: 0.1)
    monkeypatch.setattr(svc_mod, "get_total_shares", lambda code: 1000.0)
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
