from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def ticks_to_snapshots(
    ticks: Any,
    *,
    lot_size: float = 100.0,
    volume_in_lots: bool = True,
) -> pd.DataFrame:
    """Convert raw XtQuant tick data to an incremental snapshot DataFrame.

    Notes
    -----
    - XtQuant tick ``amount``/``volume`` are usually cumulative intraday values.
    - This adapter converts them into per-snapshot increments via diff.
    - When ``volume_in_lots=True``, volume increments are multiplied by ``lot_size``.
    """
    base_cols = ["time", "open", "high", "low", "close", "volume", "amount", "bid1", "bid1_vol", "ask1", "ask1_vol"]

    lot = float(lot_size)
    vol_mult = lot if bool(volume_in_lots) else 1.0

    if isinstance(ticks, pd.DataFrame):
        if ticks is None or ticks.empty:
            return pd.DataFrame(columns=base_cols)
        cols = {str(c) for c in ticks.columns}

        def _level1(x: Any) -> np.ndarray:
            arr = np.asarray(x.to_numpy(copy=False) if hasattr(x, "to_numpy") else x, dtype=object)
            out = np.zeros(arr.shape[0], dtype=np.float64)
            for i, v in enumerate(arr):
                try:
                    if v is None:
                        out[i] = 0.0
                    elif isinstance(v, (list, tuple, np.ndarray)):
                        out[i] = float(v[0]) if len(v) > 0 else 0.0
                    else:
                        out[i] = float(v)
                except Exception:
                    out[i] = 0.0
            return out

        if "lastPrice" in cols:
            close = pd.to_numeric(ticks["lastPrice"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
        elif "close" in cols:
            close = pd.to_numeric(ticks["close"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
        else:
            close = np.zeros(len(ticks), dtype=np.float64)

        tcol: Any
        if "time" in cols:
            tcol = ticks["time"]
        elif isinstance(ticks.index, pd.DatetimeIndex):
            tcol = ticks.index
        else:
            tcol = ticks.index if ticks.index is not None and len(ticks.index) == len(ticks) else None

        if tcol is None:
            ts = np.zeros(len(ticks), dtype=np.float64)
        else:
            if isinstance(tcol, pd.DatetimeIndex) or (
                hasattr(tcol, "dtype") and np.issubdtype(getattr(tcol, "dtype", object), np.datetime64)
            ):
                if isinstance(tcol, pd.DatetimeIndex):
                    ns = tcol.view("int64")
                else:
                    ns = pd.to_datetime(tcol, errors="coerce").to_numpy(dtype="datetime64[ns]").view("int64")
                ts = (ns // 1_000_000).astype(np.float64)
                nat = np.iinfo(np.int64).min
                ts[ns == nat] = 0.0
            else:
                ts = pd.to_numeric(pd.Series(tcol), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)

        high_raw = (
            pd.to_numeric(ticks["high"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64) if "high" in cols else close.copy()
        )
        low_raw = (
            pd.to_numeric(ticks["low"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64) if "low" in cols else close.copy()
        )
        amount_cum = (
            pd.to_numeric(ticks["amount"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64) if "amount" in cols else np.zeros(len(ticks), dtype=np.float64)
        )
        volume_cum = (
            pd.to_numeric(ticks["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64) if "volume" in cols else np.zeros(len(ticks), dtype=np.float64)
        )

        bid1 = None
        for k in ("bidPrice1", "bid1", "bidPrice"):
            if k in cols:
                bid1 = _level1(ticks[k])
                break
        if bid1 is None:
            bid1 = np.zeros(len(ticks), dtype=np.float64)

        bid1_vol = None
        for k in ("bidVol1", "bid1_vol", "bidVol"):
            if k in cols:
                bid1_vol = _level1(ticks[k])
                break
        if bid1_vol is None:
            bid1_vol = np.zeros(len(ticks), dtype=np.float64)

        ask1 = None
        for k in ("askPrice1", "ask1", "askPrice"):
            if k in cols:
                ask1 = _level1(ticks[k])
                break
        if ask1 is None:
            ask1 = np.zeros(len(ticks), dtype=np.float64)

        ask1_vol = None
        for k in ("askVol1", "ask1_vol", "askVol"):
            if k in cols:
                ask1_vol = _level1(ticks[k])
                break
        if ask1_vol is None:
            ask1_vol = np.zeros(len(ticks), dtype=np.float64)

        open_ = np.roll(close, 1)
        if open_.size:
            open_[0] = close[0]

        # XtQuant tick amount/volume are typically intraday cumulative values.
        # Use a 0 baseline so the first snapshot keeps the first cum increment
        # (e.g., call auction / first trade).
        amount_delta = np.diff(amount_cum, prepend=0.0)
        volume_delta = np.diff(volume_cum, prepend=0.0)
        amount_delta = np.maximum(amount_delta, 0.0)
        volume_delta = np.maximum(volume_delta, 0.0) * vol_mult

        high_monotone = np.all(np.diff(high_raw) >= -1e-12) if high_raw.size >= 2 else False
        low_monotone = np.all(np.diff(low_raw) <= 1e-12) if low_raw.size >= 2 else False
        if high_monotone and low_monotone:
            prev_close = np.roll(close, 1)
            prev_close[0] = close[0]
            high = np.maximum(close, prev_close)
            low = np.minimum(close, prev_close)
        else:
            high = high_raw.copy()
            low = low_raw.copy()
        swap = high < low
        if swap.any():
            tmp = high[swap].copy()
            high[swap] = low[swap]
            low[swap] = tmp

        out = {
            "time": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume_delta,
            "amount": amount_delta,
            "bid1": bid1,
            "bid1_vol": bid1_vol,
            "ask1": ask1,
            "ask1_vol": ask1_vol,
        }
        return pd.DataFrame(out)

    if ticks is None:
        return pd.DataFrame(columns=base_cols)
    arr = np.asarray(ticks)
    if arr.shape[0] <= 0:
        return pd.DataFrame(columns=base_cols)
    names = getattr(arr.dtype, "names", None)
    if not names:
        return pd.DataFrame(columns=base_cols)

    def _get(name: str) -> np.ndarray:
        if name not in names:
            return np.zeros(arr.shape[0], dtype=np.float64)
        x = arr[name]
        if x.dtype.kind in ("U", "S", "O"):
            return pd.to_numeric(pd.Series(x), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
        return x.astype(np.float64, copy=False)

    ts = _get("time")
    close = _get("lastPrice")

    open_ = np.roll(close, 1)
    if open_.size:
        open_[0] = close[0]
    high_raw = _get("high")
    low_raw = _get("low")
    amount_cum = _get("amount")
    volume_cum = _get("volume")
    bid1 = _get("bidPrice1") if "bidPrice1" in names else _get("bid1")
    bid1_vol = _get("bidVol1") if "bidVol1" in names else _get("bid1_vol")
    ask1 = _get("askPrice1") if "askPrice1" in names else _get("ask1")
    ask1_vol = _get("askVol1") if "askVol1" in names else _get("ask1_vol")

    amount_delta = np.diff(amount_cum, prepend=0.0)
    volume_delta = np.diff(volume_cum, prepend=0.0)
    amount_delta = np.maximum(amount_delta, 0.0)
    volume_delta = np.maximum(volume_delta, 0.0) * vol_mult

    high_monotone = np.all(np.diff(high_raw) >= -1e-12) if high_raw.size >= 2 else False
    low_monotone = np.all(np.diff(low_raw) <= 1e-12) if low_raw.size >= 2 else False

    if high_monotone and low_monotone:
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        high = np.maximum(close, prev_close)
        low = np.minimum(close, prev_close)
    else:
        high = high_raw.copy()
        low = low_raw.copy()

    swap = high < low
    if swap.any():
        tmp = high[swap].copy()
        high[swap] = low[swap]
        low[swap] = tmp

    out = {
        "time": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume_delta,
        "amount": amount_delta,
        "bid1": bid1,
        "bid1_vol": bid1_vol,
        "ask1": ask1,
        "ask1_vol": ask1_vol,
    }
    return pd.DataFrame(out)
