from __future__ import annotations

import io
import logging
import json
import re
import subprocess
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from etf_chip_engine.config import ETF_EXCLUDE_KEYWORDS
from core.warn_utils import warn_once
from core.xtdata_parsing import xtdata_field_dict_to_df


try:
    from xtquant import xtdata as _xtdata  # type: ignore
except Exception:  # pragma: no cover
    _xtdata = None

try:
    import requests as _requests  # type: ignore
except Exception:  # pragma: no cover
    _requests = None


_TICK_DOWNLOAD_STATE_VERSION = 1
_DEFAULT_TICK_STATE_DIR = Path("output") / "cache" / "chip_tick_download"
_LOT_SIZE = 100.0
_OFFICIAL_SHARE_CACHE: dict[str, dict[str, float]] = {}
_OFFICIAL_SHARE_CACHE_ERR: dict[str, str] = {}


def xtdata_available() -> bool:
    return _xtdata is not None


def require_xtdata() -> Any:
    if _xtdata is None:
        raise RuntimeError("xtdata 不可用：请在 QMT/XtQuant 环境中运行")
    return _xtdata


def normalize_etf_code(code: str) -> str:
    s = str(code).strip().upper()
    if re.fullmatch(r"\d{6}\.(SZ|SH|BJ)", s):
        return s
    m1 = re.fullmatch(r"(SZ|SH|BJ)[\.\-_:]?(\d{6})", s)
    if m1:
        return f"{m1.group(2)}.{m1.group(1)}"
    m2 = re.fullmatch(r"(\d{6})[\.\-_:]?(SZ|SH|BJ)", s)
    if m2:
        return f"{m2.group(1)}.{m2.group(2)}"
    m = re.search(r"(\d{6})", s)
    if not m:
        return ""
    code6 = m.group(1)
    # 北交所常见代码段（如 920xxx）需要保留为 BJ，避免被误归并到 SH。
    if code6.startswith("92"):
        return f"{code6}.BJ"
    if code6.startswith(("5", "6", "9")):
        return f"{code6}.SH"
    return f"{code6}.SZ"


def _normalize_trade_date_yyyymmdd(trade_date: str) -> str:
    s = str(trade_date or "").strip()
    if not s:
        return ""
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", s)
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


def _to_sse_stat_date(trade_date: str) -> str:
    td = _normalize_trade_date_yyyymmdd(trade_date)
    if not td:
        return ""
    return f"{td[:4]}-{td[4:6]}-{td[6:]}"


def _try_parse_positive_float(v: Any) -> float:
    try:
        x = float(str(v).replace(",", ""))
    except Exception:
        return 0.0
    if np.isfinite(x) and x > 0:
        return float(x)
    return 0.0


def _fetch_sse_official_share_map(*, trade_date: str = "") -> tuple[dict[str, float], str]:
    """Fetch SSE ETF shares from official endpoint.
    Returns (share_map, error_code). share_map values are in shares.
    """
    if _requests is None:
        warn_once("official_sse_requests_missing", "Shares: requests 不可用，无法拉取上交所官方份额，已降级。")
        return {}, "requests_missing"
    base_params = {
        "isPagination": "true",
        "pageHelp.pageSize": "2000",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
    }
    stat_date = _to_sse_stat_date(trade_date)
    headers = {
        "Referer": "https://www.sse.com.cn/",
        "User-Agent": "Mozilla/5.0",
    }
    def _request_once(params: dict[str, str], tag: str) -> tuple[dict[str, float], str]:
        resp = None
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                resp = _requests.get(
                    "https://query.sse.com.cn/commonQuery.do",
                    params=params,
                    headers=headers,
                    timeout=20,
                )
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                continue
        if resp is None:
            warn_once(
                f"official_sse_request_failed:{tag}",
                f"Shares: 上交所官方份额请求失败，已降级。date={tag} err={repr(last_exc)}",
            )
            return {}, "request_failed"
        if int(resp.status_code) != 200:
            warn_once(
                f"official_sse_http_failed:{tag}",
                f"Shares: 上交所官方份额返回异常状态，已降级。date={tag} status={resp.status_code}",
            )
            return {}, f"http_{resp.status_code}"
        try:
            data_json = resp.json()
        except Exception as e:
            warn_once(
                f"official_sse_json_failed:{tag}",
                f"Shares: 上交所官方份额解析失败，已降级。date={tag} err={repr(e)}",
            )
            return {}, "json_failed"
        result = data_json.get("result") if isinstance(data_json, dict) else None
        if not isinstance(result, list):
            return {}, "result_invalid"
        out: dict[str, float] = {}
        for row in result:
            if not isinstance(row, dict):
                continue
            sec = str(row.get("SEC_CODE", "")).strip()
            if not re.fullmatch(r"\d{6}", sec):
                continue
            # SSE API TOT_VOL unit is 万份 -> convert to shares.
            shares = _try_parse_positive_float(row.get("TOT_VOL")) * 10000.0
            if shares > 0:
                out[f"{sec}.SH"] = float(shares)
        if not out:
            return {}, "empty_result"
        return out, ""

    params = dict(base_params)
    if stat_date:
        params["STAT_DATE"] = stat_date
    out, err = _request_once(params, stat_date or "latest")
    if out:
        return out, ""
    if stat_date and err == "empty_result":
        # When requested date is not published yet, fallback to latest official snapshot.
        latest_out, latest_err = _request_once(dict(base_params), "latest")
        if latest_out:
            warn_once(
                f"official_sse_date_fallback:{stat_date}",
                f"Shares: 上交所指定日期份额为空，已回退最新官方可用数据。date={stat_date}",
            )
            return latest_out, f"fallback_latest:{stat_date}"
        return {}, f"{err};latest:{latest_err}"
    return {}, err


def _fetch_szse_official_share_map() -> tuple[dict[str, float], str]:
    """Fetch SZSE ETF shares from official endpoint.
    Returns (share_map, error_code). share_map values are in shares.
    """
    if _requests is None:
        warn_once("official_szse_requests_missing", "Shares: requests 不可用，无法拉取深交所官方份额，已降级。")
        return {}, "requests_missing"
    params = {
        "SHOWTYPE": "xlsx",
        "CATALOGID": "1000_lf",
        "TABKEY": "tab1",
        "random": str(_time.time()),
    }
    headers = {
        "Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        resp = _requests.get(
            "https://fund.szse.cn/api/report/ShowReport",
            params=params,
            headers=headers,
            timeout=15,
        )
    except Exception as e:
        warn_once(
            "official_szse_request_failed",
            f"Shares: 深交所官方份额请求失败，已降级。err={repr(e)}",
        )
        return {}, "request_failed"
    if int(resp.status_code) != 200:
        warn_once(
            "official_szse_http_failed",
            f"Shares: 深交所官方份额返回异常状态，已降级。status={resp.status_code}",
        )
        return {}, f"http_{resp.status_code}"
    try:
        df = pd.read_excel(io.BytesIO(resp.content), engine="openpyxl", dtype=str)
    except Exception as e:
        warn_once("official_szse_excel_failed", f"Shares: 深交所官方份额解析失败，已降级。err={repr(e)}")
        return {}, "excel_failed"
    if df is None or df.empty:
        return {}, "empty_result"
    cols = list(df.columns)
    code_col = next((c for c in cols if "基金代码" in str(c)), cols[0] if cols else None)
    scale_col = next((c for c in cols if "当前规模" in str(c) and "份" in str(c)), None)
    if scale_col is None:
        scale_col = next((c for c in cols if "当前规模" in str(c)), None)
    if scale_col is None and len(cols) > 5:
        scale_col = cols[5]
    if code_col is None or scale_col is None:
        warn_once("official_szse_columns_missing", "Shares: 深交所官方份额字段缺失，已降级。")
        return {}, "columns_missing"
    out: dict[str, float] = {}
    sub = df[[code_col, scale_col]].copy()
    sub = sub.dropna(how="any")
    for _, row in sub.iterrows():
        m = re.search(r"(\d{6})", str(row.get(code_col, "")).strip())
        if not m:
            continue
        shares = _try_parse_positive_float(row.get(scale_col))
        if shares > 0:
            out[f"{m.group(1)}.SZ"] = float(shares)
    if not out:
        return {}, "empty_result"
    return out, ""


