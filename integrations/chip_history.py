from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.warn_utils import warn_once


def _safe_code_key(code: str) -> str:
    return str(code).strip().upper().replace(".", "_")


def _safe_list(v: object) -> list[dict[str, object]]:
    if not isinstance(v, list):
        return []
    out: list[dict[str, object]] = []
    for x in v:
        if isinstance(x, dict):
            out.append(x)
    return out


def _parse_yyyymmdd(v: object) -> str:
    s = str(v or "").strip()
    return s if len(s) == 8 and s.isdigit() else ""


def _parse_float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except Exception:
        return 0.0


@dataclass(frozen=True)
class ChipDPCHistory:
    history_dir: Path
    keep_days: int = 10

    def __post_init__(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, etf_code: str) -> Path:
        return self.history_dir / f"dpc_{_safe_code_key(etf_code)}.json"

    def load(self, etf_code: str) -> list[dict[str, object]]:
        p = self._path(etf_code)
        if not p.exists():
            return []
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            return _safe_list(obj)
        except Exception as e:
            warn_once(f"dpc_history_load_failed:{str(p)}", f"State: DPC 历史文件读取失败，已降级为空数据: {p} err={repr(e)}")
            return []

    def upsert(self, *, etf_code: str, trade_date: str, dpc_peak_density: float) -> list[dict[str, object]]:
        td = _parse_yyyymmdd(trade_date)
        if not td:
            return self.load(etf_code)
        rows = self.load(etf_code)
        kept: list[dict[str, object]] = []
        replaced = False
        for r in rows:
            d = _parse_yyyymmdd(r.get("trade_date"))
            if d == td:
                kept.append({"trade_date": td, "dpc_peak_density": float(dpc_peak_density)})
                replaced = True
            else:
                kept.append(r)
        if not replaced:
            kept.append({"trade_date": td, "dpc_peak_density": float(dpc_peak_density)})
        kept2 = [x for x in kept if _parse_yyyymmdd(x.get("trade_date"))]
        kept2.sort(key=lambda x: _parse_yyyymmdd(x.get("trade_date")))
        if int(self.keep_days) > 0 and len(kept2) > int(self.keep_days):
            kept2 = kept2[-int(self.keep_days) :]
        self._path(etf_code).write_text(json.dumps(kept2, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return kept2

    def get_5d(self, etf_code: str) -> Optional[list[float]]:
        rows = self.load(etf_code)
        rows2 = [r for r in rows if _parse_yyyymmdd(r.get("trade_date"))]
        rows2.sort(key=lambda x: _parse_yyyymmdd(x.get("trade_date")))
        if len(rows2) < 5:
            return None
        tail = rows2[-5:]
        return [float(_parse_float(x.get("dpc_peak_density"))) for x in tail]
