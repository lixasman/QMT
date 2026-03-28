from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re
from pathlib import Path
import time
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.warn_utils import info_once, warn_once
from etf_chip_engine.engine import ETFChipEngine
from etf_chip_engine.microstructure.factor_engine import MicrostructureEngine
from etf_chip_engine.modules.diffusion import apply_brownian_diffusion
from etf_chip_engine.microstructure.feature_pipeline import FeaturePipeline

from stock_chip_engine.config import CONFIG
from stock_chip_engine.data.tick_adapter import ticks_to_snapshots
from stock_chip_engine.data import xtdata_provider as xdp
from stock_chip_engine.modules.corp_actions import (
    boundary_adjustment_factor,
    is_boundary_adjustment_significant,
    rescale_chip_distribution,
)


@dataclass(frozen=True)
class DailyRunResult:
    trade_date: str
    code: str
    indicators: dict[str, Any]
    state_path: str


_RUNTIME_WARNED: set[str] = set()
_SH_INDEX_CODE = "000001.SH"
_CY_INDEX_CODE = "399006.SZ"


def _warn_runtime_once(key: str, msg: str) -> None:
    warn_once(key, msg, logger_name=__name__)
    if key in _RUNTIME_WARNED:
        return
    _RUNTIME_WARNED.add(key)
    print(f"[WARN] {msg}", flush=True)


def _as_bool(v: Any, *, default: bool = False) -> bool:
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return bool(v)
    s = str(v).strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "f", "no", "n", "off"}:
        return False
    return bool(default)


def _safe_pct_change(value: Any, base: Any) -> float:
    try:
        value_f = float(value)
        base_f = float(base)
    except Exception:
        return float("nan")
    if not (np.isfinite(value_f) and np.isfinite(base_f) and abs(base_f) > 0):
        return float("nan")
    return ((value_f - base_f) / base_f) * 100.0


def _compute_rsi(close: pd.Series, *, period: int) -> float:
    if close is None or len(close) < int(period) + 1:
        return float("nan")
    delta = pd.to_numeric(close, errors="coerce").diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = float(gain.rolling(int(period), min_periods=int(period)).mean().iloc[-1])
    avg_loss = float(loss.rolling(int(period), min_periods=int(period)).mean().iloc[-1])
    if not np.isfinite(avg_gain) or not np.isfinite(avg_loss):
        return float("nan")
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    if avg_gain <= 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _trade_date_from_any(v: Any) -> str:
    m = re.search(r"(\d{8})", str(v or ""))
    return m.group(1) if m else ""


def _daily_backfill_start(*, trade_date: str, min_bars: int) -> str:
    try:
        td = datetime.strptime(str(trade_date), "%Y%m%d")
    except Exception:
        return str(trade_date)
    lookback_days = max(30, int(max(int(min_bars), 1)) * 3)
    return (td - timedelta(days=lookback_days)).strftime("%Y%m%d")


def _latest_daily_before_trade_date(daily_df: pd.DataFrame, *, trade_date: str) -> str:
    if daily_df is None or daily_df.empty or "time" not in daily_df.columns:
        return ""
    trade_date_s = str(trade_date)
    out = [
        td
        for td in (_trade_date_from_any(v) for v in daily_df["time"].tolist())
        if td and td != trade_date_s
    ]
    return out[-1] if out else ""


def _build_trade_date_bar_from_snapshots(snapshots: pd.DataFrame, *, trade_date: str) -> pd.DataFrame:
    base_cols = ["time", "open", "high", "low", "close", "volume", "amount"]
    if snapshots is None or snapshots.empty:
        return pd.DataFrame(columns=base_cols)
    required = {"close", "high", "low", "volume", "amount"}
    if not required.issubset(snapshots.columns):
        return pd.DataFrame(columns=base_cols)

    close = pd.to_numeric(snapshots["close"], errors="coerce")
    valid = close.gt(0)
    if int(valid.sum()) <= 0:
        return pd.DataFrame(columns=base_cols)

    df = snapshots.loc[valid].reset_index(drop=True)
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).clip(lower=0.0)
    amount = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0).clip(lower=0.0)

    high_v = float(pd.concat([high, close], axis=1).max(axis=1).max())
    low_v = float(pd.concat([low, close], axis=1).min(axis=1).min())
    row = {
        "time": str(trade_date),
        "open": float(close.iloc[0]),
        "high": high_v,
        "low": low_v,
        "close": float(close.iloc[-1]),
        "volume": float(volume.sum()),
        "amount": float(amount.sum()),
    }
    return pd.DataFrame([row], columns=base_cols)


def _ensure_trade_date_daily_bar(daily_df: pd.DataFrame, *, trade_date: str, snapshots: pd.DataFrame) -> pd.DataFrame:
    base = daily_df.copy() if daily_df is not None and not daily_df.empty else pd.DataFrame()
    last_td = _trade_date_from_any(base["time"].iloc[-1]) if (not base.empty and "time" in base.columns) else ""
    if last_td == str(trade_date):
        return base.reset_index(drop=True)

    trade_bar = _build_trade_date_bar_from_snapshots(snapshots, trade_date=str(trade_date))
    if trade_bar.empty:
        return base.reset_index(drop=True)
    if base.empty:
        return trade_bar.reset_index(drop=True)

    if "time" in base.columns:
        keep_mask = base["time"].map(_trade_date_from_any) != str(trade_date)
        base = base.loc[keep_mask].reset_index(drop=True)
    return pd.concat([base, trade_bar], ignore_index=True)


