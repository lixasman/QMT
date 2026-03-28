from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LOG_FILES = [
    "entry_decisions.jsonl",
    "exit_decisions.jsonl",
    "position_decisions.jsonl",
    "t0_decisions.jsonl",
]


def _parse_ts(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t:
        return None
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _date_key(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _norm_code(x: Any) -> str:
    s = str(x or "").strip().upper()
    if not s:
        return ""
    return s


def _short_json(x: Any, *, limit: int = 240) -> str:
    try:
        s = json.dumps(x, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except Exception:
        s = str(x)
    s = s.replace("\n", " ").strip()
    if len(s) > int(limit):
        return s[: int(limit)] + "…"
    return s


def _get(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _order_brief(order: Any) -> str:
    if not isinstance(order, dict) or not order:
        return ""
    side = str(order.get("side") or order.get("direction") or order.get("action") or "").strip().upper()
    qty = order.get("quantity") or order.get("qty") or ""
    price = order.get("price") or order.get("order_price") or ""
    parts = []
    if side:
        parts.append(side)
    if qty not in ("", None):
        parts.append(f"qty={qty}")
    if price not in ("", None):
        parts.append(f"price={price}")
    return " ".join(parts).strip()


def _event_summary(ev: dict[str, Any], *, source: str) -> str:
    typ = str(ev.get("type") or "").strip()
    if typ == "PHASE2_SCORE":
        score = ev.get("score")
        decision = ev.get("decision")
        note = str(ev.get("note") or "").strip()
        s = f"Entry Phase2: decision={decision} score={score}"
        if note:
            s += f" note={note}"
        return s
    if typ == "PHASE3_DECISION":
        action = ev.get("action")
        cond = ev.get("conditions")
        ob = _order_brief(ev.get("order"))
        s = f"Entry Phase3: action={action}"
        if ob:
            s += f" order[{ob}]"
        if cond:
            s += f" conditions={_short_json(cond)}"
        return s
    if typ == "PHASE3_REJECTED":
        reason = ev.get("reason")
        details = ev.get("details")
        s = f"Entry Phase3: rejected reason={reason}"
        if details:
            s += f" details={_short_json(details)}"
        return s
    if typ == "LAYER1_TRIGGERED":
        decision = ev.get("decision")
        trigger = ev.get("trigger")
        ctx = ev.get("context")
        ob = _order_brief(ev.get("order"))
        s = f"Exit Layer1: decision={decision}"
        if ob:
            s += f" order[{ob}]"
        if trigger:
            s += f" trigger={_short_json(trigger)}"
        if ctx:
            s += f" context={_short_json(ctx)}"
        return s
    if typ == "LAYER2_REDUCE":
        score_soft = ev.get("score_soft")
        k_change = ev.get("k_change")
        ob = _order_brief(ev.get("order"))
        s = f"Exit Layer2: action=REDUCE_50 score_soft={score_soft}"
        if ob:
            s += f" order[{ob}]"
        if k_change:
            s += f" k_change={_short_json(k_change)}"
        return s
    if typ in ("LIFEBOAT_BUYBACK", "LIFEBOAT_BUYBACK_REJECTED"):
        if typ.endswith("REJECTED"):
            reason = ev.get("reason")
            details = ev.get("details")
            s = f"Exit Lifeboat: rejected reason={reason}"
            if details:
                s += f" details={_short_json(details)}"
            return s
        cond = ev.get("conditions")
        ob = _order_brief(ev.get("order"))
        s = "Exit Lifeboat: buyback"
        if ob:
            s += f" order[{ob}]"
        if cond:
            s += f" conditions={_short_json(cond)}"
        return s
    if typ == "FSM_TRANSITION":
        frm = ev.get("from_state")
        to = ev.get("to_state")
        trig = ev.get("trigger")
        details = ev.get("details")
        s = f"Position: transition {frm}->{to}"
        if trig:
            s += f" trigger={trig}"
        if details:
            s += f" details={_short_json(details)}"
        return s
    if typ == "SCALE_SIGNAL_EVAL":
        decision = ev.get("decision")
        prereq = ev.get("prerequisites")
        conds = ev.get("signal_conditions")
        s = f"Position: scale decision={decision}"
        if prereq:
            s += f" prereq={_short_json(prereq)}"
        if conds:
            s += f" conds={_short_json(conds)}"
        return s
    if typ == "T0_OPERATION":
        direction = ev.get("direction")
        trig = ev.get("trigger")
        constraints = ev.get("constraints")
        ob = _order_brief(ev.get("order"))
        s = f"T0: operation {direction}"
        if trig:
            s += f" trigger={trig}"
        if ob:
            s += f" order[{ob}]"
        if constraints:
            s += f" constraints={_short_json(constraints)}"
        return s
    if typ == "T0_REGIME":
        active = ev.get("regime_active")
        reason = ev.get("reason")
        avr = ev.get("auction_vol_ratio")
        atrp = ev.get("atr5_percentile")
        return f"T0: regime active={active} reason={reason} auction_vol_ratio={avr} atr5_pct={atrp}"
    if typ == "T0_SIGNAL":
        st = ev.get("signal_type")
        action = ev.get("action")
        tp = ev.get("target_price")
        vwap = ev.get("vwap")
        sigma = ev.get("sigma")
        k = ev.get("k_value")
        oa = ev.get("order_amount")
        return f"T0: signal {st} action={action} target={tp} vwap={vwap} sigma={sigma} k={k} amt={oa}"
    if typ == "T0_BREAKER":
        layer = ev.get("breaker_layer")
        action = ev.get("action")
        tv = ev.get("trigger_value")
        th = ev.get("threshold")
        note = str(ev.get("note") or "").strip()
        s = f"T0: breaker layer={layer} action={action} trigger={tv} threshold={th}"
        if note:
            s += f" note={note}"
        return s
    if typ == "T0_ROUND_TRIP":
        direction = ev.get("direction")
        qty = ev.get("quantity")
        pnl_bps = ev.get("net_pnl_bps")
        pnl = ev.get("net_pnl_cny")
        return f"T0: round_trip dir={direction} qty={qty} pnl_bps={pnl_bps} pnl_cny={pnl}"
    if typ == "CIRCUIT_BREAKER":
        reason = ev.get("reason") or ev.get("trigger") or ""
        action = ev.get("action") or ""
        return f"Position: circuit_breaker action={action} reason={reason}"
    return f"{source}:{typ} {_short_json(ev)}"


@dataclass(frozen=True)
class ParsedEvent:
    timestamp: datetime
    etf_code: str
    source: str
    typ: str
    summary: str
    raw: dict[str, Any]


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    def gen() -> Iterable[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    return gen()


def load_events(*, logs_dir: Path, include_files: list[str]) -> list[ParsedEvent]:
    out: list[ParsedEvent] = []
    for name in include_files:
        p = logs_dir / name
        src = p.name
        for ev in _iter_jsonl(p):
            ts = _parse_ts(ev.get("timestamp"))
            if ts is None:
                continue
            code = _norm_code(ev.get("etf_code"))
            if not code:
                continue
            typ = str(ev.get("type") or "").strip()
            summary = _event_summary(ev, source=src)
            out.append(ParsedEvent(timestamp=ts, etf_code=code, source=src, typ=typ, summary=summary, raw=ev))
    out.sort(key=lambda x: (x.timestamp, x.etf_code, x.source, x.typ))
    return out


def render_report(*, events: list[ParsedEvent], date_from: str, date_to: str) -> str:
    if not events:
        return f"# 交易复盘报告\n\n- 区间：{date_from} - {date_to}\n- 事件数：0\n"

    per_code: dict[str, list[ParsedEvent]] = {}
    for e in events:
        per_code.setdefault(e.etf_code, []).append(e)

    lines: list[str] = []
    lines.append("# 交易复盘报告")
    lines.append("")
    lines.append(f"- 区间：{date_from} - {date_to}")
    lines.append(f"- 事件数：{len(events)}")
    lines.append(f"- 标的数：{len(per_code)}")
    lines.append("")

    lines.append("## 概览（按标的）")
    lines.append("")
    lines.append("| ETF | 事件数 | 买入相关 | 卖出相关 | T0 | 其它 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for code, lst in sorted(per_code.items(), key=lambda x: x[0]):
        n = len(lst)
        buy = sum(1 for e in lst if e.typ.startswith("PHASE"))
        sell = sum(1 for e in lst if e.typ.startswith("LAYER") or e.typ.startswith("LIFEBOAT"))
        t0 = sum(1 for e in lst if e.typ.startswith("T0_"))
        other = n - buy - sell - t0
        lines.append(f"| {code} | {n} | {buy} | {sell} | {t0} | {other} |")
    lines.append("")

    per_day: dict[str, list[ParsedEvent]] = {}
    for e in events:
        per_day.setdefault(_date_key(e.timestamp), []).append(e)

    for day, lst in sorted(per_day.items(), key=lambda x: x[0]):
        lines.append(f"## {day}")
        lines.append("")
        for e in lst:
            ts = e.timestamp.strftime("%H:%M:%S")
            lines.append(f"- {ts} {e.etf_code} {e.summary}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_csv(*, path: Path, events: list[ParsedEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["timestamp", "date", "etf_code", "type", "source", "summary", "raw_json"],
        )
        w.writeheader()
        for e in events:
            w.writerow(
                {
                    "timestamp": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": _date_key(e.timestamp),
                    "etf_code": e.etf_code,
                    "type": e.typ,
                    "source": e.source,
                    "summary": e.summary,
                    "raw_json": json.dumps(e.raw, ensure_ascii=False),
                }
            )


def _parse_ymd(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) == 8 and t.isdigit():
        return t
    if "-" in t:
        try:
            return datetime.strptime(t, "%Y-%m-%d").strftime("%Y%m%d")
        except Exception:
            return ""
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-dir", default="data/logs", help="日志目录，默认 data/logs")
    ap.add_argument("--out-dir", default="output/replay", help="输出目录，默认 output/replay")
    ap.add_argument("--files", default=",".join(DEFAULT_LOG_FILES), help="逗号分隔的 jsonl 文件名")
    ap.add_argument("--date", default="", help="单日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument("--from", dest="date_from", default="", help="开始日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default="", help="结束日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument("--codes", default="", help="逗号分隔 ETF 代码过滤（可带交易所后缀）")
    args = ap.parse_args(argv)

    logs_dir = Path(str(args.logs_dir))
    out_dir = Path(str(args.out_dir))
    files = [x.strip() for x in str(args.files).split(",") if x.strip()]
    if not files:
        files = list(DEFAULT_LOG_FILES)

    date_single = _parse_ymd(str(args.date))
    date_from = _parse_ymd(str(args.date_from))
    date_to = _parse_ymd(str(args.date_to))
    if date_single:
        date_from = date_single
        date_to = date_single

    codes = [_norm_code(x) for x in str(args.codes).split(",") if _norm_code(x)]
    code_set = set(codes)

    events = load_events(logs_dir=logs_dir, include_files=files)
    if date_from:
        events = [e for e in events if _date_key(e.timestamp) >= date_from]
    if date_to:
        events = [e for e in events if _date_key(e.timestamp) <= date_to]
    if code_set:
        events = [e for e in events if e.etf_code in code_set]

    df = date_from or "ALL"
    dt = date_to or "ALL"
    tag = f"{df}_{dt}" if df != dt else df
    md_path = out_dir / f"replay_{tag}.md"
    csv_path = out_dir / f"replay_{tag}.csv"

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_report(events=events, date_from=df, date_to=dt), encoding="utf-8")
    write_csv(path=csv_path, events=events)

    print(str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

