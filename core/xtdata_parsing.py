from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def xtdata_field_dict_to_df(raw: Any, *, stock_code: str, fields: list[str], time_field: str = "time") -> pd.DataFrame | None:
    if not isinstance(raw, dict) or not raw:
        return None
    if not all(isinstance(v, pd.DataFrame) for v in raw.values()):
        return None

    any_df = next(iter(raw.values()))
    if any_df.empty:
        return None

    def _row(df: pd.DataFrame) -> pd.Series:
        if stock_code in df.index:
            return df.loc[stock_code]
        return df.iloc[0]

    time_df = raw.get(time_field)
    idx = _row(any_df).index
    if isinstance(time_df, pd.DataFrame) and not time_df.empty:
        times = pd.to_numeric(_row(time_df), errors="coerce").fillna(0).to_numpy(dtype="int64")
    else:
        times = pd.to_numeric(pd.Index(idx), errors="coerce").fillna(0).to_numpy(dtype="int64")

    out = pd.DataFrame({"time": times.astype(np.float64, copy=False)})
    for f in fields:
        df = raw.get(f)
        if not isinstance(df, pd.DataFrame) or df.empty:
            out[f] = np.zeros(len(out), dtype=np.float64)
            continue
        arr = pd.to_numeric(_row(df), errors="coerce").fillna(0).to_numpy(dtype=np.float64)
        n = min(len(out), len(arr))
        if n < len(out):
            pad = np.zeros(len(out), dtype=np.float64)
            pad[:n] = arr[:n]
            out[f] = pad
        else:
            out[f] = arr[: len(out)]
    return out.reset_index(drop=True)

