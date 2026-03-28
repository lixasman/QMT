from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
import re
import os
import sys

import pandas as pd

from core.time_utils import get_trading_dates
from core.warn_utils import info_once, warn_once
from newsget.http import HttpConfig, build_session
from newsget.models import now_iso

from .deepseek_client import DeepSeekClient
from .etf_pipeline import run_etf_pipeline
from .etf_signal_pipeline import normalize_code, run_etf_signal_pipeline
from .etf_selector import select_top_hot_etfs, select_universe_daily_gainers
from .pipeline import run_pipeline


def _default_output_paths(prefix: str) -> tuple[Path, Path]:
    ts = now_iso().replace(":", "").replace("-", "")
    safe = ts.replace("+", "_").replace(".", "_")
    out_final = Path("output") / f"{prefix}_top3_{safe}.json"
    out_trace = Path("output") / f"{prefix}_trace_{safe}.json"
    return out_final, out_trace


def _env_flag_enabled(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, min_value: int, max_value: int | None = None) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return int(default)
    try:
        v = int(raw)
    except Exception:
        warn_once(
            f"finintel_env_int_invalid:{name}",
            f"FinIntel: invalid integer env ignored. name={name} raw={raw!r} fallback={int(default)}",
            logger_name=__name__,
        )
        return int(default)
    if v < int(min_value):
        return int(min_value)
    if max_value is not None and v > int(max_value):
        return int(max_value)
    return int(v)


def _prev_trading_day(today: str) -> str:
    try:
        dt = datetime.strptime(str(today), "%Y%m%d")
    except Exception:
        return ""
    start = (dt - timedelta(days=40)).strftime("%Y%m%d")
    try:
        cal = get_trading_dates(start, today)
    except Exception as e:
        warn_once(
            "finintel_prev_trading_day_query_failed",
            f"FinIntel: failed to query trading calendar for previous trading day; fallback to natural date. today={today} err={repr(e)}",
            logger_name=__name__,
        )
        return ""
    prev = ""
    for d in cal:
        s = str(d)
        if len(s) == 8 and s.isdigit() and s < today and s > prev:
            prev = s
    return prev


def _today_yyyymmdd() -> str:
    fake = os.environ.get("FININTEL_FAKE_TODAY", "").strip()
    if fake:
        if re.fullmatch(r"\d{8}", fake):
            return fake
        warn_once(
            "finintel_fake_today_invalid",
            f"FinIntel: invalid FININTEL_FAKE_TODAY ignored (expected YYYYMMDD): {fake!r}",
            logger_name=__name__,
        )

    today = datetime.now().astimezone().strftime("%Y%m%d")
    if not _env_flag_enabled("FININTEL_INDEX_NON_TRADING_TO_PREV", default=True):
        info_once(
            "finintel_non_trading_index_disabled",
            "FinIntel: non-trading-day index remap disabled by FININTEL_INDEX_NON_TRADING_TO_PREV=0; using natural date.",
            logger_name=__name__,
        )
        return today

    try:
        today_cal = get_trading_dates(today, today)
    except Exception as e:
        warn_once(
            "finintel_today_calendar_query_failed",
            f"FinIntel: failed to query trading calendar for today; fallback to natural date. today={today} err={repr(e)}",
            logger_name=__name__,
        )
        return today

    is_trading_day = any(str(d) == today for d in today_cal)
    if is_trading_day:
        return today

    prev = _prev_trading_day(today)
    if prev:
        warn_once(
            f"finintel_non_trading_day_remap:{today}",
            f"FinIntel: non-trading day detected; remap output date to previous trading day. today={today} indexed_day={prev}",
            logger_name=__name__,
        )
        return prev

    warn_once(
        f"finintel_prev_trading_day_missing:{today}",
        f"FinIntel: non-trading day detected but previous trading day unavailable; fallback to natural date. today={today}",
        logger_name=__name__,
    )
    return today


def cleanup_old_signal_outputs(
    *,
    output_dir: str | Path = "output",
    today_yyyymmdd: str,
    retention_days: int = 3,
) -> dict[str, int]:
    root = Path(output_dir)
    if not root.exists():
        return {"deleted": 0, "failed": 0}
    try:
        cutoff = (datetime.strptime(str(today_yyyymmdd), "%Y%m%d") - timedelta(days=int(retention_days))).strftime("%Y%m%d")
    except Exception:
        return {"deleted": 0, "failed": 0}
    deleted = 0
    failed = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if not name.startswith("finintel_signal"):
            continue
        match = re.search(r"(\d{8})", name)
        if not match:
            continue
        if str(match.group(1)) >= cutoff:
            continue
        try:
            path.unlink()
            deleted += 1
        except Exception as e:
            failed += 1
            logging.getLogger(__name__).warning("FinIntel: 清理历史输出失败 path=%s err=%s", str(path), repr(e))
    return {"deleted": int(deleted), "failed": int(failed)}


def _emit_json_stdout(obj: object) -> None:
    try:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(obj, ensure_ascii=True, indent=2))


def _extract_overall_rating(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"综合评级[\s\S]{0,600}?\*\*(.+?)\*\*", text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"综合评级[\s\S]{0,200}?:\s*([^\n\r]{1,30})", text)
    if m2:
        return m2.group(1).strip()
    return text.strip().splitlines()[0][:30] if text.strip() else ""


def _extract_eval_sections(text: str) -> str:
    if not text:
        return ""
    b3 = re.search(r"^#{2,4}\s*3\.\s*综合评级[\s\S]*?(?=^#{2,4}\s*4\.\s*操作建议|\Z)", text, flags=re.M)
    b4 = re.search(r"^#{2,4}\s*4\.\s*操作建议[\s\S]*?(?=^#{2,4}\s*\d+\.|\Z)", text, flags=re.M)
    parts: list[str] = []
    if b3:
        parts.append(b3.group(0).strip())
    if b4:
        parts.append(b4.group(0).strip())
    return "\n\n".join(parts).strip()


def _find_latest_dated_file(dir_path: Path, *, glob_pat: str, today: str, date_re: str) -> Path | None:
    best_date = ""
    best_path: Path | None = None
    for p in dir_path.glob(glob_pat):
        m = re.search(date_re, p.name)
        if not m:
            continue
        d = m.group(1)
        if d >= today:
            continue
        if d > best_date:
            best_date = d
            best_path = p
    return best_path



