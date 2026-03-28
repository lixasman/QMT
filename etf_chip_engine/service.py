from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
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
    ensure_daily_history_downloaded,
    filter_etf_codes_by_keywords,
    filter_etf_codes_by_liquidity,
    get_daily_bars,
    download_constituent_close_prices,
    get_etf_info,
    get_industry_etf_universe,
    get_local_tick_data,
    get_market_tick_data,
    get_total_shares,
    get_total_shares_detail,
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


def _trade_date_from_any(v: Any) -> str:
    m = re.search(r"(\d{8})", str(v or ""))
    return m.group(1) if m else ""


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
    expected_last_trade_date: str = "",
    context: str,
) -> pd.DataFrame:
    keep_n = max(int(count), 0)
    query_count = keep_n + 1 if keep_n > 0 else 1
    daily_df = get_daily_bars([code], end_time=trade_date, count=query_count)
    hist = _history_before_trade_date(daily_df, trade_date=trade_date, keep_count=keep_n)
    expected_td = str(expected_last_trade_date or "").strip()
    if expected_td:
        last_td = _trade_date_from_any(hist["time"].iloc[-1]) if (not hist.empty and "time" in hist.columns) else ""
        if last_td != expected_td:
            timeout_sec = int(CONFIG.get("daily_history_download_timeout_sec", 20) or 20)
            poll_attempts = max(int(CONFIG.get("daily_history_download_poll_attempts", 4) or 4), 1)
            poll_sleep_sec = float(CONFIG.get("daily_history_download_poll_sleep_sec", 0.5) or 0.5)
            try:
                ensure_daily_history_downloaded([code], expected_td, timeout_sec=timeout_sec)
            except Exception as e:
                _warn_runtime_once(
                    f"daily_history_auto_download_failed:{expected_td}:{code}",
                    (
                        "ETF: stale daily history auto-download failed."
                        f" code={code} target_date={expected_td} context={context}"
                        f" err={repr(e)}"
                        ),
                    )
            for attempt in range(poll_attempts):
                if attempt > 0:
                    time.sleep(max(poll_sleep_sec, 0.0))
                daily_df = get_daily_bars([code], end_time=trade_date, count=query_count)
                hist = _history_before_trade_date(daily_df, trade_date=trade_date, keep_count=keep_n)
                last_td = _trade_date_from_any(hist["time"].iloc[-1]) if (not hist.empty and "time" in hist.columns) else ""
                if last_td == expected_td:
                    break
        if last_td != expected_td:
            raise RuntimeError(
                f"stale daily history: context={context} trade_date={trade_date}"
                f" expected_last={expected_td} last_daily={last_td or 'missing'}"
            )
    return hist


def _assert_trade_date_bar_fresh(daily_df: pd.DataFrame, *, trade_date: str, context: str) -> None:
    last_td = _trade_date_from_any(daily_df["time"].iloc[-1]) if (daily_df is not None and not daily_df.empty and "time" in daily_df.columns) else ""
    if last_td != str(trade_date):
        raise RuntimeError(
            f"stale daily bar: context={context} trade_date={trade_date} last_daily={last_td or 'missing'}"
        )


def _load_trade_date_daily_bars(
    *,
    code: str,
    trade_date: str,
    count: int,
    snapshots: pd.DataFrame,
    context: str,
) -> pd.DataFrame:
    query_count = max(int(count), 1)
    daily_df = get_daily_bars([code], end_time=trade_date, count=query_count)
    effective = _ensure_trade_date_daily_bar(daily_df, trade_date=trade_date, snapshots=snapshots)
    last_td = _trade_date_from_any(effective["time"].iloc[-1]) if (effective is not None and not effective.empty and "time" in effective.columns) else ""
    if last_td != str(trade_date):
        timeout_sec = int(CONFIG.get("daily_history_download_timeout_sec", 20) or 20)
        poll_attempts = max(int(CONFIG.get("daily_history_download_poll_attempts", 4) or 4), 1)
        poll_sleep_sec = float(CONFIG.get("daily_history_download_poll_sleep_sec", 0.5) or 0.5)
        try:
            ensure_daily_history_downloaded([code], trade_date, timeout_sec=timeout_sec)
        except Exception as e:
            _warn_runtime_once(
                f"daily_bar_auto_download_failed:{trade_date}:{code}",
                (
                    "ETF: stale daily bar auto-download failed."
                    f" code={code} target_date={trade_date} context={context}"
                    f" err={repr(e)}"
                ),
            )
        for attempt in range(poll_attempts):
            if attempt > 0:
                time.sleep(max(poll_sleep_sec, 0.0))
            daily_df = get_daily_bars([code], end_time=trade_date, count=query_count)
            effective = _ensure_trade_date_daily_bar(daily_df, trade_date=trade_date, snapshots=snapshots)
            last_td = _trade_date_from_any(effective["time"].iloc[-1]) if (effective is not None and not effective.empty and "time" in effective.columns) else ""
            if last_td == str(trade_date):
                break
    if last_td != str(trade_date):
        raise RuntimeError(
            f"stale daily bar: context={context} trade_date={trade_date} last_daily={last_td or 'missing'}"
        )
    return effective


