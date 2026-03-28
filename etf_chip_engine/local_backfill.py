from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from etf_chip_engine.config import CONFIG
from etf_chip_engine.engine import ETFChipEngine
from etf_chip_engine.microstructure.factor_engine import MicrostructureEngine
from etf_chip_engine.modules import apply_brownian_diffusion
from stock_chip_engine.data.tick_adapter import ticks_to_snapshots


_AM_START = dtime(9, 30)
_AM_END = dtime(11, 30)
_PM_START = dtime(13, 0)
_PM_END = dtime(15, 0)


def _norm_code(code: str) -> str:
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        return s
    if len(s) == 6 and s.isdigit():
        if s.startswith(("5", "6", "9")):
            return f"{s}.SH"
        return f"{s}.SZ"
    return s


def _code6(code: str) -> str:
    s = _norm_code(code)
    if "." in s:
        s = s.split(".", 1)[0]
    return s if len(s) == 6 and s.isdigit() else ""


def _load_codes(path: Path) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        for tok in line.replace("\t", ",").replace(" ", ",").split(","):
            c = _norm_code(tok)
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(c)
    return out


def _parse_yyyymmdd(s: str) -> Optional[str]:
    v = str(s or "").strip()
    if re.fullmatch(r"\d{8}", v):
        return v
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v.replace("-", "")
    return None


