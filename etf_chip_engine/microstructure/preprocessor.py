"""M0 — Snapshot Preprocessor.

V2.1: Data cleaning, deduplication, validity masking, limit-locked detection,
and session filtering.  All downstream modules (M1-M8) consume data AFTER
this step.

Data-flow semantics (V2.1 fourth-round fix):
    Output is the *full* DataFrame with added mask columns (row count unchanged).
    Downstream modules use the masks to restrict computation scope; rows are
    never deleted so that adjacent-snapshot diffs (e.g. in OFI) remain valid.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONTINUOUS_SESSIONS = [
    ("09:30:00", "11:30:00"),   # morning
    ("13:00:00", "14:57:00"),   # afternoon (exclude close auction, non-tunable)
]


# ---------------------------------------------------------------------------
# Individual steps
# ---------------------------------------------------------------------------

def detect_and_fix_volume(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Detect cumulative volume and convert to incremental if needed."""
    out = df.copy()
    vol = out["volume"].astype(float)

    is_monotonic = float((vol.diff().fillna(0) >= 0).mean()) > 0.95
    ratio = float(vol.iloc[-1]) / max(float(vol.iloc[0]), 1.0) if len(vol) > 1 else 1.0
    is_cumulative = is_monotonic and ratio > 10.0

    if is_cumulative:
        out["volume"] = vol.diff().fillna(vol.iloc[0]).clip(lower=0).astype(int)
        if "amount" in out.columns:
            amt = out["amount"].astype(float)
            out["amount"] = amt.diff().fillna(amt.iloc[0]).clip(lower=0)

    return out, {"volume_is_cumulative": bool(is_cumulative)}