def _compute_adv_60(*, code: str, trade_date: str, prev_trade_date: str) -> Optional[float]:
    try:
        hist_bars = _load_daily_history_before_trade_date(
            code=code,
            trade_date=trade_date,
            count=60,
            expected_last_trade_date=str(prev_trade_date or ""),
            context=f"etf:{code}:adv60",
        )
    except RuntimeError as e:
        _warn_runtime_once(
            f"etf_adv60_history_stale:{trade_date}:{code}",
            f"ETF: ADV60 daily history stale, fallback disabled. code={code} date={trade_date} err={e}",
        )
        return None
    except Exception:
        return None

    if hist_bars is None or hist_bars.empty:
        return None
    vol_col = hist_bars["volume"] if "volume" in hist_bars.columns else hist_bars.get("vol")
    if vol_col is None or len(vol_col) <= 0:
        return None
    try:
        return float(pd.to_numeric(vol_col, errors="coerce").dropna().mean())
    except Exception:
        return None


def _normalize_codes(codes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in codes:
        s = str(c or "").strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _extract_etf_name_from_info(info: Any) -> str:
    if not isinstance(info, dict):
        return ""
    for key in ("name", "etf_name", "instrument_name", "InstrumentName"):
        v = info.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _codes_scope_id(codes: list[str]) -> str:
    norm = _normalize_codes(codes)
    payload = ",".join(sorted(norm)).encode("utf-8", errors="ignore")
    return hashlib.sha1(payload).hexdigest()


def _parse_yyyymmdd(v: str) -> Optional[datetime]:
    s = str(v or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d")
    except Exception:
        return None


def _trade_date_age_days(*, built_trade_date: str, current_trade_date: str) -> Optional[int]:
    d0 = _parse_yyyymmdd(built_trade_date)
    d1 = _parse_yyyymmdd(current_trade_date)
    if d0 is None or d1 is None:
        return None
    return int((d1.date() - d0.date()).days)


def _liquidity_rules_from_config(cfg: dict[str, object]) -> dict[str, Any]:
    return {
        "lookback_days": int(cfg.get("liquidity_prefilter_lookback_days", 60)),
        "min_active_days": int(cfg.get("liquidity_prefilter_min_active_days", 45)),
        "min_median_amount": float(cfg.get("liquidity_prefilter_min_median_amount", 2_000_000.0)),
        "min_median_volume": float(cfg.get("liquidity_prefilter_min_median_volume", 0.0)),
        "chunk_size": int(cfg.get("liquidity_prefilter_chunk_size", 400)),
    }


def _load_stable_pool_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_stable_pool_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def _resolve_premium_rates(
    *,
    code: str,
    trade_date: str,
    snapshots: pd.DataFrame,
    engine: ETFChipEngine,
    min_iopv_coverage: float = 0.95,
    etf_name: str = "",
    iopv_coverage_downgrade_sink: Optional[list[dict[str, Any]]] = None,
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

    coverage = float("nan")
    try:
        coverage = float(calc.get_coverage())
    except Exception as e:
        _warn_runtime_once(
            f"iopv_coverage_failed:{trade_date}:{code}",
            f"IOPV: failed to read coverage, downgraded. code={code} date={trade_date} err={repr(e)}",
        )

    coverage_threshold = float(min(max(float(min_iopv_coverage), 0.0), 1.0))
    if not np.isfinite(coverage):
        if iopv_coverage_downgrade_sink is not None:
            iopv_coverage_downgrade_sink.append(
                {
                    "code": str(code),
                    "name": str(etf_name or ""),
                    "coverage": None,
                    "threshold": float(coverage_threshold),
                    "reason": "coverage_unavailable",
                }
            )
        return pd.Series(np.zeros(len(snapshots), dtype=np.float64), index=snapshots.index)

    if coverage < coverage_threshold:
        _warn_runtime_once(
            f"iopv_coverage_below_threshold:{trade_date}:{code}",
            (
                "IOPV: coverage below threshold, premium_rate downgraded to zeros."
                f" code={code} name={etf_name or '-'} date={trade_date}"
                f" coverage={coverage:.4f} threshold={coverage_threshold:.4f}"
            ),
        )
        if iopv_coverage_downgrade_sink is not None:
            iopv_coverage_downgrade_sink.append(
                {
                    "code": str(code),
                    "name": str(etf_name or ""),
                    "coverage": round(float(coverage), 6),
                    "threshold": float(coverage_threshold),
                    "reason": "coverage_below_threshold",
                }
            )
        return pd.Series(np.zeros(len(snapshots), dtype=np.float64), index=snapshots.index)

    closes = pd.to_numeric(snapshots["close"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
    prem = (closes - iopv_v) / iopv_v
    _warn_runtime_once(
        f"premium_rate_fallback_iopv:{trade_date}:{code}",
        f"Micro: using IOPV fallback premium_rate. code={code} date={trade_date} iopv={iopv_v:.6f} coverage={coverage:.4f}",
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
        code_name_map: dict[str, str] = {}
        if codes is not None:
            codes_from_input = True
            codes = [str(c).strip() for c in codes if str(c).strip()]
        else:
            codes_from_input = False
            df_univ = get_industry_etf_universe()
            codes = df_univ["code"].astype(str).tolist()
            if "name" in df_univ.columns:
                for code_v, name_v in zip(df_univ["code"].astype(str).tolist(), df_univ["name"].astype(str).tolist()):
                    code_k = str(code_v).strip().upper()
                    name_k = str(name_v).strip()
                    if code_k and name_k:
                        code_name_map[code_k] = name_k
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

        liq_prefilter_enabled = _as_bool(self.config.get("liquidity_prefilter_enabled", True), default=True)
        if liq_prefilter_enabled and codes:
            liq_rules = _liquidity_rules_from_config(self.config)
            stable_pool_enabled = _as_bool(
                self.config.get("liquidity_prefilter_stable_pool_enabled", True),
                default=True,
            )
            refresh_days = max(int(self.config.get("liquidity_prefilter_pool_refresh_days", 30)), 1)
            state_path = Path(
                str(
                    self.config.get(
                        "liquidity_prefilter_pool_state_path",
                        Path("output") / "cache" / "chip_compute_pool" / "stable_pool.json",
                    )
                )
            )
            scope_id = _codes_scope_id(codes)

            liq_stats: dict[str, Any]
            if stable_pool_enabled:
                state = _load_stable_pool_state(state_path)
                can_reuse = False
                reuse_reason = "state_invalid"
                age_days: Optional[int] = None
                try:
                    state_scope = str(state.get("scope_id", ""))
                    state_rules = state.get("rules", {})
                    state_codes = _normalize_codes(state.get("kept_codes", []))
                    state_trade_date = str(state.get("trade_date", ""))
                    age_days = _trade_date_age_days(built_trade_date=state_trade_date, current_trade_date=str(trade_date))
                    if (
                        state_scope == scope_id
                        and isinstance(state_rules, dict)
                        and state_rules == liq_rules
                        and len(state_codes) > 0
                        and age_days is not None
                        and age_days >= 0
                        and age_days <= refresh_days
                    ):
                        can_reuse = True
                        reuse_reason = "within_refresh_window"
                except Exception:
                    can_reuse = False
                    reuse_reason = "state_parse_failed"

                if can_reuse:
                    kept_set = set(_normalize_codes(state.get("kept_codes", [])))
                    src_codes = _normalize_codes(codes)
                    kept_codes = [c for c in src_codes if c in kept_set]
                    removed_codes = [c for c in src_codes if c not in kept_set]
                    liq_stats = {
                        "mode": "stable_reuse",
                        "reason": reuse_reason,
                        "trade_date": str(trade_date),
                        "state_path": str(state_path),
                        "state_trade_date": str(state.get("trade_date", "")),
                        "state_updated_at": str(state.get("updated_at", "")),
                        "refresh_days": int(refresh_days),
                        "age_days": (int(age_days) if age_days is not None else None),
                        "input_count": int(len(src_codes)),
                        "kept_count": int(len(kept_codes)),
                        "removed_count": int(len(removed_codes)),
                        "fallback_kept_count": 0,
                        "failed_chunks": 0,
                        **liq_rules,
                        "kept_codes": kept_codes,
                        "removed_codes": removed_codes,
                        "removed_samples": [{"code": c} for c in removed_codes[:12]],
                    }
                else:
                    liq_stats = filter_etf_codes_by_liquidity(
                        codes,
                        trade_date=trade_date,
                        lookback_days=int(liq_rules["lookback_days"]),
                        min_active_days=int(liq_rules["min_active_days"]),
                        min_median_amount=float(liq_rules["min_median_amount"]),
                        min_median_volume=float(liq_rules["min_median_volume"]),
                        chunk_size=int(liq_rules["chunk_size"]),
                    )
                    liq_stats["mode"] = "stable_rebuild"
                    liq_stats["reason"] = reuse_reason
                    liq_stats["state_path"] = str(state_path)
                    liq_stats["refresh_days"] = int(refresh_days)
                    liq_stats["age_days"] = (int(age_days) if age_days is not None else None)
                    try:
                        state_payload = {
                            "version": 1,
                            "trade_date": str(trade_date),
                            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "scope_id": scope_id,
                            "refresh_days": int(refresh_days),
                            "rules": liq_rules,
                            "kept_codes": _normalize_codes(liq_stats.get("kept_codes", [])),
                        }
                        _save_stable_pool_state(state_path, state_payload)
                    except Exception as e:
                        _warn_runtime_once(
                            f"liquidity_stable_pool_save_failed:{trade_date}",
                            (
                                "Liquidity prefilter: stable pool state 保存失败，已降级继续。"
                                f" path={state_path} err={repr(e)}"
                            ),
                        )
            else:
                liq_stats = filter_etf_codes_by_liquidity(
                    codes,
                    trade_date=trade_date,
                    lookback_days=int(liq_rules["lookback_days"]),
                    min_active_days=int(liq_rules["min_active_days"]),
                    min_median_amount=float(liq_rules["min_median_amount"]),
                    min_median_volume=float(liq_rules["min_median_volume"]),
                    chunk_size=int(liq_rules["chunk_size"]),
                )
                liq_stats["mode"] = "dynamic"
                liq_stats["reason"] = "stable_pool_disabled"
                liq_stats["state_path"] = ""
                liq_stats["refresh_days"] = 0
                liq_stats["age_days"] = None

            codes = [str(c) for c in liq_stats.get("kept_codes", []) if str(c).strip()]
            print(
                json.dumps(
                    {
                        "timing": "etf_chip_engine.service.code_liquidity_filter",
                        "mode": str(liq_stats.get("mode", "")),
                        "reason": str(liq_stats.get("reason", "")),
                        "trade_date": str(trade_date),
                        "input_count": int(liq_stats.get("input_count", 0)),
                        "kept_count": int(liq_stats.get("kept_count", 0)),
                        "removed_count": int(liq_stats.get("removed_count", 0)),
                        "fallback_kept_count": int(liq_stats.get("fallback_kept_count", 0)),
                        "failed_chunks": int(liq_stats.get("failed_chunks", 0)),
                        "lookback_days": int(liq_stats.get("lookback_days", 0)),
                        "min_active_days": int(liq_stats.get("min_active_days", 0)),
                        "min_median_amount": float(liq_stats.get("min_median_amount", 0.0)),
                        "min_median_volume": float(liq_stats.get("min_median_volume", 0.0)),
                        "state_path": str(liq_stats.get("state_path", "")),
                        "refresh_days": int(liq_stats.get("refresh_days", 0)),
                        "age_days": liq_stats.get("age_days"),
                        "sample_removed": liq_stats.get("removed_samples", []),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        etf_info_cache: dict[str, dict[str, Any]] = {}
        max_constituents = int(self.config.get("industry_etf_max_constituents", 200))
        min_a_share_ratio = float(self.config.get("industry_etf_min_a_share_ratio", 0.95))
        min_a_share_ratio = float(min(max(min_a_share_ratio, 0.0), 1.0))
        if (max_constituents > 0 or min_a_share_ratio > 0) and codes:
            kept_codes: list[str] = []
            removed_by_constituents = 0
            removed_by_a_share_ratio = 0
            unresolved_constituents = 0
            for etf_code in codes:
                try:
                    info = get_etf_info(etf_code)
                except Exception as e:
                    _warn_runtime_once(
                        f"get_etf_info_failed_admission:{trade_date}:{etf_code}",
                        (
                            "XtData: get_etf_info failed in admission filter, keep code by default."
                            f" code={etf_code} date={trade_date} err={repr(e)}"
                        ),
                    )
                    info = {}
                etf_info_cache[etf_code] = info if isinstance(info, dict) else {}

                stocks_dict = info.get("stocks") if isinstance(info, dict) else None
                if not isinstance(stocks_dict, dict) or not stocks_dict:
                    unresolved_constituents += 1
                    kept_codes.append(etf_code)
                    continue

                constituent_count = 0
                a_share_count = 0
                for k, v in stocks_dict.items():
                    if not isinstance(k, str) or not isinstance(v, dict):
                        continue
                    constituent_count += 1
                    ku = str(k).upper()
                    if ku.endswith((".SH", ".SZ", ".BJ")):
                        a_share_count += 1

                if constituent_count <= 0:
                    unresolved_constituents += 1
                    kept_codes.append(etf_code)
                    continue

                if max_constituents > 0 and constituent_count > max_constituents:
                    removed_by_constituents += 1
                    print(
                        json.dumps(
                            {
                                "timing": "etf_chip_engine.service.admission_filter_skip",
                                "trade_date": str(trade_date),
                                "code": str(etf_code),
                                "reason": "constituent_count_exceed",
                                "constituent_count": int(constituent_count),
                                "max_constituents": int(max_constituents),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    continue

                a_share_ratio = float(a_share_count) / float(max(constituent_count, 1))
                if min_a_share_ratio > 0 and a_share_ratio < min_a_share_ratio:
                    removed_by_a_share_ratio += 1
                    non_a_share_count = max(constituent_count - a_share_count, 0)
                    print(
                        json.dumps(
                            {
                                "timing": "etf_chip_engine.service.admission_filter_skip",
                                "trade_date": str(trade_date),
                                "code": str(etf_code),
                                "reason": "a_share_ratio_low",
                                "constituent_count": int(constituent_count),
                                "a_share_count": int(a_share_count),
                                "non_a_share_count": int(non_a_share_count),
                                "a_share_ratio": round(float(a_share_ratio), 6),
                                "min_a_share_ratio": round(float(min_a_share_ratio), 6),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    continue

                kept_codes.append(etf_code)

            if removed_by_constituents > 0 or removed_by_a_share_ratio > 0 or unresolved_constituents > 0:
                print(
                    json.dumps(
                        {
                            "timing": "etf_chip_engine.service.admission_filter_summary",
                            "trade_date": str(trade_date),
                            "input_count": int(len(codes)),
                            "kept_count": int(len(kept_codes)),
                            "removed_by_constituents": int(removed_by_constituents),
                            "removed_by_a_share_ratio": int(removed_by_a_share_ratio),
                            "unresolved_constituents": int(unresolved_constituents),
                            "max_constituents": int(max_constituents),
                            "min_a_share_ratio": round(float(min_a_share_ratio), 6),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            codes = kept_codes

        total_codes = len(codes)
        min_iopv_coverage = float(
            min(max(float(self.config.get("premium_iopv_min_coverage", 0.95)), 0.0), 1.0)
        )
        iopv_coverage_downgraded: list[dict[str, Any]] = []
        print(f"trade_date={trade_date} universe={total_codes}", flush=True)
        download_stats = ensure_tick_data_downloaded(
            codes,
            trade_date,
            force=bool(force_download),
            chunk_size=int(self.config.get("tick_download_chunk_size", 80)),
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
        if not str(prev_date).strip():
            _warn_runtime_once(
                f"prev_trade_date_unavailable:{trade_date}",
                f"Chip: 无法解析前一交易日，当前批次将按冷启动处理。trade_date={trade_date}",
            )
        results: list[dict[str, Any]] = []
        skipped = 0
        prev_state_loaded = 0
        prev_state_missing = 0
        prev_state_load_failed = 0
        prev_date_unavailable = 0
        _constituent_px_cache: dict[str, float] = {}  # 跨 ETF 共享成分股收盘价缓存
        progress = _BatchProgress(
            total_codes,
            update_interval_sec=float(self.config.get("progress_update_sec", 0.5)),
        )

        def _cold_start_from_daily(etf_code: str) -> None:
            daily_df = _load_daily_history_before_trade_date(
                code=etf_code,
                trade_date=trade_date,
                count=int(self.config.get("cold_start_lookback", 60)),
                expected_last_trade_date=str(prev_date or ""),
                context=f"etf:{etf_code}:cold_start",
            )
            cs_detail = get_total_shares_detail(etf_code, trade_date=trade_date)
            cs_shares = float(cs_detail.get("shares", 0.0))
            cs_source = str(cs_detail.get("source", "none"))
            if not cs_source.startswith("official_"):
                _warn_runtime_once(
                    f"shares_source_fallback_cold_start:{trade_date}:{etf_code}",
                    (
                        "Shares: 官方份额不可用，冷启动已降级。"
                        f" code={etf_code} date={trade_date} source={cs_source}"
                        f" reason={cs_detail.get('reason', '')}"
                    ),
                )
            cs_atr = calc_atr_10(daily_df) if daily_df is not None and not daily_df.empty else 0.0
            engine.cold_start(etf_code, daily_df, total_shares=cs_shares, atr=cs_atr)

        for idx, code in enumerate(codes, start=1):
            loaded_prev_state = False
            prev_state = chip_dir / f"{code.replace('.', '_')}_{prev_date}.npz" if prev_date else None
            if prev_date and prev_state is not None and prev_state.exists():
                try:
                    engine.load_state(code, str(prev_state))
                    loaded_prev_state = True
                    prev_state_loaded += 1
                except Exception as e:
                    prev_state_load_failed += 1
                    _warn_runtime_once(
                        f"prev_state_load_failed:{trade_date}:{code}",
                        (
                            "Chip: 读取前一交易日状态失败，已降级冷启动。"
                            f" code={code} date={trade_date} prev_date={prev_date}"
                            f" state={prev_state} err={repr(e)}"
                        ),
                    )
                    _cold_start_from_daily(code)
            else:
                if prev_date:
                    prev_state_missing += 1
                    _warn_runtime_once(
                        f"prev_state_missing:{trade_date}",
                        (
                            "Chip: 前一交易日状态文件缺失，已降级冷启动。"
                            f" date={trade_date} prev_date={prev_date}"
                            f" first_code={code} expected_state={prev_state}"
                        ),
                    )
                else:
                    prev_date_unavailable += 1
                _cold_start_from_daily(code)

            asr_yesterday = float("nan")
            if loaded_prev_state and prev_date:
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

            if code in etf_info_cache:
                etf_info = etf_info_cache.get(code, {})
            else:
                try:
                    etf_info = get_etf_info(code)
                except Exception as e:
                    _warn_runtime_once(
                        f"get_etf_info_failed:{trade_date}:{code}",
                        f"XtData: get_etf_info failed, skip IOPV attach. code={code} date={trade_date} err={repr(e)}",
                    )
                    etf_info = {}
                etf_info_cache[code] = etf_info if isinstance(etf_info, dict) else {}
            etf_name = code_name_map.get(code, "")
            if not etf_name:
                etf_name = _extract_etf_name_from_info(etf_info)
                if etf_name:
                    code_name_map[code] = etf_name
            if etf_info:
                engine.attach_iopv(code, etf_info)
                # ── 拉取成分股收盘价驱动 IOPV 计算（避免 nav 兜底） ──
                stocks_dict = etf_info.get("stocks")
                if isinstance(stocks_dict, dict) and stocks_dict:
                    _SUPPORTED_MARKETS = (".SH", ".SZ", ".BJ")
                    comp_codes = [
                        str(k) for k in stocks_dict
                        if isinstance(k, str) and str(k).upper().endswith(_SUPPORTED_MARKETS)
                    ]
                    missing = [c for c in comp_codes if c not in _constituent_px_cache]
                    if missing:
                        try:
                            t_fetch0 = time.perf_counter()
                            print(
                                json.dumps(
                                    {
                                        "timing": "etf_chip_engine.service.constituent_fetch_start",
                                        "trade_date": str(trade_date),
                                        "code": str(code),
                                        "missing_codes": int(len(missing)),
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                            new_prices = download_constituent_close_prices(
                                missing,
                                trade_date,
                                timeout_sec=int(self.config.get("constituent_download_timeout_sec", 8)),
                                retry_chunk_size=int(self.config.get("constituent_retry_chunk_size", 30)),
                                skip_full_if_codes_ge=int(self.config.get("constituent_skip_full_if_codes_ge", 220)),
                            )
                            _constituent_px_cache.update(new_prices)
                            elapsed = max(time.perf_counter() - t_fetch0, 0.0)
                            print(
                                json.dumps(
                                    {
                                        "timing": "etf_chip_engine.service.constituent_fetch_done",
                                        "trade_date": str(trade_date),
                                        "code": str(code),
                                        "missing_codes": int(len(missing)),
                                        "fetched_codes": int(len(new_prices)),
                                        "elapsed_sec": round(float(elapsed), 3),
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                        except Exception as e:
                            _warn_runtime_once(
                                f"constituent_price_fetch_failed:{trade_date}:{code}",
                                f"IOPV: 成分股收盘价拉取失败: code={code} date={trade_date} err={repr(e)}",
                            )
                    calc = engine.iopv.get(code)
                    if calc is not None:
                        for sc in comp_codes:
                            px = _constituent_px_cache.get(sc)
                            if px is not None:
                                calc.update_stock_price(sc, px)

            tick_count = int(self.config.get("daily_tick_count", -1))

            def _load_snapshots_once() -> pd.DataFrame:
                ticks = get_market_tick_data(code, trade_date, count=tick_count)
                snaps = ticks_to_snapshots(ticks)
                if snaps.empty:
                    ticks_local = get_local_tick_data(code, trade_date)
                    snaps = ticks_to_snapshots(ticks_local)
                return snaps

            snapshots = _load_snapshots_once()
            if snapshots.empty and retry_download_for_empty_tick_code_once(
                code,
                trade_date,
                timeout_sec=int(self.config.get("empty_tick_retry_timeout_sec", 20)),
            ):
                snapshots = _load_snapshots_once()
            if snapshots.empty:
                wait_sec = float(max(float(self.config.get("empty_tick_post_retry_wait_sec", 6.0)), 0.0))
                poll_sec = float(max(float(self.config.get("empty_tick_post_retry_poll_sec", 1.0)), 0.2))
                if wait_sec > 0:
                    t_wait0 = time.perf_counter()
                    deadline = t_wait0 + wait_sec
                    poll_count = 0
                    while snapshots.empty and time.perf_counter() < deadline:
                        sleep_s = min(poll_sec, max(deadline - time.perf_counter(), 0.0))
                        if sleep_s > 0:
                            time.sleep(sleep_s)
                        poll_count += 1
                        snapshots = _load_snapshots_once()
                    waited = max(time.perf_counter() - t_wait0, 0.0)
                    print(
                        json.dumps(
                            {
                                "timing": "etf_chip_engine.service.empty_tick_post_retry_poll",
                                "trade_date": str(trade_date),
                                "code": str(code),
                                "waited_sec": round(float(waited), 3),
                                "poll_count": int(poll_count),
                                "recovered": bool(not snapshots.empty),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
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
                min_iopv_coverage=min_iopv_coverage,
                etf_name=etf_name,
                iopv_coverage_downgrade_sink=iopv_coverage_downgraded,
            )
            if premium_rates is not None:
                snapshots["premium_rate"] = premium_rates.to_numpy(dtype=np.float64, copy=False)

            l1_path = l1_dir / f"{code.replace('.', '_')}.parquet"
            if parquet_ok:
                snapshots.to_parquet(l1_path, index=False)
            elif str(self.config.get("l1_fallback_csv", "0")).strip() == "1":
                l1_path = l1_dir / f"{code.replace('.', '_')}.csv"
                snapshots.to_csv(l1_path, index=False, encoding="utf-8-sig")

            daily_11_effective = _load_trade_date_daily_bars(
                code=code,
                trade_date=trade_date,
                count=11,
                snapshots=snapshots,
                context=f"etf:{code}:atr",
            )
            atr = calc_atr_10(daily_11_effective)

            shares_detail = get_total_shares_detail(code, trade_date=trade_date)
            shares_today = float(shares_detail.get("shares", 0.0))
            shares_source = str(shares_detail.get("source", "none"))
            if not shares_source.startswith("official_"):
                _warn_runtime_once(
                    f"shares_source_fallback:{trade_date}:{code}",
                    (
                        "Shares: 官方份额不可用，已降级。"
                        f" code={code} date={trade_date} source={shares_source}"
                        f" reason={shares_detail.get('reason', '')}"
                    ),
                )
            if shares_today <= 0 and isinstance(snapshots, pd.DataFrame) and not snapshots.empty:
                try:
                    total_amt = snapshots["amount"].astype(float).sum()
                    total_vol = snapshots["volume"].astype(float).sum()
                    if total_vol > 0 and total_amt > 0:
                        # Last-resort fallback: very low confidence estimation.
                        assumed_daily_tr = 0.05
                        shares_today = total_vol / assumed_daily_tr
                        _warn_runtime_once(
                            f"shares_source_last_resort:{trade_date}:{code}",
                            (
                                "Shares: 官方+xtdata 均不可用，已使用低置信度换手率兜底。"
                                f" code={code} date={trade_date} total_vol={total_vol:.2f}"
                                f" assumed_daily_tr={assumed_daily_tr:.4f}"
                            ),
                        )
                except Exception:
                    pass
            shares_yesterday = engine.chips[code].total_shares if engine.chips[code].total_shares > 0 else shares_today
            engine.chips[code].total_shares = shares_yesterday

            try:
                out = engine.process_daily(
                    code,
                    snapshots,
                    shares_today=shares_today,
                    shares_yesterday=shares_yesterday,
                    atr=atr,
                )
            except ValueError as e:
                _warn_runtime_once(
                    f"process_daily_skip:{trade_date}:{code}",
                    f"process_daily 跳过: code={code} date={trade_date} err={e}",
                )
                skipped += 1
                continue
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
            adv_60 = _compute_adv_60(code=code, trade_date=trade_date, prev_trade_date=str(prev_date or ""))

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

        print(
            json.dumps(
                {
                    "timing": "etf_chip_engine.service.prev_state_load_summary",
                    "trade_date": str(trade_date),
                    "prev_date": str(prev_date),
                    "total_codes": int(total_codes),
                    "loaded_count": int(prev_state_loaded),
                    "missing_count": int(prev_state_missing),
                    "load_failed_count": int(prev_state_load_failed),
                    "prev_date_unavailable_count": int(prev_date_unavailable),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        downgraded_by_code: dict[str, dict[str, Any]] = {}
        for item in iopv_coverage_downgraded:
            code_k = str(item.get("code", "")).strip().upper()
            if not code_k:
                continue
            old = downgraded_by_code.get(code_k)
            coverage_v = item.get("coverage")
            if old is None:
                downgraded_by_code[code_k] = dict(item)
                continue
            if old.get("coverage") is None and coverage_v is not None:
                downgraded_by_code[code_k] = dict(item)

        downgraded_items = [
            {
                "code": code_k,
                "name": str(v.get("name", "")).strip(),
                "coverage": v.get("coverage"),
            }
            for code_k, v in sorted(downgraded_by_code.items(), key=lambda kv: kv[0])
        ]
        print(
            json.dumps(
                {
                    "timing": "etf_chip_engine.service.iopv_coverage_downgrade_summary",
                    "trade_date": str(trade_date),
                    "threshold": float(min_iopv_coverage),
                    "count": int(len(downgraded_items)),
                    "items": downgraded_items,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

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