def _history_before_trade_date(daily_df: pd.DataFrame, *, trade_date: str, keep_count: int) -> pd.DataFrame:
    base = daily_df.copy() if daily_df is not None and not daily_df.empty else pd.DataFrame()
    if base.empty:
        return base.reset_index(drop=True)
    if "time" in base.columns:
        keep_mask = base["time"].map(_trade_date_from_any) != str(trade_date)
        base = base.loc[keep_mask].reset_index(drop=True)
    keep_n = max(int(keep_count), 0)
    if keep_n > 0 and len(base) > keep_n:
        base = base.tail(keep_n).reset_index(drop=True)
    return base.reset_index(drop=True)


def _load_daily_history_before_trade_date(
    *,
    code: str,
    trade_date: str,
    count: int,
    lot_size: float,
    volume_in_lots: bool,
    expected_last_trade_date: str = "",
    context: str,
) -> pd.DataFrame:
    keep_n = max(int(count), 0)
    query_count = keep_n + 1 if keep_n > 0 else 1

    def _query_history() -> pd.DataFrame:
        daily_df = xdp.get_daily_bars(
            [code],
            end_time=trade_date,
            count=query_count,
            dividend_type="none",
            lot_size=lot_size,
            volume_in_lots=volume_in_lots,
        )
        return _history_before_trade_date(daily_df, trade_date=trade_date, keep_count=keep_n)

    hist = _query_history()
    expected_td = str(expected_last_trade_date or "").strip()
    if expected_td:
        last_td = _trade_date_from_any(hist["time"].iloc[-1]) if (not hist.empty and "time" in hist.columns) else ""
        if last_td != expected_td:
            start_td = _daily_backfill_start(trade_date=trade_date, min_bars=query_count)
            xdp.download_daily_data([code], start_time=start_td, end_time=trade_date)
            hist = _query_history()
            last_td = _trade_date_from_any(hist["time"].iloc[-1]) if (not hist.empty and "time" in hist.columns) else ""
            if last_td != expected_td:
                raise RuntimeError(
                    f"stale daily history: context={context} trade_date={trade_date}"
                    f" expected_last={expected_td} last_daily={last_td or 'missing'}"
                )
    return hist


def _load_tick_snapshots_for_trade_date(
    *,
    code: str,
    trade_date: str,
    lot_size: float,
    volume_in_lots: bool,
) -> pd.DataFrame:
    ticks = xdp.get_local_tick_data(code, trade_date)
    if ticks is None or (isinstance(ticks, pd.DataFrame) and ticks.empty):
        ticks = xdp.get_market_tick_data(code, trade_date, count=-1)
    snapshots = ticks_to_snapshots(ticks, lot_size=lot_size, volume_in_lots=volume_in_lots)
    return snapshots if snapshots is not None else pd.DataFrame()


def _load_trade_date_daily_bars(
    *,
    code: str,
    trade_date: str,
    count: int,
    lot_size: float,
    volume_in_lots: bool,
    expected_prev_trade_date: str,
    snapshots: pd.DataFrame,
    context: str,
) -> pd.DataFrame:
    query_n = max(int(count), 1)

    def _query_effective() -> pd.DataFrame:
        daily_df = xdp.get_daily_bars(
            [code],
            end_time=trade_date,
            count=query_n,
            dividend_type="none",
            lot_size=lot_size,
            volume_in_lots=volume_in_lots,
        )
        effective = _ensure_trade_date_daily_bar(daily_df, trade_date=trade_date, snapshots=snapshots)
        _assert_trade_date_bar_fresh(effective, trade_date=trade_date, context=context)
        return effective.reset_index(drop=True)

    effective = _query_effective()
    expected_td = str(expected_prev_trade_date or "").strip()
    if expected_td:
        last_td = _latest_daily_before_trade_date(effective, trade_date=trade_date)
        if last_td != expected_td:
            start_td = _daily_backfill_start(trade_date=trade_date, min_bars=query_n)
            xdp.download_daily_data([code], start_time=start_td, end_time=trade_date)
            effective = _query_effective()
            last_td = _latest_daily_before_trade_date(effective, trade_date=trade_date)
            if last_td != expected_td:
                raise RuntimeError(
                    f"stale daily history: context={context} trade_date={trade_date}"
                    f" expected_last={expected_td} last_daily={last_td or 'missing'}"
                )
    return effective


def _prefetch_daily_history(*, codes: list[str], trade_date: str, min_bars: int) -> None:
    if not codes:
        return
    start_td = _daily_backfill_start(trade_date=trade_date, min_bars=min_bars)
    xdp.download_daily_data(codes, start_time=start_td, end_time=trade_date)