def _get_sse_official_share_map(*, trade_date: str = "") -> tuple[dict[str, float], str]:
    td = _normalize_trade_date_yyyymmdd(trade_date)
    key = f"SSE:{td or 'latest'}"
    cached = _OFFICIAL_SHARE_CACHE.get(key)
    if isinstance(cached, dict):
        return cached, str(_OFFICIAL_SHARE_CACHE_ERR.get(key, ""))
    out, err = _fetch_sse_official_share_map(trade_date=td)
    _OFFICIAL_SHARE_CACHE[key] = out
    _OFFICIAL_SHARE_CACHE_ERR[key] = str(err or "")
    return out, err


def _get_szse_official_share_map() -> tuple[dict[str, float], str]:
    key = "SZSE:latest"
    cached = _OFFICIAL_SHARE_CACHE.get(key)
    if isinstance(cached, dict):
        return cached, str(_OFFICIAL_SHARE_CACHE_ERR.get(key, ""))
    out, err = _fetch_szse_official_share_map()
    _OFFICIAL_SHARE_CACHE[key] = out
    _OFFICIAL_SHARE_CACHE_ERR[key] = str(err or "")
    return out, err


def _download_history_data2_compat(stock_list: list[str], period: str, *, start_time: str, end_time: str) -> None:
    xtdata = require_xtdata()
    if not stock_list:
        return
    fn = getattr(xtdata, "download_history_data2", None)
    if not callable(fn):
        for s in stock_list:
            xtdata.download_history_data(s, period, start_time, end_time)
        return
    try:
        fn(stock_list, period, start_time, end_time)
    except TypeError:
        fn(stock_list, period, start_time, end_time, None)


