from __future__ import annotations

import ast
import concurrent.futures
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable, Optional

import requests

from newsget.ingestion import fetch_top10_news
from newsget.models import NewsItem
from newsget.sources.eastmoney_etf import EtfHolding, fetch_etf_top10_holdings

from .deepseek_client import DeepSeekClient
from .etf_pipeline import run_etf_pipeline
from .prompts import PROMPT_ETF_SIGNAL


try:
    from xtquant import xtdata as _xtdata  # type: ignore
except Exception:  # pragma: no cover
    _xtdata = None


WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass(frozen=True)
class KlineSeries:
    times: list[Any]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]
    amount: list[float]


def normalize_code(code: str) -> str:
    s = (code or "").strip().upper()
    if not s:
        return s
    if "." in s:
        return s
    if not s.isdigit():
        return s
    if len(s) != 6:
        return s
    if s.startswith(("5", "6", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chip_batch_csv_candidates() -> list[Path]:
    p = os.environ.get("CHIP_BATCH_CSV", "").strip()
    if p:
        return [Path(p)]
    d = os.environ.get("CHIP_BATCH_DIR", "").strip()
    base = Path(d) if d else (_project_root() / "etf_chip_engine" / "data")
    if not base.exists():
        return []
    files = sorted(base.glob("batch_results_*.csv"), reverse=True)
    return files


def _parse_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def _format_chip_dense_zones(raw: str, *, current_price: Optional[float] = None, max_items: int = 4) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    obj: Any = None
    try:
        obj = json.loads(s)
    except Exception:
        try:
            obj = ast.literal_eval(s)
        except Exception:
            return ""
    if not isinstance(obj, list) or not obj:
        return ""
    parts: list[str] = []
    cp = float(current_price) if current_price is not None and math.isfinite(float(current_price)) and float(current_price) > 0 else None
    if cp is not None:
        parts.append(f"现价:{cp:.3f}")
    for z in obj[: max(int(max_items), 0)]:
        if not isinstance(z, dict):
            continue
        price = _parse_float(z.get("price"))
        density = _parse_float(z.get("density"))
        ztype = str(z.get("type") or "").strip().lower()
        if not price:
            continue
        label = "支撑" if ztype == "support" else ("阻力" if ztype == "resistance" else (ztype or "区域"))
        dist = ""
        if cp is not None and price:
            pct = (price / cp - 1.0) * 100.0
            dist = f"，距现价{pct:+.2f}%"
        if density is not None:
            parts.append(f"{label}:{price:.3f}(密度{density:.4g}{dist})")
        else:
            parts.append(f"{label}:{price:.3f}({dist.lstrip('，')})" if dist else f"{label}:{price:.3f}")
    return "；".join(parts).strip()


def load_chip_factors(etf_code_norm: str, *, current_price: Optional[float] = None) -> dict[str, str]:
    strict = os.environ.get("CHIP_FACTOR_STRICT", "").strip() == "1"
    micro_strict = os.environ.get("MICRO_STRICT", "").strip() == "1"
    candidates = _chip_batch_csv_candidates()
    if not candidates:
        msg = f"ChipFactor: 未找到 batch_results_*.csv（可用 CHIP_BATCH_DIR/CHIP_BATCH_CSV 指定路径），etf={etf_code_norm}"
        logging.getLogger(__name__).warning(msg)
        if strict or micro_strict:
            raise RuntimeError(msg)
        return {
            "chip_trade_date": "",
            "chip_profit_ratio": "数据缺失",
            "chip_dense_zones": "数据缺失",
            "chip_asr": "数据缺失",
            "ms_vpin_rank": "数据缺失",
            "ms_vpin_max_rank": "数据缺失",
            "ms_ofi_daily_z": "数据缺失",
            "ms_kyle_lambda_z": "数据缺失",
            "ms_vs_max_logz": "数据缺失",
        }

    target = normalize_code(etf_code_norm)
    found: Optional[dict[str, str]] = None
    used_path: Optional[Path] = None
    for csv_path in candidates:
        used_path = csv_path
        if not csv_path.exists():
            continue
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = normalize_code(str(row.get("code") or ""))
                    if code != target:
                        continue
                    found = {k: str(v or "").strip() for k, v in row.items() if k}
                    break
        except Exception as e:
            msg = f"ChipFactor: 读取失败: {csv_path} etf={target} err={repr(e)}"
            logging.getLogger(__name__).warning(msg)
            if strict or micro_strict:
                raise RuntimeError(msg) from e
            found = None
        if found:
            break

    if not found:
        msg = f"ChipFactor: CSV 未找到该 ETF（已尝试最近{len(candidates)}个文件）etf={target} last_file={used_path}"
        logging.getLogger(__name__).warning(msg)
        if strict or micro_strict:
            raise RuntimeError(msg)
        return {
            "chip_trade_date": "",
            "chip_profit_ratio": "数据缺失",
            "chip_dense_zones": "数据缺失",
            "chip_asr": "数据缺失",
            "ms_vpin_rank": "数据缺失",
            "ms_vpin_max_rank": "数据缺失",
            "ms_ofi_daily_z": "数据缺失",
            "ms_kyle_lambda_z": "数据缺失",
            "ms_vs_max_logz": "数据缺失",
        }

    trade_date = str(found.get("trade_date") or "").strip()
    pr = _parse_float(found.get("profit_ratio"))
    asr = _parse_float(found.get("asr"))
    dz = _format_chip_dense_zones(str(found.get("dense_zones") or ""), current_price=current_price)
    ms_vpin_rank = _parse_float(found.get("ms_vpin_rank"))
    ms_vpin_max_rank = _parse_float(found.get("ms_vpin_max_rank"))
    ms_ofi_daily_z = _parse_float(found.get("ms_ofi_daily_z"))
    ms_kyle_lambda_z = _parse_float(found.get("ms_kyle_lambda_z"))
    ms_vs_max_logz = _parse_float(found.get("ms_vs_max_logz"))

    if pr is None or asr is None or not dz:
        logging.getLogger(__name__).warning(
            "ChipFactor: 字段缺失/不可解析 etf=%s file=%s trade_date=%s profit_ratio=%s asr=%s dense_zones_ok=%s",
            target,
            str(csv_path),
            trade_date,
            str(found.get("profit_ratio")),
            str(found.get("asr")),
            bool(dz),
        )
        if strict:
            raise RuntimeError(f"ChipFactor: 字段缺失/不可解析 etf={target} file={csv_path}")

    if any(v is None for v in (ms_vpin_rank, ms_vpin_max_rank, ms_ofi_daily_z, ms_kyle_lambda_z, ms_vs_max_logz)):
        logging.getLogger(__name__).warning(
            "MicroFactor: 字段缺失/不可解析 etf=%s file=%s trade_date=%s ms_vpin_rank=%s ms_ofi_daily_z=%s",
            target,
            str(csv_path),
            trade_date,
            str(found.get("ms_vpin_rank")),
            str(found.get("ms_ofi_daily_z")),
        )
        if micro_strict:
            raise RuntimeError(f"MicroFactor: 字段缺失/不可解析 etf={target} file={csv_path}")

    return {
        "chip_trade_date": trade_date,
        "chip_profit_ratio": (f"{pr:.2f}" if pr is not None else "数据缺失"),
        "chip_dense_zones": (dz if dz else "数据缺失"),
        "chip_asr": (f"{asr:.4f}" if asr is not None else "数据缺失"),
        "ms_vpin_rank": (f"{ms_vpin_rank:.3f}" if ms_vpin_rank is not None else "数据缺失"),
        "ms_vpin_max_rank": (f"{ms_vpin_max_rank:.3f}" if ms_vpin_max_rank is not None else "数据缺失"),
        "ms_ofi_daily_z": (f"{ms_ofi_daily_z:.3f}" if ms_ofi_daily_z is not None else "数据缺失"),
        "ms_kyle_lambda_z": (f"{ms_kyle_lambda_z:.3f}" if ms_kyle_lambda_z is not None else "数据缺失"),
        "ms_vs_max_logz": (f"{ms_vs_max_logz:.3f}" if ms_vs_max_logz is not None else "数据缺失"),
    }


def _fmt_pct(x: Optional[float], *, digits: int = 2) -> str:
    if x is None or not math.isfinite(x):
        return ""
    return f"{x:.{digits}f}"


def _fmt_num(x: Optional[float], *, digits: int = 2) -> str:
    if x is None or not math.isfinite(x):
        return ""
    return f"{x:.{digits}f}"


def _safe_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    return v


def _pct_change(a: float, b: float) -> Optional[float]:
    if not math.isfinite(a) or not math.isfinite(b) or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def _rolling_mean(xs: list[float], window: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(xs)
    if window <= 0:
        return out
    s = 0.0
    bad = 0
    q: list[float] = []
    for i, v in enumerate(xs):
        q.append(v)
        if math.isfinite(v):
            s += v
        else:
            bad += 1
        if len(q) > window:
            old = q.pop(0)
            if math.isfinite(old):
                s -= old
            else:
                bad -= 1
        if len(q) == window and bad == 0:
            out[i] = s / window
    return out


def _ema(xs: list[float], span: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(xs)
    if span <= 0 or not xs:
        return out
    alpha = 2.0 / (span + 1.0)
    ema_val: Optional[float] = None
    for i, v in enumerate(xs):
        if not math.isfinite(v):
            out[i] = None
            continue
        if ema_val is None:
            ema_val = v
        else:
            ema_val = alpha * v + (1.0 - alpha) * ema_val
        out[i] = ema_val
    return out


def _rsi(xs: list[float], period: int) -> Optional[float]:
    if period <= 0 or len(xs) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        d = xs[i] - xs[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _macd_hist(xs: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[Optional[float]]:
    if not xs:
        return []
    ema_fast = _ema(xs, fast)
    ema_slow = _ema(xs, slow)
    macd_line: list[Optional[float]] = [None] * len(xs)
    for i in range(len(xs)):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd_line[i] = None
        else:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    macd_vals = [v if v is not None else float("nan") for v in macd_line]
    signal_line = _ema(macd_vals, signal)
    hist: list[Optional[float]] = [None] * len(xs)
    for i in range(len(xs)):
        if macd_line[i] is None or signal_line[i] is None:
            hist[i] = None
        else:
            hist[i] = macd_line[i] - signal_line[i]
    return hist


def _tr_series(high: list[float], low: list[float], close: list[float]) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(close)
    for i in range(len(close)):
        if i == 0:
            out[i] = None
            continue
        h = high[i]
        l = low[i]
        pc = close[i - 1]
        if not (math.isfinite(h) and math.isfinite(l) and math.isfinite(pc)):
            out[i] = None
            continue
        out[i] = max(h - l, abs(h - pc), abs(l - pc))
    return out


def _percentile_rank(xs: list[float], x: float) -> Optional[float]:
    vals = [v for v in xs if math.isfinite(v)]
    if not vals:
        return None
    le = sum(1 for v in vals if v <= x)
    return le / len(vals) * 100.0


def _consecutive_trend(xs: list[float]) -> tuple[str, int]:
    if len(xs) < 2:
        return "震荡", 0
    days = 0
    trend: Optional[str] = None
    for i in range(len(xs) - 1, 0, -1):
        d = xs[i] - xs[i - 1]
        if d > 0:
            if trend in (None, "放大"):
                trend = "放大"
                days += 1
            else:
                break
        elif d < 0:
            if trend in (None, "缩小"):
                trend = "缩小"
                days += 1
            else:
                break
        else:
            break
    return trend or "震荡", days


def _window_mean(xs: list[Optional[float]], *, end_idx: int, window: int) -> Optional[float]:
    if window <= 0 or end_idx < 0:
        return None
    start = max(0, end_idx - window + 1)
    vals = [v for v in xs[start : end_idx + 1] if v is not None and math.isfinite(v)]
    if len(vals) < window:
        return None
    return sum(vals) / window


def _build_cn_date(dt: datetime) -> str:
    wd = WEEKDAY_CN[dt.weekday()]
    return dt.strftime(f"%Y年%m月%d日（{wd}）")


def _pick_latest_kline_series(raw: dict[str, Any], code: str) -> Optional[KlineSeries]:
    def get_field(name: str) -> list[Optional[float]]:
        df = raw.get(name)
        if df is None:
            return []
        try:
            row = df.loc[code]
        except Exception:
            try:
                row = df.loc[str(code)]
            except Exception:
                return []
        try:
            items = list(row.items())
        except Exception:
            return []
        items.sort(key=lambda x: x[0])
        return [_safe_float(v) for _, v in items]

    times_df = raw.get("time")
    times: list[Any] = []
    if times_df is not None:
        try:
            row = times_df.loc[code]
            items = list(row.items())
            items.sort(key=lambda x: x[0])
            times = [t for t, _ in items]
        except Exception:
            times = []

    opens = [v if v is not None else float("nan") for v in get_field("open")]
    highs = [v if v is not None else float("nan") for v in get_field("high")]
    lows = [v if v is not None else float("nan") for v in get_field("low")]
    closes = [v if v is not None else float("nan") for v in get_field("close")]
    vols = [v if v is not None else float("nan") for v in get_field("volume")]
    amts = [v if v is not None else float("nan") for v in get_field("amount")]
    n = min(len(opens), len(highs), len(lows), len(closes), len(vols), len(amts))
    if n < 2:
        return None
    if times and len(times) >= n:
        times = times[-n:]
    else:
        times = list(range(n))
    return KlineSeries(
        times=times[-n:],
        open=opens[-n:],
        high=highs[-n:],
        low=lows[-n:],
        close=closes[-n:],
        volume=vols[-n:],
        amount=amts[-n:],
    )


def _xtdata_available() -> bool:
    return _xtdata is not None


def _akshare_available() -> bool:
    try:
        import akshare  # type: ignore
        return True
    except Exception:
        return False


def _env_float(name: str, default: float) -> float:
    try:
        s = os.environ.get(name, "").strip()
        return float(s) if s else float(default)
    except Exception:
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            logging.getLogger(__name__).warning("Signal: %s 超时(>%ss)，已跳过", label, int(timeout_seconds))
            return default
        except Exception as e:
            if propagate and isinstance(e, propagate):
                raise
            logging.getLogger(__name__).warning("Signal: %s 失败: %s", label, repr(e))
            return default


def _xt_download_daily(stock_list: list[str], *, start_time: str) -> None:
    if not _xtdata_available():
        return
    if os.environ.get("FININTEL_SKIP_XTDATA_DOWNLOAD", "").strip() == "1":
        return
    timeout_seconds = _env_float("FININTEL_XTDATA_TIMEOUT_SECONDS", 35.0)
    try:
        _run_with_timeout(
            lambda: _xtdata.download_history_data2(stock_list, "1d", start_time, "", None, None),
            timeout_seconds=timeout_seconds,
            label="xtdata.download_history_data2",
            default=None,
            propagate=(TypeError,),
        )
        return
    except TypeError:
        pass
    try:
        _run_with_timeout(
            lambda: _xtdata.download_history_data2(stock_list, "1d", start_time, "", None),
            timeout_seconds=timeout_seconds,
            label="xtdata.download_history_data2",
            default=None,
            propagate=(TypeError,),
        )
        return
    except TypeError:
        pass
    _run_with_timeout(
        lambda: _xtdata.download_history_data2(stock_list, "1d", start_time, ""),
        timeout_seconds=timeout_seconds,
        label="xtdata.download_history_data2",
        default=None,
    )


def _xt_get_daily(stock_list: list[str], *, count: int) -> dict[str, Any]:
    if not _xtdata_available():
        return {}
    if os.environ.get("FININTEL_SKIP_XTDATA_MARKET", "").strip() == "1":
        return {}
    timeout_seconds = _env_float("FININTEL_XTDATA_TIMEOUT_SECONDS", 35.0)
    return _run_with_timeout(
        lambda: _xtdata.get_market_data(
            field_list=["time", "open", "high", "low", "close", "volume", "amount"],
            stock_list=stock_list,
            period="1d",
            start_time="",
            end_time="",
            count=count,
            dividend_type="none",
            fill_data=True,
        ),
        timeout_seconds=timeout_seconds,
        label="xtdata.get_market_data(1d)",
        default={},
    )


def _xt_get_instrument_name(code: str) -> str:
    if not _xtdata_available():
        return ""
    info = _xtdata.get_instrument_detail(code, False)
    if isinstance(info, dict):
        name = info.get("InstrumentName")
        return str(name).strip() if name else ""
    return ""


def _xt_get_last_close(raw: dict[str, Any], code: str) -> Optional[float]:
    close_df = raw.get("close")
    if close_df is None:
        return None
    row = None
    try:
        row = close_df.loc[code]
    except Exception:
        try:
            row = close_df.loc[str(code)]
        except Exception:
            row = None
    if row is None:
        return None
    try:
        items = list(row.items())
    except Exception:
        return None
    if not items:
        return None
    items.sort(key=lambda x: x[0])
    return _safe_float(items[-1][1])


def _xt_etf_basket_top5_holdings(etf_code_norm: str, *, preselect_top_n: int = 80) -> list[EtfHolding]:
    if not _xtdata_available():
        return []
    try:
        info = _xtdata.get_etf_info(etf_code_norm)
    except Exception:
        return []
    if not isinstance(info, dict):
        return []
    stocks = info.get("stocks")
    if not isinstance(stocks, dict) or not stocks:
        return []

    comps: list[tuple[str, str, float]] = []
    for k, v in stocks.items():
        code = normalize_code(str(k))
        if not code:
            continue
        meta = v if isinstance(v, dict) else {}
        vol = _safe_float(meta.get("componentVolume"))
        if vol is None or vol <= 0:
            continue
        name = str(meta.get("componentName") or "").strip()
        comps.append((code, name, float(vol)))
    if not comps:
        return []

    comps.sort(key=lambda x: x[2], reverse=True)
    pre = comps[: max(int(preselect_top_n), 0)]
    codes = [c for c, _, _ in pre]
    if not codes:
        return []

    start_time = (datetime.now().astimezone() - timedelta(days=30)).strftime("%Y%m%d")
    _xt_download_daily(codes, start_time=start_time)
    raw = _xt_get_daily(codes, count=5)

    scored: list[tuple[float, str, str]] = []
    for code, name, vol in pre:
        close = _xt_get_last_close(raw, code)
        if close is None or close <= 0:
            continue
        scored.append((vol * close, code, name))
    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[EtfHolding] = []
    for _, code, name in scored[:5]:
        nm = name or _xt_get_instrument_name(code) or code
        out.append(EtfHolding(stock_code=code, stock_name=nm))
    return out


def _xt_download_history_data2_compat(stock_list: list[str], period: str, *, start_time: str) -> None:
    if not _xtdata_available():
        return
    if os.environ.get("FININTEL_SKIP_XTDATA_DOWNLOAD", "").strip() == "1":
        return
    timeout_seconds = _env_float("FININTEL_XTDATA_TIMEOUT_SECONDS", 35.0)
    try:
        _run_with_timeout(
            lambda: _xtdata.download_history_data2(stock_list, period, start_time, "", None, None),
            timeout_seconds=timeout_seconds,
            label="xtdata.download_history_data2",
            default=None,
            propagate=(TypeError,),
        )
        return
    except TypeError:
        pass
    try:
        _run_with_timeout(
            lambda: _xtdata.download_history_data2(stock_list, period, start_time, "", None),
            timeout_seconds=timeout_seconds,
            label="xtdata.download_history_data2",
            default=None,
            propagate=(TypeError,),
        )
        return
    except TypeError:
        pass
    _run_with_timeout(
        lambda: _xtdata.download_history_data2(stock_list, period, start_time, ""),
        timeout_seconds=timeout_seconds,
        label="xtdata.download_history_data2",
        default=None,
    )


def _chunked(xs: list[str], size: int) -> Iterable[list[str]]:
    if size <= 0:
        yield xs
        return
    for i in range(0, len(xs), size):
        yield xs[i : i + size]


def _today_yyyymmdd() -> str:
    fake = os.environ.get("FININTEL_FAKE_TODAY", "").strip()
    if re.fullmatch(r"\d{8}", fake):
        return fake
    return datetime.now().astimezone().strftime("%Y%m%d")


def compute_up_down_ratio_all() -> str:
    if not _xtdata_available():
        return ""
    if os.environ.get("FININTEL_SKIP_BREADTH", "").strip() == "1":
        return "已跳过"
    day = _today_yyyymmdd()
    state_dir = Path("output") / "state"
    cache_path = state_dir / f"up_down_ratio_{day}.txt"
    if cache_path.exists():
        try:
            s = cache_path.read_text(encoding="utf-8").strip()
            return s
        except Exception:
            pass
    try:
        sectors = _xtdata.get_sector_list()
    except Exception:
        return ""
    if not isinstance(sectors, list):
        return ""

    targets = [s for s in sectors if isinstance(s, str) and ("沪深A股" in s or "创业板" in s)]
    if not targets:
        return ""

    codes: set[str] = set()
    for sec in targets:
        try:
            lst = _xtdata.get_stock_list_in_sector(sec)
        except Exception:
            continue
        if not isinstance(lst, list):
            continue
        for c in lst:
            if not isinstance(c, str):
                continue
            cc = normalize_code(c)
            if re.fullmatch(r"\d{6}\.(SZ|SH)", cc):
                codes.add(cc)

    all_codes = sorted(codes)
    if not all_codes:
        return ""

    up = down = flat = 0
    chunks = list(_chunked(all_codes, 1800))

    timeout_seconds = _env_float("FININTEL_XTDATA_TIMEOUT_SECONDS", 35.0)
    for i, chunk in enumerate(chunks, start=1):
        if i == 1 or i % 4 == 0 or i == len(chunks):
            logging.getLogger(__name__).warning("Signal: 市场宽度进度 %s/%s", i, len(chunks))
        _xt_download_history_data2_compat(chunk, "1d", start_time="")
        raw = _run_with_timeout(
            lambda: _xtdata.get_market_data(
                field_list=["close", "preClose"],
                stock_list=chunk,
                period="1d",
                start_time="",
                end_time="",
                count=1,
                dividend_type="none",
                fill_data=True,
            ),
            timeout_seconds=timeout_seconds,
            label="xtdata.get_market_data(breadth)",
            default={},
        )
        if not isinstance(raw, dict):
            continue
        close_df = raw.get("close")
        pre_df = raw.get("preClose")
        if close_df is None or pre_df is None:
            continue
        try:
            last_col = close_df.columns[-1]
            close_s = close_df[last_col]
            pre_s = pre_df[last_col]
        except Exception:
            continue

        try:
            diffs = close_s - pre_s
            up += int((diffs > 0).sum())
            down += int((diffs < 0).sum())
            flat += int((diffs == 0).sum())
        except Exception:
            continue

    if up == 0 and down == 0 and flat == 0:
        return ""
    ratio = f"{up}:{down}({flat})"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(ratio, encoding="utf-8")
    except Exception:
        pass
    return ratio


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def snapshot_all_etf_shares() -> tuple[dict[str, float], Optional[Path]]:
    try:
        import akshare as ak  # type: ignore
    except Exception:
        return {}, None
    state_dir = Path("output") / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    day = _today_yyyymmdd()
    dated_path = state_dir / f"etf_share_snapshot_{day}.json"
    latest_path = state_dir / "etf_share_snapshot.json"
    if dated_path.exists():
        cur = _load_json(dated_path)
        shares = cur.get("shares") if isinstance(cur, dict) else None
        if isinstance(shares, dict):
            return {str(k): float(v) for k, v in shares.items() if _safe_float(v) is not None}, dated_path

    shares: dict[str, float] = {}
    sz_df = None
    sh_df = None
    try:
        sz_df = ak.fund_etf_scale_szse()
    except Exception:
        sz_df = None
    for back_days in range(0, 8):
        dt = datetime.strptime(day, "%Y%m%d") - timedelta(days=back_days)
        d0 = dt.strftime("%Y%m%d")
        try:
            sh_df = ak.fund_etf_scale_sse(date=d0)
            if sh_df is not None and not getattr(sh_df, "empty", True):
                break
        except Exception:
            sh_df = None
    if sh_df is not None and not getattr(sh_df, "empty", True):
        try:
            for _, r in sh_df.iterrows():
                code6 = str(r.get("基金代码") or "").strip()
                val = _safe_float(r.get("基金份额"))
                if re.fullmatch(r"\d{6}", code6) and val is not None:
                    shares[f"{code6}.SH"] = float(val)
        except Exception:
            pass
    if sz_df is not None and not getattr(sz_df, "empty", True):
        try:
            for _, r in sz_df.iterrows():
                code6 = str(r.get("基金代码") or "").strip()
                val = _safe_float(r.get("基金份额"))
                if re.fullmatch(r"\d{6}", code6) and val is not None:
                    shares[f"{code6}.SZ"] = float(val)
        except Exception:
            pass

    if not shares:
        return {}, None

    payload = {"date": day, "time": datetime.now().astimezone().isoformat(timespec="seconds"), "shares": shares}
    dated_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return shares, dated_path


def compute_share_change_from_snapshot(etf_code_norm: str) -> tuple[str, str]:
    state_dir = Path("output") / "state"
    day = _today_yyyymmdd()
    today_path = state_dir / f"etf_share_snapshot_{day}.json"
    cur = _load_json(today_path) if today_path.exists() else {}
    cur_shares = cur.get("shares") if isinstance(cur, dict) else None
    cur_val = _safe_float(cur_shares.get(etf_code_norm)) if isinstance(cur_shares, dict) else None
    if cur_val is None:
        cur_shares2, _ = snapshot_all_etf_shares()
        cur_val = _safe_float(cur_shares2.get(etf_code_norm))

    prev_val = None
    if state_dir.exists():
        best_date = ""
        best_path: Optional[Path] = None
        for p in state_dir.glob("etf_share_snapshot_*.json"):
            m = re.search(r"_(\d{8})\.json$", p.name)
            if not m:
                continue
            d = m.group(1)
            if d >= day:
                continue
            if d > best_date:
                best_date = d
                best_path = p
        if best_path:
            prev = _load_json(best_path)
            prev_shares = prev.get("shares") if isinstance(prev, dict) else None
            if isinstance(prev_shares, dict):
                prev_val = _safe_float(prev_shares.get(etf_code_norm))
    if prev_val is None:
        latest_path = state_dir / "etf_share_snapshot.json"
        prev = _load_json(latest_path) if latest_path.exists() else {}
        prev_shares = prev.get("shares") if isinstance(prev, dict) else None
        if isinstance(prev_shares, dict):
            prev_val = _safe_float(prev_shares.get(etf_code_norm))

    if cur_val is None or prev_val is None:
        return "", ""

    delta = cur_val - prev_val
    if abs(delta) < 1e-9:
        return "不变", "0"
    direction = "增加" if delta > 0 else "减少"
    return direction, _fmt_num(abs(delta) / 10000.0)


def _eastmoney_market_from_code(code_norm: str) -> Optional[str]:
    if code_norm.endswith(".SH"):
        return "sh"
    if code_norm.endswith(".SZ"):
        return "sz"
    if code_norm.endswith(".BJ"):
        return "bj"
    return None


def _eastmoney_stock_inflow_big_super_yuan(code_norm: str) -> Optional[float]:
    try:
        import akshare as ak  # type: ignore
    except Exception:
        return None
    mkt = _eastmoney_market_from_code(code_norm)
    if not mkt:
        return None
    stock = code_norm.split(".")[0]
    try:
        df = ak.stock_individual_fund_flow(stock=stock, market=mkt)
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None
    try:
        row = df.iloc[-1]
        big = _safe_float(row.get("大单净流入-净额"))
        sup = _safe_float(row.get("超大单净流入-净额"))
        if big is None or sup is None:
            return None
        return big + sup
    except Exception:
        return None


def _eastmoney_etf_inflow_big_super_yuan(etf_code_6: str) -> Optional[float]:
    try:
        import akshare as ak  # type: ignore
    except Exception:
        return None
    try:
        df = ak.fund_etf_spot_em()
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None
    try:
        sub = df[df["代码"] == etf_code_6]
        if getattr(sub, "empty", True):
            return None
        row = sub.iloc[0]
        big = _safe_float(row.get("大单净流入-净额"))
        sup = _safe_float(row.get("超大单净流入-净额"))
        if big is None or sup is None:
            main = _safe_float(row.get("主力净流入-净额"))
            return main
        return big + sup
    except Exception:
        return None


def compute_net_inflow_wanyuan(
    *,
    top5_holdings: list[EtfHolding],
    etf_code_norm: str,
) -> str:
    total_yuan = 0.0
    ok = 0
    for h in top5_holdings:
        code_norm = normalize_code(h.stock_code)
        v = _eastmoney_stock_inflow_big_super_yuan(code_norm)
        if v is None:
            continue
        total_yuan += v
        ok += 1
    if ok > 0:
        return _fmt_num(total_yuan / 10000.0)

    etf_code_6 = etf_code_norm.split(".")[0]
    v2 = _eastmoney_etf_inflow_big_super_yuan(etf_code_6)
    if v2 is None:
        return ""
    return _fmt_num(v2 / 10000.0)


def build_hot_search_text(items: list[NewsItem], *, max_items: int = 3) -> str:
    out: list[str] = []
    for i, it in enumerate(items[:max_items], start=1):
        ts = it.publish_time or it.crawl_time or ""
        body = (it.content or "").strip().replace("\n", " ")
        summary = body[:120].strip()
        if summary:
            summary = f"摘要：{summary}"
        out.append(f"{i}.[{ts}] [{it.title}]:[{summary}]")
    return "\n".join(out).strip()


def build_etf_news_text(
    holdings_news: dict[str, Any],
    *,
    max_items: int = 3,
) -> str:
    raw_news = holdings_news.get("raw_news") or []
    summaries = holdings_news.get("summaries") or []
    top3 = holdings_news.get("top_3") or []
    if not isinstance(raw_news, list) or not isinstance(summaries, list) or not isinstance(top3, list):
        return ""

    summary_to_idx: dict[str, int] = {}
    for idx, s in enumerate(summaries):
        if isinstance(s, str) and s not in summary_to_idx:
            summary_to_idx[s] = idx

    out: list[str] = []
    for i, entry in enumerate(top3[:max_items], start=1):
        if not isinstance(entry, dict):
            continue
        s = entry.get("summary")
        if not isinstance(s, str):
            continue
        idx = summary_to_idx.get(s, -1)
        title = ""
        ts = ""
        if 0 <= idx < len(raw_news) and isinstance(raw_news[idx], dict):
            title = str(raw_news[idx].get("title") or "").strip()
            ts = str(raw_news[idx].get("publish_time") or raw_news[idx].get("crawl_time") or "").strip()
        title = title or "ETF相关"
        out.append(f"{i}.[{ts}] [{title}]:[摘要：{s}]")
    return "\n".join(out).strip()


def compute_etf_features(
    etf: KlineSeries,
    *,
    etf_code: str,
    market_sh: Optional[KlineSeries],
    market_cy: Optional[KlineSeries],
    top5_stocks: list[tuple[EtfHolding, Optional[KlineSeries]]],
    instrument_name: str,
) -> dict[str, str]:
    n = len(etf.close)
    t = n - 1

    prev_close = etf.close[t - 1]
    close_t = etf.close[t]
    open_t = etf.open[t]
    high_t = etf.high[t]
    low_t = etf.low[t]

    change_3d = _pct_change(close_t, etf.close[max(0, t - 3)])
    change_5d = _pct_change(close_t, etf.close[max(0, t - 5)])
    open_pct = _pct_change(open_t, prev_close)
    close_pct = _pct_change(close_t, prev_close)
    high_pct = _pct_change(high_t, prev_close)
    low_pct = _pct_change(low_t, prev_close)

    ma5 = _rolling_mean(etf.close, 5)
    ma10 = _rolling_mean(etf.close, 10)
    ma5_t = ma5[t]
    ma10_t = ma10[t]
    ma5_pos = _pct_change(close_t, ma5_t) if ma5_t is not None else None
    ma10_pos = _pct_change(close_t, ma10_t) if ma10_t is not None else None

    highs_5 = [v for v in etf.high[max(0, t - 4) : t + 1] if math.isfinite(v)]
    is_5d_highest = "是" if highs_5 and high_t >= max(highs_5) else "不是"

    vol_prev5 = [v for v in etf.volume[max(0, t - 5) : t] if math.isfinite(v)]
    vol_ratio = (etf.volume[t] / (sum(vol_prev5) / len(vol_prev5))) if vol_prev5 and etf.volume[t] > 0 else None

    turnover_rank = None
    try:
        float_vol = None
        if _xtdata_available():
            info = _xtdata.get_instrument_detail(etf_code, False)
            if isinstance(info, dict):
                float_vol = _safe_float(info.get("FloatVolume"))
        if float_vol and float_vol > 0:
            turns = []
            for v in etf.volume[max(0, t - 19) : t + 1]:
                if math.isfinite(v):
                    turns.append(v / float_vol * 100.0)
            if turns:
                turnover_rank = _percentile_rank(turns, turns[-1])
    except Exception:
        turnover_rank = None

    rsi_5 = _rsi([v for v in etf.close if math.isfinite(v)], 5)
    hist = _macd_hist([v for v in etf.close if math.isfinite(v)])
    hist_vals = [v for v in hist if v is not None and math.isfinite(v)]
    macd_trend, macd_days = _consecutive_trend(hist_vals)

    tr = _tr_series(etf.high, etf.low, etf.close)
    atr_status = ""
    volatility_ratio = None
    try:
        atr5_today = _window_mean(tr, end_idx=t, window=5)
        atr5_yday = _window_mean(tr, end_idx=t - 1, window=5) if t - 1 >= 0 else None
        if atr5_today is not None and atr5_yday is not None:
            if atr5_today > atr5_yday:
                atr_status = "上升"
            elif atr5_today < atr5_yday:
                atr_status = "下降"
            else:
                atr_status = "持平"
        tr_today = tr[t]
        atr10_y = _window_mean(tr, end_idx=t - 1, window=10) if t - 1 >= 0 else None
        if tr_today is not None and atr10_y is not None and atr10_y > 0:
            volatility_ratio = tr_today / atr10_y
    except Exception:
        atr_status = ""
        volatility_ratio = None

    bias_10 = ((close_t - ma10_t) / ma10_t * 100.0) if ma10_t and ma10_t != 0 else None
    close_60 = [v for v in etf.close[max(0, t - 59) : t + 1] if math.isfinite(v)]
    price_rank_60d = _percentile_rank(close_60, close_t) if close_60 else None

    top5_parts: list[str] = []
    for h, kl in top5_stocks:
        if kl is None or len(kl.close) < 2:
            top5_parts.append(f"{h.stock_name}({h.stock_code}) ")
            continue
        pct = _pct_change(kl.close[-1], kl.close[-2])
        sign = "+" if pct is not None and pct >= 0 else ""
        top5_parts.append(f"{h.stock_name}({h.stock_code}) {sign}{_fmt_pct(pct)}%")
    top5_stocks_perf = "；".join(top5_parts).strip()
    if not top5_stocks_perf and not top5_stocks:
        top5_stocks_perf = "数据缺失（未拉取权重股持仓）"

    sh_change = None
    if market_sh and len(market_sh.close) >= 2:
        sh_change = _pct_change(market_sh.close[-1], market_sh.close[-2])
    cy_change = None
    if market_cy and len(market_cy.close) >= 2:
        cy_change = _pct_change(market_cy.close[-1], market_cy.close[-2])

    market_vol_status = ""
    market_vol_diff = None
    try:
        sh_amt = market_sh.amount[-1] if market_sh else float("nan")
        cy_amt = market_cy.amount[-1] if market_cy else float("nan")
        sh_hist = market_sh.amount[-10:] if market_sh and len(market_sh.amount) >= 10 else []
        cy_hist = market_cy.amount[-10:] if market_cy and len(market_cy.amount) >= 10 else []
        if sh_hist and cy_hist and math.isfinite(sh_amt) and math.isfinite(cy_amt):
            today_amt = sh_amt + cy_amt
            avg_amt = (sum(sh_hist) + sum(cy_hist)) / (len(sh_hist) + len(cy_hist))
            if avg_amt and avg_amt > 0:
                market_vol_diff = (today_amt / avg_amt - 1.0) * 100.0
                market_vol_status = "放量" if market_vol_diff > 0 else "缩量"
    except Exception:
        market_vol_status = ""
        market_vol_diff = None

    new_high_days = 0
    closes = [v for v in etf.close if math.isfinite(v)]
    for i in range(max(0, len(closes) - 10), len(closes)):
        start = max(0, i - 19)
        win = closes[start : i + 1]
        if win and closes[i] >= max(win):
            new_high_days += 1

    rs_rating = None
    if close_pct is not None and sh_change is not None:
        rs_rating = close_pct - sh_change

    return {
        "current_date": _build_cn_date(datetime.now().astimezone()),
        "etf_name": instrument_name or "",
        "change_3d": _fmt_pct(change_3d),
        "change_5d": _fmt_pct(change_5d),
        "open_pct": _fmt_pct(open_pct),
        "close_pct": _fmt_pct(close_pct),
        "high_pct": _fmt_pct(high_pct),
        "low_pct": _fmt_pct(low_pct),
        "ma5_pos": _fmt_pct(ma5_pos),
        "ma10_pos": _fmt_pct(ma10_pos),
        "is_5d_highest": is_5d_highest,
        "vol_ratio": _fmt_num(vol_ratio),
        "turnover_rank": _fmt_pct(turnover_rank),
        "rsi_5": _fmt_num(rsi_5),
        "macd_trend": macd_trend,
        "macd_days": str(macd_days),
        "atr_status": atr_status,
        "volatility_ratio": _fmt_num(volatility_ratio),
        "net_inflow": "",
        "share_change_text": "",
        "bias_10": _fmt_pct(bias_10),
        "price_rank_60d": _fmt_pct(price_rank_60d),
        "top5_stocks_perf": top5_stocks_perf,
        "sh_change": _fmt_pct(sh_change),
        "cy_change": _fmt_pct(cy_change),
        "market_vol_status": market_vol_status,
        "market_vol_diff": _fmt_pct(market_vol_diff),
        "up_down_ratio": "",
        "new_high_days": str(new_high_days),
        "rs_rating": _fmt_pct(rs_rating),
        "hot_search_text": "",
        "news_text": "",
        "yesterday_evaluation": "无",
    }


def run_etf_signal_pipeline(
    session: requests.Session,
    deepseek: DeepSeekClient,
    *,
    etf_code: str,
    debug: bool = False,
    max_workers: Optional[int] = None,
    etf_source: str = "auto",
    yesterday_evaluation: str = "无",
    fetch_news: bool = True,
    fetch_holdings: bool = True,
    fetch_fundflow: bool = True,
    fetch_share_snapshot: bool = True,
    timing: bool = False,
) -> dict[str, Any]:
    etf_code_norm = normalize_code(etf_code)
    t0 = time.perf_counter()
    instrument_name = _xt_get_instrument_name(etf_code_norm) if _xtdata_available() else ""
    etf_source_effective = etf_source
    if etf_source_effective == "auto" and _akshare_available():
        etf_source_effective = "akshare"

    try:
        import logging

        logging.getLogger(__name__).warning("Signal: 开始分析 %s", etf_code_norm)
    except Exception:
        pass

    if fetch_news:
        t_news0 = time.perf_counter()
        day = _today_yyyymmdd()
        cache_dir = Path("output") / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        hot_cache = cache_dir / f"hot_search_{day}.json"
        if hot_cache.exists():
            try:
                hot_items_raw = json.loads(hot_cache.read_text(encoding="utf-8"))
                hot_items = []
                for x in hot_items_raw:
                    if not isinstance(x, dict):
                        continue
                    hot_items.append(
                        NewsItem(
                            source=str(x.get("source") or ""),
                            rank=int(x.get("rank") or 0),
                            title=str(x.get("title") or ""),
                            url=str(x.get("url") or ""),
                            hot=(str(x.get("hot")) if x.get("hot") is not None else None),
                            publish_time=(str(x.get("publish_time")) if x.get("publish_time") is not None else None),
                            content=(str(x.get("content")) if x.get("content") is not None else None),
                            crawl_time=str(x.get("crawl_time") or ""),
                        )
                    )
            except Exception:
                hot_items = []
        else:
            try:
                hot_items = fetch_top10_news(session, include_content=True, debug=debug)
            except Exception:
                hot_items = []
            try:
                hot_cache.write_text(json.dumps([it.to_dict() for it in hot_items[:10]], ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        hot_search_text = build_hot_search_text(hot_items, max_items=3)

        etf6 = etf_code_norm.split(".")[0]
        etf_news_cache = cache_dir / f"etf_news_{etf6}_{day}.json"
        if etf_news_cache.exists():
            try:
                holdings_news = json.loads(etf_news_cache.read_text(encoding="utf-8"))
            except Exception:
                holdings_news = {}
        else:
            holdings_news = run_etf_pipeline(
                session,
                deepseek,
                etf_code=etf_code.strip(),
                max_age_days=3,
                etf_source=etf_source_effective,
                max_workers=max_workers,
                debug=debug,
            )
            try:
                etf_news_cache.write_text(json.dumps(holdings_news, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        news_text = build_etf_news_text(holdings_news, max_items=3)
        if timing:
            try:
                import logging

                logging.getLogger(__name__).warning("Timing %s: news=%.2fs", etf_code_norm, time.perf_counter() - t_news0)
            except Exception:
                pass
    else:
        hot_items = []
        hot_search_text = ""
        holdings_news = {"top_3": [], "raw_news": [], "summaries": []}
        news_text = ""

    holdings_strict = os.environ.get("HOLDINGS_STRICT", "").strip() == "1"
    top5_source = ""
    top5_as_of = ""
    if fetch_holdings:
        t_hold0 = time.perf_counter()
        top10_holdings: list[EtfHolding] = []
        etf_name_from_holdings = ""
        as_of_from_holdings = ""
        try:
            etf_name_from_holdings, as_of_from_holdings, top10_holdings = fetch_etf_top10_holdings(session, etf_code.strip(), topline=10)
        except Exception:
            top10_holdings = []
        top5_holdings = top10_holdings[:5]
        if top5_holdings:
            top5_source = "eastmoney"
            top5_as_of = as_of_from_holdings or ""
        else:
            logging.getLogger(__name__).warning("Signal: 权重股持仓为空（eastmoney），尝试 XtQuant 申赎清单兜底，etf=%s", etf_code_norm)
            if _xtdata_available():
                top5_holdings = _xt_etf_basket_top5_holdings(etf_code_norm)
                if top5_holdings:
                    top5_source = "xtquant_basket"
                    top5_as_of = "申赎清单"
            if not top5_holdings:
                top5_source = "none"
                if holdings_strict:
                    raise RuntimeError(f"Signal: 权重股持仓缺失（eastmoney+xtquant 均失败）etf={etf_code_norm}")
                logging.getLogger(__name__).warning("Signal: 权重股持仓缺失（eastmoney+xtquant），etf=%s", etf_code_norm)
        if timing:
            try:
                import logging

                logging.getLogger(__name__).warning(
                    "Timing %s: holdings=%.2fs top5=%s", etf_code_norm, time.perf_counter() - t_hold0, len(top5_holdings)
                )
            except Exception:
                pass
    else:
        top5_holdings = []
        top5_source = "disabled"

    codes: list[str] = [etf_code_norm, "000001.SH", "399006.SZ"]
    for h in top5_holdings:
        codes.append(normalize_code(h.stock_code))
    codes = list(dict.fromkeys([c for c in codes if c]))

    if _xtdata_available():
        t_mkt0 = time.perf_counter()
        try:
            import logging

            logging.getLogger(__name__).warning("Signal: 拉取行情数据 %s", etf_code_norm)
        except Exception:
            pass
        start_time = (datetime.now().astimezone() - timedelta(days=180)).strftime("%Y%m%d")
        _xt_download_daily(codes, start_time=start_time)
        raw = _xt_get_daily(codes, count=130)
        try:
            import logging

            logging.getLogger(__name__).warning("Signal: 行情数据完成 %s", etf_code_norm)
        except Exception:
            pass
        if timing:
            try:
                import logging

                logging.getLogger(__name__).warning("Timing %s: market_data=%.2fs", etf_code_norm, time.perf_counter() - t_mkt0)
            except Exception:
                pass
    else:
        raw = {}

    etf_kl = _pick_latest_kline_series(raw, etf_code_norm) if raw else None
    sh_kl = _pick_latest_kline_series(raw, "000001.SH") if raw else None
    cy_kl = _pick_latest_kline_series(raw, "399006.SZ") if raw else None
    top5_kl: list[tuple[EtfHolding, Optional[KlineSeries]]] = []
    for h in top5_holdings:
        top5_kl.append((h, _pick_latest_kline_series(raw, normalize_code(h.stock_code)) if raw else None))

    fields = (
        compute_etf_features(
            etf_kl,
            etf_code=etf_code_norm,
            market_sh=sh_kl,
            market_cy=cy_kl,
            top5_stocks=top5_kl,
            instrument_name=instrument_name,
        )
        if etf_kl is not None
        else compute_etf_features(
            KlineSeries(times=[0, 1], open=[float("nan"), float("nan")], high=[float("nan"), float("nan")], low=[float("nan"), float("nan")], close=[float("nan"), float("nan")], volume=[float("nan"), float("nan")], amount=[float("nan"), float("nan")]),
            etf_code=etf_code_norm,
            market_sh=None,
            market_cy=None,
            top5_stocks=[],
            instrument_name=instrument_name,
        )
    )
    if not top5_holdings:
        fields["top5_stocks_perf"] = "数据缺失（未拉取权重股持仓）"
    fields["top5_source"] = top5_source
    fields["top5_as_of"] = top5_as_of
    fields["hot_search_text"] = hot_search_text
    fields["news_text"] = news_text
    fields["yesterday_evaluation"] = yesterday_evaluation or "无"
    selftest = os.environ.get("FININTEL_CHIP_SELFTEST", "").strip() == "1"
    t_breadth0 = time.perf_counter()
    if selftest:
        fields["up_down_ratio"] = "自测跳过"
    else:
        try:
            import logging

            logging.getLogger(__name__).warning("Signal: 计算市场宽度 %s", etf_code_norm)
        except Exception:
            pass
        fields["up_down_ratio"] = compute_up_down_ratio_all()
        try:
            import logging

            logging.getLogger(__name__).warning("Signal: 市场宽度完成 %s", etf_code_norm)
        except Exception:
            pass
    if timing:
        try:
            import logging

            logging.getLogger(__name__).warning("Timing %s: up_down_ratio=%.2fs", etf_code_norm, time.perf_counter() - t_breadth0)
        except Exception:
            pass
    t_share0 = time.perf_counter()
    if selftest:
        fields["share_change_text"] = "自测跳过"
    elif not fetch_share_snapshot:
        fields["share_change_text"] = "数据缺失（已跳过份额快照）"
    else:
        direction, val = compute_share_change_from_snapshot(etf_code_norm)
        if direction and val:
            fields["share_change_text"] = f"{direction}{val}万份"
        else:
            fields["share_change_text"] = "数据缺失（快照缺失或无可用历史基准）"
    if timing:
        try:
            import logging

            logging.getLogger(__name__).warning("Timing %s: share_snapshot=%.2fs", etf_code_norm, time.perf_counter() - t_share0)
        except Exception:
            pass
    if fetch_fundflow and not selftest:
        t_ff0 = time.perf_counter()
        fields["net_inflow"] = compute_net_inflow_wanyuan(top5_holdings=top5_holdings, etf_code_norm=etf_code_norm)
        if timing:
            try:
                import logging

                logging.getLogger(__name__).warning("Timing %s: net_inflow=%.2fs", etf_code_norm, time.perf_counter() - t_ff0)
            except Exception:
                pass

    close_now = None
    try:
        if etf_kl is not None and etf_kl.close and math.isfinite(etf_kl.close[-1]):
            close_now = float(etf_kl.close[-1])
    except Exception:
        close_now = None
    fields.update(load_chip_factors(etf_code_norm, current_price=close_now))

    prompt = PROMPT_ETF_SIGNAL.format_map(fields)
    if os.environ.get("FININTEL_CHIP_SELFTEST", "").strip() == "1":
        old_phrase = "**获利盘比例**： 当前价格处于近 60 日"
        if old_phrase in prompt:
            raise RuntimeError(f"ChipSelfTest: 仍在使用旧的获利盘文案: {old_phrase}")
        if "获利盘比例(筹码口径)" not in prompt:
            raise RuntimeError("ChipSelfTest: prompt 未包含‘获利盘比例(筹码口径)’，替换可能未生效")
        if fields.get("chip_profit_ratio") in ("", "数据缺失") or fields.get("chip_dense_zones") in ("", "数据缺失"):
            raise RuntimeError(f"ChipSelfTest: 缺少筹码口径因子 etf={etf_code_norm} factors={fields.get('chip_profit_ratio')}/{fields.get('chip_dense_zones')}")
        logging.getLogger(__name__).warning("ChipSelfTest: 通过 %s trade_date=%s", etf_code_norm, fields.get("chip_trade_date"))
        return {
            "etf_code": etf_code.strip(),
            "etf_code_norm": etf_code_norm,
            "fields": fields,
            "hot_items": [it.to_dict() for it in hot_items[:10]],
            "etf_news": holdings_news,
            "deepseek_output": "(skipped by FININTEL_CHIP_SELFTEST)",
            "sentiment_struct": {},
            "prompt": prompt,
        }
    try:
        import logging

        logging.getLogger(__name__).warning("Signal: 调用 DeepSeek %s", etf_code_norm)
    except Exception:
        pass
    t_ds0 = time.perf_counter()
    output = deepseek.chat(system="你是一位A股短线题材交易专家。", user=prompt, temperature=0.2, force_json=False)
    if timing:
        try:
            import logging

            logging.getLogger(__name__).warning("Timing %s: deepseek=%.2fs", etf_code_norm, time.perf_counter() - t_ds0)
            logging.getLogger(__name__).warning("Timing %s: total=%.2fs", etf_code_norm, time.perf_counter() - t0)
        except Exception:
            pass
    try:
        import logging

        logging.getLogger(__name__).warning("Signal: 完成 %s", etf_code_norm)
    except Exception:
        pass

    sentiment_struct = _extract_sentiment_struct(output)
    return {
        "etf_code": etf_code.strip(),
        "etf_code_norm": etf_code_norm,
        "fields": fields,
        "hot_items": [it.to_dict() for it in hot_items[:10]],
        "etf_news": holdings_news,
        "deepseek_output": output,
        "sentiment_struct": sentiment_struct,
        "prompt": prompt,
    }


def _extract_sentiment_struct(text: str) -> dict[str, object]:
    s = str(text or "")
    grade = "C"
    conf = "LOW"
    m = None
    for mm in re.finditer(r"SENTIMENT_JSON:\s*(\{[^\n\r]*\})\s*$", s, flags=re.M):
        m = mm
    if m:
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                g = str(obj.get("sentiment_grade") or "").strip().upper()
                c = str(obj.get("confidence") or "").strip().upper()
                if g in {"A", "B", "C", "D", "E"}:
                    grade = g
                if c in {"HIGH", "MEDIUM", "LOW"}:
                    conf = c
        except Exception:
            pass
    score_01_map: dict[str, float] = {"A": 0.90, "B": 0.65, "C": 0.50, "D": 0.25, "E": 0.10}
    score_100_map: dict[str, int] = {"A": 85, "B": 70, "C": 50, "D": 30, "E": 15}
    base01 = float(score_01_map.get(grade, 0.50))
    if conf == "LOW":
        base01 = (base01 + 0.50) / 2.0
    score01 = float(min(1.0, max(0.0, base01)))
    score100 = int(score_100_map.get(grade, 50))
    return {
        "sentiment_grade": str(grade),
        "confidence": str(conf),
        "sentiment_score_01": float(score01),
        "sentiment_score_100": int(score100),
    }
