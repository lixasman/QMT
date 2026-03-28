from __future__ import annotations

import re
import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from core.warn_utils import warn_once
from core.xtdata_parsing import xtdata_field_dict_to_df

try:
    from xtquant import xtdata as _xtdata  # type: ignore
except Exception:  # pragma: no cover
    _xtdata = None


_DEFAULT_LOT_SIZE = 100.0


def xtdata_available() -> bool:
    return _xtdata is not None


def require_xtdata() -> Any:
    if _xtdata is None:
        raise RuntimeError("xtdata unavailable: run inside QMT/XtQuant environment")
    # Ensure xtdata local paths (datadir) are initialized.
    # Without this, some historical tick queries may silently return empty.
    init_fn = getattr(_xtdata, "init_data_dir", None)
    if callable(init_fn):
        try:
            init_fn()
        except Exception:
            pass
    return _xtdata


def normalize_stock_code(code: str) -> str:
    """Normalize codes into 6-digit + suffix form: 600000.SH / 000001.SZ / 430xxx.BJ."""
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
    # BJ common code ranges (e.g., 43xxxx/83xxxx/87xxxx/92xxxx)
    if code6.startswith(("43", "83", "87", "92")):
        return f"{code6}.BJ"
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


def download_tick_data(stock_codes: list[str], trade_date: str, *, chunk_size: int = 80) -> dict[str, Any]:
    """Best-effort tick pre-download (small pools)."""
    xtdata = require_xtdata()
    codes = [normalize_stock_code(c) for c in list(stock_codes or [])]
    codes = [c for c in codes if c]
    chunks = [codes[i : i + int(chunk_size)] for i in range(0, len(codes), int(chunk_size))] if codes else []
    failed_chunks = 0
    supply_fn = getattr(xtdata, "supply_history_data", None)
    for chunk in chunks:
        try:
            # In some QMT setups, tick data is only available after "supply" is called.
            if callable(supply_fn):
                for code in chunk:
                    supply_fn(code, "tick", str(trade_date), str(trade_date))
            else:
                _download_history_data2_compat(chunk, "tick", start_time=str(trade_date), end_time=str(trade_date))
        except Exception as e:
            failed_chunks += 1
            warn_once(
                f"stock_tick_predownload_failed:{trade_date}:{failed_chunks}",
                f"Stock XtData: tick 棰勪笅杞藉け璐ワ紝宸查檷绾х户缁? date={trade_date} size={len(chunk)} err={repr(e)}",
                logger_name=__name__,
            )
    return {
        "trade_date": str(trade_date),
        "codes": int(len(codes)),
        "chunks": int(len(chunks)),
        "failed_chunks": int(failed_chunks),
    }


def download_daily_data(
    stock_codes: list[str],
    start_time: str,
    end_time: str,
    *,
    chunk_size: int = 80,
) -> dict[str, Any]:
    """Best-effort 1d pre-download to warm local K-line cache."""
    codes = [normalize_stock_code(c) for c in list(stock_codes or [])]
    codes = [c for c in codes if c]
    size = max(int(chunk_size), 1)
    chunks = [codes[i : i + size] for i in range(0, len(codes), size)] if codes else []
    failed_chunks = 0
    for chunk in chunks:
        try:
            _download_history_data2_compat(chunk, "1d", start_time=str(start_time), end_time=str(end_time))
        except Exception as e:
            failed_chunks += 1
            warn_once(
                f"stock_daily_predownload_failed:{start_time}:{end_time}:{failed_chunks}",
                f"Stock XtData: 1d 预下载失败，已降级继续: start={start_time} end={end_time} size={len(chunk)} err={repr(e)}",
                logger_name=__name__,
            )
    return {
        "start_time": str(start_time),
        "end_time": str(end_time),
        "codes": int(len(codes)),
        "chunks": int(len(chunks)),
        "failed_chunks": int(failed_chunks),
    }


def _trade_date_from_xt_value(v: Any) -> str:
    if isinstance(v, (int, float)) and v >= 10_000_000_000:
        tm = _time.localtime(float(v) / 1000.0)
        return _time.strftime("%Y%m%d", tm)
    d = str(v)
    m = re.search(r"(\d{8})", d)
    return m.group(1) if m else ""


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


def _try_parse_positive_float(v: Any) -> float:
    try:
        x = float(str(v).replace(",", ""))
    except Exception:
        return 0.0
    if np.isfinite(x) and x > 0:
        return float(x)
    return 0.0