def _safe_call_noarg(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except TypeError:
        raise
    except Exception as e:
        warn_once(f"xtdata_noarg_failed:{getattr(fn, '__name__', 'fn')}", f"XtData: 调用失败，已降级返回 None: fn={getattr(fn, '__name__', 'fn')} err={repr(e)}")
        return None


def _trade_date_from_xt_value(v: Any) -> str:
    if isinstance(v, (int, float)) and v >= 10_000_000_000:
        tm = _time.localtime(float(v) / 1000.0)
        return _time.strftime("%Y%m%d", tm)
    d = str(v)
    m = re.search(r"(\d{8})", d)
    return m.group(1) if m else ""


def _state_file_path(*, trade_date: str, state_dir: str | Path) -> Path:
    return Path(state_dir) / f"tick_{trade_date}.json"


def _default_tick_state(*, trade_date: str) -> dict[str, Any]:
    return {
        "version": _TICK_DOWNLOAD_STATE_VERSION,
        "trade_date": str(trade_date),
        "downloaded_codes": [],
        "empty_retry_codes": [],
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _normalize_code_list(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, list):
        values = list(values or [])
    for x in values:
        c = normalize_etf_code(str(x))
        if not c:
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _load_tick_state(*, trade_date: str, state_dir: str | Path) -> dict[str, Any]:
    p = _state_file_path(trade_date=trade_date, state_dir=state_dir)
    if not p.exists():
        return _default_tick_state(trade_date=trade_date)
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        warn_once(
            f"tick_state_load_failed:{trade_date}",
            f"XtData: tick download state 读取失败，已降级为新状态: path={p} err={repr(e)}",
        )
        return _default_tick_state(trade_date=trade_date)

    state = _default_tick_state(trade_date=trade_date)
    if isinstance(raw, dict):
        state["downloaded_codes"] = _normalize_code_list(raw.get("downloaded_codes", []))
        state["empty_retry_codes"] = _normalize_code_list(raw.get("empty_retry_codes", []))
    return state


def _save_tick_state(*, trade_date: str, state_dir: str | Path, state: dict[str, Any]) -> Path:
    p = _state_file_path(trade_date=trade_date, state_dir=state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _TICK_DOWNLOAD_STATE_VERSION,
        "trade_date": str(trade_date),
        "downloaded_codes": _normalize_code_list(state.get("downloaded_codes", [])),
        "empty_retry_codes": _normalize_code_list(state.get("empty_retry_codes", [])),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(p)
    return p


def _iter_chunks(codes: list[str], chunk_size: int) -> list[list[str]]:
    n = max(int(chunk_size), 1)
    return [codes[i : i + n] for i in range(0, len(codes), n)]


def _resolve_xtdata_data_dir() -> Optional[Path]:
    xtdata = require_xtdata()
    init_fn = getattr(xtdata, "init_data_dir", None)
    p: Optional[str] = None
    if callable(init_fn):
        try:
            v = init_fn()
            if isinstance(v, str) and v.strip():
                p = v.strip()
        except Exception:
            p = None
    if not p:
        raw = getattr(xtdata, "data_dir", None)
        if isinstance(raw, str) and raw.strip():
            p = raw.strip()
    if not p:
        return None
    path = Path(p)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _parse_yyyymmdd_to_date(v: str) -> Optional[date]:
    if not re.fullmatch(r"\d{8}", str(v)):
        return None
    try:
        return datetime.strptime(str(v), "%Y%m%d").date()
    except Exception:
        return None


def get_all_etf_universe() -> pd.DataFrame:
    xtdata = require_xtdata()
    download_fn = getattr(xtdata, "download_etf_info", None)
    if callable(download_fn):
        download_fn()

    get_fn = getattr(xtdata, "get_etf_info", None)
    if callable(get_fn):
        try:
            raw = _safe_call_noarg(get_fn)
        except TypeError:
            raw = None
        if isinstance(raw, dict) and raw:
            codes: list[str] = []
            names: dict[str, str] = {}
            for k, v in raw.items():
                if not isinstance(k, str):
                    continue
                code = normalize_etf_code(k)
                if not code:
                    continue
                codes.append(code)
                if isinstance(v, dict):
                    for key in ("name", "名称", "基金简称", "基金名称", "ETF名称"):
                        nm = v.get(key)
                        if isinstance(nm, str) and nm.strip():
                            names[code] = nm.strip()
                            break
            df = pd.DataFrame({"code": pd.unique(pd.Series(codes))})
            df["name"] = df["code"].map(names).fillna("")
            return df

    download_sector = getattr(xtdata, "download_sector_data", None)
    if callable(download_sector):
        try:
            download_sector()
        except Exception as e:
            warn_once("download_sector_data_failed", f"XtData: download_sector_data 失败，已降级继续: err={repr(e)}")
    try:
        sectors = xtdata.get_sector_list()
    except Exception as e:
        warn_once("get_sector_list_failed", f"XtData: get_sector_list 失败，已降级为空 sector 列表: err={repr(e)}")
        sectors = []
    codes_set: set[str] = set()
    sector_fail = 0
    if isinstance(sectors, list):
        for sec in sectors:
            if not isinstance(sec, str):
                continue
            if "ETF" not in sec.upper():
                continue
            if "过期" in sec:
                continue
            try:
                lst = xtdata.get_stock_list_in_sector(sec)
            except Exception:
                sector_fail += 1
                continue
            if not isinstance(lst, list):
                continue
            for c in lst:
                if not isinstance(c, str):
                    continue
                code = normalize_etf_code(c)
                if code:
                    codes_set.add(code)
    codes = sorted(codes_set)
    if sector_fail:
        warn_once("sector_member_failed", f"XtData: sector 成分获取失败，已降级跳过部分 sector: failed={int(sector_fail)}")
    names: dict[str, str] = {}
    info_fail = 0
    for c in codes:
        try:
            info = xtdata.get_instrument_detail(c, False)
        except Exception:
            info_fail += 1
            continue
        if isinstance(info, dict):
            nm = info.get("InstrumentName")
            if isinstance(nm, str) and nm.strip():
                names[c] = nm.strip()
    if info_fail:
        warn_once("instrument_detail_failed", f"XtData: get_instrument_detail 失败，已降级缺失部分名称: failed={int(info_fail)}")
    df = pd.DataFrame({"code": codes})
    df["name"] = df["code"].map(names).fillna("")
    return df


def get_industry_etf_universe(*, exclude_keywords: Optional[list[str]] = None) -> pd.DataFrame:
    df = get_all_etf_universe()
    out = filter_etf_universe_by_keywords(df, exclude_keywords=exclude_keywords)
    out = out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return out


def filter_etf_universe_by_keywords(df: pd.DataFrame, *, exclude_keywords: Optional[list[str]] = None) -> pd.DataFrame:
    kw = exclude_keywords if exclude_keywords is not None else ETF_EXCLUDE_KEYWORDS
    norm_kw = [re.sub(r"\s+", "", str(k)).upper() for k in kw if str(k).strip()]
    if "name" not in df.columns:
        df["name"] = ""
    mask = []
    for nm in df["name"].astype(str).tolist():
        if not nm:
            mask.append(True)
            continue
        nm_norm = re.sub(r"\s+", "", str(nm)).upper()
        bad = any(k in nm_norm for k in norm_kw)
        mask.append(not bad)
    out = df[pd.Series(mask)].copy()
    return out


def filter_etf_codes_by_keywords(
    codes: list[str],
    *,
    exclude_keywords: Optional[list[str]] = None,
) -> list[str]:
    src_codes = _normalize_code_list(codes)
    if not src_codes:
        return []

    try:
        all_df = get_all_etf_universe()
    except Exception as e:
        warn_once(
            "etf_code_keyword_filter_failed",
            f"XtData: ETF 关键词过滤失败，已降级保留输入代码: err={repr(e)}",
        )
        return src_codes

    if all_df is None or all_df.empty or "code" not in all_df.columns:
        return src_codes

    all_df = all_df.copy()
    all_df["code"] = all_df["code"].astype(str).map(normalize_etf_code)
    all_df = all_df[all_df["code"].astype(bool)].copy()
    if all_df.empty:
        return src_codes

    known_codes = set(all_df["code"].tolist())
    allowed_df = filter_etf_universe_by_keywords(all_df, exclude_keywords=exclude_keywords)
    allowed_codes = set(allowed_df["code"].tolist())

    out: list[str] = []
    for c in src_codes:
        if c not in known_codes:
            out.append(c)
            continue
        if c in allowed_codes:
            out.append(c)
    return out


def filter_etf_codes_by_liquidity(
    codes: list[str],
    *,
    trade_date: str,
    lookback_days: int = 20,
    min_active_days: int = 5,
    min_median_amount: float = 1_000_000.0,
    min_median_volume: float = 0.0,
    chunk_size: int = 400,
) -> dict[str, Any]:
    """Filter ETF codes by recent daily-liquidity metrics.

    This function intentionally uses 1d bars only so low-liquidity symbols can
    be skipped before any tick pre-download.
    """
    src_codes = _normalize_code_list(codes)
    td = _normalize_trade_date_yyyymmdd(trade_date)
    if not src_codes:
        return {
            "trade_date": str(td or trade_date),
            "input_count": 0,
            "kept_count": 0,
            "removed_count": 0,
            "fallback_kept_count": 0,
            "failed_chunks": 0,
            "lookback_days": int(max(int(lookback_days), 1)),
            "min_active_days": int(max(int(min_active_days), 0)),
            "min_median_amount": float(max(float(min_median_amount), 0.0)),
            "min_median_volume": float(max(float(min_median_volume), 0.0)),
            "chunk_size": int(max(int(chunk_size), 1)),
            "kept_codes": [],
            "removed_codes": [],
            "removed_samples": [],
        }

    lookback_i = max(int(lookback_days), 1)
    min_days_i = max(int(min_active_days), 0)
    min_amt_f = max(float(min_median_amount), 0.0)
    min_vol_f = max(float(min_median_volume), 0.0)
    chunk_i = max(int(chunk_size), 1)
    td = td or str(trade_date).strip()

    if min_days_i <= 0 and min_amt_f <= 0.0 and min_vol_f <= 0.0:
        return {
            "trade_date": str(td),
            "input_count": int(len(src_codes)),
            "kept_count": int(len(src_codes)),
            "removed_count": 0,
            "fallback_kept_count": 0,
            "failed_chunks": 0,
            "lookback_days": int(lookback_i),
            "min_active_days": int(min_days_i),
            "min_median_amount": float(min_amt_f),
            "min_median_volume": float(min_vol_f),
            "chunk_size": int(chunk_i),
            "kept_codes": list(src_codes),
            "removed_codes": [],
            "removed_samples": [],
        }

    xtdata = require_xtdata()
    kept_codes: list[str] = []
    removed_codes: list[str] = []
    removed_samples: list[dict[str, Any]] = []
    fallback_kept = 0
    failed_chunks = 0

    for chunk in _iter_chunks(src_codes, chunk_size=chunk_i):
        try:
            raw = xtdata.get_market_data(
                field_list=["volume", "amount"],
                stock_list=chunk,
                period="1d",
                start_time="",
                end_time=str(td),
                count=int(lookback_i),
                dividend_type="none",
                fill_data=True,
            )
            vol_df = raw.get("volume") if isinstance(raw, dict) else None
            amt_df = raw.get("amount") if isinstance(raw, dict) else None
            if not isinstance(vol_df, pd.DataFrame) or not isinstance(amt_df, pd.DataFrame):
                failed_chunks += 1
                fallback_kept += len(chunk)
                kept_codes.extend(chunk)
                warn_once(
                    f"etf_liquidity_filter_chunk_shape_invalid:{td}:{failed_chunks}",
                    (
                        "XtData: ETF 流动性前置筛选失败（字段结构异常），已降级保留该批代码。"
                        f" date={td} chunk_size={len(chunk)}"
                    ),
                )
                continue

            idx_map: dict[str, Any] = {}
            for idx in vol_df.index:
                c_norm = normalize_etf_code(str(idx))
                if c_norm and c_norm not in idx_map:
                    idx_map[c_norm] = idx
            for idx in amt_df.index:
                c_norm = normalize_etf_code(str(idx))
                if c_norm and c_norm not in idx_map:
                    idx_map[c_norm] = idx

            for code in chunk:
                idx = idx_map.get(code, code if code in vol_df.index else None)
                if idx is None or idx not in vol_df.index or idx not in amt_df.index:
                    # Fail-open: unknown row mapping should not drop symbols.
                    kept_codes.append(code)
                    continue

                try:
                    vol_vals = pd.to_numeric(pd.Series(vol_df.loc[idx].values), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
                    amt_vals = pd.to_numeric(pd.Series(amt_df.loc[idx].values), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
                    vol_vals = vol_vals * _LOT_SIZE
                except Exception:
                    kept_codes.append(code)
                    continue

                valid = np.isfinite(vol_vals) & np.isfinite(amt_vals) & (vol_vals > 0) & (amt_vals > 0)
                active_days = int(np.sum(valid))
                if active_days > 0:
                    median_amount = float(np.median(amt_vals[valid]))
                    median_volume = float(np.median(vol_vals[valid]))
                else:
                    median_amount = 0.0
                    median_volume = 0.0

                passed = (
                    active_days >= min_days_i
                    and median_amount >= min_amt_f
                    and median_volume >= min_vol_f
                )
                if passed:
                    kept_codes.append(code)
                else:
                    removed_codes.append(code)
                    if len(removed_samples) < 12:
                        removed_samples.append(
                            {
                                "code": code,
                                "active_days": int(active_days),
                                "median_amount": float(round(median_amount, 2)),
                                "median_volume": float(round(median_volume, 2)),
                            }
                        )
        except Exception as e:
            failed_chunks += 1
            fallback_kept += len(chunk)
            kept_codes.extend(chunk)
            warn_once(
                f"etf_liquidity_filter_chunk_failed:{td}:{failed_chunks}",
                (
                    "XtData: ETF 流动性前置筛选失败，已降级保留该批代码。"
                    f" date={td} chunk_size={len(chunk)} err={repr(e)}"
                ),
            )

    return {
        "trade_date": str(td),
        "input_count": int(len(src_codes)),
        "kept_count": int(len(kept_codes)),
        "removed_count": int(len(removed_codes)),
        "fallback_kept_count": int(fallback_kept),
        "failed_chunks": int(failed_chunks),
        "lookback_days": int(lookback_i),
        "min_active_days": int(min_days_i),
        "min_median_amount": float(min_amt_f),
        "min_median_volume": float(min_vol_f),
        "chunk_size": int(chunk_i),
        "kept_codes": kept_codes,
        "removed_codes": removed_codes,
        "removed_samples": removed_samples,
    }


def get_total_shares_detail(etf_code: str, *, trade_date: str = "") -> dict[str, Any]:
    """Resolve ETF total shares with source metadata.

    Returns:
    {
      "code": "510050.SH",
      "shares": float,           # in shares
      "source": str,             # official_sse / official_szse / xtdata_totalvolume / xtdata_floatvolume / none
      "degraded": bool,          # True when not from official exchange source
      "reason": str,             # degradation reason for diagnostics
    }
    """
    code = normalize_etf_code(etf_code)
    out: dict[str, Any] = {
        "code": code or str(etf_code).strip().upper(),
        "shares": 0.0,
        "source": "none",
        "degraded": True,
        "reason": "invalid_code",
    }
    if not code:
        return out

    ex = code.rsplit(".", 1)[-1] if "." in code else ""
    if ex == "SH":
        official_map, official_err = _get_sse_official_share_map(trade_date=trade_date)
        v = _try_parse_positive_float(official_map.get(code, 0.0))
        if v > 0:
            source = "official_sse_latest" if str(official_err).startswith("fallback_latest:") else "official_sse"
            reason = str(official_err) if source == "official_sse_latest" else ""
            out.update({"shares": float(v), "source": source, "degraded": False, "reason": reason})
            return out
        out["reason"] = f"official_sse_{official_err or 'missing'}"
    elif ex == "SZ":
        official_map, official_err = _get_szse_official_share_map()
        v = _try_parse_positive_float(official_map.get(code, 0.0))
        if v > 0:
            out.update({"shares": float(v), "source": "official_szse", "degraded": False, "reason": ""})
            return out
        out["reason"] = f"official_szse_{official_err or 'missing'}"
    else:
        out["reason"] = "unsupported_exchange"

    try:
        xtdata = require_xtdata()
        info = xtdata.get_instrument_detail(code, False)
    except Exception as e:
        out["reason"] = f"{out['reason']};xtdata_error:{repr(e)}"
        return out
    if not isinstance(info, dict):
        out["reason"] = f"{out['reason']};xtdata_invalid"
        return out
    for k, src in (("TotalVolume", "xtdata_totalvolume"), ("FloatVolume", "xtdata_floatvolume")):
        f = _try_parse_positive_float(info.get(k))
        if f > 0:
            out.update({"shares": float(f), "source": src, "degraded": True})
            return out
    out["reason"] = f"{out['reason']};xtdata_zero"
    return out


def get_total_shares(etf_code: str, trade_date: str = "") -> float:
    detail = get_total_shares_detail(etf_code, trade_date=trade_date)
    return _try_parse_positive_float(detail.get("shares"))


def download_tick_data(etf_codes: list[str], trade_date: str) -> None:
    _download_history_data2_compat(etf_codes, "tick", start_time=trade_date, end_time=trade_date)


def _download_tick_data_subprocess(
    etf_codes: list[str],
    trade_date: str,
    *,
    timeout_sec: int,
) -> tuple[bool, str, float]:
    payload = json.dumps(
        {
            "codes": _normalize_code_list(etf_codes),
            "trade_date": str(trade_date),
        },
        ensure_ascii=False,
    )
    script = (
        "import json, sys; "
        "from etf_chip_engine.data.xtdata_provider import _download_history_data2_compat; "
        "p = json.loads(sys.argv[1]); "
        "_download_history_data2_compat(p['codes'], 'tick', start_time=p['trade_date'], end_time=p['trade_date'])"
    )
    t0 = _time.perf_counter()
    try:
        cp = subprocess.run(
            [sys.executable, "-c", script, payload],
            capture_output=True,
            text=True,
            timeout=max(int(timeout_sec), 1),
        )
        elapsed = max(_time.perf_counter() - t0, 0.0)
        if int(cp.returncode) == 0:
            return True, "", float(elapsed)
        err = (cp.stderr or cp.stdout or "").strip()
        if len(err) > 400:
            err = err[-400:]
        return False, f"exit={cp.returncode} {err}", float(elapsed)
    except subprocess.TimeoutExpired:
        elapsed = max(_time.perf_counter() - t0, 0.0)
        return False, f"timeout>{int(timeout_sec)}s", float(elapsed)
    except Exception as e:
        elapsed = max(_time.perf_counter() - t0, 0.0)
        return False, repr(e), float(elapsed)


def _subprocess_download_1d(
    codes: list[str],
    trade_date: str,
    *,
    timeout_sec: int,
) -> bool:
    """在子进程中下载 1d 日线数据，返回 True 表示成功。"""
    if not codes:
        return True
    payload = json.dumps(
        {"codes": codes, "trade_date": str(trade_date)},
        ensure_ascii=False,
    )
    script = (
        "import json, sys; "
        "from etf_chip_engine.data.xtdata_provider import _download_history_data2_compat; "
        "p = json.loads(sys.argv[1]); "
        "_download_history_data2_compat(p['codes'], '1d', start_time=p['trade_date'], end_time=p['trade_date'])"
    )
    try:
        cp = subprocess.run(
            [sys.executable, "-c", script, payload],
            capture_output=True,
            text=True,
            timeout=max(int(timeout_sec), 1),
        )
        return int(cp.returncode) == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def ensure_daily_history_downloaded(
    stock_codes: list[str],
    trade_date: str,
    *,
    timeout_sec: int = 20,
    retry_chunk_size: int = 30,
    skip_full_if_codes_ge: int = 220,
) -> bool:
    codes = _normalize_code_list(stock_codes)
    if not codes:
        return True

    ok = False
    if len(codes) < max(int(skip_full_if_codes_ge), 1):
        ok = _subprocess_download_1d(codes, trade_date, timeout_sec=timeout_sec)

    if ok:
        return True

    chunk_n = max(int(retry_chunk_size), 1)
    all_ok = True
    for chunk in _iter_chunks(codes, chunk_size=chunk_n):
        if not _subprocess_download_1d(chunk, trade_date, timeout_sec=timeout_sec):
            all_ok = False
    return bool(all_ok)


def download_constituent_close_prices(
    stock_codes: list[str],
    trade_date: str,
    *,
    timeout_sec: int = 8,
    retry_chunk_size: int = 30,
    skip_full_if_codes_ge: int = 220,
) -> dict[str, float]:
    """下载成分股日线数据并返回收盘价字典。

    调用方应保证传入的代码后缀为 .SH / .SZ / .BJ。

    策略：
    1. 先尝试全量下载（subprocess + timeout）
    2. 若失败，拆分为小批次逐个重试，最大限度获取数据
    3. 无论下载结果如何，读取本地已有数据返回
    """
    codes = _normalize_code_list(stock_codes)
    if not codes:
        return {}

    # ── Phase 1: 全量尝试 ──
    ok = False
    if len(codes) < max(int(skip_full_if_codes_ge), 1):
        ok = _subprocess_download_1d(codes, trade_date, timeout_sec=timeout_sec)
    else:
        print(
            json.dumps(
                {
                    "timing": "etf_chip_engine.xtdata.constituent_download_skip_full",
                    "trade_date": str(trade_date),
                    "total_codes": len(codes),
                    "skip_full_if_codes_ge": int(skip_full_if_codes_ge),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    if not ok:
        # ── Phase 2: 分块重试 ──
        chunk_n = max(int(retry_chunk_size), 1)
        chunks = _iter_chunks(codes, chunk_size=chunk_n)
        retry_ok = 0
        retry_fail = 0
        for idx, chunk in enumerate(chunks, start=1):
            t0 = _time.perf_counter()
            print(
                json.dumps(
                    {
                        "timing": "etf_chip_engine.xtdata.constituent_download_chunk_start",
                        "trade_date": str(trade_date),
                        "chunk_idx": idx,
                        "chunks": len(chunks),
                        "chunk_codes": len(chunk),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if _subprocess_download_1d(chunk, trade_date, timeout_sec=timeout_sec):
                retry_ok += 1
                ok_flag = True
            else:
                retry_fail += 1
                ok_flag = False
            elapsed = max(_time.perf_counter() - t0, 0.0)
            print(
                json.dumps(
                    {
                        "timing": "etf_chip_engine.xtdata.constituent_download_chunk_done",
                        "trade_date": str(trade_date),
                        "chunk_idx": idx,
                        "chunks": len(chunks),
                        "chunk_codes": len(chunk),
                        "ok": ok_flag,
                        "elapsed_sec": round(elapsed, 3),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        print(
            json.dumps(
                {
                    "timing": "etf_chip_engine.xtdata.constituent_download_retry",
                    "trade_date": str(trade_date),
                    "total_codes": len(codes),
                    "chunks": len(chunks),
                    "retry_ok": retry_ok,
                    "retry_fail": retry_fail,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    # ── 读取本地已有数据 ──
    return get_daily_close_price_map(codes, trade_date)


def _download_tick_data_with_timeout(
    etf_codes: list[str],
    trade_date: str,
    *,
    timeout_sec: int,
    _depth: int = 0,
    _parent_size: int = 0,
) -> tuple[list[str], list[str]]:
    codes = _normalize_code_list(etf_codes)
    if not codes:
        return [], []

    parent_n = _parent_size or len(codes)

    # ── heartbeat: attempt start ──
    print(
        json.dumps(
            {
                "timing": "etf_chip_engine.xtdata.tick_download_attempt",
                "trade_date": str(trade_date),
                "n_codes": len(codes),
                "depth": _depth,
                "parent_size": parent_n,
                "timeout_sec": timeout_sec,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    ok, err, elapsed = _download_tick_data_subprocess(codes, trade_date, timeout_sec=timeout_sec)
    if ok:
        # ── heartbeat: success ──
        print(
            json.dumps(
                {
                    "timing": "etf_chip_engine.xtdata.tick_download_ok",
                    "trade_date": str(trade_date),
                    "n_codes": len(codes),
                    "depth": _depth,
                    "elapsed_sec": round(elapsed, 3),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return codes, []
    # Non-timeout import/runtime environment errors usually affect all codes;
    # avoid recursive fan-out in that case.
    if ("No module named" in err) or ("ImportError" in err):
        return [], codes
    # If the whole chunk failed, split to isolate problematic codes and keep progress.
    if len(codes) <= 1:
        return [], codes

    # ── heartbeat: splitting ──
    print(
        json.dumps(
            {
                "timing": "etf_chip_engine.xtdata.tick_download_split",
                "trade_date": str(trade_date),
                "n_codes": len(codes),
                "depth": _depth,
                "err_summary": err[:120] if err else "",
                "elapsed_sec": round(elapsed, 3),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    mid = max(len(codes) // 2, 1)
    left_done, left_failed = _download_tick_data_with_timeout(
        codes[:mid], trade_date, timeout_sec=timeout_sec,
        _depth=_depth + 1, _parent_size=parent_n,
    )
    right_done, right_failed = _download_tick_data_with_timeout(
        codes[mid:], trade_date, timeout_sec=timeout_sec,
        _depth=_depth + 1, _parent_size=parent_n,
    )
    return left_done + right_done, left_failed + right_failed


def ensure_tick_data_downloaded(
    etf_codes: list[str],
    trade_date: str,
    *,
    force: bool = False,
    chunk_size: int = 80,
    timeout_sec: int = 0,
    state_dir: str | Path = _DEFAULT_TICK_STATE_DIR,
) -> dict[str, Any]:
    codes = _normalize_code_list(etf_codes)
    state = _load_tick_state(trade_date=trade_date, state_dir=state_dir)
    downloaded_set = set(_normalize_code_list(state.get("downloaded_codes", [])))
    pending = codes if bool(force) else [c for c in codes if c not in downloaded_set]
    chunk_size_i = max(int(chunk_size), 1)
    chunks = _iter_chunks(pending, chunk_size=chunk_size_i)
    total_chunks = len(chunks)

    print(
        json.dumps(
            {
                "timing": "etf_chip_engine.xtdata.pre_download_start",
                "trade_date": str(trade_date),
                "input_count": int(len(codes)),
                "pending_count": int(len(pending)),
                "chunks": int(total_chunks),
                "chunk_size": int(chunk_size_i),
                "force": bool(force),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    downloaded_now = 0
    failed_codes_total: list[str] = []
    for chunk_idx, chunk in enumerate(chunks, start=1):
        chunk_start = _time.perf_counter()
        print(
            json.dumps(
                {
                    "timing": "etf_chip_engine.xtdata.pre_download_chunk_start",
                    "trade_date": str(trade_date),
                    "chunk_idx": int(chunk_idx),
                    "chunks": int(total_chunks),
                    "chunk_codes": int(len(chunk)),
                    "downloaded_now": int(downloaded_now),
                    "pending_left_before": int(max(0, len(pending) - downloaded_now)),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            timeout_i = max(int(timeout_sec), 0)
            if timeout_i > 0:
                done_codes, failed_codes = _download_tick_data_with_timeout(chunk, trade_date, timeout_sec=timeout_i)
                downloaded_set.update(done_codes)
                downloaded_now += len(done_codes)
                failed_codes_total.extend(failed_codes)
                if failed_codes:
                    sample = ",".join(failed_codes[:5])
                    print(
                        f"[WARN] XtData: pre-download timeout/failed codes skipped. "
                        f"date={trade_date} chunk={chunk_idx}/{total_chunks} failed={len(failed_codes)} sample={sample}",
                        flush=True,
                    )
                    warn_once(
                        f"tick_pre_download_timeout_failed:{trade_date}:{chunk_idx}",
                        f"XtData: tick 预下载超时/失败，已降级跳过部分代码: date={trade_date} "
                        f"chunk={chunk_idx}/{total_chunks} failed={len(failed_codes)} sample={sample}",
                    )
            else:
                download_tick_data(chunk, trade_date)
                downloaded_set.update(chunk)
                downloaded_now += len(chunk)
            elapsed = max(_time.perf_counter() - chunk_start, 0.0)
            print(
                json.dumps(
                    {
                        "timing": "etf_chip_engine.xtdata.pre_download_chunk_done",
                        "trade_date": str(trade_date),
                        "chunk_idx": int(chunk_idx),
                        "chunks": int(total_chunks),
                        "chunk_codes": int(len(chunk)),
                        "downloaded_now": int(downloaded_now),
                        "pending_left_after": int(max(0, len(pending) - downloaded_now)),
                        "failed_total": int(len(failed_codes_total)),
                        "elapsed_sec": round(float(elapsed), 3),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as e:
            print(
                f"[WARN] XtData: pre-download chunk failed, keep going. "
                f"date={trade_date} chunk={chunk_idx}/{total_chunks} size={len(chunk)} err={repr(e)}",
                flush=True,
            )
            warn_once(
                f"tick_pre_download_failed:{trade_date}:{chunk_idx}",
                f"XtData: tick 预下载失败，已降级继续: date={trade_date} chunk={chunk_idx}/{len(chunks)} size={len(chunk)} err={repr(e)}",
            )
        finally:
            state["downloaded_codes"] = sorted(downloaded_set)
            _save_tick_state(trade_date=trade_date, state_dir=state_dir, state=state)

    state_path = _state_file_path(trade_date=trade_date, state_dir=state_dir)
    if not state_path.exists():
        _save_tick_state(trade_date=trade_date, state_dir=state_dir, state=state)

    return {
        "trade_date": str(trade_date),
        "input_count": int(len(codes)),
        "pending_count": int(len(pending)),
        "downloaded_now": int(downloaded_now),
        "failed_count": int(len(set(failed_codes_total))),
        "skipped_by_cache": int(max(0, len(codes) - len(pending))),
        "chunks": int(total_chunks),
        "chunk_size": int(chunk_size_i),
        "timeout_sec": int(max(int(timeout_sec), 0)),
        "force": bool(force),
        "state_path": str(state_path),
    }


def retry_download_for_empty_tick_code_once(
    etf_code: str,
    trade_date: str,
    *,
    state_dir: str | Path = _DEFAULT_TICK_STATE_DIR,
    timeout_sec: int = 20,
) -> bool:
    code = normalize_etf_code(etf_code)
    if not code:
        return False

    state = _load_tick_state(trade_date=trade_date, state_dir=state_dir)
    retry_set = set(_normalize_code_list(state.get("empty_retry_codes", [])))
    if code in retry_set:
        return False

    timeout_i = max(int(timeout_sec), 0)
    retry_ok = False
    t0 = _time.perf_counter()
    print(
        json.dumps(
            {
                "timing": "etf_chip_engine.xtdata.empty_tick_retry_start",
                "trade_date": str(trade_date),
                "code": str(code),
                "timeout_sec": int(timeout_i),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    try:
        if timeout_i > 0:
            done_codes, failed_codes = _download_tick_data_with_timeout([code], trade_date, timeout_sec=timeout_i)
            retry_ok = code in set(done_codes)
            if not retry_ok:
                sample = ",".join(failed_codes[:5]) if failed_codes else code
                print(
                    f"[WARN] XtData: empty-tick retry timeout/failed. "
                    f"code={code} date={trade_date} timeout_sec={timeout_i} sample={sample}",
                    flush=True,
                )
                warn_once(
                    f"tick_empty_retry_timeout_failed:{trade_date}:{code}",
                    (
                        "XtData: empty-tick retry timeout/failed, downgraded and skipped. "
                        f"code={code} date={trade_date} timeout_sec={timeout_i} sample={sample}"
                    ),
                )
        else:
            download_tick_data([code], trade_date)
            retry_ok = True
    except Exception as e:
        print(
            f"[WARN] XtData: empty-tick retry failed. code={code} date={trade_date} err={repr(e)}",
            flush=True,
        )
        warn_once(
            f"tick_empty_retry_failed:{trade_date}:{code}",
            f"XtData: empty-tick retry failed. code={code} date={trade_date} err={repr(e)}",
        )
        retry_ok = False

    elapsed = max(_time.perf_counter() - t0, 0.0)
    print(
        json.dumps(
            {
                "timing": "etf_chip_engine.xtdata.empty_tick_retry_done",
                "trade_date": str(trade_date),
                "code": str(code),
                "timeout_sec": int(timeout_i),
                "retry_ok": bool(retry_ok),
                "elapsed_sec": round(float(elapsed), 3),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    retry_set.add(code)
    downloaded_set = set(_normalize_code_list(state.get("downloaded_codes", [])))
    if retry_ok:
        downloaded_set.add(code)
    state["empty_retry_codes"] = sorted(retry_set)
    state["downloaded_codes"] = sorted(downloaded_set)
    _save_tick_state(trade_date=trade_date, state_dir=state_dir, state=state)
    return True


def cleanup_xtdata_dated_files(
    *,
    keep_days: int = 365,
    today: Optional[date] = None,
) -> dict[str, Any]:
    keep_i = int(max(int(keep_days), 0))
    if keep_i <= 0:
        return {
            "enabled": False,
            "keep_days": int(keep_i),
            "data_dir": "",
            "removed_files": 0,
            "removed_bytes": 0,
        }

    data_dir = _resolve_xtdata_data_dir()
    if data_dir is None or not data_dir.exists():
        return {
            "enabled": True,
            "keep_days": int(keep_i),
            "data_dir": (str(data_dir) if data_dir is not None else ""),
            "removed_files": 0,
            "removed_bytes": 0,
        }

    today_d = today if today is not None else datetime.now().date()
    cutoff = today_d - timedelta(days=keep_i)

    removed_files = 0
    removed_bytes = 0
    for mk in ("SH", "SZ"):
        market_dir = data_dir / mk
        if not market_dir.exists():
            continue
        for p in market_dir.rglob("*.dat"):
            d = _parse_yyyymmdd_to_date(p.stem)
            if d is None or d >= cutoff:
                continue
            try:
                removed_bytes += int(p.stat().st_size)
                p.unlink()
                removed_files += 1
            except Exception as e:
                warn_once(
                    f"xtdata_history_cleanup_unlink_failed:{str(p)}",
                    f"XtData: 清理过期交易数据失败，已降级跳过: path={p} err={repr(e)}",
                )

    return {
        "enabled": True,
        "keep_days": int(keep_i),
        "data_dir": str(data_dir),
        "removed_files": int(removed_files),
        "removed_bytes": int(removed_bytes),
    }


def get_tick_last_price_map(
    codes: list[str],
    trade_date: str,
    *,
    end_hhmmss: str = "151000",
    chunk_size: int = 200,
) -> dict[str, float]:
    xtdata = require_xtdata()
    out: dict[str, float] = {}
    lst = [normalize_etf_code(str(x)) for x in list(codes) if str(x).strip()]
    lst = [c for c in lst if c]
    lst = list(dict.fromkeys(lst))
    if not lst:
        return out
    start = f"{trade_date}093000"
    end = f"{trade_date}{end_hhmmss}"
    for chunk in _iter_chunks(lst, chunk_size=max(int(chunk_size), 1)):
        try:
            raw = xtdata.get_market_data(
                field_list=["lastPrice"],
                stock_list=list(chunk),
                period="tick",
                start_time=start,
                end_time=end,
                count=1,
                dividend_type="none",
                fill_data=True,
            )
        except Exception:
            raw = None
        if not isinstance(raw, dict) or not raw:
            continue
        df = raw.get("lastPrice") if isinstance(raw, dict) else None
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for code in chunk:
            px: float | None = None
            try:
                if code in df.index:
                    px = float(df.loc[code].values[-1])
                else:
                    px = float(df.iloc[0].values[-1])
            except Exception:
                px = None
            if px is None:
                continue
            if px > 0 and np.isfinite(px):
                out[str(code)] = float(px)
    return out


def get_daily_close_price_map(
    codes: list[str],
    trade_date: str,
    *,
    chunk_size: int = 200,
) -> dict[str, float]:
    xtdata = require_xtdata()
    out: dict[str, float] = {}
    lst = [normalize_etf_code(str(x)) for x in list(codes) if str(x).strip()]
    lst = [c for c in lst if c]
    lst = list(dict.fromkeys(lst))
    if not lst:
        return out
    for chunk in _iter_chunks(lst, chunk_size=max(int(chunk_size), 1)):
        try:
            raw = xtdata.get_market_data(
                field_list=["close"],
                stock_list=list(chunk),
                period="1d",
                start_time="",
                end_time=str(trade_date),
                count=1,
                dividend_type="none",
                fill_data=True,
            )
        except Exception as e:
            warn_once(
                f"daily_close_price_map_failed:{trade_date}",
                f"XtData: 拉取成分股日线收盘价失败，已降级为空: trade_date={trade_date} size={len(chunk)} err={repr(e)}",
            )
            raw = None
        if not isinstance(raw, dict) or not raw:
            continue
        df = raw.get("close") if isinstance(raw, dict) else None
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for code in chunk:
            px: float | None = None
            try:
                code6 = code.split(".", 1)[0]
                if code in df.index:
                    px = float(df.loc[code].values[-1])
                elif code6 in df.index:
                    px = float(df.loc[code6].values[-1])
                else:
                    px = float(df.iloc[0].values[-1])
            except Exception:
                px = None
            if px is None:
                continue
            if px > 0 and np.isfinite(px):
                out[str(code)] = float(px)
    return out


def cleanup_xtdata_trade_date_files(*, trade_date: str) -> dict[str, Any]:
    data_dir = _resolve_xtdata_data_dir()
    td = str(trade_date)
    if data_dir is None or not data_dir.exists():
        return {
            "enabled": True,
            "trade_date": td,
            "data_dir": (str(data_dir) if data_dir is not None else ""),
            "removed_files": 0,
            "removed_bytes": 0,
        }

    removed_files = 0
    removed_bytes = 0
    for mk in ("SH", "SZ"):
        market_dir = data_dir / mk
        if not market_dir.exists():
            continue
        for p in market_dir.rglob("*.dat"):
            try:
                m = re.search(r"(\d{8})", p.stem)
            except Exception:
                m = None
            if not m or m.group(1) != td:
                continue
            try:
                removed_bytes += int(p.stat().st_size)
            except Exception:
                pass
            try:
                p.unlink()
                removed_files += 1
            except Exception as e:
                warn_once(
                    f"xtdata_trade_date_cleanup_unlink_failed:{str(p)}",
                    f"XtData: 清理当日交易数据失败，已降级跳过: trade_date={td} path={p} err={repr(e)}",
                )

    try:
        state_p = _state_file_path(trade_date=td, state_dir=_DEFAULT_TICK_STATE_DIR)
        if state_p.exists():
            state_p.unlink()
    except Exception as e:
        warn_once(
            f"xtdata_trade_date_cleanup_state_failed:{td}",
            f"XtData: 清理 tick 下载状态失败，已降级跳过: trade_date={td} err={repr(e)}",
        )

    return {
        "enabled": True,
        "trade_date": td,
        "data_dir": str(data_dir),
        "removed_files": int(removed_files),
        "removed_bytes": int(removed_bytes),
    }


def get_local_tick_data(etf_code: str, trade_date: str) -> Any:
    xtdata = require_xtdata()
    start = f"{trade_date}000000"
    end = f"{trade_date}235959"
    raw = xtdata.get_local_data(
        field_list=["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1", "iopv"],
        stock_list=[etf_code],
        period="tick",
        start_time=start,
        end_time=end,
        count=-1,
        dividend_type="none",
        fill_data=True,
    )
    if isinstance(raw, dict) and raw:
        v = raw.get(etf_code)
        if v is None:
            v = next(iter(raw.values()))
        if isinstance(v, dict):
            v = next(iter(v.values())) if v else None
        if isinstance(v, pd.DataFrame):
            return v.reset_index(drop=True)
    if isinstance(raw, dict) and etf_code in raw:
        return raw[etf_code]
    if isinstance(raw, dict) and raw:
        v = next(iter(raw.values()))
        return v
    return None


def get_market_tick_data(etf_code: str, trade_date: str, *, count: int = -1) -> Any:
    xtdata = require_xtdata()
    start = f"{trade_date}093000"
    end = f"{trade_date}151000"
    # NOTE:
    # - For ETF tick, `get_market_data_ex` supports depth arrays via bidPrice/bidVol/askPrice/askVol.
    # - `iopv` is NOT a valid tick field for many instruments (ValueError: "no field of name iopv"),
    #   and including it may force a silent fallback path that drops L1, making OFI unavailable.
    ex_fields = ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice", "bidVol", "askPrice", "askVol"]
    # Best-effort legacy field names for `get_market_data` (some environments ignore unsupported fields).
    fields = ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"]
    count_i = -1 if int(count) <= 0 else int(count)
    def _query_ex(count_v: int) -> Optional[pd.DataFrame]:
        ex_fn = getattr(xtdata, "get_market_data_ex", None)
        if not callable(ex_fn):
            return None
        try:
            raw_ex = ex_fn(
                field_list=ex_fields,
                stock_list=[etf_code],
                period="tick",
                start_time=start,
                end_time=end,
                count=int(count_v),
                dividend_type="none",
                fill_data=True,
            )
            if not isinstance(raw_ex, dict) or not raw_ex:
                return None
            v = raw_ex.get(etf_code)
            if v is None:
                v = next(iter(raw_ex.values()))
            if not isinstance(v, pd.DataFrame):
                return None
            df = v.copy()
            if "time" not in df.columns:
                if isinstance(df.index, pd.DatetimeIndex):
                    ts = (df.index.view("int64") // 1_000_000).astype(np.float64)
                else:
                    ts = pd.to_numeric(pd.Series(df.index), errors="coerce").fillna(0).to_numpy(dtype=np.float64)
                df.insert(0, "time", ts)
            return df.reset_index(drop=True)
        except Exception as e:
            # Emit a warning so users can diagnose why L1/OFI becomes unavailable.
            warn_once(
                f"xtdata_get_market_data_ex_failed:{trade_date}",
                f"XtData: get_market_data_ex failed, fallback to get_market_data (OFI may be unavailable). "
                f"code={etf_code} date={trade_date} err={repr(e)}",
            )
            return None

    def _query_md(count_v: int) -> Any:
        raw_md = xtdata.get_market_data(
            field_list=fields,
            stock_list=[etf_code],
            period="tick",
            start_time=start,
            end_time=end,
            count=int(count_v),
            dividend_type="none",
            fill_data=True,
        )
        if isinstance(raw_md, dict) and raw_md:
            if all(isinstance(v, pd.DataFrame) for v in raw_md.values()):
                out = xtdata_field_dict_to_df(
                    raw_md,
                    stock_code=etf_code,
                    fields=["lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"],
                    time_field="time",
                )
                return out if out is not None else None

            if etf_code in raw_md:
                return raw_md[etf_code]
            return next(iter(raw_md.values()))
        return None

    # 1) Prefer get_market_data_ex (preserves depth arrays for L1 factors).
    df_ex = _query_ex(count_i)
    if isinstance(df_ex, pd.DataFrame) and not df_ex.empty:
        return df_ex
    # Some environments return empty DF for limited count while full-range query is available.
    if count_i > 0 and isinstance(df_ex, pd.DataFrame) and df_ex.empty:
        df_ex_all = _query_ex(-1)
        if isinstance(df_ex_all, pd.DataFrame) and not df_ex_all.empty:
            return df_ex_all

    # 2) Fallback to legacy get_market_data.
    out_md = _query_md(count_i)
    if out_md is not None:
        try:
            if len(out_md) > 0:
                return out_md
        except Exception:
            return out_md
    if count_i > 0:
        out_md_all = _query_md(-1)
        if out_md_all is not None:
            try:
                if len(out_md_all) > 0:
                    return out_md_all
            except Exception:
                return out_md_all

    # Keep empty objects when present so caller can distinguish "empty data" vs "query failed".
    if isinstance(df_ex, pd.DataFrame):
        return df_ex
    if out_md is not None:
        return out_md
    return None


def get_etf_info(etf_code: str) -> dict:
    xtdata = require_xtdata()
    fn = getattr(xtdata, "get_etf_info", None)
    if not callable(fn):
        raise RuntimeError("xtdata.get_etf_info 不可用")
    try:
        out = fn(etf_code)
        return out if isinstance(out, dict) else {}
    except TypeError:
        raw = fn()
        if isinstance(raw, dict):
            k = etf_code
            if k in raw and isinstance(raw[k], dict):
                return raw[k]
            k2 = etf_code.split(".")[0]
            if k2 in raw and isinstance(raw[k2], dict):
                return raw[k2]
        return {}


def get_daily_bars(stock_list: list[str], *, end_time: str, count: int) -> pd.DataFrame:
    xtdata = require_xtdata()
    raw = xtdata.get_market_data(
        field_list=["open", "high", "low", "close", "volume", "amount", "time"],
        stock_list=stock_list,
        period="1d",
        start_time="",
        end_time=end_time,
        count=int(count),
        dividend_type="none",
        fill_data=True,
    )
    close_df = raw.get("close") if isinstance(raw, dict) else None
    if close_df is None:
        return pd.DataFrame()

    out = pd.DataFrame({"time": close_df.columns})
    for field in ("open", "high", "low", "close", "volume", "amount"):
        df = raw.get(field)
        if df is None:
            continue
        if stock_list and stock_list[0] in df.index:
            values = df.loc[stock_list[0]].values
        else:
            values = df.iloc[0].values
        if field == "volume":
            # XtData 1d volume 为“手”，引擎内部统一换算为“股/份”。
            out[field] = pd.to_numeric(pd.Series(values), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64) * _LOT_SIZE
        else:
            out[field] = values
    return out


def calc_atr_10(daily_df: pd.DataFrame) -> float:
    if daily_df is None or daily_df.empty:
        return 0.0
    high = daily_df["high"].astype(float).to_numpy()
    low = daily_df["low"].astype(float).to_numpy()
    close = daily_df["close"].astype(float).to_numpy()
    prev_close = pd.Series(close).shift(1).to_numpy(copy=True)
    if prev_close.size:
        prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr = np.nan_to_num(tr, nan=0.0, posinf=0.0, neginf=0.0)
    atr = pd.Series(tr).rolling(10, min_periods=1).mean().to_numpy()
    v = float(atr[-1]) if atr.size else 0.0
    return v if np.isfinite(v) else 0.0


def prev_trade_date(trade_date: str, *, market: str = "SH") -> str:
    xtdata = require_xtdata()
    dates = xtdata.get_trading_dates(market, start_time="", end_time=trade_date, count=2)
    if isinstance(dates, list) and len(dates) >= 2:
        return _trade_date_from_xt_value(dates[-2])
    return ""


def latest_trade_date(trade_date: str, *, market: str = "SH") -> str:
    xtdata = require_xtdata()
    dates = xtdata.get_trading_dates(market, start_time="", end_time=trade_date, count=1)
    if isinstance(dates, list) and len(dates) >= 1:
        return _trade_date_from_xt_value(dates[-1])
    return ""
