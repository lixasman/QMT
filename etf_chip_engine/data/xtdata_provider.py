from __future__ import annotations

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


_TICK_DOWNLOAD_STATE_VERSION = 1
_DEFAULT_TICK_STATE_DIR = Path("output") / "cache" / "chip_tick_download"


def xtdata_available() -> bool:
    return _xtdata is not None


def require_xtdata() -> Any:
    if _xtdata is None:
        raise RuntimeError("xtdata 不可用：请在 QMT/XtQuant 环境中运行")
    return _xtdata


def normalize_etf_code(code: str) -> str:
    s = str(code).strip().upper()
    if re.fullmatch(r"\d{6}\.(SZ|SH)", s):
        return s
    m = re.search(r"(\d{6})", s)
    if not m:
        return ""
    code6 = m.group(1)
    if code6.startswith(("5", "6", "9")):
        return f"{code6}.SH"
    return f"{code6}.SZ"


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


def get_total_shares(etf_code: str) -> float:
    xtdata = require_xtdata()
    info = xtdata.get_instrument_detail(etf_code, False)
    if not isinstance(info, dict):
        return 0.0
    for k in ("TotalVolume", "FloatVolume"):
        v = info.get(k)
        try:
            f = float(v)
        except Exception:
            continue
        if f > 0:
            return f
    return 0.0


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


def _download_tick_data_with_timeout(
    etf_codes: list[str],
    trade_date: str,
    *,
    timeout_sec: int,
) -> tuple[list[str], list[str]]:
    codes = _normalize_code_list(etf_codes)
    if not codes:
        return [], []

    ok, err, _ = _download_tick_data_subprocess(codes, trade_date, timeout_sec=timeout_sec)
    if ok:
        return codes, []
    # Non-timeout import/runtime environment errors usually affect all codes;
    # avoid recursive fan-out in that case.
    if ("No module named" in err) or ("ImportError" in err):
        return [], codes
    # If the whole chunk failed, split to isolate problematic codes and keep progress.
    if len(codes) <= 1:
        return [], codes
    mid = max(len(codes) // 2, 1)
    left_done, left_failed = _download_tick_data_with_timeout(codes[:mid], trade_date, timeout_sec=timeout_sec)
    right_done, right_failed = _download_tick_data_with_timeout(codes[mid:], trade_date, timeout_sec=timeout_sec)
    return left_done + right_done, left_failed + right_failed


def ensure_tick_data_downloaded(
    etf_codes: list[str],
    trade_date: str,
    *,
    force: bool = False,
    chunk_size: int = 200,
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
) -> bool:
    code = normalize_etf_code(etf_code)
    if not code:
        return False

    state = _load_tick_state(trade_date=trade_date, state_dir=state_dir)
    retry_set = set(_normalize_code_list(state.get("empty_retry_codes", [])))
    if code in retry_set:
        return False

    try:
        download_tick_data([code], trade_date)
    except Exception as e:
        warn_once(
            f"tick_empty_retry_failed:{trade_date}:{code}",
            f"XtData: 空数据重试下载失败: code={code} date={trade_date} err={repr(e)}",
        )

    retry_set.add(code)
    downloaded_set = set(_normalize_code_list(state.get("downloaded_codes", [])))
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
    ex_fields = ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice", "bidVol", "askPrice", "askVol", "iopv"]
    fields = ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1", "iopv"]
    count_i = -1 if int(count) <= 0 else int(count)
    ex_fn = getattr(xtdata, "get_market_data_ex", None)
    if callable(ex_fn):
        try:
            raw = ex_fn(
                field_list=ex_fields,
                stock_list=[etf_code],
                period="tick",
                start_time=start,
                end_time=end,
                count=count_i,
                dividend_type="none",
                fill_data=True,
            )
            if isinstance(raw, dict) and raw:
                v = raw.get(etf_code)
                if v is None:
                    v = next(iter(raw.values()))
                if isinstance(v, pd.DataFrame):
                    df = v.copy()
                    if "time" not in df.columns:
                        if isinstance(df.index, pd.DatetimeIndex):
                            ts = (df.index.view("int64") // 1_000_000).astype(np.float64)
                        else:
                            ts = pd.to_numeric(pd.Series(df.index), errors="coerce").fillna(0).to_numpy(dtype=np.float64)
                        df.insert(0, "time", ts)
                    return df.reset_index(drop=True)
        except Exception:
            pass

    raw = xtdata.get_market_data(
        field_list=fields,
        stock_list=[etf_code],
        period="tick",
        start_time=start,
        end_time=end,
        count=count_i,
        dividend_type="none",
        fill_data=True,
    )
    if isinstance(raw, dict) and raw:
        if all(isinstance(v, pd.DataFrame) for v in raw.values()):
            out = xtdata_field_dict_to_df(
                raw,
                stock_code=etf_code,
                fields=["lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1", "iopv"],
                time_field="time",
            )
            return out if out is not None else None

        if etf_code in raw:
            return raw[etf_code]
        return next(iter(raw.values()))
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
            out[field] = df.loc[stock_list[0]].values
        else:
            out[field] = df.iloc[0].values
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