def get_instrument_detail(stock_code: str) -> dict[str, Any]:
    xtdata = require_xtdata()
    code = normalize_stock_code(stock_code)
    if not code:
        return {}
    try:
        out = xtdata.get_instrument_detail(code, False)
        return out if isinstance(out, dict) else {}
    except Exception as e:
        warn_once(
            f"stock_instrument_detail_failed:{code}",
            f"Stock XtData: get_instrument_detail failed: code={code} err={repr(e)}",
            logger_name=__name__,
        )
        return {}


def get_float_shares_detail(stock_code: str, *, trade_date: str = "") -> dict[str, Any]:
    """Resolve stock float shares with source metadata.

    Returns:
    {
      "code": "600000.SH",
      "shares": float,           # in shares
      "source": str,             # xtdata_floatvolume / xtdata_totalvolume / none
      "degraded": bool,
      "reason": str,
    }
    """
    code = normalize_stock_code(stock_code)
    out: dict[str, Any] = {
        "code": code or str(stock_code).strip().upper(),
        "shares": 0.0,
        "source": "none",
        "degraded": True,
        "reason": "invalid_code",
        "trade_date": str(trade_date or ""),
    }
    if not code:
        return out

    info = get_instrument_detail(code)
    if not info:
        out["reason"] = "xtdata_missing"
        return out

    f = _try_parse_positive_float(info.get("FloatVolume"))
    if f > 0:
        out.update({"shares": float(f), "source": "xtdata_floatvolume", "degraded": False, "reason": ""})
        return out

    t = _try_parse_positive_float(info.get("TotalVolume"))
    if t > 0:
        out.update({"shares": float(t), "source": "xtdata_totalvolume", "degraded": True, "reason": "fallback_totalvolume"})
        return out

    out["reason"] = "xtdata_zero"
    return out


def get_divid_factors(stock_code: str, *, start_time: str = "", end_time: str = "") -> Any:
    xtdata = require_xtdata()
    code = normalize_stock_code(stock_code)
    if not code:
        return pd.DataFrame()

    fn = getattr(xtdata, "get_divid_factors", None)
    if callable(fn):
        try:
            out = fn(code, str(start_time or ""), str(end_time or ""))
            return out if out is not None else pd.DataFrame()
        except Exception as e:
            warn_once(
                f"stock_get_divid_factors_failed:{code}:{start_time}:{end_time}",
                f"Stock XtData: get_divid_factors failed, fallback to empty. code={code} start={start_time} end={end_time} err={repr(e)}",
                logger_name=__name__,
            )
            return pd.DataFrame()

    fn2 = getattr(xtdata, "getDividFactors", None)
    if callable(fn2):
        date_arg = str(end_time or start_time or "")
        try:
            out = fn2(code, date_arg)
            return out if out is not None else pd.DataFrame()
        except Exception as e:
            warn_once(
                f"stock_getDividFactors_failed:{code}:{date_arg}",
                f"Stock XtData: getDividFactors failed, fallback to empty. code={code} date={date_arg} err={repr(e)}",
                logger_name=__name__,
            )
            return pd.DataFrame()
    return pd.DataFrame()