def _load_latest_yesterday_eval(etf_code_norm: str) -> str:
    eval_dir = Path("output") / "eval"
    today = _today_yyyymmdd()
    etf6 = etf_code_norm.split(".")[0]
    if eval_dir.exists():
        md_path = _find_latest_dated_file(eval_dir, glob_pat=f"finintel_signal_eval_{etf6}_*.md", today=today, date_re=r"_(\d{8})\.md$")
        if md_path:
            try:
                s = md_path.read_text(encoding="utf-8").strip()
                return s or "无"
            except Exception as e:
                info_once(f"finintel_eval_md_read_failed:{str(md_path)}", f"FinIntel: 读取昨日评价失败，已降级继续: {md_path} err={repr(e)}", logger_name=__name__)
        txt_path = _find_latest_dated_file(eval_dir, glob_pat=f"finintel_signal_eval_{etf6}_*.txt", today=today, date_re=r"_(\d{8})\.txt$")
        if txt_path:
            try:
                s = txt_path.read_text(encoding="utf-8").strip()
                return s or "无"
            except Exception as e:
                info_once(f"finintel_eval_txt_read_failed:{str(txt_path)}", f"FinIntel: 读取昨日评价失败，已降级继续: {txt_path} err={repr(e)}", logger_name=__name__)

    out_dir = Path("output")
    best_json_path = _find_latest_dated_file(out_dir, glob_pat=f"finintel_signal_{etf6}_*.json", today=today, date_re=r"_(\d{8})\.json$")
    if not best_json_path:
        return "无"
    try:
        obj = json.loads(best_json_path.read_text(encoding="utf-8"))
        deepseek_out = str(obj.get("deepseek_output") or "")
        sections = _extract_eval_sections(deepseek_out)
        if sections:
            return sections
        rating = _extract_overall_rating(deepseek_out)
        return rating or "无"
    except Exception as e:
        info_once(f"finintel_yesterday_eval_parse_failed:{str(best_json_path)}", f"FinIntel: 解析昨日信号 JSON 失败，已降级为无: {best_json_path} err={repr(e)}", logger_name=__name__)
        return "无"


def _write_signal_human_files(result: dict, *, etf_code_norm: str) -> None:
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    day = _today_yyyymmdd()
    etf6 = etf_code_norm.split(".")[0]
    report_path = out_dir / f"finintel_signal_{etf6}_{day}.md"
    parts: list[str] = []
    deepseek_out = str(result.get("deepseek_output") or "")
    if deepseek_out:
        parts.append(deepseek_out.strip())
    prompt = str(result.get("prompt") or "")
    if prompt:
        parts.append("\n\n---\n\n## Prompt\n\n" + prompt.strip())
    report_path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")

    eval_dir = out_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    rating = _extract_overall_rating(deepseek_out)
    (eval_dir / f"finintel_signal_eval_{etf6}_{day}.txt").write_text(rating or "无", encoding="utf-8")
    md = _extract_eval_sections(deepseek_out)
    (eval_dir / f"finintel_signal_eval_{etf6}_{day}.md").write_text(md or "无", encoding="utf-8")


def _write_signal_json_and_optional_trace(
    result: dict,
    *,
    etf_code_norm: str,
    day: str,
    write_trace: bool,
) -> None:
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    etf6 = etf_code_norm.split(".")[0]
    json_path = out_dir / f"finintel_signal_{etf6}_{day}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if write_trace:
        trace_path = out_dir / f"finintel_signal_{etf6}_{day}_trace.json"
        trace_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_signal_integration_json(result, etf_code_norm=etf_code_norm, day=day)


def _rating_to_grade(rating: str) -> str:
    s = str(rating or "")
    if "强烈看多" in s:
        return "A"
    if "偏多" in s:
        return "B"
    if "强烈看空" in s:
        return "E"
    if "偏空" in s:
        return "D"
    if "中性" in s or "观望" in s:
        return "C"
    return "C"


def _write_signal_integration_json(result: dict, *, etf_code_norm: str, day: str) -> None:
    out_dir = Path("output") / "integration" / "finintel"
    out_dir.mkdir(parents=True, exist_ok=True)
    etf6 = etf_code_norm.split(".")[0]

    s0 = result.get("sentiment_struct")
    grade = ""
    confidence = ""
    score01 = None
    score100 = None
    if isinstance(s0, dict):
        grade = str(s0.get("sentiment_grade") or "").strip().upper()
        confidence = str(s0.get("confidence") or "").strip().upper()
        try:
            score01 = float(s0.get("sentiment_score_01")) if s0.get("sentiment_score_01") is not None else None
        except Exception as e:
            logging.getLogger(__name__).warning(
                "FinIntel: sentiment_score_01 解析失败，已降级回退。etf=%s day=%s err=%s",
                etf_code_norm,
                day,
                repr(e),
            )
            score01 = None
        try:
            score100 = int(s0.get("sentiment_score_100")) if s0.get("sentiment_score_100") is not None else None
        except Exception as e:
            logging.getLogger(__name__).warning(
                "FinIntel: sentiment_score_100 解析失败，已降级回退。etf=%s day=%s err=%s",
                etf_code_norm,
                day,
                repr(e),
            )
            score100 = None

    if grade not in {"A", "B", "C", "D", "E"}:
        rating = _extract_overall_rating(str(result.get("deepseek_output") or ""))
        logging.getLogger(__name__).warning(
            "FinIntel: sentiment_grade 缺失/非法，已降级由 overall_rating 映射。etf=%s day=%s raw_grade=%s",
            etf_code_norm,
            day,
            str(grade),
        )
        grade = _rating_to_grade(rating)
        confidence = "LOW"

    score_01_map: dict[str, float] = {"A": 0.90, "B": 0.65, "C": 0.50, "D": 0.25, "E": 0.10}
    score_100_map: dict[str, int] = {"A": 85, "B": 70, "C": 50, "D": 30, "E": 15}
    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
        logging.getLogger(__name__).warning(
            "FinIntel: confidence 缺失/非法，已降级为 LOW。etf=%s day=%s raw_conf=%s",
            etf_code_norm,
            day,
            str(confidence),
        )
        confidence = "LOW"
    if score01 is None:
        base01 = float(score_01_map.get(grade, 0.50))
        if confidence == "LOW":
            base01 = (base01 + 0.50) / 2.0
        score01 = float(min(1.0, max(0.0, base01)))
    if score100 is None:
        score100 = int(score_100_map.get(grade, 50))

    p = out_dir / f"sentiment_{etf6}_{day}.json"
    obj = {
        "etf_code": str(etf_code_norm),
        "day": str(day),
        "sentiment_grade": str(grade),
        "confidence": str(confidence),
        "sentiment_score_01": float(score01),
        "sentiment_score_100": int(score100),
    }
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_sentiment_summary_from_struct(
    sentiment_struct: dict | None,
    *,
    deepseek_output: str = "",
) -> dict[str, object] | None:
    if not isinstance(sentiment_struct, dict):
        return None
    grade = str(sentiment_struct.get("sentiment_grade") or "").strip().upper()
    confidence = str(sentiment_struct.get("confidence") or "").strip().upper()
    score100 = None
    try:
        if sentiment_struct.get("sentiment_score_100") is not None:
            score100 = int(sentiment_struct.get("sentiment_score_100"))
    except Exception:
        score100 = None
    if grade not in {"A", "B", "C", "D", "E"}:
        if deepseek_output:
            rating = _extract_overall_rating(deepseek_output)
            grade = _rating_to_grade(rating)
            confidence = "LOW"
        else:
            return None
    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
        confidence = "LOW"
    if score100 is None:
        score100_map: dict[str, int] = {"A": 85, "B": 70, "C": 50, "D": 30, "E": 15}
        score100 = int(score100_map.get(grade, 50))
    return {"grade": grade, "score_100": score100, "confidence": confidence}


