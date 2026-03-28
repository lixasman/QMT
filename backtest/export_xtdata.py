from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .fail_fast_warn import degrade_once, warn_once

try:
    from xtquant import xtdata  # type: ignore
except Exception:
    xtdata = None

logger = logging.getLogger("backtest.export")


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _download_history(stock_list: list[str], period: str, start_time: str, end_time: str) -> None:
    if xtdata is None:
        degrade_once(
            "bt_export_xtdata_missing",
            "Backtest export requires xtquant.xtdata, but import failed.",
            logger_name="backtest.export",
        )
        logger.error("xtquant.xtdata is not available")
        raise RuntimeError("xtquant.xtdata is not available")
    fn2 = getattr(xtdata, "download_history_data2", None)
    if callable(fn2):
        for args in (
            (stock_list, period, start_time, end_time, None, None),
            (stock_list, period, start_time, end_time, None),
            (stock_list, period, start_time, end_time),
        ):
            try:
                fn2(*args)
                logger.debug(
                    "download_history_data2 success | period=%s start=%s end=%s codes=%s argc=%s",
                    str(period),
                    str(start_time),
                    str(end_time),
                    len(stock_list),
                    len(args),
                )
                return
            except TypeError as e:
                logger.debug("download_history_data2 signature mismatch | argc=%s err=%r", len(args), e)

    warn_once(
        f"bt_export_download_fallback:{str(period)}",
        (
            "Backtest export fallback to per-code download_history_data because download_history_data2 is unavailable "
            f"or incompatible. period={period}"
        ),
        logger_name="backtest.export",
    )
    for code in stock_list:
        ok = False
        for args in (
            (code, period, start_time, end_time, None),
            (code, period, start_time, end_time),
            (code, period, start_time),
        ):
            try:
                xtdata.download_history_data(*args)
                logger.debug(
                    "download_history_data success | code=%s period=%s start=%s end=%s argc=%s",
                    str(code),
                    str(period),
                    str(start_time),
                    str(end_time),
                    len(args),
                )
                ok = True
                break
            except TypeError as e:
                logger.debug(
                    "download_history_data signature mismatch | code=%s period=%s argc=%s err=%r",
                    str(code),
                    str(period),
                    len(args),
                    e,
                )
        if not ok:
            degrade_once(
                f"bt_export_download_failed:{str(code)}:{str(period)}",
                f"Backtest export failed to download history with all known signatures. code={code} period={period}",
                logger_name="backtest.export",
            )
            raise RuntimeError(f"download history failed for code={code} period={period}")


def _codes_from_inputs(*, codes: str, codes_file: str) -> list[str]:
    if str(codes_file).strip():
        p = Path(str(codes_file))
        if not p.exists():
            logger.error("codes file not found | path=%s", str(p))
            raise RuntimeError(f"codes file not found: {p}")
        raw = p.read_text(encoding="utf-8")
    else:
        raw = str(codes or "")
    out: list[str] = []
    seen: set[str] = set()
    for token in raw.replace("\t", ",").replace("\n", ",").replace(" ", ",").split(","):
        code = str(token).strip().upper()
        if not code:
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
    logger.info("codes prepared | count=%s source=%s", len(out), "file" if str(codes_file).strip() else "inline")
    return out


def _extract_level1_numeric(values: object) -> float:
    try:
        if isinstance(values, (list, tuple)):
            if len(values) <= 0:
                return 0.0
            return float(values[0])
    except Exception:
        return 0.0
    try:
        import numpy as np  # type: ignore

        if isinstance(values, np.ndarray):
            if values.size <= 0:
                return 0.0
            return float(values[0])
    except Exception:
        pass
    try:
        return float(values)  # type: ignore[arg-type]
    except Exception:
        return 0.0