def get_daily_bars(
    stock_list: list[str],
    *,
    end_time: str,
    count: int,
    dividend_type: str = "none",
    lot_size: float = _DEFAULT_LOT_SIZE,
    volume_in_lots: bool = True,
) -> pd.DataFrame:
    xtdata = require_xtdata()
    lst = [normalize_stock_code(x) for x in list(stock_list or [])]
    lst = [x for x in lst if x]
    if not lst:
        return pd.DataFrame()

    raw = xtdata.get_market_data(
        field_list=["open", "high", "low", "close", "volume", "amount", "time"],
        stock_list=lst,
        period="1d",
        start_time="",
        end_time=str(end_time),
        count=int(count),
        dividend_type=str(dividend_type or "none"),
        fill_data=True,
    )
    close_df = raw.get("close") if isinstance(raw, dict) else None
    if close_df is None:
        return pd.DataFrame()

    out = pd.DataFrame({"time": close_df.columns})
    code0 = lst[0]
    for field in ("open", "high", "low", "close", "volume", "amount"):
        df = raw.get(field) if isinstance(raw, dict) else None
        if df is None:
            continue
        if hasattr(df, "index") and code0 in getattr(df, "index", []):
            values = df.loc[code0].values
        else:
            values = df.iloc[0].values
        if field == "volume":
            vol = pd.to_numeric(pd.Series(values), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
            if bool(volume_in_lots):
                vol = vol * float(lot_size)
            out[field] = vol
        else:
            out[field] = pd.to_numeric(pd.Series(values), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
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


def get_local_tick_data(stock_code: str, trade_date: str) -> Any:
    xtdata = require_xtdata()
    code = normalize_stock_code(stock_code)
    if not code:
        return None
    start = f"{trade_date}093000"
    end = f"{trade_date}151000"
    raw = xtdata.get_local_data(
        field_list=["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"],
        stock_list=[code],
        period="tick",
        start_time=start,
        end_time=end,
        count=-1,
        dividend_type="none",
        fill_data=True,
    )
    if isinstance(raw, dict) and raw:
        v = raw.get(code)
        if v is None:
            v = next(iter(raw.values()))
        if isinstance(v, dict):
            v = next(iter(v.values())) if v else None
        if isinstance(v, pd.DataFrame):
            return v.reset_index(drop=True)
    if isinstance(raw, dict) and code in raw:
        return raw[code]
    if isinstance(raw, dict) and raw:
        v = next(iter(raw.values()))
        return v
    return None


def get_market_tick_data(stock_code: str, trade_date: str, *, count: int = -1) -> Any:
    xtdata = require_xtdata()
    code = normalize_stock_code(stock_code)
    if not code:
        return None
    start = f"{trade_date}093000"
    end = f"{trade_date}151000"

    # Prefer get_market_data_ex: preserves depth arrays via bidPrice/bidVol/askPrice/askVol.
    ex_fields = ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice", "bidVol", "askPrice", "askVol"]
    fields = ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"]
    count_i = -1 if int(count) <= 0 else int(count)

    def _query_ex(count_v: int) -> Optional[pd.DataFrame]:
        ex_fn = getattr(xtdata, "get_market_data_ex", None)
        if not callable(ex_fn):
            return None
        try:
            raw_ex = ex_fn(
                field_list=ex_fields,
                stock_list=[code],
                period="tick",
                start_time=start,
                end_time=end,
                count=int(count_v),
                dividend_type="none",
                fill_data=True,
            )
            if not isinstance(raw_ex, dict) or not raw_ex:
                return None
            v = raw_ex.get(code)
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
            warn_once(
                f"stock_xtdata_get_market_data_ex_failed:{trade_date}:{code}",
                f"Stock XtData: get_market_data_ex failed, fallback to get_market_data. code={code} date={trade_date} err={repr(e)}",
                logger_name=__name__,
            )
            return None

    def _query_md(count_v: int) -> Any:
        raw_md = xtdata.get_market_data(
            field_list=fields,
            stock_list=[code],
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
                    stock_code=code,
                    fields=["lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"],
                    time_field="time",
                )
                return out if out is not None else None
            if code in raw_md:
                return raw_md[code]
            return next(iter(raw_md.values()))
        return None

    df_ex = _query_ex(count_i)
    if isinstance(df_ex, pd.DataFrame) and not df_ex.empty:
        return df_ex
    if count_i > 0 and isinstance(df_ex, pd.DataFrame) and df_ex.empty:
        df_ex_all = _query_ex(-1)
        if isinstance(df_ex_all, pd.DataFrame) and not df_ex_all.empty:
            return df_ex_all

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

    # Some instruments require explicit supply of historical tick data before queries return rows.
    supply_fn = getattr(xtdata, "supply_history_data", None)
    if callable(supply_fn):
        try:
            supply_fn(code, "tick", str(trade_date), str(trade_date))
        except Exception as e:
            warn_once(
                f"stock_xtdata_supply_tick_failed:{trade_date}:{code}",
                f"Stock XtData: supply_history_data failed, tick may be empty. code={code} date={trade_date} err={repr(e)}",
                logger_name=__name__,
            )
        else:
            df_ex2 = _query_ex(count_i)
            if isinstance(df_ex2, pd.DataFrame) and not df_ex2.empty:
                return df_ex2
            if count_i > 0 and isinstance(df_ex2, pd.DataFrame) and df_ex2.empty:
                df_ex2_all = _query_ex(-1)
                if isinstance(df_ex2_all, pd.DataFrame) and not df_ex2_all.empty:
                    return df_ex2_all

            out_md2 = _query_md(count_i)
            if out_md2 is not None:
                try:
                    if len(out_md2) > 0:
                        return out_md2
                except Exception:
                    return out_md2
            if count_i > 0:
                out_md2_all = _query_md(-1)
                if out_md2_all is not None:
                    try:
                        if len(out_md2_all) > 0:
                            return out_md2_all
                    except Exception:
                        return out_md2_all

    if isinstance(df_ex, pd.DataFrame):
        return df_ex
    if out_md is not None:
        return out_md
    return None


def ensure_parent_dir(path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