def _load_today_sentiment_summary(
    etf_code_norm: str,
    day: str,
    *,
    sentiment_struct: dict | None = None,
    deepseek_output: str = "",
) -> dict[str, object]:
    summary = _build_sentiment_summary_from_struct(sentiment_struct, deepseek_output=deepseek_output)
    if summary:
        return summary
    etf6 = etf_code_norm.split(".")[0]
    integration_path = Path("output") / "integration" / "finintel" / f"sentiment_{etf6}_{day}.json"
    if integration_path.exists():
        try:
            obj = json.loads(integration_path.read_text(encoding="utf-8"))
            summary = _build_sentiment_summary_from_struct(obj, deepseek_output="")
            if summary:
                return summary
        except Exception as e:
            logging.getLogger(__name__).warning(
                "HotETF: 读取情绪集成文件失败，已降级继续。etf=%s day=%s err=%s",
                etf_code_norm,
                day,
                repr(e),
            )
    signal_path = Path("output") / f"finintel_signal_{etf6}_{day}.json"
    if signal_path.exists():
        try:
            obj = json.loads(signal_path.read_text(encoding="utf-8"))
            deepseek_out = str(obj.get("deepseek_output") or "")
            rating = _extract_overall_rating(deepseek_out)
            grade = _rating_to_grade(rating)
            summary = _build_sentiment_summary_from_struct(
                {"sentiment_grade": grade, "confidence": "LOW"},
                deepseek_output=deepseek_out,
            )
            if summary:
                return summary
        except Exception as e:
            logging.getLogger(__name__).warning(
                "HotETF: 读取情绪信号文件失败，已降级继续。etf=%s day=%s err=%s",
                etf_code_norm,
                day,
                repr(e),
            )
    return {"grade": "", "score_100": None, "confidence": ""}


def _emit_hot_etf_sentiment_summary(rows: list[dict[str, object]], day: str) -> None:
    if not rows:
        return
    logger = logging.getLogger(__name__)
    logger.warning("HotETF Summary %s (n=%s)", str(day), len(rows))
    for row in rows:
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        grade = str(row.get("grade") or "").strip().upper() or "N/A"
        score100 = row.get("score_100")
        score_text = "N/A" if score100 in (None, "") else str(score100)
        confidence = str(row.get("confidence") or "").strip().upper() or "N/A"
        if name:
            logger.warning("%s %s %s %s %s", code, name, grade, score_text, confidence)
        else:
            logger.warning("%s %s %s %s", code, grade, score_text, confidence)


def _append_hot_etf_sentiment_history(rows: list[dict[str, object]], day: str) -> None:
    if not rows:
        return
    out_dir = Path("output") / "finintel_50ETF_sentiment_history"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "finintel_sentiment_history.csv"

    new_rows: list[dict[str, object]] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        new_rows.append(
            {
                "date": str(day),
                "code": code,
                "name": str(row.get("name") or ""),
                "grade": str(row.get("grade") or ""),
                "confidence": str(row.get("confidence") or ""),
            }
        )
    if not new_rows:
        return

    new_df = pd.DataFrame(new_rows, columns=["date", "code", "name", "grade", "confidence"])
    if out_path.exists():
        try:
            old_df = pd.read_csv(out_path, dtype=str)
        except Exception:
            old_df = pd.DataFrame(columns=new_df.columns)
        merged = pd.concat([old_df, new_df], ignore_index=True)
    else:
        merged = new_df

    merged["date"] = merged["date"].astype(str)
    merged["code"] = merged["code"].astype(str)
    merged = merged.drop_duplicates(subset=["date", "code"], keep="last")
    merged.to_csv(out_path, index=False, encoding="utf-8-sig")


