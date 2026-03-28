from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etf_chip_engine.models import ChipDistribution
from stock_chip_engine import service as svc_mod
from stock_chip_engine.service import StockChipService
from stock_chip_engine.data import xtdata_provider as xdp


def test_history_before_trade_date_drops_trade_date_bar_and_trims_stale_shape() -> None:
    fresh = pd.DataFrame(
        {
            "time": ["20260317", "20260318", "20260319", "20260320"],
            "close": [10.0, 11.0, 12.0, 13.0],
        }
    )
    stale = pd.DataFrame(
        {
            "time": ["20260316", "20260317", "20260318", "20260319"],
            "close": [9.0, 10.0, 11.0, 12.0],
        }
    )

    fresh_hist = svc_mod._history_before_trade_date(fresh, trade_date="20260320", keep_count=3)
    stale_hist = svc_mod._history_before_trade_date(stale, trade_date="20260320", keep_count=3)

    assert fresh_hist["time"].tolist() == ["20260317", "20260318", "20260319"]
    assert stale_hist["time"].tolist() == ["20260317", "20260318", "20260319"]


def test_load_daily_history_before_trade_date_raises_when_prev_day_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        xdp,
        "get_daily_bars",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "time": ["20260314", "20260317", "20260318"],
                "close": [10.0, 11.0, 12.0],
            }
        ),
    )

    with pytest.raises(RuntimeError, match="stale daily history"):
        svc_mod._load_daily_history_before_trade_date(
            code="600201.SH",
            trade_date="20260320",
            count=2,
            lot_size=100.0,
            volume_in_lots=True,
            expected_last_trade_date="20260319",
            context="test:cold_start",
        )


def test_load_daily_history_before_trade_date_backfills_once_when_prev_day_missing(monkeypatch) -> None:
    calls = {"daily": 0, "download": []}

    def _fake_daily_bars(*_args, **_kwargs) -> pd.DataFrame:
        calls["daily"] += 1
        if calls["daily"] == 1:
            return pd.DataFrame(
                {
                    "time": ["20260314", "20260317", "20260318"],
                    "close": [10.0, 11.0, 12.0],
                }
            )
        return pd.DataFrame(
            {
                "time": ["20260317", "20260318", "20260319"],
                "close": [11.0, 12.0, 13.0],
            }
        )

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)
    monkeypatch.setattr(
        xdp,
        "download_daily_data",
        lambda stock_codes, start_time, end_time, chunk_size=80: calls["download"].append(
            {
                "stock_codes": list(stock_codes),
                "start_time": str(start_time),
                "end_time": str(end_time),
                "chunk_size": int(chunk_size),
            }
        ),
        raising=False,
    )

    hist = svc_mod._load_daily_history_before_trade_date(
        code="600201.SH",
        trade_date="20260320",
        count=2,
        lot_size=100.0,
        volume_in_lots=True,
        expected_last_trade_date="20260319",
        context="test:cold_start",
    )

    assert hist["time"].tolist() == ["20260318", "20260319"]
    assert len(calls["download"]) == 1
    assert calls["download"][0]["stock_codes"] == ["600201.SH"]
    assert calls["download"][0]["end_time"] == "20260320"


def test_compute_adv_60_uses_previous_60_days_when_trade_date_bar_is_stale(monkeypatch) -> None:
    def _fake_daily_bars(*_args, **_kwargs) -> pd.DataFrame:
        volume = np.arange(1.0, 62.0, dtype=np.float64)
        return pd.DataFrame(
            {
                "time": [f"d{i:02d}" for i in range(61)],
                "open": volume,
                "high": volume,
                "low": volume,
                "close": volume,
                "volume": volume,
                "amount": volume,
            }
        )

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)

    adv = svc_mod._compute_adv_60(
        code="600201.SH",
        trade_date="20260320",
        lot_size=100.0,
        volume_in_lots=True,
        cfg={"daily_volume_self_check": 0},
        prev_trade_date="",
    )

    assert adv == pytest.approx(float(np.arange(2.0, 62.0, dtype=np.float64).mean()))


