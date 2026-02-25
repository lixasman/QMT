from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ReplayEvent:
    type: str
    timestamp: str
    payload: dict[str, Any]


def write_events(path: str | Path, events: Iterable[ReplayEvent]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for e in events:
        lines.append(json.dumps({"type": e.type, "timestamp": e.timestamp, "payload": e.payload}, ensure_ascii=False))
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_events(path: str | Path) -> list[ReplayEvent]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[ReplayEvent] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        out.append(ReplayEvent(type=str(obj.get("type") or ""), timestamp=str(obj.get("timestamp") or ""), payload=obj.get("payload") or {}))
    return out


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

