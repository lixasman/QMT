from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
import re
import os
import sys

from core.warn_utils import info_once
from newsget.http import HttpConfig, build_session
from newsget.models import now_iso

from .deepseek_client import DeepSeekClient
from .etf_pipeline import run_etf_pipeline
from .etf_signal_pipeline import normalize_code, run_etf_signal_pipeline
from .etf_selector import select_top_hot_etfs
from .pipeline import run_pipeline


def _default_output_paths(prefix: str) -> tuple[Path, Path]:
    ts = now_iso().replace(":", "").replace("-", "")
    safe = ts.replace("+", "_").replace(".", "_")
    out_final = Path("output") / f"{prefix}_top3_{safe}.json"
    out_trace = Path("output") / f"{prefix}_trace_{safe}.json"
    return out_final, out_trace


def _today_yyyymmdd() -> str:
    fake = os.environ.get("FININTEL_FAKE_TODAY", "").strip()
    if re.fullmatch(r"\d{8}", fake):
        return fake
    return datetime.now().astimezone().strftime("%Y%m%d")


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
        except Exception:
            score01 = None
        try:
            score100 = int(s0.get("sentiment_score_100")) if s0.get("sentiment_score_100") is not None else None
        except Exception:
            score100 = None

    if grade not in {"A", "B", "C", "D", "E"}:
        rating = _extract_overall_rating(str(result.get("deepseek_output") or ""))
        grade = _rating_to_grade(rating)
        confidence = "LOW"

    score_01_map: dict[str, float] = {"A": 0.90, "B": 0.65, "C": 0.50, "D": 0.25, "E": 0.10}
    score_100_map: dict[str, int] = {"A": 85, "B": 70, "C": 50, "D": 30, "E": 15}
    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
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


def main(argv: list[str] | None = None) -> int:
    out_final, out_trace = _default_output_paths("finintel")

    parser = argparse.ArgumentParser(description="金融情报聚合与筛选系统（DeepSeek 两阶段）")
    parser.add_argument("--debug", action="store_true", help="输出调试信息到 stderr")
    parser.add_argument("--max-workers", type=int, default=0, help="Phase2 并发数（默认读取环境或使用 8）")
    parser.add_argument("--etf", default="", help="启用 ETF 权重股新闻模式：输入 ETF 代码，如 510300")
    parser.add_argument("--signal-etf", default="", help="启用 ETF 情绪分析模式：输入 ETF 代码，如 159107")
    parser.add_argument("--signal-hot-top", type=int, default=0, help="自动筛选热门 ETF TopN 并批量分析（如 10）")
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
            top_df = select_top_hot_etfs(top_n=int(args.signal_hot_top))
            t_sel1 = time.perf_counter()
            logging.getLogger(__name__).warning("HotETF: 筛选完成，开始批量分析，共%s只", len(top_df))
            hot_csv = Path("output") / f"finintel_signal_hot_{day}.csv"
            hot_csv.parent.mkdir(parents=True, exist_ok=True)
            top_df.to_csv(hot_csv, index=False, encoding="utf-8-sig")

            results: list[dict] = []
            per_etf_seconds: list[dict[str, object]] = []
            for i, (_, row) in enumerate(top_df.iterrows(), start=1):
                t_etf0 = time.perf_counter()
                etf_code = str(row["code"])
                etf_code_norm = normalize_code(etf_code)
                etf6 = etf_code_norm.split(".")[0]
                out_json = Path("output") / f"finintel_signal_{etf6}_{day}.json"
                if out_json.exists():
                    logging.getLogger(__name__).warning("HotETF: (%s/%s) 跳过已生成 %s", i, len(top_df), etf_code_norm)
                    results.append({"code": etf_code_norm, "name": str(row.get("name", "")), "score": float(row.get("score", 0.0))})
                    per_etf_seconds.append({"code": etf_code_norm, "skipped": True, "seconds": round(time.perf_counter() - t_etf0, 3)})
                    continue
                yesterday_eval = _load_latest_yesterday_eval(etf_code_norm)
                logging.getLogger(__name__).warning("HotETF: (%s/%s) 分析 %s %s", i, len(top_df), etf_code_norm, str(row.get("name", "")))
                r = run_etf_signal_pipeline(
                    session,
                    deepseek,
                    etf_code=etf_code,
                    debug=bool(args.debug),
                    max_workers=(args.max_workers if args.max_workers and args.max_workers > 0 else None),
                    etf_source=str(args.etf_source),
                    yesterday_evaluation=yesterday_eval,
                    fetch_news=False,
                    fetch_holdings=False,
                    fetch_fundflow=False,
                    fetch_share_snapshot=(not bool(args.no_share_snapshot)),
                    timing=bool(args.timing),
                )
                _write_signal_json_and_optional_trace(r, etf_code_norm=etf_code_norm, day=day, write_trace=not args.no_trace)
                _write_signal_human_files(r, etf_code_norm=etf_code_norm)
                results.append({"code": etf_code_norm, "name": str(row.get("name", "")), "score": float(row.get("score", 0.0))})
                per_etf_seconds.append({"code": etf_code_norm, "skipped": False, "seconds": round(time.perf_counter() - t_etf0, 3)})

            t1 = time.perf_counter()
            final_obj = {
                "date": day,
                "top_n": int(args.signal_hot_top),
                "selected": results,
                "summary_csv": str(hot_csv),
            }
            if args.timing:
                final_obj["seconds"] = {
                    "select_hot": round(t_sel1 - t_sel0, 3),
                    "per_etf": per_etf_seconds,
                    "total": round(t1 - t0, 3),
                }
            print(json.dumps(final_obj, ensure_ascii=False, indent=2))
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
        print(json.dumps({"output": str(out_path)}, ensure_ascii=False))
    else:
        print(json.dumps(final_obj, ensure_ascii=False, indent=2))

    if not args.no_trace:
        trace_path = Path(args.trace_output)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.signal_etf:
        _write_signal_human_files(result, etf_code_norm=normalize_code(str(args.signal_etf)))

    return 0
