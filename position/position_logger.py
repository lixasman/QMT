from __future__ import annotations

import atexit
import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, TextIO


_JSONL_WRITERS: dict[str, TextIO] = {}
_JSONL_WRITERS_LOCK = Lock()


def _get_jsonl_writer(path: Path) -> TextIO:
    cache_key = str(path.resolve())
    writer = _JSONL_WRITERS.get(cache_key)
    if writer is not None and not writer.closed:
        return writer
    with _JSONL_WRITERS_LOCK:
        writer = _JSONL_WRITERS.get(cache_key)
        if writer is not None and not writer.closed:
            return writer
        writer = path.open("a", encoding="utf-8", buffering=1)
        _JSONL_WRITERS[cache_key] = writer
        return writer


def _close_jsonl_writers() -> None:
    with _JSONL_WRITERS_LOCK:
        writers = list(_JSONL_WRITERS.values())
        _JSONL_WRITERS.clear()
    for writer in writers:
        writer.close()


def append_jsonl(*, log_path: str, payload: dict[str, Any]) -> None:
    if not str(log_path).strip():
        return
    p = Path(str(log_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    writer = _get_jsonl_writer(p)
    writer.write(line + "\n")
    writer.flush()


atexit.register(_close_jsonl_writers)


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
