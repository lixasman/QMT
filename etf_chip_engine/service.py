from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.warn_utils import info_once, warn_once
from etf_chip_engine.config import CONFIG
from etf_chip_engine.data.tick_adapter import ticks_to_snapshots
from etf_chip_engine.data.xtdata_provider import (
    calc_atr_10,
    ensure_tick_data_downloaded,
    filter_etf_codes_by_keywords,
    get_daily_bars,
    get_etf_info,
    get_industry_etf_universe,
    get_local_tick_data,
    get_market_tick_data,
    get_total_shares,
    prev_trade_date,
    retry_download_for_empty_tick_code_once,
)
from etf_chip_engine.engine import ETFChipEngine
from etf_chip_engine.modules import calc_asr
from etf_chip_engine.microstructure.auxiliary_factors import asr_velocity, premium_vpin_cross, profit_ofi_divergence
from etf_chip_engine.microstructure.factor_engine import MicrostructureEngine


@dataclass(frozen=True)
class DailyRunResult:
    trade_date: str
    etf_code: str
    indicators: dict[str, Any]
    state_path: str


def _format_eta(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds < 0:
        return "--:--"
    total = int(seconds)
    mins, sec = divmod(total, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"
    return f"{mins:02d}:{sec:02d}"


class _BatchProgress:
    def __init__(
        self,
        total: int,
        *,
        update_interval_sec: float = 0.5,
        bar_width: int = 24,
    ) -> None:
        self.total = max(int(total), 0)
        self.update_interval_sec = float(max(update_interval_sec, 0.0))
        self.bar_width = max(int(bar_width), 8)
        self.start_ts = time.perf_counter()
        self.last_print_ts = 0.0

    def update(self, done: int, *, rows: int, skipped: int, code: str = "") -> None:
        if self.total <= 0:
            return
        done_i = max(0, min(int(done), self.total))
        now = time.perf_counter()
        is_final = done_i >= self.total
        if not is_final and (now - self.last_print_ts) < self.update_interval_sec:
            return

        elapsed = max(now - self.start_ts, 1e-9)
        speed = float(done_i) / elapsed
        remaining = max(self.total - done_i, 0)
        eta = (remaining / speed) if speed > 1e-12 else float("inf")
        pct = (100.0 * done_i / self.total) if self.total > 0 else 100.0

        fill = int(round(self.bar_width * done_i / self.total)) if self.total > 0 else self.bar_width
        fill = max(0, min(fill, self.bar_width))
        bar = ("#" * fill) + ("-" * (self.bar_width - fill))
        code_short = str(code or "")
        if len(code_short) > 14:
            code_short = code_short[:14]

        line = (
            f"\rprogress [{bar}] {done_i:>4}/{self.total:<4} {pct:6.2f}% "
            f"rows={int(rows):>4} skipped={int(skipped):>4} "
            f"speed={speed:5.2f}/s eta={_format_eta(eta)} code={code_short}"
        )
        print(line, end="" if not is_final else "\n", flush=True)
        self.last_print_ts = now


_RUNTIME_WARNED: set[str] = set()


def _warn_runtime_once(key: str, msg: str) -> None:
    warn_once(key, msg, logger_name=__name__)
    if key in _RUNTIME_WARNED:
        return
    _RUNTIME_WARNED.add(key)
    print(f"[WARN] {msg}", flush=True)


def _resolve_premium_rates(
    *,
    code: str,
    trade_date: str,
    snapshots: pd.DataFrame,
    engine: ETFChipEngine,
) -> Optional[pd.Series]:
    if snapshots is None or snapshots.empty:
        return None

    if "premium_rate" in snapshots.columns:
        try:
            prem = pd.to_numeric(snapshots["premium_rate"], errors="coerce").to_numpy(dtype=np.float64, copy=False)
        except Exception as e:
            _warn_runtime_once(
                f"premium_rate_parse_failed:{trade_date}:{code}",
                f"Micro: failed to parse premium_rate, fallback to IOPV. code={code} date={trade_date} err={repr(e)}",
            )
            prem = np.array([], dtype=np.float64)

        if prem.size > 0 and "iopv" in snapshots.columns:
            try:
                iopv = pd.to_numeric(snapshots["iopv"], errors="coerce").to_numpy(dtype=np.float64, copy=False)
            except Exception as e:
                _warn_runtime_once(
                    f"tick_iopv_parse_failed:{trade_date}:{code}",
                    f"Micro: failed to parse tick iopv, fallback to IOPV. code={code} date={trade_date} err={repr(e)}",
                )
                iopv = np.array([], dtype=np.float64)
            valid_iopv = (iopv > 0) & np.isfinite(iopv)
            if valid_iopv.size == prem.size and valid_iopv.any():
                prem_clean = np.where(valid_iopv & np.isfinite(prem), prem, 0.0).astype(np.float64, copy=False)
                return pd.Series(prem_clean, index=snapshots.index)
            _warn_runtime_once(
                f"tick_iopv_invalid:{trade_date}:{code}",
                f"Micro: tick iopv is invalid for all rows, fallback to IOPV. code={code} date={trade_date}",
            )
        elif prem.size > 0 and np.isfinite(prem).any():
            prem_clean = np.where(np.isfinite(prem), prem, 0.0).astype(np.float64, copy=False)
            return pd.Series(prem_clean, index=snapshots.index)

    if code not in engine.iopv or "close" not in snapshots.columns:
        return None

    calc = engine.iopv[code]
    try:
        iopv_v = float(calc.calculate_iopv())
    except Exception as e:
        _warn_runtime_once(
            f"iopv_calculate_failed:{trade_date}:{code}",
            f"IOPV: fallback calculation failed, premium_rate will be empty. code={code} date={trade_date} err={repr(e)}",
        )
        return None
    if not (np.isfinite(iopv_v) and iopv_v > 0):
        _warn_runtime_once(
            f"iopv_invalid:{trade_date}:{code}",
            f"IOPV: fallback value invalid, premium_rate will be empty. code={code} date={trade_date} iopv={iopv_v}",
        )
        return None

    try:
        coverage = float(calc.get_coverage())
    except Exception as e:
        _warn_runtime_once(
            f"iopv_coverage_failed:{trade_date}:{code}",
            f"IOPV: failed to read coverage, using 1.0. code={code} date={trade_date} err={repr(e)}",
        )
        coverage = 1.0
    scale = float(coverage) if np.isfinite(coverage) and coverage > 0 else 1.0

    closes = pd.to_numeric(snapshots["close"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
    prem = ((closes - iopv_v) / iopv_v) * scale
    _warn_runtime_once(
        f"premium_rate_fallback_iopv:{trade_date}:{code}",
        f"Micro: using IOPV fallback premium_rate. code={code} date={trade_date} iopv={iopv_v:.6f} coverage={scale:.4f}",
    )
    return pd.Series(prem, index=snapshots.index)


class IndustryETFChipService:
    def __init__(self, *, config: Optional[dict[str, object]] = None):
        self.config = dict(CONFIG)
        if config:
            self.config.update(config)

    def run_daily(
        self,
        trade_date: str,
        *,
        limit: Optional[int] = None,
        codes: Optional[list[str]] = None,
        force_download: bool = False,
    ) -> pd.DataFrame:
        if codes is not None:
            codes_from_input = True
            codes = [str(c).strip() for c in codes if str(c).strip()]
        else:
            codes_from_input = False
            df_univ = get_industry_etf_universe()
            codes = df_univ["code"].astype(str).tolist()
        if limit is not None:
            codes = codes[: max(int(limit), 0)]
        if codes_from_input and codes:
            before_cnt = len(codes)
            codes = filter_etf_codes_by_keywords(codes)
            removed_cnt = max(0, before_cnt - len(codes))
            if removed_cnt > 0:
                print(
                    json.dumps(
                        {
                            "timing": "etf_chip_engine.service.code_keyword_filter",
                            "input_count": int(before_cnt),
                            "removed_by_keyword": int(removed_cnt),
                            "kept_count": int(len(codes)),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

        total_codes = len(codes)
        print(f"trade_date={trade_date} universe={total_codes}", flush=True)
        download_stats = ensure_tick_data_downloaded(
            codes,
            trade_date,
            force=bool(force_download),
            chunk_size=int(self.config.get("tick_download_chunk_size", 200)),
            timeout_sec=int(self.config.get("tick_download_chunk_timeout_sec", 45)),
        )
        print(
            json.dumps(
                {
                    "timing": "etf_chip_engine.service.pre_download",
                    **download_stats,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        engine = ETFChipEngine(self.config)
        ms_engine = MicrostructureEngine(self.config)
        chip_dir = Path(str(self.config.get("chip_snapshot_dir")))
        chip_dir.mkdir(parents=True, exist_ok=True)
        l1_dir = Path(str(self.config.get("l1_snapshot_dir", Path("etf_chip_engine") / "data" / "l1_snapshots"))) / str(trade_date)
        l1_dir.mkdir(parents=True, exist_ok=True)
        parquet_ok = True
        try:
            import pyarrow  # type: ignore  # noqa: F401
        except Exception:
            try:
                import fastparquet  # type: ignore  # noqa: F401
            except Exception:
                parquet_ok = False

        prev_date = prev_trade_date(trade_date)
        results: list[dict[str, Any]] = []
        skipped = 0
        progress = _BatchProgress(
            total_codes,
            update_interval_sec=float(self.config.get("progress_update_sec", 0.5)),
        )

        for idx, code in enumerate(codes, start=1):
            prev_state = chip_dir / f"{code.replace('.', '_')}_{prev_date}.npz" if prev_date else None
            if prev_state is not None and prev_state.exists():
                engine.load_state(code, str(prev_state))
            else:
                daily_df = get_daily_bars([code], end_time=trade_date, count=int(self.config.get("cold_start_lookback", 60)))
                cs_shares = get_total_shares(code)
                cs_atr = calc_atr_10(daily_df) if daily_df is not None and not daily_df.empty else 0.0
                engine.cold_start(code, daily_df, total_shares=cs_shares, atr=cs_atr)

            asr_yesterday = float("nan")
            if prev_date and prev_state is not None and prev_state.exists():
                bars_prev = get_daily_bars([code], end_time=prev_date, count=11)
                if bars_prev is not None and not bars_prev.empty:
                    atr_prev = float(calc_atr_10(bars_prev))
                    close_prev = float(bars_prev["close"].iloc[-1])
                    if np.isfinite(atr_prev) and atr_prev > 0 and np.isfinite(close_prev):
                        asr_yesterday = float(
                            calc_asr(
                                engine.chips[code],
                                close_prev,
                                atr=atr_prev,
                                k=float(self.config.get("asr_k", 1.0)),
                            )
                        )

            try:
                etf_info = get_etf_info(code)
            except Exception as e:
                _warn_runtime_once(
                    f"get_etf_info_failed:{trade_date}:{code}",
                    f"XtData: get_etf_info failed, skip IOPV attach. code={code} date={trade_date} err={repr(e)}",
                )
                etf_info = {}
            if etf_info:
                engine.attach_iopv(code, etf_info)

            ticks = get_market_tick_data(code, trade_date, count=int(self.config.get("daily_tick_count", 2000)))
            snapshots = ticks_to_snapshots(ticks)
            if snapshots.empty:
                ticks_local = get_local_tick_data(code, trade_date)
                snapshots = ticks_to_snapshots(ticks_local)
            if snapshots.empty and retry_download_for_empty_tick_code_once(code, trade_date):
                ticks = get_market_tick_data(code, trade_date, count=int(self.config.get("daily_tick_count", 2000)))
                snapshots = ticks_to_snapshots(ticks)
                if snapshots.empty:
                    ticks_local = get_local_tick_data(code, trade_date)
                    snapshots = ticks_to_snapshots(ticks_local)
            if snapshots.empty:
                skipped += 1
                progress.update(idx, rows=len(results), skipped=skipped, code=code)
                continue
            # V2.1: do NOT delete volume==0 rows 鈥?M0 preprocessor uses
            # full DF + mask to preserve adjacent-diff semantics for OFI.
            if snapshots["volume"].gt(0).sum() == 0:
                skipped += 1
                progress.update(idx, rows=len(results), skipped=skipped, code=code)
                continue
            premium_rates = _resolve_premium_rates(
                code=code,
                trade_date=trade_date,
                snapshots=snapshots,
                engine=engine,
            )
            if premium_rates is not None:
                snapshots["premium_rate"] = premium_rates.to_numpy(dtype=np.float64, copy=False)

            l1_path = l1_dir / f"{code.replace('.', '_')}.parquet"
            if parquet_ok:
                snapshots.to_parquet(l1_path, index=False)
            elif str(self.config.get("l1_fallback_csv", "0")).strip() == "1":
                l1_path = l1_dir / f"{code.replace('.', '_')}.csv"
                snapshots.to_csv(l1_path, index=False, encoding="utf-8-sig")

            atr = calc_atr_10(get_daily_bars([code], end_time=trade_date, count=11))

            shares_today = get_total_shares(code)
            shares_yesterday = engine.chips[code].total_shares if engine.chips[code].total_shares > 0 else shares_today
            engine.chips[code].total_shares = shares_yesterday

            out = engine.process_daily(
                code,
                snapshots,
                shares_today=shares_today,
                shares_yesterday=shares_yesterday,
                atr=atr,
            )
            if premium_rates is None and "premium_rate" in snapshots.columns:
                try:
                    premium_rates = snapshots["premium_rate"].astype(np.float64)
                except Exception as e:
                    _warn_runtime_once(
                        f"premium_rate_cast_failed:{trade_date}:{code}",
                        f"Micro: premium_rate cast failed, downgrade to zeros. code={code} date={trade_date} err={repr(e)}",
                    )
                    premium_rates = pd.Series(np.zeros(len(snapshots), dtype=np.float64), index=snapshots.index)

            # Compute ADV_60 for VPIN cross-day comparability (V2.1)
            bars_60 = get_daily_bars([code], end_time=trade_date, count=61)
            adv_60 = None
            if bars_60 is not None and len(bars_60) > 1:
                # Exclude today (last row if it matches trade_date)
                hist_bars = bars_60.iloc[:-1] if len(bars_60) > 1 else bars_60
                vol_col = hist_bars["volume"] if "volume" in hist_bars.columns else hist_bars.get("vol")
                if vol_col is not None and len(vol_col) > 0:
                    adv_60 = float(vol_col.mean())

            ms_out = ms_engine.process_daily(
                etf_code=code,
                trade_date=trade_date,
                snapshots=snapshots,
                premium_rates=premium_rates,
                adv_60=adv_60,
            )
            ms_raw = ms_out.get("raw") if isinstance(ms_out, dict) else {}
            ms_feat = ms_out.get("features") if isinstance(ms_out, dict) else {}
            ms_meta = ms_out.get("meta") if isinstance(ms_out, dict) else {}

            state_path = chip_dir / f"{code.replace('.', '_')}_{trade_date}.npz"
            engine.save_state(code, str(state_path))

            avg_premium = float(np.nanmean(premium_rates.to_numpy(dtype=np.float64, copy=False))) if premium_rates is not None and len(premium_rates) else 0.0
            vpin_raw = float(ms_raw.get("vpin_raw")) if isinstance(ms_raw, dict) and ms_raw.get("vpin_raw") is not None else float("nan")
            ofi_daily_z = float(ms_feat.get("ofi_daily_z")) if isinstance(ms_feat, dict) and ms_feat.get("ofi_daily_z") is not None else float("nan")
            profit_ratio = float(out.get("profit_ratio")) if out.get("profit_ratio") is not None else float("nan")
            asr_today = float(out.get("asr")) if out.get("asr") is not None else float("nan")
            pv_cross = premium_vpin_cross(avg_premium, vpin_raw)  # V2.1: uses vpin_raw
            pod = profit_ofi_divergence(profit_ratio, ofi_daily_z)
            av = asr_velocity(asr_today, asr_yesterday)  # Returns dict

            results.append(
                {
                    "trade_date": trade_date,
                    "code": code,
                    "profit_ratio": out.get("profit_ratio"),
                    "asr": out.get("asr"),
                    "dense_zones": out.get("dense_zones"),
                    **(ms_raw if isinstance(ms_raw, dict) else {}),
                    **(ms_feat if isinstance(ms_feat, dict) else {}),
                    **(ms_meta if isinstance(ms_meta, dict) else {}),
                    "premium_vpin_cross": pv_cross,
                    "profit_ofi_divergence": pod,
                    **(av if isinstance(av, dict) else {"asr_velocity_1d": av}),
                    "state_path": str(state_path),
                }
            )
            progress.update(idx, rows=len(results), skipped=skipped, code=code)

        if total_codes == 0:
            print("progress [------------------------]    0/0   100.00% rows=   0 skipped=   0 speed= 0.00/s eta=00:00", flush=True)

        df_result = pd.DataFrame(results)

        # 鈹€鈹€ Cross-sectional ranking (V2.1) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        if len(results) > 1 and not df_result.empty:
            try:
                from etf_chip_engine.microstructure.feature_pipeline import FeaturePipeline

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
                info_once("micro_xs_ranking_failed", f"Micro: 妯埅闈㈡帓鍚嶈绠楀け璐ワ紝宸查檷绾ц烦杩? err={repr(e)}", logger_name=__name__)

        return df_result

