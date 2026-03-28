from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re
import os
from pathlib import Path
import threading
import time
from typing import Any, Optional

import pandas as pd
import logging

from core.warn_utils import info_once

try:
    from xtquant import xtdata as _xtdata  # type: ignore
except Exception:  # pragma: no cover
    _xtdata = None


logger = logging.getLogger(__name__)
_DOWNGRADE_WARNED: set[str] = set()


def _warn_downgrade_once(key: str, msg: str) -> None:
    if key in _DOWNGRADE_WARNED:
        return
    _DOWNGRADE_WARNED.add(key)
    logger.warning(msg)


NEGATIVE_NAME_KEYWORDS = [
    "货币",
    "债",
    "存单",
    "豆粕",
    "黄金",
    "QDII",
    "跨境",
    "海外",
    "美国",
    "纳斯达克",
    "标普",
    "道琼斯",
    "巴西",
    "日经",
    "德国",
    "法国",
    "港股",
    "恒生",
    "H股",
    "沪港深",
    "联接",
    "香港",
    "红利"
]


def _xtdata_available() -> bool:
    return _xtdata is not None


def _normalize_etf_code_6_to_xt(code6: str) -> str:
    s = str(code6).strip()
    if not re.fullmatch(r"\d{6}", s):
        return ""
    if s.startswith("5"):
        return f"{s}.SH"
    return f"{s}.SZ"


def _normalize_input_code(code: str) -> str:
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if re.fullmatch(r"\d{6}", s):
        return _normalize_etf_code_6_to_xt(s)
    return s


def load_universe_etf_codes(path: str | Path) -> list[str]:
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        code = _normalize_input_code(line)
        if code:
            out.append(code)
    return out


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        v = float(raw)
        if math.isfinite(v) and v > 0:
            return float(v)
    except Exception as e:
        _warn_downgrade_once(
            f"env_float_parse_failed:{name}",
            f"HotETF: 环境变量解析失败，已降级默认值。name={name} raw={raw} err={repr(e)}",
        )
    return float(default)


def _run_with_timeout(
    fn,
    *,
    timeout_seconds: float,
    label: str,
    default,
    propagate: tuple[type[BaseException], ...] = (),
):
    if timeout_seconds <= 0:
        return fn()
    done = threading.Event()
    box: dict[str, Any] = {"value": default, "exc": None, "ok": False}

    def _target() -> None:
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as e:  # pragma: no cover
            box["exc"] = e
        finally:
            done.set()

    th = threading.Thread(
        target=_target,
        name=f"finintel-hot-timeout-{label}",
        daemon=True,
    )
    th.start()

    if not done.wait(timeout=float(timeout_seconds)):
        logger.warning("HotETF: %s 超时(>%ss)，已跳过", label, int(timeout_seconds))
        return default

    err = box.get("exc")
    if err is not None:
        if propagate and isinstance(err, propagate):
            raise err
        logger.warning("HotETF: %s 失败: %s", label, repr(err))
        return default
    return box.get("value", default)


def _download_history_data2_compat(stock_list: list[str], period: str, *, start_time: str) -> None:
    if not _xtdata_available() or not stock_list:
        return
    timeout_seconds = _env_float("FININTEL_XTDATA_TIMEOUT_SECONDS", 35.0)
    try:
        _run_with_timeout(
            lambda: _xtdata.download_history_data2(stock_list, period, start_time, ""),
            timeout_seconds=timeout_seconds,
            label="xtdata.download_history_data2",
            default=None,
            propagate=(TypeError,),
        )
    except TypeError:
        _warn_downgrade_once(
            "xt_selector_download_sig_fallback_4args",
            "HotETF: xtdata.download_history_data2 4参签名不可用，降级尝试 5参签名。",
        )
        _run_with_timeout(
            lambda: _xtdata.download_history_data2(stock_list, period, start_time, "", None),
            timeout_seconds=timeout_seconds,
            label="xtdata.download_history_data2",
            default=None,
        )