def _collect_trade_date_dirs(tick_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    pat = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for p in tick_root.rglob("*"):
        if not p.is_dir():
            continue
        if not pat.fullmatch(p.name):
            continue
        td = p.name.replace("-", "")
        if td in out:
            continue
        out[td] = p
    return out


def _pick_col(cols_map: dict[str, str], *candidates: str) -> Optional[str]:
    for c in candidates:
        k = str(c).strip().lower()
        if k in cols_map:
            return cols_map[k]
    return None


def _to_numeric_series(df: pd.DataFrame, col: Optional[str], *, default: float = 0.0) -> pd.Series:
    if not col:
        return pd.Series(np.full(len(df), float(default), dtype=np.float64), index=df.index)
    return pd.to_numeric(df[col], errors="coerce").fillna(float(default))


def _in_session_series(ts: pd.Series) -> pd.Series:
    t = ts.dt.time
    return ((t >= _AM_START) & (t < _AM_END)) | ((t >= _PM_START) & (t < _PM_END))


def _vendor_csv_to_snapshots(path: Path) -> tuple[pd.DataFrame, str]:
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        try:
            df = pd.read_csv(path)
        except Exception:
            return pd.DataFrame(), ""
    if df is None or df.empty:
        return pd.DataFrame(), ""

    cols_map = {str(c).strip().lower(): str(c) for c in df.columns}
    c_time = _pick_col(cols_map, "time", "datetime", "dt")
    c_name = _pick_col(cols_map, "display_name", "name")
    c_close = _pick_col(cols_map, "current", "lastprice", "last_price", "close", "price")
    c_high = _pick_col(cols_map, "high")
    c_low = _pick_col(cols_map, "low")
    c_total_vol = _pick_col(cols_map, "total_volume", "cum_volume")
    c_total_amt = _pick_col(cols_map, "total_money", "cum_money", "cum_amount")
    c_vol_inc = _pick_col(cols_map, "volume", "vol")
    c_amt_inc = _pick_col(cols_map, "money", "amount", "amt")
    c_ask1_p = _pick_col(cols_map, "a1_p", "ask1", "askprice1", "ask_price1")
    c_ask1_v = _pick_col(cols_map, "a1_v", "ask1_v", "askvol1", "ask_vol1")
    c_bid1_p = _pick_col(cols_map, "b1_p", "bid1", "bidprice1", "bid_price1")
    c_bid1_v = _pick_col(cols_map, "b1_v", "bid1_v", "bidvol1", "bid_vol1")

    if not c_time or not c_close:
        return pd.DataFrame(), ""

    t_raw = df[c_time].astype(str).str.extract(r"(\d{14})", expand=False)
    ts = pd.to_datetime(t_raw, format="%Y%m%d%H%M%S", errors="coerce")
    valid_t = ts.notna()
    if not bool(valid_t.any()):
        return pd.DataFrame(), ""

    df2 = df.loc[valid_t].copy()
    ts2 = ts.loc[valid_t]
    session_mask = _in_session_series(ts2)
    if not bool(session_mask.any()):
        return pd.DataFrame(), ""
    df2 = df2.loc[session_mask].copy()
    ts2 = ts2.loc[session_mask]

    # Keep last row for duplicated timestamps.
    order = np.argsort(ts2.to_numpy(dtype="datetime64[ns]"), kind="mergesort")
    df2 = df2.iloc[order].reset_index(drop=True)
    ts2 = ts2.iloc[order].reset_index(drop=True)
    dedup = ~ts2.duplicated(keep="last")
    df2 = df2.loc[dedup].reset_index(drop=True)
    ts2 = ts2.loc[dedup].reset_index(drop=True)

    close = _to_numeric_series(df2, c_close, default=0.0).to_numpy(dtype=np.float64, copy=False)
    high = _to_numeric_series(df2, c_high, default=0.0).to_numpy(dtype=np.float64, copy=False) if c_high else close.copy()
    low = _to_numeric_series(df2, c_low, default=0.0).to_numpy(dtype=np.float64, copy=False) if c_low else close.copy()

    if c_total_vol:
        volume_cum = _to_numeric_series(df2, c_total_vol, default=0.0).to_numpy(dtype=np.float64, copy=False)
    else:
        volume_cum = _to_numeric_series(df2, c_vol_inc, default=0.0).to_numpy(dtype=np.float64, copy=False).cumsum()
    if c_total_amt:
        amount_cum = _to_numeric_series(df2, c_total_amt, default=0.0).to_numpy(dtype=np.float64, copy=False)
    else:
        amount_cum = _to_numeric_series(df2, c_amt_inc, default=0.0).to_numpy(dtype=np.float64, copy=False).cumsum()

    # Guard against occasional negative jumps.
    volume_cum = np.maximum.accumulate(np.maximum(volume_cum, 0.0))
    amount_cum = np.maximum.accumulate(np.maximum(amount_cum, 0.0))

    ask1 = _to_numeric_series(df2, c_ask1_p, default=0.0).to_numpy(dtype=np.float64, copy=False)
    ask1_v = _to_numeric_series(df2, c_ask1_v, default=0.0).to_numpy(dtype=np.float64, copy=False)
    bid1 = _to_numeric_series(df2, c_bid1_p, default=0.0).to_numpy(dtype=np.float64, copy=False)
    bid1_v = _to_numeric_series(df2, c_bid1_v, default=0.0).to_numpy(dtype=np.float64, copy=False)

    # Vendor CSV timestamps are parsed as local wall-clock time (no timezone).
    # Convert to UTC epoch-ms so downstream session filters decode correctly.
    try:
        ts_epoch_ms = (
            ts2.dt.tz_localize("Asia/Shanghai")
            .dt.tz_convert("UTC")
            .astype("int64")
            // 1_000_000
        )
    except Exception:
        # Fallback keeps prior behavior when tz conversion is unavailable.
        ts_epoch_ms = ts2.astype("int64") // 1_000_000
    ts_ms = ts_epoch_ms.to_numpy(dtype=np.float64, copy=False)
    raw_xt = pd.DataFrame(
        {
            "time": ts_ms,
            "lastPrice": close,
            "high": high,
            "low": low,
            "volume": volume_cum,
            "amount": amount_cum,
            "askPrice1": ask1,
            "askVol1": ask1_v,
            "bidPrice1": bid1,
            "bidVol1": bid1_v,
        }
    )

    snaps = ticks_to_snapshots(raw_xt, lot_size=100.0, volume_in_lots=False)
    if snaps is None or snaps.empty:
        return pd.DataFrame(), ""
    snaps = snaps.loc[pd.to_numeric(snaps["close"], errors="coerce").fillna(0.0) > 0].reset_index(drop=True)
    if snaps.empty:
        return pd.DataFrame(), ""

    name = ""
    if c_name:
        try:
            name = str(df2[c_name].iloc[0]).strip()
        except Exception:
            name = ""
    return snaps, name


def _calc_atr_10(daily_df: pd.DataFrame) -> float:
    if daily_df is None or daily_df.empty:
        return 0.0
    high = pd.to_numeric(daily_df["high"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
    low = pd.to_numeric(daily_df["low"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
    close = pd.to_numeric(daily_df["close"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
    prev_close = pd.Series(close).shift(1).to_numpy(copy=True)
    if prev_close.size:
        prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr = np.nan_to_num(tr, nan=0.0, posinf=0.0, neginf=0.0)
    atr = pd.Series(tr).rolling(10, min_periods=1).mean().to_numpy()
    v = float(atr[-1]) if atr.size else 0.0
    return v if np.isfinite(v) else 0.0


def _daily_bar(snaps: pd.DataFrame) -> dict[str, float]:
    close = pd.to_numeric(snaps["close"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    high = pd.to_numeric(snaps["high"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    low = pd.to_numeric(snaps["low"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    volume = pd.to_numeric(snaps["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    amount = pd.to_numeric(snaps["amount"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    return {
        "open": float(close[0]) if close.size else 0.0,
        "high": float(np.nanmax(high)) if high.size else 0.0,
        "low": float(np.nanmin(low)) if low.size else 0.0,
        "close": float(close[-1]) if close.size else 0.0,
        "volume": float(np.nansum(volume)),
        "amount": float(np.nansum(amount)),
    }


def _adv_60(history_rows: list[dict[str, float]]) -> Optional[float]:
    if not history_rows:
        return None
    vols = [float(x.get("volume", 0.0) or 0.0) for x in history_rows[-60:]]
    if not vols:
        return None
    v = float(np.nanmean(np.asarray(vols, dtype=np.float64)))
    return v if np.isfinite(v) and v > 0 else None


def _iter_zones(v: Any) -> list[dict[str, Any]]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            obj = json.loads(s)
        except Exception:
            try:
                import ast

                obj = ast.literal_eval(s)
            except Exception:
                return []
    else:
        obj = v
    if not isinstance(obj, list):
        return []
    return [x for x in obj if isinstance(x, dict)]


def _zones_to_json(v: Any) -> str:
    zones = _iter_zones(v)
    return json.dumps(zones, ensure_ascii=False, separators=(",", ":")) if zones else "[]"


def _dpc_peak_density(v: Any) -> float:
    zones = _iter_zones(v)
    peak = 0.0
    for z in zones:
        try:
            d = float(z.get("density", 0.0) or 0.0)
        except Exception:
            d = 0.0
        if d > peak:
            peak = d
    return float(peak)


def _max_density_price(v: Any, *, zone_type: str) -> Optional[float]:
    zones = _iter_zones(v)
    best_d = -1.0
    best_p: Optional[float] = None
    for z in zones:
        if str(z.get("type") or "") != zone_type:
            continue
        try:
            p = float(z.get("price", 0.0) or 0.0)
            d = float(z.get("density", 0.0) or 0.0)
        except Exception:
            continue
        if d > best_d:
            best_d = d
            best_p = p
    return best_p


def _factor_history_dirs() -> list[Path]:
    out: list[Path] = []
    ms_cfg = CONFIG.get("microstructure")
    if isinstance(ms_cfg, dict):
        raw = str(ms_cfg.get("factor_history_dir", "") or "").strip()
        if raw:
            out.append(Path(raw))
    legacy = Path("etf_chip_engine") / "data" / "factor_history"
    if not out:
        out.append(legacy)
    elif legacy not in out:
        out.append(legacy)
    return out


def _count_history_rows(path: Path) -> Optional[int]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            n = 0
            with path.open("r", encoding="utf-8") as f:
                for _ in f:
                    n += 1
            return int(max(0, n - 1))
        except Exception:
            return None
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore

            return int(max(0, pq.ParquetFile(path).metadata.num_rows))
        except Exception:
            return None
    return None


def _count_factor_history_days(code: str) -> int:
    key = code.replace(".", "_")
    for d in _factor_history_dirs():
        for ext in (".csv", ".parquet"):
            p = d / f"{key}{ext}"
            if not p.exists():
                continue
            rows = _count_history_rows(p)
            if rows is not None:
                return int(max(0, rows))
    return 0


@dataclass(frozen=True)
class _WorkerArgs:
    code: str
    date_items: tuple[tuple[str, str], ...]
    assumed_daily_turnover: float
    tick_size: float
    l1_root: str
    chip_root: str
    l1_csv_only: bool
    max_history_days: int


def _process_code(args: _WorkerArgs) -> list[dict[str, Any]]:
    cfg = dict(CONFIG)
    ms_cfg = dict(cfg.get("microstructure", {})) if isinstance(cfg.get("microstructure"), dict) else {}
    ms_cfg["max_history_days"] = int(max(args.max_history_days, 60))
    cfg["microstructure"] = ms_cfg

    engine = ETFChipEngine(cfg)
    ms_engine = MicrostructureEngine(cfg)

    code = str(args.code)
    c6 = _code6(code)
    if not c6:
        return []

    l1_root = Path(args.l1_root)
    chip_root = Path(args.chip_root)
    chip_root.mkdir(parents=True, exist_ok=True)

    hist_rows: list[dict[str, float]] = []
    rows: list[dict[str, Any]] = []

    for td, day_dir_s in args.date_items:
        file_path = Path(day_dir_s) / f"{c6}.csv"
        if not file_path.exists():
            continue
        snaps, name = _vendor_csv_to_snapshots(file_path)
        if snaps.empty:
            continue

        vol_sum = float(pd.to_numeric(snaps["volume"], errors="coerce").fillna(0.0).sum())
        amt_sum = float(pd.to_numeric(snaps["amount"], errors="coerce").fillna(0.0).sum())
        if vol_sum <= 0 or amt_sum <= 0:
            continue

        day_bar = _daily_bar(snaps)
        shares_today = max(float(day_bar["volume"]) / max(float(args.assumed_daily_turnover), 1e-6), 1.0)

        cold_start_used = False
        if code not in engine.chips:
            cold_src = hist_rows[-60:] if hist_rows else [day_bar]
            atr_cold = _calc_atr_10(pd.DataFrame((hist_rows + [day_bar])[-11:]))
            engine.cold_start(code, pd.DataFrame(cold_src), total_shares=shares_today, atr=float(atr_cold))
            cold_start_used = True

        engine.chips[code].total_shares = float(shares_today)
        # Reset per-trade-date smoothing state.
        engine._get_pr_tracker(code).reset()  # type: ignore[attr-defined]

        high = pd.to_numeric(snaps["high"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
        low = pd.to_numeric(snaps["low"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
        close = pd.to_numeric(snaps["close"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
        volume = pd.to_numeric(snaps["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)
        amount = pd.to_numeric(snaps["amount"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64, copy=False)

        for i in range(len(snaps)):
            if float(volume[i]) <= 0:
                continue
            engine.process_snapshot(
                code,
                {
                    "high": float(high[i]),
                    "low": float(low[i]),
                    "close": float(close[i]),
                    "volume": float(volume[i]),
                    "amount": float(amount[i]),
                    "premium_rate": 0.0,
                },
            )

        atr = _calc_atr_10(pd.DataFrame((hist_rows + [day_bar])[-11:]))
        apply_brownian_diffusion(engine.chips[code], float(atr), k_diff=float(cfg.get("k_diff", 0.08)))
        indicators = engine.get_indicators(
            code,
            current_price=float(day_bar["close"]),
            atr=(float(atr) if float(atr) > 0 else None),
        )

        adv_60 = _adv_60(hist_rows)
        ms_out = ms_engine.process_daily(
            etf_code=code,
            trade_date=str(td),
            snapshots=snaps,
            premium_rates=None,
            adv_60=adv_60,
            tick_size=float(args.tick_size),
        )
        ms_raw = ms_out.get("raw") if isinstance(ms_out, dict) else {}
        ms_feat = ms_out.get("features") if isinstance(ms_out, dict) else {}
        ms_meta = ms_out.get("meta") if isinstance(ms_out, dict) else {}

        l1_dir = l1_root / str(td)
        l1_dir.mkdir(parents=True, exist_ok=True)
        l1_base = l1_dir / f"{code.replace('.', '_')}"
        if bool(args.l1_csv_only):
            snaps.to_csv(l1_base.with_suffix(".csv"), index=False, encoding="utf-8-sig")
        else:
            try:
                snaps.to_parquet(l1_base.with_suffix(".parquet"), index=False)
            except Exception:
                snaps.to_csv(l1_base.with_suffix(".csv"), index=False, encoding="utf-8-sig")

        state_path = chip_root / f"{code.replace('.', '_')}_{td}.npz"
        engine.save_state(code, str(state_path))

        row: dict[str, Any] = {
            "trade_date": str(td),
            "code": str(code),
            "name": str(name),
            "profit_ratio": indicators.get("profit_ratio"),
            "profit_ratio_ema_short": indicators.get("profit_ratio_ema_short"),
            "profit_ratio_ema_long": indicators.get("profit_ratio_ema_long"),
            "asr": indicators.get("asr"),
            "dense_zones": indicators.get("dense_zones"),
            "shares_today": float(shares_today),
            "atr_10": float(atr),
            "cold_start_used": bool(cold_start_used),
            "state_path": str(state_path),
        }
        if isinstance(ms_raw, dict):
            row.update(ms_raw)
        if isinstance(ms_feat, dict):
            row.update(ms_feat)
        if isinstance(ms_meta, dict):
            row.update(ms_meta)
        rows.append(row)

        hist_rows.append(day_bar)

    return rows


def run_local_backfill(
    *,
    tick_root: Path,
    codes_file: Path,
    start_date: str,
    end_date: str,
    workers: int,
    assumed_daily_turnover: float,
    tick_size: float,
    l1_csv_only: bool,
    max_history_days: int,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    codes = _load_codes(codes_file)
    if not codes:
        raise RuntimeError(f"no codes loaded from: {codes_file}")

    date_dir_map = _collect_trade_date_dirs(tick_root)
    if not date_dir_map:
        raise RuntimeError(f"no trade-date directories found under: {tick_root}")

    s = _parse_yyyymmdd(start_date)
    if s is None:
        raise RuntimeError(f"invalid --start: {start_date}")
    if str(end_date).strip().lower() in {"", "auto", "latest"}:
        e = max(date_dir_map.keys())
    else:
        e = _parse_yyyymmdd(end_date)
        if e is None:
            raise RuntimeError(f"invalid --end: {end_date}")
    if s > e:
        raise RuntimeError(f"start > end: {s} > {e}")

    trade_dates = sorted([d for d in date_dir_map.keys() if s <= d <= e])
    if not trade_dates:
        raise RuntimeError(f"no trade dates in range: {s} ~ {e}")

    date_items: tuple[tuple[str, str], ...] = tuple((d, str(date_dir_map[d])) for d in trade_dates)

    l1_root = Path(str(CONFIG.get("l1_snapshot_dir", Path("etf_chip_engine") / "data" / "l1_snapshots")))
    chip_root = Path(str(CONFIG.get("chip_snapshot_dir", Path("etf_chip_engine") / "data" / "chip_snapshots")))
    out_main_dir = Path("etf_chip_engine") / "data"
    out_main_dir.mkdir(parents=True, exist_ok=True)
    integ_chip_dir = Path("output") / "integration" / "chip"
    integ_chip_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        _WorkerArgs(
            code=str(code),
            date_items=date_items,
            assumed_daily_turnover=float(assumed_daily_turnover),
            tick_size=float(tick_size),
            l1_root=str(l1_root),
            chip_root=str(chip_root),
            l1_csv_only=bool(l1_csv_only),
            max_history_days=int(max_history_days),
        )
        for code in codes
    ]

    rows_all: list[dict[str, Any]] = []
    done = 0
    t1 = time.perf_counter()
    worker_count = max(int(workers), 1)
    if worker_count == 1:
        row_iter = (_process_code(task) for task in tasks)
        for rows in row_iter:
            rows_all.extend(rows)
            done += 1
            print(
                json.dumps(
                    {
                        "timing": "etf_chip_engine.local_backfill.code_done",
                        "done": int(done),
                        "total": int(len(tasks)),
                        "rows_accumulated": int(len(rows_all)),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as ex:
            futs = [ex.submit(_process_code, task) for task in tasks]
            for fut in as_completed(futs):
                rows = fut.result()
                rows_all.extend(rows)
                done += 1
                print(
                    json.dumps(
                        {
                            "timing": "etf_chip_engine.local_backfill.code_done",
                            "done": int(done),
                            "total": int(len(tasks)),
                            "rows_accumulated": int(len(rows_all)),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    t2 = time.perf_counter()

    if not rows_all:
        raise RuntimeError("no valid rows produced from local tick data")

    df_all = pd.DataFrame(rows_all)
    if "trade_date" not in df_all.columns:
        raise RuntimeError("internal error: missing trade_date in results")

    hist_days_cache: dict[str, int] = {str(c): _count_factor_history_days(str(c)) for c in codes}
    written_files: list[str] = []
    written_trade_dates: list[str] = []

    for td, df_day in df_all.groupby("trade_date", sort=True):
        df_day = df_day.copy()
        if "code" in df_day.columns:
            df_day["code"] = df_day["code"].astype(str)
        df_day = df_day.sort_values("code").reset_index(drop=True)

        out_main = out_main_dir / f"batch_results_{td}.csv"
        df_day.to_csv(out_main, index=False, encoding="utf-8-sig")

        df_integ = df_day.copy()
        if "dense_zones" in df_integ.columns:
            df_integ["dense_zones_json"] = df_integ["dense_zones"].map(_zones_to_json)
            df_integ["dpc_peak_density"] = df_integ["dense_zones"].map(_dpc_peak_density)
            df_integ["support_price_max_density"] = df_integ["dense_zones"].map(
                lambda v: _max_density_price(v, zone_type="support")
            )
            df_integ["resistance_price_max_density"] = df_integ["dense_zones"].map(
                lambda v: _max_density_price(v, zone_type="resistance")
            )
        if "code" in df_integ.columns:
            df_integ["chip_engine_days"] = df_integ["code"].map(
                lambda c: int(hist_days_cache.get(str(c), 0))
            )
        out_integ = integ_chip_dir / f"batch_results_{td}.csv"
        df_integ.to_csv(out_integ, index=False, encoding="utf-8-sig")

        written_files.append(str(out_main))
        written_files.append(str(out_integ))
        written_trade_dates.append(str(td))

    t3 = time.perf_counter()
    return {
        "start_date": str(s),
        "end_date": str(e),
        "trade_dates_requested": int(len(trade_dates)),
        "trade_dates_written": int(len(set(written_trade_dates))),
        "codes": int(len(codes)),
        "rows_total": int(len(df_all)),
        "written_files": int(len(written_files)),
        "paths": {
            "batch_results_dir": str(out_main_dir),
            "integration_chip_dir": str(integ_chip_dir),
            "chip_snapshot_dir": str(chip_root),
            "l1_snapshot_dir": str(l1_root),
        },
        "seconds": {
            "prepare": round(t1 - t0, 3),
            "compute": round(t2 - t1, 3),
            "write": round(t3 - t2, 3),
            "total": round(t3 - t0, 3),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Backfill ETF chip + microstructure factors from local tick_data CSV directories."
    )
    p.add_argument("--tick-root", default="tick_data", help="local tick root (contains dated dirs)")
    p.add_argument("--codes-file", default="backtest/default_universe_50.txt", help="ETF codes file")
    p.add_argument("--start", required=True, help="start date, YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--end", default="auto", help="end date, YYYYMMDD or YYYY-MM-DD or auto")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1), help="parallel workers")
    p.add_argument("--assumed-daily-turnover", type=float, default=0.05, help="shares fallback: total_vol / turnover")
    p.add_argument("--tick-size", type=float, default=0.001, help="tick size for microstructure")
    p.add_argument("--l1-csv-only", action="store_true", help="write L1 snapshots as CSV only")
    p.add_argument("--max-history-days", type=int, default=400, help="micro factor history retention days")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    ns = build_parser().parse_args(argv)
    result = run_local_backfill(
        tick_root=Path(str(ns.tick_root)),
        codes_file=Path(str(ns.codes_file)),
        start_date=str(ns.start),
        end_date=str(ns.end),
        workers=int(ns.workers),
        assumed_daily_turnover=float(ns.assumed_daily_turnover),
        tick_size=float(ns.tick_size),
        l1_csv_only=bool(ns.l1_csv_only),
        max_history_days=int(ns.max_history_days),
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
