from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def append_jsonl(*, log_path: str, payload: dict[str, Any]) -> None:
    p = Path(str(log_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_fsm_transition(*, log_path: str, timestamp: datetime, payload: dict[str, Any]) -> None:
    obj: dict[str, Any] = {"type": "FSM_TRANSITION", "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S")}
    obj.update(dict(payload))
    append_jsonl(log_path=str(log_path), payload=obj)


def log_scale_signal_eval(*, log_path: str, timestamp: datetime, payload: dict[str, Any]) -> None:
    obj: dict[str, Any] = {"type": "SCALE_SIGNAL_EVAL", "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S")}
    obj.update(dict(payload))
    append_jsonl(log_path=str(log_path), payload=obj)


def log_t0_operation(*, log_path: str, timestamp: datetime, payload: dict[str, Any]) -> None:
    obj: dict[str, Any] = {"type": "T0_OPERATION", "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S")}
    obj.update(dict(payload))
    append_jsonl(log_path=str(log_path), payload=obj)


def log_circuit_breaker(*, log_path: str, timestamp: datetime, payload: dict[str, Any]) -> None:
    obj: dict[str, Any] = {"type": "CIRCUIT_BREAKER", "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S")}
    obj.update(dict(payload))
    append_jsonl(log_path=str(log_path), payload=obj)