def _first_non_empty_env(*names: str) -> str:
    for name in names:
        v = str(os.environ.get(name, "")).strip()
        if v:
            return v
    return ""


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            s = value.replace(",", "").strip()
            if not s:
                return None
            return float(s)
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _normalize_hot_pool_code(raw_code: object) -> str:
    s = str(raw_code or "").strip().upper()
    if not s:
        return ""
    if re.fullmatch(r"\d{6}\.(SH|SZ)", s):
        return s
    m = re.fullmatch(r"(SH|SZ)[\.\-_:]?(\d{6})", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m2 = re.fullmatch(r"(\d{6})[\.\-_:]?(SH|SZ)", s)
    if m2:
        return f"{m2.group(1)}.{m2.group(2)}"
    m3 = re.fullmatch(r"(\d{6})\.(XSHG|XSHE)", s)
    if m3:
        return f"{m3.group(1)}.{'SH' if m3.group(2) == 'XSHG' else 'SZ'}"
    s2 = s.replace("SHSE.", "").replace("SZSE.", "")
    if re.fullmatch(r"\d{6}", s2):
        return normalize_code(s2)
    norm = normalize_code(s2)
    if re.fullmatch(r"\d{6}\.(SH|SZ)", norm):
        return norm
    return ""


def _position_field(raw: object, keys: tuple[str, ...]) -> object:
    if isinstance(raw, dict):
        for k in keys:
            if k in raw:
                v = raw.get(k)
                if v not in (None, ""):
                    return v
    for k in keys:
        v = getattr(raw, k, None)
        if v not in (None, ""):
            return v
    return None


def _extract_position_qty(raw: object) -> float | None:
    qty_fields = (
        "total_qty",
        "total_volume",
        "hold_qty",
        "hold_volume",
        "volume",
        "current_amount",
        "current_volume",
        "can_use_volume",
        "available_volume",
        "position_qty",
        "m_nVolume",
        "m_nCanUseVolume",
        "持仓数量",
        "股份余额",
        "可用数量",
        "可卖数量",
    )
    vals: list[float] = []
    for k in qty_fields:
        v = _safe_float(_position_field(raw, (k,)))
        if v is None:
            continue
        vals.append(float(v))
    if not vals:
        return None
    return float(max(vals))


def _extract_position_code(raw: object) -> str:
    code_fields = (
        "stock_code",
        "code",
        "symbol",
        "instrument_id",
        "ticker",
        "m_strInstrumentID",
        "证券代码",
    )
    return _normalize_hot_pool_code(_position_field(raw, code_fields))


def _extract_position_name(raw: object) -> str:
    name_fields = (
        "stock_name",
        "name",
        "instrument_name",
        "m_strInstrumentName",
        "证券名称",
    )
    return str(_position_field(raw, name_fields) or "").strip()


def _call_xt_query(fn, *, account: object) -> object:
    if account is None:
        return fn()
    try:
        return fn(account)
    except TypeError:
        return fn()


def _load_holdings_from_xt_account() -> tuple[dict[str, str], str]:
    xt_path = _first_non_empty_env("XT_TRADER_PATH", "XT_PATH", "QMT_XT_PATH")
    xt_account = _first_non_empty_env("XT_ACCOUNT_ID", "XT_ACCOUNT", "QMT_XT_ACCOUNT")
    xt_session = _first_non_empty_env("XT_SESSION_ID", "XT_SESSION", "QMT_XT_SESSION")
    if not xt_path or not xt_account or not xt_session:
        warn_once(
            "finintel_hot_holdings_xt_env_missing",
            "HotETF: XT holding injection is disabled (missing XT path/account/session env); fallback to portfolio state if available.",
            logger_name=__name__,
        )
        return {}, "unavailable"

    try:
        session_id = int(str(xt_session).strip())
    except Exception:
        warn_once(
            "finintel_hot_holdings_xt_session_invalid",
            f"HotETF: invalid XT session id; fallback to portfolio state. raw={xt_session!r}",
            logger_name=__name__,
        )
        return {}, "unavailable"

    try:
        from xtquant import xttrader  # type: ignore
    except Exception as e:
        warn_once(
            "finintel_hot_holdings_xt_import_failed",
            f"HotETF: xtquant.xttrader import failed; fallback to portfolio state. err={repr(e)}",
            logger_name=__name__,
        )
        return {}, "unavailable"

    trader = None
    try:
        trader_cls = getattr(xttrader, "XtQuantTrader", None)
        if not callable(trader_cls):
            raise RuntimeError("xttrader missing XtQuantTrader")
        trader = trader_cls(str(xt_path), int(session_id))
        start = getattr(trader, "start", None)
        connect = getattr(trader, "connect", None)
        if callable(start):
            start()
        if callable(connect):
            connect()

        account_obj = None
        acct_cls = getattr(xttrader, "StockAccount", None)
        if callable(acct_cls):
            account_obj = acct_cls(str(xt_account))
        else:
            try:
                from xtquant import xttype  # type: ignore
            except Exception:
                xttype = None  # type: ignore[assignment]
            if xttype is not None:
                acct_cls2 = getattr(xttype, "StockAccount", None)
                if callable(acct_cls2):
                    account_obj = acct_cls2(str(xt_account))
        sub = getattr(trader, "subscribe", None)
        if account_obj is not None and callable(sub):
            try:
                sub(account_obj)
            except Exception as e:
                warn_once(
                    "finintel_hot_holdings_xt_subscribe_failed",
                    f"HotETF: XT subscribe failed while querying holdings; continue without explicit subscribe. err={repr(e)}",
                    logger_name=__name__,
                )

        query_fn = getattr(trader, "query_stock_positions", None)
        if not callable(query_fn):
            query_fn = getattr(trader, "query_positions", None)
        if not callable(query_fn):
            raise RuntimeError("xttrader missing query positions")

        positions = list(_call_xt_query(query_fn, account=account_obj) or [])
    except Exception as e:
        warn_once(
            "finintel_hot_holdings_xt_query_failed",
            f"HotETF: XT holdings query failed; fallback to portfolio state. err={repr(e)}",
            logger_name=__name__,
        )
        return {}, "error"
    finally:
        if trader is not None:
            disconnect = getattr(trader, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect()
                except Exception:
                    pass
            stop = getattr(trader, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass

    out: dict[str, str] = {}
    unknown_qty = 0
    for raw in positions:
        code = _extract_position_code(raw)
        if not code:
            continue
        qty = _extract_position_qty(raw)
        if qty is not None and float(qty) <= 0:
            continue
        if qty is None:
            unknown_qty += 1
        name = _extract_position_name(raw)
        if code not in out or (not out[code] and name):
            out[code] = name

    if unknown_qty > 0:
        warn_once(
            "finintel_hot_holdings_qty_unknown",
            f"HotETF: some XT positions missing qty fields; force-include kept to avoid omission. unknown_qty={int(unknown_qty)} total_positions={int(len(positions))}",
            logger_name=__name__,
        )
    return out, "ok"


def _load_holdings_from_portfolio_state() -> dict[str, str]:
    raw_path = _first_non_empty_env("FININTEL_PORTFOLIO_STATE_PATH", "PORTFOLIO_STATE_PATH")
    state_path = Path(raw_path) if raw_path else (Path("data") / "state" / "portfolio.json")
    if not state_path.exists():
        warn_once(
            "finintel_hot_holdings_state_missing",
            f"HotETF: portfolio state file missing; holdings injection skipped. path={state_path}",
            logger_name=__name__,
        )
        return {}
    try:
        obj = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        warn_once(
            "finintel_hot_holdings_state_read_failed",
            f"HotETF: failed to read portfolio state; holdings injection skipped. path={state_path} err={repr(e)}",
            logger_name=__name__,
        )
        return {}

    raw_positions = obj.get("positions") or {}
    if not isinstance(raw_positions, dict):
        warn_once(
            "finintel_hot_holdings_state_invalid",
            f"HotETF: invalid portfolio state structure (positions is not dict); holdings injection skipped. path={state_path}",
            logger_name=__name__,
        )
        return {}

    out: dict[str, str] = {}
    for key, val in raw_positions.items():
        if not isinstance(val, dict):
            continue
        code = _normalize_hot_pool_code(val.get("etf_code") or key)
        if not code:
            continue
        total_qty = _safe_float(val.get("total_qty"))
        if total_qty is None:
            base = _safe_float(val.get("base_qty")) or 0.0
            s1 = _safe_float(val.get("scale_1_qty")) or 0.0
            s2 = _safe_float(val.get("scale_2_qty")) or 0.0
            total_qty = float(base + s1 + s2)
        if float(total_qty) <= 0:
            continue
        name = str(val.get("name") or "").strip()
        out[code] = name
    return out


_HOT_THEME_KEYWORDS: tuple[str, ...] = (
    "石油",
    "油气",
    "煤炭",
    "电池",
    "新能源",
    "光伏",
    "风电",
    "储能",
    "半导体",
    "芯片",
    "算力",
    "人工智能",
    "机器人",
    "医药",
    "医疗",
    "创新药",
    "中药",
    "消费",
    "食品",
    "饮料",
    "白酒",
    "汽车",
    "军工",
    "证券",
    "券商",
    "银行",
    "保险",
    "地产",
    "房地产",
    "有色",
    "黄金",
    "稀土",
    "钢铁",
    "化工",
    "影视",
    "传媒",
    "游戏",
    "旅游",
    "航空",
    "航运",
    "农业",
)


def _normalize_theme_name(raw_name: object) -> str:
    s = str(raw_name or "").strip()
    if not s:
        return ""
    s = re.sub(r"[\s\-_/·•（）()【】\[\]{}]+", "", s)
    patterns = (
        r"ETF",
        r"LOF",
        r"QDII",
        r"指数",
        r"增强",
        r"联接[ABC]?",
        r"发起式",
        r"证券投资",
        r"交易型开放式",
        r"基金",
        r"[ABC]类",
        r"[ABC]$",
        r"华夏",
        r"易方达",
        r"南方",
        r"招商",
        r"广发",
        r"嘉实",
        r"博时",
        r"富国",
        r"国泰",
        r"工银",
        r"汇添富",
        r"华安",
    )
    for p in patterns:
        s = re.sub(p, "", s, flags=re.I)
    s = re.sub(r"[A-Za-z0-9\.]+", "", s)
    return str(s).strip()


def _hot_theme_key(*, code: object, name: object) -> str:
    norm_name = _normalize_theme_name(name)
    for kw in _HOT_THEME_KEYWORDS:
        if kw and kw in norm_name:
            return kw
    if norm_name:
        return norm_name[:3]
    code_norm = _normalize_hot_pool_code(code)
    return f"CODE:{code_norm[:3] if code_norm else 'UNK'}"


def _theme_count_summary(themes: list[str], *, max_items: int = 8) -> list[dict[str, object]]:
    cnt: dict[str, int] = {}
    for t in themes:
        k = str(t or "")
        if not k:
            continue
        cnt[k] = int(cnt.get(k, 0)) + 1
    items = sorted(cnt.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    out: list[dict[str, object]] = []
    for k, v in items[: max(int(max_items), 1)]:
        out.append({"theme": str(k), "count": int(v)})
    return out


def _diversify_hot_pool(
    top_df: pd.DataFrame,
    *,
    target_n: int,
    max_per_theme: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    df = top_df.copy()
    if "code" not in df.columns:
        df["code"] = ""
    if "name" not in df.columns:
        df["name"] = ""
    if "score" not in df.columns:
        df["score"] = 0.0

    df["code"] = df["code"].map(_normalize_hot_pool_code)
    df = df[df["code"].astype(bool)].copy()
    df = df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    df["name"] = df["name"].fillna("").astype(str)
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0.0)
    df = df.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)

    max_theme_i = max(int(max_per_theme), 1)
    target_i = max(int(target_n), 0)
    if target_i <= 0 or df.empty:
        return df.head(0), {
            "raw_candidates": int(len(df)),
            "selected": 0,
            "max_per_theme": int(max_theme_i),
            "unique_themes": 0,
            "raw_theme_top": [],
            "selected_theme_top": [],
        }

    df["_theme"] = [
        _hot_theme_key(code=row["code"], name=row.get("name", ""))
        for _, row in df.iterrows()
    ]
    raw_theme_top = _theme_count_summary(df["_theme"].astype(str).tolist())

    selected_idx: list[int] = []
    deferred_idx: list[int] = []
    picked_by_theme: dict[str, int] = {}
    for idx, row in df.iterrows():
        theme = str(row["_theme"] or "")
        cur = int(picked_by_theme.get(theme, 0))
        if len(selected_idx) < target_i and cur < max_theme_i:
            selected_idx.append(int(idx))
            picked_by_theme[theme] = cur + 1
        else:
            deferred_idx.append(int(idx))

    if len(selected_idx) < target_i:
        for idx in deferred_idx:
            if len(selected_idx) >= target_i:
                break
            selected_idx.append(int(idx))

    out = df.loc[selected_idx].copy().head(target_i).reset_index(drop=True)
    selected_theme_top = _theme_count_summary(out["_theme"].astype(str).tolist())
    unique_theme_n = int(out["_theme"].nunique()) if "_theme" in out.columns else 0
    out = out.drop(columns=["_theme"], errors="ignore")
    return out, {
        "raw_candidates": int(len(df)),
        "selected": int(len(out)),
        "max_per_theme": int(max_theme_i),
        "unique_themes": int(unique_theme_n),
        "raw_theme_top": raw_theme_top,
        "selected_theme_top": selected_theme_top,
    }


def _load_hot_top_must_include_holdings() -> tuple[dict[str, str], str]:
    xt_holdings, xt_status = _load_holdings_from_xt_account()
    if xt_status == "ok":
        return xt_holdings, "xt_account"
    state_holdings = _load_holdings_from_portfolio_state()
    if state_holdings:
        warn_once(
            "finintel_hot_holdings_fallback_state",
            "HotETF: using portfolio state holdings as fallback because XT holdings are unavailable.",
            logger_name=__name__,
        )
        return state_holdings, "portfolio_state"
    return {}, "none"


def _inject_holdings_into_hot_pool(top_df: pd.DataFrame, *, holdings: dict[str, str]) -> tuple[pd.DataFrame, list[str]]:
    df = top_df.copy()
    if "code" not in df.columns:
        df["code"] = ""
    if "name" not in df.columns:
        df["name"] = ""

    df["code"] = df["code"].map(_normalize_hot_pool_code)
    df["name"] = df["name"].fillna("").astype(str)
    df = df[df["code"].astype(bool)].copy()
    df = df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)

    cols = list(df.columns)
    if not cols:
        cols = ["code", "name", "score"]
        df = pd.DataFrame(columns=cols)

    existing = set(str(x) for x in df["code"].tolist())
    injected_rows: list[dict[str, object]] = []
    injected_codes: list[str] = []
    for code in sorted(holdings.keys()):
        if code in existing:
            name = str(holdings.get(code) or "").strip()
            if name and "name" in df.columns:
                mask = df["code"] == code
                if bool(mask.any()):
                    idx = int(df.index[mask][0])
                    if not str(df.at[idx, "name"] or "").strip():
                        df.at[idx, "name"] = name
            continue
        row: dict[str, object] = {k: None for k in cols}
        row["code"] = str(code)
        if "name" in row:
            row["name"] = str(holdings.get(code) or "")
        if "score" in row:
            row["score"] = 0.0
        injected_rows.append(row)
        injected_codes.append(str(code))
        existing.add(str(code))

    if injected_rows:
        add_df = pd.DataFrame(injected_rows, columns=cols)
        df = pd.concat([df, add_df], ignore_index=True)
    return df, injected_codes


def _format_code_list(codes: list[str], *, max_items: int = 25) -> str:
    xs = [str(x) for x in codes if str(x).strip()]
    if len(xs) <= int(max_items):
        return ",".join(xs)
    return ",".join(xs[: int(max_items)]) + f"...(+{len(xs) - int(max_items)})"


def _merge_source_tags(values: list[object]) -> str:
    tags: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for tag in text.split("+"):
            tag2 = tag.strip()
            if tag2 and tag2 not in tags:
                tags.append(tag2)
    return "+".join(tags)


def merge_signal_candidate_pools(hot_df: pd.DataFrame, gainers_df: pd.DataFrame) -> pd.DataFrame:
    ordered_codes: list[str] = []
    rows_by_code: dict[str, dict[str, object]] = {}
    for frame, default_tag in ((hot_df, "hot"), (gainers_df, "universe_up_gt_1pct")):
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            code = normalize_code(str(row.get("code") or ""))
            if not code:
                continue
            if code not in rows_by_code:
                rows_by_code[code] = {
                    "code": code,
                    "name": str(row.get("name") or ""),
                    "score": float(row.get("score", 0.0) or 0.0),
                    "source_tag": str(row.get("source_tag") or default_tag),
                }
                ordered_codes.append(code)
                continue
            current = rows_by_code[code]
            if not str(current.get("name") or "").strip():
                current["name"] = str(row.get("name") or "")
            current["score"] = max(float(current.get("score", 0.0) or 0.0), float(row.get("score", 0.0) or 0.0))
            current["source_tag"] = _merge_source_tags([current.get("source_tag"), row.get("source_tag") or default_tag])
    out_rows = [rows_by_code[code] for code in ordered_codes]
    return pd.DataFrame(out_rows, columns=["code", "name", "score", "source_tag"])


def main(argv: list[str] | None = None) -> int:
    out_final, out_trace = _default_output_paths("finintel")

    parser = argparse.ArgumentParser(description="金融情报聚合与筛选系统（DeepSeek 两阶段）")
    parser.add_argument("--debug", action="store_true", help="输出调试信息到 stderr")
    parser.add_argument("--max-workers", type=int, default=0, help="Phase2 并发数（默认读取环境或使用 8）")
    parser.add_argument("--etf", default="", help="启用 ETF 权重股新闻模式：输入 ETF 代码，如 510300")
    parser.add_argument("--signal-etf", default="", help="启用 ETF 情绪分析模式：输入 ETF 代码，如 159107")
    parser.add_argument("--signal-hot-top", type=int, default=0, help="自动筛选热门 ETF TopN 并批量分析（如 10）")
    parser.add_argument(
        "--signal-hot-all-50",
        action="store_true",
        help="仅用于 --signal-hot-top：直接将默认 50 只 ETF 全量加入情绪池（不做涨幅筛选）",
    )
    parser.add_argument(
        "--hot-fast",
        action="store_true",
        help="仅用于 --signal-hot-top：启用快速模式（跳过新闻/持仓/资金流抓取，速度更快但信息降级）",
    )
    parser.add_argument("--etf-max-age-days", type=int, default=3, help="ETF 模式下新闻有效期（天），默认 3")
    parser.add_argument("--etf-source", default="auto", choices=["auto", "akshare", "cls", "eastmoney"], help="ETF 模式新闻来源：auto/akshare/cls/eastmoney")
    parser.add_argument("--timing", action="store_true", help="输出各阶段耗时（定位慢点用）")
    parser.add_argument("--no-share-snapshot", action="store_true", help="不拉取 ETF 份额快照（避免 akshare 卡住）")
    parser.add_argument("--output", default=str(out_final), help="最终 Top3 JSON 输出路径")
    parser.add_argument("--trace-output", default=str(out_trace), help="包含原文与中间摘要的追踪输出路径")
    parser.add_argument("--no-trace", action="store_true", help="不写追踪文件，仅写最终 Top3")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=(logging.INFO if (args.debug or args.timing) else logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    session = build_session(HttpConfig())
    deepseek = DeepSeekClient.from_env(session)

    try:
        if args.signal_hot_top and not args.signal_etf:
            t0 = time.perf_counter()
            day = _today_yyyymmdd()
            logging.getLogger(__name__).warning("HotETF: 开始筛选 Top%s", int(args.signal_hot_top))
            t_sel0 = time.perf_counter()
            target_hot_n = max(1, int(args.signal_hot_top))
            hot_pool_multiple = _env_int("FININTEL_HOT_DIVERSIFY_POOL_MULTIPLE", 5, min_value=1, max_value=20)
            selector_top_n = max(int(target_hot_n), int(target_hot_n * hot_pool_multiple))
            hot_no_diversify = _env_flag_enabled("FININTEL_HOT_NO_DIVERSIFY", default=False)
            hot_max_per_theme = _env_int(
                "FININTEL_HOT_DIVERSIFY_MAX_PER_THEME",
                2,
                min_value=1,
                max_value=max(int(target_hot_n), 1),
            )
            logging.getLogger(__name__).warning(
                "HotETF: selecting candidates. target_top=%s selector_top=%s diversify=%s max_per_theme=%s",
                int(target_hot_n),
                int(selector_top_n),
                ("off" if hot_no_diversify else "on"),
                int(hot_max_per_theme),
            )
            top_df_raw = select_top_hot_etfs(top_n=int(selector_top_n))
            if hot_no_diversify:
                top_df = top_df_raw.head(int(target_hot_n)).copy()
                diversity_summary: dict[str, object] = {
                    "raw_candidates": int(len(top_df_raw)),
                    "selected": int(len(top_df)),
                    "max_per_theme": None,
                    "unique_themes": None,
                    "raw_theme_top": [],
                    "selected_theme_top": [],
                }
            else:
                top_df, diversity_summary = _diversify_hot_pool(
                    top_df_raw,
                    target_n=int(target_hot_n),
                    max_per_theme=int(hot_max_per_theme),
                )
            t_sel1 = time.perf_counter()
            logging.getLogger(__name__).warning("HotETF: 筛选完成，开始批量分析，共%s只", len(top_df))
            hot_selected_count = int(len(top_df))
            logging.getLogger(__name__).warning(
                "HotETF: selection done. raw_candidates=%s selected=%s unique_themes=%s top_themes=%s",
                int(diversity_summary.get("raw_candidates", len(top_df_raw))),
                int(hot_selected_count),
                diversity_summary.get("unique_themes", None),
                json.dumps(diversity_summary.get("selected_theme_top", []), ensure_ascii=False),
            )
            forced_holdings, holdings_source = _load_hot_top_must_include_holdings()
            top_df, injected_codes = _inject_holdings_into_hot_pool(top_df, holdings=forced_holdings)
            top_df = top_df.copy()
            top_df["source_tag"] = "hot"
            logging.getLogger(__name__).warning(
                "HotETF: selection merged | hot_selected=%s holdings_loaded=%s holdings_injected=%s source=%s final=%s",
                int(hot_selected_count),
                int(len(forced_holdings)),
                int(len(injected_codes)),
                str(holdings_source),
                int(len(top_df)),
            )
            if injected_codes:
                logging.getLogger(__name__).warning("HotETF: holdings force-included codes=%s", _format_code_list(injected_codes))
            universe_path = Path(_first_non_empty_env("FININTEL_SIGNAL_UNIVERSE_PATH") or (Path("backtest") / "default_universe_50.txt"))
            universe_gainers_df = select_universe_daily_gainers(
                universe_path=universe_path,
                gain_threshold=0.01,
                include_all=bool(args.signal_hot_all_50),
            )
            merged_df = merge_signal_candidate_pools(top_df, universe_gainers_df)
            logging.getLogger(__name__).warning(
                "HotETF: pool union complete | hot=%s universe_up=%s final=%s universe_path=%s",
                int(len(top_df)),
                int(len(universe_gainers_df)),
                int(len(merged_df)),
                str(universe_path),
            )
            hot_csv = Path("output") / f"finintel_signal_hot_{day}.csv"
            hot_csv.parent.mkdir(parents=True, exist_ok=True)
            merged_df.to_csv(hot_csv, index=False, encoding="utf-8-sig")
            hot_fast = bool(args.hot_fast)
            logging.getLogger(__name__).warning(
                "HotETF: 批量信号模式=%s",
                ("fast(降级)" if hot_fast else "full(含新闻/持仓/资金流)"),
            )

            results: list[dict] = []
            per_etf_seconds: list[dict[str, object]] = []
            summary_rows: list[dict[str, object]] = []
            for i, (_, row) in enumerate(merged_df.iterrows(), start=1):
                t_etf0 = time.perf_counter()
                etf_code = str(row["code"])
                etf_code_norm = normalize_code(etf_code)
                etf6 = etf_code_norm.split(".")[0]
                out_json = Path("output") / f"finintel_signal_{etf6}_{day}.json"
                if out_json.exists():
                    logging.getLogger(__name__).warning("HotETF: (%s/%s) 跳过已生成 %s", i, len(merged_df), etf_code_norm)
                    results.append(
                        {
                            "code": etf_code_norm,
                            "name": str(row.get("name", "")),
                            "score": float(row.get("score", 0.0)),
                            "source_tag": str(row.get("source_tag", "")),
                        }
                    )
                    per_etf_seconds.append({"code": etf_code_norm, "skipped": True, "seconds": round(time.perf_counter() - t_etf0, 3)})
                    summary = _load_today_sentiment_summary(etf_code_norm, day)
                    summary_rows.append(
                        {
                            "code": etf_code_norm,
                            "name": str(row.get("name", "")),
                            "grade": summary.get("grade", ""),
                            "score_100": summary.get("score_100"),
                            "confidence": summary.get("confidence", ""),
                        }
                    )
                    continue
                yesterday_eval = _load_latest_yesterday_eval(etf_code_norm)
                logging.getLogger(__name__).warning("HotETF: (%s/%s) 分析 %s %s", i, len(merged_df), etf_code_norm, str(row.get("name", "")))
                r = run_etf_signal_pipeline(
                    session,
                    deepseek,
                    etf_code=etf_code,
                    debug=bool(args.debug),
                    max_workers=(args.max_workers if args.max_workers and args.max_workers > 0 else None),
                    etf_source=str(args.etf_source),
                    yesterday_evaluation=yesterday_eval,
                    fetch_news=(not hot_fast),
                    fetch_holdings=(not hot_fast),
                    fetch_fundflow=(not hot_fast),
                    fetch_share_snapshot=(not bool(args.no_share_snapshot)),
                    timing=bool(args.timing),
                )
                _write_signal_json_and_optional_trace(r, etf_code_norm=etf_code_norm, day=day, write_trace=not args.no_trace)
                _write_signal_human_files(r, etf_code_norm=etf_code_norm)
                summary = _load_today_sentiment_summary(
                    etf_code_norm,
                    day,
                    sentiment_struct=(r.get("sentiment_struct") if isinstance(r, dict) else None),
                    deepseek_output=(str(r.get("deepseek_output") or "") if isinstance(r, dict) else ""),
                )
                summary_rows.append(
                    {
                        "code": etf_code_norm,
                        "name": str(row.get("name", "")),
                        "grade": summary.get("grade", ""),
                        "score_100": summary.get("score_100"),
                        "confidence": summary.get("confidence", ""),
                    }
                )
                results.append(
                    {
                        "code": etf_code_norm,
                        "name": str(row.get("name", "")),
                        "score": float(row.get("score", 0.0)),
                        "source_tag": str(row.get("source_tag", "")),
                    }
                )
                per_etf_seconds.append({"code": etf_code_norm, "skipped": False, "seconds": round(time.perf_counter() - t_etf0, 3)})

            t1 = time.perf_counter()
            _emit_hot_etf_sentiment_summary(summary_rows, day)
            _append_hot_etf_sentiment_history(summary_rows, day)
            cleanup_summary = cleanup_old_signal_outputs(output_dir="output", today_yyyymmdd=day, retention_days=3)
            final_obj = {
                "date": day,
                "top_n": int(args.signal_hot_top),
                "hot_selector_top_n": int(selector_top_n),
                "hot_diversify_enabled": bool(not hot_no_diversify),
                "hot_diversify_max_per_theme": (None if hot_no_diversify else int(hot_max_per_theme)),
                "hot_diversify_summary": diversity_summary,
                "hot_selected_count": int(hot_selected_count),
                "universe_path": str(universe_path),
                "universe_gainers_count": int(len(universe_gainers_df)),
                "final_selected_count": int(len(merged_df)),
                "holding_source": str(holdings_source),
                "holding_loaded_count": int(len(forced_holdings)),
                "holding_force_included": injected_codes,
                "selected": results,
                "summary_csv": str(hot_csv),
                "cleanup": cleanup_summary,
            }
            if args.timing:
                final_obj["seconds"] = {
                    "select_hot": round(t_sel1 - t_sel0, 3),
                    "per_etf": per_etf_seconds,
                    "total": round(t1 - t0, 3),
                }
            _emit_json_stdout(final_obj)
            return 0
        if args.signal_etf:
            out_final2, out_trace2 = _default_output_paths("finintel_signal")
            if args.output == str(out_final):
                args.output = str(out_final2)
            if args.trace_output == str(out_trace):
                args.trace_output = str(out_trace2)

            etf_code_norm = normalize_code(str(args.signal_etf))
            day = _today_yyyymmdd()
            if args.output == str(out_final2):
                args.output = str(Path("output") / f"finintel_signal_{etf_code_norm.split('.')[0]}_{day}.json")
            if args.trace_output == str(out_trace2):
                args.trace_output = str(Path("output") / f"finintel_signal_{etf_code_norm.split('.')[0]}_{day}_trace.json")
            yesterday_eval = _load_latest_yesterday_eval(etf_code_norm)

            result = run_etf_signal_pipeline(
                session,
                deepseek,
                etf_code=str(args.signal_etf),
                debug=bool(args.debug),
                max_workers=(args.max_workers if args.max_workers and args.max_workers > 0 else None),
                etf_source=str(args.etf_source),
                yesterday_evaluation=yesterday_eval,
                timing=bool(args.timing),
            )
        elif args.etf:
            out_final2, out_trace2 = _default_output_paths("finintel_etf")
            if args.output == str(out_final):
                args.output = str(out_final2)
            if args.trace_output == str(out_trace):
                args.trace_output = str(out_trace2)

            result = run_etf_pipeline(
                session,
                deepseek,
                etf_code=str(args.etf),
                max_age_days=int(args.etf_max_age_days),
                etf_source=str(args.etf_source),
                debug=bool(args.debug),
                max_workers=(args.max_workers if args.max_workers and args.max_workers > 0 else None),
            )
        else:
            result = run_pipeline(
                session,
                deepseek,
                debug=bool(args.debug),
                max_workers=(args.max_workers if args.max_workers and args.max_workers > 0 else None),
            )
    except Exception as e:
        logging.getLogger(__name__).error("Pipeline 执行失败: %s", repr(e), exc_info=bool(args.debug))
        return 2

    final_obj = {"top_3": result["top_3"]} if "top_3" in result else result
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(final_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.timing:
        _emit_json_stdout({"output": str(out_path)})
    else:
        _emit_json_stdout(final_obj)

    if not args.no_trace:
        trace_path = Path(args.trace_output)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.signal_etf:
        _write_signal_human_files(result, etf_code_norm=normalize_code(str(args.signal_etf)))

    return 0