def test_load_trade_date_daily_bars_backfills_once_when_prev_day_missing(monkeypatch) -> None:
    trade_date = "20260320"
    prev_date = "20260319"
    calls = {"daily": 0, "download": []}

    def _bars(times: list[str]) -> pd.DataFrame:
        n = len(times)
        close = np.linspace(10.0, 10.0 + (n - 1) * 0.1, n)
        return pd.DataFrame(
            {
                "time": times,
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": np.full(n, 1000.0, dtype=np.float64),
                "amount": close * 1000.0,
            }
        )

    def _fake_daily_bars(*_args, **_kwargs) -> pd.DataFrame:
        calls["daily"] += 1
        if calls["daily"] == 1:
            return _bars(["20260314", "20260317", "20260318"])
        return _bars(["20260317", "20260318", "20260319"])

    snapshots = pd.DataFrame(
        {
            "close": [10.85, 10.97],
            "high": [10.90, 11.00],
            "low": [10.80, 10.90],
            "volume": [100.0, 200.0],
            "amount": [1085.0, 2194.0],
        }
    )

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)
    monkeypatch.setattr(
        xdp,
        "download_daily_data",
        lambda stock_codes, start_time, end_time, chunk_size=80: calls["download"].append(
            {
                "stock_codes": list(stock_codes),
                "start_time": str(start_time),
                "end_time": str(end_time),
                "chunk_size": int(chunk_size),
            }
        ),
        raising=False,
    )

    daily_df = svc_mod._load_trade_date_daily_bars(
        code="600201.SH",
        trade_date=trade_date,
        count=3,
        lot_size=100.0,
        volume_in_lots=True,
        expected_prev_trade_date=prev_date,
        snapshots=snapshots,
        context="test:daily_context",
    )

    assert daily_df["time"].tolist() == ["20260317", "20260318", "20260319", trade_date]
    assert len(calls["download"]) == 1
    assert calls["download"][0]["stock_codes"] == ["600201.SH"]
    assert calls["download"][0]["end_time"] == trade_date


def test_run_daily_prefetches_1d_before_tick_download(monkeypatch, tmp_path: Path) -> None:
    events: list[tuple[str, tuple[str, ...], str]] = []

    monkeypatch.setattr(xdp, "require_xtdata", lambda: object())
    monkeypatch.setattr(
        xdp,
        "download_daily_data",
        lambda stock_codes, start_time, end_time, chunk_size=80: events.append(("daily", tuple(stock_codes), str(end_time))),
        raising=False,
    )
    monkeypatch.setattr(
        xdp,
        "download_tick_data",
        lambda stock_codes, trade_date, chunk_size=80: events.append(("tick", tuple(stock_codes), str(trade_date))),
    )

    def _stop_prev_trade_date(_trade_date: str, *, market: str = "SH") -> str:
        _ = market
        raise RuntimeError("stop_after_prefetch")

    monkeypatch.setattr(xdp, "prev_trade_date", _stop_prev_trade_date)

    svc = StockChipService(
        config={
            "chip_snapshot_dir": str(tmp_path / "chip_snapshots"),
            "l1_snapshot_dir": str(tmp_path / "l1_snapshots"),
        }
    )

    with pytest.raises(RuntimeError, match="stop_after_prefetch"):
        svc.run_daily("20260320", codes=["600201"])

    assert events == [
        ("daily", ("600201.SH",), "20260320"),
        ("tick", ("600201.SH",), "20260320"),
    ]