def _get_market_data_df(stock_list: list[str], *, count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    timeout_seconds = _env_float("FININTEL_XTDATA_TIMEOUT_SECONDS", 35.0)
    raw = _run_with_timeout(
        lambda: _xtdata.get_market_data(
            field_list=["close", "amount"],
            stock_list=stock_list,
            period="1d",
            start_time="",
            end_time="",
            count=count,
            dividend_type="none",
            fill_data=True,
        ),
        timeout_seconds=timeout_seconds,
        label="xtdata.get_market_data(close,amount)",
        default={},
    )
    if not isinstance(raw, dict):
        raise RuntimeError("xtdata.get_market_data 返回非 dict")
    close_df = raw.get("close")
    amount_df = raw.get("amount")
    if close_df is None or amount_df is None:
        raise RuntimeError("xtdata.get_market_data 缺少 close/amount 返回")
    return close_df, amount_df


def _iter_chunks(xs: list[str], chunk_size: int) -> list[list[str]]:
    if chunk_size <= 0:
        return [xs]
    return [xs[i : i + chunk_size] for i in range(0, len(xs), chunk_size)]


def _build_part_from_close_amount(close_df: pd.DataFrame, amount_df: pd.DataFrame) -> pd.DataFrame:
    part = pd.DataFrame(
        {
            "close": close_df.stack(),
            "amount": amount_df.stack(),
        }
    )
    part.index = part.index.set_names(["code", "time"])
    return part.reset_index()


def _fetch_daily_chunk_parts(
    chunk: list[str],
    *,
    start_time: str,
    history_days: int,
    depth: int = 0,
    max_split_depth: int = 3,
) -> list[pd.DataFrame]:
    if not chunk:
        return []
    t0 = time.perf_counter()
    try:
        _download_history_data2_compat(chunk, "1d", start_time=start_time)
        close_df, amount_df = _get_market_data_df(chunk, count=history_days)
        part = _build_part_from_close_amount(close_df, amount_df)
        elapsed = max(time.perf_counter() - t0, 0.0)
        logger.warning(
            "HotETF: 日线拉取完成 depth=%s chunk_size=%s rows=%s elapsed=%.2fs",
            int(depth),
            len(chunk),
            int(len(part)),
            float(elapsed),
        )
        return [part]
    except Exception as e:
        if len(chunk) > 1 and depth < max_split_depth:
            logger.warning(
                "HotETF: 日线拉取失败，分裂重试 depth=%s chunk_size=%s err=%s",
                int(depth),
                len(chunk),
                repr(e),
            )
            mid = max(1, len(chunk) // 2)
            left = _fetch_daily_chunk_parts(
                chunk[:mid],
                start_time=start_time,
                history_days=history_days,
                depth=depth + 1,
                max_split_depth=max_split_depth,
            )
            right = _fetch_daily_chunk_parts(
                chunk[mid:],
                start_time=start_time,
                history_days=history_days,
                depth=depth + 1,
                max_split_depth=max_split_depth,
            )
            return left + right
        logger.warning(
            "HotETF: 日线拉取失败，已跳过 depth=%s chunk_size=%s err=%s",
            int(depth),
            len(chunk),
            repr(e),
        )
        return []


def fetch_daily_history_for_codes(codes: list[str], *, history_days: int = 2, chunk_size: int = 1200) -> pd.DataFrame:
    if not _xtdata_available():
        raise RuntimeError("xtdata 不可用，无法执行ETF日线筛选")
    code_list = [_normalize_input_code(code) for code in codes if _normalize_input_code(code)]
    if not code_list:
        return pd.DataFrame(columns=["code", "time", "close", "amount"])
    start_time = (datetime.now().astimezone() - timedelta(days=max(int(history_days) * 5, 20))).strftime("%Y%m%d")
    frames: list[pd.DataFrame] = []
    for chunk in _iter_chunks(code_list, chunk_size):
        frames.extend(
            _fetch_daily_chunk_parts(
                chunk,
                start_time=start_time,
                history_days=max(int(history_days), 2),
                depth=0,
                max_split_depth=3,
            )
        )
    if not frames:
        return pd.DataFrame(columns=["code", "time", "close", "amount"])
    return pd.concat(frames, ignore_index=True)


def load_latest_daily_snapshot(codes: list[str]) -> pd.DataFrame:
    history = fetch_daily_history_for_codes(codes, history_days=2)
    if history.empty:
        return pd.DataFrame(columns=["code", "name", "prev_close", "close"])
    history = history.copy()
    history["code"] = history["code"].map(_normalize_input_code)
    history = history[history["code"].astype(bool)]
    history = history.sort_values(["code", "time"], kind="mergesort")
    rows: list[dict[str, object]] = []
    for code, grp in history.groupby("code", sort=False):
        grp2 = grp.dropna(subset=["close"]).tail(2)
        if len(grp2) < 2:
            logger.warning("HotETF: 最新两日日线不足，已跳过 code=%s", code)
            continue
        prev_row = grp2.iloc[0]
        curr_row = grp2.iloc[1]
        rows.append(
            {
                "code": str(code),
                "name": str(curr_row.get("name") or prev_row.get("name") or ""),
                "prev_close": float(prev_row["close"]),
                "close": float(curr_row["close"]),
            }
        )
    return pd.DataFrame(rows, columns=["code", "name", "prev_close", "close"])


def select_universe_daily_gainers(
    *,
    universe_path: str | Path,
    gain_threshold: float = 0.01,
    include_all: bool = False,
) -> pd.DataFrame:
    codes = load_universe_etf_codes(universe_path)
    if not codes:
        return pd.DataFrame(columns=["code", "name", "prev_close", "close", "pct_change", "source_tag"])
    snap = load_latest_daily_snapshot(codes)
    if snap.empty:
        return pd.DataFrame(columns=["code", "name", "prev_close", "close", "pct_change", "source_tag"])
    out = snap.copy()
    out["pct_change"] = out["close"] / out["prev_close"] - 1.0
    if include_all:
        out["source_tag"] = "universe_all_50"
        return out.reset_index(drop=True)
    threshold = float(gain_threshold)
    out = out[out["pct_change"] > (threshold + 1e-12)].copy()
    out["source_tag"] = "universe_up_gt_1pct"
    return out.reset_index(drop=True)


def _get_all_etf_universe() -> pd.DataFrame:
    if not _xtdata_available():
        raise RuntimeError("xtdata 不可用，无法从 XtQuant 获取 ETF 行情数据")

    codes: list[str] = []
    names: dict[str, str] = {}

    download_fn = getattr(_xtdata, "download_etf_info", None)
    get_fn = getattr(_xtdata, "get_etf_info", None)
    if callable(download_fn) and callable(get_fn):
        try:
            download_fn()
            etf_info = get_fn()
        except Exception as e:
            info_once("finintel_xtdata_get_etf_info_failed", f"FinIntel: xtdata.get_etf_info 获取失败，已降级到 sector 扫描: err={repr(e)}", logger_name=__name__)
            etf_info = None

        if isinstance(etf_info, dict):
            for k, v in etf_info.items():
                if isinstance(k, str):
                    code = _normalize_etf_code_6_to_xt(k.split(".")[0])
                    if code:
                        codes.append(code)
                        if isinstance(v, dict):
                            for key in ("name", "名称", "基金简称", "基金名称", "ETF名称"):
                                nm = v.get(key)
                                if isinstance(nm, str) and nm.strip():
                                    names[code] = nm.strip()
                                    break
            if not codes:
                _warn_downgrade_once(
                    "etf_universe_xt_etf_info_empty",
                    "HotETF: xtdata.get_etf_info 返回空/不可解析，已降级到 sector 扫描。",
                )
        elif etf_info is not None:
            _warn_downgrade_once(
                "etf_universe_xt_etf_info_not_dict",
                f"HotETF: xtdata.get_etf_info 返回结构异常(type={type(etf_info).__name__})，已降级到 sector 扫描。",
            )

    if not codes:
        _warn_downgrade_once(
            "etf_universe_fallback_sector",
            "HotETF: ETF池构建已降级到 sector 扫描。",
        )
        download_sector = getattr(_xtdata, "download_sector_data", None)
        if callable(download_sector):
            try:
                download_sector()
            except Exception as e:
                info_once("finintel_download_sector_failed", f"FinIntel: download_sector_data 失败，已降级继续: err={repr(e)}", logger_name=__name__)
        try:
            sectors = _xtdata.get_sector_list()
        except Exception as e:
            info_once("finintel_get_sector_list_failed", f"FinIntel: get_sector_list 失败，已降级为空列表: err={repr(e)}", logger_name=__name__)
            sectors = []
        if isinstance(sectors, list) and sectors:
            sec_candidates = [s for s in sectors if isinstance(s, str) and "ETF" in s.upper() and "过期" not in s]
            sec_codes: set[str] = set()
            sector_member_failed = 0
            for sec in sec_candidates:
                try:
                    lst = _xtdata.get_stock_list_in_sector(sec)
                except Exception:
                    sector_member_failed += 1
                    continue
                if not isinstance(lst, list):
                    continue
                for c in lst:
                    if not isinstance(c, str):
                        continue
                    cc = c.strip().upper()
                    if re.fullmatch(r"\d{6}\.(SZ|SH)", cc):
                        sec_codes.add(cc)
                    else:
                        cc2 = _normalize_etf_code_6_to_xt(cc.split(".")[0])
                        if cc2:
                            sec_codes.add(cc2)

            if sector_member_failed > 0:
                _warn_downgrade_once(
                    "etf_universe_sector_member_failed",
                    f"HotETF: sector成分拉取部分失败，已降级跳过。failed={int(sector_member_failed)} total={int(len(sec_candidates))}",
                )

            if sec_codes:
                codes = sorted(sec_codes)
                instrument_failed = 0
                for c in codes:
                    try:
                        info = _xtdata.get_instrument_detail(c, False)
                    except Exception:
                        instrument_failed += 1
                        continue
                    if isinstance(info, dict):
                        nm = info.get("InstrumentName")
                        if isinstance(nm, str) and nm.strip():
                            names[c] = nm.strip()
                if instrument_failed > 0:
                    _warn_downgrade_once(
                        "etf_universe_instrument_detail_failed",
                        f"HotETF: instrument_detail 拉取部分失败，已降级部分名称缺失。failed={int(instrument_failed)} total={int(len(codes))}",
                    )

    if not codes:
        _warn_downgrade_once(
            "etf_universe_fallback_akshare",
            "HotETF: ETF池构建已降级到 akshare 现货列表。",
        )
        try:
            import akshare as ak  # type: ignore
        except Exception:
            raise RuntimeError("无法获取 ETF 列表：xtdata.get_etf_info 不可用且 akshare 未安装")
        df = ak.fund_etf_spot_em()
        if df is None or getattr(df, "empty", True):
            raise RuntimeError("无法获取 ETF 列表：ak.fund_etf_spot_em 返回空")
        df = df.rename(columns={"代码": "code6", "名称": "name"})
        df["code"] = df["code6"].map(_normalize_etf_code_6_to_xt)
        df = df[df["code"].astype(bool)]
        df = df[["code", "name"]].drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
        return df

    df = pd.DataFrame({"code": pd.unique(pd.Series(codes))})
    if names:
        df["name"] = df["code"].map(names).fillna("")
    else:
        df["name"] = ""
    return df


def select_top_hot_etfs(
    *,
    top_n: int = 10,
    min_amount: float = 50_000_000.0,
    negative_keywords: Optional[list[str]] = None,
    history_days: int = 30,
    chunk_size: int = 1200,
) -> pd.DataFrame:
    if not _xtdata_available():
        raise RuntimeError("xtdata 不可用，无法执行ETF筛选")
    if top_n <= 0:
        return pd.DataFrame(columns=["code", "name", "close", "score", "factor_a", "factor_b", "factor_c", "amount"])

    # 1) 获取全量ETF代码与名称，并执行负面清单过滤（剔除“货币/债/QDII/跨境”等噪音品种）
    universe = _get_all_etf_universe()
    universe = universe.copy()
    universe["name"] = universe["name"].fillna("")

    blacklist = negative_keywords if negative_keywords is not None else NEGATIVE_NAME_KEYWORDS
    if blacklist:
        pattern = "|".join(re.escape(x) for x in blacklist if x)
        if pattern:
            universe = universe[~universe["name"].astype(str).str.contains(pattern, regex=True, na=False)]

    codes = universe["code"].astype(str).tolist()
    if not codes:
        return pd.DataFrame(columns=["code", "name", "close", "score", "factor_a", "factor_b", "factor_c", "amount"])

    # 2) 批量拉取近 N 天日线（close/amount）。为了避免单次请求过大，这里按代码分块处理（不是逐行遍历）
    start_time = (datetime.now().astimezone() - timedelta(days=120)).strftime("%Y%m%d")

    frames: list[pd.DataFrame] = []
    chunks = _iter_chunks(codes, chunk_size)
    total_chunks = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        logger.warning("HotETF: 拉取日线数据 (%s/%s) chunk_size=%s", idx, total_chunks, len(chunk))
        parts = _fetch_daily_chunk_parts(
            chunk,
            start_time=start_time,
            history_days=history_days,
            depth=0,
            max_split_depth=3,
        )
        if parts:
            frames.extend(parts)
        else:
            logger.warning("HotETF: chunk无可用数据，已跳过 (%s/%s) chunk_size=%s", idx, total_chunks, len(chunk))

    if not frames:
        logger.warning("HotETF: 无可用日线数据，返回空结果")
        return pd.DataFrame(columns=["code", "name", "close", "score", "factor_a", "factor_b", "factor_c", "amount"])
    df = pd.concat(frames, ignore_index=True)
    df = df.merge(universe[["code", "name"]], on="code", how="left")
    df = df.sort_values(["code", "time"], kind="mergesort").reset_index(drop=True)

    # 3) 因子所需的滚动指标（向量化：groupby + rolling + transform）
    logger.warning("HotETF: 计算滚动指标与因子 (MA/Amount/Volatility)")
    df["ma5"] = df.groupby("code", sort=False)["close"].transform(lambda s: s.rolling(5, min_periods=5).mean())
    df["ma20"] = df.groupby("code", sort=False)["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["amt_ma5"] = df.groupby("code", sort=False)["amount"].transform(lambda s: s.rolling(5, min_periods=5).mean())
    df["amt_ma20"] = df.groupby("code", sort=False)["amount"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["ret"] = df.groupby("code", sort=False)["close"].pct_change()
    df["vol20"] = df.groupby("code", sort=False)["ret"].transform(lambda s: s.rolling(20, min_periods=20).std())

    # 4) 取最新交易日截面，并按策略执行过滤：流动性、趋势（close > MA5）
    last_time = df.groupby("code", sort=False)["time"].transform("max")
    snap = df[df["time"] == last_time].copy()

    logger.warning("HotETF: 过滤 (amount>=%.0f, close>MA5, 数据充足)", float(min_amount))
    snap = snap[snap["amount"] >= float(min_amount)]
    snap = snap[snap["close"] > snap["ma5"]]

    # 5) 计算三因子，并做健壮性处理（除零/无穷/数据不足的ETF直接剔除）
    snap["factor_a"] = (snap["close"] - snap["ma20"]) / snap["ma20"]
    snap["factor_b"] = snap["amt_ma5"] / snap["amt_ma20"]
    snap["factor_c"] = snap["vol20"]

    snap = snap.replace([math.inf, -math.inf], pd.NA)
    snap = snap.dropna(subset=["factor_a", "factor_b", "factor_c", "close", "amount", "name"])

    # 6) 截面Rank(PCT)并合成Score，输出TopN
    snap["rank_a"] = snap["factor_a"].rank(pct=True)
    snap["rank_b"] = snap["factor_b"].rank(pct=True)
    snap["rank_c"] = snap["factor_c"].rank(pct=True)
    snap["score"] = 0.4 * snap["rank_a"] + 0.4 * snap["rank_b"] + 0.2 * snap["rank_c"]

    out = snap.sort_values("score", ascending=False, kind="mergesort").head(int(top_n)).copy()
    out = out[["code", "name", "close", "score", "factor_a", "factor_b", "factor_c", "amount"]]
    out = out.reset_index(drop=True)
    logger.warning("HotETF: Top%s 选股完成", int(top_n))
    return out