def dedup_by_timestamp(df: pd.DataFrame, ts_col: str = "time") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Remove duplicate timestamps, keeping the last occurrence."""
    n_before = len(df)
    if ts_col not in df.columns:
        return df, {"duplicate_ts_count": 0, "duplicate_ts_ratio": 0.0}
    out = df.drop_duplicates(subset=[ts_col], keep="last")
    n_dup = n_before - len(out)
    return out, {
        "duplicate_ts_count": n_dup,
        "duplicate_ts_ratio": n_dup / max(n_before, 1),
    }


def detect_limit_locked(df: pd.DataFrame) -> pd.Series:
    """Identify limit-up/limit-down locked snapshots.

    Limit-up:  ask1 <= 0  or  ask1_vol == 0 (no sell orders)
    Limit-down: bid1 <= 0  or  bid1_vol == 0 (no buy orders)
    """
    ask_locked = (df["ask1"] <= 0) | (df["ask1_vol"] <= 0)
    bid_locked = (df["bid1"] <= 0) | (df["bid1_vol"] <= 0)
    return ask_locked | bid_locked


def build_validity_mask(df: pd.DataFrame) -> pd.Series:
    """Build validity mask for continuous-auction snapshots.

    All conditions must hold:
        bid1 > 0, ask1 > 0, bid1 < ask1,
        bid1_vol > 0, ask1_vol > 0,  (V2.1: tightened from >=0)
        close > 0                     (V2.1: prevents log(1e-8) in RV)
    """
    return (
        (df["bid1"] > 0)
        & (df["ask1"] > 0)
        & (df["bid1"] < df["ask1"])
        & (df["bid1_vol"] > 0)
        & (df["ask1_vol"] > 0)
        & (df["close"] > 0)
    )


def _time_to_seconds(t: Any) -> float:
    """Convert a time value to seconds-since-midnight.

    Handles:
      - int/float timestamp in milliseconds (e.g. XtQuant epoch-ms)
      - HH:MM:SS string
      - pandas Timestamp
    """
    if isinstance(t, (int, float, np.integer, np.floating)):
        ts = int(t)
        # XtQuant often gives epoch-ms; convert to HH:MM:SS of the day.
        if ts > 1_000_000_000_000:  # epoch milliseconds
            dt = pd.Timestamp(ts, unit="ms", tz="Asia/Shanghai")
        elif ts > 1_000_000_000:  # epoch seconds
            dt = pd.Timestamp(ts, unit="s", tz="Asia/Shanghai")
        else:
            # Might already be HHMMSS int
            h, m, s = ts // 10000, (ts // 100) % 100, ts % 100
            return float(h * 3600 + m * 60 + s)
        return float(dt.hour * 3600 + dt.minute * 60 + dt.second)
    if isinstance(t, str):
        parts = t.split(":")
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    if hasattr(t, "hour"):
        return float(t.hour * 3600 + t.minute * 60 + t.second)
    return -1.0


def filter_continuous_auction(df: pd.DataFrame, ts_col: str = "time") -> pd.Series:
    """Return a boolean mask for snapshots within continuous-auction sessions."""
    if ts_col not in df.columns:
        return pd.Series(True, index=df.index)

    sessions_sec = []
    for start_s, end_s in CONTINUOUS_SESSIONS:
        sp = start_s.split(":")
        ep = end_s.split(":")
        s = int(sp[0]) * 3600 + int(sp[1]) * 60 + int(sp[2])
        e = int(ep[0]) * 3600 + int(ep[1]) * 60 + int(ep[2])
        sessions_sec.append((s, e))

    secs = df[ts_col].apply(_time_to_seconds)
    mask = pd.Series(False, index=df.index)
    for s, e in sessions_sec:
        mask = mask | ((secs >= s) & (secs <= e))
    return mask


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class SnapshotPreprocessor:
    """M0 orchestrator — runs all preprocessing steps in the correct order.

    Order (V2.1):
        1.  Volume mode detection & incremental conversion
        1.5 Timestamp de-duplication
        2.  Session time filtering (continuous auction only)
        3.  Limit-locked detection (BEFORE validity mask)
        4.  Validity mask (only applied to non-locked rows)

    Output semantics (V2.1 fourth-round):
        Returns the FULL DataFrame (row count unchanged) with added columns:
        - ``limit_locked_mask``        bool
        - ``valid_continuous_mask``     bool (valid & in continuous session & not locked)
        - ``session_mask``             bool
    """

    def process(
        self,
        snapshots: pd.DataFrame,
        *,
        ts_col: str = "time",
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Run the full M0 pipeline.

        Returns
        -------
        df : pd.DataFrame
            Full DataFrame with mask columns appended.
        quality_meta : dict
            Diagnostic metadata for M9 diagnostics assembly.
        """
        if snapshots is None or snapshots.empty:
            return snapshots, {
                "n_total": 0,
                "n_valid": 0,
                "valid_ratio": 0.0,
                "volume_is_cumulative": False,
                "duplicate_ts_count": 0,
                "duplicate_ts_ratio": 0.0,
                "limit_locked_ratio": 0.0,
            }

        quality: dict[str, Any] = {}

        # Step 1: volume mode
        df, vol_meta = detect_and_fix_volume(snapshots)
        quality.update(vol_meta)

        # Step 1.5: dedup
        df, dup_meta = dedup_by_timestamp(df, ts_col=ts_col)
        quality.update(dup_meta)
        # Reset index after potential row removal from dedup
        df = df.reset_index(drop=True)

        # Step 2: session mask
        session_mask = filter_continuous_auction(df, ts_col=ts_col)
        df["session_mask"] = session_mask

        # Step 3: limit-locked (BEFORE validity mask)
        limit_locked = detect_limit_locked(df)
        df["limit_locked_mask"] = limit_locked
        quality["limit_locked_ratio"] = float(limit_locked.mean()) if len(limit_locked) else 0.0

        # Step 4: validity mask (applied to non-locked, in-session rows only)
        raw_valid = build_validity_mask(df)
        valid_continuous = session_mask & raw_valid & ~limit_locked
        df["valid_continuous_mask"] = valid_continuous

        n_total = len(df)
        n_valid = int(valid_continuous.sum())
        quality["n_total"] = n_total
        quality["n_valid"] = n_valid
        quality["valid_ratio"] = n_valid / max(n_total, 1)

        return df, quality
