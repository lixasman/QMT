from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from core.enums import DataQuality


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_layer1_triggered(
    *,
    log_path: str | Path,
    timestamp: datetime,
    etf_code: str,
    trigger: dict[str, Any],
    context: dict[str, Any],
    decision: str,
    order: Optional[dict[str, Any]],
) -> None:
    p = Path(log_path)
    payload: dict[str, Any] = {
        "type": "LAYER1_TRIGGERED",
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_code": str(etf_code),
        "trigger": trigger,
        "context": context,
        "decision": str(decision),
        "order": order,
    }
    _append_jsonl(p, payload)


def log_layer2_reduce(
    *,
    log_path: str | Path,
    timestamp: datetime,
    etf_code: str,
    score_soft: float,
    signals: dict[str, Any],
    order: dict[str, Any],
    k_change: dict[str, Any],
) -> None:
    p = Path(log_path)
    payload: dict[str, Any] = {
        "type": "LAYER2_REDUCE",
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_code": str(etf_code),
        "score_soft": float(score_soft),
        "signals": signals,
        "action": "REDUCE_50",
        "order": order,
        "k_change": k_change,
    }
    _append_jsonl(p, payload)


def log_lifeboat_buyback(
    *,
    log_path: str | Path,
    timestamp: datetime,
    etf_code: str,
    sell_time: datetime,
    trading_minutes_elapsed: int,
    conditions: dict[str, Any],
    order: dict[str, Any],
    post_state: dict[str, Any],
) -> None:
    p = Path(log_path)
    payload: dict[str, Any] = {
        "type": "LIFEBOAT_BUYBACK",
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_code": str(etf_code),
        "cooldown": {
            "sell_time": sell_time.strftime("%H:%M:%S"),
            "trading_minutes_elapsed": int(trading_minutes_elapsed),
        },
        "conditions": conditions,
        "order": order,
        "post_state": post_state,
    }
    _append_jsonl(p, payload)


def log_lifeboat_buyback_rejected(
    *,
    log_path: str | Path,
    timestamp: datetime,
    etf_code: str,
    reason: str,
    details: dict[str, Any],
) -> None:
    p = Path(log_path)
    payload: dict[str, Any] = {
        "type": "LIFEBOAT_BUYBACK_REJECTED",
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_code": str(etf_code),
        "reason": str(reason),
        "details": details,
    }
    _append_jsonl(p, payload)


def serialize_data_health(health: Mapping[str, DataQuality]) -> dict[str, str]:
    return {str(k): str(v.value) for k, v in health.items()}