def test_stock_service_flow_with_prev_state_and_corp_rescale(monkeypatch, tmp_path: Path) -> None:
    trade_date = "20260304"
    prev_date = "20260303"
    code = "000001.SZ"

    chip_dir = tmp_path / "chip_snapshots"
    l1_dir = tmp_path / "l1_snapshots"
    fh_dir = tmp_path / "factor_history"
    chip_dir.mkdir(parents=True, exist_ok=True)
    l1_dir.mkdir(parents=True, exist_ok=True)
    fh_dir.mkdir(parents=True, exist_ok=True)

    # Prepare a previous state file so the service takes the "load + corp action rescale" path.
    chips = ChipDistribution(etf_code=code, base_price=0.0, bucket_size=0.01)
    chips.ensure_range(0.0, 20.0, padding_buckets=0)
    chips.chips[:] = 0.0
    chips.chips[chips.price_to_index(10.0)] = np.float32(1000.0)
    chips.total_shares = 1_000_000.0
    prev_state = chip_dir / f"{code.replace('.', '_')}_{prev_date}.npz"
    chips.save(prev_state)

    # Monkeypatch xtdata provider I/O.
    monkeypatch.setattr(xdp, "require_xtdata", lambda: object())
    monkeypatch.setattr(xdp, "prev_trade_date", lambda *_args, **_kwargs: prev_date)
    monkeypatch.setattr(xdp, "get_instrument_detail", lambda _code: {"PriceTick": 0.01})
    monkeypatch.setattr(
        xdp,
        "get_float_shares_detail",
        lambda _code, trade_date="": {"code": _code, "shares": 1_000_000.0, "source": "xtdata_floatvolume", "degraded": False},
    )
    monkeypatch.setattr(xdp, "get_divid_factors", lambda *_args, **_kwargs: pd.DataFrame({"time": [trade_date], "dr": [1.1111111111]}))

    def _fake_daily_bars(
        stock_list,
        *,
        end_time: str,
        count: int,
        dividend_type: str = "none",
        lot_size: float = 100.0,
        volume_in_lots: bool = True,
    ) -> pd.DataFrame:
        _ = stock_list, lot_size, volume_in_lots
        n = int(count)
        if n <= 0:
            return pd.DataFrame()

        # Two-bar window for corp-action factor detection
        if n == 2:
            times = [prev_date, end_time]
            if dividend_type == "front":
                closes = [9.0, 10.0]  # factor=0.9
            else:
                closes = [10.0, 10.0]
            return pd.DataFrame(
                {
                    "time": times,
                    "open": closes,
                    "high": closes,
                    "low": closes,
                    "close": closes,
                    "volume": [10000.0, 10000.0],
                    "amount": [100000.0, 100000.0],
                }
            )

        # ATR / ADV windows: simple stable bars
        times = pd.bdate_range(end=pd.Timestamp(prev_date), periods=n - 1).strftime("%Y%m%d").tolist() + [end_time]
        close = np.full(n, 10.0, dtype=np.float64)
        return pd.DataFrame(
            {
                "time": times,
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": np.full(n, 10000.0, dtype=np.float64),
                "amount": np.full(n, 100000.0, dtype=np.float64),
            }
        )

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)

    # Fake tick data: cumulative volume in lots and amount in currency.
    ticks = pd.DataFrame(
        {
            "time": [93000, 93003, 93006, 93009],
            "lastPrice": [10.0, 10.0, 10.0, 10.0],
            "high": [10.0, 10.0, 10.0, 10.0],
            "low": [10.0, 10.0, 10.0, 10.0],
            "amount": [0.0, 10000.0, 20000.0, 30000.0],
            "volume": [0.0, 10.0, 20.0, 30.0],
            "bidPrice1": [9.99, 9.99, 9.99, 9.99],
            "bidVol1": [1000.0, 1000.0, 1000.0, 1000.0],
            "askPrice1": [10.01, 10.01, 10.01, 10.01],
            "askVol1": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
    monkeypatch.setattr(xdp, "get_local_tick_data", lambda *_args, **_kwargs: ticks)
    monkeypatch.setattr(xdp, "get_market_tick_data", lambda *_args, **_kwargs: None)

    svc = StockChipService(
        config={
            "chip_snapshot_dir": str(chip_dir),
            "l1_snapshot_dir": str(l1_dir),
            "microstructure": {"factor_history_dir": str(fh_dir)},
            "tick_volume_self_check": 0,  # keep test quiet
        }
    )

    df = svc.run_daily(trade_date, codes=[code])
    assert df is not None
    assert len(df) == 1

    row = df.iloc[0].to_dict()
    assert row["code"] == code
    assert bool(row.get("corp_action_applied")) is True
    assert abs(float(row.get("corp_action_factor")) - 0.9) < 1e-9
    assert Path(str(row.get("state_path"))).exists()
    assert str(row.get("prev_trade_date")) == prev_date
    assert bool(row.get("prev_state_found")) is True
    assert bool(row.get("prev_state_loaded")) is True
    assert bool(row.get("cold_start_used")) is False
    assert str(row.get("cold_start_reason") or "") == ""
    assert str(row.get("state_init")) == "prev_state"
    assert Path(str(row.get("prev_state_path"))).exists()


def test_stock_service_flow_populates_daily_context_fields(monkeypatch, tmp_path: Path) -> None:
    trade_date = "20260320"
    prev_date = "20260319"
    code = "000001.SZ"

    chip_dir = tmp_path / "chip_snapshots"
    l1_dir = tmp_path / "l1_snapshots"
    fh_dir = tmp_path / "factor_history"
    chip_dir.mkdir(parents=True, exist_ok=True)
    l1_dir.mkdir(parents=True, exist_ok=True)
    fh_dir.mkdir(parents=True, exist_ok=True)

    prev_state = chip_dir / f"{code.replace('.', '_')}_{prev_date}.npz"
    chips = ChipDistribution(etf_code=code, base_price=0.0, bucket_size=0.01)
    chips.ensure_range(0.0, 200.0, padding_buckets=0)
    chips.chips[:] = 0.0
    chips.chips[chips.price_to_index(99.0)] = np.float32(1000.0)
    chips.total_shares = 1_000_000.0
    chips.save(prev_state)

    monkeypatch.setattr(xdp, "require_xtdata", lambda: object())
    monkeypatch.setattr(xdp, "prev_trade_date", lambda *_args, **_kwargs: prev_date)
    monkeypatch.setattr(xdp, "get_instrument_detail", lambda _code: {"PriceTick": 0.01})
    monkeypatch.setattr(
        xdp,
        "get_float_shares_detail",
        lambda _code, trade_date="": {"code": _code, "shares": 1_000_000.0, "source": "xtdata_floatvolume", "degraded": False},
    )
    monkeypatch.setattr(xdp, "get_divid_factors", lambda *_args, **_kwargs: pd.DataFrame())

    def _bars(end_time: str, closes: list[float], *, opens: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> pd.DataFrame:
        times = pd.bdate_range(end=pd.Timestamp(prev_date), periods=len(closes) - 1).strftime("%Y%m%d").tolist() + [end_time]
        close_arr = np.asarray(closes, dtype=np.float64)
        volume_arr = np.asarray(volumes, dtype=np.float64)
        return pd.DataFrame(
            {
                "time": times,
                "open": np.asarray(opens, dtype=np.float64),
                "high": np.asarray(highs, dtype=np.float64),
                "low": np.asarray(lows, dtype=np.float64),
                "close": close_arr,
                "volume": volume_arr,
                "amount": close_arr * volume_arr,
            }
        )

    daily_11 = _bars(
        trade_date,
        closes=[90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0, 100.0],
        opens=[89.5, 90.5, 91.5, 92.5, 93.5, 94.5, 95.5, 96.5, 97.5, 98.5, 101.0],
        highs=[90.5, 91.5, 92.5, 93.5, 94.5, 95.5, 96.5, 97.5, 98.5, 99.5, 103.0],
        lows=[89.0, 90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 98.0],
        volumes=[1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 2000.0],
    )
    daily_61 = _bars(
        trade_date,
        closes=[40.0 + i for i in range(61)],
        opens=[39.5 + i for i in range(61)],
        highs=[40.5 + i for i in range(61)],
        lows=[39.0 + i for i in range(61)],
        volumes=[1000.0] * 60 + [2000.0],
    )
    sh_bars = _bars(
        trade_date,
        closes=[3000.0, 3030.0],
        opens=[2990.0, 3010.0],
        highs=[3010.0, 3040.0],
        lows=[2980.0, 3000.0],
        volumes=[1.0, 1.0],
    )
    cy_bars = _bars(
        trade_date,
        closes=[2000.0, 1960.0],
        opens=[1990.0, 1980.0],
        highs=[2010.0, 1990.0],
        lows=[1980.0, 1950.0],
        volumes=[1.0, 1.0],
    )

    def _fake_daily_bars(
        stock_list,
        *,
        end_time: str,
        count: int,
        dividend_type: str = "none",
        lot_size: float = 100.0,
        volume_in_lots: bool = True,
    ) -> pd.DataFrame:
        _ = end_time, dividend_type, lot_size, volume_in_lots
        code0 = stock_list[0]
        if code0 == code:
            if int(count) == 2:
                return _bars(
                    trade_date,
                    closes=[99.0, 100.0],
                    opens=[99.0, 101.0],
                    highs=[100.0, 103.0],
                    lows=[98.0, 98.0],
                    volumes=[1000.0, 2000.0],
                )
            if int(count) == 11:
                return daily_11.copy()
            if int(count) == 61:
                return daily_61.copy()
        if code0 == "000001.SH" and int(count) == 2:
            return sh_bars.copy()
        if code0 == "399006.SZ" and int(count) == 2:
            return cy_bars.copy()
        raise AssertionError(f"unexpected get_daily_bars request: code={code0} count={count}")

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)

    ticks = pd.DataFrame(
        {
            "time": [93000, 93003, 93006, 93009],
            "lastPrice": [99.5, 100.0, 100.5, 100.0],
            "high": [99.5, 100.0, 100.5, 100.5],
            "low": [99.5, 99.8, 100.0, 99.9],
            "amount": [0.0, 10000.0, 20000.0, 30000.0],
            "volume": [0.0, 10.0, 20.0, 30.0],
            "bidPrice1": [99.9, 99.9, 100.0, 99.9],
            "bidVol1": [1000.0, 1000.0, 1000.0, 1000.0],
            "askPrice1": [100.1, 100.1, 100.2, 100.1],
            "askVol1": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
    monkeypatch.setattr(xdp, "get_local_tick_data", lambda *_args, **_kwargs: ticks)
    monkeypatch.setattr(xdp, "get_market_tick_data", lambda *_args, **_kwargs: None)

    svc = StockChipService(
        config={
            "chip_snapshot_dir": str(chip_dir),
            "l1_snapshot_dir": str(l1_dir),
            "microstructure": {"factor_history_dir": str(fh_dir)},
            "tick_volume_self_check": 0,
            "daily_volume_self_check": 0,
        }
    )

    df = svc.run_daily(trade_date, codes=[code])
    assert len(df) == 1

    row = df.iloc[0].to_dict()
    assert abs(float(row["change_3d"]) - 3.0927835051546393) < 1e-12
    assert abs(float(row["change_5d"]) - 5.263157894736842) < 1e-12
    assert abs(float(row["open_pct"]) - 2.0202020202020203) < 1e-12
    assert abs(float(row["close_pct"]) - 1.0101010101010102) < 1e-12
    assert abs(float(row["high_pct"]) - 4.040404040404041) < 1e-12
    assert abs(float(row["low_pct"]) - (-1.0101010101010102)) < 1e-12
    assert abs(float(row["ma5_pos"]) - 2.0202020202020203) < 1e-12
    assert abs(float(row["ma10_pos"]) - 4.545454545454546) < 1e-12
    assert abs(float(row["rsi_5"]) - 100.0) < 1e-12
    assert abs(float(row["vol_ratio"]) - 2.0) < 1e-12
    assert abs(float(row["sh_change"]) - 1.0) < 1e-12
    assert abs(float(row["cy_change"]) - (-2.0)) < 1e-12


def test_stock_service_flow_uses_tick_bar_when_trade_date_daily_bar_missing(monkeypatch, tmp_path: Path) -> None:
    trade_date = "20260320"
    prev_date = "20260319"
    code = "600201.SH"

    chip_dir = tmp_path / "chip_snapshots"
    l1_dir = tmp_path / "l1_snapshots"
    fh_dir = tmp_path / "factor_history"
    chip_dir.mkdir(parents=True, exist_ok=True)
    l1_dir.mkdir(parents=True, exist_ok=True)
    fh_dir.mkdir(parents=True, exist_ok=True)

    chips = ChipDistribution(etf_code=code, base_price=0.0, bucket_size=0.01)
    chips.ensure_range(0.0, 30.0, padding_buckets=0)
    chips.chips[:] = 0.0
    chips.chips[chips.price_to_index(15.0)] = np.float32(1000.0)
    chips.total_shares = 1_000_000.0
    chips.save(chip_dir / f"{code.replace('.', '_')}_{prev_date}.npz")

    monkeypatch.setattr(xdp, "require_xtdata", lambda: object())
    monkeypatch.setattr(xdp, "prev_trade_date", lambda *_args, **_kwargs: prev_date)
    monkeypatch.setattr(xdp, "get_instrument_detail", lambda _code: {"PriceTick": 0.01})
    monkeypatch.setattr(
        xdp,
        "get_float_shares_detail",
        lambda _code, trade_date="": {"code": _code, "shares": 1_000_000.0, "source": "xtdata_floatvolume", "degraded": False},
    )
    monkeypatch.setattr(xdp, "get_divid_factors", lambda *_args, **_kwargs: pd.DataFrame())

    def _bars(times: list[str], closes: list[float], *, opens: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> pd.DataFrame:
        close_arr = np.asarray(closes, dtype=np.float64)
        volume_arr = np.asarray(volumes, dtype=np.float64)
        return pd.DataFrame(
            {
                "time": times,
                "open": np.asarray(opens, dtype=np.float64),
                "high": np.asarray(highs, dtype=np.float64),
                "low": np.asarray(lows, dtype=np.float64),
                "close": close_arr,
                "volume": volume_arr,
                "amount": close_arr * volume_arr,
            }
        )

    stale_daily = _bars(
        ["20260306", "20260309", "20260310", "20260311", "20260312", "20260313", "20260316", "20260317", "20260318", "20260319"],
        [14.95, 14.89, 15.18, 14.80, 15.04, 15.10, 15.12, 15.08, 16.17, 14.83],
        opens=[14.56, 14.79, 15.00, 15.20, 14.75, 14.93, 15.00, 15.19, 15.12, 15.89],
        highs=[15.02, 15.01, 15.33, 15.29, 15.14, 15.41, 15.23, 15.40, 16.19, 16.16],
        lows=[14.51, 14.49, 15.00, 14.76, 14.67, 14.90, 14.81, 14.98, 14.93, 14.66],
        volumes=[25254400.0, 24007600.0, 20815000.0, 17993300.0, 16451700.0, 21188600.0, 15668000.0, 18833200.0, 51201800.0, 69414600.0],
    )
    bars_61 = _bars(
        [f"d{i:02d}" for i in range(61)],
        [10.0 + i * 0.1 for i in range(61)],
        opens=[9.9 + i * 0.1 for i in range(61)],
        highs=[10.1 + i * 0.1 for i in range(61)],
        lows=[9.8 + i * 0.1 for i in range(61)],
        volumes=[10000.0] * 61,
    )

    def _fake_daily_bars(stock_list, *, end_time: str, count: int, dividend_type: str = "none", lot_size: float = 100.0, volume_in_lots: bool = True) -> pd.DataFrame:
        _ = end_time, dividend_type, lot_size, volume_in_lots
        code0 = stock_list[0]
        if code0 == code and int(count) == 2:
            return stale_daily.tail(2).reset_index(drop=True)
        if code0 == code and int(count) == 11:
            return stale_daily.copy()
        if code0 == code and int(count) == 61:
            return bars_61.copy()
        if code0 == "000001.SH" and int(count) == 2:
            return _bars([prev_date, trade_date], [3000.0, 3030.0], opens=[2990.0, 3010.0], highs=[3010.0, 3040.0], lows=[2980.0, 3000.0], volumes=[1.0, 1.0])
        if code0 == "399006.SZ" and int(count) == 2:
            return _bars([prev_date, trade_date], [2000.0, 1960.0], opens=[1990.0, 1980.0], highs=[2010.0, 1990.0], lows=[1980.0, 1950.0], volumes=[1.0, 1.0])
        raise AssertionError(f"unexpected get_daily_bars request: code={code0} count={count}")

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)

    ticks = pd.DataFrame(
        {
            "time": [93000, 93003, 150000, 150003],
            "lastPrice": [15.89, 15.20, 14.51, 14.60],
            "high": [15.89, 15.89, 15.89, 15.89],
            "low": [15.89, 15.20, 14.51, 14.29],
            "amount": [1000.0, 2000.0, 3000.0, 4000.0],
            "volume": [10.0, 20.0, 30.0, 40.0],
            "bidPrice1": [15.88, 15.19, 14.50, 14.59],
            "bidVol1": [1000.0, 1000.0, 1000.0, 1000.0],
            "askPrice1": [15.90, 15.21, 14.52, 14.60],
            "askVol1": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
    monkeypatch.setattr(xdp, "get_local_tick_data", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(xdp, "get_market_tick_data", lambda req_code, *_args, **_kwargs: ticks if req_code == code else pd.DataFrame())

    svc = StockChipService(
        config={
            "chip_snapshot_dir": str(chip_dir),
            "l1_snapshot_dir": str(l1_dir),
            "microstructure": {"factor_history_dir": str(fh_dir)},
            "tick_volume_self_check": 0,
            "daily_volume_self_check": 0,
        }
    )

    row = svc.run_daily(trade_date, codes=[code]).iloc[0].to_dict()
    assert abs(float(row["close_pct"]) - (-1.5509103236546993)) < 1e-8








def test_stock_service_flow_raises_when_benchmark_stale_and_no_tick(monkeypatch, tmp_path: Path) -> None:
    trade_date = "20260320"
    prev_date = "20260319"
    code = "000001.SZ"

    chip_dir = tmp_path / "chip_snapshots"
    l1_dir = tmp_path / "l1_snapshots"
    fh_dir = tmp_path / "factor_history"
    chip_dir.mkdir(parents=True, exist_ok=True)
    l1_dir.mkdir(parents=True, exist_ok=True)
    fh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(xdp, "require_xtdata", lambda: object())
    monkeypatch.setattr(xdp, "prev_trade_date", lambda *_args, **_kwargs: prev_date)
    monkeypatch.setattr(xdp, "get_instrument_detail", lambda _code: {"PriceTick": 0.01})
    monkeypatch.setattr(xdp, "get_float_shares_detail", lambda *_args, **_kwargs: {"shares": 1_000_000.0, "source": "xtdata_floatvolume", "degraded": False})
    monkeypatch.setattr(xdp, "get_divid_factors", lambda *_args, **_kwargs: pd.DataFrame())

    def _bars(times: list[str]) -> pd.DataFrame:
        n = len(times)
        close = np.linspace(10.0, 10.0 + (n - 1) * 0.1, n)
        return pd.DataFrame(
            {
                "time": times,
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": np.full(n, 1000.0, dtype=np.float64),
                "amount": close * 1000.0,
            }
        )

    def _fake_daily_bars(stock_list, *, end_time: str, count: int, dividend_type: str = "none", lot_size: float = 100.0, volume_in_lots: bool = True) -> pd.DataFrame:
        _ = end_time, count, dividend_type, lot_size, volume_in_lots
        code0 = stock_list[0]
        if code0 == code:
            if int(count) == 2:
                return _bars([prev_date, trade_date])
            if int(count) == 11:
                return _bars([f"202603{i:02d}" for i in range(7, 17)] + [trade_date])
            if int(count) == 61:
                return _bars([f"d{i:02d}" for i in range(61)])
        if code0 in {"000001.SH", "399006.SZ"} and int(count) == 2:
            return _bars([prev_date])
        raise AssertionError(f"unexpected get_daily_bars request: code={code0} count={count}")

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)
    monkeypatch.setattr(xdp, "get_local_tick_data", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(xdp, "get_market_tick_data", lambda *_args, **_kwargs: pd.DataFrame())

    svc = StockChipService(
        config={
            "chip_snapshot_dir": str(chip_dir),
            "l1_snapshot_dir": str(l1_dir),
            "microstructure": {"factor_history_dir": str(fh_dir)},
            "tick_volume_self_check": 0,
            "daily_volume_self_check": 0,
        }
    )

    with pytest.raises(RuntimeError, match="stale daily bar"):
        svc.run_daily(trade_date, codes=[code])


def test_stock_service_flow_raises_when_corp_action_event_present_but_trade_bar_stale(monkeypatch, tmp_path: Path) -> None:
    trade_date = "20260320"
    prev_date = "20260319"
    code = "600201.SH"

    chip_dir = tmp_path / "chip_snapshots"
    l1_dir = tmp_path / "l1_snapshots"
    fh_dir = tmp_path / "factor_history"
    chip_dir.mkdir(parents=True, exist_ok=True)
    l1_dir.mkdir(parents=True, exist_ok=True)
    fh_dir.mkdir(parents=True, exist_ok=True)

    chips = ChipDistribution(etf_code=code, base_price=0.0, bucket_size=0.01)
    chips.ensure_range(0.0, 30.0, padding_buckets=0)
    chips.chips[:] = 0.0
    chips.chips[chips.price_to_index(15.0)] = np.float32(1000.0)
    chips.total_shares = 1_000_000.0
    chips.save(chip_dir / f"{code.replace('.', '_')}_{prev_date}.npz")

    monkeypatch.setattr(xdp, "require_xtdata", lambda: object())
    monkeypatch.setattr(xdp, "prev_trade_date", lambda *_args, **_kwargs: prev_date)
    monkeypatch.setattr(xdp, "get_instrument_detail", lambda _code: {"PriceTick": 0.01})
    monkeypatch.setattr(xdp, "get_float_shares_detail", lambda *_args, **_kwargs: {"shares": 1_000_000.0, "source": "xtdata_floatvolume", "degraded": False})
    monkeypatch.setattr(xdp, "get_divid_factors", lambda *_args, **_kwargs: pd.DataFrame({"time": [trade_date], "dr": [1.1111111111]}))

    def _bars(times: list[str]) -> pd.DataFrame:
        n = len(times)
        close = np.linspace(10.0, 10.0 + (n - 1) * 0.1, n)
        return pd.DataFrame(
            {
                "time": times,
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": np.full(n, 1000.0, dtype=np.float64),
                "amount": close * 1000.0,
            }
        )

    def _fake_daily_bars(stock_list, *, end_time: str, count: int, dividend_type: str = "none", lot_size: float = 100.0, volume_in_lots: bool = True) -> pd.DataFrame:
        _ = end_time, dividend_type, lot_size, volume_in_lots
        code0 = stock_list[0]
        if code0 == code and int(count) == 2:
            return _bars(["20260318", prev_date])
        if code0 == code and int(count) in {11, 61}:
            return _bars([f"d{i:02d}" for i in range(int(count))])
        if code0 in {"000001.SH", "399006.SZ"} and int(count) == 2:
            return _bars([prev_date, trade_date])
        raise AssertionError(f"unexpected get_daily_bars request: code={code0} count={count}")

    monkeypatch.setattr(xdp, "get_daily_bars", _fake_daily_bars)
    monkeypatch.setattr(xdp, "get_local_tick_data", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(xdp, "get_market_tick_data", lambda *_args, **_kwargs: pd.DataFrame())

    svc = StockChipService(
        config={
            "chip_snapshot_dir": str(chip_dir),
            "l1_snapshot_dir": str(l1_dir),
            "microstructure": {"factor_history_dir": str(fh_dir)},
            "tick_volume_self_check": 0,
            "daily_volume_self_check": 0,
        }
    )

    with pytest.raises(RuntimeError, match="corp action"):
        svc.run_daily(trade_date, codes=[code])
