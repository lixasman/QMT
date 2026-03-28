from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    if str(path).strip() in ("", "."):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_phase2_score(
    *,
    log_path: str | Path,
    timestamp: datetime,
    etf_code: str,
    signals: dict[str, Any],
    score: float,
    diversity_gate: bool,
    decision: str,
    note: str = "",
) -> None:
    p = Path(log_path)
    payload: dict[str, Any] = {
        "type": "PHASE2_SCORE",
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_code": etf_code,
        "signals": signals,
        "score": float(score),
        "diversity_gate": bool(diversity_gate),
        "decision": decision,
        "note": note,
    }
    _append_jsonl(p, payload)


def log_phase3_decision(
    *,
    log_path: str | Path,
    timestamp: datetime,
    etf_code: str,
    action: str,
    conditions: dict[str, Any],
    order: dict[str, Any] | None,
) -> None:
    p = Path(log_path)
    payload: dict[str, Any] = {
        "type": "PHASE3_DECISION",
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_code": etf_code,
        "action": action,
        "conditions": conditions,
        "order": order,
    }
    _append_jsonl(p, payload)


def log_phase3_rejected(
    *,
    log_path: str | Path,
    timestamp: datetime,
    etf_code: str,
    reason: str,
    details: dict[str, Any],
) -> None:
    p = Path(log_path)
    payload: dict[str, Any] = {
        "type": "PHASE3_REJECTED",
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_code": etf_code,
        "reason": reason,
        "details": details,
    }
    _append_jsonl(p, payload)