def _normalize_tick_df(*, raw: object, code: str):
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore

    payload = raw
    if isinstance(raw, dict):
        payload = raw.get(str(code))
        if payload is None and raw:
            payload = next(iter(raw.values()))

    if payload is None:
        return None
    if isinstance(payload, pd.DataFrame):
        src = payload.copy()
    elif hasattr(payload, "dtype") and getattr(getattr(payload, "dtype", None), "names", None):
        src = pd.DataFrame(payload)
    elif isinstance(payload, list):
        src = pd.DataFrame(payload)
    else:
        return None
    if src.empty:
        return None

    cols = {str(c).lower(): str(c) for c in src.columns}

    def _col(*names: str) -> str | None:
        for n in names:
            k = str(n).strip().lower()
            if k in cols:
                return cols[k]
        return None

    def _series(*names: str, level1: bool = False, default: float = 0.0):
        c = _col(*names)
        if c is None:
            return np.full(len(src), float(default), dtype=np.float64)
        ser = src[c]
        if not level1:
            return pd.to_numeric(ser, errors="coerce").fillna(float(default)).to_numpy(dtype=np.float64)
        arr = np.zeros(len(src), dtype=np.float64)
        for i, v in enumerate(ser.tolist()):
            arr[i] = float(_extract_level1_numeric(v))
        return arr

    c_time = _col("time", "datetime", "dt")
    if c_time is not None:
        time_raw = src[c_time]
        if hasattr(time_raw, "dtype") and np.issubdtype(getattr(time_raw, "dtype", object), np.datetime64):
            ns = pd.to_datetime(time_raw, errors="coerce").to_numpy(dtype="datetime64[ns]").view("int64")
            time_arr = (ns // 1_000_000).astype(np.float64)
            nat = np.iinfo(np.int64).min
            time_arr[ns == nat] = 0.0
        else:
            time_arr = pd.to_numeric(time_raw, errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
    elif isinstance(src.index, pd.DatetimeIndex):
        ns = src.index.view("int64")
        time_arr = (ns // 1_000_000).astype(np.float64)
    else:
        time_arr = pd.to_numeric(pd.Series(src.index), errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)

    last_arr = _series("lastprice", "last_price", "close", "c", "price", "last")
    high_arr = _series("high", default=0.0)
    low_arr = _series("low", default=0.0)
    amount_arr = _series("amount", "amt", "turnover")
    volume_arr = _series("volume", "vol", "v", "pvolume")
    bid1_arr = _series("bidprice1", "bid_price1", "bid1", "bidprice", level1=True)
    bid1_vol_arr = _series("bidvol1", "bid_vol1", "bid1_vol", "bidvol", level1=True)
    ask1_arr = _series("askprice1", "ask_price1", "ask1", "askprice", level1=True)
    ask1_vol_arr = _series("askvol1", "ask_vol1", "ask1_vol", "askvol", level1=True)
    iopv_arr = _series("iopv", "iopv_price", default=0.0)
    stock_status_arr = _series("stockstatus", "stock_status", "status", default=0.0)

    out = pd.DataFrame(
        {
            "time": time_arr,
            "lastPrice": last_arr,
            "high": high_arr,
            "low": low_arr,
            "amount": amount_arr,
            "volume": volume_arr,
            "bidPrice1": bid1_arr,
            "bidVol1": bid1_vol_arr,
            "askPrice1": ask1_arr,
            "askVol1": ask1_vol_arr,
            "iopv": iopv_arr,
            "stockStatus": stock_status_arr,
        }
    )
    out = out.sort_values("time", kind="mergesort").reset_index(drop=True)
    return out


def _query_tick_raw(*, code: str, start_time: str, end_time: str):
    if xtdata is None:
        raise RuntimeError("xtquant.xtdata is not available")

    ex_fn = getattr(xtdata, "get_market_data_ex", None)
    if callable(ex_fn):
        try:
            return ex_fn(
                field_list=["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice", "bidVol", "askPrice", "askVol", "stockStatus"],
                stock_list=[str(code)],
                period="tick",
                start_time=str(start_time or ""),
                end_time=str(end_time or ""),
                count=-1,
                dividend_type="none",
                fill_data=True,
            )
        except Exception as e:
            warn_once(
                f"bt_export_tick_ex_fallback:{str(code)}",
                (
                    "Backtest export get_market_data_ex(tick) failed, fallback to get_market_data. "
                    f"code={code} err={repr(e)}"
                ),
                logger_name="backtest.export",
            )

    last_err: Exception | None = None
    for fields in (
        ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1", "stockStatus", "iopv"],
        ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1", "stockStatus"],
    ):
        try:
            return xtdata.get_market_data(
                field_list=fields,
                stock_list=[str(code)],
                period="tick",
                start_time=str(start_time or ""),
                end_time=str(end_time or ""),
                count=-1,
                dividend_type="none",
                fill_data=True,
            )
        except Exception as e:
            last_err = e
    if last_err is not None:
        raise last_err
    return None


def _export_one(*, code: str, period: str, start_time: str, end_time: str, out_path: Path) -> int:
    if xtdata is None:
        logger.error("xtquant.xtdata is not available")
        raise RuntimeError("xtquant.xtdata is not available")
    try:
        from core.xtdata_parsing import xtdata_field_dict_to_df
    except Exception as e:
        degrade_once(
            "bt_export_numpy_pandas_missing",
            "Backtest export requires numpy/pandas in current python env.",
            logger_name="backtest.export",
        )
        logger.error("xtdata export requires numpy/pandas in current python env | err=%r", e)
        raise RuntimeError("xtdata export requires numpy/pandas in current python env") from e
    try:
        _download_history([str(code)], str(period), str(start_time), str(end_time))
    except Exception as e:
        warn_once(
            f"bt_export_code_download_failed:{str(code)}:{str(period)}",
            (
                "Backtest export per-code download failed, continue with current local cache. "
                f"code={code} period={period} err={repr(e)}"
            ),
            logger_name="backtest.export",
        )

    period_l = str(period).strip().lower()
    df = None
    prev_rows = -1
    stable = 0
    for attempt in range(6):
        if period_l == "tick":
            try:
                raw = _query_tick_raw(code=str(code), start_time=str(start_time), end_time=str(end_time))
            except Exception as e:
                degrade_once(
                    f"bt_export_tick_query_failed:{str(code)}",
                    f"Backtest export tick query failed. code={code} err={repr(e)}",
                    logger_name="backtest.export",
                )
                logger.error("tick query failed | code=%s err=%r", str(code), e)
                raise
            df = _normalize_tick_df(raw=raw, code=str(code))
        else:
            try:
                raw = xtdata.get_market_data(
                    field_list=["time", "open", "high", "low", "close", "volume", "amount"],
                    stock_list=[str(code)],
                    period=str(period),
                    start_time=str(start_time or ""),
                    end_time=str(end_time or ""),
                    count=-1,
                )
            except Exception as e:
                degrade_once(
                    f"bt_export_get_market_data_failed:{str(code)}:{str(period)}",
                    f"Backtest export get_market_data failed. code={code} period={period} err={repr(e)}",
                    logger_name="backtest.export",
                )
                logger.error("get_market_data failed | code=%s period=%s err=%r", str(code), str(period), e)
                raise
            df = xtdata_field_dict_to_df(raw, stock_code=str(code), fields=["open", "high", "low", "close", "volume", "amount"])
        rows = 0 if df is None else int(len(df))
        if rows == int(prev_rows):
            stable += 1
        else:
            prev_rows = int(rows)
            stable = 0
        if stable >= 1:
            break
        if attempt < 5:
            time.sleep(1.0)

    if df is None or df.empty:
        warn_once(
            f"bt_export_empty_df:{str(code)}:{str(period)}",
            f"Backtest export produced empty dataframe. code={code} period={period}",
            logger_name="backtest.export",
        )
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.debug("export done | code=%s period=%s rows=%s file=%s", str(code), str(period), len(df), str(out_path))
    return int(len(df))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backtest.export_xtdata")
    p.add_argument("--codes", default="", help="comma-separated ETF codes, e.g. 512480.SH,159363.SZ")
    p.add_argument("--codes-file", default="", help="txt/csv file with ETF codes")
    p.add_argument("--start", required=True, help="start date time, e.g. 20250101 or 20250101000000")
    p.add_argument("--end", default="", help="end date time, e.g. 20251231 or empty for now")
    p.add_argument("--periods", default="1d,tick", help="comma-separated periods, default 1d,tick")
    p.add_argument("--out-root", default="backtest/data", help="output root dir")
    return p


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    ns = build_parser().parse_args(argv)
    logger.info(
        "export start | start=%s end=%s periods=%s out_root=%s",
        str(ns.start),
        str(ns.end),
        str(ns.periods),
        str(ns.out_root),
    )
    if xtdata is None:
        degrade_once(
            "bt_export_xtdata_missing_main",
            "Backtest export cannot run because xtquant.xtdata is unavailable.",
            logger_name="backtest.export",
        )
        raise RuntimeError("xtquant.xtdata is not available")

    codes = _codes_from_inputs(codes=str(ns.codes), codes_file=str(ns.codes_file))
    if not codes:
        logger.error("empty codes")
        raise RuntimeError("empty codes")

    periods = [x.strip() for x in str(ns.periods).split(",") if x.strip()]
    if not periods:
        periods = ["1d", "tick"]
    out_root = Path(str(ns.out_root))

    for period in periods:
        logger.info("download start | period=%s codes=%s", str(period), len(codes))
        _download_history(codes, str(period), str(ns.start), str(ns.end))
        period_rows = 0
        for code in codes:
            fname = f"{str(code).upper().replace('.', '_')}.csv"
            out_path = out_root / str(period) / fname
            n = _export_one(
                code=str(code),
                period=str(period),
                start_time=str(ns.start),
                end_time=str(ns.end),
                out_path=out_path,
            )
            period_rows += int(n)
            if int(n) <= 0:
                logger.warning("export empty | period=%s code=%s file=%s", str(period), str(code), str(out_path))
            else:
                logger.info("export rows | period=%s code=%s rows=%s file=%s", str(period), str(code), int(n), str(out_path))
            print(f"{period} {code} rows={n} file={out_path}")
        logger.info("period done | period=%s total_rows=%s codes=%s", str(period), int(period_rows), len(codes))
    logger.info("export done | periods=%s codes=%s out_root=%s", len(periods), len(codes), str(out_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