def _compute_daily_context_features(daily_df: pd.DataFrame) -> dict[str, float]:
    out = {
        "change_3d": float("nan"),
        "change_5d": float("nan"),
        "open_pct": float("nan"),
        "close_pct": float("nan"),
        "high_pct": float("nan"),
        "low_pct": float("nan"),
        "ma5_pos": float("nan"),
        "ma10_pos": float("nan"),
        "rsi_5": float("nan"),
        "vol_ratio": float("nan"),
    }
    if daily_df is None or daily_df.empty:
        return out

    df = daily_df.tail(11).reset_index(drop=True)
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return out

    close = pd.to_numeric(df["close"], errors="coerce")
    open_ = pd.to_numeric(df["open"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")
    if len(close) < 2:
        return out

    prev_close = float(close.iloc[-2])
    curr_close = float(close.iloc[-1])
    out["open_pct"] = _safe_pct_change(float(open_.iloc[-1]), prev_close)
    out["close_pct"] = _safe_pct_change(curr_close, prev_close)
    out["high_pct"] = _safe_pct_change(float(high.iloc[-1]), prev_close)
    out["low_pct"] = _safe_pct_change(float(low.iloc[-1]), prev_close)

    if len(close) >= 4:
        out["change_3d"] = _safe_pct_change(curr_close, float(close.iloc[-4]))
    if len(close) >= 6:
        out["change_5d"] = _safe_pct_change(curr_close, float(close.iloc[-6]))

    if len(close) >= 5:
        ma5 = float(close.tail(5).mean())
        if np.isfinite(ma5) and np.isfinite(prev_close) and abs(prev_close) > 0:
            out["ma5_pos"] = ((curr_close - ma5) / prev_close) * 100.0
    if len(close) >= 10:
        ma10 = float(close.tail(10).mean())
        if np.isfinite(ma10) and np.isfinite(prev_close) and abs(prev_close) > 0:
            out["ma10_pos"] = ((curr_close - ma10) / prev_close) * 100.0

    out["rsi_5"] = _compute_rsi(close, period=5)
    if len(volume) >= 6:
        avg_prev_5 = float(volume.iloc[-6:-1].mean())
        if np.isfinite(avg_prev_5) and avg_prev_5 > 0:
            out["vol_ratio"] = float(volume.iloc[-1]) / avg_prev_5
    return out


def _iter_divid_factor_rows(raw: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    if raw is None:
        return out
    if hasattr(raw, "iterrows"):
        try:
            for idx, row in raw.iterrows():  # type: ignore[attr-defined]
                rec = dict(row.to_dict()) if hasattr(row, "to_dict") else dict(row)
                rec["_index"] = idx
                out.append(rec)
            return out
        except Exception:
            return out
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(dict(item))
        return out
    if isinstance(raw, dict):
        out.append(dict(raw))
    return out


def _has_trade_date_divid_event(*, code: str, trade_date: str) -> bool:
    raw = xdp.get_divid_factors(code, start_time=trade_date, end_time=trade_date)
    for rec in _iter_divid_factor_rows(raw):
        day = _trade_date_from_any(rec.get("_index") or rec.get("date") or rec.get("time"))
        if str(day) == str(trade_date):
            return True
    return False


def _assert_trade_date_bar_fresh(daily_df: pd.DataFrame, *, trade_date: str, context: str) -> None:
    last_td = _trade_date_from_any(daily_df["time"].iloc[-1]) if (daily_df is not None and not daily_df.empty and "time" in daily_df.columns) else ""
    if last_td != str(trade_date):
        raise RuntimeError(
            f"stale daily bar: context={context} trade_date={trade_date} last_daily={last_td or 'missing'}"
        )


def _compute_index_change(
    *,
    code: str,
    trade_date: str,
    lot_size: float,
    volume_in_lots: bool,
    tick_volume_in_lots: bool,
) -> float:
    try:
        daily_df = xdp.get_daily_bars(
            [code],
            end_time=trade_date,
            count=2,
            dividend_type="none",
            lot_size=lot_size,
            volume_in_lots=volume_in_lots,
        )
    except Exception as e:
        raise RuntimeError(f"benchmark daily query failed: code={code} trade_date={trade_date} err={repr(e)}") from e

    last_td = _trade_date_from_any(daily_df["time"].iloc[-1]) if (daily_df is not None and not daily_df.empty and "time" in daily_df.columns) else ""
    if last_td != str(trade_date):
        snapshots = _load_tick_snapshots_for_trade_date(
            code=code,
            trade_date=trade_date,
            lot_size=lot_size,
            volume_in_lots=tick_volume_in_lots,
        )
        daily_df = _ensure_trade_date_daily_bar(daily_df, trade_date=trade_date, snapshots=snapshots)

    _assert_trade_date_bar_fresh(daily_df, trade_date=trade_date, context=f"benchmark:{code}")
    if daily_df is None or len(daily_df) < 2 or "close" not in daily_df.columns:
        raise RuntimeError(f"insufficient daily bars: context=benchmark:{code} trade_date={trade_date}")

    close = pd.to_numeric(daily_df["close"], errors="coerce")
    if len(close) < 2:
        raise RuntimeError(f"insufficient close history: context=benchmark:{code} trade_date={trade_date}")
    return _safe_pct_change(float(close.iloc[-1]), float(close.iloc[-2]))


def _normalize_codes(codes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in list(codes or []):
        s = xdp.normalize_stock_code(str(c))
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _maybe_check_tick_volume_unit(*, snapshots: pd.DataFrame, code: str, trade_date: str, cfg: dict[str, object]) -> None:
    if snapshots is None or snapshots.empty:
        return
    if not _as_bool(cfg.get("tick_volume_self_check", 1), default=True):
        return
    n = int(cfg.get("tick_volume_self_check_sample", 200))
    low = float(cfg.get("tick_volume_self_check_vwap_ratio_low", 0.2))
    high = float(cfg.get("tick_volume_self_check_vwap_ratio_high", 5.0))

    df = snapshots.tail(max(n, 10))
    if "amount" not in df.columns or "volume" not in df.columns or "close" not in df.columns:
        return
    vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    amt = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    close = pd.to_numeric(df["close"], errors="coerce").fillna(0.0)
    mask = (vol > 0) & (amt > 0) & (close > 0)
    if int(mask.sum()) < 5:
        return
    vwap = (amt[mask] / vol[mask]).to_numpy(dtype=np.float64)
    px = close[mask].to_numpy(dtype=np.float64)
    med_vwap = float(np.nanmedian(vwap)) if vwap.size else float("nan")
    med_px = float(np.nanmedian(px)) if px.size else float("nan")
    if not (np.isfinite(med_vwap) and np.isfinite(med_px) and med_px > 0):
        return
    ratio = med_vwap / med_px
    if ratio < low or ratio > high:
        _warn_runtime_once(
            f"stock_tick_volume_unit_suspicious:{trade_date}:{code}",
            (
                "Stock tick volume unit suspicious: implied VWAP/close ratio out of range. "
                f"code={code} date={trade_date} ratio={ratio:.4f} "
                "If tick volume is already in shares (not lots), set config tick_volume_in_lots=0."
            ),
        )


def _maybe_check_daily_volume_unit(*, daily_df: pd.DataFrame, code: str, trade_date: str, cfg: dict[str, object]) -> bool:
    """Return False if daily bars volume unit looks inconsistent (VWAP/close ratio)."""
    if daily_df is None or daily_df.empty:
        return True
    if not _as_bool(cfg.get("daily_volume_self_check", 1), default=True):
        return True

    # Use a smaller sample for daily bars; values are per-day.
    n = int(cfg.get("daily_volume_self_check_sample", 30))
    low = float(cfg.get("tick_volume_self_check_vwap_ratio_low", 0.2))
    high = float(cfg.get("tick_volume_self_check_vwap_ratio_high", 5.0))

    df = daily_df.tail(max(n, 5))
    if "amount" not in df.columns or "volume" not in df.columns or "close" not in df.columns:
        return True

    vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    amt = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    close = pd.to_numeric(df["close"], errors="coerce").fillna(0.0)
    mask = (vol > 0) & (amt > 0) & (close > 0)
    if int(mask.sum()) < 3:
        return True

    vwap = (amt[mask] / vol[mask]).to_numpy(dtype=np.float64)
    px = close[mask].to_numpy(dtype=np.float64)
    med_vwap = float(np.nanmedian(vwap)) if vwap.size else float("nan")
    med_px = float(np.nanmedian(px)) if px.size else float("nan")
    if not (np.isfinite(med_vwap) and np.isfinite(med_px) and med_px > 0):
        return True

    ratio = med_vwap / med_px
    if ratio < low or ratio > high:
        _warn_runtime_once(
            f"stock_daily_volume_unit_suspicious:{trade_date}:{code}",
            (
                "Stock daily volume unit suspicious: implied VWAP/close ratio out of range. "
                f"code={code} date={trade_date} ratio={ratio:.4f} "
                "If daily volume is already in shares (not lots), set config daily_volume_in_lots=0. "
                "ADV60 will be disabled for VPIN."
            ),
        )
        return False
    return True


def _build_skew_premium_rate(
    snapshots: pd.DataFrame,
    *,
    lot_size: float,
    min_cum_vol_ratio: float,
    min_cum_vol_lots: float,
) -> np.ndarray:
    if snapshots is None or snapshots.empty:
        return np.zeros(0, dtype=np.float64)
    if "close" not in snapshots.columns or "amount" not in snapshots.columns or "volume" not in snapshots.columns:
        return np.zeros(len(snapshots), dtype=np.float64)

    close = pd.to_numeric(snapshots["close"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    amt = pd.to_numeric(snapshots["amount"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    vol = pd.to_numeric(snapshots["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)

    cum_amt = np.cumsum(np.maximum(amt, 0.0))
    cum_vol = np.cumsum(np.maximum(vol, 0.0))
    total_vol = float(cum_vol[-1]) if cum_vol.size else 0.0

    out = np.zeros_like(close, dtype=np.float64)
    if total_vol <= 0:
        return out

    cum_vwap = np.divide(cum_amt, cum_vol, out=np.zeros_like(cum_amt), where=cum_vol > 0)
    valid = cum_vwap > 0
    out[valid] = (close[valid] - cum_vwap[valid]) / cum_vwap[valid]

    thr_ratio = float(max(float(min_cum_vol_ratio), 0.0))
    thr_lots = float(max(float(min_cum_vol_lots), 0.0))
    thr_shares = thr_lots * float(lot_size)
    thr = max(thr_ratio * total_vol, thr_shares)
    if thr > 0:
        out[cum_vol < thr] = 0.0
    return out


def _compute_adv_60(
    *,
    code: str,
    trade_date: str,
    lot_size: float,
    volume_in_lots: bool,
    cfg: dict[str, object],
    prev_trade_date: str,
) -> Optional[float]:
    try:
        hist = _load_daily_history_before_trade_date(
            code=code,
            trade_date=trade_date,
            count=60,
            lot_size=lot_size,
            volume_in_lots=volume_in_lots,
            expected_last_trade_date=str(prev_trade_date or ""),
            context=f"symbol:{code}:adv60",
        )
    except RuntimeError as e:
        _warn_runtime_once(
            f"stock_adv60_history_stale:{trade_date}:{code}",
            f"Stock: ADV60 daily history stale, fallback disabled. code={code} date={trade_date} err={e}",
        )
        return None
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    # If daily volume unit looks wrong, disable ADV60 for VPIN (fallback to intraday bucket sizing).
    if not _maybe_check_daily_volume_unit(daily_df=hist, code=code, trade_date=trade_date, cfg=cfg):
        return None
    if hist is None or hist.empty or "volume" not in hist.columns:
        return None
    v = pd.to_numeric(hist["volume"], errors="coerce").dropna()
    if v.empty:
        return None
    adv = float(v.mean())
    return adv if np.isfinite(adv) and adv > 0 else None


def _corp_action_factor_for_trade_date(
    *,
    code: str,
    trade_date: str,
    tick_size: float,
    lot_size: float,
    volume_in_lots: bool,
) -> tuple[float, bool, str]:
    """Return (factor, should_apply, reason)."""
    if not _has_trade_date_divid_event(code=code, trade_date=trade_date):
        return 1.0, False, ""

    try:
        bars_none = xdp.get_daily_bars(
            [code],
            end_time=trade_date,
            count=2,
            dividend_type="none",
            lot_size=lot_size,
            volume_in_lots=volume_in_lots,
        )
        bars_front = xdp.get_daily_bars(
            [code],
            end_time=trade_date,
            count=2,
            dividend_type="front",
            lot_size=lot_size,
            volume_in_lots=volume_in_lots,
        )
    except Exception as e:
        return 1.0, False, f"corp_action_daily_bars_failed:{repr(e)}"

    if bars_none is None or bars_front is None or len(bars_none) < 2 or len(bars_front) < 2:
        return 1.0, False, "corp_action_insufficient_bars"

    last_none_td = _trade_date_from_any(bars_none["time"].iloc[-1]) if "time" in bars_none.columns else ""
    last_front_td = _trade_date_from_any(bars_front["time"].iloc[-1]) if "time" in bars_front.columns else ""
    if last_none_td != str(trade_date) or last_front_td != str(trade_date):
        return 1.0, False, f"corp_action_trade_bar_stale:none={last_none_td or 'missing'} front={last_front_td or 'missing'}"

    try:
        close_none_prev = float(bars_none["close"].iloc[-2])
        close_front_prev = float(bars_front["close"].iloc[-2])
    except Exception:
        return 1.0, False, "corp_action_close_parse_failed"

    factor = boundary_adjustment_factor(close_none_prev=close_none_prev, close_front_prev=close_front_prev)
    if not np.isfinite(factor) or factor <= 0:
        return float(factor), False, "corp_action_factor_invalid"

    should_apply = is_boundary_adjustment_significant(
        factor=factor,
        close_none_prev=close_none_prev,
        tick_size=tick_size,
    )
    return float(factor), bool(should_apply), ""


class StockChipService:
    def __init__(self, config: Optional[dict[str, object]] = None):
        cfg = dict(CONFIG)
        if config:
            # Shallow merge; nested microstructure dict is merged separately.
            ms_cfg = dict(cfg.get("microstructure", {})) if isinstance(cfg.get("microstructure"), dict) else {}
            user_ms = config.get("microstructure") if isinstance(config.get("microstructure"), dict) else None
            if user_ms:
                ms_cfg.update({k: user_ms[k] for k in user_ms})
            cfg.update({k: config[k] for k in config if k != "microstructure"})
            cfg["microstructure"] = ms_cfg

        self.config: dict[str, object] = cfg

        chip_dir = Path(str(self.config.get("chip_snapshot_dir")))
        chip_dir.mkdir(parents=True, exist_ok=True)
        l1_dir = Path(str(self.config.get("l1_snapshot_dir")))
        l1_dir.mkdir(parents=True, exist_ok=True)

    def run_daily(
        self,
        trade_date: str,
        *,
        codes: list[str],
        limit: Optional[int] = None,
        force_download: bool = False,
    ) -> pd.DataFrame:
        xdp.require_xtdata()

        td = str(trade_date).strip()
        if not td or not td.isdigit() or len(td) != 8:
            raise ValueError(f"invalid trade_date: {trade_date}")

        lst = _normalize_codes(list(codes or []))
        if limit is not None:
            lst = lst[: int(limit)]
        if not lst:
            return pd.DataFrame()

        daily_prefetch_bars = max(int(self.config.get("cold_start_lookback", 60)), 61)

        try:
            _prefetch_daily_history(codes=lst, trade_date=td, min_bars=daily_prefetch_bars)
        except Exception as e:
            _warn_runtime_once(
                f"stock_daily_predownload_failed:{td}",
                f"Stock: 1d 预下载失败，继续使用现有缓存: trade_date={td} codes={len(lst)} err={repr(e)}",
            )

        # Best-effort predownload for small pools (kept simple: no state cache).
        # This helps warm local cache before per-code get_local_tick_data calls.
        try:
            _ = xdp.download_tick_data(lst, td)
        except Exception:
            pass

        prev_date = xdp.prev_trade_date(td)
        if not str(prev_date).strip():
            _warn_runtime_once(
                f"stock_prev_trade_date_unavailable:{td}",
                f"Stock: 鏃犳硶瑙ｆ瀽鍓嶄竴浜ゆ槗鏃ワ紝褰撳墠鎵规灏嗘寜鍐峰惎鍔ㄥ鐞嗐€倀rade_date={td}",
            )

        chip_dir = Path(str(self.config.get("chip_snapshot_dir")))
        l1_root = Path(str(self.config.get("l1_snapshot_dir"))) / td
        l1_root.mkdir(parents=True, exist_ok=True)

        parquet_ok = True
        try:
            import pyarrow  # type: ignore  # noqa: F401
        except Exception:
            try:
                import fastparquet  # type: ignore  # noqa: F401
            except Exception:
                parquet_ok = False

        lot_size = float(self.config.get("lot_size", 100.0))
        tick_volume_in_lots = _as_bool(self.config.get("tick_volume_in_lots", 1), default=True)
        daily_volume_in_lots = _as_bool(
            self.config.get("daily_volume_in_lots", self.config.get("tick_volume_in_lots", 1)),
            default=bool(tick_volume_in_lots),
        )
        benchmark_changes = {
            "sh_change": _compute_index_change(
                code=str(self.config.get("sh_index_code", _SH_INDEX_CODE)),
                trade_date=td,
                lot_size=lot_size,
                volume_in_lots=daily_volume_in_lots,
                tick_volume_in_lots=tick_volume_in_lots,
            ),
            "cy_change": _compute_index_change(
                code=str(self.config.get("cy_index_code", _CY_INDEX_CODE)),
                trade_date=td,
                lot_size=lot_size,
                volume_in_lots=daily_volume_in_lots,
                tick_volume_in_lots=tick_volume_in_lots,
            ),
        }

        results: list[dict[str, Any]] = []

        for code in lst:
            # Per-symbol tick size (microstructure) and bucket_size (chip grid)
            info = xdp.get_instrument_detail(code)
            tick_size = float(info.get("PriceTick") or self.config.get("bucket_size") or 0.01)
            if not (np.isfinite(tick_size) and tick_size > 0):
                tick_size = float(self.config.get("bucket_size", 0.01))
            bucket_size = tick_size

            # Build per-symbol engine config
            cfg = dict(self.config)
            cfg["bucket_size"] = float(bucket_size)

            engine = ETFChipEngine(cfg)
            ms_engine = MicrostructureEngine(cfg)

            # Stock-only: allow overriding the ETF engine's frozen kappa3 via instance override.
            try:
                k3 = cfg.get("kappa3", None)
                if k3 is not None:
                    k3f = float(k3)
                    if np.isfinite(k3f) and k3f >= 0:
                        engine.turnover.kappa3 = float(k3f)
                    else:
                        _warn_runtime_once(
                            f"stock_kappa3_invalid:{td}:{code}",
                            f"Stock: invalid kappa3 ignored: code={code} date={td} kappa3={k3!r}",
                        )
            except Exception as e:
                _warn_runtime_once(
                    f"stock_kappa3_override_failed:{td}:{code}",
                    f"Stock: kappa3 override failed (ignored): code={code} date={td} err={repr(e)}",
                )

            # Load yesterday state if available
            prev_state_path = ""
            prev_state_found = False
            prev_state_loaded = False
            cold_start_used = False
            cold_start_reason = ""
            corp_factor = 1.0
            corp_applied = False
            prev_state = chip_dir / f"{code.replace('.', '_')}_{prev_date}.npz" if prev_date else None

            if prev_state is not None:
                prev_state_path = str(prev_state)
                prev_state_found = bool(prev_state.exists())

            if prev_state_found and prev_state is not None:
                try:
                    engine.load_state(code, str(prev_state))
                    prev_state_loaded = True
                except Exception as e:
                    _warn_runtime_once(
                        f"stock_prev_state_load_failed:{td}:{code}",
                        f"Stock: 鏄ㄦ棩绛圭爜 state 鍔犺浇澶辫触锛屽皢鍐峰惎鍔? code={code} date={td} path={prev_state} err={repr(e)}",
                    )
                    prev_state_loaded = False
                    cold_start_reason = "prev_state_load_failed"

            # Cold start if no previous state
            if not prev_state_loaded:
                cold_start_used = True
                if not cold_start_reason:
                    if not str(prev_date).strip():
                        cold_start_reason = "prev_date_unavailable"
                    elif not prev_state_found:
                        cold_start_reason = "no_prev_state"
                    else:
                        cold_start_reason = "unknown"

                daily_df = _load_daily_history_before_trade_date(
                    code=code,
                    trade_date=td,
                    count=int(self.config.get("cold_start_lookback", 60)),
                    lot_size=lot_size,
                    volume_in_lots=daily_volume_in_lots,
                    expected_last_trade_date=str(prev_date or ""),
                    context=f"symbol:{code}:cold_start",
                )
                shares_detail = xdp.get_float_shares_detail(code, trade_date=td)
                shares_today = float(shares_detail.get("shares", 0.0))
                atr = xdp.calc_atr_10(daily_df) if daily_df is not None and not daily_df.empty else 0.0
                engine.cold_start(code, daily_df, total_shares=shares_today, atr=atr)

            # Corporate action detection + rescale (only when prior state exists)
            if prev_state_loaded:
                corp_factor, should_apply, reason = _corp_action_factor_for_trade_date(
                    code=code,
                    trade_date=td,
                    tick_size=tick_size,
                    lot_size=lot_size,
                    volume_in_lots=daily_volume_in_lots,
                )
                if reason:
                    if str(reason).startswith("corp_action_"):
                        raise RuntimeError(f"corp action unavailable: code={code} date={td} reason={reason}")
                    info_once(
                        f"stock_corp_factor_unavailable:{td}:{code}",
                        f"Stock: corp-action factor unavailable, skip rescale. code={code} date={td} reason={reason}",
                        logger_name=__name__,
                    )
                if should_apply:
                    try:
                        chips0 = engine.chips[code]
                        engine.chips[code] = rescale_chip_distribution(
                            chips0,
                            price_factor=float(corp_factor),
                            new_bucket_size=float(chips0.bucket_size),
                        )
                        corp_applied = True
                        info_once(
                            f"stock_corp_rescale_applied:{td}:{code}",
                            f"Stock: corp-action rescale applied. code={code} date={td} factor={corp_factor:.6f}",
                            logger_name=__name__,
                        )
                    except Exception as e:
                        _warn_runtime_once(
                            f"stock_corp_rescale_failed:{td}:{code}",
                            f"Stock: corp-action rescale failed, fallback cold-start. code={code} date={td} err={repr(e)}",
                        )
                        cold_start_used = True
                        cold_start_reason = "corp_rescale_failed"
                        daily_df = _load_daily_history_before_trade_date(
                            code=code,
                            trade_date=td,
                            count=int(self.config.get("cold_start_lookback", 60)),
                            lot_size=lot_size,
                            volume_in_lots=daily_volume_in_lots,
                            expected_last_trade_date=str(prev_date or ""),
                            context=f"symbol:{code}:cold_start",
                        )
                        shares_detail = xdp.get_float_shares_detail(code, trade_date=td)
                        shares_today = float(shares_detail.get("shares", 0.0))
                        atr = xdp.calc_atr_10(daily_df) if daily_df is not None and not daily_df.empty else 0.0
                        engine.cold_start(code, daily_df, total_shares=shares_today, atr=atr)
                        corp_factor = 1.0
                        corp_applied = False

            # Load tick data and build snapshots
            ticks = xdp.get_local_tick_data(code, td)
            if ticks is None or (isinstance(ticks, pd.DataFrame) and ticks.empty):
                ticks = xdp.get_market_tick_data(code, td, count=-1)
            snapshots = ticks_to_snapshots(ticks, lot_size=lot_size, volume_in_lots=tick_volume_in_lots)
            if snapshots is None or snapshots.empty:
                _warn_runtime_once(
                    f"stock_empty_tick:{td}:{code}",
                    f"Stock: tick 鏁版嵁涓虹┖锛屽凡璺宠繃: code={code} date={td}",
                )
                continue
            if int(snapshots["volume"].gt(0).sum()) <= 0:
                _warn_runtime_once(
                    f"stock_zero_volume_tick:{td}:{code}",
                    f"Stock: tick 鍏ㄤ负闆舵垚浜ら噺锛屽凡璺宠繃: code={code} date={td}",
                )
                continue

            # Optional: limit tick rows (debug)
            tick_count = int(self.config.get("daily_tick_count", -1))
            if tick_count > 0 and len(snapshots) > tick_count:
                snapshots = snapshots.tail(tick_count).reset_index(drop=True)

            # Volume unit diagnostic
            _maybe_check_tick_volume_unit(snapshots=snapshots, code=code, trade_date=td, cfg=self.config)

            # Shares / turnover base
            shares_detail = xdp.get_float_shares_detail(code, trade_date=td)
            shares_today = float(shares_detail.get("shares", 0.0))
            shares_source = str(shares_detail.get("source", "none"))
            shares_degraded = bool(shares_detail.get("degraded", True))

            # Last-resort fallback when shares are missing
            low_conf_shares = False
            if shares_today <= 0:
                try:
                    total_vol = float(pd.to_numeric(snapshots["volume"], errors="coerce").fillna(0.0).sum())
                    total_amt = float(pd.to_numeric(snapshots["amount"], errors="coerce").fillna(0.0).sum())
                    if total_vol > 0 and total_amt > 0:
                        assumed_daily_tr = 0.05
                        shares_today = total_vol / assumed_daily_tr
                        low_conf_shares = True
                        shares_source = "fallback_assumed_turnover"
                        shares_degraded = True
                        _warn_runtime_once(
                            f"stock_shares_last_resort:{td}:{code}",
                            (
                                "Stock: FloatVolume/TotalVolume unavailable, using low-confidence turnover fallback."
                                f" code={code} date={td} total_vol={total_vol:.2f}"
                                f" assumed_daily_tr={assumed_daily_tr:.4f}"
                            ),
                        )
                except Exception:
                    pass

            if shares_today <= 0:
                _warn_runtime_once(
                    f"stock_shares_missing:{td}:{code}",
                    f"Stock: shares_today 鏃犳晥锛岃烦杩囪绠? code={code} date={td} source={shares_source}",
                )
                continue

            engine.chips[code].total_shares = float(shares_today)
            # New trade day -> reset PR EMA smoothing.
            engine._get_pr_tracker(code).reset()  # type: ignore[attr-defined]

            # Skew signal injected as premium_rate
            premium_rate = _build_skew_premium_rate(
                snapshots,
                lot_size=lot_size,
                min_cum_vol_ratio=float(self.config.get("skew_min_cum_volume_ratio", 0.01)),
                min_cum_vol_lots=float(self.config.get("skew_min_cum_volume_lots", 200)),
            )
            snapshots = snapshots.copy()
            snapshots["premium_rate"] = premium_rate

            # Persist L1 snapshots for audit/recompute (optional)
            l1_path = l1_root / f"{code.replace('.', '_')}.parquet"
            if parquet_ok:
                try:
                    snapshots.to_parquet(l1_path, index=False)
                except Exception:
                    pass
            elif str(self.config.get("l1_fallback_csv", "0")).strip() == "1":
                try:
                    l1_path = l1_root / f"{code.replace('.', '_')}.csv"
                    snapshots.to_csv(l1_path, index=False, encoding="utf-8-sig")
                except Exception:
                    pass

            # ATR and end-of-day diffusion
            daily_11_effective = _load_trade_date_daily_bars(
                code=code,
                trade_date=td,
                count=11,
                lot_size=lot_size,
                volume_in_lots=daily_volume_in_lots,
                expected_prev_trade_date=str(prev_date or ""),
                snapshots=snapshots,
                context=f"symbol:{code}:daily_context",
            )
            atr = xdp.calc_atr_10(daily_11_effective)
            daily_context = _compute_daily_context_features(daily_11_effective)

            # Process all snapshots (avoid ETF redemption logic by not calling process_daily)
            high = snapshots["high"].to_numpy(dtype=np.float64, copy=False)
            low = snapshots["low"].to_numpy(dtype=np.float64, copy=False)
            close = snapshots["close"].to_numpy(dtype=np.float64, copy=False)
            vol = snapshots["volume"].to_numpy(dtype=np.float64, copy=False)
            amt = snapshots["amount"].to_numpy(dtype=np.float64, copy=False)
            prem = snapshots["premium_rate"].to_numpy(dtype=np.float64, copy=False)

            for i in range(len(snapshots)):
                if float(vol[i]) <= 0:
                    continue
                engine.process_snapshot(
                    code,
                    {
                        "high": float(high[i]),
                        "low": float(low[i]),
                        "close": float(close[i]),
                        "volume": float(vol[i]),
                        "amount": float(amt[i]),
                        "premium_rate": float(prem[i]),
                    },
                )

            # Day-end diffusion
            apply_brownian_diffusion(engine.chips[code], float(atr), k_diff=float(self.config.get("k_diff", 0.08)))

            last_close = float(close[-1]) if len(close) else float("nan")
            indicators = engine.get_indicators(code, current_price=float(last_close), atr=float(atr) if atr > 0 else None)

            # Microstructure factors (disable AP filter by passing premium_rates=None)
            adv_60 = _compute_adv_60(
                code=code,
                trade_date=td,
                lot_size=lot_size,
                volume_in_lots=daily_volume_in_lots,
                cfg=self.config,
                prev_trade_date=str(prev_date or ""),
            )
            ms_out = ms_engine.process_daily(
                etf_code=code,
                trade_date=td,
                snapshots=snapshots,
                premium_rates=None,
                adv_60=adv_60,
                tick_size=float(tick_size),
            )
            ms_raw = ms_out.get("raw") if isinstance(ms_out, dict) else {}
            ms_feat = ms_out.get("features") if isinstance(ms_out, dict) else {}
            ms_meta = ms_out.get("meta") if isinstance(ms_out, dict) else {}

            state_path = chip_dir / f"{code.replace('.', '_')}_{td}.npz"
            engine.save_state(code, str(state_path))
            state_init = "cold_start" if bool(cold_start_used) else "prev_state"

            results.append(
                {
                    "trade_date": td,
                    "code": code,
                    # Chip indicators
                    "profit_ratio": indicators.get("profit_ratio"),
                    "profit_ratio_ema_short": indicators.get("profit_ratio_ema_short"),
                    "profit_ratio_ema_long": indicators.get("profit_ratio_ema_long"),
                    "asr": indicators.get("asr"),
                    "dense_zones": indicators.get("dense_zones"),
                    # Stock-specific meta
                    "tick_size": float(tick_size),
                    "bucket_size": float(engine.chips[code].bucket_size),
                    "shares_today": float(shares_today),
                    "shares_source": shares_source,
                    "shares_degraded": bool(shares_degraded),
                    "shares_low_confidence": bool(low_conf_shares),
                    "corp_action_factor": float(corp_factor) if np.isfinite(corp_factor) else float("nan"),
                    "corp_action_applied": bool(corp_applied),
                    **daily_context,
                    **benchmark_changes,
                    # Microstructure
                    **(ms_raw if isinstance(ms_raw, dict) else {}),
                    **(ms_feat if isinstance(ms_feat, dict) else {}),
                    **(ms_meta if isinstance(ms_meta, dict) else {}),
                    "state_path": str(state_path),
                    # Debug / audit: ensure daily run is not silently cold-starting.
                    "prev_trade_date": str(prev_date or "").strip(),
                    "prev_state_path": str(prev_state_path),
                    "prev_state_found": bool(prev_state_found),
                    "prev_state_loaded": bool(prev_state_loaded),
                    "cold_start_used": bool(cold_start_used),
                    "cold_start_reason": str(cold_start_reason or ""),
                    "state_init": str(state_init),
                }
            )

        df_result = pd.DataFrame(results)

        # Cross-sectional ranking (same feature list as ETF engine)
        if len(results) > 1 and not df_result.empty:
            try:
                xs_pipeline = FeaturePipeline()
                xs_cols = [
                    "vpin_rank",
                    "delta_vpin_z",
                    "ofi_skew_z",
                    "kyle_lambda_z",
                    "rv_bipower_z",
                    "vwap_dev_z",
                ]
                codes_series = df_result["code"].astype(str)
                for col in xs_cols:
                    if col not in df_result.columns:
                        continue
                    vals: dict[str, float] = {}
                    for code_key, v in zip(codes_series.tolist(), df_result[col].tolist()):
                        try:
                            fv = float(v)
                        except Exception:
                            fv = float("nan")
                        vals[code_key] = fv
                    ranked = xs_pipeline.cross_sectional_rank(vals)
                    df_result[f"{col}_xs"] = codes_series.map(ranked)
            except Exception as e:
                info_once(
                    "stock_micro_xs_ranking_failed",
                    f"Stock Micro: 妯埅闈㈡帓鍚嶈绠楀け璐ワ紝宸查檷绾ц烦杩? err={repr(e)}",
                    logger_name=__name__,
                )

        return df_result













